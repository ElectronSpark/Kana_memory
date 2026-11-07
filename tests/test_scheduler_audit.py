"""A saved answer's observation time must survive delayed rating and restarts."""
from datetime import datetime, timedelta
import json

import pytest

from record_test_support import decode_current, encode_current
from fsrs import Card, Rating

from support.learning_store_fixtures import START, item, question, ready
from support.study_path_runtime_support import path_app
from support.learning_fixtures import learning_app
from support.app_fixtures import setup_app
from support.app_fixtures import question_app
from kanamemory.domain import Question, StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.practice_availability import summarize_queue_availability


@pytest.mark.parametrize("rating", [1, 2, 3, 4])
@pytest.mark.parametrize("already_reviewed", [False, True])
def test_delayed_rating_after_restart_uses_frozen_answer_time(tmp_path, rating, already_reviewed):
    location = tmp_path / "learner"
    with StudyStore(location, time_zone="UTC") as store:
        learning = LearningStore(store)
        learning.enroll(item(), now=START)
        learning.append_questions(question(), now=START)
        submitted = ready(learning)
        qid = "q1"
        if already_reviewed:
            learning.submit(qid, "right", True, now=submitted)
            previous = learning.finalize(qid, 4, now=submitted)
            submitted = datetime.fromisoformat(previous["due_at_utc"])
            qid = "q2"
            learning.append_questions(question(qid=qid), now=submitted)
        before = learning.get_unit(item()["id"], "understanding")
        card = (Card.from_dict(before["scheduler_state"]) if before["scheduler_state"]
                else Card(card_id=int(before["unit_id"][:15], 16), due=submitted))
        expected, review = store.scheduler.review_card(
            card, Rating(rating), review_datetime=submitted, review_duration=1250)
        frozen = learning.submit(qid, "right", True, now=submitted, duration_ms=1250)
        backup = store.export_backup()

    finalized = submitted + timedelta(days=7)
    with StudyStore(location, time_zone="UTC") as store:
        store.import_backup(backup)
        learning = LearningStore(store)
        assert learning.get_question(qid)["result"] == frozen
        result = learning.finalize(qid, rating, now=finalized)
        saved = learning.get_unit(item()["id"], "understanding")
        assert saved["scheduler_state"] == expected.to_dict()
        assert result["due_at_utc"] == expected.due.isoformat()
        assert result["finalized_at_utc"] == finalized.isoformat()
        event = store._db.execute(
            "SELECT * FROM learning_event WHERE question_id=?", (qid,)).fetchone()
        assert event["occurred_at_utc"] == submitted.isoformat()
        assert json.loads(event["review_log_json"]) == review.to_dict()
        assert saved["review_count"] == before["review_count"] + 1
        assert learning.get_unit(item()["id"], "application")["scheduler_state"] is None
        learning.append_questions(question(qid="next"), now=finalized)
        queue_due = max(expected.due, finalized + timedelta(seconds=60))
        assert learning.current(now=finalized)["type"] == "waiting"
        assert learning.current(now=finalized)["due_at_utc"] == queue_due.isoformat()
        assert learning.current(now=queue_due)["question"]["id"] == "next"
        store.import_backup(store.export_backup())
        restored = LearningStore(store)
        assert restored.finalize(qid, 4, now=finalized + timedelta(days=1)) == result
        assert restored.get_unit(item()["id"], "understanding") == saved


