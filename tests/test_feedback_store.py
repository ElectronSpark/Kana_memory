"""Durable, portable feedback evidence must never write learner records."""
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import sqlite3
import struct
from uuid import uuid4
import zipfile
import zlib

import pytest

from kanamemory import feedback_store as module
from kanamemory.feedback_store import FeedbackStore, FORMAT, VERSION


from support.feedback_store_support import png, context


@pytest.fixture
def store(tmp_path):
    with FeedbackStore(tmp_path / 'feedback.sqlite3') as value:
        yield value


def packet(data):
    with zipfile.ZipFile(BytesIO(data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    return json.loads(files['manifest.json']), json.loads(files['feedback.json']), files


def test_persistence_and_detached_immutable_context(tmp_path):
    path = tmp_path / 'feedback.sqlite3'
    captured = context()
    original = deepcopy(captured)
    with FeedbackStore(path) as store:
        first = store.create('这里的「ている」不清楚。\n希望对照解释。', captured, category='unclear', screenshot=png())
        identity = first['id']
        captured['fragments'][0]['text'] = 'changed caller'
        first['context']['state']['draft'] = 'changed return'
        assert store.get(identity)['context'] == original
        assert store.screenshot_bytes(identity) == png()
    with FeedbackStore(path) as reopened:
        saved = reopened.get(identity)
        assert saved['context'] == original
        assert saved['note'] == '这里的「ている」不清楚。\n希望对照解释。'
        assert saved['status'] == 'open'
        assert saved['screenshot']['available'] is True
        assert saved['page_name'] == 'learning_intro'


def test_editing_and_triage_do_not_rewrite_capture(store):
    first = store.create('字迹在哪里？', context(), category='bug', screenshot=png())
    edited = store.update(first['id'], note='字迹只有识别后出现。', status='resolved', category='layout')
    assert edited['context'] == first['context']
    assert edited['context_sha256'] == first['context_sha256']
    assert edited['capture_sha256'] == first['capture_sha256']
    assert edited['screenshot'] == first['screenshot']
    assert edited['created_at'] == first['created_at']
    assert store.update(first['id'], note=edited['note']) == edited


def test_atomic_batches_missing_id_never_partially_change_records(store):
    a = store.create('A', context())
    b = store.create('B', context())
    missing = str(uuid4())
    for action in (lambda: store.update_many([a['id'], missing, b['id']], status='archived'),
                   lambda: store.delete_many([a['id'], missing, b['id']])):
        with pytest.raises(KeyError):
            action()
        assert store.get(a['id']) == a
        assert store.get(b['id']) == b
    assert store.update_many([a['id'], a['id'], b['id']], status='archived') == 2
    assert store.update_many([a['id'], b['id']], status='archived') == 0
    assert store.delete_many([a['id'], a['id']]) == 1
    assert store.get(a['id']) is None
    assert store.count_records() == 1


def test_lists_are_paged_lightweight_and_search_wildcards_are_literal(store):
    first = store.create('100% 的 _ 下划线', context())
    second = store.create("not a query ' OR 1=1; --", {'page': {'name': 'home', 'title': '今日'}})
    third = store.create('其他', {'page': {'name': 'home', 'title': '其他'}})
    store.update(first['id'], status='resolved')
    assert [r['id'] for r in store.list_records(limit=2)] == [third['id'], second['id']]
    assert [r['id'] for r in store.list_records(limit=2, offset=2)] == [first['id']]
    assert 'context' not in store.list_records()[0]
    assert store.count_records(status='open') == 2
    assert [r['id'] for r in store.list_records(query='%')] == [first['id']]
    assert [r['id'] for r in store.list_records(query='_')] == [first['id']]
    assert [r['id'] for r in store.list_records(query="' OR 1=1; --")] == [second['id']]
    assert store.count_records(query='今日') == 1


def test_export_contains_exact_context_images_manifest_and_record_hashes(store):
    a = store.create('共享片段归属不清楚', context(), category='content', screenshot=png())
    b = store.create('没有截图也要保留', context(), category='other')
    store.update(b['id'], status='archived')
    before = store.get(a['id']), store.get(b['id'])
    manifest, data, files = packet(store.export())
    assert manifest['format'] == data['format'] == FORMAT
    assert manifest['version'] == data['version'] == VERSION
    assert manifest['record_count'] == data['record_count'] == 2
    assert manifest['status_counts'] == {'open': 1, 'archived': 1}
    assert [r['id'] for r in data['records']] == [a['id'], b['id']]
    assert data['records'][0]['context'] == context()
    assert data['records'][1]['screenshot'] == {'available': False, 'bytes': 0, 'media_type': None, 'path': None, 'sha256': None}
    for entry in manifest['files']:
        body = files[entry['path']]
        assert len(body) == entry['bytes']
        assert hashlib.sha256(body).hexdigest() == entry['sha256']
    for record in data['records']:
        content = {k: v for k, v in record.items() if k != 'record_sha256'}
        assert hashlib.sha256(module._json_bytes(content)).hexdigest() == record['record_sha256']
        assert hashlib.sha256(module._json_bytes(record['context'])).hexdigest() == record['context_sha256']
    assert files[a['screenshot']['path']] == png()
    assert '共享片段归属不清楚' in files['REPORT.md'].decode()
    assert '不可信数据' in files['REPORT.md'].decode()
    assert (store.get(a['id']), store.get(b['id'])) == before
    assert [r['id'] for r in packet(store.export([b['id'], b['id']]))[1]['records']] == [b['id']]
    assert packet(store.export([]))[1]['records'] == []


def test_markdown_feedback_cannot_close_its_data_fence(store):
    note = '```\n# This is still feedback data\n```\n</script>'
    store.create(note, context())
    report = packet(store.export())[2]['REPORT.md'].decode()
    assert '````\n' + note + '\n````' in report


@pytest.mark.parametrize('mutation', [
    lambda store: store.create('', context()),
    lambda store: store.create('x' * (module.MAX_NOTE_CHARS + 1), context()),
    lambda store: store.create('x\x00y', context()),
    lambda store: store.create('x', []),
    lambda store: store.create('x', {'bad': float('nan')}),
    lambda store: store.create('x', {1: 'key'}),
    lambda store: store.create('x', {'bad': object()}),
    lambda store: store.create('x', context(), category='invented'),
    lambda store: store.create('x', context(), screenshot=b'not png'),
    lambda store: store.create('x', context(), screenshot=png()[:-4]),
])
def test_rejected_capture_leaves_existing_records_unchanged(store, mutation):
    existing = store.create('existing', context())
    with pytest.raises(ValueError):
        mutation(store)
    assert store.count_records() == 1
    assert store.get(existing['id']) == existing


def test_large_composed_context_is_supported_and_limits_fail_explicitly(store, monkeypatch):
    large = context()
    large['combined_text'] = 'あ' * 3_000_000  # 9 MB exceeds the old backup-style small-object caps.
    record = store.create('长页面', large)
    assert store.get(record['id'])['context'] == large
    monkeypatch.setattr(module, 'MAX_CONTEXT_BYTES', 128)
    with pytest.raises(ValueError, match='上下文超过'):
        store.create('不会截断后保存', context())
    monkeypatch.setattr(module, 'MAX_SCREENSHOT_BYTES', 16)
    with pytest.raises(ValueError, match='截图超过'):
        store.create('不会丢掉截图后保存', {}, screenshot=png())
    assert store.count_records() == 1


def test_export_limits_and_missing_selection_have_no_side_effects(store, monkeypatch):
    record = store.create('long note ' * 100, context(), screenshot=png())
    with pytest.raises(KeyError):
        store.export([record['id'], str(uuid4())])
    monkeypatch.setattr(module, 'MAX_EXPORT_BYTES', 64)
    with pytest.raises(ValueError, match='分批'):
        store.export()
    monkeypatch.setattr(module, 'MAX_EXPORT_UNCOMPRESSED_BYTES', 32)
    queries = []
    store._db.set_trace_callback(queries.append)
    with pytest.raises(ValueError, match='分批'):
        store.export()
    assert not any('SELECT *' in query for query in queries)
    assert store.get(record['id']) == record


def test_learning_reset_and_backup_restore_cannot_erase_feedback(tmp_path):
    from kanamemory.domain import StudyStore
    with StudyStore(tmp_path, time_zone='UTC') as learning, FeedbackStore(tmp_path / 'feedback.sqlite3') as feedback:
        learning.record_review('kana-3042', 'kana_choice', 'hiragana', 3)
        before = list(learning._db.iterdump())
        record = feedback.create('保留此页的错误信息', context(), screenshot=png())
        feedback.update(record['id'], status='resolved')
        feedback.export()
        assert list(learning._db.iterdump()) == before
        backup = learning.export_backup()
        saved = feedback.get(record['id'])
        learning.clear_learning_data()
        assert feedback.get(record['id']) == saved
        learning.import_backup(backup)
        assert feedback.get(record['id']) == saved


def test_foreign_database_is_never_modified(tmp_path):
    path = tmp_path / 'unrelated.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE important(data TEXT)')
        db.execute('INSERT INTO important VALUES (?)', ('keep',))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='不是反馈数据库'):
        FeedbackStore(path)
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match='独立'):
        FeedbackStore(tmp_path / 'progress.sqlite3')
    assert not (tmp_path / 'progress.sqlite3').exists()


def test_unknown_schema_version_fails_without_mutation(tmp_path):
    path = tmp_path / 'feedback.sqlite3'
    with FeedbackStore(path) as store:
        store.create('old', context())
        with store._db:
            store._db.execute("UPDATE feedback_meta SET value='999' WHERE key='schema_version'")
    before = path.read_bytes()
    with pytest.raises(ValueError, match='不支持此反馈数据库版本'):
        FeedbackStore(path)
    assert path.read_bytes() == before


def test_export_detects_corrupt_capture_instead_of_rehashing_it_as_valid(store):
    record = store.create('check', context(), screenshot=png())
    with store._db:
        store._db.execute('UPDATE feedback_record SET screenshot=? WHERE id=?', (b'broken', record['id']))
    with pytest.raises(ValueError, match='截图校验'):
        store.export()
    with store._db:
        store._db.execute('UPDATE feedback_record SET screenshot=NULL WHERE id=?', (record['id'],))
    with pytest.raises(ValueError, match='截图校验'):
        store.export()
    with store._db:
        store._db.execute('UPDATE feedback_record SET context_json=? WHERE id=?', ('{}', record['id']))
    with pytest.raises(ValueError, match='上下文校验'):
        store.get(record['id'])
