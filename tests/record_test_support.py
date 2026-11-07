"""Test-only materialization/mutation of current records, never an app importer."""
from pathlib import Path
from types import SimpleNamespace
import base64
import hashlib
import json
import sqlite3
import tempfile
import zlib


class RecordDocument(dict):
    columns = None


def decode_current(data):
    from kanamemory import record_transport, progress_backup
    root = Path(__file__).resolve().parents[1] / '.test_runs' / 'record-test-decode'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        payload = record_transport.decode(data, SimpleNamespace(data_dir=Path(directory)),
                                          outer_limit=progress_backup.MAX_BYTES)
        tables = payload['tables']
        try:
            result = RecordDocument({**payload, 'tables': {
                name: list(rows) for name, rows in tables.items()}})
            result.columns = dict(tables.columns)
            return result
        finally:
            tables.close()


def _columns():
    from kanamemory.record_schema import create_base
    from kanamemory.learning_store import ensure_schema
    from kanamemory import learning_evidence, tutor_memory, record_transport
    db = sqlite3.connect(':memory:')
    try:
        create_base(db)
        db.commit()
        ensure_schema(db)
        learning_evidence.ensure_schema(db)
        tutor_memory.ensure_schema(db)
        return {name: [row[1] for row in db.execute('PRAGMA table_info(' + name + ')')]
                for name in record_transport.names()}
    finally:
        db.close()


def encode_current(document):
    """Re-sign intentionally tampered rows; do not invoke semantic validators."""
    from kanamemory import record_transport
    columns = getattr(document, 'columns', None) or _columns()
    tables = document['tables']
    names = list(tables)
    manifests = []
    for name in names:
        fields = list(columns.get(name, list(tables[name][0]) if tables[name] else ['value']))
        for row in tables[name]:
            fields.extend(key for key in row if key not in fields)
        manifests.append({'name': name, 'columns': fields, 'rows': len(tables[name])})
    # Preserve a bad version/algorithm so the production decoder must reject it.
    header = {key: document[key] for key in ('format', 'version', 'algorithm_version', 'created_at_utc')}
    header['tables'] = manifests
    dump = lambda value: json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf8')
    result = bytearray(dump(header) + b'\n')
    raw = b''.join(dump([row.get(key) for key in manifest['columns']]) + b'\n'
                   for manifest in manifests for row in tables[manifest['name']])
    chunks = [raw[i:i + record_transport.BLOCK] for i in range(0, len(raw), record_transport.BLOCK)]
    for index, chunk in enumerate(chunks):
        result.extend(dump({'block': index, 'bytes': len(chunk), 'sha256': hashlib.sha256(chunk).hexdigest(),
                            'data': base64.b64encode(zlib.compress(chunk)).decode('ascii')}) + b'\n')
    result.extend(dump({'end': True, 'blocks': len(chunks), 'bytes': len(raw),
                        'sha256': hashlib.sha256(raw).hexdigest()}) + b'\n')
    return bytes(result)


def as_legacy(document, version=9):
    """Synthetic old fixture for the explicit converter only."""
    from kanamemory.legacy_record_conversion import LEGACY_FORMAT, legacy_table_names
    tables = {name: [dict(row) for row in document['tables'][name]] for name in legacy_table_names(version)}
    for session in tables['study_session']:
        questions = [json.loads(row['snapshot_json']) for row in document['tables']['session_question']
                     if row['session_id'] == session['session_id']]
        session['question_snapshot_json'] = json.dumps(questions, ensure_ascii=False) if version == 1 else questions
    return {'format': LEGACY_FORMAT, 'version': version,
            'algorithm_version': document['algorithm_version'], 'created_at_utc': document['created_at_utc'],
            'tables': tables}


def encode_legacy_envelope(payload, *, outer_limit):
    """Only tests manufacture retired envelopes; production has no old writer."""
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf8')
    codec = zlib.compressobj(6, zlib.DEFLATED, 31)
    compressed = codec.compress(raw) + codec.flush()
    result = json.dumps({'format': 'kanamemory-learning-backup-envelope', 'version': 1,
        'encoding': 'gzip+base64', 'expanded_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
        'data': base64.b64encode(compressed).decode('ascii')}, separators=(',', ':')).encode('ascii')
    assert len(result) <= outer_limit
    return result
