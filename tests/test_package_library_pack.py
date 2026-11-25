"""tools/package_library_pack.py: which ids a pack claims in provides.items.

Synthetic data only. A per-item side table (question_readings, question_dependencies,
varied_practice: {"items": {content id: record}}) holds records about lessons, not lessons;
only the learning-catalogue tables define provided items and their fingerprints. An
installed extension library's own question_readings document merges per content id.
"""
import copy
import importlib.util
import json
from pathlib import Path
import re
import zipfile

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_pack
from kanamemory.content_library_service import ContentLibraryService
from kanamemory.domain import StudyStore
from kanamemory.material_review import item_fingerprint
from support.content_library_support import (CORE_ITEMS, CORE_NAMESPACES, build_core_resources, extension_namespaces,
                                             pack_bytes)
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)

ROOT = Path(__file__).resolve().parents[1]
_SHA = "a" * 64
READINGS = {"schema_version": 1, "items": {
    "word:demo:mizu": {"vocab_meaning": {_SHA: {"ids": ["word:demo:mizu"], "fields": {"prompt": [[0, 1, "みず", 0]]}}}},
    "grammar:demo:desu": {"grammar_meaning": {_SHA: {"fields": {"prompt": [[0, 2, "がくせい", -1]]}}}}}}
DEPENDENCIES = {"schema_version": 1, "items": {
    "grammar:demo:desu": {"grammar_meaning": {_SHA: {"words": ["word:demo:mizu"]}}}}}


