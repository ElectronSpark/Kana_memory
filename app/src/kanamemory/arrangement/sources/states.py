"""StoreStates: the scheduling state behind an answer, read from a learner store (design 12 E3, WP-E).

Implements ports.StateSource for both lanes; read-only, no SQL here.

- Kana state (content, skill, script): the card the queue schedules,
  StudyStore.get_scheduling_card (the ability carrier when a kana ability
  route is active, with its probe due). ``due`` is that card's due_at_utc.
  A word skill is no scheduling state and has no card: it reads as never
  rated.
- Course unit (content, ability): LearningStore.get_unit. ``due`` is the
  scheduling due only: LearningStore.scheduled_question_due of the unit with
  its pending question, if any (the unit's due including the bounded
  auxiliary adjustment, a free-retrieval probe, and an earlier active
  confirmation). The independence guards (floors, the feedback, tutor,
  retention and teaching exposure waits) are left out: they delay serving
  a question but do not make a due unit "not due"; the mastery check and
  the queues keep their own guards.

``last_rated`` is the scheduler state's last_review: only a formal rating
sets it (observations, unscored attempts, exposures and auxiliary
adjustments do not), and it is the instant the scheduler library measures
its 24 hours from. ``in_step``: the scheduler state is Learning or
Relearning. ``retrievability``: Scheduler.get_card_retrievability.
"""
from __future__ import annotations

from datetime import datetime

from fsrs import Card, State

from ...domain import WORD_SKILLS
from ..model import Lane, StateRef, StateView


class StoreStates:
    """ports.StateSource over one StudyStore (and its LearningStore for the course lane)."""

    def __init__(self, store, learning=None):
        self._store = store
        self._learning = learning

    @property
    def learning(self):
        if self._learning is None:
            from ...learning_store import LearningStore
            self._learning = LearningStore(self._store)
        return self._learning

    def view(self, ref: StateRef, now: datetime) -> StateView:
        if ref.lane is Lane.KANA:
            return self._kana(ref, now)
        return self._course(ref, now)

    def kana_card(self, ref: StateRef) -> dict | None:
        """The scheduling card of a kana state, or None (never rated, or a word skill)."""
        if ref.ability in WORD_SKILLS:
            return None
        return self._store.get_scheduling_card(ref.content_id, ref.ability, ref.script)

    def course_unit(self, ref: StateRef) -> dict | None:
        return self.learning.get_unit(ref.content_id, ref.ability)

    def _kana(self, ref: StateRef, now: datetime) -> StateView:
        card = self.kana_card(ref)
        if card is None or not card["review_count"]:
            return StateView(ref.key, None, None)
        return state_view(self._store.scheduler, ref.key, card["scheduler_state"], card["due_at_utc"], now)

    def _course(self, ref: StateRef, now: datetime) -> StateView:
        unit = self.course_unit(ref)
        if unit is None or not unit["review_count"] or not unit["scheduler_state"]:
            return StateView(ref.key, None, None)
        question_id = self._store.queries.first_pending_question_id(unit["unit_id"])
        question = self.learning.get_question(question_id) if question_id else None
        due = self.learning.scheduled_question_due(unit, question or {}, now)
        return state_view(self._store.scheduler, ref.key, unit["scheduler_state"], due, now)


def state_view(scheduler, key: str, state: dict, due: str | None, now: datetime, *,
               in_scope: bool = True) -> StateView:
    """The StateView of one scheduler state (a card's or unit's scheduler_state) with the lane's ``due``.

    ``scheduler``: the store's fsrs Scheduler (StudyStore.scheduler), whose
    retrievability at ``now`` the view carries. Shared by the point reads
    here and the bulk read of extra practice (sources/extra_states).
    """
    card = Card.from_dict(state)
    return StateView(
        key,
        datetime.fromisoformat(due) if due else None,
        card.last_review,
        in_step=card.last_review is not None and card.state in (State.Learning, State.Relearning),
        retrievability=(scheduler.get_card_retrievability(card, current_datetime=now)
                        if card.last_review is not None else None),
        in_scope=in_scope)
