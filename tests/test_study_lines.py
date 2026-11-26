"""The four rules of the two-line design's first part (study_lines; pure, made-up routes).

The line of a lesson and of a section, each line's next section (optional
sections and finished lines left out), the suggestion (a: the only line without
unlearned content, b: route order, c: a grammar section made ready by a word
section; 深度优先: the route's next section) and the readiness verdict (the
floor min(templates, 3), the missing lessons in route order, ticked lessons
known), on hand-made overviews and readiness tables.
"""
import ast
from pathlib import Path

import pytest

from kanamemory import study_lines as lines
from kanamemory.route_mix.readiness import Readiness, choice_key


def entry(eid, family="word", domain=None, state="new", kind="learning", title=None):
    return {"id": eid, "content_id": eid, "kind": kind, "family": family, "domain": domain, "state": state,
            "title": title or eid}


def section(sid, *entries, optional=False, title=None):
    new = sum(item["state"] == "new" for item in entries)
    return {"id": sid, "title": title or sid, "entries": list(entries), "new_count": new, "optional": optional}


def overview(*sections):
    return {"stages": [{"id": "stage:1", "units": [{"id": "unit:1", "sections": list(sections)}]}]}


def grammar(eid, state="new"):
    return entry(eid, "grammar", None, state)


# ------------------------------------------------------------------ L1: the line of a lesson and of a section

@pytest.mark.parametrize("family, domain, line", [
    ("word", None, lines.WORD), ("grammar", "pronunciation", lines.WORD), ("grammar", "expression", lines.WORD),
    ("grammar", None, lines.GRAMMAR), ("grammar", "usage", lines.GRAMMAR), (None, None, None), ("kana", None, None)])
def test_words_rules_and_expressions_are_the_word_line_other_grammar_the_grammar_line(family, domain, line):
    assert lines.line_of(family, domain) == line


def test_a_section_is_grammar_only_when_all_its_lessons_are_of_the_grammar_line():
    assert lines.section_line(section("g", grammar("g1"), grammar("g2"))) == lines.GRAMMAR
    assert lines.section_line(section("m", grammar("g1"), entry("w1"))) == lines.WORD
    assert lines.section_line(section("e", entry("x1", "grammar", "expression"))) == lines.WORD
    assert lines.section_line(section("k", entry("あ", kind="kana", family=None))) is None
    assert lines.section_line(section("r", entry("ref", kind="reference", family=None))) is None
    # A kana entry beside lessons does not change the lessons' line.
    assert lines.section_line(section("gk", grammar("g1"), entry("あ", kind="kana", family=None))) == lines.GRAMMAR


# ------------------------------------------------------------------ L2: each line's next section

MIXED = overview(section("w1", entry("a", state="completed")), section("g1", grammar("p")),
                 section("opt", entry("o"), optional=True), section("w2", entry("b")),
                 section("g2", grammar("q")), section("w3", entry("c")))


def test_each_line_has_its_own_next_sections_in_route_order_and_optional_ones_are_skipped():
    assert [s["id"] for s in lines.next_sections(MIXED, 5)] == ["g1", "w2", "g2", "w3"]
    assert [s["id"] for s in lines.next_sections(MIXED, 5, lines.WORD)] == ["w2", "w3"]
    assert [s["id"] for s in lines.next_sections(MIXED, 1, lines.GRAMMAR)] == ["g1"]
    assert [s["id"] for s in lines.next_sections(MIXED, 2, lines.GRAMMAR)] == ["g1", "g2"]


def test_together_the_two_lines_are_the_route_order():
    """AC4: the two lines' next sections, merged by route position, are the route's next sections."""
    route = lines.next_sections(MIXED, 99)
    merged = sorted(lines.next_sections(MIXED, 99, lines.WORD) + lines.next_sections(MIXED, 99, lines.GRAMMAR),
                    key=route.index)
    assert merged == route


def test_a_finished_line_shows_nothing():
    done = overview(section("w1", entry("a", state="added")), section("g1", grammar("p")))
    result = lines.route_lines(done)
    assert [item.line for item in result.lines] == [lines.GRAMMAR]
    assert result.get(lines.WORD) is None


def test_while_the_route_asks_for_kana_first_the_lines_wait():
    kana = overview(section("k", entry("あ", kind="kana", family=None)), section("w", entry("a")),
                    section("g", grammar("p")))
    result = lines.route_lines(kana, breadth=True)
    assert result.lines == ()
    assert result.suggestion == lines.Suggestion(None, "k", "k")


# ------------------------------------------------------------------ L3: what to add first

def test_suggestion_a_the_only_line_without_unlearned_content():
    route = overview(section("w1", entry("a", state="added")), section("w2", entry("b")),
                     section("g1", grammar("p")))
    result = lines.route_lines(route, breadth=True)
    assert result.suggestion.line == lines.GRAMMAR and result.suggestion.section_id == "g1"
    assert result.suggestion.reason == ""


def test_suggestion_b_both_lines_with_unlearned_content_take_route_order():
    route = overview(section("w1", entry("a", state="started")), section("g0", grammar("o", state="added")),
                     section("g1", grammar("p")), section("w2", entry("b")))
    assert lines.route_lines(route, breadth=True).suggestion.section_id == "g1"
    nothing = overview(section("w2", entry("b")), section("g1", grammar("p")))
    assert lines.route_lines(nothing, breadth=True).suggestion.section_id == "w2"