def _package(tmp_path, **options):
    spec = importlib.util.spec_from_file_location("package_library_pack", ROOT / "tools" / "package_library_pack.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    namespaces = copy.deepcopy(CORE_NAMESPACES)
    # "a_practice" sorts before "lesson": a side table read first must not lend its records' fingerprints.
    namespaces.update(question_readings=READINGS, question_dependencies=DEPENDENCIES,
                      a_practice={"items": {"word:demo:mizu": {"item_fingerprint": "f" * 64, "templates": {}}}})
    resources = build_core_resources(tmp_path / "resources", namespaces)
    result = tool.package(resources, tmp_path / "pack.zip", **options)
    return result, rules.validate_library_pack(tmp_path / "pack.zip").manifest


def test_side_tables_do_not_claim_the_lessons_they_annotate(tmp_path):
    result, manifest = _package(tmp_path, library_id="author.readings", extension=True,
                                namespaces=["question_readings", "question_dependencies", "a_practice"])
    assert result["provides"] == [] and result["items"] == 0 and result["namespaces"] == 3
    assert manifest["provides"] == {"items": [], "namespaces": ["a_practice", "question_dependencies", "question_readings"]}
    assert manifest["fingerprints"] == {} and manifest["revisions"] == {} and manifest["references"] == []
    # Installing it beside the core library is not a conflict or an undeclared override of the core lessons.
    installed = {cid: item_fingerprint(item) for cid, item in CORE_ITEMS.items()}
    precheck = rules.precheck_pack(manifest, installed_fingerprints=installed, learned_ids=set(CORE_ITEMS),
                                   installed_library_ids={rules.CORE_LIBRARY_ID})
    assert precheck.conflicting_ids == () and precheck.undeclared_overrides == () and precheck.new_item_count == 0
    assert precheck.unknown_fingerprint_count == 0 and precheck.blocking_reasons() == ()


def test_the_oldest_app_version_goes_into_the_manifest_only_as_a_comparable_number(tmp_path):
    _, plain = _package(tmp_path / "plain", library_id="author.plain", extension=True, namespaces=["lesson"])
    assert "app_min_version" not in plain["requires"]
    _, manifest = _package(tmp_path / "versioned", library_id="author.versioned", extension=True,
                           namespaces=["lesson"], app_min_version=" 0.36.0 ")
    assert manifest["requires"] == {**plain["requires"], "app_min_version": "0.36.0"}
    assert rules.app_too_old(manifest, "0.35.0") == "0.36.0" and rules.app_too_old(manifest, "0.36.0") is None
    for bad in ("", "next", "0.36.0-beta", "v1"):
        with pytest.raises(ValueError, match="app_min_version must be a dotted number"):
            _package(tmp_path / ("bad" + str(len(bad))), library_id="author.bad", extension=True,
                     namespaces=["lesson"], app_min_version=bad)


def test_lessons_are_provided_with_their_own_fingerprints(tmp_path):
    result, manifest = _package(tmp_path, library_id="author.lessons", extension=True,
                                namespaces=["a_practice", "lesson", "question_readings"])
    assert sorted(result["provides"]) == sorted(CORE_ITEMS) == sorted(manifest["provides"]["items"])
    assert manifest["fingerprints"] == {cid: item_fingerprint(item) for cid, item in CORE_ITEMS.items()}
    assert manifest["revisions"] == {cid: "1" for cid in CORE_ITEMS}


def test_rewritten_ids_still_cover_side_table_keys(tmp_path):
    result, manifest = _package(tmp_path, library_id="author.copy", extension=True, rewrite_ids=True,
                                namespaces=["lesson", "question_readings"])
    derived = sorted(f"{cid.partition(':')[0]}:author.copy:{cid.partition(':')[2]}" for cid in CORE_ITEMS)
    assert sorted(result["provides"]) == derived == sorted(manifest["fingerprints"])
    with zipfile.ZipFile(tmp_path / "pack.zip") as archive:
        readings = json.loads(archive.read("teaching/question_readings.json"))["payload"]
    assert sorted(readings["items"]) == derived
    assert readings["items"]["word:author.copy:demo:mizu"]["vocab_meaning"][_SHA]["ids"] == ["word:author.copy:demo:mizu"]


@pytest.mark.parametrize("core_has_readings", [True, False])
def test_an_extension_library_ships_readings_for_its_own_items(tmp_path, monkeypatch, core_has_readings):
    namespaces = copy.deepcopy(CORE_NAMESPACES)
    if core_has_readings:
        namespaces["question_readings"] = READINGS
    resources = build_core_resources(tmp_path / "resources", namespaces)
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(tmp_path / "libraries"))
    content_pack.clear_cache()
    own = {"grammar_meaning": {_SHA: {"ids": ["grammar:ext:masu"], "fields": {"prompt": [[0, 1, "た", 0]]}}}}
    other = {"vocab_meaning": {_SHA: {"fields": {"prompt": [[0, 1, "すい", -1]]}}}}   # an entry for a core lesson
    extension = {"lesson": extension_namespaces()["lesson"],
                 "question_readings": {"schema_version": 1, "items": {"grammar:ext:masu": own, "word:demo:mizu": other}}}
    store = StudyStore(tmp_path / "learner", time_zone="UTC")
    try:
        service = ContentLibraryService(store, catalogue_ids=lambda: set(CORE_ITEMS), catalogue_fingerprints=lambda: {},
                                        learned_ids=set, word_exists=lambda word_id: False)
        inspection = service.inspect_bytes(pack_bytes("author.n3", extension, items=["grammar:ext:masu"]), "lib.zip")
        assert inspection.precheck.blocking_reasons() == () and inspection.precheck.conflicting_ids == ()
        service.install(inspection)
        content_pack.clear_cache()  # "restart"
        merged = content_pack.load_namespace(content_pack.QUESTION_READINGS, {})
        core_ids = set(READINGS["items"]) if core_has_readings else set()
        assert merged["schema_version"] == 1 and set(merged["items"]) == core_ids | {"word:demo:mizu", "grammar:ext:masu"}
        assert dict(merged["items"]["grammar:ext:masu"]) == own
        content_pack.validate_question_readings(
            {"schema_version": 1, "items": {cid: dict(entry) for cid, entry in merged["items"].items()}})
        report = content_pack.library_merge_report().get("question_readings", {})
        if core_has_readings:  # the core entry of a core lesson stays; the other library's is a reported conflict
            assert dict(merged["items"]["word:demo:mizu"]) == READINGS["items"]["word:demo:mizu"]
            assert report["conflicts"] == [("items", "word:demo:mizu", rules.CORE_LIBRARY_ID, "author.n3")]
        else:
            assert dict(merged["items"]["word:demo:mizu"]) == other and not report.get("conflicts")
        assert content_pack.library_status() == {"author.n3": {"state": "ready", "reason": None}}
    finally:
        store.close()
        content_pack.clear_cache()


