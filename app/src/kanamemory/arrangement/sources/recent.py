"""RecentFirsts: the first formal rating of course units, and the queue's serviceable due of one unit (design 12 N1, N2).

Read-only, no SQL here: TeachingQueries.course_earliest_answers (one index
seek per unit, the instant the study clock's first ratings use) answers
"when was this unit first formally rated"; one object memorises what it read
for one queue pass (LearningStore keeps one with a pass's exposure tables).
serviceable() applies rules/recent.serviceable_due to a unit row and a due
the caller computed: the one reading of "is it due" for the course queue,
the home counts, the records page and extra practice.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime

from ..rules.recent import is_recent, serviceable_due, served_early


def _instant(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


class RecentFirsts:
    """{unit_id: first formal rating or None}, read on demand and kept for one pass."""

    def __init__(self, store):
        self._store = store
        self._known: dict[str, datetime | None] = {}

    def prime(self, unit_ids: Iterable[str]) -> None:
        """Read the units not yet known in one query."""
        missing = sorted({unit_id for unit_id in unit_ids if unit_id and unit_id not in self._known})
        if not missing:
            return
        for unit_id in missing:
            self._known[unit_id] = None
        for row in self._store.teaching_queries.course_earliest_answers(missing):
            self._known[row["unit_id"]] = _instant(row["at"])

    def first(self, unit_id: str) -> datetime | None:
        if unit_id not in self._known:
            self.prime((unit_id,))
        return self._known.get(unit_id)

    def recent(self, unit_id: str, now: datetime) -> bool:
        return is_recent(self.first(unit_id), now)


def last_rated(unit: Mapping) -> datetime | None:
    """A unit's last formal rating: its scheduler state's last_review (only a formal rating sets it)."""
    unit = dict(unit)
    state = unit.get("scheduler_state")
    if state is None:
        raw = unit.get("scheduler_state_json")
        state = json.loads(raw) if raw else {}
    return _instant((state or {}).get("last_review"))


def serviceable(unit: Mapping, due: str | None, firsts: RecentFirsts, now: datetime) -> tuple[str | None, bool]:
    """(the serviceable due of ``unit`` as an ISO string, whether it is due at ``now`` only by that reading).

    ``due``: the due the caller schedules the unit at. The interval is
    measured on the unit's stored due (``due_at_utc``) from its last formal
    rating; the result is never later than ``due``.
    """
    unit = dict(unit)
    when, stored, last = _instant(due), _instant(unit.get("due_at_utc")), last_rated(unit)
    if when is None or stored is None or last is None or not unit.get("unit_id"):
        return due, False
    first = firsts.first(unit["unit_id"])
    earliest = serviceable_due(stored, last, first, now)
    if earliest is None or earliest >= when:
        return due, False
    return earliest.isoformat(), served_early(when, earliest, now)
