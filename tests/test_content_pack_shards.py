"""Sharded (v2) teaching packs: lazy item decoding and file-level checksums."""
from contextlib import closing
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from kanamemory import content_pack
from kanamemory.content_pack import (ContentPackError, ShardedNamespace, clear_cache, load_namespace,
                                    namespace_document, shard_index, validate_installed_pack, validate_pack)

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("build_teaching_content", _ROOT / "tools/build_teaching_content.py")
_BUILDER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BUILDER)

PRACTICE = {
    "schema_version": 1,
    "revision": "synthetic-v1",
    "items": {
        "word:a": {"item_fingerprint": "f" * 64, "item_revision": "1",
                   "templates": {"vocab_meaning": [{"prompt": "a"}], "vocab_recall": []},
                   "history": {}, "contexts": {}},
        "word:b": {"item_fingerprint": "e" * 64, "item_revision": "2",
                   "templates": {"vocab_meaning": [{"prompt": "b", "pair": (1, 2)}]}},
    },
    "provenance": [{"source": "synthetic", "tags": {"x", "y"}}],
    "template_retirements": {"word:a": {}},
}


def _write(source, name, value):
    source.mkdir(exist_ok=True)
    (source / (name + ".json")).write_text(json.dumps(namespace_document(name, value)), encoding="utf-8")


@pytest.fixture
def pack(tmp_path, monkeypatch):
    source, path = tmp_path / "source", tmp_path / "teaching.sqlite3"
    _write(source, "varied_practice", PRACTICE)
    _write(source, "lesson", {"values": [1, (2, 3)]})
    _BUILDER.build(source, path, profile="demo")
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(path))
    clear_cache()
    yield path
    clear_cache()


def _tamper(path, sql, *args):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(sql, args)
        connection.commit()
    clear_cache()


def test_sharded_namespace_equals_source_and_decodes_items_on_first_access(pack, monkeypatch):
    reads = []
    original = content_pack._read_shard
    monkeypatch.setattr(content_pack, "_read_shard",
                        lambda path, name, key, sha, size: reads.append(key) or original(path, name, key, sha, size))
    value = load_namespace("varied_practice", {})
    assert isinstance(value, ShardedNamespace) and reads == []
    assert list(value) == list(PRACTICE) and list(value["items"]) == ["word:a", "word:b"]
    assert "word:a" in value["items"] and "missing" not in value["items"] and reads == []
    assert value.get("schema_version") == 1 and reads == ["top:schema_version"]
    record = value.get("items", {}).get("word:b", {})
    assert record == PRACTICE["items"]["word:b"] and type(record["templates"]["vocab_meaning"][0]["pair"]) is tuple
    assert reads == ["top:schema_version", "item:word:b"]
    assert value["items"].get("word:b") is record and reads.count("item:word:b") == 1
    assert value["items"].get("unknown", {}) == {}
    assert value.loaded_keys() == {"schema_version", "items/word:b"}
    # Equality, deepcopy and a full walk match the plain source mapping.
    assert value == PRACTICE
    copied = deepcopy(value)
    assert type(copied) is dict and type(copied["items"]) is dict and copied == PRACTICE
    copied["template_retirements"]["word:b"] = {}
    assert "word:b" not in value["template_retirements"]
    with pytest.raises(TypeError):
        value["items"] = {}


def test_whole_namespaces_decode_fresh_and_remain_isolated(pack):
    first = load_namespace("lesson")
    assert first == {"values": [1, (2, 3)]} and type(first) is dict
    first["values"].append(9)
    assert load_namespace("lesson") == {"values": [1, (2, 3)]}
    assert load_namespace("absent", {"x": []}) == {"x": []}


def test_separate_loads_do_not_share_decoded_records(pack):
    one, two = load_namespace("varied_practice"), load_namespace("varied_practice")
    one["items"]["word:a"]["templates"]["vocab_recall"].append("local edit")
    assert two["items"]["word:a"]["templates"]["vocab_recall"] == []


