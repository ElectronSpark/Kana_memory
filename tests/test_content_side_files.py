"""Side files (design 08 section 4.1): per-text data read through the teaching database's resolution.

The active installed core library's copy when its pack carries the file, else the bundled copy; the packaging tool
carries the side files of the resources; precheck and registry stay truthful; a damaged side file refuses the
pack. Made-up content only.
"""
import hashlib
import json
import sqlite3
import zipfile

import pytest

from kanamemory import card_quote_readings as cards
from kanamemory import content_library_rules as rules
from kanamemory import content_pack, material_review, natural_translations, order_translations
from kanamemory import sentence_annotations
from kanamemory.content_library_service import ContentLibraryService
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.material_review import item_fingerprint
from support.content_library_support import CORE_ITEMS, build_core_resources
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)

REVIEW = {"format": "kanamemory-material-review", "version": 1, "revision": "test-1", "items": {}, "questions": []}


def _card_file(reading):
    return json.dumps({"format": cards.FORMAT, "version": cards.VERSION,
                       "items": {"word:demo:mizu": {"focus": {"水": [[0, 1, reading, "course"]]}}}},
                      ensure_ascii=False)


def _sqlite(path, table):
    with sqlite3.connect(path) as db:
        db.execute(f"CREATE TABLE {table}(id TEXT PRIMARY KEY)")


def add_side_files(resources, *, reading="みず", review_revision="test-1"):
    """Every side file in a resources folder (small made-up stand-ins of the right file types)."""
    for name in ("sentence_annotations.sqlite3", "natural_translations.sqlite3", "order_translations.sqlite3",
                 "lexical_sentence_review.sqlite3"):
        _sqlite(resources / name, "sample")
    (resources / cards.FILE).write_text(_card_file(reading), "utf-8")
    (resources / material_review.REVIEW_FILE).write_text(json.dumps({**REVIEW, "revision": review_revision}), "utf-8")


@pytest.fixture
def world(tmp_path, monkeypatch):
    resources = build_core_resources(tmp_path / "resources")
    root = tmp_path / "libraries"
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(root))
    content_pack.clear_cache()
    store = StudyStore(tmp_path / "learner", time_zone="UTC")
    LearningStore(store)
    service = ContentLibraryService(store, catalogue_ids=lambda: set(CORE_ITEMS),
                                    catalogue_fingerprints=lambda: {c: item_fingerprint(i) for c, i in CORE_ITEMS.items()},
                                    learned_ids=lambda: set(), word_exists=lambda word_id: False)
    yield {"store": store, "service": service, "root": root, "resources": resources, "tmp": tmp_path}
    store.close()
    content_pack.clear_cache()
    cards._items.cache_clear()
    material_review._review_data.cache_clear()


def _install(world, monkeypatch, resources, name):
    from package_library_pack import package
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(world["resources"] / "teaching_content.sqlite3"))
    content_pack.clear_cache()
    output = world["tmp"] / name
    package(resources, output)
    entry = world["service"].install(world["service"].inspect_bytes(output.read_bytes(), name))
    monkeypatch.delenv("KANAMEMORY_TEACHING_PACK")
    content_pack.clear_cache()  # "restart" onto the installed core pack (the app restarts after an install)
    world["service"].startup_sync()
    return entry, output


def test_bundled_only_every_side_file_is_the_bundled_copy(world, monkeypatch):
    bundled = content_pack._resources()
    for name in rules.SIDE_FILES:
        assert content_pack.side_file_path(name) == bundled / name  # KANAMEMORY_TEACHING_PACK: the bundled copy
    monkeypatch.delenv("KANAMEMORY_TEACHING_PACK")
    content_pack.clear_cache()
    for name in rules.SIDE_FILES:
        assert content_pack.side_file_path(name) == bundled / name  # no installed core
    assert sentence_annotations._database_path() == bundled / "sentence_annotations.sqlite3"
    assert natural_translations._database_path() == bundled / "natural_translations.sqlite3"
    assert order_translations._database_path() == bundled / "order_translations.sqlite3"
    with pytest.raises(KeyError):
        content_pack.side_file_path("teaching_content.sqlite3")


