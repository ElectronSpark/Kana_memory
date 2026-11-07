"""One-way conversion UI keeps live queues untouched until explicit restore."""
import asyncio
import json
from threading import Event, get_ident
from types import MethodType
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import backup_ui, backup_worker, progress_backup
from kanamemory.backup_worker import BackupCancelled, BackupJob, BackupResult
from kanamemory.domain import StudyStore
from kanamemory.learning_ui import LearningUIMixin
from support.backup_background_candidate_support import app
from support.app_fixtures import setup_app
from support.backup_retained_callbacks_support import controls
from support.backup_retained_callbacks_support import wire
from support.app_fixtures import app_module
from record_test_support import as_legacy, decode_current


LEGACY = b'isolated legacy fixture'
CURRENT = b'isolated current-format fixture'
SUMMARY = {'cards': 8, 'reviews': 17, 'learning_content': 3,
           'learning_reviews': 5, 'has_unfinished_session': True}


@pytest.fixture
def conversion_app(app, monkeypatch):
    app.platform.import_backup.return_value = LEGACY
    monkeypatch.setattr(progress_backup, 'is_legacy_backup', lambda data: data == LEGACY,
                        raising=False)
    return app


def stages(monkeypatch, *, failure=None):
    calls = []
    async def stage(app, action, title, data=None):
        calls.append((action, data))
        if failure and action == failure[0]:
            raise failure[1]
        if action == 'convert':
            assert data is LEGACY
            return BackupResult(CURRENT)
        assert data is CURRENT
        return BackupResult(SUMMARY, committed=action == 'restore')
    monkeypatch.setattr(backup_ui, '_stage', stage)
    return calls


def retained(app):
    return (app.main_window.content, app.session_id, app.questions, app.question,
            app.store.load_session_progress())


def semantic_tables(tables):
    # The synthetic v9 fixture stores parsed question lists, not raw JSON
    # strings. Compare every field after serialization without inventing a
    # whitespace-preservation requirement for those structured lists.
    return {name: [dict(row, snapshot_json=json.loads(row['snapshot_json']))
                   if name == 'session_question' else row for row in rows]
            for name, rows in tables.items()}


def test_convert_exports_checked_new_bytes_without_replacing_live_progress(conversion_app, monkeypatch):
    app = conversion_app
    before = retained(app)
    calls = stages(monkeypatch)
    asyncio.run(app_module.KanaMemory.convert_legacy_learning_records(app))
    assert calls == [('convert', LEGACY), ('inspect', CURRENT)]
    exported, filename = app.platform.export_backup.call_args.args
    assert exported is CURRENT and filename.startswith('KanaMemory-records-converted-')
    assert filename.endswith('.json')
    assert retained(app) == before and app.store.get_setting('original') == 'keep'
    app.main_window.dialog.assert_not_awaited()
    app.show_home.assert_not_called()
    assert '复习记录 22 次' in app.alert.call_args.args[0]
    assert '当前学习记录未替换' in app.alert.call_args.args[0]
    assert not app._backup_request


@pytest.mark.parametrize('confirmed', [False, True])
def test_restore_old_file_converts_once_and_binds_confirmation_to_new_bytes(conversion_app, monkeypatch, confirmed):
    app = conversion_app
    before = retained(app)
    calls = stages(monkeypatch)
    dialog = Mock(return_value=object())
    monkeypatch.setattr(app_module.toga, 'ConfirmDialog', dialog)
    async def confirm(_):
        app.platform.import_backup.return_value = b'external file changed'
        return confirmed
    app.main_window.dialog.side_effect = confirm
    asyncio.run(backup_ui.restore(app))
    assert calls == [('convert', LEGACY), ('inspect', CURRENT)] + ([('restore', CURRENT)] if confirmed else [])
    assert dialog.call_args.args[1].startswith('旧格式已转换。\n')
    app.platform.import_backup.assert_awaited_once()
    app.platform.export_backup.assert_not_awaited()
    if confirmed:
        assert app.session_id is None and app.questions == []
        app.show_home.assert_called_once()
    else:
        assert retained(app) == before
        app.show_home.assert_not_called()
    assert not app._backup_request


@pytest.mark.parametrize('action', ['convert', 'inspect'])
@pytest.mark.parametrize('error', [ValueError('invalid fixture'), BackupCancelled()])
def test_failed_or_cancelled_conversion_never_exports_restores_or_resets(conversion_app, monkeypatch, action, error):
    app = conversion_app
    before = retained(app)
    calls = stages(monkeypatch, failure=(action, error))
    asyncio.run(backup_ui.convert_legacy(app))
    assert all(name != 'restore' for name, _ in calls)
    app.platform.export_backup.assert_not_awaited()
    app.main_window.dialog.assert_not_awaited()
    assert retained(app) == before
    assert not app._backup_request
    assert ('已取消转换' if isinstance(error, BackupCancelled) else '未能转换') in app.alert.call_args.args[0]


