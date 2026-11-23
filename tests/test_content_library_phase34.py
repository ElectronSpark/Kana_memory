"""08 phase 3/4 end to end: a core library plus installed extension libraries after a "restart".

All content is synthetic or the original demo content; extension libraries are
copies with rewritten ids (tools/package_library_pack.py --rewrite-ids).
"""
import copy
import importlib.util
from pathlib import Path

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_library_merge as merge
from kanamemory import content_pack, course_prerequisites, diagnostic_events, learning_dependencies
from kanamemory.content_library_service import ContentLibraryService
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.material_review import item_fingerprint
from kanamemory import personal_content_versions  # noqa: F401  (import the bundled catalogue before fixtures swap packs)
from support.content_library_support import CORE_ITEMS, CORE_NAMESPACES, build_core_resources, pack_bytes
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)

ROOT = Path(__file__).resolve().parents[1]
CORE = rules.CORE_LIBRARY_ID
DEMO_ID = "grammar:demo:polite_noun"


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _world(tmp_path, monkeypatch, resources):
    root = tmp_path / "libraries"
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(root))
    content_pack.clear_cache()
    store = StudyStore(tmp_path / "learner", time_zone="UTC")
    learning = LearningStore(store)
    ids = set()
    for _, value in content_pack.read_pack_namespaces(resources / "teaching_content.sqlite3"):
        ids.update(cid for cid, _ in __import__("kanamemory.content_library_merge", fromlist=["x"]).catalogue_items(value))
    learned = set()
    service = ContentLibraryService(store, catalogue_ids=lambda: set(ids), catalogue_fingerprints=lambda: {},
                                    learned_ids=lambda: learned, word_exists=lambda word_id: False)
    return {"store": store, "learning": learning, "service": service, "root": root, "learned": learned,
            "resources": resources}


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    namespaces = copy.deepcopy(CORE_NAMESPACES)
    namespaces["course_prerequisites"] = {"VERSION": "core-1", "FORM_BASES": {"te": ("grammar:demo:desu",)},
                                          "FORM_CONSUMERS": {"te": ()}, "SEMANTIC_REQUIREMENTS": {}}
    world = _world(tmp_path, monkeypatch, build_core_resources(tmp_path / "resources", namespaces))
    yield world
    world["store"].close()
    content_pack.clear_cache()


@pytest.fixture
def demo(tmp_path, monkeypatch):
    resources = tmp_path / "demo-resources"
    _tool("build_demo_content").build_demo(resources)
    world = _world(tmp_path, monkeypatch, resources)
    yield world
    world["store"].close()
    content_pack.clear_cache()


def _install(world, data, *, accept=False):
    service = world["service"]
    inspection = service.inspect_bytes(data, "lib.zip")
    service.install(inspection, accept_missing_references=accept)
    content_pack.clear_cache()  # "restart"
    return inspection.precheck


def test_demo_copy_with_rewritten_ids_merges_beside_the_core_library(demo, tmp_path):
    package = _tool("package_library_pack").package
    result = package(demo["resources"], tmp_path / "ext.zip", library_id="demo.copy", extension=True,
                     rewrite_ids=True, depend_on_source=True, title="演示副本")
    derived = "grammar:demo.copy:demo:polite_noun"
    assert result["provides"] == [derived] and result["references"] == 1
    manifest = rules.validate_library_pack(tmp_path / "ext.zip").manifest
    assert manifest["references"] == [{"id": DEMO_ID, "kind": "item", "requirement": "required", "note": ""}]
    assert manifest["revisions"] == {derived: "original-demo-1"} and manifest["provides"]["items"] == [derived]
    assert manifest["retires"] == [] and manifest["aliases"] == {} and manifest["overrides"] == []
    precheck = _install(demo, (tmp_path / "ext.zip").read_bytes())
    assert precheck.new_item_count == 1 and precheck.blocking_reasons() == ()
    grammar = content_pack.load_namespace("learning_content")["_GRAMMAR"]
    assert [row["id"] for row in grammar] == [DEMO_ID, derived]
    assert grammar[1]["depends_on"] == [DEMO_ID] and grammar[1]["title"] == grammar[0]["title"]
    catalogue = content_pack.effective_catalogue()
    assert catalogue.library_of(derived) == "demo.copy" and catalogue.library_of(DEMO_ID) == CORE
    assert catalogue.missing_required_references(derived) == () and not catalogue.fallback
    assert content_pack.library_status() == {"demo.copy": {"state": "ready", "reason": None}}
    # Disabling the copy makes its id unavailable (not missing) for dependency plans.
    demo["service"].set_enabled("demo.copy", False)
    content_pack.clear_cache()
    view = content_pack.effective_catalogue()
    assert view.library_of(derived) is None and view.is_unavailable(derived)
    plan = learning_dependencies.build_dependency_plan(["grammar:x"], {"grammar:x": {"id": "grammar:x", "depends_on": [derived]}},
                                                       allow_unresolved=True, catalogue_view=view)
    assert plan["unresolved"] == [{"id": derived, "reason": "unavailable", "path": ["grammar:x", derived]}]
    with pytest.raises(ValueError, match="已停用"):
        learning_dependencies.build_dependency_plan(["grammar:x"], {"grammar:x": {"id": "grammar:x", "depends_on": [derived]}},
                                                    catalogue_view=view)


