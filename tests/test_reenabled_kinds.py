"""Re-enabled directions continue their original cards without duplicate reviews."""
from runtime_record_test_support import snapshot_json, write_snapshot
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

import pytest
from fsrs import Card, Rating

from kanamemory.domain import ALGORITHM_VERSION, StudyStore, build_practice_batch
from support.domain_support import SmallContent
from support.domain_support import START


KINDS = ("kana_choice", "kana_romaji", "romaji_kana", "romaji_kana_full",
         "romaji_handwriting", "kana_handwriting")
from support.reenabled_kinds_support import EVIDENCE, evidence, plan, start, rate, filtered_learning_queue


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("script", ("hiragana", "katakana"))
def test_reenabled_card_is_due_once_and_continues_original_memory(store, kind, script):
    session, learned, keep, original_card = filtered_learning_queue(store, kind, script)
    original_evidence = evidence(store)
    original_repeat = store.load_session_progress()["questions"][1]
    # Repeated preference changes restore one live timed copy and never
    # manufacture a review or duplicate an already pending direction.
    for _ in range(3):
        enabled = store.reconcile_session_kinds(session, (keep.kind, kind))
        assert enabled["changed"] and enabled["restored_count"] == 1
        assert enabled["questions"][enabled["question_index"]:] == [keep, original_repeat]
        assert not store.reconcile_session_kinds(session, (keep.kind, kind))["changed"]
        assert store.reconcile_session_kinds(session, (keep.kind,))["changed"]
    enabled = store.reconcile_session_kinds(session, (keep.kind, kind))
    assert evidence(store) == original_evidence
    rate(store, session, enabled["question_index"], 4)

    due = datetime.fromisoformat(original_card["due_at_utc"])
    for now in (START, due - timedelta(microseconds=1)):
        assert learned.content_id not in {q.content_id for q in plan(store, kind, script, now)}
        assert not store.get_due(kind, script, now=now)
        assert store.prepare_next_question(session, now=now)["waiting_until_utc"] == original_card["due_at_utc"]
    for now in (due, due + timedelta(days=1)):
        assert learned.content_id not in {
            q.content_id for q in plan(store, kind, script, now, new_only=True)}
        resumed = [q for q in plan(store, kind, script, now) if q.content_id == learned.content_id]
        assert len(resumed) == 1
        assert store.batch_counts(resumed, now=now) == {
            "content_count": 1, "total": 1, "due": 1, "new": 0}
    assert store.get_scheduling_card(learned.content_id, kind, script) == original_card

    ready = store.prepare_next_question(session, now=due)
    assert ready["waiting_until_utc"] is None
    index = ready["question_index"]
    assert ready["questions"][index:] == [original_repeat]
    reviewed = rate(store, session, index, 4, now=due)
    card = reviewed["card"]
    assert card["card_id"] == original_card["card_id"]
    assert card["introduced_at_utc"] == original_card["introduced_at_utc"]
    assert card["review_count"] == 2
    history = store.review_history(learned.content_id, kind, script)
    assert len(history) == 2
    assert history[1]["state_before"] == original_card["scheduler_state"]
    assert history[1]["state_after"] == card["scheduler_state"]
    # A delayed callback for this same answered occurrence is idempotent.
    assert rate(store, session, index, 4, now=due)["already_recorded"]
    assert store.get_scheduling_card(learned.content_id, kind, script) == card
    assert len(store.review_history(learned.content_id, kind, script)) == 2
    assert learned.content_id not in {q.content_id for q in plan(store, kind, script, due)}
    assert store.load_session_progress() is None


