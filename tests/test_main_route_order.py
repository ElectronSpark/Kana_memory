"""The given beginning of the main route (study_path_catalog MAIN_ROUTE_ORDER; main_route_order).

Small fixture catalogues only: runs on the demo content.
"""
from collections import Counter
from copy import deepcopy
import json

import pytest

from kanamemory import diagnostic_events
from kanamemory import main_route_order as given
from kanamemory import study_path_catalog as catalog
from kanamemory.content_library_merge import builtin_policy
from kanamemory.route_mix.params import GIVEN_ORDER, MixParams
from kanamemory.route_mix.plan import MixPlan, apply_plan, plan_mix, snapshot, tree_digest
from kanamemory.route_mix.readiness import Readiness
from kanamemory.route_mix.sections import split_library_sections
from kanamemory.route_mix.sources import mixable_stage_ids, needed_items
from kanamemory.study_path_catalog import build_study_path, flatten_sections, study_path_records

WORDS = "駅 学校 本 犬 猫 水 茶 肉 魚 米"


def word(form, depends=()):
    return {"id": "word:" + form, "title": form, "family": "word", "depends_on": list(depends)}


def grammar(key, depends=(), category="判断"):
    return {"id": "grammar:" + key, "title": key + "：说明", "family": "grammar", "category": category,
            "level": "入门", "depends_on": list(depends)}


def rule(key):
    return {"id": "pronunciation:" + key, "title": key + "：说明", "family": "grammar", "domain": "pronunciation",
            "category": "读音规则", "depends_on": []}


@pytest.fixture
def fixture_rules(monkeypatch):
    """Two word topics in stage 1 and 2, grammar in stage 1, no weaving or small-unit plans."""
    monkeypatch.setattr(catalog, "WORD_TOPICS", [
        (1, "places", "地点词", "地点", "駅 学校"), (2, "things", "东西", "东西", "本 犬 猫"),
        (2, "food", "饮食", "饮食", "水 茶 肉 魚 米")])
    monkeypatch.setattr(catalog, "SMALL_UNIT_PLANS", {})
    monkeypatch.setattr(catalog, "GRAMMAR_ROUTE_CONFIG", {"levels": {"入门": 1}})
    monkeypatch.setattr(catalog, "GRAMMAR_TOPICS", {})
    monkeypatch.setattr(catalog, "COHERENCE_GROUPS", ())
    monkeypatch.setattr(catalog, "_PATH", {})


def catalogue():
    return [rule("mora"), grammar("copula:plain"), grammar("copula:polite", ["grammar:copula:plain"]),
            grammar("topic:wa", ["grammar:copula:plain"], "话题"),
            *(word(form) for form in WORDS.split()), word("学生", ["grammar:topic:wa"])]


def learning_ids(path):
    return [entry["id"] for section in flatten_sections(path) for entry in section["entries"]
            if entry["kind"] == "learning"]


def given_sections(path):
    return [section for section in flatten_sections(path) if section.get(GIVEN_ORDER)]


# --- absent, empty, malformed: exactly today's route ---------------------------------------------

@pytest.mark.parametrize("value", [None, [], (), "word:駅", {"a": 1}, [None, 3], ["word:nothing"],
                                   [{"title": "空", "sections": []}], [{"title": "x"}, "word:駅"],
                                   [{"title": "x", "sections": [{"title": "y", "entries": ["word:nothing"]}]}]])
def test_an_absent_empty_or_unusable_order_builds_todays_route(fixture_rules, monkeypatch, value):
    items = catalogue()
    today = build_study_path([], items)
    if value is not None:
        monkeypatch.setitem(catalog._PATH, given.ORDER_KEY, value)
    assert build_study_path([], items) == today
    assert build_study_path([], items, main_route_order=[]) == today


# --- meaning: the listed items first, in order; the rest as today -------------------------------

def test_the_listed_items_open_the_route_in_order_and_the_rest_follows_as_today(fixture_rules):
    items = catalogue()
    order = ["word:水", "word:茶", "grammar:copula:plain", "word:駅", "word:学校", "word:本"]
    path = build_study_path([], items, main_route_order=order)
    ids = learning_ids(path)
    stage_zero = [entry["id"] for stage in path["stages"] if stage["id"] == catalog.STAGES[0][0]
                  for unit in stage["units"] for section in unit["sections"] for entry in section["entries"]]
    assert stage_zero == ["pronunciation:mora"]  # the sounds stage stays first
    assert ids[1:1 + len(order)] == order
    assert Counter(ids) == Counter(item["id"] for item in items)  # nothing lost or doubled
    today = build_study_path([], items)
    rest = [cid for cid in learning_ids(today) if cid not in order and cid != "pronunciation:mora"]
    assert ids[1 + len(order):] == rest


