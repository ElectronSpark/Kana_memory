"""Design 08 section 6.5: an installed extension library is read on demand.

Synthetic core resources and packs only (support/content_library_support.py);
no private course content is read.
"""
import copy
import json
import os
import shutil

import pytest

from kanamemory import content_library_index as library_index
from kanamemory import content_library_rules as rules
from kanamemory import content_pack
from kanamemory.content_library_service import ContentLibraryService
from kanamemory.domain import StudyStore
from kanamemory.material_review import item_fingerprint
from support.content_library_support import CORE_ITEMS, CORE_NAMESPACES, build_core_resources, pack_bytes
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)

LIBRARY = "author.lazy"
WORDS = ["word:lazy:%02d" % index for index in range(6)]


def _item(cid, index):
    return {"id": cid, "revision": 1, "family": "word", "domain": "vocabulary", "title": "語%d" % index,
            "reading": "ご", "meaning": "词%d" % index, "instruction": "说明", "source_urls": [],
            "depends_on": [WORDS[index - 1]] if index else [],
            "question_templates": {"vocab_meaning": [{"prompt": "語%d" % index, "task": "意思是？", "answer": "词%d" % index,
                                                      "choices": ["词%d" % index, "别的", "其他"]}] * (index + 1)}}


def _namespaces():
    items = {cid: _item(cid, index) for index, cid in enumerate(WORDS)}
    return {"lesson": {"CONTENT_REVISION": 99, "_ITEMS": items, "SOURCES": [{"id": "lazy"}]},
            "question_dependencies": {"schema_version": 1, "items": {
                cid: {"vocab_meaning": {"%064x" % index: {"words": [], "grammar": [], "kana": [], "oov": []}}}
                for index, cid in enumerate(WORDS)}}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    resources = build_core_resources(tmp_path / "resources", copy.deepcopy(CORE_NAMESPACES))
    root = tmp_path / "libraries"
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(root))
    content_pack.clear_cache()
    store = StudyStore(tmp_path / "learner", time_zone="UTC")
    service = ContentLibraryService(store, catalogue_ids=lambda: set(CORE_ITEMS), catalogue_fingerprints=lambda: {},
                                    learned_ids=lambda: set(), word_exists=lambda word_id: False)
    namespaces = _namespaces()
    fingerprints = {cid: item_fingerprint(item) for cid, item in namespaces["lesson"]["_ITEMS"].items()}
    inspection = service.inspect_bytes(pack_bytes(LIBRARY, namespaces, items=list(WORDS), fingerprints=fingerprints),
                                       "lazy.zip")
    assert inspection.precheck.blocking_reasons() == ()
    entry = service.install(inspection)
    content_pack.clear_cache()  # "restart"
    yield {"store": store, "service": service, "root": root, "directory": root / entry["path"], "entry": entry}
    store.close()
    content_pack.clear_cache()


def _layer(world):
    return content_pack.LibraryLayer(world["entry"], world["directory"])


def _index_files(world):
    folder = world["directory"] / content_pack.INDEX_DIRECTORY
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


def test_install_writes_the_indexes_and_a_start_reads_no_document(world, monkeypatch):
    assert _index_files(world) == ["lesson.json", "question_dependencies.json"]
    documents = []
    original = content_pack.LibraryLayer._verified_bytes
    monkeypatch.setattr(content_pack.LibraryLayer, "_verified_bytes",
                        lambda self, name: documents.append(name) or original(self, name))
    before = library_index.slice_reads()
    catalogue = content_pack.effective_catalogue()
    assert catalogue.extension_ids == (LIBRARY,) and not catalogue.fallback
    assert content_pack.library_status() == {LIBRARY: {"state": "ready", "reason": None}}
    lessons = content_pack.load_namespace("lesson")["_ITEMS"]
    assert set(lessons) == set(CORE_ITEMS) | set(WORDS)
    assert [lessons[cid]["title"] for cid in WORDS] == ["語%d" % index for index in range(6)]
    assert catalogue.library_of(WORDS[3]) == LIBRARY
    dependencies = content_pack.load_namespace("question_dependencies")
    assert set(dependencies["items"]) == set(WORDS) and dependencies["schema_version"] == 1
    # The catalogue, its prerequisites and the tables' ids came from the indexes alone.
    assert documents == [] and library_index.slice_reads() == before
    templates = lessons[WORDS[2]]["question_templates"]
    assert isinstance(templates, library_index.LazyMapping) and len(templates["vocab_meaning"]) == 3
    assert library_index.slice_reads() == before + 1
    assert list(dependencies["items"][WORDS[4]]["vocab_meaning"]) == ["%064x" % 4]
    assert library_index.slice_reads() == before + 2 and documents == []


