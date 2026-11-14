"""Portable content packs must remain inert and independent of learner data."""
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from kanamemory.content_pack import (PERSONAL_REQUIRED_NAMESPACES, QUESTION_READINGS, ContentPackError,
                                    ShardedNamespace, clear_cache, decode_value, encode_value, load_namespace,
                                    namespace_document, validate_installed_pack, validate_pack,
                                    validate_question_dependencies, validate_question_readings)

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
    # Version 1 (whole-namespace) packs; the sharded layout's file-level checks
    # are covered in tests/test_content_pack_shards.py.
    source, pack = tmp_path / "source", tmp_path / "teaching.sqlite3"
    _write(source, "lesson", {"value": 1})
    _BUILDER.build(source, pack, profile="demo", layout="legacy")
    assert validate_pack(pack)["namespace_count"] == 1
    with closing(sqlite3.connect(pack)) as connection:
        connection.execute("UPDATE namespace SET payload_json=?", (json.dumps({"value": 2}),))
        connection.commit()
    with pytest.raises(ContentPackError, match="checksum"):
        validate_pack(pack)


# ------------------------------------------- question_dependencies (design 11 section 5)

_SHA = "a" * 64
_GOOD = {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {_SHA: {
    "words": ["word:b"], "grammar": ["grammar:g"], "kana": ["kana:a"], "oov": ["私"]}}}}}


def _dependencies(**changes):
    value = json.loads(json.dumps(_GOOD))
    value["items"]["word:a"]["vocab_meaning"][_SHA].update(changes)
    return value


@pytest.mark.parametrize("value", [
    _GOOD,
    {"schema_version": 1, "items": {}},
    {"schema_version": 1, "items": {"word:a": {}}},
    {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {_SHA: {}}}}},
    # Design 11 section 18: stem facts are subsets of their base lists and add no requirement.
    _dependencies(stem_words=["word:b"], stem_oov=["私"]),
    _dependencies(stem_words=[]),
    _dependencies(stem_oov=["私"]),
])
def test_question_dependencies_accepts_the_v1_contract_including_missing_items(value):
    assert validate_question_dependencies(value) is value


@pytest.mark.parametrize("value", [
    {"schema_version": 2, "items": {}},
    {"schema_version": 1},
    {"schema_version": 1, "items": {}, "extra": 1},
    {"schema_version": 1, "items": []},
    {"schema_version": 1, "items": {"": {}}},
    {"schema_version": 1, "items": {"word:a": {"Vocab": {}}}},
    {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {"abc": {}}}}},
    {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {_SHA.upper(): {}}}}},
    {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {_SHA: []}}}},
    _dependencies(readings=["x"]),
    _dependencies(words="word:b"),
    _dependencies(words=[f"word:{i}" for i in range(41)]),
    _dependencies(words=["word:b", "word:b"]),
    _dependencies(words=["word:b", ""]),
    _dependencies(words=["word:a"]),
    _dependencies(grammar=[7]),
    _dependencies(oov=["私" * 65]),
    _dependencies(kana=["k" * 1025]),
    _dependencies(stem_words=["word:c"]),            # not one of the template's required words
    _dependencies(stem_oov=["僕"]),                   # not one of its oov surfaces
    _dependencies(stem_words="word:b"),
    _dependencies(stem_words=["word:b", "word:b"]),
    _dependencies(stem_grammar=["grammar:g"]),       # only words can be stem facts
    {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {_SHA: {"stem_words": ["word:b"]}}}}},
    {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {_SHA: {"words": "word:b", "stem_words": ["word:b"]}}}}},
])
def test_question_dependencies_rejects_malformed_namespaces(value):
    with pytest.raises(ContentPackError):
        validate_question_dependencies(value)