def test_a_flat_order_groups_words_and_starts_a_unit_at_each_grammar_lesson(fixture_rules):
    items = catalogue()
    order = ["word:水", "word:茶", "grammar:copula:plain", "word:駅", "word:学校", "word:本"]
    path = build_study_path([], items, main_route_order=order)
    sections = given_sections(path)
    assert [[entry["id"] for entry in section["entries"]] for section in sections] == [
        ["word:水", "word:茶"], ["grammar:copula:plain"], ["word:駅", "word:学校", "word:本"]]
    # One topic: today's topic title; words of several topics: the neutral title.
    assert [section["title"] for section in sections] == ["饮食", "判断", catalog.ORDER_WORDS_TITLE]
    assert [section["unit_title"] for section in sections] == [
        catalog.ORDER_OPENING_TITLE.format(1), "判断", "判断"]
    first = next(stage for stage in path["stages"] if stage["id"] == catalog.STAGES[1][0])
    assert first["title"] == catalog.STAGES[1][1]
    assert [len(unit["sections"]) for unit in first["units"][:2]] == [1, 2]
    assert [section["id"] for section in sections] == [
        section["id"] for unit in first["units"][:2] for section in unit["sections"]]
    for section in sections:
        assert section["stage_id"] == first["id"] and not section["optional"]
        assert section["component_section_ids"] == [section["id"]] and section["topics"] == [section["title"]]
    # The same order gives the same ids (they are digests of the content).
    assert [s["id"] for s in given_sections(build_study_path([], items, main_route_order=order))] == [
        s["id"] for s in sections]


def _records(count_words, count_rules, count_grammar=0):
    def record(cid, family, topic, domain=None, unit="单元"):
        return (1, "unit:" + topic, unit, topic, 0, {"id": cid, "kind": "learning", "content_id": cid, "title": cid,
                                                     "family": family, "domain": domain, "depends_on": []})
    rows = [record(f"word:{n}", "word", f"话题{n % 3}") for n in range(count_words)]
    rows += [record(f"pronunciation:{n}", "grammar", "读音", "pronunciation") for n in range(count_rules)]
    rows += [record(f"grammar:{n}", "grammar", "判断", unit="判断单元") for n in range(count_grammar)]
    return rows


def test_a_flat_order_keeps_todays_section_sizes_and_eight_sections_a_unit():
    records = _records(19, 6, 2)
    layout, trouble = given.flat_layout([r[5]["id"] for r in records], records, words_title="词汇",
                                        opening_title="开头 · 第 {} 组")
    assert not trouble
    assert [[len(ids) for _, ids in sections] for _, sections in layout] == [[8, 8, 3, 4, 2], [1], [1]]
    assert [title for title, _ in layout] == ["开头 · 第 1 组", "判断单元", "判断单元"]
    many = _records(70, 0)
    layout, _ = given.flat_layout([r[5]["id"] for r in many], many, words_title="词汇", opening_title="开头 · 第 {} 组")
    assert [[len(ids) for _, ids in sections] for _, sections in layout] == [[8] * 8, [6]]
    assert [title for title, _ in layout] == ["开头 · 第 1 组", "开头 · 第 2 组"]
    assert {title for _, sections in layout for title, _ in sections} == {"词汇"}


def test_dependencies_of_later_lessons_on_the_beginning_are_resolved_section_edges(fixture_rules):
    items = catalogue()
    path = build_study_path([], items, main_route_order=["grammar:copula:plain", "grammar:topic:wa"])
    sections = flatten_sections(path)
    owner = {entry["id"]: section for section in sections for entry in section["entries"]}
    student = owner["word:学生"]
    assert owner["grammar:topic:wa"]["id"] in student["depends_on"]
    assert not any(section.get("unresolved_dependencies") for section in sections)
    positions = {section["id"]: index for index, section in enumerate(sections)}
    for section in sections:
        assert all(positions[d] < positions[section["id"]] for d in section["depends_on"])


# --- prerequisites stay hard ---------------------------------------------------------------------

