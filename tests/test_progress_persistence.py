from record_test_support import decode_current, encode_current
"""Crash recovery and data backups preserve learner-owned scheduling state."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from support.session_snapshots import unrouted

from kanamemory.domain import Question, StudyStore

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def questions():
    return [Question(c, "kana_choice", "hiragana", "", answer, audio_text=answer,
                     metadata={"content_version": "fixture"}) for c, answer in (("a", "あ"), ("ka", "か"))]


def test_restart_restores_settings_assisted_feedback_and_strokes(tmp_path):
    state = {"answer_revealed": True, "answer_correct": False, "feedback_pending": True,
             "answered_text": "あ", "strokes": [[[1, 2, 3], [4, 5, 6]]], "ink_width": 360, "ink_height": 210}
    with StudyStore(tmp_path) as store:
        store.set_setting("practice_preferences", {"kind": "kana_handwriting", "count": 5})
        session = store.start_session(questions())
        store.save_session_progress(session, 0, assisted_indices=[0], state=state)
    with StudyStore(tmp_path) as reopened:
        assert reopened.get_setting("practice_preferences")["count"] == 5
        pending = reopened.load_session_progress()
        assert unrouted(pending["questions"]) == questions()
        assert pending["assisted_indices"] == [0] and pending["state"] == state


def test_rating_advances_checkpoint_atomically_and_cannot_be_replayed(tmp_path):
    with StudyStore(tmp_path) as store:
        session = store.start_session(questions())
        store.save_session_progress(session, 0, assisted_indices=[0], state={"answer_revealed": True})
        first = store.record_review("a", "kana_choice", "hiragana", 1, session_id=session,
                                    question_index=0, assisted=True, outcome="incorrect", now=NOW)
    with StudyStore(tmp_path) as store:
        pending = store.load_session_progress()
        assert pending["question_index"] == 1 and pending["session_right"] == 0
        assert pending["state"] == {} and pending["assisted_indices"] == [0]
        replay = store.record_review("a", "kana_choice", "hiragana", 1, session_id=session,
                                     question_index=0, assisted=True, now=NOW)
        assert replay["already_recorded"] and replay["event_id"] == first["event_id"]
        assert store.stats(NOW)["total_reviews"] == 1
        # A late UI checkpoint cannot roll back a committed rating.
        store.save_session_progress(session, 0, state={"feedback_pending": True})
        assert store.load_session_progress()["question_index"] == 1
        store.record_review("ka", "kana_choice", "hiragana", 3, session_id=session,
                            question_index=1, outcome="correct", now=NOW)
        assert store.load_session_progress() is None


def test_mismatched_saved_question_does_not_write_any_review(tmp_path):
    with StudyStore(tmp_path) as store:
        session = store.start_session(questions())
        with pytest.raises(ValueError, match="match"):
            store.record_review("ka", "kana_choice", "hiragana", 3, session_id=session, question_index=0)
        assert store.stats()["total_reviews"] == 0


def filled_store(path):
    store = StudyStore(path, time_zone="UTC")
    store.set_setting("practice_preferences", {"kind": "kana_choice", "count": 10})
    session = store.start_session(questions(), now=NOW)
    store.save_session_progress(session, 0)
    store.record_review("a", "kana_choice", "hiragana", 3, session_id=session,
                        question_index=0, outcome="correct", now=NOW)
    store.save_session_progress(session, 1, session_right=1, assisted_indices=[1],
                                state={"answer_revealed": True, "feedback_pending": True})
    return store


def test_backup_round_trip_retains_fsrs_preferences_and_pending_attempt(tmp_path):
    with filled_store(tmp_path / "source") as source, StudyStore(tmp_path / "target") as target:
        backup = source.export_backup()
        summary = target.inspect_backup(backup)
        assert summary["cards"] == summary["reviews"] == 1 and summary["has_unfinished_session"]
        # Inspection is read-only.
        assert target.stats()["total_reviews"] == 0
        target.set_setting("old_setting", "recover me")
        imported = target.import_backup(backup)
        assert target.review_history() == source.review_history()
        assert target.get_card("a", "kana_choice", "hiragana") == source.get_card("a", "kana_choice", "hiragana")
        assert target.load_session_progress() == source.load_session_progress()
        assert target.get_setting("practice_preferences") == source.get_setting("practice_preferences")
        from pathlib import Path
        rollback = Path(imported["rollback_path"])
        assert rollback.is_file()
        assert target.previous_backup() == rollback.read_bytes()
        target.import_backup(rollback.read_bytes())
        assert target.get_setting("old_setting") == "recover me" and target.stats()["total_reviews"] == 0


@pytest.mark.parametrize("corruption", ["version", "sql", "foreign_key", "scheduler", "checkpoint", "missing_table", "nan", "review_key"])
def test_invalid_backups_leave_live_database_and_files_unchanged(tmp_path, corruption):
    with filled_store(tmp_path / "source") as source, filled_store(tmp_path / "target") as target:
        data = decode_current(source.export_backup())
        if corruption == "version": data["version"] = 999
        elif corruption == "sql": data["tables"]["app_setting"][0]["sql"] = "DELETE FROM study_card"
        elif corruption == "foreign_key": data["tables"]["review_event"][0]["card_id"] = "missing"
        elif corruption == "scheduler": data["tables"]["study_card"][0]["scheduler_version"] = "old"
        elif corruption == "checkpoint": data["tables"]["session_checkpoint"][0]["question_index"] = 0
        elif corruption == "missing_table": del data["tables"]["study_card"]
        elif corruption == "nan": data["tables"]["session_checkpoint"][0]["state_json"] = '{"ink_width":NaN}'
        elif corruption == "review_key": data["tables"]["session_answer"][0]["question_index"] = 1
        before = decode_current(target.export_backup())["tables"]
        with pytest.raises(ValueError): target.import_backup(encode_current(data))
        assert decode_current(target.export_backup())["tables"] == before
        assert not list(target.data_dir.glob("learning-before-restore-*.json"))


def test_restore_write_failure_rolls_back_all_live_tables(tmp_path):
    import sqlite3
    with filled_store(tmp_path / "source") as source, filled_store(tmp_path / "target") as target:
        before = decode_current(target.export_backup())["tables"]
        target._db.execute("CREATE TRIGGER simulated_disk_failure BEFORE INSERT ON study_card BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match="simulated"):
            target.import_backup(source.export_backup())
        assert decode_current(target.export_backup())["tables"] == before
        assert decode_current(target.previous_backup())["tables"] == before


def test_recent_history_does_not_fetch_or_decode_scheduler_payloads(tmp_path):
    with StudyStore(tmp_path) as store:
        for i in range(5):
            store.record_review("a", "kana_choice", "hiragana", 3, now=NOW + timedelta(days=i))
        recent = store.review_history(limit=2, newest_first=True, include_states=False)
        assert len(recent) == 2
        assert recent[0]["occurred_at_utc"] > recent[1]["occurred_at_utc"]
        assert all("state_before_json" not in item and "state_after" not in item for item in recent)


def test_plan_counts_and_due_groups_are_read_only(tmp_path):
    with StudyStore(tmp_path) as store:
        store.record_review("a", "kana_choice", "hiragana", 1, now=NOW)
        later = NOW + timedelta(days=1)
        assert store.practice_counts(questions(), now=later) == {"due": 1, "new": 1}
        assert store.due_groups(later) == [{"skill": "kana_choice", "script": "hiragana", "due": 1}]
        assert store.stats(later)["total_reviews"] == 1
