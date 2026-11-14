"""PracticeSitting: the memory of one sitting of extra practice (design 12 E2, E3, 10 (6)).

Memory only, never stored: per state its PracticeMark (misses in a row,
whether it waits for a retry, where it was last asked) and the position
counter of the sitting. A practice-only answer is folded in with
rules/credit.practice_answer; a confirmed miss (two misses in a row on a state
in review that is not yet due, at most once per 24 hours) is handed to the
injected ports.ConfirmedMissMove, which applies the existing bounded
auxiliary adjustment. Nothing else of the schedule changes. The later
ExtraSession (the order of extra questions, the early formal batches) keeps
one of these for its sitting; leaving the sitting forgets it.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType

from ..model import Credit, PracticeEffect, PracticeMark, StateRef
from ..ports import ConfirmedMissMove, StateSource
from ..rules.credit import CONFIRMING_MISSES, classify, may_advance, practice_answer

_UNMARKED = PracticeMark()


class PracticeSitting:
    """Marks and position of one sitting; record() settles one practice-only answer."""

    def __init__(self, states: StateSource, moves: ConfirmedMissMove):
        self._states = states
        self._moves = moves
        self._marks: dict[str, PracticeMark] = {}
        self._position = 0

    @property
    def position(self) -> int:
        """The position the next question of this sitting will have (0 for the first)."""
        return self._position

    @property
    def marks(self) -> Mapping[str, PracticeMark]:
        """Read-only marks by StateRef.key, as rules/extra.next_extra and second_segment take them."""
        return MappingProxyType(self._marks)

    def mark(self, ref: StateRef) -> PracticeMark:
        return self._marks.get(ref.key, _UNMARKED)

    def record(self, ref: StateRef, correct: bool, now: datetime, *, question: Mapping[str, object],
               answered_text: str = "", assisted: bool = False) -> PracticeEffect:
        """Settle one practice-only answer about ``ref`` at ``now``.

        NONE: correct, nothing changes. RETRY: a miss; practise it again later in
        this sitting (also for a confirmed miss that may not or could not move
        the due). ADVANCE: a confirmed miss whose next formal review was brought
        forward now (the port reported a new due). ``question`` is the question
        that was practised (the port records it with the miss).

        Raises ValueError when the answer is not practice only at ``now`` (the
        caller must settle a formal answer through the lane's rating function).
        """
        if type(correct) is not bool:
            raise ValueError("A practice answer is correct or not")
        state = self._states.view(ref, now)
        if classify(state, now) is not Credit.PRACTICE:
            raise ValueError("This answer is a formal rating, not practice")
        mark = self.mark(ref)
        can_advance = False
        if not correct and mark.wrong_streak + 1 >= CONFIRMING_MISSES:
            can_advance = may_advance(state, now, self._moves.last_advanced(ref))
        new_mark, effect = practice_answer(mark, correct, self._position, can_advance=can_advance)
        if effect is PracticeEffect.ADVANCE:
            moved = self._moves.advance(ref, question, now, answered_text=answered_text, assisted=assisted)
            if moved is None:
                effect = PracticeEffect.RETRY
        self._marks[ref.key] = new_mark
        self._position += 1
        return effect

    def note_formal(self, ref: StateRef) -> None:
        """Count a formal (early) answer of this sitting: it takes a position and clears the state's miss."""
        self._marks[ref.key] = PracticeMark(0, False, self._position)
        self._position += 1
