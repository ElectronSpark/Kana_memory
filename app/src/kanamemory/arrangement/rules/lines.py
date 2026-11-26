"""The time each study line gets inside new content and course review (design 12 LS1-LS3, LS8).

Pure. The three units of rules/shares and their weights are untouched: the
share between the word line and the grammar line lives inside new content and
inside course review. Every course-lane action the clock charges carries its
lesson's line (ClockEvent.line); kana-lane actions carry none. Line usage is
the same charged seconds as rules/clock's, summed per (unit, line) by the same
walk back over study time: nothing is stored, and the same history always
gives the same line usage.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime, timedelta

from ..model import ClockEvent, LineUsage, Unit
from .clock import LOOKBACK, charges

WORD, GRAMMAR = "word", "grammar"   # the values of study_lines.WORD / GRAMMAR

# Product parameters (design 12 section 11): starting values.
# The line share looks back over 2 hours of study time, not the units' 30 minutes (clock.WINDOW): one
# grammar lesson's teaching block is longer than grammar's share of a 30-minute window, so a 30-minute
# window would forget a grammar lesson before its line's share came round again.
LINE_WINDOW = 7200.0
# The three tilts between the lines, each (word, grammar).
LINE_WEIGHTS = {"words": (85, 15), "even": (70, 30), "grammar": (50, 50)}
# The tilt used until the learner can choose one (design 12 LS4, a later step).
DEFAULT_TILT = "even"

_UNITS = (Unit.NEW, Unit.COURSE)
_LINES = (WORD, GRAMMAR)


def _line_usage(seconds: Mapping[tuple[Unit, str], float]) -> LineUsage:
    return LineUsage(seconds.get((Unit.NEW, WORD), 0.0), seconds.get((Unit.NEW, GRAMMAR), 0.0),
                     seconds.get((Unit.COURSE, WORD), 0.0), seconds.get((Unit.COURSE, GRAMMAR), 0.0))


def _add(seconds: dict, event: ClockEvent, part: float) -> None:
    if event.unit in _UNITS and event.line in _LINES:
        key = (event.unit, event.line)
        seconds[key] = seconds.get(key, 0.0) + part


def line_usage(events: Sequence[ClockEvent], now: datetime, *, window: float = LINE_WINDOW,
               lookback: timedelta = LOOKBACK) -> LineUsage:
    """Charged seconds per (unit, line) over the last ``window`` seconds of study time.

    The walk of clock.window_usage with this window: from the newest action
    back, the oldest action that still reaches into the window counts with the
    part inside it; actions after ``now`` or older than ``lookback`` are
    ignored, and when the kept events reach less far the window is what they
    reach. Kana-lane actions (line None) take their part of the window and are
    not counted here, so the four sums plus the kana-lane part equal
    clock.window_usage(events, now, window=window) unit by unit.
    """
    recent = [event for event in events if now - lookback <= event.at <= now]
    seconds, total = {}, 0.0
    for event, charged in reversed(charges(recent)):
        if total >= window:
            break
        part = min(charged, window - total)
        _add(seconds, event, part)
        total += part
    return _line_usage(seconds)


def period_usage(events: Sequence[ClockEvent], days: Collection[str]) -> LineUsage:
    """Charged seconds per (unit, line) on the given study days (the records page)."""
    days = frozenset(days)
    seconds = {}
    for event, charged in charges(events):
        if event.day in days:
            _add(seconds, event, charged)
    return _line_usage(seconds)


def behind(usage: LineUsage, unit: Unit, weights: tuple[int, int] = LINE_WEIGHTS[DEFAULT_TILT]) -> str | None:
    """The line with the smaller used time per weight inside ``unit``; None when the two are equal (also both zero).

    ``weights``: (word, grammar). Compared cross-multiplied, without dividing.
    """
    word_weight, grammar_weight = weights
    word, grammar = usage.of(unit, WORD), usage.of(unit, GRAMMAR)
    left, right = word * grammar_weight, grammar * word_weight
    if left < right:
        return WORD
    if right < left:
        return GRAMMAR
    return None