@pytest.mark.parametrize("outcome", ["incorrect", "revealed"])
def test_delayed_failed_answer_relearning_is_due_from_attempt_not_return(tmp_path, outcome):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        learning = LearningStore(store)
        learning.enroll(item(), now=START)
        learning.append_questions(question(), now=START)
        submitted = ready(learning)
        learning.submit("q1", "right", True, now=submitted)
        initial = learning.finalize("q1", 4, now=submitted)
        submitted = datetime.fromisoformat(initial["due_at_utc"])
        learning.append_questions(question(qid="failed"), now=submitted)
        before = learning.get_unit(item()["id"], "understanding")
        expected, _ = store.scheduler.review_card(Card.from_dict(before["scheduler_state"]),
            Rating.Again, review_datetime=submitted)
        learning.submit("failed", "wrong", False, outcome=outcome,
            assisted=outcome == "revealed", now=submitted)
        resumed = submitted + timedelta(days=2)
        result = learning.finalize("failed", 4, now=resumed)
        assert result["rating"] == 1
        assert learning.get_unit(item()["id"], "understanding")["scheduler_state"] == expected.to_dict()
        learning.append_questions(question(qid="retry"), now=resumed)
        assert learning.current(now=resumed)["type"] == "waiting"
        assert learning.current(now=resumed)["due_at_utc"] == (resumed + timedelta(seconds=60)).isoformat()
        assert learning.current(now=resumed + timedelta(seconds=60))["question"]["id"] == "retry"
        assert learning.finalize("failed", 4, now=resumed + timedelta(days=1)) == result


