"""Add new memory items to a live queue without abandoning unfinished work."""
from runtime_record_test_support import snapshot_json, write_snapshot
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from kanamemory.domain import (StudyStore, build_questions, build_practice_batch,
    practice_plan_counts, prepare_session_snapshot, _question_from_snapshot)
from kanamemory.kana_routing import bind_questions
from support.domain_support import SmallContent
from support.domain_support import START


KINDS = ("kana_choice", "romaji_kana")


def plan(store, *, seed=1, limit=3, script="hiragana", **config):
    return build_practice_batch(SmallContent(), store, kinds=KINDS,
        schedule_mode="spaced", adaptive=True, script=script, seed=seed,
        limit=limit, now=START, **config)


def database_rows(store):
    return {table: [tuple(row) for row in store._db.execute(f"SELECT * FROM {table}")]
        for table in ("study_session", "session_question", "session_checkpoint", "study_card", "review_event",
                      "session_answer", "practice_attempt", "word_spelling_attempt")}


def begin(store):
    questions = plan(store)
    session = store.start_session(questions, now=START)
    store.save_session_progress(session, 0)
    return session, questions


@pytest.mark.parametrize("state", [
    {"page": "question", "answered_text": "unsubmitted"},
    {"page": "feedback", "answered_text": "a", "answer_correct": True,
     "feedback_pending": True, "answer_revealed": False, "question_audio_ok": True},
    {"page": "ink", "ink_width": 900, "ink_height": 450,
     "question_audio_ok": True, "strokes": [[[20, 30, 0], [50, 80, 18]]]},
    {"page": "session_wait"},
])
def test_append_retains_entire_prefix_draft_and_scheduler(tmp_path, state):
    with StudyStore(tmp_path, time_zone="UTC") as store:
        session, questions = begin(store)
        first = questions[0]
        reviewed = store.record_review(first.content_id, first.kind, first.script, 1,
            session_id=session, question_index=0, outcome="incorrect", now=START)
        assert reviewed["queue_extended"]
        pending = store.load_session_progress()
        store.save_session_progress(session, pending["question_index"],
            pending["session_right"], assisted_indices=[0, 1], state=state)
        before = database_rows(store)
        prefix = store.get_session_questions(session)
        retry = prefix[-1]
        assert retry.metadata["not_before_utc"] == reviewed["due_at_utc"]
        token = store.session_append_token(session)
        addition = plan(store, new_only=True, seed=2)
        assert len(addition) == 6  # Several directions per new item stay intact.
        assert store.append_session(session, addition, expected_token=token) == session
        after = database_rows(store)
        # Enrollment binds only the new tail to the current ability routes.
        bound_addition = [_question_from_snapshot(q) for q in
                          bind_questions(store, [q.to_dict() for q in addition])]
        assert store.get_session_questions(session) == prefix + bound_addition
        for table in before.keys() - {"session_question"}:
            assert after[table] == before[table]
        assert len(after["study_session"]) == len(before["study_session"]) == 1
        assert after["session_question"][:len(prefix)] == before["session_question"]
        assert store.get_session_questions(session)[len(prefix) - 1] == retry
        frozen = store.load_session_progress()
    with StudyStore(tmp_path, time_zone="UTC") as reopened:
        assert reopened.load_session_progress() == frozen


@pytest.mark.parametrize("script", ["hiragana", "katakana", "both"])
@pytest.mark.parametrize("mode", ["reinforce", "spaced"])
def test_new_selection_and_preview_exclude_all_reserved_content(tmp_path, script, mode):
    repo = SmallContent()
    with StudyStore(tmp_path, time_zone="UTC") as store:
        session, questions = begin(store)
        reserved = {q.content_id for q in questions}
        assert not store.review_history()  # Opening alone creates no card.
        config = dict(kinds=KINDS, adaptive=True, script=script,
            schedule_mode=mode, new_only=True, limit=100, seed=3, now=START)
        result = build_practice_batch(repo, store, **config)
        assert result and not reserved & {q.content_id for q in result}
        counts = practice_plan_counts(repo, store, **config)
        assert counts["total"] == len(result)
        assert counts["content_count"] == len({q.content_id for q in result})
        # The direct single-skill builder does not use the mixed-batch selector.
        for alphabet in ("hiragana", "katakana"):
            direct = build_questions(repo, store, kind="kana_romaji", script=alphabet,
                limit=100, seed=3, new_only=True, now=START)
            assert direct and not reserved & {q.content_id for q in direct}
        # Historical, ended queues do not permanently reserve unlearned items.
        store.clear_session_progress()
        available = build_practice_batch(repo, store, **config)
        assert reserved <= {q.content_id for q in available}


def test_mixed_family_new_preview_excludes_reserved_word_and_kana(tmp_path):
    repo = SmallContent()
    # Every possible selected kana has a word context. Mixed batches now attach
    # matching word exercises after selecting the requested kana memory items.
    repo.words = [{"id": f"word-context-{row['id']}", "reading": row["katakana"],
                   "gloss": "context", "practice_approved": True}
                  for row in repo.kana if row["category"] == "basic"]
    with StudyStore(tmp_path, time_zone="UTC") as store:
        config = dict(kinds=(*KINDS, "word_meaning_kana", "word_kana"), adaptive=True,
            script="both", schedule_mode="spaced", limit=4, now=START, seed=4)
        questions = build_practice_batch(repo, store, **config)
        assert any(q.kind.startswith("word_") for q in questions)
        assert len({q.content_id for q in questions if not q.kind.startswith("word_")}) == 4
        session = store.start_session(questions)
        store.save_session_progress(session, 0)
        reserved = {q.content_id for q in questions}
        config.update(new_only=True, limit=100)
        addition = build_practice_batch(repo, store, **config)
        assert not reserved & {q.content_id for q in addition}
        assert practice_plan_counts(repo, store, **config)["total"] == len(addition)


