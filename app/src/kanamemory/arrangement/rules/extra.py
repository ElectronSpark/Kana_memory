"""Which learned state extra practice offers next (design 12 E2).

Pure. Extra practice only runs when scheduled work is done, so due states and
states inside their steps are never candidates. First come the states that
may be rated early, the one closest to being forgotten first; when those are
used up, the states rated within the last 24 hours go round as practice only,
so the supply never runs dry while one such state exists.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from ..model import Credit, PracticeMark, StateView
from .credit import classify

REPEAT_GAP = 8  # other questions between two practices of one state, when there are enough states

_UNMARKED = PracticeMark()


def eligible(states: Sequence[StateView], now: datetime) -> list[StateView]:
    """Learned states that are not due and not inside their steps."""
    return [state for state in states
            if state.last_rated is not None and not state.in_step and state.due is not None and state.due > now]


def first_segment(states: Sequence[StateView], now: datetime) -> list[StateView]:
    """States that may be rated early: in scope first, then lowest retrievability first."""
    pool = [state for state in eligible(states, now) if classify(state, now) is Credit.FORMAL_EARLY]
    return sorted(pool, key=lambda state: (
        not state.in_scope, 1.0 if state.retrievability is None else state.retrievability, state.key))


def second_segment(states: Sequence[StateView], now: datetime, marks: Mapping[str, PracticeMark],
                   position: int, *, gap: int = REPEAT_GAP) -> list[StateView]:
    """Practice-only states in the order to offer them at ``position``.

    In scope first; then those missed in this sitting; then the least recently
    practised. A state practised within the last ``gap`` questions is left out
    unless nothing else remains, in which case the least recent one leads.
    """
    pool = [state for state in eligible(states, now) if classify(state, now) is Credit.PRACTICE]

    def mark(state):
        return marks.get(state.key, _UNMARKED)

    def order(state):
        return (not state.in_scope, not mark(state).needs_retry, mark(state).last_position,
                state.last_rated, state.key)

    spaced = [state for state in pool
              if mark(state).last_position < 0 or position - mark(state).last_position > gap]
    if spaced:
        return sorted(spaced, key=order)
    return sorted(pool, key=lambda state: (mark(state).last_position, state.key))


def practice_run(states: Sequence[StateView], now: datetime, marks: Mapping[str, PracticeMark], position: int,
                 count: int, *, groups: Mapping[str, str] | None = None, gap: int = REPEAT_GAP) -> list[StateView]:
    """Up to ``count`` practice-only states for the questions at ``position``, ``position`` + 1, ...

    Each is the head of second_segment at its position, as if the ones before
    it had been practised there; a state (or another state of its group) comes
    once per run. ``groups`` maps a state key to the scheduling state it
    shares (kana question kinds of one ability); a key without a group is its
    own. The caller hands in marks that already speak for the whole group.
    """
    def group(key: str) -> str:
        return groups.get(key, key) if groups else key

    members: dict[str, list[str]] = {}
    for state in states:
        members.setdefault(group(state.key), []).append(state.key)
    marks = dict(marks)
    run: list[StateView] = []
    chosen: set[str] = set()
    for offset in range(max(0, count)):
        head = next((state for state in second_segment(states, now, marks, position + offset, gap=gap)
                     if group(state.key) not in chosen), None)
        if head is None:
            break
        run.append(head)
        chosen.add(group(head.key))
        for key in members[group(head.key)]:
            mark = marks.get(key, _UNMARKED)
            marks[key] = PracticeMark(mark.wrong_streak, mark.needs_retry, position + offset)
    return run


def next_extra(states: Sequence[StateView], now: datetime, marks: Mapping[str, PracticeMark],
               position: int) -> tuple[Credit, StateView] | None:
    """The next extra question and what its answer will count as; None when nothing can be practised."""
    early = first_segment(states, now)
    if early:
        return Credit.FORMAL_EARLY, early[0]
    practice = second_segment(states, now, marks, position)
    if practice:
        return Credit.PRACTICE, practice[0]
    return None