@pytest.mark.parametrize("kind", ("word_kana", "word_meaning_kana"))
@pytest.mark.parametrize("script", ("hiragana", "katakana"))
def test_reenabled_legacy_word_timer_stays_retired_and_new_context_does_not_restart_fsrs(store, kind, script):
    # Build the previous release's answered word and its pending timed copy.
    # Current word submissions deliberately cannot create this old state.
    learned = next(q for q in plan(store, kind, script, START) if q.content_id == "word-reviewed")
    keep = next(q for q in plan(store, "kana_choice", script, START) if q.content_id == "kana-1")
    key = store._card_key(learned.content_id, kind, script)
    initial = Card(card_id=int(key[:15], 16), due=START)
    updated, log = store.scheduler.review_card(initial, Rating.Again, review_datetime=START)
    repeat = replace(learned, metadata={**learned.metadata,
        "not_before_utc": updated.due.isoformat(), "relearning_of_index": 0})
    session = start(store, [learned, keep, repeat])
    learned, keep, repeat = store.get_session_questions(session)
    event = str(uuid4())
    with store._db:
        store._db.execute("INSERT INTO study_card VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            key, learned.content_id, "fixture", kind, script, updated.to_json(),
            ALGORITHM_VERSION, updated.due.isoformat(), START.isoformat(), 1, 0))
        store._db.execute("INSERT INTO review_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            event, session, key, START.isoformat(), 1, "", 0, "incorrect", 100,
            initial.to_json(), updated.to_json(), ALGORITHM_VERSION, log.to_json()))
        store._db.execute("INSERT INTO session_answer VALUES (?,?,?,?)", (session, 0, event, 0))
    store.save_session_progress(session, 1)
    original = store.get_card(learned.content_id, kind, script)
    frozen = evidence(store)

    for _ in range(3):
        store.reconcile_session_kinds(session, (keep.kind,), now=START)
        enabled = store.reconcile_session_kinds(session, (keep.kind, kind), now=START)
        assert enabled["restored_count"] == 0
        assert enabled["questions"][enabled["question_index"]:] == [keep]
        assert evidence(store) == frozen
        assert store.prepare_next_question(session, now=START)["waiting_until_utc"] is None
    rate(store, session, enabled["question_index"], 4)
    assert store.load_session_progress() is None

    for now in (START, updated.due, updated.due + timedelta(days=1)):
        assert store.get_due(kind, script, now=now) == []
        examples = plan(store, kind, script, now, new_only=True)
        assert len(examples) == 1 and examples[0].content_id == learned.content_id
        assert "not_before_utc" not in examples[0].metadata
        assert store.batch_counts(examples, now) == {
            "content_count": 0, "total": 1, "due": 0, "new": 0,
            "auxiliary_count": 1, "word_count": 1}

    current = start(store, examples, now=updated.due)
    submit = dict(session_id=current, question_index=0, answered_text=examples[0].answer,
                  outcome="correct", kana_rows=SmallContent().kana_list(), now=updated.due)
    result = store.record_review(learned.content_id, kind, script, None, **submit)
    assert result["card"] is None and result["due_at_utc"] is None
    assert not result["queue_extended"]
    assert store.record_review(learned.content_id, kind, script, None, **submit)["already_recorded"]
    assert store.get_card(learned.content_id, kind, script) == original
    assert len(store.review_history(learned.content_id, kind, script)) == 1
    assert len(evidence(store)["word_spelling_attempt"]) == 1
    assert store.load_session_progress() is None


@pytest.mark.parametrize("restore", (False, True))
def test_reenabled_timing_and_reservations_survive_restart_or_backup(tmp_path, restore):
    source = tmp_path / "source"
    kind, script = "kana_handwriting", "hiragana"
    with StudyStore(source, time_zone="UTC") as store:
        session, learned, keep, card = filtered_learning_queue(store, kind, script)
        frozen = evidence(store)
        pending = store.load_session_progress()
        backup = store.export_backup()
    target = tmp_path / "restored" if restore else source
    with StudyStore(target, time_zone="UTC") as store:
        if restore:
            store.import_backup(backup)
        assert evidence(store) == frozen
        assert store.load_session_progress() == pending
        enabled = store.reconcile_session_kinds(session, (keep.kind, kind))
        assert enabled["restored_count"] == 1
        assert enabled["questions"][enabled["question_index"]:] == [keep, pending["questions"][1]]
        assert not store.reconcile_session_kinds(session, (keep.kind, kind))["changed"]
        assert evidence(store) == frozen
        # Even filtered/unanswered items stay reserved while this queue lives.
        assert {learned.content_id, keep.content_id} <= store.queued_content_ids()
        assert not {learned.content_id, keep.content_id}.intersection(
            q.content_id for q in plan(store, kind, script, START, new_only=True))
        rate(store, session, pending["question_index"], 4)
        due = datetime.fromisoformat(card["due_at_utc"])
        assert store.prepare_next_question(session, now=START)["waiting_until_utc"] == card["due_at_utc"]
        assert learned.content_id not in {q.content_id for q in plan(store, kind, script, due - timedelta(seconds=1))}
        due_questions = [q for q in plan(store, kind, script, due) if q.content_id == learned.content_id]
        assert len(due_questions) == 1
        assert store.batch_counts(due_questions, now=due)["new"] == 0
        assert store.get_card(learned.content_id, kind, script) == card


