"""Consolidation practice of what was taught today: when a repeat is ready, when a deck is due (design 12 N5).

Pure. A course state whose lesson got its explanation less than TAUGHT_WINDOW
ago in the probe-first flow ("taught today": a failed probe, 还不会, or taught
directly; never a passed probe) is practised again in the same sitting, at
most PRACTICE_REPEATS times, the k-th repeat once REPEAT_GAPS[k-1] have
passed since the state's last exposure (a formal answer or a practice
answer). Only while the answer would be practice only (rules/credit: not
due, not inside a step, last formal rating less than 24 hours old): the
deck never rates. A deck is offered once DECK_READY_COUNT states are ready,
or one has been ready for READY_WAIT, or nothing else can be served; it
holds at most DECK_SIZE items, the lessons interleaved. After the learner
leaves a deck no new one is offered for LEAVE_PAUSE.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from ..model import ConsolidationFacts
from .credit import FORMAL_SPACING

# Product parameters (design 12 sections 11 and 21): starting values.
PRACTICE_REPEATS = 3
REPEAT_GAPS = (timedelta(minutes=10), timedelta(minutes=30), timedelta(hours=3))
TAUGHT_WINDOW = timedelta(hours=24)
DECK_SIZE = 8
DECK_READY_COUNT = 3
READY_WAIT = timedelta(minutes=10)
LEAVE_PAUSE = timedelta(minutes=10)


def taught_today(taught_at: datetime | None, now: datetime) -> bool:
    """Whether a teaching mark at ``taught_at`` (probe-first flow) lies less than TAUGHT_WINDOW before ``now``."""
    return taught_at is not None and timedelta(0) <= now - taught_at < TAUGHT_WINDOW


def ready_at(facts: ConsolidationFacts, now: datetime) -> datetime | None:
    """When the state's next consolidation repeat is ready, or None when it gets none (now or any more).

    None: not taught today, never rated after its teaching (a passed probe
    is rated before), inside a step, due (formal work), last formal rating
    24 hours or more ago (an answer would be formal), or all repeats done.
    """
    if not taught_today(facts.taught_at, now) or facts.last_rated is None or facts.last_rated < facts.taught_at:
        return None
    if facts.in_step or (facts.due is not None and facts.due <= now) or now - facts.last_rated >= FORMAL_SPACING:
        return None
    if facts.repeats >= PRACTICE_REPEATS:
        return None
    last = max(value for value in (facts.last_exposure, facts.last_rated) if value is not None)
    return last + REPEAT_GAPS[facts.repeats]


def is_ready(facts: ConsolidationFacts, now: datetime) -> bool:
    when = ready_at(facts, now)
    return when is not None and when <= now


def deck_due(ready: Sequence[datetime], now: datetime, *, nothing_else: bool = False) -> bool:
    """Whether a deck is offered now; ``ready``: the ready instants of the states that are ready at ``now``."""
    ready = [value for value in ready if value <= now]
    if not ready:
        return False
    return len(ready) >= DECK_READY_COUNT or min(ready) + READY_WAIT <= now or nothing_else


def paused(left_at: datetime | None, now: datetime) -> bool:
    """Whether the learner left a consolidation deck less than LEAVE_PAUSE ago."""
    return left_at is not None and timedelta(0) <= now - left_at < LEAVE_PAUSE


def deck_order(facts: Iterable[ConsolidationFacts], now: datetime, *, size: int = DECK_SIZE) -> list[ConsolidationFacts]:
    """The ready states of one deck: the longest ready first, lessons interleaved (round robin), at most ``size``."""
    ready = sorted((item for item in facts if is_ready(item, now)), key=lambda item: (ready_at(item, now), item.key))
    lessons: dict[str, list[ConsolidationFacts]] = {}
    for item in ready:
        lessons.setdefault(item.content_id, []).append(item)
    order, queues = [], list(lessons.values())
    while queues and len(order) < size:
        for queue in list(queues):
            order.append(queue.pop(0))
            if not queue:
                queues.remove(queue)
            if len(order) >= size:
                break
    return order
