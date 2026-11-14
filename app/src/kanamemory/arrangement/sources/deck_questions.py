"""StoreDeckQuestions: ports.DeckQuestions over a learner store, one strategy per lane (design 12 E5).

Each lane keeps its own builder, judge and display (sources/deck_kana,
sources/deck_course); this class only picks the lane of a state. Nothing here
writes.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from ..model import DeckAnswerMode, DeckTexts, Lane, StateRef
from .deck_course import CourseDeckLane
from .deck_kana import KanaDeckLane


class StoreDeckQuestions:
    """ports.DeckQuestions: the kana lane and the course lane side by side."""

    def __init__(self, kana: KanaDeckLane, course: CourseDeckLane):
        self._lanes = {Lane.KANA: kana, Lane.COURSE: course}

    def _lane(self, ref: StateRef):
        return self._lanes[ref.lane]

    def scheduled(self, ref: StateRef) -> bool:
        return self._lane(ref).scheduled(ref)

    def build(self, ref: StateRef, now: datetime) -> Mapping[str, object] | None:
        return self._lane(ref).build(ref, now)

    def mode(self, ref: StateRef, question: Mapping[str, object]) -> DeckAnswerMode:
        return self._lane(ref).mode(ref, question)

    def texts(self, ref: StateRef, question: Mapping[str, object]) -> DeckTexts:
        return self._lane(ref).texts(ref, question)

    def judge(self, ref: StateRef, question: Mapping[str, object], answer: str) -> tuple[bool | None, str]:
        return self._lane(ref).judge(ref, question, answer)