def test_startup_validation_reads_manifest_only_and_shards_are_checked_when_read(pack):
    result = validate_installed_pack()
    assert result["version"] == "2" and result["namespace_count"] == 2
    assert result["shard_count"] == 1 + 4 + 2  # lesson, four top-level keys, two items
    assert result["manifest_sha256"] == result["content_sha256"]
    assert validate_pack(pack, deep=True)["shard_count"] == result["shard_count"]
    assert shard_index("varied_practice") == {"vocab_meaning": {"f" * 64: "word:a", "e" * 64: "word:b"},
                                              "vocab_recall": {"f" * 64: "word:a"}}
    assert shard_index("lesson") == {}


def _same_size_tamper(path):
    with closing(sqlite3.connect(path)) as connection:
        raw = connection.execute("SELECT payload FROM shard WHERE shard_key='item:word:b'").fetchone()[0]
    changed = raw.replace(b'"b"', b'"c"', 1)
    assert changed != raw and len(changed) == len(raw)
    _tamper(path, "UPDATE shard SET payload=? WHERE shard_key='item:word:b'", changed)


def test_tampered_shard_is_rejected_on_access_and_by_deep_validation(pack):
    _same_size_tamper(pack)
    validate_pack(pack)  # Same size and an intact manifest: startup stays cheap.
    with pytest.raises(ContentPackError, match="checksum"):
        validate_pack(pack, deep=True)
    value = load_namespace("varied_practice")
    assert value["items"]["word:a"]["item_revision"] == "1"
    with pytest.raises(ContentPackError, match="checksum"):
        value["items"]["word:b"]


def test_shard_already_loaded_before_tampering_is_not_reread(pack):
    value = load_namespace("varied_practice")
    record = value["items"]["word:b"]
    _same_size_tamper(pack)
    assert value["items"]["word:b"] is record


@pytest.mark.parametrize("tampering", [
    "UPDATE shard SET payload=payload||X'20' WHERE shard_key='item:word:a'",
    "UPDATE shard SET sha256='" + "0" * 64 + "' WHERE shard_key='item:word:a'",
    "UPDATE shard SET kinds_json='[\"vocab_recall\"]' WHERE shard_key='item:word:b'",
    "UPDATE shard SET item_fingerprint='" + "1" * 64 + "' WHERE shard_key='item:word:b'",
    "DELETE FROM shard WHERE shard_key='item:word:b'",
    "DELETE FROM shard WHERE shard_key='top:provenance'",
    "DELETE FROM shard WHERE namespace='lesson'",
    "INSERT INTO shard SELECT namespace,'item:word:z',99,kinds_json,item_fingerprint,size,sha256,payload "
    "FROM shard WHERE shard_key='item:word:a'",
    "UPDATE namespace SET structure_json='{\"top\":[\"items\"],\"items_key\":\"items\"}' WHERE name='varied_practice'",
    "UPDATE namespace SET layout='whole' WHERE name='varied_practice'",
    "UPDATE metadata SET value='" + "2" * 64 + "' WHERE key='manifest_sha256'",
    "DELETE FROM metadata WHERE key='manifest_sha256'",
    "UPDATE metadata SET value='personal' WHERE key='profile'",
    "UPDATE metadata SET value='3' WHERE key='version'",
])
def test_broken_manifest_missing_or_extra_shard_rejects_the_pack(pack, tampering):
    _tamper(pack, tampering)
    with pytest.raises(ContentPackError):
        validate_pack(pack)
    with pytest.raises(ContentPackError):
        load_namespace("lesson", {})


def test_pack_replacement_refreshes_manifest(pack, tmp_path):
    source = tmp_path / "source"
    _write(source, "lesson", {"values": [5]})
    _BUILDER.build(source, pack, profile="demo")
    assert load_namespace("lesson") == {"values": [5]}


def test_namespace_not_eligible_for_sharding_stays_whole(tmp_path, monkeypatch):
    source, path = tmp_path / "source", tmp_path / "teaching.sqlite3"
    _write(source, "context_choice", {"items": ["not", "records"]})
    _BUILDER.build(source, path, profile="demo")
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(path))
    clear_cache()
    assert load_namespace("context_choice") == {"items": ["not", "records"]}
    clear_cache()
