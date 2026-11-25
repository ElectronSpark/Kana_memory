"""Design 12 section 21 (N1-N5): the pure rules of recently learned course states.

Made-up instants only. Covers the recent predicate at its 14-day edge, the
course order key (scope, recency, due), the clock's attribution of every
kind of action (teaching, first ratings of first and later states, steps,
reviews, unscored answers and practice of recent and old states, kana),
the N2 "serviceable" reading (2-day interval: a day after the rating;
7-day interval and old states: at the due), the credit of an N2 answer,
and the N5 consolidation schedule (gaps, count, a passed probe, a step,
24 hours, deck readiness, the cap, interleaving, the leave pause).
"""
from datetime import datetime, timedelta, timezone

from kanamemory.arrangement.model import (ConsolidationFacts, Credit, FirstRatings, RawEvent, StateRef, StateView,
                                          Unit)
from kanamemory.arrangement.rules import clock, consolidation, credit, focus, recent

T0 = datetime(2030, 5, 1, 8, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


# ---- the predicate

def test_a_state_is_recent_for_fourteen_times_twenty_four_hours_after_its_first_rating():
    assert recent.RECENT_DAYS == 14 and recent.RECENT == timedelta(hours=14 * 24)
    assert recent.is_recent(T0, T0)
    assert recent.is_recent(T0, T0 + recent.RECENT - timedelta(seconds=1))
    assert not recent.is_recent(T0, T0 + recent.RECENT)
    assert not recent.is_recent(None, T0)


# ---- N1: the order key

def test_the_order_key_puts_recent_due_reviews_first_and_keeps_the_due_order_inside_each_group():
    old_early, old_late = (3, 100.0), (3, 200.0)
    recent_late = (3, 300.0)
    keys = sorted([recent.ordered_priority(old_early, False), recent.ordered_priority(recent_late, True),
                   recent.ordered_priority(old_late, False)])
    assert keys == [(3, 0, 300.0), (3, 1, 100.0), (3, 1, 200.0)]
    # tiers 0-2 and new work are unchanged
    for tier in ((0, 0), (1, 0), (2, 50.0), (4, -1), (4, 0), (5, 0)):
        assert recent.ordered_priority(tier, True) == tier


def test_with_a_scope_the_scope_component_stays_in_front_of_the_recency_component():
    inside_old = focus.scoped_priority(recent.ordered_priority((3, 100.0), False), 0)
    outside_recent = focus.scoped_priority(recent.ordered_priority((3, 50.0), True), 1)
    inside_recent = focus.scoped_priority(recent.ordered_priority((3, 400.0), True), 0)
    assert sorted([outside_recent, inside_old, inside_recent]) == [inside_recent, inside_old, outside_recent]
    assert inside_recent == (3, 0, 0, 400.0)


# ---- N3: the clock's attribution

def test_attribution_of_every_kind_of_action_new_content_pays_the_learning_day():
    """Design 12 N3 (final): new content pays for the day a state is learned and for its consolidation."""
    assert recent.LEARNING_DAY == timedelta(hours=24)
    firsts = {"u1": FirstRatings(at(300), "d1"),             # a new lesson's first state, first rated at 300 s
              "u2": FirstRatings(at(500), "d1"),             # an ability opened later, first rated at 500 s
              "u0": FirstRatings(T0 - 15 * DAY, "d0"),       # an old state
              "k1": FirstRatings(at(100), "d1")}             # a kana state
    raw = [
        RawEvent(at(100), "d1", "course", "u1", teaching_mark=True),   # teaching: new content
        RawEvent(at(200), "d1", "course", "u1"),                       # unscored before the first rating: new
        RawEvent(at(300), "d1", "course", "u1", formal=True),          # first rating of the first state: new
        RawEvent(at(900), "d1", "course", "u1", formal=True),          # same-day step: new
        RawEvent(at(1500), "d1", "course", "u1", formal=True),         # a same-day relearning (a miss, then again): new
        RawEvent(at(950), "d1", "course", "u1"),                       # an unscored answer after the first: course
        RawEvent(at(990), "d1", "course", "u1", consolidation=True),   # a consolidation answer: new
        RawEvent(T0 + 5 * DAY, "d6", "course", "u1", consolidation=True),   # any day: new
        RawEvent(at(400), "d1", "course", "u2"),                       # later ability before its first rating
        RawEvent(at(500), "d1", "course", "u2", formal=True),          # its first check: course review
        RawEvent(at(800), "d1", "course", "u2", formal=True),          # its same-day step: new
        RawEvent(at(600), "d1", "course", "u0", formal=True),          # an old state's review: course
        RawEvent(at(700), "d1", "kana", "k1", formal=True),            # kana: kana practice
        RawEvent(at(300) + DAY - timedelta(seconds=1), "d2", "course", "u1", formal=True),  # 23 h 59 min 59 s: new
        RawEvent(at(300) + DAY, "d2", "course", "u1", formal=True),    # the review at +24 h: course
        RawEvent(T0 + 3 * DAY, "d4", "course", "u1", formal=True)]     # a later review of a recent state: course
    result = clock.attribute(raw, firsts, frozenset({"u2"}))
    assert [item.unit for item in result] == [
        Unit.NEW, Unit.NEW, Unit.NEW, Unit.NEW, Unit.NEW, Unit.COURSE, Unit.NEW, Unit.NEW,
        Unit.COURSE, Unit.COURSE, Unit.NEW,
        Unit.COURSE, Unit.KANA,
        Unit.NEW, Unit.COURSE, Unit.COURSE]
    assert [item.teaching for item in result].count(True) == 1


def test_the_learning_day_edge_is_24_hours_after_the_first_rating():
    assert recent.learning_day(T0, T0 + timedelta(hours=23, minutes=59))
    assert not recent.learning_day(T0, T0 + timedelta(hours=24))
    assert not recent.learning_day(None, T0)


# ---- N2: one day is enough for a short interval

def test_a_recent_two_day_interval_is_served_a_day_after_its_rating():
    last, due = T0, T0 + 2 * DAY
    assert recent.serviceable_due(due, last, T0, T0) == T0 + DAY
    assert recent.served_early(due, T0 + DAY, T0 + DAY)
    assert not recent.served_early(due, T0 + DAY, T0 + DAY - timedelta(seconds=1))
    assert not recent.served_early(due, T0 + DAY, due)   # at the due itself it is simply due


def test_a_seven_day_interval_and_an_old_state_are_served_at_their_due():
    assert recent.serviceable_due(T0 + 7 * DAY, T0, T0, T0) == T0 + 7 * DAY
    assert recent.serviceable_due(T0 + 3 * DAY, T0, T0, T0) == T0 + DAY          # three days: still short
    assert recent.serviceable_due(T0 + 2 * DAY, T0, T0 - 20 * DAY, T0) == T0 + 2 * DAY   # not recent
    # a same-day step keeps its own due (it is earlier than a day)
    assert recent.serviceable_due(T0 + timedelta(minutes=10), T0, T0, T0) == T0 + timedelta(minutes=10)
    assert recent.serviceable_due(None, T0, T0, T0) is None


def test_an_answer_due_only_by_the_short_interval_reading_is_an_early_formal_rating():
    ref = "course:c:understanding"
    early = StateView(ref, T0 + DAY, T0, early=True)
    assert credit.classify(early, T0 + DAY) is Credit.FORMAL_EARLY
    assert credit.classify(StateView(ref, T0 + DAY, T0), T0 + DAY) is Credit.FORMAL_DUE


# ---- N5: consolidation of what was taught today

def facts(**values):
    base = dict(ref=StateRef.course("lesson:a", "understanding"), taught_at=T0, last_rated=at(60),
                due=T0 + 2 * DAY, in_step=False, repeats=0, last_exposure=at(60))
    base.update(values)
    return ConsolidationFacts(**base)


def test_the_repeats_are_ready_ten_minutes_thirty_minutes_and_three_hours_after_the_last_exposure():
    assert consolidation.PRACTICE_REPEATS == 3
    assert consolidation.REPEAT_GAPS == (timedelta(minutes=10), timedelta(minutes=30), timedelta(hours=3))
    first = facts()
    assert consolidation.ready_at(first, at(61)) == at(60) + timedelta(minutes=10)
    assert not consolidation.is_ready(first, at(60 + 599)) and consolidation.is_ready(first, at(660))
    second = facts(repeats=1, last_exposure=at(700))
    assert consolidation.ready_at(second, at(800)) == at(700) + timedelta(minutes=30)
    third = facts(repeats=2, last_exposure=at(3000))
    assert consolidation.ready_at(third, at(3001)) == at(3000) + timedelta(hours=3)
    assert consolidation.ready_at(facts(repeats=3, last_exposure=at(3000)), at(30000)) is None


def test_no_consolidation_for_a_passed_probe_inside_a_step_when_due_or_after_twenty_four_hours():
    now = at(4000)
    assert consolidation.ready_at(facts(taught_at=None), now) is None                  # never taught
    assert consolidation.ready_at(facts(taught_at=at(100)), now) is None               # rated before the teaching
    assert consolidation.ready_at(facts(in_step=True), now) is None                     # inside a step
    assert consolidation.ready_at(facts(due=at(3000)), now) is None                     # due: formal work
    assert consolidation.ready_at(facts(), T0 + DAY) is None                            # taught 24 h ago
    assert consolidation.ready_at(facts(), T0 + DAY - timedelta(seconds=1)) is not None


def test_a_deck_is_due_with_three_ready_or_one_ready_for_ten_minutes_or_nothing_else():
    now = at(10000)
    assert not consolidation.deck_due([], now, nothing_else=True)
    assert not consolidation.deck_due([now, now], now)
    assert consolidation.deck_due([now, now, now], now)
    assert consolidation.deck_due([now - timedelta(minutes=10)], now)
    assert not consolidation.deck_due([now - timedelta(minutes=9)], now)
    assert consolidation.deck_due([now], now, nothing_else=True)
    assert not consolidation.deck_due([now + timedelta(seconds=1)], now, nothing_else=True)


def test_a_deck_holds_at_most_eight_items_with_the_lessons_interleaved():
    now = at(10000)
    items = [facts(ref=StateRef.course(f"lesson:{lesson}", ability), last_exposure=at(60 + index))
             for index, (lesson, ability) in enumerate(
                 [(name, ability) for name in "abc" for ability in ("understanding", "reading", "use", "listening")])]
    order = consolidation.deck_order(items, now)
    assert len(order) == consolidation.DECK_SIZE == 8
    assert [item.content_id for item in order[:6]] == ["lesson:a", "lesson:b", "lesson:c"] * 2


def test_after_leaving_a_deck_none_is_offered_for_ten_minutes():
    assert consolidation.paused(T0, T0 + timedelta(minutes=9, seconds=59))
    assert not consolidation.paused(T0, T0 + timedelta(minutes=10))
    assert not consolidation.paused(None, T0)
