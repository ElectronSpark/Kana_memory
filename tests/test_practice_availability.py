"""Home queue counts reflect actionable work without editing learning evidence."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

from kanamemory.domain import Question, StudyStore
from kanamemory.practice_availability import QueueAvailability, summarize_queue_availability


START = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as value:
        yield value


from support.practice_availability_support import question


def begin(store, questions):
    session = store.start_session(questions, now=START)
    store.save_session_progress(session, 0)
    return session


def rate(store, session, rating, *, now=START):
    pending = store.load_session_progress()
    index = pending["question_index"]
    item = pending["questions"][index]
    return store.record_review(item.content_id, item.kind, item.script, rating,
        session_id=session, question_index=index, now=now,
        outcome="incorrect" if rating == 1 else "correct", answered_text=item.answer)


def summary(store, **kwargs):
    """The entire database, not just review totals, must remain unchanged."""
    before = tuple(store._db.iterdump())
    pending = store.load_session_progress()
    result = summarize_queue_availability(store, **kwargs)
    assert tuple(store._db.iterdump()) == before
    assert store.load_session_progress() == pending
    return result


def test_finished_initial_round_has_two_waiting_questions_not_two_ready(store):
    session = begin(store, [question("a"), question("b")])
    first = rate(store, session, 3)
    second = rate(store, session, 1)
    assert summary(store, now=START) == QueueAvailability(0, 2, second["due_at_utc"])
    pending = store.prepare_next_question(session, now=START)
    assert pending["state"] == {"page": "session_wait"}
    assert pending["waiting_until_utc"] == second["due_at_utc"]
    assert summary(store, pending=pending, now=START) == QueueAvailability(0, 2, second["due_at_utc"])
    assert store.get_card("a", "kana_choice", "hiragana")["due_at_utc"] == first["due_at_utc"]
    assert len(store.review_history()) == 2


def test_ready_and_waiting_are_separate_and_expire_at_exact_original_due_time(store):
    session = begin(store, [question("a"), question("b"), question("c")])
    rated = rate(store, session, 1)
    due = datetime.fromisoformat(rated["due_at_utc"])
    assert summary(store, now=START) == QueueAvailability(2, 1, rated["due_at_utc"])
    assert summary(store, now=due - timedelta(microseconds=1)).ready_count == 2
    assert summary(store, now=due) == QueueAvailability(3)
    prepared = store.prepare_next_question(session, now=due)
    assert prepared["waiting_until_utc"] is None
    assert len(prepared["questions"]) - prepared["question_index"] == 3
    assert {item.content_id for item in prepared["questions"][prepared["question_index"]:]} == {"a", "b", "c"}


def test_reopen_keeps_waiting_steps_and_completion_has_no_pending_count(tmp_path):
    directory = tmp_path / "learner"
    with StudyStore(directory, time_zone="UTC") as store:
        session = begin(store, [question("a"), question("b")])
        rate(store, session, 3)
        earliest = rate(store, session, 1)["due_at_utc"]
        store.prepare_next_question(session, now=START)
        waiting = store.load_session_progress()
        evidence = tuple(store._db.iterdump())
    with StudyStore(directory, time_zone="UTC") as store:
        assert tuple(store._db.iterdump()) == evidence
        assert summary(store, now=START) == QueueAvailability(0, 2, earliest)
        assert store.load_session_progress() == waiting
        store.prepare_next_question(session, now=START + timedelta(minutes=1))
        rate(store, session, 4, now=START + timedelta(minutes=1))
        assert summary(store, now=START + timedelta(minutes=1)).ready_count == 0
        assert summary(store, now=START + timedelta(minutes=1)).waiting_count == 1
        store.prepare_next_question(session, now=START + timedelta(minutes=10))
        rate(store, session, 4, now=START + timedelta(minutes=10))
        assert summary(store, now=START + timedelta(minutes=10)) == QueueAvailability()
        assert store.load_session_progress() is None


@pytest.mark.parametrize("state", [
    {"page": "question", "romaji_answer": "part"},
    {"page": "feedback", "feedback_pending": True, "answered_text": "a"},
])
def test_visible_draft_and_ungraded_feedback_are_ready_even_if_clock_moves_back(store, state):
    due = START + timedelta(minutes=1)
    session = begin(store, [question("a", future=due), question("b", future=due)])
    store.save_session_progress(session, 0, state=state)
    assert summary(store, now=START) == QueueAvailability(1, 1, due.isoformat())
    prepared = store.prepare_next_question(session, now=START)
    assert prepared["waiting_until_utc"] is None
    assert prepared["state"] == state


def test_wait_page_does_not_make_its_future_question_ready(store):
    due = START + timedelta(minutes=1)
    session = begin(store, [question(future=due)])
    store.save_session_progress(session, 0, state={"page": "session_wait"})
    assert summary(store, now=START) == QueueAvailability(0, 1, due.isoformat())


def test_suspended_buried_disabled_and_legacy_word_timers_are_not_actionable(store):
    due = START + timedelta(minutes=1)
    session = begin(store, [question("whole"), question("buried"), question("direction"),
        question("disabled", "kana_romaji"), question("legacy-word", "word_kana", future=due),
        question("future", future=due), question("ready")])
    store.set_content_control("whole", "suspend", now=START)
    store.set_content_control("buried", "bury", now=START)
    store.record_review("direction", "kana_choice", "hiragana", 4, now=START - timedelta(days=10))
    with store._db:
        store._db.execute("UPDATE study_card SET suspended=1 WHERE content_id='direction'")
    allowed = {"kana_choice", "word_kana"}
    assert summary(store, now=START, allowed_kinds=allowed) == QueueAvailability(1, 1, due.isoformat(), 5)
    store.reconcile_session_kinds(session, allowed, now=START)
    prepared = store.prepare_next_question(session, now=START)
    assert prepared["waiting_until_utc"] is None
    assert len(prepared["questions"]) - prepared["question_index"] == 2
    assert prepared["questions"][prepared["question_index"]].content_id == "ready"


def test_disabled_current_draft_is_excluded_and_waiting_item_stays_scheduled(store):
    due = START + timedelta(minutes=1)
    session = begin(store, [question("disabled", "kana_romaji"), question("a", future=due)])
    store.save_session_progress(session, 0, state={"page": "question", "romaji_answer": "a"})
    assert summary(store, now=START, allowed_kinds={"kana_choice"}) == QueueAvailability(0, 1, due.isoformat(), 1)
    store.reconcile_session_kinds(session, {"kana_choice"}, now=START)
    assert store.prepare_next_question(session, now=START)["waiting_until_utc"] == due.isoformat()


def test_reenabled_repetition_is_counted_once_without_restoring_or_rescheduling_it(store):
    session = begin(store, [question("a", "kana_romaji"), question("keep")])
    due = rate(store, session, 1)["due_at_utc"]
    store.reconcile_session_kinds(session, {"kana_choice"}, now=START)
    assert summary(store, now=START, allowed_kinds={"kana_choice"}) == QueueAvailability(1)
    kinds = {"kana_choice", "kana_romaji"}
    for _ in range(3):
        assert summary(store, now=START, allowed_kinds=kinds) == QueueAvailability(1, 1, due)
    assert summary(store, now=datetime.fromisoformat(due), allowed_kinds=kinds) == QueueAvailability(2)
    restored = store.reconcile_session_kinds(session, kinds, now=START)
    assert restored["restored_count"] == 1
    assert summary(store, now=START, allowed_kinds=kinds) == QueueAvailability(1, 1, due)
    assert store.get_card("a", "kana_romaji", "hiragana")["due_at_utc"] == due


def test_reenabled_superseded_step_does_not_reappear_in_count(store):
    session = begin(store, [question("a", "kana_romaji"), question("keep")])
    rate(store, session, 1)
    store.reconcile_session_kinds(session, {"kana_choice"}, now=START)
    store.record_review("a", "kana_romaji", "hiragana", 4, now=START + timedelta(minutes=1))
    assert summary(store, now=START + timedelta(minutes=2),
        allowed_kinds={"kana_choice", "kana_romaji"}) == QueueAvailability(1)


def test_empty_or_completed_checkpoint_has_no_available_or_waiting_count(store):
    assert summary(store, now=START) == QueueAvailability()
    session = begin(store, [question()])
    with store._db:
        store._db.execute("UPDATE session_checkpoint SET question_index=1 WHERE session_id=?", (session,))
    assert summary(store, now=START) == QueueAvailability()


def test_all_kinds_disabled_is_a_read_only_zero_count(store):
    begin(store, [question(), question("b")])
    assert summary(store, now=START, allowed_kinds=[]) == QueueAvailability(excluded_count=2)


def test_historical_prefix_is_never_counted(store):
    session = begin(store, [question(f"old-{n}") for n in range(1000)] + [question("ready")])
    # Migrated queues can include skipped rows without any review event.
    store.save_session_progress(session, 1000)
    assert summary(store, now=START) == QueueAvailability(1)


def test_future_word_example_is_retired_but_unscheduled_word_is_auxiliary(store):
    due = START + timedelta(minutes=1)
    session = begin(store, [question("word", "word_kana"), question("word", "word_kana", future=due)])
    store.set_content_control("word", "suspend", now=START)
    assert summary(store, now=START) == QueueAvailability(1, excluded_count=1)
    assert store.prepare_next_question(session, now=START)["waiting_until_utc"] is None
