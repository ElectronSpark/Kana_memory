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
    assert errors == 2 and lines[1:3] == ["unit 1: empty or missing title",
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


# --- the checker: question floor, runs, lifted chains, library lessons --------------------------

def sha(n):
    return f"{n:064x}"


# 茶 has three meaning questions that need 水 (one also 肉) and one switched-on one that needs 水:
# without an order 水 comes first, so it has 2 and 1; listed before 水 it has none.
TEA = Readiness({"word:茶": {"meaning": [(sha(1), ["word:水"]), (sha(2), ["word:水"]), (sha(3), ["word:水", "word:肉"])],
                             "on:meaning": [(sha(1), ["word:水"])]},
                 "word:駅": {"meaning": [(sha(4), [])]}})


def test_the_checker_holds_every_given_lesson_to_the_question_floor(fixture_rules, tmp_path):
    from check_main_route_order import check, floor_rows, given_routes
    items = catalogue()
    report = tmp_path / "floor.tsv"
    errors, lines = check(["word:茶", "word:駅"], items, readiness=TEA, library_of=lambda cid: None,
                          params=MixParams(), report=report)
    assert errors == 2
    assert ("word:茶: 0 gated meaning questions at its place, 2 without the order, needs 2; "
            "blocked most by word:水 (3), word:肉 (1)") in lines
    assert ("word:茶: 0 gated on:meaning questions at its place, 1 without the order, needs 1; "
            "blocked most by word:水 (1)") in lines
    rows = report.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "lesson\tkey\twithout_order\twith_order\trequired\tshort\tblocked_by"
    assert rows[1:] == ["word:茶\tmeaning\t2\t0\t2\tyes\tword:水:3 word:肉:1",
                        "word:茶\ton:meaning\t1\t0\t1\tyes\tword:水:1",
                        "word:駅\tmeaning\t1\t1\t1\t\t"]
    # Bringing 水 forward too meets the floor: the order passes.
    assert check(["word:水", "word:茶", "word:駅"], items, readiness=TEA, library_of=lambda cid: None,
                 params=MixParams())[0] == 0
    with_tree, without_tree = given_routes(["word:水", "word:茶"], items)
    rows = floor_rows(with_tree, without_tree, TEA, MixParams(floor=1))
    assert [(row.lesson, row.key, row.without, row.with_order, row.required, row.short) for row in rows] == [
        ("word:茶", "meaning", 2, 2, 1, False), ("word:茶", "on:meaning", 1, 1, 1, False)]


def test_the_checker_reports_long_runs_and_strict_makes_them_fail(fixture_rules):
    from check_main_route_order import check
    items = [*catalogue(), *(grammar(f"extra{n}") for n in range(5))]
    order = ["word:駅", "word:学校", "word:本", "word:犬", "word:猫",
             *(f"grammar:extra{n}" for n in range(5))]
    params = MixParams(word_target=2, nonword_target=1)
    errors, lines = check(order, items, readiness=Readiness({}), library_of=lambda cid: None, params=params)
    assert errors == 0
    assert ("longest runs in the beginning: 5 word lessons (1 sections), 5 other lessons (5 sections)") in lines
    assert "warning: a run of 5 word lessons, more than 2 x 2 (the mix never breaks up the beginning)" in lines
    assert "warning: a run of 5 other lessons, more than 4 x 1 (the mix never breaks up the beginning)" in lines
    assert check(order, items, readiness=Readiness({}), library_of=lambda cid: None, params=params,
                 strict=True)[0] == 2
    assert check(order, items, readiness=Readiness({}), library_of=lambda cid: None, strict=True)[0] == 0


def test_strict_measures_the_longest_word_run_of_the_mixed_route_as_the_core_content_test(fixture_rules, monkeypatch):
    import check_main_route_order as tool
    from kanamemory.route_mix.placement import runs, section_of
    items, order = catalogue(), ["word:駅"]
    with_tree, _ = tool.given_routes(order, items)
    mixed, mixable = tool.mix_route(with_tree, items, MixParams(), lambda cid: None, Readiness({}))
    assert mixed is not None
    measured = tool.mixed_word_runs(mixed, mixable)
    # One definition: the length is placement.runs' over the stage's sections, with the run's first and last lesson.
    for run in measured:
        sections = [section_of(s, {}) for s in tool._stage_sections(mixed, run.stage_id)]
        assert run.length == runs(sections)[0]
    longest = max(measured, key=lambda run: run.length)
    assert longest.length > 1  # a real run, not one word
    flat = [e["id"] for s in tool._stage_sections(mixed, longest.stage_id) for e in s["entries"]]
    stretch = flat[flat.index(longest.first):flat.index(longest.last) + 1]
    assert len(stretch) == longest.length and all(cid.startswith("word:") for cid in stretch)
    options = dict(readiness=Readiness({}), library_of=lambda cid: None, params=MixParams())
    errors, lines = tool.check(order, items, strict=True, **options)
    assert errors == 0 and (f"longest run of word lessons on the mixed route: {longest.length} (stage "
                            f"{longest.stage_id}, {longest.first} … {longest.last}; limit 100)") in lines
    # Not measured without --strict (it computes the plan).
    assert not any("mixed route" in line for line in tool.check(order, items, **options)[1])
    # At the limit the order fails, naming the run.
    monkeypatch.setattr(tool, "WORD_RUN_LIMIT", longest.length)
    errors, lines = tool.check(order, items, strict=True, **options)
    assert errors == 1
    assert (f"error: stage {longest.stage_id} of the mixed route has a run of {longest.length} word lessons, from "
            f"{longest.first} to {longest.last} (must stay under {longest.length})") in lines


def test_a_flat_order_never_merges_grammar_points(fixture_rules):
    items = [*catalogue(), *(grammar(f"extra{n}") for n in range(3))]
    path = build_study_path([], items, main_route_order=[f"grammar:extra{n}" for n in range(3)])
    assert [[e["id"] for e in s["entries"]] for s in given_sections(path)] == [
        ["grammar:extra0"], ["grammar:extra1"], ["grammar:extra2"]]
    assert len({s["unit_id"] for s in given_sections(path)}) == 3


def test_every_unlisted_lesson_of_a_prerequisite_chain_is_reported_once(fixture_rules):
    records = study_path_records([], catalogue())
    problems = given.order_problems(["word:学生", "grammar:topic:wa"], records)
    assert problems["prerequisite_after"] == [("entry 0", "word:学生", "grammar:topic:wa", "entry 1"),
                                              ("entry 1", "grammar:topic:wa", "grammar:copula:plain", None)]
    assert problems["lifted"] == [("grammar:copula:plain", "grammar:topic:wa")]
    # 学生 needs 话题 は, which needs copula:plain: the error names the first missing link, the notes
    # every lesson the route lifts, the second link included.
    problems = given.order_problems(["word:学生"], records)
    assert problems["prerequisite_after"] == [("entry 0", "word:学生", "grammar:topic:wa", None)]
    assert problems["lifted"] == [("grammar:topic:wa", "word:学生"), ("grammar:copula:plain", "grammar:topic:wa")]
    assert given.problem_count(problems) == 1  # the notes do not count
    problems = given.order_problems(["word:学生", "grammar:copula:polite"], records)
    assert problems["lifted"] == [("grammar:topic:wa", "word:学生"), ("grammar:copula:plain", "grammar:topic:wa")]
    assert ("note: grammar:copula:plain is not listed and will be placed in the beginning, before "
            "grammar:topic:wa, which needs it") in given.describe_problems(problems)
    # What the route lifts is exactly what is reported.
    path = build_study_path([], catalogue(), main_route_order=["word:学生", "grammar:copula:polite"])
    assert [e["id"] for s in given_sections(path) for e in s["entries"]] == [
        "grammar:copula:plain", "grammar:topic:wa", "word:学生", "grammar:copula:polite"]


def test_a_lesson_of_an_extension_library_in_the_order_is_an_error(fixture_rules):
    from check_main_route_order import check
    records = study_path_records([], catalogue())
    library_of = lambda cid: "library.test" if cid == "word:犬" else None  # noqa: E731
    problems = given.order_problems(["word:駅", "word:犬"], records, library_of=library_of)
    assert problems["library"] == [("entry 1", "word:犬", "library.test")]
    assert given.problem_count(problems) == 1
    errors, lines = check(["word:駅", "word:犬"], catalogue(), readiness=Readiness({}), library_of=library_of)
    assert errors == 1
    assert "entry 1: word:犬 is provided by the extension library library.test, not by the core content" in lines


def test_one_bad_order_shows_the_floor_runs_chain_and_library_findings(fixture_rules):
    from check_main_route_order import check
    order = ["word:茶", "word:学生", "word:駅", "word:学校", "word:本", "word:犬", "word:猫"]
    errors, lines = check(order, catalogue(), readiness=TEA, params=MixParams(word_target=2),
                          library_of=lambda cid: "library.test" if cid == "word:犬" else None)
    text = "\n".join(lines)
    assert "word:犬 is provided by the extension library library.test" in text           # 7
    assert "note: grammar:copula:plain is not listed" in text                              # 6
    assert "word:茶: 0 gated meaning questions at its place" in text                       # 1
    assert "warning: a run of 6 word lessons, more than 2 x 2" in text                     # 2
    assert errors == 4  # library, the missing prerequisite, two floor rows


# --- run-time diagnostics: one per build whenever the check finds an error -----------------------

@pytest.mark.parametrize("value", [["word:駅", "word:nothing"], ["word:駅", "word:学校", "word:駅"],
                                   [{"title": "x", "sections": [{"title": "y", "entries": ["word:駅"]}]}, "word:駅"],
                                   [{}], "word:駅", [None, 3], [{"title": "空", "sections": []}]])
def test_every_order_with_an_error_records_one_diagnostic_and_builds_as_before(fixture_rules, monkeypatch, value):
    items = catalogue()
    assert given.problem_count(given.order_problems(value, study_path_records([], items))) > 0
    monkeypatch.setitem(catalog._PATH, given.ORDER_KEY, value)
    before = diagnostics()
    path = build_study_path([], items)
    assert diagnostics() == before + 1
    # The route is the one these orders built before: the usable ids first, or today's route.
    usable = [cid for cid in dict.fromkeys(given.order_ids(value)) if cid in {item["id"] for item in items}]
    assert learning_ids(path)[1:1 + len(usable)] == usable
    if not usable:
        monkeypatch.delitem(catalog._PATH, given.ORDER_KEY)
        assert path == build_study_path([], items)


def test_a_library_lesson_in_the_stored_order_records_a_diagnostic_when_providers_are_known(fixture_rules):
    items = catalogue()
    before = diagnostics()
    build_study_path([], items, main_route_order=["word:駅"], library_of=lambda cid: None)
    assert diagnostics() == before
    build_study_path([], items, main_route_order=["word:駅"], library_of=lambda cid: "lib" if cid == "word:駅" else None)
    assert diagnostics() == before + 1


# --- the rest of the route: ids that would change meaning are renewed ----------------------------

def _sets(path):
    sections = {s["id"]: frozenset(e["id"] for e in s["entries"]) for s in flatten_sections(path)}
    units = {u["id"]: frozenset(e["id"] for s in u["sections"] for e in s["entries"])
             for stage in path["stages"] for u in stage["units"]}
    return sections, units


def test_a_section_or_unit_of_the_rest_with_other_lessons_gets_a_new_id(fixture_rules):
    items = catalogue()
    today_sections, today_units = _sets(build_study_path([], items))
    path = build_study_path([], items, main_route_order=["word:水", "word:茶"])
    sections, units = _sets(path)
    rest = {sid: ids for sid, ids in sections.items()
            if sid not in {s["id"] for s in given_sections(path)}}
    for sid, ids in rest.items():
        assert today_sections.get(sid, ids) == ids  # an id that stays names the same lessons
    for uid, ids in units.items():
        assert today_units.get(uid, ids) == ids or all(s.get(GIVEN_ORDER) for s in flatten_sections(path)
                                                       if s["unit_id"] == uid)
    food = frozenset({"word:肉", "word:魚", "word:米"})
    renewed = next(sid for sid, ids in rest.items() if ids == food)
    assert renewed not in today_sections
    assert renewed == "section:" + catalog._digest("main-rest|" + catalog.STAGES[2][0] + "|word:肉|word:魚|word:米")
    assert sum(1 for sid in rest if sid in today_sections) == len(rest) - 1  # only that one changed
    # Every reference follows: units, depends_on.
    known = set(sections)
    for stage in path["stages"]:
        for unit in stage["units"]:
            for section in unit["sections"]:
                assert section["unit_id"] == unit["id"]
                assert set(section["depends_on"]) <= known
    # The same order gives the same ids.
    assert _sets(build_study_path([], items, main_route_order=["word:水", "word:茶"])) == (sections, units)


def _tree(*units):
    return {"stages": [{"id": "stage:x", "units": [
        {"id": uid, "sections": [{"id": sid, "unit_id": uid, "depends_on": list(deps),
                                  "entries": [{"id": cid} for cid in ids]} for sid, ids, deps in sections]}
        for uid, sections in units]}]}


def test_renewing_follows_a_merge_of_other_neighbours():
    # Without the order: a, then b+c; with b listed, the rest merges a with c under a's id.
    reference = _tree(("unit:u", [("section:a", ["a"], []), ("section:bc", ["b", "c"], ["section:a"])]),
                      ("unit:v", [("section:d", ["d"], ["section:a"])]))
    path = _tree(("unit:u", [("section:a", ["a", "c"], [])]), ("unit:v", [("section:d", ["d"], ["section:a"])]))
    given.renew_rest_ids(path, reference)
    new_section = "section:" + given._digest("main-rest|stage:x|a|c")
    new_unit = "unit:" + given._digest("main-rest|stage:x|a|c")
    assert path == _tree((new_unit, [(new_section, ["a", "c"], [])]),
                         ("unit:v", [("section:d", ["d"], [new_section])]))
    # The same lessons (or an id the reference does not have) keep their ids.
    same = _tree(("unit:u", [("section:a", ["a"], []), ("section:new", ["c"], [])]))
    assert given.renew_rest_ids(deepcopy(same), _tree(("unit:u", [("section:a", ["a"], []),
                                                                  ("section:bc", ["c"], [])]))) == same
