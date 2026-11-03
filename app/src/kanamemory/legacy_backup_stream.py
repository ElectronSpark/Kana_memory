"""Read-only decoder for retired large backups; conversion is its sole caller."""
from __future__ import annotations
import base64
import hashlib
from io import BytesIO
import json
import sqlite3
import zlib
from .record_transport import DiskTables, DiskRows, _load

FORMAT = 'kanamemory-learning-backup-stream'
VERSION = 1
INNER_VERSION = 10
BLOCK = 64 * 1024
EXPANDED_LIMIT = 1024 * 1024 * 1024
ROW_LIMIT = 129 * 1024 * 1024
TABLE_ROW_LIMIT = 1000000
PREFIX = ('{"format":"' + FORMAT + '"').encode()

def names():
    from .legacy_record_conversion import legacy_table_names
    return legacy_table_names(10)

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
        if (not isinstance(header, dict) or set(header) != {'format','version','inner_version','algorithm_version','created_at_utc','tables'}
                or header['format'] != FORMAT or type(header['version']) is not int or header['version'] != VERSION
                or type(header['inner_version']) is not int or header['inner_version'] != INNER_VERSION
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
        return {'format': 'kanamemory-learning-backup', 'version': INNER_VERSION,
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
