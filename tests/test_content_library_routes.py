"""Design 10 WP-R4a: content libraries carry study routes (namespace study_routes).

Synthetic data only: the policy table, the route_id prefix rule at merge
(refused rows are reported, never raised), the precheck route summary and
outside-identity classification, the packaging tool's --route/--sample-route
round trip, ContentLibraryService.libraries() route_count and the fake-UI text.
WP-R4b: content_pack.clear_cache drops built route trees (token path), the
precheck's full route check (catalogue | provides, kana table), libraries()
routes_ignored, and the end-to-end install / disable / enable round trip of a
--sample-route library through StudyPathService.routes_overview (an empty
kana table; the learning items are the synthetic lesson items with a mapping
instruction, as learning_content would serve them).
"""
import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import content_library_merge as merge
from kanamemory import content_library_rules as rules
from kanamemory import content_pack
from kanamemory import study_routes
from kanamemory.content_availability import Catalogue
from kanamemory.learning_store import LearningStore
from kanamemory.study_path import StudyPathService
from kanamemory.content_library_service import ContentLibraryService
from kanamemory.domain import StudyStore
from support.app_fixtures import setup_app, question_app  # noqa: F401
from support.content_library_support import CORE_ITEMS, build_core_resources, extension_namespaces, pack_bytes
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)
from support.fake_ui import text_of
from support.learning_fixtures import learning_app, click  # noqa: F401
from support.library_availability_support import table_hashes

ROOT = Path(__file__).resolve().parents[1]
CORE = rules.CORE_LIBRARY_ID
LIB = "author.n3"
GOOD = "route:author.n3:n3"