def test_a_prerequisite_listed_later_or_not_at_all_is_placed_first_and_reported(fixture_rules):
    items = catalogue()
    order = ["word:学生", "grammar:copula:polite", "word:駅", "grammar:copula:plain"]
    before = sum(event["count"] for event in diagnostic_events.error_events()
                 if event["operation"] == "main_route_order")
    path = build_study_path([], items, main_route_order=order)
    after = sum(event["count"] for event in diagnostic_events.error_events()
                if event["operation"] == "main_route_order")
    assert after == before + 1  # one diagnostic per build
    ids = learning_ids(path)
    assert ids[1:6] == ["grammar:copula:plain", "grammar:topic:wa", "word:学生", "grammar:copula:polite", "word:駅"]
    assert Counter(ids) == Counter(item["id"] for item in items)
    problems = given.order_problems(order, study_path_records([], items))
    assert problems["prerequisite_after"] == [
        ("entry 0", "word:学生", "grammar:topic:wa", None),
        ("entry 1", "grammar:copula:polite", "grammar:copula:plain", "entry 3")]
    assert given.problem_count(problems) == 2
    lines = given.describe_problems(problems)
    assert "entry 0: word:学生 comes before its prerequisite grammar:topic:wa, which is not listed" in lines
    assert "entry 1: grammar:copula:polite comes before its prerequisite grammar:copula:plain, which is listed later, at entry 3" in lines


def test_a_valid_order_records_no_diagnostic(fixture_rules):
    items = catalogue()
    before = diagnostic_events.error_events()
    build_study_path([], items, main_route_order=["grammar:copula:plain", "word:駅"])
    assert diagnostic_events.error_events() == before


def test_the_check_reports_unknown_repeated_and_malformed_entries_and_notes_the_sounds_stage(fixture_rules):
    items = catalogue()
    records = study_path_records([], items)
    order = ["word:駅", "new:jmdict:1:かぎ", "word:駅", 5, "pronunciation:mora", "expr:お願いします"]
    problems = given.order_problems(order, records)
    assert problems["unknown"] == [("entry 1", "new:jmdict:1:かぎ"), ("entry 5", "expr:お願いします")]
    assert problems["repeated"] == [("entry 2", "word:駅", "entry 0")]
    assert problems["malformed"] == ["entry 3"]
    assert problems["kept_in_place"] == [("entry 4", "pronunciation:mora")]
    assert given.problem_count(problems) == 4
    assert given.order_problems("word:駅", records)["malformed"] == [None]
    assert given.problem_count(given.order_problems(None, records)) == 0
    # The app skips them: the route still holds every lesson once, the rule in the sounds stage.
    path = build_study_path([], items, main_route_order=order)
    ids = learning_ids(path)
    assert ids[:2] == ["pronunciation:mora", "word:駅"]
    assert Counter(ids) == Counter(item["id"] for item in items)


def test_the_checking_tool_reads_tsv_json_and_namespace_orders_and_fails_on_errors(fixture_rules, tmp_path):
    from check_main_route_order import check, read_order
    items = catalogue()
    tsv = tmp_path / "order.tsv"
    tsv.write_bytes("pos\tkind\tid\n0\tword\tword:学生\n1\tword\tword:駅\n".encode("utf-8"))
    listed = tmp_path / "order.json"
    listed.write_text(json.dumps(["grammar:copula:plain", "word:駅"]), encoding="utf-8")
    namespace = tmp_path / "study_path_catalog.json"
    namespace.write_text(json.dumps({"format": "x", "version": 1, "name": "study_path_catalog",
                                     "payload": {given.ORDER_KEY: ["word:駅"]}}), encoding="utf-8")
    assert read_order(tsv) == ["word:学生", "word:駅"]
    assert read_order(listed) == ["grammar:copula:plain", "word:駅"]
    assert read_order(namespace_file=namespace) == ["word:駅"]
    errors, lines = check(read_order(tsv), items)
    assert errors == 1 and lines[1] == ("entry 0: word:学生 comes before its prerequisite grammar:topic:wa, "
                                        "which is not listed")
    assert check(read_order(listed), items)[0] == 0
    assert check(None, items) == (0, ["MAIN_ROUTE_ORDER: absent (the route is built as before)"])
    # The structured form, from a .json order and from a namespace document.
    units = tmp_path / "units.json"
    units.write_text(json.dumps(STRUCTURED, ensure_ascii=False), encoding="utf-8")
    namespace.write_text(json.dumps({"format": "x", "version": 1, "name": "study_path_catalog",
                                     "payload": {given.ORDER_KEY: STRUCTURED}}, ensure_ascii=False), encoding="utf-8")
    assert read_order(units) == STRUCTURED == read_order(namespace_file=namespace)
    errors, lines = check(read_order(units), items)
    assert errors == 0 and lines[0].startswith("MAIN_ROUTE_ORDER (units): 7 lessons listed, 0 errors")
    broken = [unit_of("", ("太多", [f"word:{w}" for w in "駅 学校 本 犬 猫 水 茶 肉 魚".split()]))]
    errors, lines = check(broken, items)
    assert errors == 2 and lines[1:] == ["unit 1: empty or missing title",
                                         "unit 1 section 1: 9 word entries, more than 8"]


