"""What one answer counts as, and what a miss in practice leads to (design 12 E3).

Pure. This is the single place that decides between a formal rating and
practice only; both lanes, the kana reinforce rounds and the mastery check
ask it. Scheduled work is always rated. A state that is not due is rated
early once 24 hours have passed since its last formal rating (the scheduler
library then scales the gain by how early the answer is); within 24 hours it
is practice only, because the library would add nothing for a correct answer
and would cut most of the day's progress for a wrong one.

Misses in practice are debounced: one miss may be a slip and changes nothing;
a second miss in a row on the same state, in the same sitting, is a confirmed
miss and may bring its next formal review forward once per 24 hours. The size
of that move belongs to the existing bounded auxiliary adjustment, not here.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..model import Credit, PracticeEffect, PracticeMark, StateView

# Product parameters (design 12 section 11).
FORMAL_SPACING = timedelta(hours=24)   # elapsed time, as the scheduler library counts it; not a calendar day
ADVANCE_SPACING = timedelta(hours=24)  # at most one forward move per state in this time
CONFIRMING_MISSES = 2                  # consecutive misses in one sitting that confirm a miss


def classify(state: StateView, now: datetime) -> Credit:
    """Formal (scheduled), formal (early) or practice only."""
    if state.last_rated is None or (state.due is not None and state.due <= now):
        return Credit.FORMAL_DUE       # a first check, a due review, or a step that has come due
    if state.in_step:
        return Credit.PRACTICE         # inside its steps and not yet due: the schedule owns it
    if now - state.last_rated >= FORMAL_SPACING:
        return Credit.FORMAL_EARLY
    return Credit.PRACTICE


def may_advance(state: StateView, now: datetime, last_advanced: datetime | None) -> bool:
    """May a confirmed miss bring this state's next formal review forward?

    Only a rated state outside its steps that is not yet due, and at most once
    per ADVANCE_SPACING.
    """
    if state.last_rated is None or state.in_step or state.due is None or state.due <= now:
        return False
    return last_advanced is None or now - last_advanced >= ADVANCE_SPACING


def practice_answer(mark: PracticeMark, correct: bool, position: int, *,
                    can_advance: bool) -> tuple[PracticeMark, PracticeEffect]:
    """Fold one practice-only answer into the state's mark for this sitting.

    ``position`` is the index of this question in the sitting. A correct
    answer clears the miss. The first miss asks for a retry later; a miss on
    that retry is confirmed and, when ``can_advance``, asks for the forward
    move. The state stays marked for another retry after any miss.
    """
    if correct:
        return PracticeMark(0, False, position), PracticeEffect.NONE
    streak = mark.wrong_streak + 1
    if streak >= CONFIRMING_MISSES and can_advance:
        return PracticeMark(0, True, position), PracticeEffect.ADVANCE
    return PracticeMark(min(streak, CONFIRMING_MISSES), True, position), PracticeEffect.RETRY