def _tool():
    spec = importlib.util.spec_from_file_location("package_library_pack", ROOT / "tools" / "package_library_pack.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _spec(route_id=GOOD, entries=("grammar:ext:masu", "grammar:demo:desu"), **extra):
    return {"route_id": route_id, "title": "N3 路线", "library_id": LIB, "version": 1,
            "stages": [{"id": "s", "title": "阶段", "units": [{"id": "u", "title": "单元", "sections": [
                {"id": "a", "title": "第一节", "entries": list(entries[:1])},
                {"id": "b", "title": "第二节", "entries": list(entries[1:])}]}]}], **extra}


def _route_pack(routes, *, references=(), listed=None):
    namespaces = {"lesson": extension_namespaces()["lesson"], "study_routes": {"routes": routes}}
    provides = {"items": ["grammar:ext:masu"], "namespaces": sorted(namespaces),
                "routes": list(routes) if listed is None else listed}
    from kanamemory.material_review import item_fingerprint
    return pack_bytes(LIB, namespaces, items=["grammar:ext:masu"],
                      fingerprints={"grammar:ext:masu": item_fingerprint(namespaces["lesson"]["_ITEMS"]["grammar:ext:masu"])},
                      manifest_overrides={"provides": provides, "references": list(references)})


def _report(tmp_path, data, name="pack.zip"):
    path = tmp_path / name
    path.write_bytes(data)
    return rules.validate_library_pack(path)


# ---------------------------------------------------------------- policy and merge


def test_policy_table_holds_study_routes_items_by_id():
    assert merge.NAMESPACE_POLICIES["study_routes"] == "items_by_id"
    assert merge.builtin_policy("study_routes", "routes") == "items_by_id"
    assert merge.effective_policy("study_routes", "routes", [(LIB, "map_by_key")])[1] == [
        (LIB, "study_routes", "map_by_key", "items_by_id")]
    assert merge.route_prefix(LIB) == "route:author.n3:" and merge.route_library_id(GOOD) == LIB
    assert merge.route_library_id("route:author.n3:") is None and merge.route_library_id("author.n3:x") is None


def test_wrong_prefix_is_dropped_and_reported_not_raised():
    payload = {"routes": {GOOD: _spec(), "route:other.lib:x": _spec("route:other.lib:x"),
                          "route:author.n3:y": _spec("route:author.n3:z"), "route:author.n3:bad": ["not", "a", "map"]}}
    merged, report = merge.merge_namespace([(LIB, payload)], namespace="study_routes")
    assert list(merged["routes"]) == [GOOD]
    assert [row[2] for row in report.refused_entries] == ["route:other.lib:x", "route:author.n3:y", "route:author.n3:bad"]
    assert report.refused_entries[0] == (LIB, "routes", "route:other.lib:x", "route_id must start with route:author.n3:")
    assert list(payload["routes"]) == [GOOD, "route:other.lib:x", "route:author.n3:y", "route:author.n3:bad"]  # not mutated
    # A library cannot slip a route into another library's (or the core's) prefix.
    merged, report = merge.merge_namespace(
        [(CORE, {"routes": {"route:kanamemory.core:extra": {"title": "核心"}}}),
         (LIB, {"routes": {"route:kanamemory.core:extra": {"title": "冒充"}, GOOD: _spec()}})], namespace="study_routes")
    assert merged["routes"] == {"route:kanamemory.core:extra": {"title": "核心"}, GOOD: _spec()}
    assert report.refused_entries == ((LIB, "routes", "route:kanamemory.core:extra",
                                       "route_id must start with route:kanamemory.core:".replace("kanamemory.core", LIB)),)
    # Non-route namespaces never use the filter.
    _, report = merge.merge_namespace([(LIB, {"routes": {"x": 1}}), ("other.lib", {"routes": {"y": 2}})], namespace="lesson")
    assert report.refused_entries == ()


def test_same_route_id_follows_the_same_id_rules():
    same = [(LIB, {"routes": {GOOD: _spec()}}), (LIB, {"routes": {GOOD: _spec()}})]
    merged, report = merge.merge_namespace(same, namespace="study_routes")
    assert merged == {"routes": {GOOD: _spec()}} and report.duplicates == (("routes", GOOD, LIB, LIB),)
    changed = [(LIB, {"routes": {GOOD: _spec()}}), (LIB, {"routes": {GOOD: _spec(title="改")}})]
    merged, report = merge.merge_namespace(changed, namespace="study_routes")
    assert merged["routes"][GOOD]["title"] == "N3 路线" and report.conflicts == (("routes", GOOD, LIB, LIB),)
    merged, report = merge.merge_namespace(changed, namespace="study_routes", overrides_accepted=[(LIB, GOOD)])
    assert merged["routes"][GOOD]["title"] == "改" and report.overridden == (("routes", GOOD, LIB, LIB),)


def test_effective_catalogue_lists_accepted_routes_with_their_library():
    ext = merge.ExtensionLibrary(LIB, {"provides": {"items": ["grammar:ext:masu"], "namespaces": ["lesson", "study_routes"]}},
                                 {"lesson": extension_namespaces()["lesson"],
                                  "study_routes": {"routes": {GOOD: _spec(), "route:x.y.z:r": _spec("route:x.y.z:r")}}})
    catalogue = merge.build_effective_catalogue(core_items=dict(CORE_ITEMS), extensions=[ext])
    assert catalogue.study_route_ids() == (GOOD,) and catalogue.route_library(GOOD) == LIB
    assert catalogue.route_library("route:x.y.z:r") is None
    assert catalogue.study_routes() == ((GOOD, LIB, _spec()),)
    assert catalogue.refused_routes == ((LIB, "routes", "route:x.y.z:r", "route_id must start with route:author.n3:"),)
    assert ("route_refused", LIB, "route:x.y.z:r", "route_id must start with route:author.n3:") in catalogue.diagnostics
    assert list(catalogue.payloads[LIB]["study_routes"]["routes"]) == [GOOD]
    assert not catalogue.fallback
    core = merge.core_only(core_ids=frozenset(CORE_ITEMS))
    assert core.study_routes() == () and core.refused_routes == () and core.route_library(GOOD) is None


# ---------------------------------------------------------------- shape pre-check and precheck


@pytest.mark.parametrize("spec,problem", [
    (_spec("route:other.lib:n3"), "route_id must start"),
    ({**_spec(), "build": {"kind": "filter"}}, "exactly one of stages or build"),
    ({"route_id": GOOD, "title": "空"}, "exactly one of stages or build"),
    ({"route_id": GOOD, "build": "filter"}, "build must be a mapping"),
    ({"route_id": GOOD, "stages": []}, "stages must be a non-empty list"),
    ({"route_id": GOOD, "stages": [{"units": [{"sections": [{"entries": [1]}]}]}]}, "entries are text ids"),
    ({"route_id": GOOD, "stages": [{"units": [{"sections": [{"entries": [{"id": "grammar:x"}]}]}]}]}, "entries are text ids"),
    ({"route_id": GOOD, "stages": [{"units": [{"sections": [{"title": "无条目"}]}]}]}, "entries list"),
    ({"route_id": GOOD, "stages": [{"units": [{"sections": [{"entries": [f"word:x:{i}" for i in range(20001)]}]}]}]},
     "more than 20000 entries"),
])
def test_route_shape_precheck_rejects_bad_specs(spec, problem):
    problems = rules.route_spec_problems(LIB, spec["route_id"], spec)
    assert problems and problem in problems[0] + " ".join(problems)


def test_route_shape_precheck_accepts_explicit_and_build_specs():
    assert rules.route_spec_problems(LIB, GOOD, _spec(entries=("grammar:ext:masu", {"id": "grammar:ext:masu", "kind": "reference"}))) == []
    assert rules.route_spec_problems(LIB, GOOD, {"route_id": GOOD, "build": {"kind": "filter", "select": {"tags": ["n3"]}}}) == []
    assert rules.route_entry_ids(_spec(entries=("a", {"id": "b", "kind": "reference"}, "kana:kana-3042:hiragana"))) == [
        "a", "b", "kana:kana-3042:hiragana"]


def test_precheck_counts_routes_and_classifies_outside_identities(tmp_path):
    routes = {GOOD: _spec(entries=("grammar:ext:masu", "grammar:demo:desu", "word:demo:mizu", "grammar:nowhere",
                                   "kana:kana-3042:hiragana")),
              "route:author.n3:small": _spec("route:author.n3:small", entries=("grammar:ext:masu",)),
              "route:other.lib:x": _spec("route:other.lib:x")}
    report = _report(tmp_path, _route_pack(routes, listed=[GOOD, "route:author.n3:small"], references=[
        {"id": "grammar:demo:desu", "kind": "item", "requirement": "optional"},
        {"id": "grammar:nowhere", "kind": "item", "requirement": "optional"}]))
    assert report.manifest["provides"]["routes"] == [GOOD, "route:author.n3:small"]
    assert list(report.study_routes["routes"]) == list(routes)
    check = rules.precheck_pack(report.manifest, installed_fingerprints={}, learned_ids=(), installed_library_ids=[CORE],
                                catalogue_ids={"grammar:demo:desu", "word:demo:mizu"}, routes=report.study_routes)
    # WP-R4b full check: GOOD names grammar:nowhere (in no installed catalogue) and
    # the small route has an empty second section, so both are ignored (warning only).
    assert check.route_count == 0
    assert check.route_entry_counts == ()
    assert check.route_external_required == ()
    assert check.route_external_optional == ("grammar:demo:desu", "grammar:nowhere")
    assert check.route_external_undeclared == ("word:demo:mizu",)
    assert check.route_external_missing == ("grammar:nowhere",)
    assert check.route_errors == ((GOOD, "学习路线引用目录外身份：grammar:nowhere"),
                                  ("route:author.n3:small", "学习路线规格无效：section.entries"),
                                  ("route:other.lib:x", "route_id must start with route:author.n3:"))
    assert check.blocking_reasons() == ()  # author errors and refused routes only warn
    assert ("3 条路线规格有误，安装后将被忽略：route:author.n3:n3、route:author.n3:small、route:other.lib:x"
            in check.warnings)
    assert "manifest 列出的路线与 study_routes 文档不一致" not in check.warnings
    assert "包作者错误：路线引用了 1 个本库以外、未在 references 中声明的身份" in check.warnings
    assert "路线引用的 1 个身份不在已安装的库中，相关条目将显示为依赖缺失" in check.warnings
    # A required outside identity is classified (its absence already blocks through the references rule).
    report = _report(tmp_path, _route_pack({GOOD: _spec()}, references=[
        {"id": "grammar:demo:desu", "kind": "item", "requirement": "required"}]), "required.zip")
    check = rules.precheck_pack(report.manifest, installed_fingerprints={}, learned_ids=(), installed_library_ids=[CORE],
                                catalogue_ids={"grammar:demo:desu"}, routes=report.study_routes)
    assert check.route_external_required == ("grammar:demo:desu",) and check.warnings == () and check.route_count == 1


def test_precheck_without_routes_is_unchanged(tmp_path):
    report = _report(tmp_path, pack_bytes(LIB, extension_namespaces()))
    assert report.study_routes is None and "routes" not in report.manifest["provides"]
    kwargs = dict(installed_fingerprints={}, learned_ids=(), installed_library_ids=[CORE], catalogue_ids=set())
    check = rules.precheck_pack(report.manifest, **kwargs)
    assert check == rules.precheck_pack(report.manifest, routes=None, **kwargs)
    assert (check.route_count, check.route_entry_counts, check.route_errors, check.warnings) == (0, (), (), ())
    with pytest.raises(rules.ContentLibraryError):
        rules.validate_manifest({**report.manifest, "provides": {**report.manifest["provides"], "other": []}})
    bad = _report(tmp_path, _route_pack({GOOD: _spec()}, listed=[GOOD]), "listed.zip")
    shape = rules.precheck_pack(bad.manifest, routes={"routes": ["x"]}, **kwargs)
    assert shape.route_errors and shape.route_count == 0


# ---------------------------------------------------------------- packaging tool, install, service


@pytest.fixture
def world(tmp_path, monkeypatch):
    resources = build_core_resources(tmp_path / "resources")
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(tmp_path / "libraries"))
    content_pack.clear_cache()
    store = StudyStore(tmp_path / "learner", time_zone="UTC")
    service = ContentLibraryService(store, catalogue_ids=lambda: set(CORE_ITEMS), catalogue_fingerprints=lambda: {},
                                    learned_ids=lambda: set(), word_exists=lambda word_id: False)
    yield {"resources": resources, "service": service, "store": store}
    store.close()
    content_pack.clear_cache()


