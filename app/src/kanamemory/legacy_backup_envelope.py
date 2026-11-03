"""Read-only retired gzip envelope; only used by explicit legacy conversion."""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import json
import re
import zlib

FORMAT = "kanamemory-learning-backup-envelope"
VERSION = 1
EXPANDED_LIMIT = 128 * 1024 * 1024
BLOCK = 64 * 1024
FIELDS = {"format", "version", "encoding", "expanded_bytes", "sha256", "data"}


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("学习备份封装含重复字段。")
        value[key] = item
    return value


def decode(data, *, outer_limit, expanded_limit=EXPANDED_LIMIT, cancel_check=None):
    """Return exact inner UTF-8 bytes; reject lies before expanding past them."""
    if not isinstance(data, bytes) or not 0 < len(data) <= outer_limit:
        raise ValueError("请选择 64 MiB 以内的学习记录 JSON 文件。")
    check = cancel_check or (lambda: None)
    check()
    try:
        envelope = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if (not isinstance(envelope, dict) or set(envelope) != FIELDS
                or envelope["format"] != FORMAT
                or type(envelope["version"]) is not int or envelope["version"] != VERSION
                or envelope["encoding"] != "gzip+base64"
                or type(envelope["expanded_bytes"]) is not int
                or not 0 < envelope["expanded_bytes"] <= expanded_limit
                or not isinstance(envelope["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", envelope["sha256"]) is None
                or not isinstance(envelope["data"], str)):
            raise ValueError("学习备份封装字段或版本不正确。")
        compressed = base64.b64decode(envelope["data"], validate=True)
        if len(compressed) > (outer_limit // 4) * 3:
            raise ValueError("压缩学习备份超过文件大小限制。")
        codec, output, digest = zlib.decompressobj(31), BytesIO(), hashlib.sha256()
        limit = min(expanded_limit, envelope["expanded_bytes"])
        for start in range(0, len(compressed), BLOCK):
            check()
            pending = compressed[start:start + BLOCK]
            if codec.eof:
                raise ValueError("学习备份含多余压缩流或尾随数据。")
            while pending:
                check()
                remaining = limit - output.tell()
                chunk = codec.decompress(pending, min(BLOCK, remaining + 1))
                if len(chunk) > remaining:
                    raise ValueError("学习备份实际展开长度超过声明或上限。")
                output.write(chunk)
                digest.update(chunk)
                if codec.unused_data:
                    raise ValueError("学习备份含多余压缩流或尾随数据。")
                pending = codec.unconsumed_tail
        if not codec.eof:
            raise ValueError("学习备份压缩流被截断。")
        if output.tell() != envelope["expanded_bytes"] or digest.hexdigest() != envelope["sha256"]:
            raise ValueError("学习备份长度或校验值不匹配。")
        return output.getvalue()
    except (UnicodeError, json.JSONDecodeError, zlib.error, RecursionError) as exc:
        raise ValueError("学习备份封装损坏。") from exc
