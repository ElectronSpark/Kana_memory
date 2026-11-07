"""Current question preferences safely prune an already running queue."""
from runtime_record_test_support import snapshot_json, write_snapshot
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from kanamemory.domain import Question, SKILLS, StudyStore
from support.domain_support import START


EVIDENCE_TABLES = ("study_card", "review_event", "session_answer", "practice_attempt")


def question(content_id, kind="kana_choice", **metadata):
    return Question(content_id, kind, "hiragana", "あ", "a", ["a", "i", "u"],
        accepted_answers=("a",), metadata={"practice_config": {"schedule_mode": "spaced"}, **metadata})


def begin(store, questions):
    session = store.start_session(questions, now=START)
    store.save_session_progress(session, 0)
    return session


def evidence(store):
    return {table: [tuple(row) for row in store._db.execute(f"SELECT * FROM {table}")]
            for table in EVIDENCE_TABLES}


def all_rows(store):
    return {**evidence(store), **{table: [tuple(row) for row in store._db.execute(f"SELECT * FROM {table}")]
            for table in ("study_session", "session_question", "session_checkpoint")}}


def rate(store, session, index, rating=4, now=START):
    q = store.get_session_questions(session)[index]
    return store.record_review(q.content_id, q.kind, q.script, rating,
        session_id=session, question_index=index, outcome="correct", now=now)


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as result:
        yield result


@pytest.mark.parametrize("state", [
    {"page": "question", "romaji_answer": "draft"},
    {"page": "feedback", "answered_text": "a", "answer_correct": True,
     "feedback_pending": True, "answer_revealed": False},
    {"page": "ink_question", "ink_width": 900, "ink_height": 450,
     "question_audio_ok": True, "strokes": [[[20, 30, 0], [50, 80, 18]]]},
    {"page": "session_wait"},
])
def test_allowed_current_draft_prefix_and_evidence_are_preserved(store, state):
    session = begin(store, [question("recorded", "kana_handwriting"),
        question("current"), question("disabled", "kana_handwriting"), question("last", "kana_romaji")])
    review = rate(store, session, 0, 1)
    assert review["queue_extended"]
    store.save_session_progress(session, 1, 1, [0, 1, 3], state)
    before = store.get_session_questions(session)
    unchanged = evidence(store)
    token = store.session_append_token(session)
    result = store.reconcile_session_kinds(session, {"kana_choice", "kana_romaji"})
    assert result["changed"] and not result["completed"]
    assert result["questions"] == [before[i] for i in (0, 2, 4, 1, 3)]
    assert result["question_index"] == 3 and result["session_right"] == 1
    assert result["state"] == state
    assert result["assisted_indices"] == [0, 3, 4]
    assert evidence(store) == unchanged
    assert store.session_append_token(session) != token
    assert store.get_session_questions(session) == result["questions"]
    assert store.load_session_progress() == {k: v for k, v in result.items() if k not in {"changed", "completed", "restored_count"}}
    # Enabling restores only the learned timed repetition, not the ungraded
    # disabled introductory question. The visible allowed draft stays put.
    enabled = store.reconcile_session_kinds(session, SKILLS)
    assert enabled["changed"] and enabled["question_index"] == 3
    assert enabled["restored_count"] == 1
    assert enabled["questions"] == [*result["questions"], before[4]]
    assert enabled["state"] == state and evidence(store) == unchanged


@pytest.mark.parametrize("state", [
    {"page": "feedback", "answered_text": "あ", "answer_correct": True,
     "feedback_pending": True, "answer_revealed": True},
    {"page": "ink_question", "strokes": [[[20, 30, 0], [50, 80, 18]]]},
    {"page": "session_wait"},
])
def test_disabled_current_draft_is_cleared_without_scoring(store, state):
    session = begin(store, [question("disabled", "kana_handwriting"), question("keep")])
    store.save_session_progress(session, 0, assisted_indices=[0], state=state)
    before = all_rows(store)
    result = store.reconcile_session_kinds(session, {"kana_choice"})
    assert result["question_index"] == 1 and result["state"] == {}
    assert result["assisted_indices"] == [0]
    assert result["questions"][1].content_id == "keep"
    assert evidence(store) == {k: before[k] for k in EVIDENCE_TABLES}
    # Already ordered prefix need not be rewritten.
    assert all_rows(store)["study_session"] == before["study_session"]