def _install(world, data):
    inspection = world["service"].inspect_bytes(data, "lib.zip")
    world["service"].install(inspection)
    content_pack.clear_cache()  # "restart"
    return inspection.precheck


def test_only_core_has_no_routes(world):
    assert content_pack.load_namespace("study_routes", {"routes": {}}) == {"routes": {}}
    catalogue = content_pack.effective_catalogue()
    assert catalogue.study_routes() == () and catalogue.refused_routes == ()
    rows = world["service"].libraries()
    assert [row["route_count"] for row in rows] == [0]


def test_packaging_tool_writes_routes_and_they_read_back_after_install(world, tmp_path):
    tool = _tool()
    route_file = tmp_path / "route.json"
    route_file.write_text(json.dumps({"route_id": "route:copy.lib:own", "title": "自定义",
                                      "stages": [{"id": "s", "title": "阶段", "units": [{"title": "单元", "sections": [
                                          {"title": "一", "entries": ["grammar:copy.lib:demo:desu", "word:demo:mizu"]},
                                          {"title": "二", "entries": [{"id": "word:copy.lib:demo:mizu", "kind": "reference"}]}]}]}]},
                                     ensure_ascii=False), encoding="utf-8")
    specs = tool.load_route_specs(route_file)
    result = tool.package(world["resources"], tmp_path / "copy.zip", library_id="copy.lib", extension=True,
                          namespaces=["lesson"], rewrite_ids=True, title="副本", routes=specs, with_sample_route=True)
    assert result["routes"] == ["route:copy.lib:own", "route:copy.lib:sample"]
    report = rules.validate_library_pack(tmp_path / "copy.zip")
    manifest = report.manifest
    assert manifest["provides"]["routes"] == ["route:copy.lib:own", "route:copy.lib:sample"]
    assert "study_routes" in manifest["provides"]["namespaces"] and "teaching/study_routes.json" in manifest["files"]
    sample = report.study_routes["routes"]["route:copy.lib:sample"]
    sections = sample["stages"][0]["units"][0]["sections"]
    assert [s["title"] for s in sections] == ["本库内容", "原内容对照"]
    assert sections[0]["entries"] == ["grammar:copy.lib:demo:desu", "word:copy.lib:demo:mizu"]
    assert sections[1]["entries"] == ["grammar:demo:desu", "word:demo:mizu"]
    # Outside identities of the routes become optional references (Q7).
    assert manifest["references"] == [{"id": cid, "kind": "item", "requirement": "optional", "note": ""}
                                      for cid in ("grammar:demo:desu", "word:demo:mizu")]
    precheck = _install(world, (tmp_path / "copy.zip").read_bytes())
    assert precheck.route_count == 2 and precheck.blocking_reasons() == ()
    assert precheck.route_entry_counts == (("route:copy.lib:own", 3), ("route:copy.lib:sample", 4))
    assert precheck.route_external_optional == ("word:demo:mizu", "grammar:demo:desu")
    assert precheck.route_external_undeclared == () and precheck.route_external_missing == ()
    catalogue = content_pack.effective_catalogue()
    assert [(rid, lib) for rid, lib, _ in catalogue.study_routes()] == [("route:copy.lib:own", "copy.lib"),
                                                                       ("route:copy.lib:sample", "copy.lib")]
    assert catalogue.route_library("route:copy.lib:sample") == "copy.lib" and not catalogue.fallback
    merged = content_pack.load_namespace("study_routes", {"routes": {}})
    assert list(merged["routes"]) == ["route:copy.lib:own", "route:copy.lib:sample"]
    rows = {row["library_id"]: row for row in world["service"].libraries()}
    assert rows["copy.lib"]["route_count"] == 2 and rows[CORE]["route_count"] == 0
    # The packaging tool refuses routes it would have to drop.
    with pytest.raises(ValueError, match="must start with route:copy.lib:"):
        tool.package(world["resources"], tmp_path / "bad.zip", library_id="copy.lib", extension=True,
                     namespaces=["lesson"], routes=[{**specs[0], "route_id": "route:else.lib:own"}])
    with pytest.raises(ValueError, match="extension"):
        tool.package(world["resources"], tmp_path / "core.zip", routes=specs)
    with pytest.raises(ValueError, match="rewrite-ids"):
        tool.package(world["resources"], tmp_path / "s.zip", library_id="copy.lib", extension=True, with_sample_route=True)