def test_an_installed_core_without_side_files_keeps_the_bundled_copies(world, monkeypatch):
    entry, _ = _install(world, monkeypatch, world["resources"], "core.zip")
    folder = world["root"] / entry["path"]
    assert content_pack.lexicon_path() == folder / "lexicon/content.sqlite3"
    for name in rules.SIDE_FILES:
        assert content_pack.side_file_path(name) == content_pack._resources() / name
    # The lexical review of the active lexicon follows the side-file resolution; another lexicon keeps its own.
    assert content_pack.lexicon_side_file(content_pack.lexicon_path(), "lexical_sentence_review.sqlite3") == \
        content_pack._resources() / "lexical_sentence_review.sqlite3"
    other = world["tmp"] / "other" / "content.sqlite3"
    assert content_pack.lexicon_side_file(other, "lexical_sentence_review.sqlite3") == \
        other.with_name("lexical_sentence_review.sqlite3")


def test_an_installed_core_with_side_files_wins_and_its_readers_use_them(world, monkeypatch):
    add_side_files(world["resources"])
    entry, _ = _install(world, monkeypatch, world["resources"], "core.zip")
    folder = world["root"] / entry["path"]
    for name, layer in rules.SIDE_FILES.items():
        assert content_pack.side_file_path(name) == folder / layer / name
    assert sentence_annotations._database_path() == folder / "annotations/sentence_annotations.sqlite3"
    assert natural_translations._database_path() == folder / "annotations/natural_translations.sqlite3"
    assert order_translations._database_path() == folder / "annotations/order_translations.sqlite3"
    assert content_pack.lexicon_side_file(content_pack.lexicon_path(), "lexical_sentence_review.sqlite3") == \
        folder / "annotations/lexical_sentence_review.sqlite3"
    assert material_review.review_data()["revision"] == "test-1"
    assert cards.quotes_for("word:demo:mizu", "focus") == {"水": [[0, 1, "みず", "course"]]}


def test_a_newer_core_library_replaces_the_side_files_of_the_older_one(world, monkeypatch):
    add_side_files(world["resources"])
    first, _ = _install(world, monkeypatch, world["resources"], "core1.zip")
    assert cards.quotes_for("word:demo:mizu", "focus")["水"][0][2] == "みず"
    newer = build_core_resources(world["tmp"] / "newer", content_version="20260201")
    add_side_files(newer, reading="すい", review_revision="test-2")
    second, _ = _install(world, monkeypatch, newer, "core2.zip")
    assert second["path"] != first["path"] and second["library_version"] == "20260201"
    folder = world["root"] / second["path"]
    assert content_pack.side_file_path(cards.FILE) == folder / "annotations" / cards.FILE
    assert cards.quotes_for("word:demo:mizu", "focus")["水"][0][2] == "すい"  # cached by path: the new copy
    assert material_review.review_data()["revision"] == "test-2"


def test_a_damaged_side_file_refuses_the_pack_and_a_changed_installed_one_falls_back(world, monkeypatch):
    from package_library_pack import package
    add_side_files(world["resources"])
    output = world["tmp"] / "core.zip"
    package(world["resources"], output)
    with zipfile.ZipFile(output) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    member = "annotations/" + cards.FILE
    members[member] = members[member].replace("みず".encode(), "すい".encode())
    damaged = world["tmp"] / "damaged.zip"
    with zipfile.ZipFile(damaged, "w") as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    with pytest.raises(rules.ContentLibraryError, match="checksum mismatch"):
        world["service"].inspect_bytes(damaged.read_bytes(), "damaged.zip")
    entry, _ = _install(world, monkeypatch, world["resources"], "core-installed.zip")
    installed = world["root"] / entry["path"] / member
    installed.write_text(_card_file("みずみず"), "utf-8")  # another size: the layer is damaged at the next start
    content_pack.clear_cache()
    assert content_pack.library_status()[rules.CORE_LIBRARY_ID]["state"] == "damaged"
    assert content_pack.side_file_path(cards.FILE) == content_pack._resources() / cards.FILE


