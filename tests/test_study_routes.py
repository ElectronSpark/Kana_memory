"""Parallel study route rules (design 10 WP-R1) on synthetic catalogues only."""
from copy import deepcopy

import pytest

from kanamemory import study_routes as routes
from kanamemory.study_path_catalog import (assemble_study_path, build_study_path, flatten_sections,
                                           study_path_records)

RID = "route:demo.lib:basics"
KANA = [{"id": "syn-a", "hiragana": "あ", "katakana": "ア", "category": "basic", "tier": "core"},
        {"id": "syn-ka", "hiragana": "か", "katakana": "カ", "category": "basic", "tier": "core"}]


def item(cid, family="word", depends=(), tags=None):
    value = {"id": cid, "family": family, "title": "syn-" + cid.split(":")[-1], "depends_on": list(depends)}
    if tags is not None:
        value["tags"] = list(tags)
    return value


CATALOGUE = [item("grammar:g1", "grammar"), item("word:w1", depends=["grammar:g1"], tags=["n5"]),
             item("word:w2", tags=["n5"]), item("word:w3", depends=["word:w2"]),
             item("word:w4"), item("word:w5")]
IDS = {value["id"] for value in CATALOGUE}
KANA_IDS = {"kana:syn-a:hiragana", "kana:syn-a:katakana", "kana:syn-ka:hiragana", "kana:syn-ka:katakana"}


def section(title, entries, **extra):
    return {"title": title, "entries": list(entries), **extra}


def spec(*units, stages=None, **extra):
    value = {"route_id": RID, "library_id": "demo.lib", "title": "合成路线", "version": 1,
             "stages": stages or [{"id": "s1", "title": "阶段一",
                                   "units": [{"title": "单元" + str(i), "sections": list(u)}
                                             for i, u in enumerate(units)]}]}
    value.update(extra)
    return value


def validate(value, **kwargs):
    return routes.validate_route_spec(value, catalogue_ids=IDS, kana_identities=KANA_IDS, **kwargs)


def build(value, **kwargs):
    return routes.build_route(value, catalogue=deepcopy(CATALOGUE), kana_rows=deepcopy(KANA), **kwargs)


def titles(tree):
    return [s["title"] for s in flatten_sections(tree)]


# --- constants and route identities -------------------------------------------------

def test_core_route_constants_and_route_id_rules():
    assert routes.CORE_ROUTE_ID == "route:kanamemory.core:main"
    assert routes.ROUTES_SETTING == "study_routes_v1"
    assert routes.parse_route_id(routes.CORE_ROUTE_ID) == ("kanamemory.core", "main")
    assert routes.route_id("demo.lib", "basics") == RID
    assert routes.local_route_id("abc-123") == "route:local:abc-123"
    assert routes.is_local_route("route:local:abc-123") and not routes.is_local_route(RID)
    for bad in ("demo.lib:basics", "route:demo.lib", "route:Demo:basics", "route:demo.lib:a:b",
                "route:demo.lib:", None, "route:" + "x" * 2000):
        with pytest.raises(ValueError):
            routes.parse_route_id(bad)


# --- route spec validation (design 10 section 6.2) ----------------------------------

def test_valid_spec_is_returned_as_detached_copy():
    value = spec([section("A", ["word:w1", {"id": "kana:syn-a:hiragana", "kind": "reference"}],
                          after=["word:w2"]), section("B", ["word:w2"], optional=True)])
    result = validate(value, library_id="demo.lib")
    assert result == value and result is not value
    result["stages"][0]["title"] = "changed"
    assert value["stages"][0]["title"] == "阶段一"


def test_spec_route_id_prefix_must_match_library():
    with pytest.raises(ValueError, match="前缀"):
        validate(spec([section("A", ["word:w1"])]), library_id="other.lib")
    with pytest.raises(ValueError, match="library_id"):
        validate(spec([section("A", ["word:w1"])], library_id="other.lib"))
    with pytest.raises(ValueError, match="标识无效"):
        validate(spec([section("A", ["word:w1"])], route_id="demo.lib:basics"))
    with pytest.raises(ValueError, match="核心主线"):
        validate(spec([section("A", ["word:w1"])], route_id=routes.CORE_ROUTE_ID,
                      library_id="kanamemory.core"))


def test_spec_identities_must_exist_in_effective_catalogue():
    with pytest.raises(ValueError, match="目录外身份"):
        validate(spec([section("A", ["word:missing"])]))
    with pytest.raises(ValueError, match="目录外身份"):
        validate(spec([section("A", ["kana:syn-zz:hiragana"])]))


def test_spec_identities_are_unique_within_route():
    with pytest.raises(ValueError, match="重复"):
        validate(spec([section("A", ["word:w1"]), section("B", [{"id": "word:w1", "kind": "reference"}])]))