def test_installed_pack_with_a_wrong_prefix_route_keeps_the_rest(world, tmp_path):
    data = _route_pack({GOOD: _spec(), "route:other.lib:x": _spec("route:other.lib:x")}, listed=[GOOD],
                       references=[{"id": "grammar:demo:desu", "kind": "item", "requirement": "optional"}])
    precheck = _install(world, data)
    assert precheck.route_count == 1 and precheck.route_errors and precheck.blocking_reasons() == ()
    catalogue = content_pack.effective_catalogue()
    assert catalogue.study_route_ids() == (GOOD,) and not catalogue.fallback
    assert catalogue.refused_routes == ((LIB, "routes", "route:other.lib:x", "route_id must start with route:author.n3:"),)
    assert content_pack.library_status() == {LIB: {"state": "ready", "reason": None}}
    assert content_pack.library_merge_report().get("study_routes", {}).get("refused_entries", []) == []
    assert list(content_pack.load_namespace("study_routes", {})["routes"]) == [GOOD]
    assert {row["library_id"]: row["route_count"] for row in world["service"].libraries()} == {CORE: 0, LIB: 1}


# ---------------------------------------------------------------- fake UI


@pytest.fixture
def library_app(learning_app, tmp_path, monkeypatch):
    app = learning_app
    resources = build_core_resources(tmp_path / "resources")
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(tmp_path / "libraries"))
    content_pack.clear_cache()
    app.go_back = Mock()
    # The service checks route kana entries against the App's kana table (WP-R4b).
    app.repo.kana_list = Mock(return_value=[{"id": "kana-3042", "hiragana": "あ", "katakana": "ア"}])
    service = app.content_library_service()
    service._catalogue_ids = lambda: set(CORE_ITEMS)
    service._catalogue_fingerprints = lambda: {}
    yield app
    content_pack.clear_cache()


