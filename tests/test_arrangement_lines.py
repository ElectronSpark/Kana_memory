"""The time share between the word line and the grammar line, measured (design 12 LS1-LS3, LS8): pure rules.

Synthetic events and overviews only. Covers line_usage against the units' walk
(clock.window_usage with the same window) over random histories, the window's
edge and its default length, kana-lane events, an empty history, period_usage
by study day, behind under each tilt (equality, zero usage, a zero weight), and
the suggestion's rule (b) following ``behind`` (rules (a) and (c) and 深度优先
unchanged).
"""
from datetime import datetime, timedelta, timezone
import random

import pytest

from kanamemory import study_lines
from kanamemory.arrangement.model import ClockEvent, LineUsage, Unit, Usage
from kanamemory.arrangement.rules import clock, lines
from kanamemory.route_mix.readiness import Readiness

T0 = datetime(2027, 3, 2, 9, tzinfo=timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def event(seconds, unit, line=None, *, teaching=False, day=None):
    when = at(seconds)
    return ClockEvent(when, day or when.date().isoformat(), unit, teaching, line)


# ------------------------------------------------------------------ the model

def test_line_usage_defaults_to_zero_and_of_names_each_unit_and_line():
    usage = LineUsage(1.0, 2.0, 3.0, 4.0)
    assert LineUsage() == LineUsage(0.0, 0.0, 0.0, 0.0)
    assert [usage.of(unit, line) for unit in (Unit.NEW, Unit.COURSE) for line in ("word", "grammar")] == [1, 2, 3, 4]
    assert usage.of(Unit.KANA, "word") == 0.0 and usage.of(Unit.NEW, None) == 0.0
    assert (usage.word, usage.grammar) == (4.0, 6.0)


def test_a_clock_event_built_as_before_has_no_line():
    assert ClockEvent(T0, "2027-03-02", Unit.NEW).line is None
    assert ClockEvent(T0, "2027-03-02", Unit.NEW, True).teaching is True


def test_the_line_window_and_weights_are_the_decided_values():
    assert lines.LINE_WINDOW == 7200.0 and lines.LINE_WINDOW > clock.WINDOW
    assert lines.LINE_WEIGHTS == {"words": (85, 15), "even": (70, 30), "grammar": (50, 50)}
    assert lines.DEFAULT_TILT == "even"
    assert (lines.WORD, lines.GRAMMAR) == (study_lines.WORD, study_lines.GRAMMAR)


# ------------------------------------------------------------------ line_usage

def random_history(rng, count):
    events, now = [], 0
    for _ in range(count):
        now += rng.choice([1, 5, 30, 120, 299, 301, 900, 1801, 40000])
        lane = rng.random()
        if lane < 0.3:
            unit, line = rng.choice([Unit.NEW, Unit.KANA]), None   # kana lane: no line
        else:
            unit, line = rng.choice([Unit.NEW, Unit.COURSE]), rng.choice(["word", "grammar"])
        events.append(event(now, unit, line, teaching=rng.random() < 0.1))
    return events, at(now + rng.choice([0, 1, 60]))


def kana_lane_usage(events, now, window):
    """The units' walk over the kana-lane events alone, inside the same walk (their part of the window)."""
    recent = [item for item in events if now - clock.LOOKBACK <= item.at <= now]
    total, kana = 0.0, {Unit.NEW: 0.0, Unit.KANA: 0.0, Unit.COURSE: 0.0}
    for item, seconds in reversed(clock.charges(recent)):
        if total >= window:
            break
        part = min(seconds, window - total)
        if item.line is None:
            kana[item.unit] += part
        total += part
    return Usage(kana[Unit.NEW], kana[Unit.COURSE], kana[Unit.KANA])


@pytest.mark.parametrize("seed", range(200))
def test_the_lines_plus_the_kana_lane_part_are_the_units_seconds_over_the_same_window(seed):
    rng = random.Random(seed)
    events, now = random_history(rng, rng.randrange(0, 60))
    window = rng.choice([lines.LINE_WINDOW, clock.WINDOW, 600.0])
    by_line = lines.line_usage(events, now, window=window)
    units = clock.window_usage(events, now, window=window)
    kana = kana_lane_usage(events, now, window)
    assert by_line.new_word + by_line.new_grammar + kana.new == pytest.approx(units.new)
    assert by_line.course_word + by_line.course_grammar + kana.course == pytest.approx(units.course)
    assert kana.kana == pytest.approx(units.kana)


def test_the_oldest_action_counts_with_its_part_inside_the_window():
    # 20 s opening, then gaps of 300 s each (capped), all on the word line: 20 + 30 x 300 = 9,020 s.
    events = [event(0, Unit.NEW, "word")] + [event(300 * n, Unit.COURSE, "grammar") for n in range(1, 31)]
    now = at(9000)
    usage = lines.line_usage(events, now)
    assert usage.course_grammar == 7200.0 and usage.new_word == 0.0
    shorter = lines.line_usage(events, now, window=9010.0)
    assert (shorter.course_grammar, shorter.new_word) == (9000.0, 10.0)
    # When the kept events reach less far than the window, the window is what they reach.
    assert lines.line_usage(events[-3:], now) == LineUsage(course_grammar=20.0 + 600.0)


def test_actions_after_now_or_before_the_lookback_are_ignored():
    events = [event(-15 * 86400, Unit.NEW, "word"), event(0, Unit.NEW, "grammar"), event(100, Unit.NEW, "word")]
    assert lines.line_usage(events, at(50)) == LineUsage(new_grammar=20.0)


def test_kana_lane_events_carry_no_line_and_take_their_part_of_the_window():
    events = [event(0, Unit.NEW), event(100, Unit.KANA), event(200, Unit.COURSE, "word")]
    assert lines.line_usage(events, at(200), window=150.0) == LineUsage(course_word=100.0)
    assert lines.line_usage([event(0, Unit.KANA), event(10, Unit.NEW)], at(10)) == LineUsage()


def test_an_empty_history_uses_nothing():
    assert lines.line_usage([], T0) == LineUsage()
    assert lines.period_usage([], ["2027-03-02"]) == LineUsage()


# ------------------------------------------------------------------ period_usage

def test_period_usage_sums_the_charges_of_the_given_study_days():
    events = [event(0, Unit.NEW, "grammar", teaching=True, day="d1"), event(100, Unit.COURSE, "word", day="d1"),
              event(86400, Unit.NEW, "word", day="d2"), event(86450, Unit.KANA, day="d2"),
              event(86500, Unit.COURSE, "grammar", day="d2")]
    assert lines.period_usage(events, ["d1"]) == LineUsage(new_grammar=240.0, course_word=100.0)
    assert lines.period_usage(events, ["d1", "d2"]) == LineUsage(
        new_word=20.0, new_grammar=240.0, course_word=100.0, course_grammar=50.0)
    assert lines.period_usage(events, []) == LineUsage()


# ------------------------------------------------------------------ behind

@pytest.mark.parametrize("tilt, word, grammar, expected", [
    ("even", 70.0, 30.0, None), ("even", 70.0, 29.0, "grammar"), ("even", 69.0, 30.0, "word"),
    ("words", 85.0, 15.0, None), ("words", 80.0, 20.0, "word"), ("words", 90.0, 10.0, "grammar"),
    ("grammar", 50.0, 50.0, None), ("grammar", 60.0, 40.0, "grammar"), ("grammar", 1.0, 2.0, "word"),
])
def test_behind_compares_used_time_per_weight(tilt, word, grammar, expected):
    usage = LineUsage(new_word=word, new_grammar=grammar)
    assert lines.behind(usage, Unit.NEW, lines.LINE_WEIGHTS[tilt]) == expected


def test_behind_looks_inside_the_given_unit_and_defaults_to_the_even_tilt():
    usage = LineUsage(new_word=100.0, new_grammar=0.0, course_word=0.0, course_grammar=100.0)
    assert lines.behind(usage, Unit.NEW) == "grammar"
    assert lines.behind(usage, Unit.COURSE) == "word"
    assert lines.behind(LineUsage(new_word=70.0, new_grammar=30.0), Unit.NEW) is None


def test_behind_with_no_usage_or_a_zero_weight_never_divides():
    assert lines.behind(LineUsage(), Unit.NEW) is None
    assert lines.behind(LineUsage(), Unit.KANA) is None
    assert lines.behind(LineUsage(new_word=10.0), Unit.NEW, (0, 0)) is None
    assert lines.behind(LineUsage(new_word=10.0), Unit.NEW, (100, 0)) is None
    assert lines.behind(LineUsage(new_word=10.0, new_grammar=5.0), Unit.NEW, (0, 100)) == "grammar"


# ------------------------------------------------------------------ the suggestion follows ``behind`` (LS8)

def entry(eid, family="word", domain=None, state="new"):
    return {"id": eid, "content_id": eid, "kind": "learning", "family": family, "domain": domain, "state": state,
            "title": eid}


def section(sid, *entries, title=None):
    return {"id": sid, "title": title or sid, "entries": list(entries),
            "new_count": sum(item["state"] == "new" for item in entries), "optional": False}


def overview(*sections):
    return {"stages": [{"id": "stage:1", "units": [{"id": "unit:1", "sections": list(sections)}]}]}


def grammar(eid, state="new"):
    return entry(eid, "grammar", None, state)


def suggested(route, **kw):
    suggestion = study_lines.route_lines(route, breadth=kw.pop("breadth", True), **kw).suggestion
    return None if suggestion is None else suggestion.section_id


# Neither line has added-but-unlearned content; the word section comes first on the route.
NEITHER = overview(section("w1", entry("a", state="completed")), section("w2", entry("b")),
                   section("w3", entry("c")), section("g1", grammar("p")))
# Both lines have it.
BOTH = overview(section("w1", entry("a", state="added")), section("g0", grammar("o", state="started")),
                section("w2", entry("b")), section("g1", grammar("p")))


def test_rule_b_neither_line_unlearned_follows_behind():
    assert suggested(NEITHER) == "w2"
    assert suggested(NEITHER, behind=None) == "w2"
    assert suggested(NEITHER, behind=study_lines.GRAMMAR) == "g1"
    assert suggested(NEITHER, behind=study_lines.WORD) == "w2"


def test_rule_b_both_lines_unlearned_follows_behind():
    assert suggested(BOTH) == "w2"
    assert suggested(BOTH, behind=study_lines.GRAMMAR) == "g1"


def test_rule_a_still_wins_when_one_line_is_unlearned():
    route = overview(section("w1", entry("a", state="added")), section("w2", entry("b")), section("g1", grammar("p")))
    assert suggested(route, behind=study_lines.WORD) == "g1"
    route = overview(section("g0", grammar("o", state="added")), section("w2", entry("b")), section("g1", grammar("p")))
    assert suggested(route, behind=study_lines.GRAMMAR) == "w2"


def test_behind_naming_a_line_without_a_next_section_changes_nothing():
    words_only = overview(section("w1", entry("a", state="completed")), section("w2", entry("b")),
                          section("g1", grammar("p", state="completed")))
    assert suggested(words_only, behind=study_lines.GRAMMAR) == "w2"
    assert suggested(words_only, behind="kana") == "w2"


def test_rule_c_applies_after_behind_chose_grammar():
    route = overview(section("w1", entry("a", state="completed")), section("w2", entry("b", ), title="动物"),
                     section("g1", grammar("p"), title="は"))
    readiness = Readiness({"p": {"application": [("t1", ["b"])]}, "b": {}})
    result = study_lines.route_lines(route, readiness=readiness, breadth=True, behind=study_lines.GRAMMAR)
    assert result.suggestion == study_lines.Suggestion(
        study_lines.WORD, "w2", "动物", "「は」的题还要用到 1 项没加入的内容，先加「动物」。")
    ready = study_lines.route_lines(route, readiness=readiness, ticked=("b",), breadth=True,
                                    behind=study_lines.GRAMMAR)
    assert ready.suggestion == study_lines.Suggestion(study_lines.GRAMMAR, "g1", "は")


def test_not_breadth_ignores_behind():
    assert suggested(NEITHER, breadth=False, behind=study_lines.GRAMMAR) == "w2"
    direct = study_lines.suggest({"word": None, "grammar": None}, frozenset(), breadth=False, route_next=None,
                                 positions={}, behind=study_lines.GRAMMAR)
    assert direct is None


def test_following_the_suggestion_one_section_at_a_time_gives_the_weights_proportion():
    """LS8's effect: adding one section at a time by 建议先加, new-content time follows 70 : 30 by default,
    however many word sections come before the next grammar section on the route."""
    word_section, grammar_section = 240.0, 600.0   # new-content seconds one section costs
    used = LineUsage()
    words = grammar_count = 0
    for _ in range(400):
        nexts = {"word": {"id": f"w{words}", "title": ""}, "grammar": {"id": f"g{grammar_count}", "title": ""}}
        positions = {f"w{words}": 0, f"g{grammar_count}": 1}   # the route always puts words first
        chosen = study_lines.suggest(nexts, frozenset(), breadth=True, route_next=nexts["word"],
                                     positions=positions, behind=lines.behind(used, Unit.NEW)).line
        if chosen == study_lines.WORD:
            used, words = LineUsage(new_word=used.new_word + word_section, new_grammar=used.new_grammar), words + 1
        else:
            used = LineUsage(new_word=used.new_word, new_grammar=used.new_grammar + grammar_section)
            grammar_count += 1
    share = used.new_word / (used.new_word + used.new_grammar)
    assert share == pytest.approx(0.70, abs=0.01)
