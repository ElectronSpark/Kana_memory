"""Fast draft persistence must preserve strict history validation and durability."""
from contextlib import closing
from copy import deepcopy
import sqlite3

import pytest

from kanamemory.domain import StudyStore
from kanamemory import tutor_store as module
from kanamemory.tutor_ui import TutorUIMixin
from support.tutor_draft_fixtures import completed_state


@pytest.fixture
def tutor(tmp_path):
    with StudyStore(tmp_path / 'learner', time_zone='UTC') as store:
        value = module.AITutorStore(store)
        store.set_setting(module.SETTING, completed_state())
        yield value


def read(tutor):
    return tutor.store.get_setting(module.SETTING)


def external_write(tutor, value):
    with closing(sqlite3.connect(tutor.store.path)) as db, db:
        db.execute('UPDATE app_setting SET value_json=? WHERE key=?',
                   (module._json(value), module.SETTING))


def test_repeated_typing_validates_200_old_results_once_and_preserves_history(tutor, monkeypatch):
    initial = completed_state(200)
    tutor.store.set_setting(module.SETTING, initial)
    result_checks = []
    original = module._result

    def checked(*args, **kwargs):
        result_checks.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, '_result', checked)
    tutor.save_chat_draft('为')
    assert len(result_checks) == 200
    result_checks.clear()
    for draft in ('为什', '为什么', '为什么？'):
        tutor.save_chat_draft(draft)
    assert result_checks == []
    saved = read(tutor)
    assert saved['chat_draft'] == '为什么？'
    assert saved['revision'] == initial['revision'] + 4
    assert {key: value for key, value in saved.items() if key not in {'chat_draft', 'revision'}} == {
        key: value for key, value in initial.items() if key not in {'chat_draft', 'revision'}}


def test_same_draft_is_noop_and_context_is_not_aliased(tutor):
    origin = dict(source='general', question_id='', target_id='', after_answer=False)
    tutor.save_chat_draft('原因？', origin)
    before = read(tutor)
    changes = tutor.store._db.total_changes
    assert tutor.save_chat_draft('原因？', origin) == '原因？'
    assert read(tutor) == before and tutor.store._db.total_changes == changes
    origin['target_id'] = 'word:changed-by-caller'
    assert read(tutor) == before


def test_external_tampering_with_same_revision_is_rejected_without_overwrite(tutor):
    tutor.save_chat_draft('已保存')
    forged = read(tutor)
    forged['entries'][0]['result']['correct'] = False
    external_write(tutor, forged)
    with pytest.raises(ValueError, match='辅导结果被修改'):
        tutor.save_chat_draft('不可覆盖错误记录')
    assert read(tutor) == forged


def test_external_valid_change_with_same_revision_is_preserved(tutor):
    tutor.save_chat_draft('原草稿')
    external = read(tutor)
    external['messages'].append(dict(id='external', role='user', text='外部追加的问题',
        target_id='', turn_id='external-turn', created_at_utc='2026-11-01T12:00:00+00:00'))
    external_write(tutor, external)
    tutor.save_chat_draft('接着输入')
    saved = read(tutor)
    assert saved['messages'] == external['messages']
    assert saved['entries'] == external['entries']
    assert saved['revision'] == external['revision'] + 1


@pytest.mark.parametrize('failure', ['update', 'commit'])
def test_failed_commit_retains_old_draft_and_retry_succeeds(tutor, failure):
    tutor.save_chat_draft('稳定草稿')
    before = read(tutor)
    db = tutor.store._db
    if failure == 'update':
        db.execute("CREATE TEMP TRIGGER fail_draft BEFORE UPDATE ON app_setting "
                   "WHEN NEW.key='ai_tutor_v1' BEGIN SELECT RAISE(ABORT, 'draft failure'); END")
    else:
        db.execute('CREATE TABLE draft_parent(id INTEGER PRIMARY KEY)')
        db.execute('CREATE TABLE draft_guard(parent_id INTEGER REFERENCES draft_parent(id) DEFERRABLE INITIALLY DEFERRED)')
        db.execute("CREATE TEMP TRIGGER fail_draft AFTER UPDATE ON app_setting "
                   "WHEN NEW.key='ai_tutor_v1' BEGIN INSERT INTO draft_guard VALUES(999); END")
    with pytest.raises(sqlite3.IntegrityError):
        tutor.save_chat_draft('失败草稿')
    assert not db.in_transaction and read(tutor) == before
    db.execute('DROP TRIGGER fail_draft')
    tutor.save_chat_draft('重试草稿')
    assert read(tutor)['chat_draft'] == '重试草稿'
    assert read(tutor)['revision'] == before['revision'] + 1


def test_nested_transaction_is_rejected_without_committing_other_writes(tutor):
    before = read(tutor)
    db = tutor.store._db
    db.execute('BEGIN')
    db.execute("UPDATE app_setting SET value_json=? WHERE key=?",
               (module._json({**before, 'chat_draft': '未提交'}), module.SETTING))
    try:
        with pytest.raises(RuntimeError, match='own transaction'):
            tutor.save_chat_draft('不得提交')
        assert db.in_transaction
    finally:
        db.rollback()
    assert read(tutor) == before
    tutor.save_chat_draft('回滚后输入')
    assert read(tutor)['chat_draft'] == '回滚后输入'


def test_context_secret_rejected_while_text_secret_is_redacted(tutor):
    tutor.save_chat_draft('安全草稿')
    before = read(tutor)
    secret = 'sk-proj-' + 'X' * 30
    origin = dict(source='general', question_id='', target_id=secret, after_answer=False)
    with pytest.raises(ValueError):
        tutor.save_chat_draft('安全文字', origin)
    assert read(tutor) == before
    tutor.save_chat_draft('误粘贴 ' + secret)
    assert secret not in module._json(read(tutor))
    assert '[已移除密钥]' in read(tutor)['chat_draft']


def test_warm_draft_still_checks_total_encoded_size(tutor, monkeypatch):
    tutor.save_chat_draft('短')
    before = read(tutor)
    monkeypatch.setattr(module, 'MAX_BYTES', len(module._json(before).encode('utf8')) + 20)
    with pytest.raises(ValueError):
        tutor.save_chat_draft('长' * 100)
    assert read(tutor) == before


def test_restore_and_clear_are_seen_by_existing_tutor_and_restart(tutor):
    initial = deepcopy(read(tutor))
    backup = tutor.store.export_backup()
    tutor.save_chat_draft('备份后的草稿')
    tutor.store.import_backup(backup)
    tutor.save_chat_draft('恢复后继续')
    saved = read(tutor)
    assert saved['revision'] == initial['revision'] + 1
    assert saved['entries'] == initial['entries']
    with StudyStore(tutor.store.data_dir, time_zone='UTC') as reopened:
        assert module.AITutorStore(reopened).snapshot() == saved
    tutor.store.clear_learning_data()
    tutor.save_chat_draft('清除后新问题')
    assert not read(tutor)['entries']
    assert read(tutor)['revision'] == 1


def test_ui_reuses_service_only_for_current_store(tmp_path):
    app = TutorUIMixin()
    with StudyStore(tmp_path / 'first', time_zone='UTC') as first, StudyStore(tmp_path / 'second', time_zone='UTC') as second:
        app.store = first
        first_service = app.tutor_store()
        assert app.tutor_store() is first_service
        app.store = second
        assert app.tutor_store() is not first_service
        assert app.tutor_store().store is second
