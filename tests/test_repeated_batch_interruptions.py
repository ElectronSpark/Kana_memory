"""Appending during repeated interruptions preserves all queued practice."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import MethodType
from unittest.mock import Mock

import pytest

from kanamemory.app import KanaMemory
from kanamemory.domain import StudyStore, build_practice_batch
from support.domain_support import SmallContent
from support.session_snapshots import unrouted
from support.fake_ui import descendants, text_of
from support.app_fixtures import setup_app, set_mode


START = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
KINDS = ("kana_choice", "romaji_kana")


def plan(repo, store, now, *, script, seed, new_only=False, limit=3):
    return build_practice_batch(repo, store, kinds=KINDS, script=script,
        schedule_mode="spaced", adaptive=True, limit=limit, seed=seed,
        new_only=new_only, now=now)


def rate(store, session, index, rating, now):
    question = store.get_session_questions(session)[index]
    return store.record_review(question.content_id, question.kind,
        question.script, rating, session_id=session, question_index=index,
        now=now, outcome="incorrect" if rating == 1 else "correct",
        answered_text="wrong" if rating == 1 else question.answer)


@pytest.mark.parametrize("script", ["hiragana", "katakana"])
def test_review_then_three_appended_batches_preserve_drafts_and_due_repetitions(tmp_path, script):
    repo = SmallContent()
    repo.kana = [row for row in repo.kana
                 if row["category"] == "basic" and row["tier"] == "modern_core"]
    directory = tmp_path / "learner"
    store = StudyStore(directory, time_zone="UTC")
    try:
        for item in repo.kana[:3]:
            for kind in KINDS:
                store.record_review(item["id"], kind, script, 4, now=START)
        now = START + timedelta(days=30)
        original = plan(repo, store, now, script=script, seed=1)
        assert {q.content_id for q in original} == {r["id"] for r in repo.kana[:3]}
        session = store.start_session(original, now=now)
        store.save_session_progress(session, 0)
        failed = rate(store, session, 0, 1, now)
        assert failed["queue_extended"]
        rate(store, session, 1, 4, now + timedelta(seconds=1))
        now += timedelta(seconds=2)
        initial_session_count = store._db.execute("SELECT COUNT(*) FROM study_session").fetchone()[0]

        for seed in (2, 3, 4):
            pending = store.load_session_progress()
            index = pending["question_index"]
            store.save_session_progress(session, index, pending["session_right"],
                assisted_indices=[0, index],
                state={"page": "question", "answered_text": "unsubmitted"})
            pending = store.load_session_progress()
            prefix = pending["questions"]
            token = store.session_append_token(session)
            before = store.review_history()
            cards = [tuple(row) for row in store._db.execute("SELECT * FROM study_card")]
            questions = plan(repo, store, now, script=script, seed=seed, new_only=True)
            assert len({q.content_id for q in questions}) == 3
            assert len(questions) == 6
            assert not {q.content_id for q in questions} & {q.content_id for q in prefix}
            assert store.load_session_progress() == pending
            assert store.review_history() == before
            assert store.append_session(session, questions, expected_token=token) == session
            added = store.load_session_progress()
            assert added["questions"][:len(prefix)] == prefix
            assert unrouted(added["questions"][len(prefix):]) == questions
            assert {**added, "questions": prefix} == pending
            assert store.review_history() == before
            assert [tuple(row) for row in store._db.execute("SELECT * FROM study_card")] == cards
            assert store._db.execute("SELECT COUNT(*) FROM study_session").fetchone()[0] == initial_session_count
            assert store._db.execute("SELECT COUNT(*) FROM session_checkpoint").fetchone()[0] == 1

            # Stop before rating the draft, then reconstruct the store exactly
            # as an app restart does. Both old and appended questions survive.
            store.close()
            store = StudyStore(directory, time_zone="UTC")
            assert store.load_session_progress() == added
            assert store.review_history() == before
            assert rate(store, session, 0, 1, now)["already_recorded"]
            assert store.load_session_progress() == added
            assert store.review_history() == before
            # Continue just one original question before interrupting again.
            rate(store, session, index, 4, now)
            now += timedelta(seconds=1)

        queued = store.queued_content_ids()
        remaining = plan(repo, store, now, script=script, seed=9, new_only=True, limit=100)
        introduced = {event["content_id"] for event in store.review_history()}
        assert {q.content_id for q in remaining} == {
            r["id"] for r in repo.kana if r["category"] == "basic"} - introduced - queued
        original_retry = next(q for q in store.get_session_questions(session)
                              if q.metadata.get("relearning_of_index") == 0)
        assert original_retry.metadata["not_before_utc"] == failed["due_at_utc"]
        # Finish the entire extended queue, respecting the genuine due step.
        for _ in range(100):
            pending = store.prepare_next_question(session, now=now)
            if pending is None:
                break
            if pending["waiting_until_utc"]:
                now = datetime.fromisoformat(pending["waiting_until_utc"])
                continue
            rate(store, session, pending["question_index"], 4, now)
            now += timedelta(seconds=1)
        else:
            pytest.fail("Extended practice queue did not finish")
        assert store.load_session_progress() is None
        summary = store.get_session_summary(session)
        assert summary["answered"] == summary["total"] == 25
        assert summary["skipped"] == 0
        events = store.review_history()
        assert len({event["event_id"] for event in events}) == len(events)
        answers = store._db.execute("SELECT COUNT(*) FROM session_answer").fetchone()[0]
        assert len(events) == answers + 6
    finally:
        store.close()


def test_three_new_batches_append_without_losing_current_question(setup_app, tmp_path):
    app = setup_app
    app.repo = SmallContent()
    app._clean_preferences = KanaMemory._clean_preferences
    app.show_practice_setup = MethodType(KanaMemory.show_practice_setup.__wrapped__, app)
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        app.store = store
        set_mode(app, "看假名选读音", script="平假名", scopes=("清音与拨音",), count=5)

        def display_first_question():
            app.page = "question"
            store.save_session_progress(app.session_id, app.question_index)

        app.show_question = display_first_question
        def resume(**kwargs):
            pending = store.load_session_progress()
            app.session_id, app.questions = pending["session_id"], pending["questions"]
            app.question_index = pending["question_index"]
            app.session_right = pending["session_right"]
            app.page = pending["state"].get("page", "question")
        app.resume_practice = Mock(side_effect=resume)
        asyncio.run(app.start_practice())
        for cycle in range(3):
            old_session = app.session_id
            position = store.load_session_progress()["question_index"]
            rate(store, old_session, position, 4, START + timedelta(seconds=cycle))
            before = store.review_history()
            pending = store.load_session_progress()
            store.save_session_progress(old_session, pending["question_index"], pending["session_right"],
                state={"page": "feedback", "feedback_pending": True, "answered_text": "unrated"})
            pending = store.load_session_progress()
            reserved = {q.content_id for q in pending["questions"]}
            app.page = "home"
            KanaMemory.show_new_batch(app)
            app.count_picker.value, app.custom_count_input.value = "自定义", "3"
            app.refresh_practice_setup()
            assert app.batch_picker.value == "只学新内容"
            assert app.practice_start_button.text == "追加到当前练习"
            assert "原队列未完成" in app.practice_count_summary.text
            asyncio.run(app.start_practice())
            assert app.session_id == old_session
            latest = store.load_session_progress()
            assert latest["questions"][:len(pending["questions"])] == pending["questions"]
            assert {key: value for key, value in latest.items() if key != "questions"} == {
                key: value for key, value in pending.items() if key != "questions"}
            added = latest["questions"][len(pending["questions"]):]
            assert len(added) == 3
            assert not {q.content_id for q in added} & reserved
            assert store.review_history() == before
            assert not app.practice_replace_slot.children
            app.resume_practice.assert_called_with(preserve_audio=True)
            # A delayed second tap after resuming cannot append again.
            asyncio.run(app.start_practice())
            assert store.load_session_progress() == latest
        assert store._db.execute("SELECT COUNT(*) FROM study_session").fetchone()[0] == 1
        assert len(store.get_session_questions(app.session_id)) == 14
        app.alert.assert_not_called()