def test_precheck_and_list_show_route_lines(library_app):
    app = library_app
    data = _route_pack({GOOD: _spec(entries=("grammar:ext:masu", "grammar:demo:desu", "word:demo:mizu")),
                        "route:other.lib:x": _spec("route:other.lib:x")}, listed=[GOOD],
                       references=[{"id": "grammar:demo:desu", "kind": "item", "requirement": "optional"}])
    app.platform.import_library_pack = AsyncMock(return_value=data)
    app.show_content_libraries()
    assert "本库路线" not in text_of(app.body)
    asyncio.run(app.add_content_library())
    assert app.page == "content_library_precheck"
    text = text_of(app.body)
    assert "本库路线 1 条" in text and f"路线 {GOOD}：3 个条目" in text
    assert "路线引用本库以外的身份 2 项（必需 0 · 可选 1 · 未声明 1）" in text
    assert "路线规格有误（将被忽略）：route:other.lib:x" in text
    assert "提示：包作者错误：路线引用了 1 个本库以外、未在 references 中声明的身份" in text
    click(app, "安装")
    assert app.page == "content_libraries"
    assert text_of(app.body).count("本库路线 1 条") == 1
    content_pack.clear_cache()  # "restart": the refused route is listed on the library row (WP-R4b)
    app.show_content_libraries()
    text = text_of(app.body)
    assert "已忽略路线 1 条" in text and "已忽略：route:other.lib:x · route_id must start with route:author.n3:" in text