def test_spec_optional_flags_must_be_boolean():
    with pytest.raises(ValueError, match="optional"):
        validate(spec([section("A", ["word:w1"], optional="yes")]))
    stages = [{"id": "s1", "title": "阶段", "optional": 1,
               "units": [{"title": "U", "sections": [section("A", ["word:w1"])]}]}]
    with pytest.raises(ValueError, match="optional"):
        validate(spec(stages=stages))


def test_spec_after_only_references_entries_of_this_route():
    with pytest.raises(ValueError, match="after"):
        validate(spec([section("A", ["word:w1"], after=["word:w2"])]))


def test_spec_entry_count_is_capped_at_20000():
    many = ["word:n%05d" % i for i in range(routes.MAX_ROUTE_ENTRIES + 1)]
    value = spec([section("A", many)])
    with pytest.raises(ValueError, match="上限"):
        routes.validate_route_spec(value, catalogue_ids=set(many), kana_identities=set())
    value["stages"][0]["units"][0]["sections"][0]["entries"] = many[:-1]
    routes.validate_route_spec(value, catalogue_ids=set(many), kana_identities=set())


def test_spec_requires_exactly_one_of_stages_or_filter_build():
    both = spec([section("A", ["word:w1"])], build={"kind": "filter", "select": {"ids": ["word:w1"]}})
    with pytest.raises(ValueError, match="二选一"):
        validate(both)
    neither = spec([section("A", ["word:w1"])])
    del neither["stages"]
    with pytest.raises(ValueError, match="二选一"):
        validate(neither)


def test_spec_filter_build_shape_is_checked():
    base = spec([section("A", ["word:w1"])])
    del base["stages"]
    for build_value, message in (({"kind": "manual", "select": {"ids": ["word:w1"]}}, "build.kind"),
                                 ({"kind": "filter", "select": {}}, "为空"),
                                 ({"kind": "filter", "select": {"ids": ["word:nope"]}}, "目录外"),
                                 ({"kind": "filter", "select": {"levels": ["n5"]}}, "未知字段"),
                                 ({"kind": "filter", "select": {"tags": []}}, "tags")):
        with pytest.raises(ValueError, match=message):
            validate({**base, "build": build_value})
    assert validate({**base, "build": {"kind": "filter", "select": {"families": ["kana"]}}})


def test_spec_rejects_unknown_fields_bad_entries_and_duplicate_ids():
    with pytest.raises(ValueError, match="未知字段"):
        validate(spec([section("A", ["word:w1"])], waive=["word:w1"]))
    with pytest.raises(ValueError, match="条目格式"):
        validate(spec([section("A", [{"id": "word:w1", "kind": "learning"}])]))
    with pytest.raises(ValueError, match="section.id"):
        validate(spec([section("A", ["word:w1"], id="x"), section("B", ["word:w2"], id="x")]))
    stages = [{"id": "s1", "title": "阶段", "units": [
        {"title": "同名", "sections": [section("A", ["word:w1"])]},
        {"title": "同名", "sections": [section("B", ["word:w2"])]}]}]
    with pytest.raises(ValueError, match="单元标题重复"):
        validate(spec(stages=stages))


# --- explicit route building (design 10 sections 6.3/6.4) ---------------------------

def test_hard_prerequisites_come_first_then_authored_order():
    # Same tie rule as the core route: among ready sections the earliest
    # authored one comes next; a blocked one waits for its prerequisite.
    tree = build(spec([section("W3", ["word:w3"]), section("W4", ["word:w4"]), section("W2", ["word:w2"])]))
    assert titles(tree) == ["W4", "W2", "W3"]
    by_title = {s["title"]: s for s in flatten_sections(tree)}
    assert by_title["W3"]["depends_on"] == [by_title["W2"]["id"]]
    assert tree["diagnostics"] == []


def test_units_are_ordered_by_hard_prerequisites_between_units():
    tree = build(spec([section("W3", ["word:w3"])], [section("W2", ["word:w2"])]))
    assert [u["title"] for u in tree["stages"][0]["units"]] == ["单元1", "单元0"]


def test_soft_after_reorders_without_adding_hard_prerequisites():
    tree = build(spec([section("W4", ["word:w4"], after=["word:w5"]), section("W5", ["word:w5"])]))
    assert titles(tree) == ["W5", "W4"]
    assert all(s["depends_on"] == [] for s in flatten_sections(tree))
    assert tree["diagnostics"] == []