def test_delayed_free_recall_success_timestamp_is_the_observation_time(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        learning = LearningStore(store)
        learning.enroll(item(), now=START)
        learning.append_questions(question(kind="grammar_text", input_mode="text"), now=START)
        submitted = ready(learning)
        learning.submit("q1", "right", True, now=submitted)
        learning.finalize("q1", 3, now=submitted + timedelta(days=2))
        unit = learning.get_unit(item()["id"], "understanding")
        assert unit["last_unassisted_success_at_utc"] == submitted.isoformat()
        assert unit["scheduler_state"]["last_review"] == submitted.isoformat()


def test_rating_cannot_precede_the_frozen_answer(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        learning = LearningStore(store)
        learning.enroll(item(), now=START)
        learning.append_questions(question(), now=START)
        submitted = ready(learning)
        learning.submit("q1", "right", True, now=submitted)
        before = learning.get_unit(item()["id"], "understanding")
        with pytest.raises(ValueError, match="Finalization precedes answer"):
            learning.finalize("q1", 3, now=submitted - timedelta(seconds=1))
        assert learning.get_unit(item()["id"], "understanding") == before
        assert learning.history() == []


@pytest.mark.parametrize("rating", [1, 2, 3, 4])
def test_prompt_rating_keeps_original_fsrs_due_and_queue_time(tmp_path, rating):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        learning = LearningStore(store)
        learning.enroll(item(), now=START)
        learning.append_questions(question(), now=START)
        submitted = ready(learning)
        learning.submit("q1", "right", True, now=submitted)
        result = learning.finalize("q1", rating, now=submitted)
        learning.append_questions(question(qid="next"), now=submitted)
        assert learning.get_question("next")["not_before_utc"] == result["due_at_utc"]
        assert learning.current(now=submitted)["due_at_utc"] == result["due_at_utc"]


def kana_question(cid="kana-a", kind="kana_choice", script="hiragana"):
    return Question(cid, kind, script, "あ", "a", ["a", "i", "u", "e"],
                    metadata={"practice_config": {"schedule_mode": "spaced"}})


def begin_kana(store, questions, when=START):
    sid = store.start_session(questions, now=when)
    store.save_session_progress(sid, 0)
    return sid


def rate_kana(store, sid, index=0, rating=1, when=START, finalized=None):
    q = store.get_session_questions(sid)[index]
    return store.record_review(q.content_id, q.kind, q.script, rating,
        session_id=sid, question_index=index, now=when, finalized_at=finalized,
        outcome="incorrect" if rating == 1 else "correct", answered_text=q.answer)


def test_kana_delayed_failure_keeps_fsrs_due_but_waits_after_saved_feedback(tmp_path):
    location = tmp_path / "learner"
    finalized = START + timedelta(days=2)
    cooldown = finalized + timedelta(seconds=60)
    with StudyStore(location, time_zone="UTC") as store:
        sid = begin_kana(store, [kana_question()])
        result = rate_kana(store, sid, finalized=finalized)
        assert result["due_at_utc"] == (START + timedelta(minutes=1)).isoformat()
        assert result["card"]["scheduler_state"]["last_review"] == START.isoformat()
        original = store.get_session_questions(sid)
        assert len(original) == 2
        assert original[1].metadata["not_before_utc"] == result["due_at_utc"]
        available = summarize_queue_availability(store, now=finalized)
        assert (available.ready_count, available.waiting_count, available.next_due_utc) == (0, 1, cooldown.isoformat())
        assert store.prepare_next_question(sid, now=finalized)["waiting_until_utc"] == cooldown.isoformat()
        with pytest.raises(ValueError, match="feedback"):
            rate_kana(store, sid, 1, 3, when=finalized)
        snapshot = store.export_backup()
        before = store.get_card("kana-a", "kana_choice", "hiragana")
        assert rate_kana(store, sid, finalized=finalized + timedelta(days=1))["already_recorded"]
        assert store.feedback_wait_times([("kana-a", "kana_choice", "hiragana")], finalized) == {
            ("kana-a", "kana_choice", "hiragana"): cooldown}
    with StudyStore(location, time_zone="UTC") as store:
        store.import_backup(snapshot)
        assert store.get_card("kana-a", "kana_choice", "hiragana") == before
        assert store.get_session_questions(sid) == original
        assert summarize_queue_availability(store, now=finalized).next_due_utc == cooldown.isoformat()
        assert store.prepare_next_question(sid, now=cooldown)["waiting_until_utc"] is None
        assert rate_kana(store, sid, 1, 3, when=cooldown)["card"]["review_count"] == 2


def test_kana_feedback_delay_applies_to_rebuilt_queue_across_kinds_within_same_script(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        finalized = START + timedelta(days=40)
        sid = begin_kana(store, [kana_question()])
        result = rate_kana(store, sid, rating=4, finalized=finalized)
        assert not result["queue_extended"]
        assert datetime.fromisoformat(result["due_at_utc"]) < finalized
        assert store.load_session_progress() is None
        questions = [kana_question(), kana_question("kana-i"),
                     kana_question(kind="kana_romaji"), kana_question(script="katakana")]
        new_sid = begin_kana(store, questions, finalized)
        available = summarize_queue_availability(store, now=finalized)
        assert (available.ready_count, available.waiting_count) == (2, 2)
        assert available.next_due_utc == (finalized + timedelta(seconds=60)).isoformat()
        prepared = store.prepare_next_question(new_sid, now=finalized)
        assert prepared["questions"][prepared["question_index"]].content_id == "kana-i"
        # record_review returns the live ability projection (2c44345); the raw row keeps its due.
        assert store.get_scheduling_card("kana-a", "kana_choice", "hiragana") == result["card"]
        assert store.get_card("kana-a", "kana_choice", "hiragana")["due_at_utc"] == result["due_at_utc"]
        assert store._db.execute("SELECT COUNT(*) FROM review_event").fetchone()[0] == 1


def test_kana_disable_reenable_preserves_feedback_wait_and_exact_learning_due(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        finalized = START + timedelta(days=2)
        sid = begin_kana(store, [kana_question(), kana_question("kana-i", "romaji_kana")])
        result = rate_kana(store, sid, finalized=finalized)
        store.reconcile_session_kinds(sid, {"romaji_kana"}, now=finalized)
        restored = store.reconcile_session_kinds(sid, {"romaji_kana", "kana_choice"}, now=finalized)
        assert restored["restored_count"] == 1
        restored_again = store.reconcile_session_kinds(sid, {"romaji_kana", "kana_choice"}, now=finalized)
        assert restored_again["restored_count"] == 0
        repeat = restored["questions"][-1]
        assert repeat.metadata["not_before_utc"] == result["due_at_utc"]
        availability = summarize_queue_availability(store, now=finalized)
        assert (availability.ready_count, availability.waiting_count) == (1, 1)
        assert availability.next_due_utc == (finalized + timedelta(seconds=60)).isoformat()


def test_home_unqueued_review_uses_feedback_wait_without_rewriting_due(path_app):
    app = path_app
    finalized = app._test_clock[0]
    submitted = finalized - timedelta(days=40)
    cid = next(row["id"] for row in app.repo.kana_list() if row["hiragana"] == "あ")
    sid = begin_kana(app.store, [kana_question(cid)], submitted)
    result = rate_kana(app.store, sid, rating=4, when=submitted, finalized=finalized)
    assert app.store.load_session_progress() is None
    counts = app._study_path_unqueued_reviews()
    assert counts == {"ready": 0, "waiting": 1,
                      "next_due": (finalized + timedelta(seconds=60)).isoformat()}
    # record_review returns the live ability projection (2c44345); the raw row keeps its due.
    assert app.store.get_scheduling_card(cid, "kana_choice", "hiragana") == result["card"]
    assert app.store.get_card(cid, "kana_choice", "hiragana")["due_at_utc"] == result["due_at_utc"]
    app._test_clock[0] += timedelta(seconds=60)
    assert app._study_path_unqueued_reviews() == {"ready": 1, "waiting": 0, "next_due": None}


@pytest.mark.parametrize("delay", [None, timedelta(0), timedelta(seconds=5)])
def test_kana_prompt_good_rating_does_not_change_learning_due(tmp_path, delay):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        sid = begin_kana(store, [kana_question()])
        finalized = START + delay if delay is not None else None
        result = rate_kana(store, sid, rating=3, finalized=finalized)
        due = (START + timedelta(minutes=10)).isoformat()
        assert result["due_at_utc"] == due
        assert summarize_queue_availability(store, now=START).next_due_utc == due


@pytest.mark.parametrize("course", [False, True])
@pytest.mark.parametrize("bad_time", ["broken-date", (START - timedelta(days=1)).isoformat()])
def test_invalid_feedback_finalization_time_is_rejected_atomically(tmp_path, course, bad_time):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        if course:
            learning = LearningStore(store)
            learning.enroll(item(), now=START)
            learning.append_questions(question(), now=START)
            submitted = ready(learning)
            learning.submit("q1", "right", True, now=submitted)
            learning.finalize("q1", 3, now=submitted)
        else:
            sid = begin_kana(store, [kana_question()])
            rate_kana(store, sid, finalized=START + timedelta(seconds=5))
        original = store.export_backup()
        payload = decode_current(original)
        if course:
            row = payload["tables"]["learning_event"][0]
            field = "result_json"
        else:
            row = payload["tables"]["review_event"][0]
            field = "review_log_json"
        record = json.loads(row[field])
        record["finalized_at_utc"] = bad_time
        row[field] = json.dumps(record)
        with pytest.raises(ValueError):
            store.import_backup(encode_current(payload))
        assert decode_current(store.export_backup())["tables"] == decode_current(original)["tables"]


@pytest.mark.parametrize("field,value", [
    ("submitted_at_utc", "not-a-date"), ("submitted_at_utc", "2026-10-25T12:00:00"),
    ("submitted_at_utc", None), ("submitted_at_utc", 123),
    ("answer_duration_ms", -1), ("answer_duration_ms", "1250"),
    ("answer_duration_ms", True), ("answer_duration_ms", 1.5),
])
def test_corrupt_frozen_answer_checkpoint_backup_is_rejected_atomically(tmp_path, field, value):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        sid = begin_kana(store, [kana_question()])
        store.save_session_progress(sid, 0, state={"page": "feedback", "feedback_pending": True,
            "answered_text": "a", "submitted_at_utc": START.isoformat(), "answer_duration_ms": 1250})
        original = decode_current(store.export_backup())
        payload = json.loads(json.dumps(original))
        row = payload["tables"]["session_checkpoint"][0]
        state = json.loads(row["state_json"])
        state[field] = value
        row["state_json"] = json.dumps(state)
        with pytest.raises(ValueError):
            store.import_backup(encode_current(payload))
        assert decode_current(store.export_backup())["tables"] == original["tables"]


@pytest.mark.parametrize("extra", [
    {}, {"submitted_at_utc": START.isoformat()}, {"answer_duration_ms": 0},
    {"submitted_at_utc": START.isoformat(), "answer_duration_ms": 1250},
])
def test_legacy_and_partial_frozen_answer_checkpoints_remain_importable(tmp_path, extra):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        sid = begin_kana(store, [kana_question()])
        state = {"page": "feedback", "feedback_pending": True, "answered_text": "a", **extra}
        store.save_session_progress(sid, 0, state=state)
        store.import_backup(store.export_backup())
        assert store.load_session_progress()["state"] == state