def test_precheck_and_list_without_routes_show_no_route_line(library_app):
    app = library_app
    app.platform.import_library_pack = AsyncMock(return_value=pack_bytes(LIB, extension_namespaces()))
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    assert app.page == "content_library_precheck" and "路线" not in text_of(app.body)
    click(app, "安装")
    assert app.page == "content_libraries" and "本库路线" not in text_of(app.body)


# ---------------------------------------------------------------- WP-R4b: route cache, full check, routes_ignored

E2E = "e2e.lib"
E2E_ROUTE = "route:e2e.lib:sample"


def _sample_library(world, tmp_path, library_id=E2E):
    path = tmp_path / (library_id + ".zip")
    _tool().package(world["resources"], path, library_id=library_id, extension=True, namespaces=["lesson"],
                    rewrite_ids=True, title="端到端", with_sample_route=True)
    return path.read_bytes()


def _items():
    """The effective lesson items as learning items (instruction as a mapping)."""
    return [{**item, "instruction": {"summary": item["instruction"] or item["title"]}}
            for item in content_pack.load_namespace("lesson")["_ITEMS"].values()]


def _restart(world):
    content_pack.clear_cache()
    world["service"].startup_sync()
    world["store"].content_catalogue = Catalogue(ids=frozenset(item["id"] for item in _items()),
                                                 effective=content_pack.effective_catalogue())


def test_clear_cache_drops_built_route_trees_and_a_new_token_rebuilds(world, tmp_path, monkeypatch):
    _install(world, _sample_library(world, tmp_path))
    calls = []
    original = study_routes.build_route
    monkeypatch.setattr(study_routes, "build_route", lambda *a, **k: calls.append(1) or original(*a, **k))
    first = content_pack.effective_catalogue()

    def build():
        catalogue = content_pack.effective_catalogue()
        return study_routes.build_route_set({"version": "core", "stages": []}, library_rows=catalogue.study_routes(),
                                            catalogue=_items(), kana_rows=(), cache_token=catalogue.token)
    tree = build().route(E2E_ROUTE)
    assert len(calls) == 1
    assert build().route(E2E_ROUTE) == tree and len(calls) == 1  # same token: reused
    content_pack.clear_cache()  # rebuilt effective catalogue: the trees are gone
    assert content_pack.effective_catalogue() is not first and content_pack.effective_catalogue().token == first.token
    assert build().route(E2E_ROUTE) == tree and len(calls) == 2
    # Disabling changes the registry rows, so the restarted catalogue has another token and no route.
    world["service"].set_enabled(E2E, False)
    content_pack.clear_cache()
    disabled = content_pack.effective_catalogue()
    assert disabled.token != first.token and disabled.study_routes() == ()
    assert build().route_ids() == (study_routes.CORE_ROUTE_ID,) and len(calls) == 2
    world["service"].set_enabled(E2E, True)
    content_pack.clear_cache()
    assert content_pack.effective_catalogue().token == first.token
    assert build().route(E2E_ROUTE) == tree and len(calls) == 3


def test_enrolling_a_catalogue_lesson_keeps_the_built_route_trees(world, tmp_path, monkeypatch):
    # The key of a built tree holds what the builders read: the catalogue and the saved lessons outside it.
    # It used to list every saved lesson, so each enrolment rebuilt every library route.
    _install(world, _sample_library(world, tmp_path))
    calls = []
    original = study_routes.build_route
    monkeypatch.setattr(study_routes, "build_route", lambda *a, **k: calls.append(1) or original(*a, **k))
    items = _items()
    core = {"version": "core", "stages": []}

    def build(saved, cached=True):
        catalogue = content_pack.effective_catalogue()
        return study_routes.build_route_set(core, library_rows=catalogue.study_routes(), catalogue=items, kana_rows=(),
                                            extra_items=saved, cache_token=catalogue.token if cached else None)
    tree = build([]).route(E2E_ROUTE)
    assert len(calls) == 1
    # A saved lesson of the catalogue, even an older snapshot of it, is read from the catalogue.
    saved = [dict(items[0], revision="older", depends_on=[]), dict(items[-1])]
    assert build(saved).route(E2E_ROUTE) == tree and len(calls) == 1
    assert build(saved, cached=False).route(E2E_ROUTE) == tree and len(calls) == 2  # what a fresh build gives
    # A lesson outside the catalogue can be part of a route: another key, with its revision in it.
    own = {**items[0], "id": "local:test:own", "content_id": "local:test:own", "revision": "1", "depends_on": []}
    build([*saved, own])
    assert len(calls) == 3
    build([*saved, own])
    assert len(calls) == 3
    build([*saved, dict(own, revision="2")])
    assert len(calls) == 4