def test_the_order_is_core_content_only():
    assert builtin_policy("study_path_catalog", given.ORDER_KEY) == "singleton"


# --- route_mix leaves the beginning alone ------------------------------------------------------

def mix_case():
    items = catalogue()
    order = ["word:水", "grammar:copula:plain", "word:茶"]
    path = build_study_path([], items, main_route_order=order)
    return items, order, path


def test_route_mix_keeps_the_beginning_first_in_order_and_mixes_the_rest(fixture_rules):
    _, order, path = mix_case()
    detached = snapshot(path)
    mixable = mixable_stage_ids(detached)
    needed = needed_items(detached, mixable, {})
    assert not set(order) & set(needed)  # the beginning is never placed
    # Word sections that need the grammar after them: placed between them by the mix.
    table = {cid: {"use": [(f"{n:064x}", ["grammar:copula:polite"])]} for n, cid in enumerate(needed)
             if cid.startswith("word:")}
    plan = plan_mix(detached, Readiness(table), mixable=mixable, library_of={}, key={"test": 1},
                    params=MixParams())
    mixed = apply_plan(path, plan, mixable=mixable, providers={})
    assert mixed is not None
    lead = given_sections(path)
    first = next(stage for stage in mixed["stages"] if stage["id"] == catalog.STAGES[1][0])
    flat = [section for unit in first["units"] for section in unit["sections"]]
    assert [(s["id"], s["unit_id"]) for s in flat[:len(lead)]] == [(s["id"], s["unit_id"]) for s in lead]
    assert [e["id"] for s in flatten_sections(mixed) for e in s["entries"]][1:4] == order
    assert plan.summary["given"] == len(lead)
    assert Counter(e["id"] for s in flatten_sections(mixed) for e in s["entries"]) == Counter(
        e["id"] for s in flatten_sections(path) for e in s["entries"])


def test_a_plan_that_moves_the_beginning_does_not_apply(fixture_rules):
    _, _, path = mix_case()
    detached = snapshot(path)
    mixable = mixable_stage_ids(detached)
    plan = plan_mix(detached, Readiness({}), mixable=mixable, library_of={}, key={"test": 1})
    assert apply_plan(path, plan, mixable=mixable, providers={}) is not None
    stage_id, rows = plan.stages[0]
    lead = given_sections(path)
    assert rows[:len(lead)] == tuple((s["id"], s["unit_id"]) for s in lead)
    # The rest of the stage first, then the beginning: units stay contiguous, prerequisites hold,
    # but the beginning no longer opens the stage.
    moved = (*rows[len(lead):], *rows[:len(lead)])
    bad = MixPlan(plan.key, ((stage_id, moved), *plan.stages[1:]), plan.summary)
    assert apply_plan(path, bad, mixable=mixable, providers={}) is None


def test_the_beginning_is_not_cut_by_library_and_marks_the_digest(fixture_rules):
    items = catalogue()
    path = build_study_path([], items, main_route_order=[f"word:{form}" for form in WORDS.split()])
    library_of = lambda cid: "library.test" if cid.startswith("word:") else None  # noqa: E731
    split = split_library_sections(path, library_of)
    assert [s["id"] for s in given_sections(split)] == [s["id"] for s in given_sections(path)]
    plain = deepcopy(path)
    for section in flatten_sections(plain):
        section.pop(GIVEN_ORDER, None)
    assert tree_digest(plain) != tree_digest(path)
    assert snapshot(path)["stages"][1]["units"][0]["sections"][0][GIVEN_ORDER] is True


# --- the structured form -----------------------------------------------------------------------

def unit_of(title, *sections):
    return {"title": title, "sections": [{"title": name, "entries": list(ids)} for name, ids in sections]}


STRUCTURED = [unit_of("吃喝", ("早饭", ["word:水", "word:茶"]), ("是什么", ["grammar:copula:plain"])),
              unit_of("出门", ("地方", ["word:駅", "word:学校"]), ("东西", ["word:本", "word:犬"]))]


def diagnostics():
    return sum(event["count"] for event in diagnostic_events.error_events() if event["operation"] == "main_route_order")