def test_thorough_suggests_the_route_next_section_as_today():
    route = overview(section("w1", entry("a", state="added")), section("w2", entry("b")),
                     section("g1", grammar("p")))
    suggestion = lines.route_lines(route, breadth=False).suggestion
    assert (suggestion.line, suggestion.section_id) == (lines.WORD, "w2")


def test_no_suggestion_when_nothing_is_left():
    route = overview(section("w1", entry("a", state="completed")))
    result = lines.route_lines(route, breadth=True)
    assert result.lines == () and result.suggestion is None


# ------------------------------------------------------------------ L4: is a section ready

GATED = overview(section("w1", entry("a", state="completed")),
                 section("g1", grammar("p")),
                 section("w2", entry("b", title="猫"), entry("c", title="犬"), title="动物"),
                 section("w3", entry("d", title="鸟"), title="更多动物"))


def table(**rows):
    return Readiness(rows)


def test_counts_sum_over_the_lessons_and_the_floor_is_min_of_templates_and_three():
    readiness = table(p={"understanding": [("t1", ["a"]), ("t2", []), ("t3", ["a"])],
                         "application": [("t4", ["a"]), ("t5", ["b"])]})
    result = lines.route_lines(GATED, readiness=readiness, breadth=True)
    verdict = result.get(lines.GRAMMAR).verdict
    # understanding: 3 of 3; application: 1 of 2 below min(2, 3) = 2: missing b, in its section.
    assert verdict.counts == (("understanding", 3), ("application", 1))
    assert not verdict.ready
    assert verdict.missing == (lines.Missing("b", "猫", "w2", "动物"),)
    assert lines.counts_text(verdict.counts) == "现在能出：理解 3／应用 1"


def test_a_lesson_with_few_templates_needs_only_those():
    readiness = table(p={"understanding": [("t1", ["a"])], "application": [("t2", []), ("t3", ["a"])]})
    verdict = lines.route_lines(GATED, readiness=readiness).get(lines.GRAMMAR).verdict
    assert verdict.ready and verdict.missing == ()


def test_ticked_lessons_count_as_known():
    readiness = table(p={"application": [("t1", ["b"]), ("t2", ["c"]), ("t3", ["d"])]})
    before = lines.route_lines(GATED, readiness=readiness).get(lines.GRAMMAR).verdict
    assert [item.entry_id for item in before.missing] == ["b", "c", "d"]
    after = lines.route_lines(GATED, readiness=readiness, ticked=("b", "c", "d")).get(lines.GRAMMAR).verdict
    assert after.ready and after.counts == (("application", 3),)


def test_the_cheapest_templates_are_named_first_and_unreachable_lessons_are_counted():
    readiness = table(p={"application": [("t1", ["b", "c", "d"]), ("t2", ["d"]), ("t3", ["x"])]})
    verdict = lines.route_lines(GATED, readiness=readiness).get(lines.GRAMMAR).verdict
    # Floor 3 with nothing met: every template is needed; x is no lesson of this route.
    assert [item.entry_id for item in verdict.missing] == ["b", "c", "d"]
    assert verdict.unreachable == 1
    one = table(p={"application": [("t1", ["b", "c"]), ("t2", ["d"]), ("t3", [])]})
    # Floor 3, one met: the two cheapest of the others; t2 (one lesson) before t1 (two).
    assert [m.entry_id for m in lines.route_lines(GATED, readiness=one).get(lines.GRAMMAR).verdict.missing] == \
        ["b", "c", "d"]


def test_choice_keys_are_not_shown():
    readiness = table(p={"application": [("t1", [])], choice_key("application"): [("t1", [])]})
    assert lines.route_lines(GATED, readiness=readiness).get(lines.GRAMMAR).verdict.counts == (("application", 1),)


def test_without_the_lessons_in_the_table_there_is_no_verdict():
    assert lines.route_lines(GATED).get(lines.GRAMMAR).verdict is None
    other = table(zzz={"understanding": [("t1", [])]})
    assert lines.route_lines(GATED, readiness=other).get(lines.GRAMMAR).verdict is None


def test_suggestion_c_a_grammar_section_made_ready_by_a_word_section():
    route = overview(section("w1", entry("a", state="completed")), section("g1", grammar("p"), title="は"),
                     section("w2", entry("b", title="猫"), title="动物"))
    readiness = table(p={"application": [("t1", ["b"])]}, b={})
    result = lines.route_lines(route, readiness=readiness, breadth=True)
    assert result.suggestion == lines.Suggestion(
        lines.WORD, "w2", "动物", "「は」的题还要用到 1 项没加入的内容，先加「动物」。")
    # Ticking the word makes the grammar section ready: the suggestion is the grammar section again.
    ready = lines.route_lines(route, readiness=readiness, ticked=("b",), breadth=True)
    assert ready.suggestion == lines.Suggestion(lines.GRAMMAR, "g1", "は")
    # Under 深度优先 the route's next section stays the suggestion.
    assert lines.route_lines(route, readiness=readiness, breadth=False).suggestion.section_id == "g1"


def test_readiness_ids_are_the_lessons_of_each_lines_next_section():
    assert lines.readiness_ids(GATED) == ("b", "c", "p")


def test_the_module_imports_no_toga_store_or_app():
    tree = ast.parse(Path(lines.__file__).read_text(encoding="utf-8"))
    names = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    names |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert names <= {"__future__", "collections.abc", "dataclasses", "route_mix.readiness"}