def test_missing_required_reference_blocks_only_new_enrolment_and_keeps_catalogue_valid(synthetic):
    items = {"grammar:n3:a": {**CORE_ITEMS["grammar:demo:desu"], "id": "grammar:n3:a", "depends_on": ["grammar:nowhere"]},
             "grammar:n3:b": {**CORE_ITEMS["grammar:demo:desu"], "id": "grammar:n3:b", "depends_on": ["grammar:n3:a"]},
             "grammar:n3:c": {**CORE_ITEMS["grammar:demo:desu"], "id": "grammar:n3:c",
                              "depends_on": ["grammar:demo:desu", "grammar:maybe"]}}
    data = pack_bytes("author.n3", {"lesson": {"_ITEMS": items}}, manifest_overrides={"references": [
        {"id": "grammar:nowhere", "kind": "item", "requirement": "required"},
        {"id": "grammar:maybe", "kind": "item", "requirement": "optional"},
        {"id": "grammar:demo:desu", "kind": "item", "requirement": "required"}]})
    precheck = _install(synthetic, data, accept=True)
    assert precheck.missing_required_references == ("grammar:nowhere",)
    assert precheck.missing_optional_references == ("grammar:maybe",)
    lesson = content_pack.load_namespace("lesson")["_ITEMS"]
    assert lesson["grammar:n3:a"]["depends_on"] == [] and lesson["grammar:n3:c"]["depends_on"] == ["grammar:demo:desu"]
    catalogue = content_pack.effective_catalogue()
    assert catalogue.missing_required_references("grammar:n3:a") == ("grammar:nowhere",)
    assert catalogue.missing_required_references("grammar:n3:b") == ("grammar:nowhere",)
    assert catalogue.missing_required_references("grammar:n3:c") == ()
    assert catalogue.dropped_optional == {"grammar:n3:c": ("grammar:maybe",)}
    # The resolved catalogue validates; a plan reports the blocked item instead of enrolling it.
    full = {cid: dict(item) for cid, item in lesson.items()}
    learning_dependencies.validate_catalog(full)
    plan = learning_dependencies.build_dependency_plan(["grammar:n3:b"], full, allow_unresolved=True,
                                                       catalogue_view=catalogue)
    assert plan["to_enroll"] == [] and plan["unresolved"][0]["reason"] == "blocked_dependency"
    status = learning_dependencies._status(None, "grammar:n3:a", set(), full, catalogue)
    assert status["status"] == "unavailable"
    assert learning_dependencies._status(None, "grammar:n3:a", {"grammar:n3:a"}, full, catalogue)["status"] == "ready"


def test_same_id_same_fingerprint_dedupes_and_different_fingerprint_follows_priority(synthetic):
    same = copy.deepcopy(CORE_ITEMS["grammar:demo:desu"])
    changed = dict(CORE_ITEMS["word:demo:mizu"], revision=2, meaning="改写的水")
    data = pack_bytes("author.n3", {"lesson": {"_ITEMS": {"grammar:demo:desu": same, "word:demo:mizu": changed}}},
                      manifest_overrides={"overrides": [{"id": "word:demo:mizu", "reason": "better gloss"}],
                                          "revisions": {"word:demo:mizu": "2", "grammar:demo:desu": "1"}})
    _install(synthetic, data)
    lesson = content_pack.load_namespace("lesson")["_ITEMS"]
    assert lesson["word:demo:mizu"]["meaning"] == "水"
    report = content_pack.library_merge_report()["lesson"]
    assert ["_ITEMS", "grammar:demo:desu", CORE, "author.n3"] in [list(row) for row in report["duplicates"]]
    assert ["_ITEMS", "word:demo:mizu", CORE, "author.n3"] in [list(row) for row in report["conflicts"]]
    assert report["revision_conflicts"] == []
    catalogue = content_pack.effective_catalogue()
    assert catalogue.library_of("word:demo:mizu") == CORE
    assert catalogue.providers_of("grammar:demo:desu") == (CORE, "author.n3")
    # Accepting the declared override (registry overrides_accepted) lets the library win for that id only.
    registry = rules.with_accepted_overrides(synthetic["service"].registry(), "author.n3", ["word:demo:mizu"])
    synthetic["store"].set_setting(rules.REGISTRY_SETTING, registry)
    synthetic["service"].write_mirror(registry)
    content_pack.clear_cache()
    assert content_pack.load_namespace("lesson")["_ITEMS"]["word:demo:mizu"]["meaning"] == "改写的水"
    assert content_pack.load_namespace("lesson")["_ITEMS"]["grammar:demo:desu"] == CORE_ITEMS["grammar:demo:desu"]
    assert content_pack.effective_catalogue().library_of("word:demo:mizu") == "author.n3"
    overridden = content_pack.library_merge_report()["lesson"]["overridden"]
    assert ["_ITEMS", "word:demo:mizu", "author.n3", CORE] in [list(row) for row in overridden]


