"""StorePracticeMoves: the confirmed-miss move of practice over a learner store (design 12 E3, 10 (6)).

Implements ports.ConfirmedMissMove for both lanes through the owners of the
bounded auxiliary adjustment; no SQL and no adjustment rule here:

- advance: LearningEvidenceStore.record_auxiliary writes one auxiliary
  evidence row (source course or kana, role auxiliary, the opportunity this
  answer alone, weight AUXILIARY_STEP) and, when learning_evidence's bounded
  adjustment applies, one adjustment row and the moved due, in its own
  transaction. The course unit is the question's (target, ability); the kana
  card is the scheduling card StoreStates reads (the carrier of an active
  kana ability route), and the evidence ability is "<script>:<skill>" or,
  routed, "<script>:<route ability>", as a formal kana answer records it.
- last_advanced: the latest effective adjustment of that unit or card that
  moved its due earlier, of any source (StudyQueries.card_adjustment_moves).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import uuid4

from ..model import Lane, StateRef
from .states import StoreStates

EVENT_PREFIX = "practice:"   # evidence event ids of practice misses; source and role carry the meaning


class StorePracticeMoves:
    """ports.ConfirmedMissMove over one StudyStore."""

    def __init__(self, store, states: StoreStates | None = None):
        self._store = store
        self._states = states or StoreStates(store)

    def _card(self, ref: StateRef) -> tuple[str, str] | None:
        """(card kind, card id) of the adjustment records, or None when the state has no card or unit."""
        if ref.lane is Lane.KANA:
            card = self._states.kana_card(ref)
            return ("kana", card["card_id"]) if card else None
        unit = self._states.course_unit(ref)
        return ("course", unit["unit_id"]) if unit else None

    def last_advanced(self, ref: StateRef) -> datetime | None:
        card = self._card(ref)
        if card is None:
            return None
        moves = [datetime.fromisoformat(row["occurred_at_utc"])
                 for row in self._store.queries.card_adjustment_moves(*card)
                 if datetime.fromisoformat(row["after_due_at_utc"]) < datetime.fromisoformat(row["before_due_at_utc"])]
        return max(moves, default=None)

    def advance(self, ref: StateRef, question: Mapping[str, object], now: datetime, *,
                answered_text: str = "", assisted: bool = False) -> datetime | None:
        from ...learning_evidence import LearningEvidenceStore, reviewed_question
        result = {"correct": False, "outcome": "incorrect", "assisted": bool(assisted), "answered_text": answered_text}
        event_id = EVENT_PREFIX + uuid4().hex
        if ref.lane is Lane.KANA:
            card = self._states.kana_card(ref)
            if card is None:
                return None
            ability = ref.script + ":" + card.get("ability", ref.ability)
            evidence = {"target_id": ref.content_id, "ability": ability, "kind": ref.ability,
                        "revision": card["content_version"], "prompt": question.get("prompt", ""),
                        "answer": question.get("answer", "")}
            row = LearningEvidenceStore(self._store).record_auxiliary(
                event_id, evidence, result, source="kana", trusted=True, now=now, kana_card_id=card["card_id"])
        else:
            if (question.get("target_id"), question.get("ability")) != (ref.content_id, ref.ability):
                raise ValueError("The practised question belongs to another course unit")
            row = LearningEvidenceStore(self._store).record_auxiliary(
                event_id, dict(question), result, source="course", trusted=reviewed_question(dict(question)), now=now)
        return datetime.fromisoformat(row["after_due_at_utc"]) if row else None
