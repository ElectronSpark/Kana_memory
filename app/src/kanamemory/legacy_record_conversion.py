"""One-way import boundary for retired backups; never reads or mutates live rows.

Only this module understands versions 1–10 and the former session-array column.
It creates current records in private staging, validates their references and
saved observations, then emits the single current transport. No old writer is
retained and no normal learning operation imports this module.
"""
from __future__ import annotations

import json
import re
import sqlite3
from types import SimpleNamespace

from . import record_transport
from .record_schema import TABLES, SESSION_COLUMNS, create_base, iter_legacy_questions

LEGACY_FORMAT = 'kanamemory-learning-backup'
LEGACY_BASE = ('app_setting', 'study_session', 'study_card', 'review_event',
               'practice_attempt', 'session_checkpoint', 'session_answer')
LEGACY_V3 = (*LEGACY_BASE, 'word_spelling_attempt')
_FORMAT_MARKER = re.compile(rb'"format"\s*:\s*"(kanamemory-learning-backup(?:-envelope|-stream)?)"')


def legacy_table_names(version):
    from .learning_store import TABLES as COURSE_TABLES
    from .progress_backup import _teaching_tables
    base = LEGACY_V3 if version >= 3 else LEGACY_BASE
    if version >= 8:
        base = (*base, *COURSE_TABLES)
    if version >= 9:
        base = (*base, *_teaching_tables())
    return base


def is_legacy_backup(data):
    """Cheap format hint only; conversion still strictly validates the whole file."""
    return isinstance(data, bytes) and _FORMAT_MARKER.search(data[:8192]) is not None


def _read(data, store, check):
    from .progress_backup import MAX_BYTES
    from . import legacy_backup_envelope, legacy_backup_stream
    if type(data) is not bytes or not 0 < len(data) <= MAX_BYTES:
        raise ValueError('请选择 64 MiB 以内的旧版学习记录。')
    check()
    # A stream's first line is a complete header. Plain backups usually have
    # the complete document on one line, so only the marker selects this path.
    first = data.lstrip(b'\xef\xbb\xbf \t\r\n')
    marker = _FORMAT_MARKER.search(first[:8192])
    if marker is not None and marker.group(1) == legacy_backup_stream.FORMAT.encode():
        return legacy_backup_stream.decode(first, store, outer_limit=MAX_BYTES,
                                           cancel_check=check)
    payload = record_transport._load(data.decode('utf-8-sig'))
    if isinstance(payload, dict) and payload.get('format') == legacy_backup_envelope.FORMAT:
        inner = legacy_backup_envelope.decode(data, outer_limit=MAX_BYTES, cancel_check=check)
        check()
        payload = record_transport._load(inner.decode('utf-8-sig'))
    return payload


def _insert(db, name, row, schema):
    from .record_validation import validate_row
    validate_row(row, schema)
    columns = [field[1] for field in schema]
    # Table and column names originate in app-owned DDL, never input SQL.
    db.execute('INSERT INTO ' + name + ' (' + ','.join(columns) + ') VALUES ('
               + ','.join('?' for _ in columns) + ')', [row[key] for key in columns])


def convert_legacy_backup(store, data, *, cancel_check=None):
    from .progress_backup import MAX_BYTES, _empty_teaching_tables, _validated_payload
    from .domain import ALGORITHM_VERSION
    from .learning_store import TABLES as COURSE_TABLES, ensure_schema
    from . import learning_evidence, tutor_memory
    check = cancel_check or (lambda: None)
    payload = _read(data, store, check)
    source_tables = payload.get('tables') if isinstance(payload, dict) else None
    stage = validated = None
    try:
        if (not isinstance(payload, dict)
                or set(payload) != {'format', 'version', 'algorithm_version', 'created_at_utc', 'tables'}
                or payload.get('format') != LEGACY_FORMAT
                or type(payload.get('version')) is not int or not 1 <= payload['version'] <= 10
                or payload.get('algorithm_version') != ALGORITHM_VERSION
                or not isinstance(source_tables, dict)):
            raise ValueError('旧版学习记录格式或算法版本不兼容，未转换。')
        version = payload['version']
        if version == 10 and not isinstance(source_tables, record_transport.DiskTables):
            raise ValueError('旧版分块备份必须通过有界分块读取。')
        if set(source_tables) != set(legacy_table_names(version)):
            raise ValueError('旧版学习记录缺少必要的记录集合。')
        stage = record_transport.DiskTables(store.data_dir)
        db = stage.db
        db.execute('PRAGMA foreign_keys=ON')
        create_base(db)
        db.commit()
        ensure_schema(db)
        learning_evidence.ensure_schema(db)
        tutor_memory.ensure_schema(db)
        all_names = record_transport.names()
        defaults = _empty_teaching_tables()
        defaults['learning_meta'] = [{'key': 'schema_version', 'value': '1'}]
        defaults['learner_record_meta'] = [{'key': 'schema_version', 'value': '1'}]
        for name in reversed(all_names):
            db.execute('DELETE FROM ' + name)
        for name in all_names:
            check()
            schema = list(db.execute('PRAGMA table_info(' + name + ')'))
            if name == 'session_question':
                continue  # Installed immediately after each parent session.
            rows = source_tables.get(name, defaults.get(name, []))
            if not isinstance(rows, list) or len(rows) > record_transport.TABLE_ROW_LIMIT:
                raise ValueError('旧版记录集合过大或类型不正确。')
            for index, row in enumerate(rows):
                if index % 256 == 0:
                    check()
                if name != 'study_session':
                    _insert(db, name, row, schema)
                    continue
                if not isinstance(row, dict) or set(row) != set(SESSION_COLUMNS) | {'question_snapshot_json'}:
                    raise ValueError('旧版会话字段不正确。')
                snapshot = row['question_snapshot_json']
                if 2 <= version <= 9:
                    if not isinstance(snapshot, list):
                        raise ValueError('旧版题单必须为数组。')
                    snapshot = json.dumps(snapshot, ensure_ascii=False, allow_nan=False,
                                          separators=(',', ':'))
                _insert(db, name, {key: row[key] for key in SESSION_COLUMNS}, schema)
                for question_index, raw in enumerate(iter_legacy_questions(snapshot)):
                    if question_index >= record_transport.TABLE_ROW_LIMIT:
                        raise ValueError('单份旧题单超过记录数量上限。')
                    if question_index % 256 == 0:
                        check()
                    db.execute('INSERT INTO session_question VALUES(?,?,?)',
                               (row['session_id'], question_index, raw))
        db.commit()
        current = {'format': record_transport.FORMAT, 'version': record_transport.VERSION,
                   'algorithm_version': payload['algorithm_version'],
                   'created_at_utc': payload['created_at_utc'], 'tables': stage.populate(all_names)}
        validated, _, _ = _validated_payload(store, current, cancel_check=check)
        check()
        # Encoding does not consult the live store and cannot add reviews,
        # recompute due times, or regenerate already issued questions.
        with validated.db:
            validated.db.execute('BEGIN')
            return record_transport.encode(SimpleNamespace(_db=validated.db),
                                            outer_limit=MAX_BYTES, cancel_check=check)
    except (sqlite3.Error, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as exc:
        raise ValueError('旧版学习记录损坏或不兼容，原记录未修改。') from exc
    finally:
        for tables in (validated, stage, source_tables):
            if isinstance(tables, record_transport.DiskTables):
                tables.close()
