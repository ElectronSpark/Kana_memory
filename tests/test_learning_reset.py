"""Reset progress atomically without deleting preferences, files or new work."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys

import pytest
from record_test_support import decode_current, encode_current, as_legacy

from kanamemory.domain import Question, StudyStore
from kanamemory.practice_worker import study_revision


from support.learning_reset_support import NOW, populate
TABLES = ("word_spelling_attempt", "session_answer", "session_checkpoint", "review_event",
          "practice_attempt", "study_card", "session_question", "study_session", "learning_evidence", "learning_evidence_pattern", "learning_aux_adjustment")


def assert_session_content(store, session, expected):
    actual = store.get_session_questions(session)
    for question in actual:
        assert question.metadata['kana_ability_route_v1']['native_session_id'] == session
    assert [replace(q, metadata={k: v for k, v in q.metadata.items() if k != 'kana_ability_route_v1'}) for q in actual] == expected


def tables(store):
    return decode_current(store.export_backup())["tables"]


def assert_empty(store, session):
    assert all(store._db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0 for table in TABLES)
    assert store.load_session_progress() is None
    assert store.review_history() == []
    assert store.get_due(now=NOW + timedelta(days=365)) == []
    assert store.next_due_at() is None
    assert store.stats(NOW)["total_items"] == 0
    assert store.stats(NOW)["total_reviews"] == 0
    assert store.previous_choice_options("kana_choice", "hiragana") == {}
    with pytest.raises(ValueError, match="Unknown session_id"):
        store.get_session_questions(session)
    for content_id in ("a", "ka", "never-studied"):
        assert store.is_content_available(content_id, NOW)
        assert store.get_content_control(content_id) == {"suspended": False, "buried_until_utc": None}


def test_reset_retains_settings_files_and_allows_fresh_learning_after_reopen(tmp_path):
    with StudyStore(tmp_path, time_zone="UTC") as store:
        session, questions = populate(store)
        kept_settings = [row for row in tables(store)["app_setting"] if row["key"] not in {"study_item_controls_v1", "kana_ability_bridges_v1"}]
        backup = store.export_backup()
        files = {"learning-before-restore-saved.json": backup,
                 "exported-learning.json": backup,
                 "models/japanese-model.bin": b"downloaded model",
                 "resources/content.sqlite3": b"packaged content fixture"}
        for name, data in files.items():
            path = tmp_path / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
        result = store.clear_learning_data()
        assert result == {"deleted_rows": {"word_spelling_attempt": 0, "session_answer": 2, "session_checkpoint": 1,
            "review_event": 2, "practice_attempt": 1, "study_card": 2, "session_question": 3, "study_session": 1,
            "learning_evidence": 2, "learning_evidence_pattern": 2, "learning_aux_adjustment": 0},
            "deleted_settings": ["study_item_controls_v1", "kana_ability_bridges_v1"]}
        assert_empty(store, session)
        assert tables(store)["app_setting"] == kept_settings
        assert store.daily_new_limit == 37
        assert store.previous_backup() == backup
        assert {name: (tmp_path / name).read_bytes() for name in files} == files
        assert store._db.execute("PRAGMA foreign_key_check").fetchall() == []
    with StudyStore(tmp_path, time_zone="UTC") as reopened:
        assert_empty(reopened, session)
        assert tables(reopened)["app_setting"] == kept_settings
        new_session = reopened.start_session(questions, now=NOW + timedelta(hours=1))
        reopened.save_session_progress(new_session, 0)
        result = reopened.record_review("a", "kana_choice", "hiragana", 4,
            session_id=new_session, question_index=0, outcome="correct", now=NOW + timedelta(hours=1))
        assert result["card"]["review_count"] == 1
        assert result["card"]["suspended"] == 0
        assert reopened.stats()["total_reviews"] == 1
        assert reopened.load_session_progress()["question_index"] == 1


def test_existing_connections_invalidate_choices_snapshots_and_worker_revisions(tmp_path):
    with StudyStore(tmp_path) as store:
        session, questions = populate(store)
        with StudyStore(tmp_path) as observer:
            revisions = [study_revision(item) for item in (store, observer)]
            for item in (store, observer):
                assert_session_content(item, session, questions)
                assert item.previous_choice_options("kana_choice", "hiragana")["a"] == questions[0].options
            store.clear_learning_data()
            for index, item in enumerate((store, observer)):
                assert study_revision(item) != revisions[index]
                assert_empty(item, session)
                with pytest.raises(ValueError, match="Unknown session_id"):
                    item.record_review("a", "kana_choice", "hiragana", 4,
                        session_id=session, question_index=0, outcome="correct", now=NOW)
            replacement = Question("a", "kana_choice", "hiragana", "あ", "a", ["a", "sa", "na", "ha"])
            new_session = store.start_session([replacement])
            assert_session_content(observer, new_session, [replacement])
            assert observer.previous_choice_options("kana_choice", "hiragana") == {"a": replacement.options}


@pytest.mark.parametrize("failing_table", ["study_session", "app_setting"])
def test_delete_failure_rolls_back_records_controls_and_cached_views(tmp_path, failing_table):
    with StudyStore(tmp_path) as store:
        session, questions = populate(store)
        before = tables(store)
        pending = store.load_session_progress()
        choices = store.previous_choice_options("kana_choice", "hiragana")
        store.get_session_questions(session)
        store._db.execute("CREATE TEMP TRIGGER simulated_reset_failure BEFORE DELETE ON " + failing_table +
                          " BEGIN SELECT RAISE(ABORT, 'simulated reset failure'); END")
        with StudyStore(tmp_path) as observer:
            with pytest.raises(sqlite3.IntegrityError, match="simulated reset failure"):
                store.clear_learning_data()
            assert not store._db.in_transaction
            assert tables(store) == tables(observer) == before
            assert store.load_session_progress() == pending
            assert_session_content(store, session, questions)
            assert store.previous_choice_options("kana_choice", "hiragana") == choices
            assert not store.is_content_available("a", NOW)
        store._db.execute("DROP TRIGGER simulated_reset_failure")
        store.clear_learning_data()
        assert_empty(store, session)


def test_reset_is_repeatable_and_does_not_commit_an_existing_transaction(tmp_path):
    with StudyStore(tmp_path) as store:
        session, _ = populate(store)
        before = tables(store)
        store._db.execute("BEGIN")
        store._db.execute("INSERT INTO app_setting VALUES ('pending', '{}')")
        with pytest.raises(RuntimeError, match="active transaction"):
            store.clear_learning_data()
        assert store._db.in_transaction
        store._db.rollback()
        assert tables(store) == before
        store.clear_learning_data()
        result = store.clear_learning_data()
        assert result == {"deleted_rows": dict.fromkeys(TABLES, 0), "deleted_settings": []}
        assert_empty(store, session)


def test_reset_removes_queue_kind_policy_but_retains_ordinary_preferences(tmp_path):
    with StudyStore(tmp_path, time_zone="UTC") as store:
        session, _ = populate(store)
        preferences = store.get_setting("practice_preferences")
        store.set_setting("practice_queue_kinds", {
            "session_id": session, "kinds": ["kana_choice"],
            "regular_kinds": ["kana_choice", "kana_handwriting"]})
        result = store.clear_learning_data()
        assert "practice_queue_kinds" in result["deleted_settings"]
        assert store.get_setting("practice_queue_kinds") is None
        assert store.get_setting("practice_preferences") == preferences
        assert store.load_session_progress() is None
