"""StoreConsolidation: the course states taught today, as rules/consolidation needs them (design 12 N5).

Read-only, no SQL here: everything comes from what is stored already, so a
restart (and the start's archive of pending course questions) loses nothing.

- Teaching marks: TeachingQueries.instruction_rows (a lesson's guide, or an
  added ability's own guide: guided_at_utc) of lessons started under the
  probe-first flow (learning_store._probe_era) within TAUGHT_WINDOW. A
  lesson's mark covers its units; an added ability's mark covers that unit.
- A unit counts only when its first formal rating (RecentFirsts) is not
  before the mark: an ability whose probe passed before the lesson was
  taught is recent but not "taught today".
- The unit's due is the queue's (LearningStore.serviceable_due, with its
  pending question); ``in_step`` and ``last_rated`` come from its scheduler
  state.
- ``repeats`` and the last practice answer: TeachingQueries.
  consolidation_exposures (the evidence rows a consolidation deck writes,
  event ids practice:consolidation:...) since the mark.
"""
from __future__ import annotations

import json
from datetime import datetime

from fsrs import Card, State

from ..model import ConsolidationFacts, StateRef
from ..rules.consolidation import TAUGHT_WINDOW
from .recent import RecentFirsts, last_rated


def _instant(value) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class StoreConsolidation:
    """The facts of every course state whose lesson was taught today, read from one learner store."""

    def __init__(self, store, learning=None):
        self._store = store
        self._learning = learning

    @property
    def learning(self):
        if self._learning is None:
            from ...learning_store import LearningStore
            self._learning = LearningStore(self._store)
        return self._learning

    def _marks(self, now: datetime) -> dict[tuple[str, str | None], datetime]:
        """{(content id, added ability or None): the latest probe-first teaching mark within the window}."""
        from ...learning_store import _probe_era
        queries = self._store.teaching_queries
        marks: dict[tuple[str, str | None], datetime] = {}
        for row in queries.instruction_rows(queries.ability_states()):
            at = _instant(row["guided_at_utc"])
            if at is None or not (now - TAUGHT_WINDOW < at <= now) or not _probe_era(row["checkpoint_json"]):
                continue
            key = (row["content_id"], dict(row).get("instruction_scope"))
            marks[key] = max(at, marks.get(key, at))
        return marks

    def facts(self, now: datetime) -> list[ConsolidationFacts]:
        marks = self._marks(now)
        if not marks:
            return []
        learning = self.learning
        unavailable = learning.availability().unavailable_ids
        firsts = RecentFirsts(self._store)
        since = min(marks.values())
        exposures: dict[tuple[str, str], list[datetime]] = {}
        for row in self._store.teaching_queries.consolidation_exposures(since):
            exposures.setdefault((row["target_id"], row["ability"]), []).append(_instant(row["at"]))
        result = []
        for content_id in sorted({content for content, _ in marks}):
            if content_id in unavailable:
                continue
            item = learning.get_item(content_id)
            for unit in (item or {}).get("units") or ():
                if unit is None or unit["suspended"] or not unit["review_count"]:
                    continue
                taught = marks.get((content_id, unit["ability"])) or marks.get((content_id, None))
                if taught is None:
                    continue
                first = firsts.first(unit["unit_id"])
                if first is None or first < taught:
                    continue   # a passed probe (rated before the teaching) is recent but not taught today
                result.append(self._facts(unit, taught, exposures.get((content_id, unit["ability"]), []), now))
        return result

    def _facts(self, unit: dict, taught: datetime, answers: list[datetime], now: datetime) -> ConsolidationFacts:
        learning = self.learning
        question_id = self._store.queries.first_pending_question_id(unit["unit_id"])
        question = learning.get_question(question_id) if question_id else None
        due = learning.scheduled_question_due(unit, question or {}, now)
        state = unit.get("scheduler_state") or json.loads(unit.get("scheduler_state_json") or "{}")
        card = Card.from_dict(state) if state else None
        since = [at for at in answers if at >= taught]
        last = last_rated(unit)
        latest = max([at for at in (last, *since) if at is not None], default=None)
        return ConsolidationFacts(StateRef.course(unit["content_id"], unit["ability"]), taught, last,
                                  _instant(due), in_step=bool(card and card.last_review is not None
                                                              and card.state in (State.Learning, State.Relearning)),
                                  repeats=len(since), last_exposure=latest)