def test_a_core_pack_carries_the_teaching_database_as_its_only_teaching_member(tmp_path):
    result, manifest = _package(tmp_path)
    report = rules.validate_library_pack(tmp_path / "pack.zip")
    assert manifest["layers"]["teaching"] == [rules.TEACHING_PACK_FILE] and report.teaching_documents == ()
    with zipfile.ZipFile(tmp_path / "pack.zip") as archive:
        members = archive.namelist()
    assert [name for name in members if name.startswith("teaching/")] == [rules.TEACHING_PACK_FILE]
    database = tmp_path / "resources" / "teaching_content.sqlite3"
    assert manifest["provides"]["namespaces"] == sorted(name for name, _ in content_pack.read_pack_namespaces(database))
    assert result["namespaces"] == len(manifest["provides"]["namespaces"]) == 5
    # The namespaces are still walked: lessons are provided with their fingerprints, side tables claim nothing.
    assert sorted(manifest["provides"]["items"]) == sorted(CORE_ITEMS)
    assert manifest["fingerprints"] == {cid: item_fingerprint(item) for cid, item in CORE_ITEMS.items()}
    assert report.has_teaching_pack and report.has_lexicon and report.total_bytes == sum(
        info["bytes"] for info in manifest["files"].values())


def test_an_extension_pack_still_carries_one_json_document_per_namespace(tmp_path):
    _, manifest = _package(tmp_path, library_id="author.json", extension=True, namespaces=["lesson", "question_readings"])
    assert manifest["layers"]["teaching"] == ["teaching/lesson.json", "teaching/question_readings.json"]
    assert rules.TEACHING_PACK_FILE not in manifest["files"]
    report = rules.validate_library_pack(tmp_path / "pack.zip")
    assert [name for name, _ in report.teaching_documents] == ["lesson", "question_readings"]


def test_a_pack_over_the_size_limits_stops_before_writing_with_a_breakdown_by_kind(tmp_path, monkeypatch):
    _, manifest = _package(tmp_path / "fits")
    total = sum(info["bytes"] for info in manifest["files"].values())
    monkeypatch.setattr(rules, "MAX_UNPACKED_BYTES", total - 1)  # the members together (section 16.10)
    with pytest.raises(ValueError) as caught:
        _package(tmp_path / "over")
    message = str(caught.value)
    assert not (tmp_path / "over" / "pack.zip").exists()
    assert f"over MAX_UNPACKED_BYTES {total - 1:,}" in message and "by 1" in message and "Bytes by kind:" in message
    sqlite_bytes = manifest["files"][rules.TEACHING_PACK_FILE]["bytes"]
    assert re.search(rf"teaching sqlite +1 files +{sqlite_bytes:,} ", message)
    assert "lexicon" in message and "other" in message and "teaching json" not in message
    assert "Largest members: " + rules.TEACHING_PACK_FILE in message
    monkeypatch.setattr(rules, "MAX_UNPACKED_BYTES", total + 10 ** 6)
    monkeypatch.setattr(rules, "MAX_TEACHING_BYTES", 10)  # one namespace document of an extension pack
    with pytest.raises(ValueError, match=r"teaching/lesson\.json is [\d,]+ bytes, over MAX_TEACHING_BYTES 10") as caught:
        _package(tmp_path / "document", library_id="author.big", extension=True, namespaces=["lesson"])
    assert "teaching json" in str(caught.value) and not (tmp_path / "document" / "pack.zip").exists()


