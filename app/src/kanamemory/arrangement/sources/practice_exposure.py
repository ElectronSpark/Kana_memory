"""StorePracticeExposure: what a practice answer that showed its answer registers (design 12 E5, 10 (6), AC17).

Implements ports.PracticeExposure through the lanes' owners; no SQL here.
Each lane registers exactly what its existing exposure guard reads, plus one
evidence row "见过" (role exposure, weight 0, event id "practice:..."):

- course: LearningStore.record_practice_exposure — the lesson's feedback
  exposure stamp, which effective_question_due turns into the existing short
  wait (FEEDBACK_COOLDOWN_SECONDS) before a formal question of any ability of
  that lesson, and the evidence row.
- kana: StudyStore.record_practice_exposure — the evidence row, which
  StudyStore.feedback_wait_times reads like a formal answer's finalization:
  the kana's directions in that script wait the same 60 seconds.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import uuid4

from ..model import Lane, StateRef
from .practice_moves import EVENT_PREFIX


class StorePracticeExposure:
    """ports.PracticeExposure over one StudyStore (``learning``: its LearningStore, made on first course use)."""

    def __init__(self, store, learning=None):
        self._store = store
        self._learning = learning

    def record(self, ref: StateRef, question: Mapping[str, object], now: datetime, *, correct: bool,
               answered_text: str = "", assisted: bool = False, event_id: str | None = None) -> None:
        result = {"outcome": "correct" if correct else "incorrect", "correct": bool(correct),
                  "assisted": bool(assisted), "answered_text": answered_text}
        event_id = event_id or EVENT_PREFIX + uuid4().hex
        if ref.lane is Lane.KANA:
            self._store.record_practice_exposure(event_id, ref.content_id, ref.ability, ref.script,
                                                 question, result, now=now)
            return
        if (question.get("target_id"), question.get("ability")) != (ref.content_id, ref.ability):
            raise ValueError("The practised question belongs to another course unit")
        if self._learning is None:
            from ...learning_store import LearningStore
            self._learning = LearningStore(self._store)
        self._learning.record_practice_exposure(event_id, question, result, now=now)