def test_the_pack_lists_the_side_files_with_size_and_hash_in_their_layers(world, tmp_path):
    from package_library_pack import package
    add_side_files(world["resources"])
    result = package(world["resources"], tmp_path / "core.zip")
    manifest = rules.validate_library_pack(tmp_path / "core.zip").manifest
    for name, layer in rules.SIDE_FILES.items():
        member = rules.side_file_member(name)
        raw = (world["resources"] / name).read_bytes()
        assert member in manifest["layers"][layer]
        assert manifest["files"][member] == {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    # Side files raise no minimum app version: an app that does not read one installs the pack and ignores it.
    assert result["app_min_version"] is None and "app_min_version" not in manifest["requires"]


def test_a_pack_without_side_files_lists_none(world, tmp_path):
    from package_library_pack import package
    package(world["resources"], tmp_path / "core.zip")
    manifest = rules.validate_library_pack(tmp_path / "core.zip").manifest
    assert manifest["layers"]["annotations"] == [] and manifest["layers"]["review"] == []


def test_a_side_file_over_the_member_limit_stops_the_tool_naming_it(world, tmp_path, monkeypatch):
    from package_library_pack import package
    add_side_files(world["resources"])
    (world["resources"] / cards.FILE).write_text(_card_file("み" * 30), "utf-8")
    size = (world["resources"] / cards.FILE).stat().st_size
    monkeypatch.setattr(rules, "MAX_FILE_BYTES", size - 1)
    monkeypatch.setattr(rules, "MAX_UNPACKED_BYTES", 10 ** 12)
    with pytest.raises(ValueError, match="annotations/card_quote_readings.json"):
        package(world["resources"], tmp_path / "core.zip")
    assert not (tmp_path / "core.zip").exists()


def test_precheck_and_registry_are_truthful_with_and_without_side_files(world, tmp_path):
    from package_library_pack import package
    service = world["service"]
    package(world["resources"], tmp_path / "plain.zip")
    plain = service.inspect_bytes((tmp_path / "plain.zip").read_bytes(), "plain.zip")
    add_side_files(world["resources"])
    package(world["resources"], tmp_path / "side.zip")
    side = service.inspect_bytes((tmp_path / "side.zip").read_bytes(), "side.zip")
    for field in ("new_item_count", "same_fingerprint_ids", "conflicting_ids", "missing_required_references",
                  "replaces_core"):
        assert getattr(plain.precheck, field) == getattr(side.precheck, field), field
    assert side.precheck.blocking_reasons() == plain.precheck.blocking_reasons() == ()
    assert side.report.total_bytes == plain.report.total_bytes + sum(
        (world["resources"] / name).stat().st_size for name in rules.SIDE_FILES)
    entry = service.install(side)
    # The registry entry names the pack's whole file table, side files included.
    assert entry["content_sha256"] == side.manifest["content_sha256"] != plain.manifest["content_sha256"]


def test_an_app_that_does_not_know_a_side_file_still_accepts_the_pack(world, tmp_path):
    """The member checks every app since 0.35.0 applies (validate_library_pack): any .sqlite3, .json or .txt member
    of the annotations and review layers is accepted, whatever its name; only a known file is read."""
    from package_library_pack import package
    (world["resources"] / cards.FILE).write_text(_card_file("みず"), "utf-8")
    package(world["resources"], tmp_path / "core.zip")
    with zipfile.ZipFile(tmp_path / "core.zip") as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members.pop(rules.MANIFEST_FILE))
    raw = b'{"unknown": true}'
    members["annotations/future_side_file.json"] = raw
    manifest["files"]["annotations/future_side_file.json"] = {"bytes": len(raw),
                                                              "sha256": hashlib.sha256(raw).hexdigest()}
    manifest["layers"]["annotations"].append("annotations/future_side_file.json")
    manifest["content_sha256"] = rules.content_digest(manifest["files"])
    with zipfile.ZipFile(tmp_path / "future.zip", "w") as archive:
        archive.writestr(rules.MANIFEST_FILE, json.dumps(manifest, ensure_ascii=False))
        for name, data in members.items():
            archive.writestr(name, data)
    report = rules.validate_library_pack(tmp_path / "future.zip")
    assert "annotations/future_side_file.json" in report.files