def test_the_payload_equals_the_document_decoded_whole(world):
    layer = _layer(world)
    assert not layer.damaged
    for name in sorted(layer.teaching):
        whole = layer.read_namespace(name)
        opened = layer.open_namespace(name)
        assert opened == whole and copy.deepcopy(opened) == whole
    lesson = layer.open_namespace("lesson")
    for cid, item in _namespaces()["lesson"]["_ITEMS"].items():
        assert item_fingerprint(lesson["_ITEMS"][cid]) == item_fingerprint(item)  # fingerprints read through the view
        assert copy.deepcopy(lesson["_ITEMS"][cid]) == item
    # The merged namespace hands out the same lessons, still deferred, and its isolated copies stay independent.
    first = content_pack.load_namespace("lesson")["_ITEMS"]
    second = content_pack.load_namespace("lesson")["_ITEMS"]
    assert first[WORDS[1]] is not second[WORDS[1]] and first[WORDS[1]] == second[WORDS[1]]
    first[WORDS[1]]["title"] = "changed"
    assert second[WORDS[1]]["title"] == "語1"


@pytest.mark.parametrize("damage", ["missing", "garbage", "other_document", "stale_reference"])
def test_an_index_that_cannot_be_used_is_rebuilt_from_the_document(world, damage):
    layer = _layer(world)
    path = world["directory"] / content_pack.INDEX_DIRECTORY / "lesson.json"
    good = path.read_bytes()
    if damage == "missing":
        path.unlink()
    elif damage == "garbage":
        path.write_bytes(good[:len(good) // 2])
    elif damage == "other_document":
        path.write_bytes((world["directory"] / content_pack.INDEX_DIRECTORY / "question_dependencies.json").read_bytes())
    else:
        index = json.loads(good)
        index["document"]["sha256"] = "0" * 64
        index["sha256"] = library_index._self_digest(index)
        path.write_bytes(json.dumps(index).encode("utf-8"))
    assert copy.deepcopy(layer.open_namespace("lesson")) == layer.read_namespace("lesson")
    assert path.read_bytes() == good  # written again for the next start


def test_a_folder_that_cannot_be_written_still_reads_on_demand(world, monkeypatch):
    shutil.rmtree(world["directory"] / content_pack.INDEX_DIRECTORY)
    monkeypatch.setattr(content_pack.os, "replace", lambda *args: (_ for _ in ()).throw(PermissionError("read-only")))
    layer = _layer(world)
    opened = layer.open_namespace("lesson")
    assert isinstance(opened["_ITEMS"][WORDS[0]]["question_templates"], library_index.LazyMapping)
    assert copy.deepcopy(opened) == layer.read_namespace("lesson")
    assert _index_files(world) == []  # nothing half-written is left behind
    catalogue = content_pack.effective_catalogue()
    assert catalogue.extension_ids == (LIBRARY,) and not catalogue.fallback


def _change_keeping_size(world, *, keep_time):
    document = world["directory"] / "teaching" / "lesson.json"
    raw, stat = document.read_bytes(), document.stat()
    changed = raw.replace("别的".encode("utf-8"), "别地".encode("utf-8"))
    assert changed != raw and len(changed) == len(raw)
    document.write_bytes(changed)
    os.utime(document, ns=(stat.st_atime_ns, stat.st_mtime_ns if keep_time else stat.st_mtime_ns + 5_000_000_000))


def test_a_document_written_again_is_read_whole_and_refused_at_the_start_as_before(world):
    _change_keeping_size(world, keep_time=False)
    layer = _layer(world)
    assert not layer.damaged  # the sizes still match
    with pytest.raises(content_pack.ContentPackError):
        layer.open_namespace("lesson")  # its index names another modification time: whole read, checksum mismatch
    catalogue = content_pack.effective_catalogue()
    assert catalogue.fallback and LIBRARY in catalogue.failed
    assert content_pack.library_status()[LIBRARY]["state"] == "damaged"
    assert world["service"]._passes_recheck(world["entry"]) is False


def test_damage_the_start_cannot_see_fails_where_it_is_read_and_is_refused_at_the_next_start(world):
    from kanamemory import diagnostic_events
    _change_keeping_size(world, keep_time=True)  # as if the bytes went bad on the medium
    catalogue = content_pack.effective_catalogue()
    assert not catalogue.fallback  # like a shard of the core pack, the part is checked when it is read
    lessons = content_pack.load_namespace("lesson")["_ITEMS"]
    assert lessons[WORDS[0]]["title"] == "語0"
    recorded = []
    original = diagnostic_events.record_error
    diagnostic_events.record_error = lambda *args: recorded.append(args) or original(*args)
    try:
        for _ in range(2):
            with pytest.raises(library_index.LibraryIndexError):
                lessons[WORDS[0]]["question_templates"]["vocab_meaning"]
    finally:
        diagnostic_events.record_error = original
    assert [args[:2] for args in recorded] == [("content_library", "read")]  # once, not per read
    assert _index_files(world) == ["question_dependencies.json"]  # the index of the bad document is gone
    content_pack.clear_cache()  # "restart": no index, so the document is read whole and refused
    catalogue = content_pack.effective_catalogue()
    assert catalogue.fallback and LIBRARY in catalogue.failed
    assert content_pack.library_status()[LIBRARY]["state"] == "damaged"


def test_a_document_the_index_cannot_describe_is_decoded_whole(world, monkeypatch):
    def refuse(raw, name, **options):
        raise library_index.LibraryIndexError("not indexed")
    monkeypatch.setattr(library_index, "build_index", refuse)
    shutil.rmtree(world["directory"] / content_pack.INDEX_DIRECTORY)
    layer = _layer(world)
    opened = layer.open_namespace("lesson")
    assert opened == layer.read_namespace("lesson") and type(opened["_ITEMS"][WORDS[0]]["question_templates"]) is dict
    assert _index_files(world) == []


def _handmade_layer(tmp_path, document):
    """A library folder whose lesson document is exactly ``document`` (bytes the pack builder would refuse)."""
    import hashlib
    import io
    import zipfile
    data = pack_bytes("author.odd", {"lesson": {"_ITEMS": {WORDS[0]: _item(WORDS[0], 0)}}})
    folder = tmp_path / "odd"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extractall(folder)
    manifest = json.loads((folder / rules.MANIFEST_FILE).read_text("utf-8"))
    (folder / "teaching" / "lesson.json").write_bytes(document)
    manifest["files"]["teaching/lesson.json"] = {"bytes": len(document), "sha256": hashlib.sha256(document).hexdigest()}
    manifest["content_sha256"] = rules.content_digest(manifest["files"])
    (folder / rules.MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")
    layer = content_pack.LibraryLayer({"library_id": "author.odd", "content_sha256": manifest["content_sha256"]}, folder)
    assert not layer.damaged, layer.reason
    return layer, folder


def test_a_part_the_decoder_refuses_is_refused_when_the_index_is_built_not_later(tmp_path):
    item = dict(_item(WORDS[0], 0), question_templates={"vocab_meaning": [{"prompt": "語", "weight": 1.5}]})
    good = json.dumps({"format": "kanamemory-teaching-namespace", "version": 1, "name": "lesson",
                       "payload": {"_ITEMS": {WORDS[0]: item}}}, ensure_ascii=False).encode("utf-8")
    layer, folder = _handmade_layer(tmp_path, good.replace(b"1.5", b"NaN"))
    with pytest.raises(content_pack.ContentPackError):
        layer.read_namespace("lesson")
    with pytest.raises(content_pack.ContentPackError):
        layer.open_namespace("lesson")  # a library installed by an older build: same answer as every start gave
    assert not (folder / content_pack.INDEX_DIRECTORY / "lesson.json").exists()
    # Only an index built without the check (the install skips it after its own precheck) defers the refusal.
    assert layer.build_index("lesson", validate=False)[0] is not None
    opened = layer.open_namespace("lesson")
    with pytest.raises(library_index.LibraryIndexError):
        opened["_ITEMS"][WORDS[0]]["question_templates"]["vocab_meaning"]


def test_unpaired_surrogate_escapes_do_not_cost_the_library(tmp_path):
    document = (b'{"format": "kanamemory-teaching-namespace", "version": 1, "name": "lesson", "payload": {"_ITEMS": {"'
                + WORDS[0].encode() + b'": {"id": "' + WORDS[0].encode() + b'", "family": "word", "title": "\\ud800",'
                b' "question_templates": {"vocab_meaning": [{"prompt": "\\udfff"}]}}}}}')
    layer, folder = _handmade_layer(tmp_path, document)
    whole = layer.read_namespace("lesson")
    assert whole["_ITEMS"][WORDS[0]]["title"] == "\ud800"
    for _ in range(2):  # built, then read back from the folder
        opened = layer.open_namespace("lesson")
        assert isinstance(opened["_ITEMS"][WORDS[0]]["question_templates"], library_index.LazyMapping)
        assert copy.deepcopy(opened) == whole
    assert (folder / content_pack.INDEX_DIRECTORY / "lesson.json").is_file()


def test_a_superseded_folder_this_process_still_reads_waits_for_a_later_start(world):
    service = world["service"]
    assert content_pack.effective_catalogue().extension_ids == (LIBRARY,)  # this process reads the first version
    namespaces = _namespaces()
    namespaces["lesson"]["_ITEMS"][WORDS[0]]["meaning"] = "新的词义"
    fingerprints = {cid: item_fingerprint(item) for cid, item in namespaces["lesson"]["_ITEMS"].items()}
    inspection = service.inspect_bytes(pack_bytes(LIBRARY, namespaces, version="20260103", items=list(WORDS),
                                                  fingerprints=fingerprints), "lazy2.zip")
    entry = service.install(inspection)
    assert entry["path"] != world["entry"]["path"]
    service._remove_superseded(entry)
    assert world["directory"].is_dir()  # still read on demand here
    lessons = content_pack.load_namespace("lesson")["_ITEMS"]
    assert len(lessons[WORDS[5]]["question_templates"]["vocab_meaning"]) == 6
    content_pack.clear_cache()  # "restart": the new version is the one being read
    service._remove_superseded(entry)
    assert not world["directory"].exists() and (world["root"] / entry["path"]).is_dir()


def test_removing_the_library_removes_its_indexes(world):
    service = world["service"]
    assert _index_files(world)
    service.remove(LIBRARY)
    content_pack.clear_cache()
    service.startup_sync()
    assert not world["directory"].exists()
    assert rules.registry_entry(service.registry(), LIBRARY) is None