@pytest.mark.parametrize("shard", [frozenset(), frozenset({"question_dependencies"})])
def test_built_packs_validate_question_dependencies_when_present(tmp_path, monkeypatch, shard):
    source, pack = tmp_path / "source", tmp_path / "teaching.sqlite3"
    _write(source, "lesson", {"values": [1]})
    _write(source, "question_dependencies", _GOOD)
    result = _BUILDER.build(source, pack, profile="demo", shard=shard)
    assert validate_pack(pack, deep=True)["namespace_count"] == 2
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(pack))
    clear_cache()
    loaded = load_namespace("question_dependencies", {})
    assert loaded["schema_version"] == 1 and dict(loaded["items"]["word:a"]) == _GOOD["items"]["word:a"]
    assert ("shards" in result) == (shard != frozenset()) or shard == frozenset()
    # A malformed entry never builds: the builder's deep validation rejects it.
    _write(source, "question_dependencies", _dependencies(words=["word:a"]))
    with pytest.raises(ContentPackError, match="dependency"):
        _BUILDER.build(source, pack, profile="demo", shard=shard)
    _write(source, "question_dependencies", {"schema_version": 2, "items": {}})
    with pytest.raises(ContentPackError, match="dependencies"):
        _BUILDER.build(source, pack, profile="demo", shard=shard)
    # Other namespaces keep their generic checks only; the pack still loads.
    assert load_namespace("lesson") == {"values": [1]}


def test_question_dependencies_is_optional_for_personal_packs():
    assert "question_dependencies" not in PERSONAL_REQUIRED_NAMESPACES


# ------------------------------------------- question_readings (design 11 section 7)

_READING_ENTRY = {"ids": ["word:b", "grammar:g"],
          "fields": {"prompt": [[0, 1, "た", 0], [3, 5, "きっぷ", -1]], "task": [[2, 4, "ぶんみゃく", 1]]},
          "options": {"買い物": [[0, 3, "かいもの", 0]], "3人": [[0, 2, "さんにん", -1]]}}
_READINGS_GOOD = {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {_SHA: _READING_ENTRY}}}}


def _readings(**changes):
    entry = {**json.loads(json.dumps(_READING_ENTRY)), **changes}
    entry = {key: value for key, value in entry.items() if value is not None}
    return {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {_SHA: entry}}}}


@pytest.mark.parametrize("value", [
    _READINGS_GOOD,
    {"schema_version": 1, "items": {}},
    {"schema_version": 1, "items": {"word:a": {}}},
    {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {}}}},
    _readings(ids=None, fields={"prompt": [[0, 2, "がっこう", -1]]}, options=None),      # no lesson: no "ids"
    _readings(fields=None),                                                               # options only
    _readings(fields={"prompt": [[0, 1, "らーめん", 0], [1, 2, "ゔぁ", 1]]}),             # ー and ゔ are kana
    _readings(options={"x" * 400: [[0, 1, "あ", -1]]}),
    _readings(ids=[f"word:{i}" for i in range(60)]),
])
def test_question_readings_accepts_the_v1_contract(value):
    assert validate_question_readings(value) is value


@pytest.mark.parametrize("value", [
    {"schema_version": 2, "items": {}},
    {"schema_version": True, "items": {}},
    {"schema_version": 1},
    {"schema_version": 1, "items": {}, "extra": 1},
    {"schema_version": 1, "items": []},
    {"schema_version": 1, "items": {"": {}}},
    {"schema_version": 1, "items": {"word:a": {"Vocab": {}}}},
    {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {"abc": _READING_ENTRY}}}},
    {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {_SHA.upper(): _READING_ENTRY}}}},
    {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {_SHA: {}}}}},            # an entry holds a span
    {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {_SHA: []}}}},
    _readings(readings={"切符": "きっぷ"}),                                               # unknown key
    _readings(fields=None, options=None),                                                 # ids alone
    _readings(ids=[]), _readings(ids="word:b"), _readings(ids=["word:b", "word:b"]), _readings(ids=["word:b", ""]),
    _readings(ids=["word:b", 7]), _readings(ids=[f"word:{i}" for i in range(61)]), _readings(ids=["k" * 1025, "x"]),
    _readings(fields={}), _readings(options={}), _readings(fields=[]), _readings(fields={"prompt": []}),
    _readings(fields={"Prompt": [[0, 1, "た", -1]]}), _readings(fields={"": [[0, 1, "た", -1]]}),
    _readings(fields={f"f{i}": [[0, 1, "た", -1]] for i in range(17)}),
    _readings(options={"": [[0, 1, "た", -1]]}), _readings(options={"x" * 401: [[0, 1, "た", -1]]}),
    _readings(options={f"選択{i}": [[0, 2, "せんたく", -1]] for i in range(61)}),
    _readings(fields={"prompt": [[0, 1, "た"]]}), _readings(fields={"prompt": [[0, 1, "た", -1, 0]]}),
    _readings(fields={"prompt": [(0, 1, "た", -1)]}), _readings(fields={"prompt": [{"start": 0}]}),
    _readings(fields={"prompt": [[1, 1, "た", -1]]}), _readings(fields={"prompt": [[2, 1, "た", -1]]}),
    _readings(fields={"prompt": [[-1, 1, "た", -1]]}), _readings(fields={"prompt": [[0.0, 1, "た", -1]]}),
    _readings(fields={"prompt": [[True, 2, "た", -1]]}), _readings(fields={"prompt": [[0, 100001, "た", -1]]}),
    _readings(fields={"prompt": [[3, 5, "きっぷ", -1], [0, 1, "た", 0]]}),                 # not sorted
    _readings(fields={"prompt": [[0, 3, "たべ", 0], [2, 5, "きっぷ", -1]]}),               # overlapping
    _readings(fields={"prompt": [[0, 1, "", -1]]}), _readings(fields={"prompt": [[0, 1, "タ", -1]]}),
    _readings(fields={"prompt": [[0, 1, "ta", -1]]}), _readings(fields={"prompt": [[0, 1, "た べ", -1]]}),
    _readings(fields={"prompt": [[0, 1, "食", -1]]}), _readings(fields={"prompt": [[0, 1, "あ" * 41, -1]]}),
    _readings(fields={"prompt": [[0, 1, 7, -1]]}),
    _readings(fields={"prompt": [[0, 1, "た", 2]]}), _readings(fields={"prompt": [[0, 1, "た", -2]]}),
    _readings(fields={"prompt": [[0, 1, "た", "0"]]}), _readings(fields={"prompt": [[0, 1, "た", False]]}),
    _readings(ids=None, fields={"prompt": [[0, 1, "た", 0]]}),                              # a ref needs "ids"
    _readings(fields={"prompt": [[i, i + 1, "あ", -1] for i in range(81)]}),
])
def test_question_readings_rejects_malformed_namespaces(value):
    with pytest.raises(ContentPackError):
        validate_question_readings(value)


