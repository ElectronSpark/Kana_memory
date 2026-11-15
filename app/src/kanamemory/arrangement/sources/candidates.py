"""DispatchCandidates: the heads one "继续学习" dispatch could serve (design 12 B3, WP-B3).

Implements ports.CandidateSource for ONE dispatch. It is built from what
the continue action's decision (continue_runtime.decide) has already computed: each lane's
head and its frozen round priority. A review candidate is a lane head whose
frozen priority tier is exactly 3 (an ordinary due review). The new-content
candidate is found only when asked: first_teaching_dispatch.share_candidate
(the "a block is due" branch of the first-teaching service, with the
two-unscored-checks stop), and none while a block this round promoted is
unfinished and can go on within the bridge wait ("one block at a time").
Reads only, apart from clearing the promotion marker of a block that no
longer holds the slot. No SQL: the store's query owners, the first-teaching
probes and practice_round answer.
"""
from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime

from ..model import Candidate, Unit
from ..rules.focus import kana_member

REVIEW_TIER = 3

_LANE_UNIT = {"course": Unit.COURSE, "kana": Unit.KANA}


def _everything(unit: Unit, ref) -> bool:
    return True


def head_member(lane: str, head: dict) -> str | None:
    """The focus-scope identity of a lane head: a course head's content id, a kana head's kana_member."""
    if lane == "course":
        return (head.get("content") or {}).get("id")
    question = head.get("question")
    if question is None:
        return None
    get = question.get if isinstance(question, dict) else (lambda field: getattr(question, field, None))
    content_id, script = get("content_id"), get("script")
    return kana_member(content_id, script) if content_id and script else None


def head_in_scope(lane: str, head: dict | None, scope_contents: Collection[str] | None) -> bool:
    """Whether a lane head lies inside the focus scope (always without a scope)."""
    if scope_contents is None:
        return True
    return head is not None and head_member(lane, head) in scope_contents


class ScopeMembership:
    """DispatchCandidates' ``in_scope(unit, ref)`` for a scope content set; ref is (lane, head)."""

    def __init__(self, scope_contents: Collection[str]):
        self._scope = scope_contents

    def __call__(self, unit: Unit, ref) -> bool:
        lane, head = ref
        return head_in_scope(lane, head, self._scope)


@dataclass(frozen=True)
class LaneHead:
    """One lane's head as dispatch computed it, with its frozen round priority (None: not served)."""

    head: dict
    priority: tuple | None


