"""Current-only persistence and a one-way, read-only retired-format boundary."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from kanamemory import progress_backup, record_transport
from kanamemory.domain import Question, StudyStore
from record_test_support import decode_current, encode_current, as_legacy, encode_legacy_envelope


def seeded(store):
    q = Question('kana-a', 'kana_choice', 'hiragana', 'あ', 'a',
                 options=['a', 'ka', 'sa', 'ta'], audio_text='あ')
    sid = store.start_session([q, q], now=datetime(2026, 10, 31, tzinfo=timezone.utc))
    store.save_session_progress(sid, 0, state={'romaji_answer': 'a',
        'strokes': [[[1, 2, 3], [4, 5, 6]]], 'ink_width': 360, 'ink_height': 210})
    return sid


def dump(store):
    return tuple(store._db.iterdump())


@pytest.mark.parametrize('version', range(1, 10))
@pytest.mark.parametrize('envelope', [False, True])
def test_conversion_preserves_every_current_field_without_touching_live(tmp_path, version, envelope):
    with StudyStore(tmp_path/'source') as source, StudyStore(tmp_path/'live') as live:
        seeded(source)
        live.set_setting('private-existing', 'do not replace')
        expected = decode_current(source.export_backup())
        old = as_legacy(expected, version)
        raw = (encode_legacy_envelope(old, outer_limit=progress_backup.MAX_BYTES) if envelope
               else json.dumps(old, ensure_ascii=False).encode())
        original = dump(live)
        assert progress_backup.is_legacy_backup(raw)
        for method in (live.inspect_backup, live.import_backup):
            with pytest.raises(ValueError, match='先转换'):
                method(raw)
        converted = progress_backup.convert_legacy_backup(live, raw)
        assert not progress_backup.is_legacy_backup(converted)
        assert converted.startswith(record_transport.PREFIX)
        got = decode_current(converted)
        for name, rows in expected['tables'].items():
            if name == 'session_question':
                assert [json.loads(row['snapshot_json']) for row in got['tables'][name]] == [json.loads(row['snapshot_json']) for row in rows]
            else:
                assert got['tables'][name] == rows
        assert dump(live) == original and live.previous_backup() is None
        assert not list(live.data_dir.glob('.backup-stage-*'))
        live.import_backup(converted)
        assert live.load_session_progress() == source.load_session_progress()
        assert 'question_snapshot_json' not in [r[1] for r in live._db.execute('PRAGMA table_info(study_session)')]


def test_legacy_stream_is_explicitly_converted(tmp_path):
    with StudyStore(tmp_path) as store:
        seeded(store)
        expected = decode_current(store.export_backup())
        old = as_legacy(expected, 9)
        for row in old['tables']['study_session']:
            row['question_snapshot_json'] = json.dumps(row['question_snapshot_json'], ensure_ascii=False)
        old.update(format='kanamemory-learning-backup-stream', version=1)
        lines = encode_current(old).splitlines()
        header = json.loads(lines[0])
        header['inner_version'] = 10
        lines[0] = json.dumps(header, separators=(',', ':')).encode()
        converted = progress_backup.convert_legacy_backup(store, b'\n'.join(lines)+b'\n')
        assert store.inspect_backup(converted)['sessions'] == 1
        got = decode_current(converted)
        assert json.loads(got['tables']['session_question'][0]['snapshot_json']) == json.loads(expected['tables']['session_question'][0]['snapshot_json'])


@pytest.mark.parametrize('damage', ['unknown_version', 'duplicate_key', 'missing_table', 'extra_column',
                                  'gap_index', 'dangling_answer', 'bad_meta', 'nonfinite', 'extra_header'])
def test_current_record_validation_is_atomic(tmp_path, damage):
    with StudyStore(tmp_path) as store:
        seeded(store)
        document = decode_current(store.export_backup())
        if damage == 'unknown_version': document['version'] = 2
        elif damage == 'missing_table': del document['tables']['session_question']
        elif damage == 'extra_column': document['tables']['session_question'][0]['sql'] = 'DROP TABLE study_card'
        elif damage == 'gap_index': document['tables']['session_question'][1]['question_index'] = 3
        elif damage == 'dangling_answer': document['tables']['session_question'][0]['session_id'] = 'missing'
        elif damage == 'bad_meta': document['tables']['learner_record_meta'][0]['value'] = '0'
        elif damage == 'nonfinite': document['tables']['session_checkpoint'][0]['state_json'] = '{"ink_width":NaN}'
        raw = encode_current(document)
        if damage == 'duplicate_key': raw = raw.replace(b'"version":1', b'"version":1,"version":1', 1)
        elif damage == 'extra_header': raw = raw.replace(b'"version":1', b'"version":1,"sql":"DROP TABLE x"', 1)
        before = dump(store)
        with pytest.raises(ValueError): store.import_backup(raw)
        assert dump(store) == before and store.previous_backup() is None
        assert not list(store.data_dir.glob('.backup-stage-*'))


def test_conversion_cancellation_cleans_staging_and_preserves_rows(tmp_path):
    with StudyStore(tmp_path) as store:
        seeded(store)
        raw = json.dumps(as_legacy(decode_current(store.export_backup()))).encode()
        before = dump(store)
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            if calls == 6: raise InterruptedError('cancel conversion')
        with pytest.raises(InterruptedError):
            progress_backup.convert_legacy_backup(store, raw, cancel_check=cancel)
        assert dump(store) == before and store.previous_backup() is None
        assert not list(store.data_dir.glob('.backup-stage-*'))


def test_current_only_export_has_no_legacy_decision_dependencies(tmp_path, monkeypatch):
    with StudyStore(tmp_path) as store:
        seeded(store)
        import kanamemory.legacy_record_conversion as old
        monkeypatch.setattr(old, 'convert_legacy_backup', lambda *a, **k: pytest.fail('legacy converter used'))
        for size in (0, 100, 100000):
            store.set_setting('padding', 'x' * size)
            value = store.export_backup()
            assert value.startswith(record_transport.PREFIX)
            assert store.inspect_backup(value)['sessions'] == 1
        assert not hasattr(record_transport, 'should_stream')
        assert not hasattr(progress_backup, '_encode_snapshot')


def test_historical_word_observation_is_not_regraded(tmp_path, monkeypatch):
    from support.word_interval_backup_support import mature
    from support.word_interval_backup_support import submit
    with StudyStore(tmp_path/'source') as source, StudyStore(tmp_path/'target') as target:
        mature(source)
        submit(source, answered_text='かか')
        expected = decode_current(source.export_backup())
        def forbidden(*args, **kwargs): pytest.fail('import must not regrade historical answers')
        import kanamemory.domain as domain
        import kanamemory.word_romaji_units as units
        monkeypatch.setattr(domain, 'check_answer', forbidden)
        monkeypatch.setattr(domain, '_review_spelling_units', forbidden)
        monkeypatch.setattr(units, 'check_romaji_parts', forbidden)
        converted = progress_backup.convert_legacy_backup(target, json.dumps(as_legacy(expected)).encode())
        target.import_backup(converted)
        assert decode_current(target.export_backup())['tables']['word_spelling_attempt'] == expected['tables']['word_spelling_attempt']


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('field,value', [('question_index', True), ('question_index', 0.0),
                                        ('session_id', 123), ('session_id', None)])
def test_external_rows_reject_sqlite_implicit_scalar_conversion(tmp_path, legacy, field, value):
    with StudyStore(tmp_path) as store:
        seeded(store)
        document = decode_current(store.export_backup())
        document['tables']['session_checkpoint'][0][field] = value
        before = dump(store)
        with pytest.raises(ValueError):
            if legacy:
                progress_backup.convert_legacy_backup(store, json.dumps(as_legacy(document)).encode())
            else:
                store.import_backup(encode_current(document))
        assert dump(store) == before and store.previous_backup() is None