def test_extension_prerequisite_rules_never_raise_for_missing_consumers(synthetic, monkeypatch):
    extension = {"lesson": {"_ITEMS": {"grammar:ext:masu": dict(CORE_ITEMS["grammar:demo:desu"], id="grammar:ext:masu")}},
                 "course_prerequisites": {"VERSION": "ext", "FORM_BASES": {"te": ("grammar:ext:masu",)},
                                          "FORM_CONSUMERS": {"te": ("grammar:missing", "grammar:ext:masu")},
                                          "SEMANTIC_REQUIREMENTS": {"grammar:absent": ("grammar:demo:desu",),
                                                                    "grammar:ext:masu": ("grammar:demo:desu",)}}}
    _install(synthetic, pack_bytes("author.n3", extension))
    rules_payload = content_pack.load_namespace("course_prerequisites")
    assert rules_payload["FORM_BASES"] == {"te": ("grammar:demo:desu",)} and rules_payload["VERSION"] == "core-1"
    rows = content_pack.namespace_contributions("course_prerequisites")
    assert ("SEMANTIC_REQUIREMENTS", "grammar:absent", "author.n3", ("grammar:demo:desu",)) in rows
    for name in ("FORM_BASES", "FORM_CONSUMERS", "SEMANTIC_REQUIREMENTS"):
        monkeypatch.setattr(course_prerequisites, name, rules_payload[name])
    monkeypatch.setattr(course_prerequisites, "VERSION", rules_payload["VERSION"])
    lesson = content_pack.load_namespace("lesson")["_ITEMS"]
    catalogue = {cid: {**item, "depends_on": list(item.get("depends_on", []))} for cid, item in lesson.items()}
    result = course_prerequisites.apply_course_prerequisites(catalogue)
    assert {(row[2], row[3], row[4]) for row in result["skipped"]} == {
        ("te", "grammar:missing", "missing_consumer"), ("grammar:absent", "grammar:demo:desu", "missing_consumer")}
    assert catalogue["grammar:ext:masu"]["depends_on"] == ["grammar:demo:desu"]
    assert catalogue["grammar:demo:desu"]["depends_on"] == [] and course_prerequisites.skipped_contributions()
    assert any(e["operation"] == "content_library" and e["stage"] == "refresh" for e in diagnostic_events.error_events())
    # The core rules stay strict.
    monkeypatch.setattr(course_prerequisites, "SEMANTIC_REQUIREMENTS", {"grammar:core-missing": ("grammar:demo:desu",)})
    with pytest.raises(ValueError, match="consumer is missing"):
        course_prerequisites.apply_course_prerequisites(copy.deepcopy(catalogue), contributions=())


def test_broken_extension_falls_back_to_core_only_and_is_flagged(synthetic):
    _install(synthetic, pack_bytes("author.n3", {"lesson": {"_ITEMS": {"grammar:ext:masu": dict(
        CORE_ITEMS["grammar:demo:desu"], id="grammar:ext:masu")}}}))
    entry = rules.registry_entry(synthetic["service"].registry(), "author.n3")
    target = synthetic["root"] / entry["path"] / "teaching/lesson.json"
    raw = target.read_bytes()
    target.write_bytes(raw.replace("ext:masu".encode(), "ext:masx".encode()))  # same size, wrong checksum
    content_pack.clear_cache()
    assert set(content_pack.load_namespace("lesson")["_ITEMS"]) == set(CORE_ITEMS)
    catalogue = content_pack.effective_catalogue()
    assert catalogue.fallback and "author.n3" in catalogue.failed
    status = content_pack.library_status()
    assert status["author.n3"]["state"] == "damaged" and "checksum" in status["author.n3"]["reason"]
    synthetic["service"].startup_sync()
    assert rules.registry_entry(synthetic["service"].registry(), "author.n3")["state"] == "damaged"


