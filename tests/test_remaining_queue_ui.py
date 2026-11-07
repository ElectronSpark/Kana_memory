"""Resume counts describe outstanding work, never immutable session history."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from support.app_fixtures import question_app, setup_app
from kanamemory import domain
from kanamemory.app import KanaMemory
from kanamemory.practice_availability import summarize_queue_availability
from kanamemory.domain import Question, StudyStore


START = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def question(ident, kind="kana_choice", *, spaced=False):
    return Question(ident, kind, "hiragana", "あ", "a", ["a", "i", "u", "e"],
        metadata={"kana_symbol": "あ", "romaji": "a", "content_version": "test",
                  "practice_config": {"schedule_mode": "spaced" if spaced else "reinforce"}})


def home(app, store):
    """Kana-queue (ready, waiting) counts as the study-path home reads them, read-only."""
    before = tuple(store._db.iterdump())
    pending = store.load_session_progress(lane="main", lazy_questions=True)
    available = summarize_queue_availability(store, pending,
        allowed_kinds=KanaMemory._queue_kinds(app, pending) if pending else None)
    assert tuple(store._db.iterdump()) == before
    return available.ready_count, available.waiting_count


def rate(store, session, rating, now):
    pending = store.load_session_progress()
    index = pending["question_index"]
    item = pending["questions"][index]
    return store.record_review(item.content_id, item.kind, item.script, rating,
        session_id=session, question_index=index, now=now,
        outcome="incorrect" if rating == 1 else "correct", answered_text=item.answer)


def memory(store):
    return {table: [tuple(row) for row in store._db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("study_card", "review_event", "session_answer", "word_spelling_attempt")}


def test_old_thousand_row_history_shows_only_three_unfinished_questions(question_app, tmp_path):
    app = question_app
    directory = tmp_path / "learner"
    with StudyStore(directory, time_zone="UTC") as store:
        # Both answered and disabled rows belong to the immutable historical
        # prefix. The older mobile format uses precisely these same indices.
        original = ([question(f"answered-{i}") for i in range(10)]
                    + [question(f"disabled-{i}", "kana_romaji") for i in range(990)]
                    + [question(f"pending-{i}") for i in range(3)])
        session = store.start_session(original, now=START)
        store.save_session_progress(session, 0)
        for index in range(10):
            rate(store, session, 4, START + timedelta(seconds=index))
        store.reconcile_session_kinds(session, ["kana_choice"], now=START + timedelta(seconds=10))
        pending = store.load_session_progress()
        assert pending["question_index"] == 1000
        assert len(pending["questions"]) == 1003
        records = memory(store)
        assert home(app, store)[0] == 3

    with StudyStore(directory, time_zone="UTC") as reopened:
        assert reopened.load_session_progress() == pending
        assert memory(reopened) == records
        assert home(app, reopened)[0] == 3
        app.session_id, app.questions = session, pending["questions"]
        app.question_index, app.session_right = 1000, pending["session_right"]
        app.show_question()
        assert app.title == "练习 · 现在可答 3 题"
        assert reopened.load_session_progress()["question_index"] == 1000
        assert memory(reopened) == records


@pytest.mark.parametrize("state", [
    {"page": "question", "answered_text": "unsubmitted"},
    {"page": "feedback", "feedback_pending": True, "answered_text": "a"},
])
def test_two_append_interruptions_count_only_pending_and_preserve_schedule(setup_app, tmp_path, monkeypatch, state):
    app = setup_app
    clock = SimpleNamespace(now=START)
    monkeypatch.setattr(domain, "utc_now", lambda: clock.now)
    directory = tmp_path / "learner"
    store = StudyStore(directory, time_zone="UTC")
    try:
        session = store.start_session([question(f"original-{i}", spaced=True) for i in range(4)])
        store.save_session_progress(session, 0)
        failed = rate(store, session, 1, clock.now)
        retry_due = failed["due_at_utc"]
        rate(store, session, 4, clock.now + timedelta(seconds=1))
        assert home(app, store)[0] == 2

        for cycle, (amount, appended_count, after_rating) in enumerate(((2, 4, 3), (3, 6, 5))):
            clock.now += timedelta(seconds=3)
            pending = store.load_session_progress()
            store.save_session_progress(session, pending["question_index"], pending["session_right"],
                                        state=state)
            before = store.load_session_progress()
            preserved = memory(store)
            store.append_session(session, [question(f"new-{cycle}-{i}") for i in range(amount)],
                                 expected_token=store.session_append_token(session))
            after = store.load_session_progress()
            assert after["questions"][:len(before["questions"])] == before["questions"]
            assert {key: value for key, value in after.items() if key != "questions"} == {
                key: value for key, value in before.items() if key != "questions"}
            assert memory(store) == preserved
            assert home(app, store)[0] == appended_count
            # Reopening and merely displaying a draft/feedback do not turn it
            # into a completed answer or alter the earlier scheduled retry.
            store.close()
            store = StudyStore(directory, time_zone="UTC")
            assert store.load_session_progress() == after
            assert memory(store) == preserved
            assert home(app, store)[0] == appended_count
            assert store.get_card("original-0", "kana_choice", "hiragana")["due_at_utc"] == retry_due
            rate(store, session, 4, clock.now)
            assert home(app, store)[0] == after_rating

        for _ in range(20):
            pending = store.prepare_next_question(session, now=clock.now)
            if pending is None:
                break
            if pending["waiting_until_utc"]:
                assert pending["waiting_until_utc"] == retry_due
                assert home(app, store) == (0, 1)
                clock.now = datetime.fromisoformat(retry_due)
            else:
                rate(store, session, 4, clock.now)
        else:
            pytest.fail("Extended queue failed to finish")
        assert home(app, store) == (0, 0)
        assert store.get_session_summary(session)["answered"] == 10
        assert store.get_session_summary(session)["total"] == 10
        assert store._db.execute("SELECT COUNT(*) FROM session_answer WHERE session_id=?", (session,)).fetchone()[0] == 10

        fresh = store.start_session([question("fresh-a"), question("fresh-b")])
        store.save_session_progress(fresh, 0)
        assert home(app, store)[0] == 2
        assert store.get_session_summary(session)["answered"] == 10
    finally:
        store.close()


def test_already_completed_checkpoint_does_not_offer_zero_or_overflow_resume(setup_app, tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        session = store.start_session([question("finished")])
        store.save_session_progress(session, 0)
        # Defensive display of an old/stale checkpoint must not advertise an
        # impossible next question. Reading the home screen remains read-only.
        with store._db:
            store._db.execute("UPDATE session_checkpoint SET question_index=1 WHERE session_id=?", (session,))
        assert home(setup_app, store) == (0, 0)
