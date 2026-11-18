"""The study clock: timed actions -> charged seconds -> usage over a sliding window (design 12 B6).

Pure. Time is charged by the gap between consecutive actions, whichever unit
they belong to, so that reading feedback and introductions counts and both
lanes are measured alike. Nothing is stored: the same history always gives
the same usage. The window slides over study time and does not reset at
midnight; the day totals exist for display only.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from ..model import ClockEvent, FirstRatings, RawEvent, Unit, Usage

# Product parameters (design 12 section 11): starting values, not memory-model constants.
GAP_CAP = 300.0               # an ordinary action is charged at most this many seconds
TEACHING_GAP_CAP = 900.0      # a teaching action follows reading and a guide: a longer cap
SITTING_BREAK = 1800.0        # a longer gap, or another study day, starts a new sitting
NEW_SITTING = 20.0            # charge of an ordinary action that opens a sitting
TEACHING_NEW_SITTING = 240.0  # charge of a teaching action that opens a sitting
WINDOW = 1800.0               # seconds of study time the share choice looks back over
LOOKBACK = timedelta(days=14)

_REVIEW_UNIT = {"course": Unit.COURSE, "kana": Unit.KANA}


def attribute(events: Sequence[RawEvent], firsts: Mapping[str, FirstRatings]) -> list[ClockEvent]:
    """Give each action its unit (design 12 section 3, "新内容的归属").

    New content: a teaching mark, anything before the state's first formal
    rating, that first rating, and the next formal rating on the same study
    day (the same-day step). Everything later is review of the action's lane.
    Only a teaching mark is a teaching action for the gap caps: the reading
    and the guide end at the mark, and the first rating after it is an
    ordinary answer.
    """
    attributed = []
    for event in events:
        info = firsts.get(event.state)
        unit = _REVIEW_UNIT[event.lane]
        if event.teaching_mark or info is None or event.at < info.first:
            unit = Unit.NEW
        elif event.formal and (event.at == info.first or (
                info.follow_up is not None and event.at == info.follow_up and event.day == info.day)):
            unit = Unit.NEW
        attributed.append(ClockEvent(event.at, event.day, unit, event.teaching_mark))
    return attributed


def charges(events: Sequence[ClockEvent]) -> list[tuple[ClockEvent, float]]:
    """Charged seconds of each action, oldest first.

    The charge is the gap to the previous action, capped; an action that opens
    a sitting gets a fixed charge instead, because the time before it was not
    study time.
    """
    charged, previous = [], None
    for event in sorted(events, key=lambda item: item.at):
        gap = None if previous is None else (event.at - previous.at).total_seconds()
        if gap is None or gap > SITTING_BREAK or event.day != previous.day:
            seconds = TEACHING_NEW_SITTING if event.teaching else NEW_SITTING
        else:
            seconds = min(TEACHING_GAP_CAP if event.teaching else GAP_CAP, max(0.0, gap))
        charged.append((event, seconds))
        previous = event
    return charged


def sitting_break(previous: datetime, previous_day: str, now: datetime, day: str) -> bool:
    """Whether an action at ``now`` (on study day ``day``) starts a new sitting after one at ``previous``.

    The same rule as charges(): a gap longer than SITTING_BREAK, or another study day.
    """
    return (now - previous).total_seconds() > SITTING_BREAK or day != previous_day


def _add(usage: dict, unit: Unit, seconds: float) -> None:
    usage[unit] = usage.get(unit, 0.0) + seconds


def _usage(usage: Mapping[Unit, float]) -> Usage:
    return Usage(usage.get(Unit.NEW, 0.0), usage.get(Unit.COURSE, 0.0), usage.get(Unit.KANA, 0.0))


def window_usage(events: Sequence[ClockEvent], now: datetime, *, window: float = WINDOW,
                 lookback: timedelta = LOOKBACK) -> Usage:
    """Charged seconds per unit over the last ``window`` seconds of study time.

    Walks back from the newest action; the oldest action that still reaches
    into the window counts with the part inside it. Actions after ``now`` or
    older than ``lookback`` are ignored.
    """
    recent = [event for event in events if now - lookback <= event.at <= now]
    usage, total = {}, 0.0
    for event, seconds in reversed(charges(recent)):
        if total >= window:
            break
        part = min(seconds, window - total)
        _add(usage, event.unit, part)
        total += part
    return _usage(usage)


def day_usage(events: Sequence[ClockEvent], day: str) -> Usage:
    """Charged seconds per unit on one study day (display only)."""
    usage = {}
    for event, seconds in charges(events):
        if event.day == day:
            _add(usage, event.unit, seconds)
    return _usage(usage)