def test_filter_keeps_allowed_repeat_time_and_actual_scheduler_due(store):
    session = begin(store, [question("repeated"), question("audio", "kana_handwriting"), question("other")])
    review = rate(store, session, 0, 1)
    before = store.get_session_questions(session)
    unchanged = evidence(store)
    result = store.reconcile_session_kinds(session, {"kana_choice"})
    assert result["questions"] == before
    assert result["question_index"] == 2
    assert result["questions"][-1].metadata["not_before_utc"] == review["due_at_utc"]
    assert evidence(store) == unchanged
    assert result["questions"][-1].metadata["relearning_of_index"] == 0
    rate(store, session, 2)
    waiting = store.prepare_next_question(session, now=START)
    assert waiting["waiting_until_utc"] == review["due_at_utc"]
    ready = store.prepare_next_question(session, now=START + timedelta(minutes=1))
    assert ready["waiting_until_utc"] is None
    rate(store, session, ready["question_index"], now=START + timedelta(minutes=1))
    assert store.load_session_progress() is None
    assert len(store.review_history()) == 3


def test_all_disabled_returns_completion_without_false_answers_or_resurrection(store):
    session = begin(store, [question("audio", "kana_handwriting")])
    review = rate(store, session, 0, 1)
    store.prepare_next_question(session, now=START)
    unchanged = evidence(store)
    result = store.reconcile_session_kinds(session, {"kana_choice"})
    assert result["changed"] and result["completed"]
    assert result["question_index"] == len(result["questions"]) == 2
    assert result["state"] == {} and result["session_right"] == 1
    assert store.load_session_progress() is None
    assert evidence(store) == unchanged
    assert store.get_card("audio", "kana_handwriting", "hiragana")["due_at_utc"] == review["due_at_utc"]
    assert store.reconcile_session_kinds(session, SKILLS) is None
    assert store.get_session_summary(session)["answered"] == 1


def test_noop_preserves_database_timestamp_and_pending_append_token(store):
    session = begin(store, [question("a"), question("b", "kana_romaji")])
    store.save_session_progress(session, 0, state={"page": "question", "romaji_answer": "a"})
    before = all_rows(store)
    token = store.session_append_token(session)
    changes = store._db.total_changes
    result = store.reconcile_session_kinds(session, iter(("kana_choice", "kana_romaji", "kana_choice")))
    assert not result["changed"] and not result["completed"]
    assert all_rows(store) == before and store._db.total_changes == changes
    assert store.session_append_token(session) == token


@pytest.mark.parametrize("kinds", [[], (), set(), None, "kana_choice", ["unknown"],
                                    ["kana_choice", "unknown"], [False], [[]]])
def test_invalid_or_empty_allowlist_does_not_mutate_session(store, kinds):
    session = begin(store, [question("a", "kana_handwriting")])
    before = all_rows(store)
    with pytest.raises(ValueError):
        store.reconcile_session_kinds(session, kinds)
    assert all_rows(store) == before and not store._db.in_transaction


def test_noncurrent_session_does_not_touch_current_queue(store):
    old = begin(store, [question("a")])
    current = begin(store, [question("b", "kana_handwriting")])
    before = all_rows(store)
    assert store.reconcile_session_kinds(old, {"kana_choice"}) is None
    assert store.reconcile_session_kinds("missing", {"kana_choice"}) is None
    assert all_rows(store) == before
    assert store.load_session_progress()["session_id"] == current


def test_existing_caller_transaction_is_not_rolled_back(store):
    session = begin(store, [question("audio", "kana_handwriting"), question("keep")])
    before = store.load_session_progress()
    store._db.execute("BEGIN IMMEDIATE")
    store._db.execute("INSERT INTO app_setting VALUES (?,?)", ("caller_pending_setting", "true"))
    try:
        with pytest.raises(RuntimeError, match="active transaction"):
            store.reconcile_session_kinds(session, {"kana_choice"})
        assert store._db.in_transaction
        assert store.get_setting("caller_pending_setting") is True
        assert store.load_session_progress() == before
    finally:
        store._db.rollback()
    assert store.get_setting("caller_pending_setting") is None


