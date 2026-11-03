"""Read-only availability of an unfinished queue, separate from its history."""
from dataclasses import dataclass
from datetime import datetime

from .domain import SKILLS, WORD_SKILLS, WORD_KIND_PAUSE, _question_from_snapshot, _utc
from .kana_question_policy import question_allowed


@dataclass(frozen=True)
class QueueAvailability:
    ready_count: int = 0
    waiting_count: int = 0
    next_due_utc: str | None = None
    excluded_count: int = 0
    blocked_count: int = 0
    content_blocked_count: int = 0

    @property
    def total_count(self) -> int:
        return self.ready_count + self.waiting_count + self.blocked_count + self.content_blocked_count


def summarize_queue_availability(store, pending=None, *, allowed_kinds=None,
                                 now: datetime | None = None,
                                 dependency_graph=None) -> QueueAvailability:
    """Describe what resume can offer without advancing or rewriting the queue.

    Future learning repetitions remain scheduled work, but are not questions
    available now. The same pause and visible-draft rules as
    StudyStore.prepare_next_question apply. Passing the effective queue kinds
    also previews reconciliation, including a previously disabled repetition
    that can be restored on its original due date.
    """
    now = _utc(now)
    pending = store.load_session_progress() if pending is None else pending
    if not pending:
        return QueueAvailability()
    questions, index = pending["questions"], pending["question_index"]
    if index >= len(questions):
        return QueueAvailability()
    if allowed_kinds is not None:
        if isinstance(allowed_kinds, (str, bytes)):
            raise ValueError("A collection of practice kinds is required")
        allowed = set(allowed_kinds)
        if not allowed <= SKILLS:
            raise ValueError("Choose known practice kinds")
    else:
        allowed = None
    unavailable = store._unavailable_content_ids(now)
    from .kana_routing import active_units
    rows = store.scheduling_cards(aliases=True) if active_units(store) else store.queries.suspended_kana_rows()
    paused_directions = {(row["content_id"], row["skill"], row["script"])
        for row in rows if row["suspended"]}

    def permitted(question):
        from .kana_question_policy import preserved_retired_question
        from .kana_context_words import context_active
        from .session_content import retired
        permitted = question_allowed(question) or (question is questions[index]
            and preserved_retired_question(question, pending.get("state")))
        return permitted and not retired(question) and context_active(question, store) and (allowed is None or question.kind in allowed)

    def paused(question):
        # Word examples are auxiliary practice. Only obsolete word timers
        # are retired; kana-level suspension does not disable a word example.
        if question.kind in WORD_SKILLS:
            return (bool(question.metadata.get("not_before_utc")) or
                    bool(question.metadata.get(WORD_KIND_PAUSE, {}).get("disabled")) and
                    (allowed is None or question.kind not in allowed))
        return (question.content_id in unavailable or
                (question.content_id, question.kind, question.script) in paused_directions)

    active = [(old, question) for old, question in enumerate(questions[index:], start=index)
              if permitted(question) and not paused(question)]
    excluded = len(questions) - index - len(active)
    if allowed:
        original = store._session_snapshot(pending["session_id"])
        # Reconciliation reserves all allowed surviving directions, including
        # paused ones. Use its exact input rather than just our active rows.
        surviving = [item for item in original[index:] if item["kind"] in allowed
            and question_allowed(item)
            and not (item["kind"] in WORD_SKILLS and item.get("metadata", {}).get("not_before_utc"))]
        restored = store._restorable_session_repetitions(pending["session_id"],
            original, index, allowed, surviving, now)
        active.extend((None, _question_from_snapshot(item)) for item in restored)

    state = pending.get("state") or {}
    from .kana_learning_intro import intro_wait_times
    intro_due = intro_wait_times(store, [question for _, question in active])
    feedback_due = store.feedback_wait_times(
        ((q.content_id, q.kind, q.script) for _, q in active), now)
    from .review_dispatch import kana_resume_priority
    visible_current = kana_resume_priority(state) is not None
    from .kana_dependency_runtime import (
        kana_dependency_graph, kana_dependency_state, kana_prerequisite_status)
    graph = kana_dependency_graph(graph=dependency_graph)
    dependency_state = kana_dependency_state(store)
    ready, future, blocked = 0, [], 0
    for old, question in active:
        preserved = old == index and visible_current
        if kana_prerequisite_status(question, graph, dependency_state, preserve=preserved)["blocked"]:
            blocked += 1
            continue
        value = question.metadata.get("not_before_utc")
        due = datetime.fromisoformat(value) if value else now
        teaching_due = (intro_due.get((question.content_id, question.script), now)
                        if question.metadata.get("kana_intro_v1") is True else now)
        due = max(due, teaching_due, feedback_due.get(
            (question.content_id, question.kind, question.script), now))
        # A displayed draft or ungraded feedback can always be resumed. The
        # scheduler likewise leaves it in place, including after clock drift.
        if due <= now or preserved:
            ready += 1
        else:
            future.append(due)
    from .session_content import retired
    content_blocked = sum(retired(q) for q in questions[index:])
    return QueueAvailability(ready, len(future), min(future).isoformat() if future else None,
                             excluded, blocked, content_blocked)