def test_precheck_fully_validates_routes_against_catalogue_and_kana_table(tmp_path):
    kana_ok, kana_gone = "kana:kana-3042:hiragana", "kana:kana-9999:hiragana"
    routes = {GOOD: _spec(),
              "route:author.n3:kana": _spec("route:author.n3:kana", entries=("grammar:ext:masu", kana_gone)),
              "route:author.n3:after": {**_spec("route:author.n3:after"), "stages": [
                  {"id": "s", "title": "阶段", "units": [{"id": "u", "title": "单元", "sections": [
                      {"id": "a", "title": "一", "entries": ["grammar:ext:masu"], "after": ["word:demo:mizu"]}]}]}]},
              "route:author.n3:dup": _spec("route:author.n3:dup", entries=("grammar:ext:masu", "grammar:ext:masu")),
              "route:author.n3:nolib": {key: value for key, value in _spec("route:author.n3:nolib").items()
                                        if key != "library_id"}}
    report = _report(tmp_path, _route_pack(routes, references=[
        {"id": "grammar:demo:desu", "kind": "item", "requirement": "optional"}]))
    kwargs = dict(installed_fingerprints={}, learned_ids=(), installed_library_ids=[CORE],
                  catalogue_ids={"grammar:demo:desu"}, routes=report.study_routes)
    check = rules.precheck_pack(report.manifest, kana_identities={kana_ok}, **kwargs)
    assert check.route_count == 1 and check.route_entry_counts == ((GOOD, 2),)
    assert check.route_errors == (("route:author.n3:kana", "学习路线引用目录外身份：" + kana_gone),
                                  ("route:author.n3:after", "软顺序 after 只能引用本路线条目：word:demo:mizu"),
                                  ("route:author.n3:dup", "学习路线内条目身份重复：grammar:ext:masu"),
                                  ("route:author.n3:nolib", "学习路线规格的 library_id 与路线标识不一致。"))
    assert check.blocking_reasons() == () and "manifest 列出的路线与 study_routes 文档不一致" not in check.warnings
    # Without a kana table the routes' own kana entries are taken as known.
    relaxed = rules.precheck_pack(report.manifest, **kwargs)
    assert [rid for rid, _ in relaxed.route_entry_counts] == [GOOD, "route:author.n3:kana"]
    # The same rule over a running catalogue's rows (refused rows come from the merge instead).
    rows = [(rid, LIB, spec) for rid, spec in routes.items()]
    assert rules.route_problems_in_catalogue(rows, catalogue_ids={"grammar:demo:desu", "grammar:ext:masu"},
                                             kana_identities={kana_ok}) == {LIB: check.route_errors}
    assert rules.kana_identities_of([{"id": "kana-3042", "hiragana": "あ", "katakana": "ア"},
                                     {"id": "kana-x", "hiragana": "ゔ", "katakana": ""}]) == {
        "kana:kana-3042:hiragana", "kana:kana-3042:katakana", "kana:kana-x:hiragana"}
    assert rules.kana_identities_of(None) is None  # no kana table: the route check takes kana entries as known


def test_library_rows_count_ignored_routes_with_reasons(world, tmp_path):
    world["service"]._kana_identities = lambda: {"kana:kana-3042:hiragana"}
    data = _route_pack({GOOD: _spec(),
                        "route:author.n3:missing": _spec("route:author.n3:missing",
                                                         entries=("grammar:ext:masu", "grammar:nowhere")),
                        "route:author.n3:kana": _spec("route:author.n3:kana",
                                                      entries=("grammar:ext:masu", "kana:kana-9:hiragana")),
                        "route:other.lib:x": _spec("route:other.lib:x")},
                       listed=[GOOD, "route:author.n3:missing", "route:author.n3:kana"],
                       references=[{"id": "grammar:demo:desu", "kind": "item", "requirement": "optional"},
                                   {"id": "grammar:nowhere", "kind": "item", "requirement": "optional"}])
    precheck = _install(world, data)
    assert precheck.route_count == 1 and precheck.blocking_reasons() == ()
    assert [rid for rid, _ in precheck.route_errors] == ["route:author.n3:missing", "route:author.n3:kana",
                                                         "route:other.lib:x"]
    rows = {row["library_id"]: row for row in world["service"].libraries()}
    assert rows[CORE]["routes_ignored"] == 0 and rows[CORE]["routes_ignored_reasons"] == ()
    assert rows[LIB]["route_count"] == 3 and rows[LIB]["routes_ignored"] == 3
    assert rows[LIB]["routes_ignored_reasons"] == (
        ("route:other.lib:x", "route_id must start with route:author.n3:"),
        ("route:author.n3:missing", "学习路线引用目录外身份：grammar:nowhere"),
        ("route:author.n3:kana", "学习路线引用目录外身份：kana:kana-9:hiragana"))


