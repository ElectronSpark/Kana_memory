"""Portable content packs must remain inert and independent of learner data."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from kanamemory.content_pack import (ContentPackError, clear_cache, decode_value,
                                    encode_value, load_namespace, namespace_document,
                                    validate_installed_pack, validate_pack)

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("build_teaching_content", _ROOT / "tools/build_teaching_content.py")
_BUILDER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BUILDER)


def _write(source, name, value):
    source.mkdir(exist_ok=True)
    (source / (name + ".json")).write_text(json.dumps(namespace_document(name, value)), encoding="utf-8")


def test_roundtrip_preserves_order_collection_types_and_plain_text():
    original = {"second": ["__import__('os').remove('learner.db')"], "first": (1, {"a", "b"}),
                "frozen": frozenset({"x"}), "mapping": {"z": 1, "a": 2}}
    result = decode_value(json.loads(json.dumps(encode_value(original))))
    assert result == original
    assert list(result) == list(original)
    assert list(result["mapping"]) == ["z", "a"]
    assert type(result["first"]) is tuple
    assert type(result["first"][1]) is set
    assert type(result["frozen"]) is frozenset


@pytest.mark.parametrize("value", [object(), float("nan"), {1: "integer key"}, {"__km_content_type__": "tuple"}])
def test_writer_rejects_non_data_values(value):
    with pytest.raises(ContentPackError):
        encode_value(value)


@pytest.mark.parametrize("value", [
    {"__km_content_type__": "python", "items": ["print('execute')"]},
    {"__km_content_type__": "tuple", "items": [], "code": "x"},
    {"__km_content_type__": "set", "items": [[1]]},
    {"__km_content_type__": {"python": "x"}, "items": []},
])
def test_reader_rejects_unknown_or_invalid_typed_values(value):
    with pytest.raises(ContentPackError):
        decode_value(value)


def test_load_is_read_only_cloned_and_refreshes_after_pack_replace(tmp_path, monkeypatch):
    source, pack = tmp_path / "source", tmp_path / "teaching.sqlite3"
    _write(source, "lesson", {"values": [1]})
    _BUILDER.build(source, pack, profile="demo")
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(pack))
    before = pack.read_bytes()
    loaded = load_namespace("lesson")
    loaded["values"].append(99)
    assert load_namespace("lesson") == {"values": [1]}
    assert pack.read_bytes() == before
    _write(source, "lesson", {"values": [1, 2]})
    _BUILDER.build(source, pack, profile="demo")
    assert load_namespace("lesson") == {"values": [1, 2]}


def test_missing_optional_pack_or_namespace_uses_independent_default(tmp_path, monkeypatch):
    pack = tmp_path / "teaching.sqlite3"
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(pack))
    default = {"items": []}
    load_namespace("optional", default)["items"].append(3)
    assert default == {"items": []}
    assert not pack.exists()
    with pytest.raises(ContentPackError, match="missing"):
        validate_installed_pack()
    _write(tmp_path / "source", "other", {})
    _BUILDER.build(tmp_path / "source", pack, profile="demo")
    assert load_namespace("optional", default) == default
    assert validate_installed_pack()["profile"] == "demo"


def test_bad_pack_is_error_not_silent_content_fallback(tmp_path, monkeypatch):
    pack = tmp_path / "bad.sqlite3"
    pack.write_bytes(b"not a sqlite database")
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(pack))
    with pytest.raises(ContentPackError):
        load_namespace("lesson", {})


def test_future_pack_version_is_rejected(tmp_path, monkeypatch):
    source, pack = tmp_path / "source", tmp_path / "teaching.sqlite3"
    _write(source, "lesson", {})
    _BUILDER.build(source, pack, profile="demo")
    with closing(sqlite3.connect(pack)) as connection:
        connection.execute("UPDATE metadata SET value='99' WHERE key='version'")
        connection.commit()
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(pack))
    clear_cache()
    with pytest.raises(ContentPackError):
        load_namespace("lesson")


def test_builder_never_replaces_learner_database(tmp_path):
    source, learner = tmp_path / "source", tmp_path / "learner.sqlite3"
    _write(source, "lesson", {})
    with closing(sqlite3.connect(learner)) as connection:
        connection.execute("CREATE TABLE cards(id TEXT PRIMARY KEY, due TEXT)")
        connection.execute("INSERT INTO cards VALUES ('saved', '2027-01-01')")
        connection.commit()
    before = learner.read_bytes()
    with pytest.raises(ContentPackError):
        _BUILDER.build(source, learner, profile="demo")
    assert learner.read_bytes() == before


def test_duplicate_namespaces_are_rejected(tmp_path):
    source = tmp_path / "source"
    _write(source, "lesson", {})
    (source / "duplicate.json").write_text((source / "lesson.json").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ContentPackError):
        _BUILDER.build(source, tmp_path / "pack.sqlite3", profile="demo")


def test_renamed_input_files_still_build_a_valid_pack(tmp_path, monkeypatch):
    source, pack = tmp_path / "source", tmp_path / "teaching.sqlite3"
    _write(source, "lesson_a", {"text": "first namespace"})
    _write(source, "lesson_z", {"text": "last namespace"})
    # File sorting deliberately opposes namespace sorting. Filenames are not
    # part of the content format and can change during ordinary authoring.
    (source / "lesson_a.json").rename(source / "99-last-file.json")
    (source / "lesson_z.json").rename(source / "01-first-file.json")
    result = _BUILDER.build(source, pack, profile="demo")
    validated = validate_pack(pack)
    assert validated["namespace_count"] == 2
    assert validated["content_sha256"] == result["content_sha256"]
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(pack))
    assert load_namespace("lesson_a") == {"text": "first namespace"}
    assert load_namespace("lesson_z") == {"text": "last namespace"}


def test_personal_build_cannot_silently_omit_courses(tmp_path):
    _write(tmp_path / "source", "learning_content", {})
    with pytest.raises(ContentPackError, match="incomplete"):
        _BUILDER.build(tmp_path / "source", tmp_path / "pack.sqlite3", profile="personal")


@pytest.mark.parametrize("tampering", [
    "UPDATE metadata SET value='unknown' WHERE key='profile'",
    "UPDATE metadata SET value='personal' WHERE key='profile'",
    "DELETE FROM namespace WHERE name='lesson'",
])
def test_invalid_profile_or_deleted_namespace_is_rejected(tmp_path, monkeypatch, tampering):
    source, pack = tmp_path / "source", tmp_path / "teaching.sqlite3"
    _write(source, "lesson", {})
    _BUILDER.build(source, pack, profile="demo")
    with closing(sqlite3.connect(pack)) as connection:
        connection.execute(tampering)
        connection.commit()
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(pack))
    with pytest.raises(ContentPackError):
        load_namespace("lesson", {})
    with pytest.raises(ContentPackError):
        validate_pack(pack)


def test_startup_validates_content_checksum_before_using_learner_state(tmp_path):
    source, pack = tmp_path / "source", tmp_path / "teaching.sqlite3"
    _write(source, "lesson", {"value": 1})
    _BUILDER.build(source, pack, profile="demo")
    assert validate_pack(pack)["namespace_count"] == 1
    with closing(sqlite3.connect(pack)) as connection:
        connection.execute("UPDATE namespace SET payload_json=?", (json.dumps({"value": 2}),))
        connection.commit()
    with pytest.raises(ContentPackError, match="checksum"):
        validate_pack(pack)
