"""Shared strict scalar boundary before SQLite can coerce external records."""
from __future__ import annotations

import math


def validate_row(row, schema):
    """Accept app-owned PRAGMA table_info output; never trust an input schema."""
    columns = [field[1] for field in schema]
    if not isinstance(row, dict) or set(row) != set(columns):
        raise ValueError('学习记录字段与当前格式不符。')
    for field in schema:
        _, name, affinity, required, _, primary_key = field
        value = row[name]
        if value is None:
            if required or primary_key:
                raise ValueError('学习记录缺少必要字段。')
            continue
        valid = ((affinity == 'TEXT' and type(value) is str)
                 or (affinity == 'INTEGER' and type(value) is int)
                 or (affinity == 'REAL' and type(value) in (int, float) and math.isfinite(value)))
        if not valid:
            raise ValueError('学习记录字段类型不正确，未进行隐式转换。')