@pytest.mark.parametrize("change", ["draft", "advance", "snapshot", "replacement", "finished"])
def test_append_rejects_stale_worker_results_without_any_mutation(tmp_path, change):
    with StudyStore(tmp_path, time_zone="UTC") as store:
        session, original = begin(store)
        token = store.session_append_token(session)
        prepared = prepare_session_snapshot(plan(store, new_only=True, seed=2))
        if change == "draft":
            store.save_session_progress(session, 0, state={"page": "question", "answered_text": "new draft"})
        elif change == "advance":
            q = original[0]
            store.record_review(q.content_id, q.kind, q.script, 4, session_id=session,
                question_index=0, outcome="correct", now=START)
        elif change == "snapshot":
            # Same length, changed choice order: counts alone cannot detect it.
            payload = [q.to_dict() for q in original]
            payload[0]["options"] = list(reversed(payload[0]["options"])) + ["changed"]
            with store._db:
                write_snapshot(store._db, session, json.dumps(payload, ensure_ascii=False))
        elif change == "replacement":
            other = store.start_prepared_session(prepared)
            store.save_session_progress(other, 0)
        else:
            store.clear_session_progress()
        before = database_rows(store)
        with pytest.raises(ValueError, match="队列已改变"):
            store.append_prepared_session(session, prepared, expected_token=token)
        assert database_rows(store) == before


def test_duplicate_append_is_rejected_even_with_fresh_token(tmp_path):
    with StudyStore(tmp_path, time_zone="UTC") as store:
        session, _ = begin(store)
        prepared = prepare_session_snapshot(plan(store, new_only=True, seed=2))
        token = store.session_append_token(session)
        store.append_prepared_session(session, prepared, expected_token=token)
        before = database_rows(store)
        for retry_token in (token, store.session_append_token(session)):
            with pytest.raises(ValueError):
                store.append_prepared_session(session, prepared, expected_token=retry_token)
            assert database_rows(store) == before


@pytest.mark.parametrize("failure", ["size", "database"])
def test_failed_append_rolls_back_and_keeps_snapshot_cache_valid(tmp_path, monkeypatch, failure):
    with StudyStore(tmp_path, time_zone="UTC") as store:
        session, _ = begin(store)
        token = store.session_append_token(session)
        prepared = prepare_session_snapshot(plan(store, new_only=True, seed=2))
        cached = store.get_session_questions(session)
        before = database_rows(store)
        if failure == "size":
            from kanamemory import progress_backup
            monkeypatch.setattr(progress_backup, "MAX_BYTES", len(prepared.snapshot_json.encode("utf-8")) + 1)
            error = ValueError
        else:
            store._db.execute("""CREATE TEMP TRIGGER fail_append AFTER INSERT
                ON session_question BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END""")
            error = sqlite3.IntegrityError
        with pytest.raises(error):
            store.append_prepared_session(session, prepared, expected_token=token)
        assert not store._db.in_transaction
        assert database_rows(store) == before
        assert store.get_session_questions(session) == cached


def test_other_connection_can_append_then_original_rejects_stale_token(tmp_path):
    with StudyStore(tmp_path, time_zone="UTC") as first:
        session, _ = begin(first)
        token = first.session_append_token(session)
        prepared = prepare_session_snapshot(plan(first, new_only=True, seed=2))
        with StudyStore(tmp_path, time_zone="UTC") as second:
            assert second.session_append_token(session) == token
            second.append_prepared_session(session, prepared, expected_token=token)
        before = database_rows(first)
        with pytest.raises(ValueError):
            first.append_prepared_session(session, prepared, expected_token=token)
        assert database_rows(first) == before


def test_appended_choices_are_visible_in_same_connection_history(tmp_path):
    with StudyStore(tmp_path, time_zone="UTC") as store:
        session, _ = begin(store)
        previous = store.previous_choice_options("kana_choice", "hiragana")
        addition = plan(store, new_only=True, seed=2)
        store.append_session(session, addition, expected_token=store.session_append_token(session))
        current = store.previous_choice_options("kana_choice", "hiragana")
        assert previous.items() <= current.items()
        for q in addition:
            if q.kind == "kana_choice":
                assert current[q.content_id] == q.options


def test_empty_or_overlapping_cross_script_addition_keeps_live_queue(tmp_path):
    with StudyStore(tmp_path, time_zone="UTC") as store:
        session, original = begin(store)
        before = database_rows(store)
        token = store.session_append_token(session)
        for questions in ([], [replace(original[0], script="katakana", kind="kana_romaji")]):
            with pytest.raises(ValueError):
                store.append_session(session, questions, expected_token=token)
            assert database_rows(store) == before


def test_appended_queue_backup_round_trip_preserves_old_and_new_questions(tmp_path):
    with StudyStore(tmp_path / "source", time_zone="UTC") as store:
        session, _ = begin(store)
        store.save_session_progress(session, 0, assisted_indices=[0],
            state={"page": "question", "answered_text": "unsubmitted"})
        addition = plan(store, new_only=True, seed=2)
        store.append_session(session, addition, expected_token=store.session_append_token(session))
        expected = store.load_session_progress()
        backup = store.export_backup()
    with StudyStore(tmp_path / "restored", time_zone="UTC") as restored:
        restored.import_backup(backup)
        assert restored.load_session_progress() == expected
        assert restored.queued_content_ids() == {q.content_id for q in expected["questions"]}
        assert not restored.review_history()