def test_skipped_ungraded_introductory_question_is_not_restored(store):
    source = plan(store, "kana_handwriting", "hiragana", START)[0]
    keep = plan(store, "kana_choice", "hiragana", START)[0]
    session = start(store, [source, keep])
    store.reconcile_session_kinds(session, (keep.kind,))
    pending = store.load_session_progress()
    enabled = store.reconcile_session_kinds(session, (keep.kind, source.kind))
    assert not enabled["changed"] and enabled["restored_count"] == 0
    assert store.load_session_progress() == pending
    assert not store.review_history()


@pytest.mark.parametrize("action", ("suspend", "bury", "direction"))
def test_reenabled_kind_does_not_override_paused_or_deferred_card(store, action):
    session, learned, keep, card = filtered_learning_queue(store, "kana_handwriting", "hiragana")
    if action == "direction":
        with store._db:
            store._db.execute("UPDATE study_card SET suspended=1 WHERE card_id=?", (card["card_id"],))
    else:
        store.set_content_control(learned.content_id, action, now=START)
    frozen = evidence(store)
    assert not store.reconcile_session_kinds(session, (keep.kind, learned.kind), now=START)["changed"]
    assert evidence(store) == frozen
    if action == "direction":
        with store._db:
            store._db.execute("UPDATE study_card SET suspended=0 WHERE card_id=?", (card["card_id"],))
    else:
        store.set_content_control(learned.content_id, "resume" if action == "suspend" else "unbury", now=START)
    assert store.reconcile_session_kinds(session, (keep.kind, learned.kind), now=START)["restored_count"] == 1


def test_newer_review_invalidates_old_repeat_even_when_due_is_identical(store):
    session, learned, keep, card = filtered_learning_queue(store, "kana_handwriting", "hiragana")
    # Again at the same instant yields the same one-minute due, so matching
    # only the timestamp would incorrectly resurrect an obsolete occurrence.
    newer = store.record_review(learned.content_id, learned.kind, learned.script, 1, now=START)
    assert newer["due_at_utc"] == card["due_at_utc"]
    frozen = evidence(store)
    enabled = store.reconcile_session_kinds(session, (keep.kind, learned.kind))
    assert not enabled["changed"] and enabled["restored_count"] == 0
    assert evidence(store) == frozen


def test_answered_restored_repeat_does_not_return_after_another_toggle(store):
    session, learned, keep, card = filtered_learning_queue(store, "kana_handwriting", "hiragana")
    due = datetime.fromisoformat(card["due_at_utc"])
    extra = next(q for q in plan(store, keep.kind, keep.script, START) if q.content_id == "kana-2")
    store.append_session(session, [extra], expected_token=store.session_append_token(session))
    extra = store.get_session_questions(session)[-1]
    store.reconcile_session_kinds(session, (keep.kind, learned.kind))
    # Present one different item first so the same-content separation rule
    # does not delay the due repetition behind the remaining visual item.
    ready = store.prepare_next_question(session, now=due)
    assert ready["questions"][ready["question_index"]] == keep
    rate(store, session, ready["question_index"], 4, now=due)
    ready = store.prepare_next_question(session, now=due)
    assert ready["questions"][ready["question_index"]].kind == learned.kind
    reviewed = rate(store, session, ready["question_index"], 4, now=due)
    assert reviewed["card"]["review_count"] == 2
    pending = store.load_session_progress()
    assert pending["questions"][pending["question_index"]:] == [extra]
    frozen = evidence(store)
    store.reconcile_session_kinds(session, (keep.kind,))
    assert not store.reconcile_session_kinds(session, (keep.kind, learned.kind))["changed"]
    assert evidence(store) == frozen
    assert store.load_session_progress() == pending