def test_extension_cycle_falls_back_and_names_the_library(synthetic, monkeypatch):
    loop = {"grammar:x": dict(CORE_ITEMS["grammar:demo:desu"], id="grammar:x", depends_on=["grammar:y"]),
            "grammar:y": dict(CORE_ITEMS["grammar:demo:desu"], id="grammar:y", depends_on=["grammar:x"])}
    data = pack_bytes("author.loop", {"lesson": {"_ITEMS": loop}})
    # Review B-01: the install precheck's trial merge refuses the cycle.
    check = synthetic["service"].inspect_bytes(data, "loop.zip")
    assert check.precheck.merge_errors and "成环" in check.precheck.blocking_reasons()[0]
    with pytest.raises(ValueError, match="成环"):
        synthetic["service"].install(check, accept_missing_references=True)
    # A cycle that still reaches startup (an older install) falls back to the core library.
    monkeypatch.setattr(merge, "offline_merge_problems", lambda **kwargs: ())
    _install(synthetic, data)
    assert set(content_pack.load_namespace("lesson")["_ITEMS"]) == set(CORE_ITEMS)
    assert content_pack.library_status()["author.loop"]["state"] == "damaged"


def test_started_items_keep_their_route_when_an_extension_adds_a_prerequisite(synthetic, monkeypatch):
    """R2: an untouched exact snapshot gets the new prerequisite; real work keeps the old route."""
    core_item = dict(CORE_ITEMS["word:demo:mizu"], depends_on=[], source_ids=[], abilities=["understanding"])
    catalogue = {"word:demo:mizu": copy.deepcopy(core_item),
                 "grammar:demo:desu": dict(CORE_ITEMS["grammar:demo:desu"], depends_on=[])}
    monkeypatch.setattr(course_prerequisites, "SEMANTIC_REQUIREMENTS", {"word:demo:mizu": ("grammar:demo:desu",)})
    monkeypatch.setattr(course_prerequisites, "FORM_CONSUMERS", {})
    course_prerequisites.apply_course_prerequisites(
        catalogue, contributions=(("SEMANTIC_REQUIREMENTS", "word:demo:mizu", "author.n3", ("grammar:demo:desu",)),))
    current = catalogue["word:demo:mizu"]
    assert current["depends_on"] == ["grammar:demo:desu"]
    signature = (item_fingerprint(current), (), frozenset({"understanding"}), ("grammar:demo:desu",))
    monkeypatch.setattr(learning_dependencies, "_bundled_dependency_signature", lambda cid: signature)
    saved = copy.deepcopy(core_item)
    learning = synthetic["learning"]
    assert learning_dependencies.effective_dependencies(learning, saved, preserved=True) == []
    assert learning_dependencies.effective_dependencies(learning, saved, preserved=False) == ["grammar:demo:desu"]
    edited = dict(saved, meaning="别的义项")
    assert learning_dependencies.effective_dependencies(learning, edited, preserved=False) == []


def test_frontier_wrapper_uses_plan_order_and_enrolment():
    catalogue = {"word:a": {"id": "word:a", "family": "word"}, "word:b": {"id": "word:b", "family": "word"},
                 "grammar:g": {"id": "grammar:g", "family": "grammar"},
                 "grammar:t": {"id": "grammar:t", "family": "grammar", "depends_on": ["word:a", "word:b", "grammar:g"]}}
    plan = learning_dependencies.build_dependency_plan(["grammar:t"], catalogue)
    result = learning_dependencies.dependency_frontier(plan, limits={"word": 1, "grammar": 1, "kana": 5})
    assert result["first"] == ["word:a", "grammar:g", "grammar:t"] and result["planned"] == ["word:b"]


def test_any_extension_merge_failure_falls_back_and_names_the_library(synthetic, monkeypatch):
    """Review B-01: an unexpected exception in the merge falls back to core only instead of stopping the start."""
    _install(synthetic, pack_bytes("author.n3", {"lesson": {"_ITEMS": {"grammar:ext:masu": dict(
        CORE_ITEMS["grammar:demo:desu"], id="grammar:ext:masu")}}}))
    original = merge.build_effective_catalogue

    def failing(**kwargs):
        if any(ext.library_id == "author.n3" for ext in kwargs["extensions"]):
            raise RecursionError("maximum recursion depth exceeded")
        return original(**kwargs)
    monkeypatch.setattr(merge, "build_effective_catalogue", failing)
    assert set(content_pack.load_namespace("lesson")["_ITEMS"]) == set(CORE_ITEMS)
    catalogue = content_pack.effective_catalogue()
    assert catalogue.fallback and catalogue.failed == {"author.n3": "maximum recursion depth exceeded"}
    assert content_pack.library_status()["author.n3"]["state"] == "damaged"
    assert any(e["operation"] == "content_library" and e["stage"] == "read" for e in diagnostic_events.error_events())