def test_soft_after_cycle_is_ignored_with_diagnostics():
    tree = build(spec([section("W4", ["word:w4"], after=["word:w5"]),
                       section("W5", ["word:w5"], after=["word:w4"])]))
    assert titles(tree) == ["W5", "W4"]
    w5 = next(s for s in flatten_sections(tree) if s["title"] == "W5")
    assert tree["diagnostics"] == [{"code": "soft_order_cycle", "section_id": w5["id"], "after": "word:w4"}]
    # A soft order against a hard prerequisite never wins.
    tree = build(spec([section("W2", ["word:w2"], after=["word:w3"]), section("W3", ["word:w3"])]))
    assert titles(tree) == ["W2", "W3"]
    assert [d["code"] for d in tree["diagnostics"]] == ["soft_order_cycle"]


def test_hard_section_cycle_inside_one_unit_is_rejected():
    catalogue = [item("word:a1"), item("word:a2", depends=["word:b1"]),
                 item("word:b1"), item("word:b2", depends=["word:a1"])]
    value = spec([section("A", ["word:a1", "word:a2"]), section("B", ["word:b1", "word:b2"])])
    with pytest.raises(ValueError, match="成环"):
        routes.build_route(value, catalogue=catalogue, kana_rows=[])


def test_mutually_dependent_units_are_merged_with_diagnostic():
    catalogue = [item("word:a1"), item("word:a2", depends=["word:b1"]),
                 item("word:b1"), item("word:b2", depends=["word:a1"])]
    value = spec([section("A1", ["word:a1"]), section("A2", ["word:a2"])],
                 [section("B1", ["word:b1"]), section("B2", ["word:b2"])])
    tree = routes.build_route(value, catalogue=catalogue, kana_rows=[])
    assert len(tree["stages"][0]["units"]) == 1
    assert titles(tree) == ["A1", "B1", "A2", "B2"]
    assert [d["code"] for d in tree["diagnostics"]] == ["units_merged_for_prerequisite_cycle"]


def test_external_prerequisites_name_hard_prerequisites_outside_route():
    tree = build(spec([section("W1", ["word:w1"]), section("W3", ["word:w3", "kana:syn-a:hiragana"])]))
    by_title = {s["title"]: s for s in flatten_sections(tree)}
    assert by_title["W1"]["external_prerequisites"] == ["grammar:g1"]
    assert by_title["W3"]["external_prerequisites"] == ["word:w2"]
    assert all("unresolved_dependencies" not in s for s in flatten_sections(tree))


def test_missing_identity_is_rejected_by_build():
    with pytest.raises(ValueError, match="目录外身份"):
        build(spec([section("A", ["word:absent"])]))


def test_route_tree_shape_entries_and_identities():
    value = spec([section("A", ["word:w2", {"id": "kana:syn-a:hiragana", "kind": "reference"}]),
                  section("B", ["word:w3"], id="b-stable", optional=True)])
    tree = build(value)
    core = {e["id"]: e for s in flatten_sections(build_study_path(KANA, CATALOGUE)) for e in s["entries"]}
    assert (tree["route_id"], tree["library_id"], tree["title"]) == (RID, "demo.lib", "合成路线")
    assert tree["version"] == routes.ROUTE_TREE_VERSION and tree["stages"][0]["id"] == RID + "#s1"
    first, second = flatten_sections(tree)
    assert set(first) >= {"id", "title", "entries", "depends_on", "optional", "stage_id", "stage_title",
                          "unit_id", "unit_title", "external_prerequisites"}
    assert first["entries"][0] == core["word:w2"]
    assert first["entries"][1]["kind"] == "reference" and first["entries"][1]["depends_on"] == []
    assert second["id"] == RID + "#b-stable" and second["optional"] is True
    assert first["id"].startswith("section:") and first["unit_id"].startswith("unit:")
    assert build(value) == tree
    other = build({**value, "route_id": "route:demo.lib:other"})
    assert flatten_sections(other)[0]["id"] != first["id"]
    assert flatten_sections(other)[0]["unit_id"] != first["unit_id"]


# --- filter routes ----------------------------------------------------------------

def filter_spec(select):
    value = spec([section("A", ["word:w1"])], build={"kind": "filter", "select": select})
    del value["stages"]
    return value


def test_filter_route_reuses_core_grouping_with_route_scoped_identities():
    tree = build(filter_spec({"tags": ["n5"]}))
    entries = [e["id"] for s in flatten_sections(tree) for e in s["entries"]]
    assert sorted(entries) == ["word:w1", "word:w2"]
    core = flatten_sections(build_study_path(KANA, CATALOGUE))
    core_section = {e["id"]: s for s in core for e in s["entries"]}
    for route_section in flatten_sections(tree):
        original = core_section[route_section["entries"][0]["id"]]
        assert route_section["id"] != original["id"]
        assert (route_section["title"], route_section["stage_id"]) == (original["title"], original["stage_id"])
        assert route_section["entries"] == [e for e in original["entries"] if e["id"] in entries]
    w1 = next(s for s in flatten_sections(tree) if s["entries"][0]["id"] == "word:w1")
    assert w1["external_prerequisites"] == ["grammar:g1"] and "unresolved_dependencies" not in w1
    assert tree["diagnostics"] == [] and tree["route_id"] == RID


