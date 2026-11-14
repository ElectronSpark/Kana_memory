"""PracticeCredit: what one answer counts as, asked in one place (design 12 E3, M3).

The application asks this service whenever it settles an answer that might
not be scheduled work: the kana answer settlement in a reinforce batch, the
mastery check's early formal settlement and, later, extra practice. The
rule itself is rules/credit.classify; the state it is applied to comes from a
ports.StateSource, so tests can hand in states held in memory.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from ..model import Credit, StateRef, StateView
from ..ports import StateSource
from ..rules.credit import classify


class PracticeCredit:
    """Formal (due), formal (early) or practice only, for a course unit or a kana state."""

    def __init__(self, states: StateSource):
        self._states = states

    def view(self, ref: StateRef, now: datetime) -> StateView:
        """The state as the rule sees it at ``now`` (read through the port)."""
        return self._states.view(ref, now)

    def classify(self, ref: StateRef, now: datetime) -> Credit:
        """What an answer about ``ref`` given at ``now`` counts as."""
        return classify(self._states.view(ref, now), now)

    def course(self, content_id: str, ability: str, now: datetime) -> Credit:
        """What an answer about the course unit (content, ability) counts as at ``now``."""
        return self.classify(StateRef.course(content_id, ability), now)

    def kana(self, content_id: str, skill: str, script: str, now: datetime) -> Credit:
        """What an answer about the kana state (content, skill, script) counts as at ``now``."""
        return self.classify(StateRef.kana(content_id, skill, script), now)

    def kana_answer(self, question: object, now: datetime) -> Credit:
        """The decision where a learner's kana answer is settled.

        ``question`` is the kana question that was answered: a domain.Question
        or its dict (content_id, kind, script). ``now`` is the instant of the
        answer (the one the rating would use). Word-skill questions are not a
        scheduling state: they come back FORMAL_DUE, meaning "settle as today".
        """
        content_id, kind, script = (_field(question, name) for name in ("content_id", "kind", "script"))
        return self.kana(content_id, kind, script, now)

    @staticmethod
    def is_formal(credit: Credit) -> bool:
        """True for both formal kinds: the answer goes through the lane's rating function."""
        return credit is not Credit.PRACTICE


def _field(question: object, name: str) -> str:
    value = question.get(name) if isinstance(question, Mapping) else getattr(question, name, None)
    if not isinstance(value, str) or not value:
        raise ValueError("A kana question needs its content, kind and script")
    return value