# ---------------------------------------------------------------- WP-R4b: end to end


def test_sample_route_library_install_disable_enable_round_trip(world, tmp_path):
    store, service = world["store"], world["service"]
    class NoKana:
        def kana_list(self):
            return []
    repo = NoKana()
    def path_service():
        return StudyPathService(repo, store, LearningStore(store), catalogue=_items())

    def hashes():
        return table_hashes(store)  # every table; only the registry key is skipped
    LearningStore(store)  # the learning schema exists before the first hash
    start = hashes()
    precheck = _install(world, _sample_library(world, tmp_path))
    assert precheck.route_count == 1 and precheck.route_errors == () and precheck.blocking_reasons() == ()
    _restart(world)
    token = content_pack.effective_catalogue().token
    assert {row["library_id"]: row["routes_ignored"] for row in service.libraries()} == {CORE: 0, E2E: 0}
    overview = path_service().routes_overview()
    assert [(row["route_id"], row["status"]) for row in overview["routes"]] == [
        (study_routes.CORE_ROUTE_ID, "available"), (E2E_ROUTE, "available")]
    assert overview["diagnostics"] == [] and hashes() == start
    # Add one section of the library route and make it current: the only progress change.
    joined = path_service()
    result = joined.add_sections([E2E_ROUTE + "#own"], route_id=E2E_ROUTE)
    assert result["added_sections"] == [E2E_ROUTE + "#own"]
    assert sorted(result["added_learning"]) == ["grammar:e2e.lib:demo:desu", "word:e2e.lib:demo:mizu"]
    assert joined.set_active_route(E2E_ROUTE) is True
    added = hashes()
    assert added != start
    stored = store.get_setting(study_routes.ROUTES_SETTING)
    assert stored["active_route"] == E2E_ROUTE
    assert stored["routes"][E2E_ROUTE]["added_sections"] == [E2E_ROUTE + "#own"]
    row = {r["route_id"]: r for r in path_service().routes_overview()["routes"]}[E2E_ROUTE]
    assert row["active"] and row["recorded_added_sections"] == 1 and row["progress"]["added_sections"] == 1
    # Disable the library and restart: a read-only row, sections kept, current route falls back.
    service.set_enabled(E2E, False)
    _restart(world)
    assert content_pack.effective_catalogue().token != token
    gone = path_service()
    summary = gone.routes_overview()
    assert summary["active_route_id"] == study_routes.CORE_ROUTE_ID
    assert summary["stored_active_route_id"] == E2E_ROUTE
    assert [r["route_id"] for r in summary["routes"]] == [study_routes.CORE_ROUTE_ID, E2E_ROUTE]
    row = summary["routes"][1]
    assert (row["available"], row["status"], row["progress"], row["recorded_added_sections"]) == (
        False, "library_unavailable", None, 1)
    assert gone.route_state(E2E_ROUTE)["added_sections"] == [E2E_ROUTE + "#own"]
    assert store.get_setting(study_routes.ROUTES_SETTING) == stored and hashes() == added
    # Enable again: the route, its sections and the current route are back.
    service.set_enabled(E2E, True)
    _restart(world)
    assert content_pack.effective_catalogue().token == token
    back = path_service().routes_overview()
    assert back["active_route_id"] == E2E_ROUTE
    row = {r["route_id"]: r for r in back["routes"]}[E2E_ROUTE]
    assert row["available"] and row["active"] and row["recorded_added_sections"] == 1
    assert row["progress"]["added_sections"] == 1
    assert store.get_setting(study_routes.ROUTES_SETTING) == stored and hashes() == added