def test_restore_conversion_failure_never_offers_replacement_confirmation(conversion_app, monkeypatch):
    app = conversion_app
    calls = stages(monkeypatch, failure=('convert', ValueError('bad fixture')))
    before = retained(app)
    asyncio.run(backup_ui.restore(app))
    assert calls == [('convert', LEGACY)]
    app.main_window.dialog.assert_not_awaited()
    assert retained(app) == before


@pytest.mark.parametrize('failure', ['picker', 'busy'])
def test_new_restore_clears_stale_commit_before_picker_or_readiness_failure(conversion_app, failure):
    app = conversion_app
    app._backup_restore_committed = True
    before = retained(app)
    if failure == 'picker':
        app.platform.import_backup.side_effect = OSError('cannot read selected file')
    else:
        app._ink_busy = True
    asyncio.run(backup_ui.restore(app))
    assert not app._backup_restore_committed and not app._backup_request
    assert app.alert.call_args.args[0].startswith('未能恢复备份：')
    assert retained(app) == before
    app.main_window.dialog.assert_not_awaited()
    app.show_home.assert_not_called()


@pytest.mark.parametrize('valid', [True, False])
def test_nonlegacy_input_is_validated_before_calling_it_current(conversion_app, monkeypatch, valid):
    app = conversion_app
    app.platform.import_backup.return_value = CURRENT
    calls = stages(monkeypatch, failure=None if valid else ('inspect', ValueError('not records')))
    before = retained(app)
    asyncio.run(backup_ui.convert_legacy(app))
    assert calls == [('inspect', CURRENT)]
    app.platform.export_backup.assert_not_awaited()
    assert retained(app) == before
    assert ('已是当前记录格式' in app.alert.call_args.args[0]) is valid


def test_cancel_picker_or_repeated_conversion_request_does_not_start_work(conversion_app, monkeypatch):
    app = conversion_app
    stage = AsyncMock()
    monkeypatch.setattr(backup_ui, '_stage', stage)
    app.platform.import_backup.return_value = None
    asyncio.run(backup_ui.convert_legacy(app))
    stage.assert_not_awaited()
    app._backup_request = True
    asyncio.run(backup_ui.convert_legacy(app))
    app.platform.import_backup.assert_awaited_once()


def test_settings_entry_dispatches_conversion_without_adding_home_controls(conversion_app, monkeypatch):
    app = conversion_app
    wire(app)
    app_module.KanaMemory.show_settings.__wrapped__(app)
    callback = controls(app)['转换旧版学习记录'].on_press
    convert = AsyncMock()
    monkeypatch.setattr(backup_ui, 'convert_legacy', convert)
    asyncio.run(callback(None))
    convert.assert_awaited_once_with(app)


def test_current_settings_hub_data_menu_opens_conversion(conversion_app, monkeypatch):
    app = conversion_app
    wire(app)
    for name in ('show_prompt_settings', 'show_settings', 'show_feedback_manager', 'show_ai_service_settings'):
        setattr(app, name, Mock())
    app.show_learning_data = MethodType(LearningUIMixin.show_learning_data, app)
    LearningUIMixin.show_settings_hub.__wrapped__(app)
    assert app.page == 'settings_hub'
    controls(app)['学习数据'].on_press(None)
    assert app.page == 'learning_data'
    convert = AsyncMock()
    monkeypatch.setattr(backup_ui, 'convert_legacy', convert)
    asyncio.run(controls(app)['转换旧版学习记录'].on_press(None))
    convert.assert_awaited_once_with(app)
    app.show_settings.assert_not_called()


def test_conversion_worker_owns_connection_and_passes_safe_cancel_check(tmp_path, monkeypatch):
    thread = get_ident()
    calls = []
    class Tracked(StudyStore):
        def __init__(self, *args, **kwargs):
            calls.append(('open', get_ident()))
            super().__init__(*args, **kwargs)
        def close(self):
            calls.append(('close', get_ident()))
            super().close()
    def convert(store, data, *, cancel_check):
        calls.append(('convert', get_ident()))
        assert data is LEGACY
        cancel_check()
        assert store._db.execute('SELECT count(*) FROM review_event').fetchone()[0] == 0
        return CURRENT
    monkeypatch.setattr(backup_worker, 'StudyStore', Tracked)
    monkeypatch.setattr(progress_backup, 'convert_legacy_backup', convert, raising=False)
    result = asyncio.run(BackupJob('convert', tmp_path/'live', 'UTC', LEGACY).run())
    assert result.value is CURRENT and result.committed is False
    assert [action for action, _ in calls] == ['open', 'convert', 'close']
    assert len({owner for _, owner in calls}) == 1 and calls[0][1] != thread