def test_a_structured_order_is_taken_as_given_with_its_titles(fixture_rules, monkeypatch):
    items = catalogue()
    before = diagnostics()
    monkeypatch.setitem(catalog._PATH, given.ORDER_KEY, deepcopy(STRUCTURED))
    path = build_study_path([], items)
    assert diagnostics() == before
    first = next(stage for stage in path["stages"] if stage["id"] == catalog.STAGES[1][0])
    lead = [unit for unit in first["units"] if all(s.get(GIVEN_ORDER) for s in unit["sections"])]
    assert [(unit["title"], [(s["title"], [e["id"] for e in s["entries"]]) for s in unit["sections"]])
            for unit in lead] == [(unit["title"], [(s["title"], s["entries"]) for s in unit["sections"]])
                                  for unit in STRUCTURED]
    order = given.order_ids(STRUCTURED)
    ids = learning_ids(path)
    assert ids[1:1 + len(order)] == list(order)
    assert Counter(ids) == Counter(item["id"] for item in items)
    assert given.problem_count(given.order_problems(STRUCTURED, study_path_records([], items))) == 0


def test_each_structural_problem_is_reported(fixture_rules):
    items = catalogue()
    records = study_path_records([], items)
    value = [unit_of("",("一", ["word:水"])),                                    # unit 1: empty title
             {"title": "二", "sections": []},                                       # unit 2: empty
             unit_of("三", ("", ["word:茶"]), ("空", [])),                          # empty title, empty section
             unit_of("四", ("太多", [f"word:{w}" for w in "駅 学校 本 犬 猫 肉 魚 米 学生".split()])),  # 9 words
             unit_of("五", ("语法", ["grammar:copula:plain", "grammar:copula:polite", "grammar:topic:wa",
                                     "pronunciation:mora", "word:犬"])),            # mixed family
             {"title": "六", "sections": ["word:犬", {"title": "x", "entries": "word:犬"},
                                         {"title": "y", "entries": [7]}]},           # unreadable
             {"title": "七"}]
    problems = given.order_problems(value, records)
    assert problems["empty_title"] == ["unit 1", "unit 3 section 1"]
    assert problems["empty"] == ["unit 2", "unit 3 section 2"]
    assert problems["oversize"] == [("unit 4 section 1", "word", 9, 8)]
    assert problems["mixed_family"] == [("unit 5 section 1", ("grammar", "word"))]
    assert problems["malformed"] == ["unit 6 section 1", "unit 6 section 2", "unit 6 section 3 entry 1", "unit 7"]
    four = given.order_problems([unit_of("x", ("y", ["grammar:copula:plain", "grammar:copula:polite",
                                                       "grammar:topic:wa", "pronunciation:mora",
                                                       "grammar:copula:plain"]))], records)
    assert four["oversize"] == [("unit 1 section 1", "grammar", 5, 4)]  # reading rules count as grammar
    assert four["repeated"] == [("unit 1 section 1 entry 5", "grammar:copula:plain", "unit 1 section 1 entry 1")]
    lines = given.describe_problems(problems)
    assert "unit 4 section 1: 9 word entries, more than 8" in lines
    assert "unit 5 section 1: entries of different families (grammar, word)" in lines
    assert "unit 1: empty or missing title" in lines and "unit 2: empty" in lines


def test_the_app_repairs_a_structured_order_and_keeps_every_lesson(fixture_rules):
    items = catalogue()
    words = [f"word:{form}" for form in WORDS.split()]
    value = [unit_of("混合", ("太多", words[:9] + ["grammar:copula:polite"])),
             {"title": "", "sections": [{"title": "", "entries": ["word:学生"]}, {"title": "空", "entries": []}]}]
    before = diagnostics()
    path = build_study_path([], items, main_route_order=value)
    assert diagnostics() == before + 1  # one diagnostic for all the repairs
    lead = [s for s in flatten_sections(path) if s.get(GIVEN_ORDER)]
    # A lifted prerequisite has a section of its own just before the section that needs it, in the
    # same unit, titled with its topic; the mixed, oversized section is split by family and size;
    # the missing titles are replaced (unit: its first grammar lesson's unit; section: its topic);
    # the empty section is left out.
    assert [(s["unit_title"], s["title"], [e["id"] for e in s["entries"]]) for s in lead] == [
        ("混合", "判断", ["grammar:copula:plain"]),
        ("混合", "太多", words[:8]),
        ("混合", "太多（2）", words[8:9]),
        ("混合", "太多（3）", ["grammar:copula:polite"]),
        ("话题", "话题", ["grammar:topic:wa"]),
        ("话题", "词义与读法", ["word:学生"])]
    ids = learning_ids(path)
    assert Counter(ids) == Counter(item["id"] for item in items)
    sections = flatten_sections(path)
    positions = {section["id"]: index for index, section in enumerate(sections)}
    assert all(positions[d] < positions[s["id"]] for s in sections for d in s["depends_on"])