@pytest.mark.parametrize("failure", ["snapshot", "checkpoint"])
def test_failure_rolls_back_snapshot_checkpoint_and_cache(store, failure):
    session = begin(store, [question("a"), question("audio", "kana_handwriting")])
    cached = store.get_session_questions(session)
    before = all_rows(store)
    target = "session_question" if failure == "snapshot" else "session_checkpoint"
    store._db.execute(f"CREATE TEMP TRIGGER reject_filter BEFORE UPDATE ON {target} "
                      "BEGIN SELECT RAISE(ABORT, 'filter failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="filter failure"):
        store.reconcile_session_kinds(session, {"kana_choice"})
    assert not store._db.in_transaction and all_rows(store) == before
    assert store.get_session_questions(session) == cached


def test_repeated_filter_and_append_preserve_draft_and_avoid_duplicates(store):
    session = begin(store, [question("a"), question("audio", "kana_handwriting"),
                            question("typed", "kana_romaji")])
    state = {"page": "question", "answered_text": "partial"}
    store.save_session_progress(session, 0, state=state)
    old_token = store.session_append_token(session)
    store.reconcile_session_kinds(session, {"kana_choice", "kana_romaji"})
    with pytest.raises(ValueError, match="队列已改变"):
        store.append_session(session, [question("new")], expected_token=old_token)
    store.append_session(session, [question("new"), question("new", "kana_romaji")],
                         expected_token=store.session_append_token(session))
    result = store.reconcile_session_kinds(session, {"kana_choice"})
    assert result["state"] == state and result["question_index"] == 3
    assert [(q.content_id, q.kind) for q in result["questions"][3:]] == [
        ("a", "kana_choice"), ("new", "kana_choice")]
    assert len(result["questions"]) == 5
    assert not store.review_history()
    with pytest.raises(ValueError, match="已经在练习队列"):
        store.append_session(session, [question("new")], expected_token=store.session_append_token(session))


def test_filtered_queue_and_evidence_round_trip_backup_and_reopen(tmp_path):
    path = tmp_path / "source"
    with StudyStore(path, time_zone="UTC") as store:
        session = begin(store, [question("rated"), question("keep", "kana_romaji"),
            question("skip", "kana_handwriting")])
        rate(store, session, 0, 1)
        store.save_session_progress(session, 1, 1, [1], {"page": "question", "romaji_answer": "a"})
        store.reconcile_session_kinds(session, {"kana_choice", "kana_romaji"})
        frozen = all_rows(store)
        progress = store.load_session_progress()
        backup = store.export_backup()
    with StudyStore(path, time_zone="UTC") as reopened:
        assert all_rows(reopened) == frozen and reopened.load_session_progress() == progress
    with StudyStore(tmp_path / "restore", time_zone="UTC") as restored:
        restored.import_backup(backup)
        assert all_rows(restored) == frozen and restored.load_session_progress() == progress


def test_raw_surviving_question_payload_is_not_normalized_during_filter(store):
    session = begin(store, [question("keep"), question("skip", "kana_handwriting")])
    raw = json.loads(snapshot_json(store._db, session))
    raw[0]["accepted_answers"] = [{"kind": "legacy", "text": "a"}]
    raw_state = '{ "page": "question", "romaji_answer": "partial" }'
    with store._db:
        write_snapshot(store._db, session, json.dumps(raw))
        store._db.execute("UPDATE session_checkpoint SET state_json=? WHERE session_id=?", (raw_state, session))
    store.reconcile_session_kinds(session, {"kana_choice"})
    after = json.loads(snapshot_json(store._db, session))
    assert after == [raw[1], raw[0]]
    assert store._db.execute("SELECT state_json FROM session_checkpoint WHERE session_id=?", (session,)).fetchone()[0] == raw_state


def test_other_connection_observes_filtered_snapshot_invalidation(store):
    session = begin(store, [question("keep"), question("skip", "kana_handwriting")])
    store.get_session_questions(session)  # Prime connection-local snapshot cache.
    with StudyStore(store.data_dir, time_zone="UTC") as other:
        result = other.reconcile_session_kinds(session, {"kana_choice"})
    assert store.load_session_progress()["questions"] == result["questions"]
    assert store.load_session_progress()["question_index"] == 1


def test_disabling_every_unrated_question_completes_without_introducing_cards(store):
    session = begin(store, [question("a", "kana_handwriting"), question("b", "kana_handwriting")])
    before = evidence(store)
    result = store.reconcile_session_kinds(session, {"kana_choice"})
    assert result["completed"] and result["question_index"] == 2
    assert store.load_session_progress() is None and evidence(store) == before
    assert store.get_session_summary(session) == {"answered": 0, "correct": 0,
        "content_count": 0, "total": 2, "skipped": 2, "auxiliary_count": 0}


def test_large_filter_is_one_transaction_and_noop_does_not_write(store):
    questions = [question(str(i), "kana_choice" if i % 2 else "kana_handwriting")
                 for i in range(5000)]
    session = begin(store, questions)
    questions = store.get_session_questions(session)
    statements = []
    store._db.set_trace_callback(statements.append)
    try:
        result = store.reconcile_session_kinds(session, {"kana_choice"})
        assert result["question_index"] == 2500
        assert result["questions"][2500:] == questions[1::2]
        assert statements.count("BEGIN IMMEDIATE") == 1
        assert statements.count("COMMIT") == 1
        assert any(sql.startswith("INSERT INTO session_question") for sql in statements)
        assert not any(sql.startswith("UPDATE study_session") for sql in statements)
        statements.clear()
        changes = store._db.total_changes
        unchanged = store.reconcile_session_kinds(session, {"kana_choice"})
        assert not unchanged["changed"] and store._db.total_changes == changes
        assert not any(sql.startswith(("UPDATE", "DELETE", "INSERT")) for sql in statements)
    finally:
        store._db.set_trace_callback(None)