@pytest.mark.parametrize("shard", [None, frozenset()])
def test_the_builder_stores_question_readings_per_item_and_validates_them(tmp_path, monkeypatch, shard):
    assert QUESTION_READINGS == "question_readings" and QUESTION_READINGS in _BUILDER.SHARDED_NAMESPACES
    assert QUESTION_READINGS not in PERSONAL_REQUIRED_NAMESPACES
    source, pack = tmp_path / "source", tmp_path / "teaching.sqlite3"
    other = {"vocab_usage": {"b" * 64: {"fields": {"prompt": [[0, 2, "がっこう", -1]]}}}}
    value = {"schema_version": 1, "items": {"word:a": {"vocab_meaning": {_SHA: _READING_ENTRY}}, "word:b": other}}
    _write(source, "lesson", {"values": [1]})
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(pack))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(tmp_path / "no-libraries"))
    kwargs = {} if shard is None else {"shard": shard}
    _BUILDER.build(source, pack, profile="demo", **kwargs)   # a pack without the namespace builds and loads
    clear_cache()
    assert load_namespace(QUESTION_READINGS, {}) == {} and load_namespace("lesson") == {"values": [1]}
    _write(source, QUESTION_READINGS, value)
    result = _BUILDER.build(source, pack, profile="demo", **kwargs)
    assert validate_pack(pack, deep=True)["namespace_count"] == 2
    clear_cache()
    loaded = load_namespace(QUESTION_READINGS, {})
    assert loaded["schema_version"] == 1 and list(loaded["items"]) == ["word:a", "word:b"]
    assert dict(loaded["items"]["word:a"]) == value["items"]["word:a"]
    assert dict(loaded["items"]["word:b"]) == value["items"]["word:b"]
    if shard is None:  # one shard per content id plus the schema version; only the item read is decoded
        assert result["shards"] == 4 and isinstance(loaded, ShardedNamespace)
        assert loaded.loaded_keys() == {"schema_version", "items/word:a", "items/word:b"}
    # A malformed entry never builds: the builder's deep validation rejects it, whole or sharded.
    _write(source, QUESTION_READINGS, _readings(fields={"prompt": [[0, 1, "タ", -1]]}))
    with pytest.raises(ContentPackError, match="reading"):
        _BUILDER.build(source, pack, profile="demo", **kwargs)
    _write(source, QUESTION_READINGS, {"schema_version": 2, "items": {}})
    with pytest.raises(ContentPackError, match="readings"):
        _BUILDER.build(source, pack, profile="demo", **kwargs)
    clear_cache()
