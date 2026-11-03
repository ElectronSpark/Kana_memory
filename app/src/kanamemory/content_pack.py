"""Read-only teaching data. A content pack never contains executable code.

The pack is independent of learner databases: loading content neither migrates
nor writes saved cards, review history, or unfinished queues.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import closing
from functools import lru_cache
import json
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3

FORMAT = "kanamemory-teaching-content"
VERSION = 1
NAMESPACE_FORMAT = "kanamemory-teaching-namespace"
_TAG = "__km_content_type__"
_NAME = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_MAX_PAYLOAD_BYTES = 32 * 1024 * 1024
PERSONAL_REQUIRED_NAMESPACES = frozenset({
    "learning_content", "learning_questions", "grammar_curriculum", "exam_grammar_curriculum",
    "usage_grammar_curriculum", "pronunciation_curriculum", "vocabulary_catalog", "vocabulary_lessons",
    "example_catalogue", "example_expansion_grammar_a", "example_expansion_grammar_a_tail",
    "example_expansion_grammar_b", "example_expansion_pronunciation", "example_expansion_words",
    "example_expansion_signatures", "structured_lessons_grammar_a", "structured_lessons_grammar_b",
    "structured_lessons_pronunciation", "structured_lessons_words", "lesson_diagrams_grammar_a",
    "lesson_diagrams_grammar_b", "lesson_diagrams_words_sounds", "study_path_catalog",
    "course_prerequisites", "question_contexts", "presentation_metadata",
})


class ContentPackError(ValueError):
    """The supplied data is not a supported teaching-content pack."""


def encode_value(value):
    """Encode inert values, preserving tuples/sets used by public legacy APIs."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is dict:
        if _TAG in value or any(type(key) is not str for key in value):
            raise ContentPackError("Content mappings need ordinary string keys")
        return {key: encode_value(item) for key, item in value.items()}
    if type(value) is list:
        return [encode_value(item) for item in value]
    if type(value) in (tuple, set, frozenset):
        items = [encode_value(item) for item in value]
        if type(value) in (set, frozenset):
            items.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        return {_TAG: type(value).__name__, "items": items}
    raise ContentPackError("Unsupported content value type: " + type(value).__name__)


def decode_value(value):
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is list:
        return [decode_value(item) for item in value]
    if type(value) is dict:
        if _TAG not in value:
            return {key: decode_value(item) for key, item in value.items()}
        if set(value) != {_TAG, "items"} or not isinstance(value["items"], list):
            raise ContentPackError("Invalid typed content value")
        kind = value[_TAG]
        constructor = {"tuple": tuple, "set": set, "frozenset": frozenset}.get(kind) if isinstance(kind, str) else None
        if constructor is None:
            raise ContentPackError("Unknown content value type")
        try:
            return constructor(decode_value(item) for item in value["items"])
        except (TypeError, ValueError) as error:
            raise ContentPackError("Invalid content collection") from error
    raise ContentPackError("Unsupported JSON content value")


def namespace_document(name, value):
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ContentPackError("Invalid namespace name")
    return {"format": NAMESPACE_FORMAT, "version": VERSION,
            "name": name, "payload": encode_value(value)}


def pack_path() -> Path:
    """An explicit path supports isolated builds/tests without private content."""
    configured = os.environ.get("KANAMEMORY_TEACHING_PACK")
    return Path(configured) if configured else Path(__file__).with_name("resources") / "teaching_content.sqlite3"


def _validate_metadata(connection):
    metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    if metadata.get("format") != FORMAT or metadata.get("version") != str(VERSION):
        raise ContentPackError("Unsupported teaching content pack")
    profile = metadata.get("profile")
    if profile not in {"personal", "demo"}:
        raise ContentPackError("Unknown teaching content profile")
    names = {row[0] for row in connection.execute("SELECT name FROM namespace")}
    try:
        declared = json.loads(metadata.get("namespaces", "[]"))
        if (not isinstance(declared, list) or not declared
                or any(not isinstance(value, str) or not _NAME.fullmatch(value) for value in declared)
                or len(declared) != len(set(declared)) or set(declared) != names):
            raise ContentPackError("Teaching content namespace manifest mismatch")
    except (TypeError, ValueError) as error:
        raise ContentPackError("Invalid teaching content namespace manifest") from error
    if profile == "personal" and not PERSONAL_REQUIRED_NAMESPACES <= names:
        raise ContentPackError("Personal teaching content pack is incomplete")
    return metadata


def validate_pack(path):
    """Require a complete, supported, intact pack before opening learner state."""
    path = Path(path)
    if not path.is_file():
        raise ContentPackError("Teaching content pack is missing")
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            metadata = _validate_metadata(connection)
            documents = list(connection.execute("SELECT name,payload_json FROM namespace ORDER BY name"))
            for _, raw in documents:
                if not isinstance(raw, str) or len(raw.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
                    raise ContentPackError("Teaching namespace exceeds its size limit")
                decode_value(json.loads(raw))
            digest = hashlib.sha256(json.dumps(documents, ensure_ascii=False).encode("utf-8")).hexdigest()
            if digest != metadata.get("content_sha256"):
                raise ContentPackError("Teaching content checksum mismatch")
            return {**metadata, "namespace_count": len(documents)}
    except (sqlite3.Error, TypeError, ValueError, RecursionError) as error:
        if isinstance(error, ContentPackError):
            raise
        raise ContentPackError("Invalid teaching content pack") from error


def validate_installed_pack():
    return validate_pack(pack_path())


@lru_cache(maxsize=128)
def _read_namespace(path, modified_ns, size, name):
    del modified_ns, size  # The values invalidate the cache after replacing a pack.
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            _validate_metadata(connection)
            row = connection.execute("SELECT payload_json FROM namespace WHERE name=?", (name,)).fetchone()
    except sqlite3.Error as error:
        raise ContentPackError("Cannot read teaching content pack") from error
    if row is None:
        return False, None
    raw = row[0]
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ContentPackError("Teaching namespace exceeds its size limit")
    try:
        return True, decode_value(json.loads(raw))
    except (ValueError, TypeError, RecursionError) as error:
        raise ContentPackError("Invalid teaching namespace") from error


def load_namespace(name, default=None):
    """Return an isolated value; absent packs/namespaces use an explicit default.

    Missing optional content is allowed in clean/demo builds. An existing but
    invalid pack is an error, never a reason to execute or restore embedded data.
    """
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ContentPackError("Invalid namespace name")
    path = pack_path()
    try:
        stat = path.stat()
    except FileNotFoundError:
        return deepcopy(default)
    found, value = _read_namespace(str(path.resolve()), stat.st_mtime_ns, stat.st_size, name)
    return deepcopy(value if found else default)


def clear_cache():
    _read_namespace.cache_clear()