def _tool():
    spec = importlib.util.spec_from_file_location("package_library_pack", ROOT / "tools" / "package_library_pack.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def test_content_with_ability_rules_needs_the_app_that_knows_them():
    """Brief J11 F4: a lesson that lists no ability, or practice of an ability its lesson leaves out, breaks apps
    older than ABILITY_RULES_MIN_APP; the tool raises the minimum by itself and says which rule did."""
    tool = _tool()
    lesson = {"id": "grammar:demo:desu", "abilities": ["understanding"]}
    read = {"id": "pronunciation:demo:read", "abilities": []}

    def values(*items, practice=None, overlay=False):
        found = {"lesson": {"_ITEMS": {item["id"]: item for item in items}}}
        if practice is not None:
            found["varied_practice"] = {"schema_version": 1, "items": practice}
        if overlay:
            found["answer_overlay"] = {"schema_version": 1, "items": {
                "grammar:demo:desu": {"grammar_meaning": {"a" * 64: {"strict": True}}}}}
        return found
    assert tool.minimum_app_version(values(lesson), None) == (None, None)
    assert tool.minimum_app_version(values(lesson, read), None) == (tool.ABILITY_RULES_MIN_APP, "abilities")
    own = {"grammar:demo:desu": {"item_fingerprint": "f" * 64, "templates": {"grammar_meaning": []}}}
    other = {"grammar:demo:desu": {"item_fingerprint": "f" * 64, "templates": {"grammar_cloze": []}}}
    assert tool.minimum_app_version(values(lesson, practice=own), None) == (None, None)
    assert tool.minimum_app_version(values(lesson, practice=other), None) == (tool.ABILITY_RULES_MIN_APP, "abilities")
    # Practice of a lesson the pack does not carry, or of a lesson without an ability list, is not judged here.
    assert tool.minimum_app_version(values(practice=other), None) == (None, None)
    assert tool.minimum_app_version(values({"id": "grammar:demo:desu"}, practice=other), None) == (None, None)
    # A higher given version is kept; the highest rule wins over a lower one and over a lower given version.
    assert tool.minimum_app_version(values(lesson, read), "0.40.0") == ("0.40.0", None)
    assert tool.minimum_app_version(values(lesson, read), "0.36.18") == ("0.36.18", None)
    assert tool.minimum_app_version(values(lesson, read), "0.36.0") == (tool.ABILITY_RULES_MIN_APP, "abilities")
    assert tool.minimum_app_version(values(lesson, overlay=True), None) == (tool.ANSWER_OVERLAY_MIN_APP,
                                                                           "answer_overlay")
    assert tool.minimum_app_version(values(lesson, read, overlay=True), None) == (tool.ABILITY_RULES_MIN_APP,
                                                                                 "abilities")
    assert rules.app_version_key(tool.ABILITY_RULES_MIN_APP) > rules.app_version_key(tool.ANSWER_OVERLAY_MIN_APP)


def test_a_pack_with_a_lesson_that_is_only_read_gets_the_minimum_and_names_the_rule(tmp_path):
    tool = _tool()
    namespaces = copy.deepcopy(CORE_NAMESPACES)
    namespaces["lesson"]["_ITEMS"]["pronunciation:demo:read"] = {
        "id": "pronunciation:demo:read", "revision": 1, "family": "pronunciation", "domain": "pronunciation",
        "title": "读", "reading": "", "meaning": "", "instruction": "只读", "abilities": [], "question_templates": [],
        "source_urls": []}
    resources = build_core_resources(tmp_path / "resources", namespaces)
    result = tool.package(resources, tmp_path / "pack.zip", library_id="author.read", extension=True,
                          namespaces=["lesson"], typed_check=lambda db, ids: [])
    manifest = rules.validate_library_pack(tmp_path / "pack.zip").manifest
    assert manifest["requires"]["app_min_version"] == tool.ABILITY_RULES_MIN_APP == result["app_min_version"]
    assert result["app_min_version_rule"] == "abilities"
    assert rules.app_too_old(manifest, "0.36.17") == tool.ABILITY_RULES_MIN_APP
    plain = tool.package(build_core_resources(tmp_path / "plain", copy.deepcopy(CORE_NAMESPACES)),
                         tmp_path / "plain.zip", library_id="author.plain", extension=True, namespaces=["lesson"],
                         typed_check=lambda db, ids: [])
    assert plain["app_min_version"] is None and plain["app_min_version_rule"] is None
