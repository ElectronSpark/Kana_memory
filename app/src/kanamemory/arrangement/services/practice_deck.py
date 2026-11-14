"""PracticeDeck: practice-only questions, built in memory and judged on the spot (design 12 E5, 10 (6)).

Memory only. A deck is a list of DeckItems (the state each question
practises, and the question when it is already built). Before serving an
item it asks PracticeCredit: an item whose state has become formal work at
that moment (due, a step that has come due, or 24 hours since its last
rating) is left out, because the deck never rates and the schedule serves it.
A question is built by the lane's existing builder (ports.DeckQuestions) when
the item has none and judged by the lane's existing judge.

An answer writes no scheduling record and never touches a unit's pending
question. It registers the exposure (ports.PracticeExposure: one evidence row
"见过" and what the lane's exposure guard reads) and is folded into the
sitting's memory (PracticeSitting.record), whose confirmed miss is the only
schedule change. A miss comes back later in the deck, after REPEAT_GAP other
questions when there are that many, at most MAX_RETRIES times per item. A
question about no scheduling state (a kana word skill) is judged only.

The deck does not resume: leaving ends it and nothing of it is stored.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from datetime import datetime

from ..model import DeckAnswerMode, DeckCard, DeckItem, DeckOutcome, PracticeEffect
from ..ports import DeckQuestions, PracticeExposure
from ..rules.extra import REPEAT_GAP
from .practice import PracticeCredit
from .practice_sitting import PracticeSitting

MAX_RETRIES = 2   # times one missed item comes back in a deck


class PracticeDeck:
    """One deck: next() serves a card, answer() or self_check() settles it."""

    def __init__(self, items: Iterable[DeckItem], *, credit: PracticeCredit, sitting: PracticeSitting,
                 questions: DeckQuestions, exposure: PracticeExposure, gap: int = REPEAT_GAP):
        self._queue: deque[tuple[DeckItem, int]] = deque((item, 0) for item in items)
        self._credit, self._sitting = credit, sitting
        self._questions, self._exposure = questions, exposure
        self._gap = gap
        self._current: tuple[DeckCard, int] | None = None
        self._answered = True
        self.served = 0          # cards served (a card asked again after an unscored answer counts once)
        self.correct = 0         # scored answers that were right
        self.left_out = 0        # items left out because their state had become formal work

    @property
    def pending(self) -> int:
        """Items still to serve (a retry waiting in the deck included)."""
        return len(self._queue)

    @property
    def current(self) -> DeckCard | None:
        return self._current[0] if self._current else None

    def next(self, now: datetime) -> DeckCard | None:
        """The card to show at ``now``: the current one while it is unanswered, else the next; None when done."""
        if self._current is not None and not self._answered:
            return self._current[0]
        self._current = None
        while self._queue:
            item, retries = self._queue.popleft()
            if self._questions.scheduled(item.ref) and PracticeCredit.is_formal(self._credit.classify(item.ref, now)):
                self.left_out += 1
                continue
            question = item.question if item.question is not None else self._questions.build(item.ref, now)
            if question is None:
                continue
            self.served += 1
            card = DeckCard(item, question, self._questions.mode(item.ref, question),
                            self._questions.texts(item.ref, question), self.served)
            self._current, self._answered = (card, retries), False
            return card
        return None

    def answer(self, answer: str, now: datetime, *, revealed: bool = False) -> DeckOutcome:
        """Judge a typed or chosen answer (``revealed``: the learner asked for the answer; a miss, assisted)."""
        card = self._open_card()
        if revealed:
            return self._settle(False, now, answered_text="", assisted=True)
        correct, message = self._questions.judge(card.item.ref, card.question, answer)
        if correct is None:
            return DeckOutcome(card, None, None, message)
        return self._settle(correct, now, answered_text=answer, message=message)

    def self_check(self, knew: bool, now: datetime) -> DeckOutcome:
        """Settle a self-check card with the learner's own report."""
        card = self._open_card()
        if card.mode is not DeckAnswerMode.SELF_CHECK:
            raise ValueError("Only a self-check question is settled by the learner's report")
        return self._settle(bool(knew), now, answered_text="")

    def _open_card(self) -> DeckCard:
        if self._current is None or self._answered:
            raise ValueError("No deck question is waiting for an answer")
        return self._current[0]

    def _settle(self, correct: bool, now: datetime, *, answered_text: str, assisted: bool = False,
                message: str = "") -> DeckOutcome:
        card, retries = self._current
        ref, question = card.item.ref, card.question
        effect = PracticeEffect.NONE if correct else PracticeEffect.RETRY
        if self._questions.scheduled(ref):
            if PracticeCredit.is_formal(self._credit.classify(ref, now)):
                # It became formal work while the card was open: nothing is recorded, the schedule serves it.
                self._answered = True
                return DeckOutcome(card, correct, None, message)
            # A failed write (a busy database) leaves the card open for the same answer again.
            self._exposure.record(ref, question, now, correct=correct, answered_text=answered_text, assisted=assisted)
            effect = self._sitting.record(ref, correct, now, question=question,
                                          answered_text=answered_text, assisted=assisted)
        self._answered = True
        if correct:
            self.correct += 1
        elif retries < MAX_RETRIES:
            position = min(len(self._queue), self._gap)
            self._queue.insert(position, (DeckItem(ref, card.item.question), retries + 1))
        return DeckOutcome(card, correct, effect, message)