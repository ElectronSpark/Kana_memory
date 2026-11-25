"""DueReviews: the due reviews "继续学习" can serve, as far as the question kinds go (design 12 UI6, AC13).

One job: for "重点之外到期 N 题", list each due review that the continue
action can serve under the learner's kinds, as the scope member it belongs
to (a lesson content id, or a kana entry id "kana:<id>:<script>",
rules/focus.kana_member). Read-only, no SQL here; every predicate is its
owner's:

- Course: each scored, unsuspended ability due at ``now`` (StudyQueries.
  review_unit_queue_rows) whose content is not unavailable. An ability with
  a pending question counts when LearningStore.iter_pending_questions under
  the enabled kinds lists one of its questions (its kind enabled, not
  paused by a retention reservation, the content available: the rows the
  home counts read); one without a pending question counts when the
  enabled kinds plan a question for its lesson: the caller hands in the
  home preview's planning (``plannable``, the "planned" pairs of
  home.previews.course_preview); without it, when an enabled
  kind asks for its ability (learning_questions.KIND_ABILITIES), which can
  count an ability whose lesson has no question of that kind.
- Kana: each card StudyStore.get_due lists at ``now`` (not suspended, not
  unavailable, its direction allowed, no word card) that a kind in force
  serves (rules/depth.served: any kind of its ability is on).

It is a count of due work, not of questions answerable this minute: an
anti-leak wait of a few minutes, a round's admission limit or a queue not
yet built does not remove an item, and a due review held back by an unmet
prerequisite, by material under review or by missing Japanese input is
still listed.
"""
from __future__ import annotations

from collections.abc import Collection, Iterable
from datetime import datetime

from ..rules.depth import served
from ..rules.focus import kana_member


class DueReviews:
    """Due reviews of one learner store that the continue action can serve, by scope member."""

    def __init__(self, store):
        self._store = store

    def course(self, now: datetime, enabled_kinds: Iterable[str], *, unavailable: Iterable[str] = (),
               plannable: Collection[tuple[str, str]] | None = None) -> list[str]:
        """The content id of each due course ability an enabled kind serves (one entry per ability).

        ``plannable``: the (content id, ability) pairs without a pending
        question that get one planned under the enabled kinds; None when the
        caller has no planning at hand (then the ability alone decides).
        """
        kinds = tuple(enabled_kinds)
        if not kinds:
            return []
        from .recent import RecentFirsts, serviceable
        firsts = RecentFirsts(self._store)

        def due(row) -> bool:
            # Design 12 N2: the queue's reading of the due (a recent unit's short interval after one day).
            value = serviceable(row, row["due_at_utc"], firsts, now)[0]
            return bool(value) and datetime.fromisoformat(value) <= now

        candidates = list(self._store.queries.review_unit_queue_rows(exclude=unavailable))
        firsts.prime(row["unit_id"] for row in candidates)
        rows = [row for row in candidates if row["due_at_utc"] and due(row)]
        if not rows:
            return []
        from ...learning_questions import KIND_ABILITIES
        abilities = {KIND_ABILITIES[kind] for kind in kinds if kind in KIND_ABILITIES}
        visible = set()
        if any(row["queued"] for row in rows):
            from ...learning_store import LearningStore
            visible = {row["unit_id"] for row in LearningStore.reader(self._store).iter_pending_questions(kinds)}
        def unqueued(row) -> bool:
            if plannable is None:
                return row["ability"] in abilities
            return (row["content_id"], row["ability"]) in plannable
        return [row["content_id"] for row in rows if (row["unit_id"] in visible if row["queued"] else unqueued(row))]

    def kana(self, now: datetime, kinds: Iterable[str]) -> list[str]:
        """The kana entry id of each kana card due at ``now`` that one of ``kinds`` serves (one entry per card)."""
        kinds = tuple(kinds)
        return [kana_member(card["content_id"], card["script"]) for card in self._store.get_due(now=now)
                if served(card["skill"], kinds)]
