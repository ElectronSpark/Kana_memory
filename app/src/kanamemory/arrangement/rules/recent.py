"""Recently learned course states: the one predicate, their order and their short intervals (design 12 section 21).

Pure. A course scheduling state (a lesson's ability) is recent while its
FIRST formal rating lies less than RECENT_DAYS before the moment asked
about. Every reader asks this module: the order of due course reviews (N1),
the "is it due" of a short interval (N2), what is served before the share
rule (N4), and the consolidation practice of rules/consolidation (N5).
Kana states are never recent. The clock does not use recency: it charges
new content for the day a state is learned (learning_day, N3).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .credit import FORMAL_SPACING

# Product parameters (design 12 sections 11 and 21): starting values, not memory-model constants.
RECENT_DAYS = 14
RECENT = timedelta(days=RECENT_DAYS)       # 14 x 24 hours of elapsed time, not calendar days
EARLY_INTERVAL = timedelta(days=3)         # a recent state's interval up to this is served after one day
SERVE_AFTER = FORMAL_SPACING               # one day: the scheduler library's own 24 hours of an early rating

REVIEW_TIER = 3   # ordinary due reviews; tiers 0-2 (feedback, a started answer, short steps) are never reordered
LEARNING_DAY = timedelta(hours=24)  # the day a state is learned: its formal answers then are new content (N3)


def is_recent(first_rated: datetime | None, at: datetime) -> bool:
    """Whether a state first formally rated at ``first_rated`` is recent at ``at`` (None: never rated)."""
    return first_rated is not None and at - first_rated < RECENT


def learning_day(first_rated: datetime | None, at: datetime) -> bool:
    """Whether ``at`` lies within LEARNING_DAY of a state's first formal rating (the clock's N3 only)."""
    return first_rated is not None and timedelta(0) <= at - first_rated < LEARNING_DAY


def serviceable_due(due: datetime | None, last_rated: datetime | None, first_rated: datetime | None,
                    now: datetime) -> datetime | None:
    """When the course queue may serve a state as an ordinary due review (N2).

    A recent state whose scheduled interval (``due`` minus its last formal
    rating) is at most EARLY_INTERVAL is served once SERVE_AFTER has passed
    since that rating; every other state at its ``due``. The stored due is
    never changed: this is the queue's reading of it, and the answer is a
    formal rating at its real time (the scheduler discounts the earliness).
    """
    if due is None or last_rated is None or not is_recent(first_rated, now):
        return due
    if due - last_rated > EARLY_INTERVAL:
        return due
    return min(due, last_rated + SERVE_AFTER)


def served_early(due: datetime | None, serviceable: datetime | None, now: datetime) -> bool:
    """Whether a state is due at ``now`` only by the N2 reading (its stored due still lies ahead)."""
    return (due is not None and serviceable is not None and serviceable <= now < due)


def recency_component(recent: bool) -> int:
    """0 for a recent state, 1 for the others: the component after the tier in the course order (N1)."""
    return 0 if recent else 1


def ordered_priority(priority: tuple, recent: bool) -> tuple:
    """The course lane's collection key with the recency component after the tier (N1).

    Only ordinary due reviews (tier 3) get it, so the recent group comes
    first and each group keeps its due order; the other tiers are unchanged.
    A focus scope's component (rules/focus.scoped_priority) is inserted
    after the tier afterwards, so it stays in front of this one.
    """
    if not priority or priority[0] != REVIEW_TIER:
        return tuple(priority)
    return (priority[0], recency_component(recent), *priority[1:])