def test_conversion_cancellation_keeps_thread_barrier_until_connection_closes(tmp_path, monkeypatch):
    entered, release, closed = Event(), Event(), Event()
    class Tracked(StudyStore):
        def close(self):
            super().close()
            closed.set()
    def convert(store, data, *, cancel_check):
        entered.set()
        assert release.wait(5)
        cancel_check()
        return CURRENT
    monkeypatch.setattr(backup_worker, 'StudyStore', Tracked)
    monkeypatch.setattr(progress_backup, 'convert_legacy_backup', convert, raising=False)
    async def run():
        task = asyncio.create_task(BackupJob('convert', tmp_path/'live', 'UTC', LEGACY).run())
        async def wait_entered():
            while not entered.is_set():
                if task.done():
                    return await task
                await asyncio.sleep(.001)
        await asyncio.wait_for(wait_entered(), 3)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done() and not closed.is_set()
        release.set()
        with pytest.raises(BackupCancelled):
            await task
        assert closed.is_set()
    asyncio.run(run())


@pytest.mark.parametrize('data', [None, 'not immutable bytes', bytearray(b'bytes')])
def test_conversion_worker_rejects_non_bytes_before_opening_store(tmp_path, data):
    with pytest.raises(TypeError):
        BackupJob('convert', tmp_path/'never-opened', 'UTC', data)
    assert not (tmp_path/'never-opened').exists()


def test_real_conversion_stage_preserves_live_draft_and_exports_same_records(app):
    before = decode_current(app.store.export_backup())['tables']
    old = json.dumps(as_legacy(decode_current(app.store.export_backup())), ensure_ascii=False).encode('utf8')
    app.platform.import_backup.return_value = old
    view, questions, question, sid = app.main_window.content, app.questions, app.question, app.session_id
    asyncio.run(backup_ui.convert_legacy(app))
    app.platform.export_backup.assert_awaited_once()
    current = app.platform.export_backup.call_args.args[0]
    assert not progress_backup.is_legacy_backup(current)
    assert semantic_tables(decode_current(current)['tables']) == semantic_tables(before)
    assert decode_current(app.store.export_backup())['tables'] == before
    assert app.main_window.content is view and app.questions is questions and app.question is question
    assert app.session_id == sid and app.store.load_session_progress()['state'] == {'romaji_answer': 'a'}
    assert not app._backup_busy and not app._backup_request
    app.main_window.dialog.assert_not_awaited()


@pytest.mark.parametrize('confirmed', [False, True])
def test_real_legacy_restore_only_replaces_after_confirmation_and_keeps_queue(app, confirmed):
    original = decode_current(app.store.export_backup())['tables']
    draft = app.store.load_session_progress()
    old = json.dumps(as_legacy(decode_current(app.store.export_backup())), ensure_ascii=False).encode('utf8')
    app.store.set_setting('after_backup', 'keep unless restoring')
    current = decode_current(app.store.export_backup())['tables']
    app.platform.import_backup.return_value = old
    app.main_window.dialog.return_value = confirmed
    asyncio.run(backup_ui.restore(app))
    app.main_window.dialog.assert_awaited_once()
    actual = decode_current(app.store.export_backup())['tables']
    if confirmed:
        assert semantic_tables(actual) == semantic_tables(original)
    else:
        assert actual == current
    assert app.store.load_session_progress() == draft
    if confirmed:
        recovery = app.store.previous_backup()
        assert recovery and not progress_backup.is_legacy_backup(recovery)
        assert decode_current(recovery)['tables'] == current
        app.show_home.assert_called_once()
    else:
        app.show_home.assert_not_called()
    assert not app._backup_busy and not app._backup_request


def test_export_previous_converts_retired_json_without_modifying_recovery_file(tmp_path):
    folder = tmp_path/'previous'
    with StudyStore(folder, time_zone='UTC') as store:
        store.set_setting('old marker', 'synthetic only')
        original = decode_current(store.export_backup())['tables']
        old = json.dumps(as_legacy(decode_current(store.export_backup()))).encode('utf8')
        recovery = folder/'learning-before-restore-20260101T000000-test.json'
        recovery.write_bytes(old)
        store.set_setting('current marker', 'must survive conversion')
    result = asyncio.run(BackupJob('previous', folder, 'UTC').run())
    assert result.value and not result.committed
    assert decode_current(result.value)['tables'] == original
    assert recovery.read_bytes() == old
    with StudyStore(folder, time_zone='UTC') as store:
        assert store.get_setting('current marker') == 'must survive conversion'