def test_restore_rolls_back_whole_queue_on_failed_snapshot_write(store):
    session, learned, keep, _ = filtered_learning_queue(store, "kana_handwriting", "hiragana")
    pending, frozen = store.load_session_progress(), evidence(store)
    store._db.execute("CREATE TEMP TRIGGER reject_restore BEFORE INSERT ON session_question "
                      "BEGIN SELECT RAISE(ABORT, 'restore failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="restore failure"):
        store.reconcile_session_kinds(session, (keep.kind, learned.kind))
    assert store.load_session_progress() == pending
    assert evidence(store) == frozen and not store._db.in_transaction


def test_restore_keeps_existing_queue_when_snapshot_budget_is_exhausted(store, monkeypatch):
    session, learned, keep, _ = filtered_learning_queue(store, "kana_handwriting", "hiragana")
    pending, frozen = store.load_session_progress(), evidence(store)
    snapshot = snapshot_json(store._db, session)
    monkeypatch.setattr("kanamemory.progress_backup.MAX_BYTES", len(snapshot.encode("utf-8")))
    with pytest.raises(ValueError, match="记录过大"):
        store.reconcile_session_kinds(session, (keep.kind, learned.kind))
    assert store.load_session_progress() == pending
    assert evidence(store) == frozen and not store._db.in_transaction


def test_hint_on_later_archived_copy_stays_assisted_after_repeated_toggles(store):
    session, learned, keep, card = filtered_learning_queue(store, "kana_handwriting", "hiragana")
    store.reconcile_session_kinds(session, (keep.kind, learned.kind))
    extra = next(q for q in plan(store, keep.kind, keep.script, START) if q.content_id == "kana-2")
    store.append_session(session, [extra], expected_token=store.session_append_token(session))
    extra = store.get_session_questions(session)[-1]
    rate(store, session, 2, 4)
    due = datetime.fromisoformat(card["due_at_utc"])
    ready = store.prepare_next_question(session, now=due)
    index = ready["question_index"]
    assert ready["questions"][index].kind == learned.kind
    store.save_session_progress(session, index, ready["session_right"], [index],
        {"page": "feedback", "answer_revealed": True, "feedback_pending": True})
    frozen = evidence(store)
    # The first archived timed copy (index 1) has no assistance. A later
    # restored copy was revealed; deduplication must retain that information.
    assert 1 not in store.load_session_progress()["assisted_indices"]
    for _ in range(3):
        store.reconcile_session_kinds(session, (keep.kind,))
        enabled = store.reconcile_session_kinds(session, (keep.kind, learned.kind))
        assert enabled["restored_count"] == 1
        repeat_index = len(enabled["questions"]) - 1
        assert repeat_index in enabled["assisted_indices"]
        assert enabled["state"] == {}
        assert enabled["questions"][enabled["question_index"]:] == [extra, enabled["questions"][repeat_index]]
    assert evidence(store) == frozen


def test_same_kind_other_script_is_not_used_as_the_restoration_evidence(store):
    session, learned, keep, card = filtered_learning_queue(store, "kana_handwriting", "hiragana")
    store.record_review(learned.content_id, learned.kind, "katakana", 1, now=START)
    enabled = store.reconcile_session_kinds(session, (keep.kind, learned.kind))
    live = enabled["questions"][enabled["question_index"]:]
    assert [(q.content_id, q.kind, q.script) for q in live] == [
        (keep.content_id, keep.kind, keep.script), (learned.content_id, learned.kind, learned.script)]
    assert live[-1].metadata["not_before_utc"] == card["due_at_utc"]


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as value:
        yield value
