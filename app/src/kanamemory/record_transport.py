"""Current learner records: one bounded transport and one current schema.

No SQL/schema from an input is executed. Raw JSON strings remain byte-for-byte
unchanged in this transport; existing semantic validators run before restore.
"""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import zlib
import weakref
from collections.abc import Mapping

FORMAT = 'kanamemory-learning-records'
VERSION = 1
BLOCK = 64 * 1024
EXPANDED_LIMIT = 1024 * 1024 * 1024  # disk budget; never one expanded allocation
ROW_LIMIT = 129 * 1024 * 1024
TABLE_ROW_LIMIT = 1000000
PREFIX = ('{"format":"' + FORMAT + '"').encode()


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('备份分块含重复字段。')
        value[key] = item
    return value


def _load(data):
    return json.loads(data, object_pairs_hook=_unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def names():
    from .progress_backup import TABLES, _teaching_tables
    from .learning_store import TABLES as LEARNING_TABLES
    return (*TABLES, *LEARNING_TABLES, *_teaching_tables())


def encode(store, *, outer_limit, cancel_check=None):
    """Caller holds a read/replacement transaction; read each DB row once."""
    from .domain import ALGORITHM_VERSION, utc_now
    from .progress_backup import _empty_teaching_tables
    check = cancel_check or (lambda: None)
    check()
    defaults = _empty_teaching_tables()
    defaults['learning_meta'] = [{'key': 'schema_version', 'value': '1'}]
    manifests, readers = [], []
    for name in names():
        present = store._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        if present:
            columns = [row[1] for row in store._db.execute('PRAGMA table_info(' + name + ')')]
            count = store._db.execute('SELECT count(*) FROM ' + name).fetchone()[0]
            readers.append((name, columns, None))
        else:
            # Schema is still app-owned. A never-opened course/AI area exports
            # the same default empty metadata as the legacy exporter.
            from .learning_store import ensure_schema
            from . import learning_evidence, tutor_memory
            schema = sqlite3.connect(':memory:')
            try:
                ensure_schema(schema)
                learning_evidence.ensure_schema(schema)
                tutor_memory.ensure_schema(schema)
                columns = [row[1] for row in schema.execute('PRAGMA table_info(' + name + ')')]
            finally:
                schema.close()
            rows = defaults.get(name, [])
            count = len(rows)
            readers.append((name, columns, rows))
        if count > TABLE_ROW_LIMIT or not columns:
            raise ValueError('备份表大小或结构超出限制。')
        manifests.append({'name': name, 'columns': columns, 'rows': count})
    header = {'format': FORMAT, 'version': VERSION, 'algorithm_version': ALGORITHM_VERSION, 'created_at_utc': utc_now().isoformat(),
        'tables': manifests}
    output, pending, digest = BytesIO(), bytearray(), hashlib.sha256()
    blocks, expanded = 0, 0

    def emit(value):
        encoded = _json(value).encode('utf-8') + b'\n'
        if output.tell() + len(encoded) > outer_limit:
            raise ValueError('压缩学习备份超过文件大小限制（64 MiB）。')
        output.write(encoded)

    def flush(raw):
        nonlocal blocks, expanded
        check()
        expanded += len(raw)
        if expanded > EXPANDED_LIMIT:
            raise ValueError('备份超过分阶段恢复的磁盘数据上限（1 GiB）。')
        digest.update(raw)
        emit({'block': blocks, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
              'data': base64.b64encode(zlib.compress(raw, 6)).decode('ascii')})
        blocks += 1

    emit(header)
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for name, columns, fallback in readers:
        rows = (store._db.execute('SELECT * FROM ' + name + ' ORDER BY rowid')
                if fallback is None else ([row[column] for column in columns] for row in fallback))
        for index, row in enumerate(rows):
            if index % 256 == 0:
                check()
            row_bytes = 0
            for part in encoder.iterencode(list(row)):
                for start in range(0, len(part), BLOCK // 4):
                    chunk = part[start:start + BLOCK // 4].encode('utf-8')
                    row_bytes += len(chunk)
                    if row_bytes > ROW_LIMIT:
                        raise ValueError('备份单行超过安全解码上限。')
                    pending.extend(chunk)
                    while len(pending) >= BLOCK:
                        flush(bytes(pending[:BLOCK]))
                        del pending[:BLOCK]
            pending.extend(b'\n')
    if pending:
        flush(bytes(pending))
    emit({'end': True, 'blocks': blocks, 'bytes': expanded, 'sha256': digest.hexdigest()})
    return output.getvalue()


class DiskRows(list):
    """Repeatable row-list contract without retaining historical row objects."""
    def __init__(self, owner, name, count, raw=False):
        self.owner, self.name, self.count, self.raw = owner, name, count, raw
    def __len__(self):
        return self.count
    def __iter__(self):
        if self.raw:
            columns = self.owner.columns[self.name]
            for row in self.owner.db.execute('SELECT value FROM row_data WHERE name=? ORDER BY seq', (self.name,)):
                value = _load(row[0])
                if not isinstance(value, list) or len(value) != len(columns):
                    raise ValueError('备份分块行字段数量不匹配。')
                yield dict(zip(columns, value))
        else:
            for row in self.owner.db.execute('SELECT * FROM ' + self.name + ' ORDER BY rowid'):
                yield dict(row)
    def __getitem__(self, index):
        if type(index) is not int or not 0 <= index < self.count:
            raise IndexError(index)
        if self.raw:
            row = self.owner.db.execute('SELECT value FROM row_data WHERE name=? AND seq=?', (self.name, index)).fetchone()
            return dict(zip(self.owner.columns[self.name], _load(row[0])))
        row = self.owner.db.execute('SELECT * FROM ' + self.name + ' ORDER BY rowid LIMIT 1 OFFSET ?', (index,)).fetchone()
        return dict(row)
    def __eq__(self, other):
        if not isinstance(other, (list, tuple, DiskRows)):
            return NotImplemented
        return len(self) == len(other) and all(left == right for left, right in zip(self, other))
    def __ne__(self, other):
        result = self.__eq__(other)
        return NotImplemented if result is NotImplemented else not result


class DiskTables(dict):
    def __init__(self, directory):
        from .backup_stage import StageOwner
        super().__init__()
        self.temporary = StageOwner(directory)
        self.db = None
        try:
            # Tables and row views form a GC cycle. An unreachable staging
            # connection may be finalized by a different Python thread; only
            # that private connection permits cross-thread cleanup. Live
            # learner connections remain owned by their worker thread.
            self.db = sqlite3.connect(str(Path(self.temporary.name) / 'stage.sqlite3'),
                                      check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute('PRAGMA cache_size=-4096')
            self.db.execute('PRAGMA temp_store=FILE')
        except BaseException:
            if self.db is not None:
                self.db.close()
            self.temporary.cleanup()
            raise
        self.columns = {}
        # QA readers may retain only tables after their payload header dies.
        # The finalizer owns resources, never a bound self/row-view cycle.
        self.finalizer = weakref.finalize(self, self._cleanup, self.db, self.temporary)
    @staticmethod
    def _cleanup(db, temporary):
        db.close()
        temporary.cleanup()
    def populate(self, table_names):
        for name in table_names:
            self[name] = DiskRows(self, name, self.db.execute('SELECT count(*) FROM ' + name).fetchone()[0])
        return self
    def close(self):
        if self.db is not None:
            self.finalizer()
            self.db = None


def decode(data, store, *, outer_limit, cancel_check=None):
    """Check every block and ordered row count before semantic preflight."""
    if type(data) is not bytes or not 0 < len(data) <= outer_limit:
        raise ValueError('请选择 64 MiB 以内的学习记录 JSON 文件。')
    check = cancel_check or (lambda: None)
    check()
    tables = DiskTables(store.data_dir)
    tables.db.execute('CREATE TABLE row_data(name TEXT,seq INTEGER,value TEXT,PRIMARY KEY(name,seq))')
    try:
        source = BytesIO(data)
        header_line = source.readline(1024 * 1024 + 1)
        if len(header_line) > 1024 * 1024:
            raise ValueError('备份分块头过大。')
        header = _load(header_line)
        if (not isinstance(header, dict) or set(header) != {'format','version','algorithm_version','created_at_utc','tables'}
                or header['format'] != FORMAT or type(header['version']) is not int or header['version'] != VERSION
                or not isinstance(header['tables'], list)):
            raise ValueError('备份分块头无效。')
        manifests = header['tables']
        if len(manifests) != len(names()):
            raise ValueError('备份分块表不完整。')
        for expected, manifest in zip(names(), manifests):
            if (not isinstance(manifest, dict) or set(manifest) != {'name','columns','rows'}
                    or manifest['name'] != expected or not isinstance(manifest['columns'], list)
                    or not manifest['columns'] or any(not isinstance(column, str) for column in manifest['columns'])
                    or len(set(manifest['columns'])) != len(manifest['columns'])
                    or type(manifest['rows']) is not int or not 0 <= manifest['rows'] <= TABLE_ROW_LIMIT):
                raise ValueError('备份分块表声明无效。')
            tables.columns[expected] = manifest['columns']
            tables[expected] = DiskRows(tables, expected, manifest['rows'], raw=True)
        pending, digest = bytearray(), hashlib.sha256()
        blocks = expanded = table_index = row_index = 0

        def accept(line):
            nonlocal table_index, row_index
            while table_index < len(manifests) and row_index == manifests[table_index]['rows']:
                table_index += 1
                row_index = 0
            if table_index == len(manifests):
                raise ValueError('备份分块包含多余行。')
            if len(line) > ROW_LIMIT:
                raise ValueError('备份单行超过安全解码上限。')
            # JSON/type/column validation is repeated through the semantic
            # preflight adapter; a transport only stores inert UTF-8 here.
            text = line.decode('utf-8')
            tables.db.execute('INSERT INTO row_data VALUES(?,?,?)',
                (manifests[table_index]['name'], row_index, text))
            row_index += 1

        while True:
            check()
            line = source.readline(2 * BLOCK + 4096)
            if not line or not line.endswith(b'\n') or len(line) > 2 * BLOCK + 4095:
                raise ValueError('备份分块缺失、截断或过大。')
            block = _load(line)
            if isinstance(block, dict) and 'end' in block:
                if (set(block) != {'end','blocks','bytes','sha256'} or block['end'] is not True
                        or type(block['blocks']) is not int or block['blocks'] != blocks
                        or type(block['bytes']) is not int or block['bytes'] != expanded
                        or block['sha256'] != digest.hexdigest() or pending or source.read(1)):
                    raise ValueError('备份分块结束校验不一致。')
                while table_index < len(manifests) and row_index == manifests[table_index]['rows']:
                    table_index += 1
                    row_index = 0
                if table_index != len(manifests):
                    raise ValueError('备份分块缺少数据行。')
                break
            if (not isinstance(block, dict) or set(block) != {'block','bytes','sha256','data'}
                    or type(block['block']) is not int or block['block'] != blocks
                    or type(block['bytes']) is not int or not 0 < block['bytes'] <= BLOCK
                    or not isinstance(block['data'], str) or not isinstance(block['sha256'], str)):
                raise ValueError('备份分块顺序或字段无效。')
            compressed = base64.b64decode(block['data'], validate=True)
            codec = zlib.decompressobj()
            raw = codec.decompress(compressed, block['bytes'] + 1)
            if (len(raw) != block['bytes'] or not codec.eof or codec.unused_data or codec.unconsumed_tail
                    or hashlib.sha256(raw).hexdigest() != block['sha256']):
                raise ValueError('备份分块内容、长度或摘要不一致。')
            expanded += len(raw)
            if expanded > EXPANDED_LIMIT:
                raise ValueError('备份超过分阶段恢复的磁盘数据上限（1 GiB）。')
            digest.update(raw)
            blocks += 1
            pending.extend(raw)
            start = 0
            while (end := pending.find(b'\n', start)) >= 0:
                accept(pending[start:end])
                start = end + 1
            if start:
                del pending[:start]
            if len(pending) > ROW_LIMIT:
                raise ValueError('备份单行超过安全解码上限。')
        tables.db.commit()
        return {'format': FORMAT, 'version': VERSION,
            'algorithm_version': header['algorithm_version'], 'created_at_utc': header['created_at_utc'], 'tables': tables}
    except (UnicodeError, zlib.error, RecursionError) as exc:
        tables.close()
        raise ValueError('学习备份分块损坏。') from exc
    except (ValueError, TypeError, KeyError, sqlite3.Error):
        tables.close()
        raise
    except BaseException:
        tables.close()
        raise


class QuestionIndex:
    """Validated normalized snapshots with indexed lookup and no giant array."""
    def __init__(self, db, session_id, validator, cancel_check=None):
        from .domain import session_practice_lane
        self.db, self.session_id = db, session_id
        check = cancel_check or (lambda: None)
        self.count = 0
        for row in db.execute('SELECT question_index,snapshot_json FROM session_question WHERE session_id=? ORDER BY question_index', (session_id,)):
            if self.count % 256 == 0:
                check()
            if type(row[0]) is not int or row[0] != self.count:
                raise ValueError('题目索引必须连续且从零开始。')
            validator('[' + row[1] + ']')
            self.count += 1
        session_practice_lane(self)
    def __len__(self):
        return self.count
    def __iter__(self):
        for row in self.db.execute('SELECT snapshot_json FROM session_question WHERE session_id=? ORDER BY question_index', (self.session_id,)):
            yield _load(row[0])
    def __getitem__(self, index):
        if type(index) is not int or not 0 <= index < self.count:
            raise IndexError(index)
        row = self.db.execute('SELECT snapshot_json FROM session_question WHERE session_id=? AND question_index=?', (self.session_id, index)).fetchone()
        if row is None:
            raise IndexError(index)
        return _load(row[0])


class RowIndex(Mapping):
    """Cross-table references use existing primary indexes, not an event map."""
    def __init__(self, db, table, key):
        self.db, self.table, self.key = db, table, key
    def __len__(self):
        return self.db.execute('SELECT count(*) FROM ' + self.table).fetchone()[0]
    def __iter__(self):
        for row in self.db.execute('SELECT ' + self.key + ' FROM ' + self.table):
            yield row[0]
    def __getitem__(self, value):
        row = self.db.execute('SELECT * FROM ' + self.table + ' WHERE ' + self.key + '=?', (value,)).fetchone()
        if row is None:
            raise KeyError(value)
        return dict(row)
