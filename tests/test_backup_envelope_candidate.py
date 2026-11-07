"""Isolated candidate only: bounded transport, old schemas and atomic restore."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import zlib

import pytest

from kanamemory import legacy_backup_envelope as envelope, progress_backup as backup, record_transport
from record_test_support import decode_current, encode_current, as_legacy, encode_legacy_envelope
from kanamemory.domain import StudyStore, Question
from kanamemory.learning_store import LearningStore, TABLES as LEARNING_TABLES


def q():
    return Question('kana-a', 'kana_choice', 'hiragana', 'あ', 'a',
                    options=['a', 'ka', 'sa', 'ta'], audio_text='あ')


def payload(store, version=8):
    LearningStore(store)
    return as_legacy(decode_current(store.export_backup()), version)


def rows(store):
    return {name: [tuple(r) for r in store._db.execute('SELECT * FROM '+name+' ORDER BY rowid')]
            for name in (*backup.TABLES, *LEARNING_TABLES)}


@pytest.mark.parametrize('version', range(1, 9))
def test_old_v1_v8_and_envelope_share_semantic_validation(tmp_path, version):
    with StudyStore(tmp_path/'source') as source, StudyStore(tmp_path/'target') as target:
        sid = source.start_session([q()])
        source.save_session_progress(sid, 0, state={'romaji_answer': 'a'})
        value = payload(source, version)
        raw = json.dumps(value, ensure_ascii=False).encode()
        encoded = encode_legacy_envelope(value, outer_limit=backup.MAX_BYTES)
        assert envelope.decode(encoded, outer_limit=backup.MAX_BYTES) == json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        raw_current = backup.convert_legacy_backup(target, raw)
        envelope_current = backup.convert_legacy_backup(target, encoded)
        assert decode_current(raw_current)['tables'] == decode_current(envelope_current)['tables']
        target.import_backup(envelope_current)
        assert target.load_session_progress()['state'] == {'romaji_answer': 'a'}
        assert target.load_session_progress()['session_id'] == sid
        assert target.previous_backup()


def test_current_export_has_one_format_and_separate_limits(tmp_path, monkeypatch):
    with StudyStore(tmp_path/'source') as store:
        store.start_session([q()])
        assert store.export_backup().startswith(record_transport.PREFIX)
        assert backup.MAX_BYTES == backup.SESSION_SNAPSHOT_LIMIT == 64*1024*1024
        monkeypatch.setattr(backup, 'MAX_BYTES', 8192)
        monkeypatch.setattr(record_transport, 'EXPANDED_LIMIT', 65536)
        store.set_setting('padding', 'x'*20000)
        data = store.export_backup()
        assert data.startswith(record_transport.PREFIX) and len(data) < backup.MAX_BYTES
        assert store.inspect_backup(data)['sessions'] == 1
        store.set_setting('padding', 'x'*70000)
        with pytest.raises(ValueError, match='磁盘数据上限'):
            store.export_backup()


@pytest.mark.parametrize('damage', ['bool', 'version', 'encoding', 'length', 'negative',
    'digest', 'digest_type', 'base64', 'data_type', 'unknown', 'duplicate', 'crc',
    'truncated', 'trailing', 'second_member', 'nested', 'nonfinite'])
def test_malformed_never_changes_live_rows_or_creates_recovery(tmp_path, damage):
    with StudyStore(tmp_path) as store:
        store.set_setting('keep', 'original')
        value = payload(store)
        encoded = encode_legacy_envelope(value, outer_limit=backup.MAX_BYTES)
        box = json.loads(encoded)
        compressed = base64.b64decode(box['data'])
        if damage == 'bool': box['expanded_bytes'] = True
        elif damage == 'version': box['version'] = True
        elif damage == 'encoding': box['encoding'] = 'zip'
        elif damage == 'length': box['expanded_bytes'] += 1
        elif damage == 'negative': box['expanded_bytes'] = -1
        elif damage == 'digest': box['sha256'] = '0'*64
        elif damage == 'digest_type': box['sha256'] = []
        elif damage == 'base64': box['data'] += '!'
        elif damage == 'data_type': box['data'] = 1
        elif damage == 'unknown': box['extra'] = 1
        elif damage == 'crc': box['data'] = base64.b64encode(compressed[:-8]+b'\0'*8).decode()
        elif damage == 'truncated': box['data'] = base64.b64encode(compressed[:-4]).decode()
        elif damage == 'trailing': box['data'] = base64.b64encode(compressed+b'x').decode()
        elif damage == 'second_member': box['data'] = base64.b64encode(compressed+compressed).decode()
        elif damage == 'nested': box = json.loads(encode_legacy_envelope(box, outer_limit=backup.MAX_BYTES))
        elif damage == 'nonfinite': box['expanded_bytes'] = float('nan')
        damaged = json.dumps(box).encode()
        if damage == 'duplicate':
            damaged = damaged[:-1]+b',"version":1}'
        before = rows(store)
        with pytest.raises(ValueError):
            backup.convert_legacy_backup(store, damaged)
        assert rows(store) == before
        assert not list(store.data_dir.glob('learning-before-restore-*'))


def test_false_small_declaration_stops_under_default_global_limit(monkeypatch):
    box = json.loads(encode_legacy_envelope({'text': 'x'*200000}, outer_limit=backup.MAX_BYTES))
    box['expanded_bytes'] = 1024
    factory = zlib.decompressobj
    outputs = []
    class Spy:
        def __init__(self): self.real = factory(31)
        def __getattr__(self, name): return getattr(self.real, name)
        def decompress(self, value, max_length):
            output = self.real.decompress(value, max_length)
            outputs.append(len(output))
            return output
    monkeypatch.setattr(zlib, 'decompressobj', lambda _: Spy())
    with pytest.raises(ValueError, match='实际展开长度'):
        envelope.decode(json.dumps(box).encode(), outer_limit=backup.MAX_BYTES)
    assert sum(outputs) <= 1025


def test_outer_expanded_and_session_caps_independent(tmp_path, monkeypatch):
    with StudyStore(tmp_path) as store:
        store.start_session([q()])
        value = payload(store)
        encoded = encode_legacy_envelope(value, outer_limit=backup.MAX_BYTES)
        with pytest.raises(ValueError, match='64 MiB'):
            envelope.decode(encoded, outer_limit=len(encoded)-1)
        with pytest.raises(ValueError):
            envelope.decode(encoded, outer_limit=backup.MAX_BYTES, expanded_limit=32)
        monkeypatch.setattr(backup, 'SESSION_SNAPSHOT_LIMIT', 32)
        with pytest.raises(ValueError, match='快照超过'):
            backup.convert_legacy_backup(store, encoded)


@pytest.mark.parametrize('corrupt', ['column', 'foreign_key', 'invalid_question'])
def test_envelope_does_not_bypass_old_semantics(tmp_path, corrupt):
    with StudyStore(tmp_path) as store:
        sid = store.start_session([q()])
        store.save_session_progress(sid, 0)
        value = payload(store)
        if corrupt == 'column': value['tables']['study_session'][0]['oops'] = 1
        elif corrupt == 'foreign_key': value['tables']['session_checkpoint'][0]['session_id'] = 'missing'
        else: value['tables']['study_session'][0]['question_snapshot_json'][0]['kind'] = 'unknown'
        before = rows(store)
        with pytest.raises(ValueError):
            backup.convert_legacy_backup(store, encode_legacy_envelope(value, outer_limit=backup.MAX_BYTES))
        assert rows(store) == before


def test_large_current_restore_small_then_undo_uses_atomic_envelope(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, 'MAX_BYTES', 8192)
    monkeypatch.setattr(record_transport, 'EXPANDED_LIMIT', 65536)
    with StudyStore(tmp_path/'live') as live, StudyStore(tmp_path/'small') as small:
        LearningStore(live)
        live.set_setting('padding', 'x'*20000)
        sid = live.start_session([q()])
        live.save_session_progress(sid, 0, state={'romaji_answer':'a'})
        before = rows(live)
        small.set_setting('replacement', True)
        result = live.import_backup(small.export_backup())
        path = Path(result['rollback_path'])
        assert path.exists() and not list(live.data_dir.glob('*.tmp'))
        undo = live.previous_backup()
        assert undo.startswith(record_transport.PREFIX)
        live.import_backup(undo)
        assert rows(live) == before


@pytest.mark.parametrize('failure', ['write', 'fsync', 'rename', 'sql'])
def test_recovery_io_or_sql_failure_is_atomic(tmp_path, monkeypatch, failure):
    with StudyStore(tmp_path/'live') as live, StudyStore(tmp_path/'new') as new:
        LearningStore(live)
        live.set_setting('keep', 'original')
        new.set_setting('replacement', True)
        data = new.export_backup()
        before, scheduler = rows(live), live.scheduler
        if failure == 'write':
            original_open = Path.open
            class FailedWriter:
                def __init__(self, file): self.file = file
                def __enter__(self): return self
                def __exit__(self, *args): self.file.close()
                def write(self, value): self.file.write(value[:10]); raise OSError('write')
            def opening(path, *args, **kwargs):
                value = original_open(path, *args, **kwargs)
                return FailedWriter(value) if path.suffix == '.tmp' else value
            monkeypatch.setattr(Path, 'open', opening)
        elif failure in ('fsync', 'rename'):
            def fail(*args): raise OSError(failure)
            monkeypatch.setattr(backup.os, 'fsync' if failure == 'fsync' else 'replace', fail)
        else:
            live._db.execute("""CREATE TEMP TRIGGER no_replacement BEFORE INSERT ON app_setting
                WHEN NEW.key='replacement' BEGIN SELECT RAISE(ABORT,'forced SQL failure'); END""")
        with pytest.raises((OSError, sqlite3.Error)):
            live.import_backup(data)
        assert rows(live) == before
        assert live.scheduler is scheduler
        assert not live._db.in_transaction
        assert not list(live.data_dir.glob('*.tmp'))
        finals = list(live.data_dir.glob('learning-before-restore-*.json'))
        assert len(finals) == int(failure == 'sql')
        if finals:
            assert live.inspect_backup(finals[0].read_bytes())


def test_recovery_snapshot_holds_writer_lock_before_file_write(tmp_path, monkeypatch):
    with StudyStore(tmp_path/'live') as live, StudyStore(tmp_path/'new') as new:
        LearningStore(live)
        live.set_setting('keep', 'original')
        data = new.export_backup()
        write = backup._write_recovery_file
        checked = []
        def check(path, original):
            assert live._db.in_transaction
            other = sqlite3.connect(live.path, timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    other.execute("INSERT INTO app_setting VALUES ('concurrent','true')")
            finally:
                other.close()
            checked.append(True)
            write(path, original)
        monkeypatch.setattr(backup, '_write_recovery_file', check)
        live.import_backup(data)
        assert checked == [True]


def test_failed_final_commit_restores_rows_and_retains_valid_recovery(tmp_path, monkeypatch):
    with StudyStore(tmp_path/'live') as live, StudyStore(tmp_path/'new') as new:
        LearningStore(live)
        live.set_setting('keep', 'original')
        data = new.export_backup()
        before = rows(live)
        base = live._db
        class FailedCommit:
            def __getattr__(self, name): return getattr(base, name)
            def __enter__(self): base.__enter__(); return self
            def __exit__(self, kind, value, traceback):
                if kind is None and base.in_transaction and self.armed:
                    base.rollback()
                    raise sqlite3.OperationalError('forced commit failure')
                return base.__exit__(kind, value, traceback)
            armed = False
        proxy = FailedCommit()
        live._db = proxy
        write = backup._write_recovery_file
        def arm(path, data): write(path, data); proxy.armed = True
        monkeypatch.setattr(backup, '_write_recovery_file', arm)
        with pytest.raises(sqlite3.OperationalError, match='commit'):
            live.import_backup(data)
        live._db = base
        assert rows(live) == before
        assert live.previous_backup()
