"""StoreExtraStates: the learned states extra practice chooses from, read from one learner store (design 12 E2).

Implements ports.ExtraStates. Read-only, no SQL here: one bulk read per lane
through its owner, then memory.

- Kana: StudyStore.scheduling_cards(aliases=True) (one SELECT of study_card,
  projected per question kind; a routed ability's kinds share the carrier
  card and its state, the probe due on the probe kind). Kept: rated
  (review_count > 0), not suspended, content available (StudyStore.
  unavailable_content_ids), not a word skill, a kind the main lane uses now
  (``kana_kinds``: the question kind itself is on, because the builder makes
  a question of that kind), the direction allowed (kana_question_policy.
  direction_allowed) and, for a routed ability, a kind already reviewed in
  that mode (mode_reviewed not False, as kana_routing.candidate_cards).
  ``due`` is the projected card's due.
- Course: LearningStore.iter_pending_questions(``course_kinds``) (pending
  questions of enabled kinds, without suspended units, unavailable content
  or units held by a retention reservation), the first per unit, of units
  already rated (review_count > 0). ``due`` is LearningStore.
  scheduled_question_due with one exposure_tables() pass for the whole read.

States inside their steps are read too: they are never offered (rules/extra
drops them), but their due is what "arranged work has come due" watches.
``in_scope`` comes from the focus scope (``scope``: lesson ids and kana entry
ids "kana:<id>:<script>", None for no scope). A point read again goes through
sources/states.StoreStates, the same reader the credit rule uses. The
learner's review filter (``serve(now)``, ReviewFilterService.current: 只做范围内,
暂不复习 and the extra-practice part, design 12 H10, E9) decides which states the
pool holds at all; ``read(now, with_part=False)`` reads it without the part
(the 加练 page counts each part from that read).
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime

from ...domain import WORD_SKILLS
from ...kana_question_policy import direction_allowed
from ..model import ExtraEntry, Lane, StateRef
from ..rules.focus import kana_member
from ..rules.review_filter import allows
from .states import StoreStates, state_view

_FOCUS = object()   # read the focus scope through the injected callable


class StoreExtraStates:
    """ports.ExtraStates over one StudyStore (and its LearningStore)."""

    def __init__(self, store, *, kana_kinds: Callable[[], Iterable[str]], course_kinds: Callable[[], Iterable[str]],
                 scope: Callable[[], frozenset[str] | None] = lambda: None, learning=None,
                 serve: Callable[..., object] = lambda now, **kwargs: None):
        self._store = store
        self._kana_kinds, self._course_kinds, self._scope = kana_kinds, course_kinds, scope
        self._serve = serve
        self._states = StoreStates(store, learning)

    def read(self, now: datetime, *, with_part: bool = True) -> list[ExtraEntry]:
        scope = self._scope()
        serve = self._serve(now, with_part=with_part)
        entries = [*self.kana(now, scope), *self.course(now, scope)]
        return entries if serve is None else [entry for entry in entries if allows(serve, entry_member(entry))]

    def any_learned(self, now: datetime) -> bool:
        """Whether read(now) holds a state: the first course unit or kana state that read() would keep (same
        tests and the same filter, with the part), without computing any due or view (brief J11 F5)."""
        serve = self._serve(now)
        kinds = tuple(self._course_kinds())
        if kinds:
            rows = self._states.learning.iter_pending_questions(kinds)
            try:
                for row in rows:
                    if row["review_count"] and row["scheduler_state_json"] and allows(serve, row["content_id"]):
                        return True
            finally:
                rows.close()
        kana = set(self._kana_kinds())
        if not kana:
            return False
        unavailable = self._store.unavailable_content_ids(now)
        for card in self._store.scheduling_cards(aliases=True):
            content_id, skill, script = card["content_id"], card["skill"], card["script"]
            if (card["review_count"] > 0 and not card["suspended"] and skill not in WORD_SKILLS and skill in kana
                    and content_id not in unavailable and card.get("mode_reviewed") is not False
                    and direction_allowed(content_id, skill, script) and allows(serve, kana_member(content_id, script))):
                return True
        return False

    def reread(self, entry: ExtraEntry, now: datetime) -> ExtraEntry | None:
        view = self._states.view(entry.ref, now)
        if view.last_rated is None:
            return None
        return ExtraEntry(entry.ref, replace(view, in_scope=entry.view.in_scope), entry.card_id)

    def kana(self, now: datetime, scope=_FOCUS) -> list[ExtraEntry]:
        """The learned kana states (one per question kind in use), in the projection's order."""
        scope = self._scope() if scope is _FOCUS else scope
        kinds = set(self._kana_kinds())
        if not kinds:
            return []
        unavailable = self._store.unavailable_content_ids(now)
        scheduler = self._store.scheduler
        result = []
        for card in self._store.scheduling_cards(aliases=True):
            content_id, skill, script = card["content_id"], card["skill"], card["script"]
            if (card["review_count"] <= 0 or card["suspended"] or skill in WORD_SKILLS or skill not in kinds
                    or content_id in unavailable or card.get("mode_reviewed") is False
                    or not direction_allowed(content_id, skill, script)):
                continue
            ref = StateRef.kana(content_id, skill, script)
            view = state_view(scheduler, ref.key, json.loads(card["scheduler_state_json"]), card["due_at_utc"], now,
                              in_scope=scope is None or kana_member(content_id, script) in scope)
            result.append(ExtraEntry(ref, view, str(card["card_id"])))
        return result

    def course(self, now: datetime, scope=_FOCUS) -> list[ExtraEntry]:
        """The learned course units with a pending question of an enabled kind, in pending order."""
        scope = self._scope() if scope is _FOCUS else scope
        kinds = tuple(self._course_kinds())
        if not kinds:
            return []
        learning = self._states.learning
        tables = learning.exposure_tables(now)
        scheduler = self._store.scheduler
        seen, result = set(), []
        for row in learning.iter_pending_questions(kinds):
            if row["unit_id"] in seen or not row["review_count"] or not row["scheduler_state_json"]:
                continue
            seen.add(row["unit_id"])
            ref = StateRef.course(row["content_id"], row["ability"])
            due, early = learning.serviceable_due(dict(row), json.loads(row["snapshot_json"]), now, tables=tables)
            view = state_view(scheduler, ref.key, json.loads(row["scheduler_state_json"]), due, now,
                              in_scope=scope is None or row["content_id"] in scope, early=early)
            result.append(ExtraEntry(ref, view))
        return result


def entry_member(entry: ExtraEntry) -> str:
    """The scope member of a pool entry: the lesson of a course unit, the kana entry of a kana state."""
    ref = entry.ref
    return kana_member(ref.content_id, ref.script) if ref.lane is Lane.KANA else ref.content_id