class DispatchCandidates:
    """ports.CandidateSource over one dispatch of the unified study path.

    ``scope_contents`` is the learner's focus scope (FocusService.
    scope_contents: lesson content ids and kana_member ids), None for none:
    it decides ``in_scope`` of each candidate (ScopeMembership) unless an
    explicit ``in_scope(unit, ref)`` is given, and the new-content probe
    offers fresh candidates inside it first. Without either everything is
    in scope. ``skipped`` holds the course contents whose teaching the
    learner passed over in this run. ``ref`` of every candidate is (lane, head).
    """

    def __init__(self, store, learning, *, pending, kana: LaneHead | None, course: LaneHead | None,
                 enabled_kinds, kana_kinds, now: datetime, projection=None, skipped=frozenset(),
                 in_scope: Callable[[Unit, object], bool] | None = None, memo=None,
                 scope_contents: Collection[str] | None = None):
        self._store = store
        self._learning = learning
        self._session = pending
        self._heads = {"course": course, "kana": kana}
        self._enabled_kinds = enabled_kinds
        self._kana_kinds = kana_kinds
        self._now = now
        self._projection = projection
        self._skipped = frozenset(skipped)
        self._scope = None if scope_contents is None else frozenset(scope_contents)
        self._in_scope = in_scope or (_everything if self._scope is None else ScopeMembership(self._scope))
        self._memo = memo     # services.probe_memo.ProbeMemo of the store, or None (always probe)
        self._new: list = []  # [candidate or None] once probed

    def _candidate(self, lane: str, head: dict, unit: Unit | None = None) -> Candidate:
        unit = unit or _LANE_UNIT[lane]
        ref = (lane, head)
        return Candidate(unit, self._in_scope(unit, ref), ref)

    def review_candidates(self) -> tuple[Candidate, ...]:
        return tuple(self._candidate(lane, entry.head) for lane, entry in self._heads.items()
                     if entry is not None and entry.priority is not None and entry.priority[0] == REVIEW_TIER)

    def new_candidate(self) -> Candidate | None:
        if not self._new:
            self._new.append(None if self.block_open() else self._probe())
        return self._new[0]

    def without_new(self) -> ReviewsOnly:
        """The same dispatch with new content withheld (its head could not be promoted)."""
        return ReviewsOnly(self)

    def promotion_refused(self) -> None:
        """The new head could not be promoted: this round does not probe for new content again."""
        if self._memo is not None:
            self._memo.refused(self._round(), self._now)

    def _round(self):
        from ...practice_round import round_id
        return round_id(self._store)

    def _probe(self) -> Candidate | None:
        from ...first_teaching_dispatch import share_probe
        if self._memo is not None and self._memo.futile(self._round(), self._now):
            return None
        probe = share_probe(self._store, self._learning, enabled_kinds=self._enabled_kinds,
                            kana_kinds=self._kana_kinds, now=self._now, projection=self._projection,
                            skip_teaching_ids=self._skipped, **({} if self._scope is None else {"scope": self._scope}))
        found = probe.found
        if found is None:
            if self._memo is not None and not probe.waiting:
                # A block inside its bridge wait is the candidate within a minute: probe again.
                self._memo.found_nothing(self._round(), self._now)
            return None
        lane, head = found
        if (lane == "course" and head.get("type") == "instruction"
                and head.get("content", {}).get("id") in self._skipped):
            return None
        return self._candidate(lane, head, Unit.NEW)

    def block_open(self) -> bool:
        """Whether the block this round promoted still holds the slot; otherwise its marker is cleared."""
        from ...practice_round import clear_promoted, promoted
        marker = promoted(self._store)
        if marker is None:
            return False
        if self._goes_on(marker):
            return True
        clear_promoted(self._store)
        return False

    def _goes_on(self, marker: tuple) -> bool:
        """Unfinished AND servable now or within the bridge wait (design 12 B3).

        A result (also an unresolved one), a skip, a question that is gone, or a
        wait longer than the bridge (its kind switched off, a prerequisite, a
        deferral or retry wait) ends the hold.
        """
        from ...first_teaching_dispatch import course_block_continues, kana_block_continues
        if marker[0] == "course":
            content_id = self._store.teaching_queries.unanswered_course_question(marker[1])
            return (content_id is not None and content_id not in self._skipped
                    and course_block_continues(self._learning, marker[1], enabled_kinds=self._enabled_kinds,
                                               now=self._now, projection=self._projection))
        from ...practice_round import promoted_native_index
        from ...review_dispatch import kana_resume_priority
        _, session_id, key = marker
        pending = self._session
        if not isinstance(pending, dict) or pending.get("completed") or pending.get("session_id") != session_id:
            return False
        index = promoted_native_index(self._store, session_id, key)
        # An answered occurrence lies before the session's current index.
        if index is None or index < pending["question_index"]:
            return False
        if kana_resume_priority(pending.get("state")) is not None:
            return True   # a kana item is open on screen; decide once it is answered
        return kana_block_continues(self._store, self._kana_kinds, key, self._now)


class ReviewsOnly:
    """ports.CandidateSource: the review candidates of one dispatch, with new content withheld."""

    def __init__(self, source: DispatchCandidates):
        self._source = source

    def review_candidates(self) -> tuple[Candidate, ...]:
        return self._source.review_candidates()

    def new_candidate(self) -> None:
        return None