def test_filter_route_families_ids_and_empty_selection():
    kana = build(filter_spec({"families": ["kana"], "ids": ["kana:syn-a:hiragana", "kana:syn-ka:hiragana"]}))
    assert sorted(e["id"] for s in flatten_sections(kana) for e in s["entries"]) == [
        "kana:syn-a:hiragana", "kana:syn-ka:hiragana"]
    ordered = build(filter_spec({"ids": ["word:w3", "word:w2"]}))
    order = [e["id"] for s in flatten_sections(ordered) for e in s["entries"]]
    assert order.index("word:w2") < order.index("word:w3")
    with pytest.raises(ValueError, match="为空"):
        build(filter_spec({"families": ["pronunciation"]}))


# --- core route stays build_study_path ---------------------------------------------

def test_core_build_is_records_then_assembly_and_annotation_is_detached():
    records = study_path_records(KANA, CATALOGUE)
    assert assemble_study_path(records) == build_study_path(KANA, CATALOGUE)
    path = build_study_path(KANA, CATALOGUE)
    snapshot = deepcopy(path)
    view = routes.annotate_core_route(path)
    assert path == snapshot
    assert view["route_id"] == routes.CORE_ROUTE_ID and view["diagnostics"] == []
    assert all(s["external_prerequisites"] == [] for s in flatten_sections(view))


# --- study_routes_v1 (design 10 section 7.1) ---------------------------------------

def route_state(**overrides):
    value = {"added_sections": ["section:a"], "enabled": True, "source": "library",
             "library_id": "demo.lib", "created_at_utc": "2026-11-08T00:00:00+00:00"}
    value.update(overrides)
    return value


def test_default_routes_state_is_core_only_and_valid():
    default = routes.default_routes_state()
    assert default == {"version": 1, "active_route": routes.CORE_ROUTE_ID, "routes": {}}
    assert routes.validate_routes_state(default) == default
    assert routes.default_routes_state() is not routes.default_routes_state()


def test_routes_state_accepts_library_and_local_routes_as_detached_copy():
    local = "route:local:abc"
    value = {"version": 1, "active_route": local, "routes": {
        RID: route_state(enabled=False),
        local: route_state(source="local", library_id="local", added_sections=[],
                           spec={"route_id": local, "title": "我的路线"})}}
    result = routes.validate_routes_state(value)
    assert result == value and result is not value
    result["routes"][RID]["added_sections"].append("x")
    assert value["routes"][RID]["added_sections"] == ["section:a"]


@pytest.mark.parametrize("mutate, message", [
    (lambda v: v.update(version=2), "版本"),
    (lambda v: v.update(extra=1), "版本"),
    (lambda v: v["routes"].update({routes.CORE_ROUTE_ID: route_state(library_id="kanamemory.core")}), "核心主线"),
    (lambda v: v["routes"].update({"route:bad": route_state()}), "标识无效"),
    (lambda v: v["routes"][RID].update(added_sections=["a", "a"]), "added_sections"),
    (lambda v: v["routes"][RID].update(added_sections=["x" * 2000]), "added_sections"),
    (lambda v: v["routes"][RID].update(enabled="yes"), "enabled"),
    (lambda v: v["routes"][RID].update(library_id="other.lib"), "source/library_id"),
    (lambda v: v["routes"][RID].update(source="local"), "source/library_id"),
    (lambda v: v["routes"][RID].update(created_at_utc=""), "created_at_utc"),
    (lambda v: v["routes"][RID].update(spec={"route_id": RID}), "spec"),
    (lambda v: v["routes"][RID].update(note="x"), RID),
    (lambda v: v.update(active_route="route:demo.lib:unknown"), "active_route"),
])
def test_routes_state_rejects_invalid_shapes(mutate, message):
    value = {"version": 1, "active_route": RID, "routes": {RID: route_state()}}
    mutate(value)
    with pytest.raises(ValueError, match=message):
        routes.validate_routes_state(value)


def test_routes_state_caps_added_sections():
    value = {"version": 1, "active_route": routes.CORE_ROUTE_ID, "routes": {
        RID: route_state(added_sections=["s%05d" % i for i in range(routes.MAX_ROUTE_ENTRIES + 1)])}}
    with pytest.raises(ValueError, match="added_sections"):
        routes.validate_routes_state(value)
