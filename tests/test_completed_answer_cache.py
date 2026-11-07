"""Draft caching must never revive a committed question or change either lane."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

import pytest

from kanamemory.domain import Question, StudyStore

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
ROWS = [{"id": "kana-3042", "hiragana": "あ", "katakana": "ア"}]


def question(word=False):
    return (Question("word:one", "word_meaning_kana", "hiragana", "a", "あ", ["あ"],
                     metadata={"word_romaji_drill": True}) if word else
            Question("kana-3042", "kana_romaji", "hiragana", "あ", "a", ["a", "i"]))


def begin(store, word=False):
    q = question(word)
    sid = store.start_session([q, q, q], now=NOW)
    store.save_session_progress(sid, 0)
    return sid, q


def answer(store, sid, q):
    return store.record_review(q.content_id, q.kind, q.script,
        None if q.kind == "word_meaning_kana" else 3,
        session_id=sid, question_index=0, answered_text=q.answer,
        outcome="correct", now=NOW, **({"kana_rows": ROWS} if q.kind == "word_meaning_kana" else {}))


def aggregates(trace):
    return [sql for sql in trace if "SELECT MAX(question_index),SUM(correct)" in sql]


def test_repeated_drafts_do_not_scan_completed_history_and_remain_durable(tmp_path):
    with StudyStore(tmp_path) as store:
        main, _ = begin(store)
        word, _ = begin(store, True)
        store._completed_session_answers(main)
        store._completed_session_answers(word)
        trace = []
        store._db.set_trace_callback(trace.append)
        for value in ("a", "ab", "abc"):
            store.save_session_progress(main, 0, state={"romaji_answer": value})
            store.save_session_progress(word, 0, state={"word_answer": "あ"})
        assert not aggregates(trace)
        assert len([sql for sql in trace if sql == "COMMIT"]) == 6
    with StudyStore(tmp_path) as reopened:
        assert reopened.load_session_progress("main")["state"] == {"romaji_answer": "abc"}
        assert reopened.load_session_progress("word_romaji")["state"] == {"word_answer": "あ"}


@pytest.mark.parametrize("word", [False, True])
def test_new_answer_invalidates_cached_empty_total_and_stale_draft_cannot_replay(tmp_path, word):
    with StudyStore(tmp_path) as store:
        sid, q = begin(store, word)
        assert store._completed_session_answers(sid) == (None, None)
        first = answer(store, sid, q)
        store.save_session_progress(sid, 0, state={"answered_text": "old draft"})
        pending = store.load_session_progress("word_romaji" if word else "main")
        assert (pending["question_index"], pending["session_right"], pending["state"]) == (1, 1, {})
        assert answer(store, sid, q)["already_recorded"]
        assert store._completed_session_answers(sid) == (0, 1)
        assert first["scheduled"]


@pytest.mark.parametrize("word", [False, True])
@pytest.mark.parametrize("external", [False, True])
def test_direct_sql_update_delete_insert_invalidate_in_both_connections(tmp_path, word, external):
    with StudyStore(tmp_path) as store:
        sid, q = begin(store, word)
        answer(store, sid, q)
        table = "word_spelling_attempt" if word else "session_answer"
        row = tuple(store._db.execute("SELECT * FROM " + table).fetchone())
        assert store._completed_session_answers(sid) == (0, 1)
        db = sqlite3.connect(store.path) if external else store._db
        try:
            with db:
                db.execute("UPDATE " + table + " SET correct=0 WHERE session_id=?", (sid,))
            assert store._completed_session_answers(sid) == (0, 0)
            with db:
                db.execute("DELETE FROM " + table + " WHERE session_id=?", (sid,))
            assert store._completed_session_answers(sid) == (None, None)
            with db:
                db.execute("INSERT INTO " + table + " VALUES (" + ",".join("?" for _ in row) + ")", row)
            assert store._completed_session_answers(sid) == (0, 1)
        finally:
            if external:
                db.close()


@pytest.mark.parametrize("word", [False, True])
def test_rollback_and_reused_revision_do_not_publish_uncommitted_totals(tmp_path, word):
    with StudyStore(tmp_path) as store:
        sid, q = begin(store, word)
        answer(store, sid, q)
        table = "word_spelling_attempt" if word else "session_answer"
        assert store._completed_session_answers(sid) == (0, 1)
        for index in (1, 2):
            store._db.execute("BEGIN")
            store._db.execute("UPDATE " + table + " SET question_index=?,correct=0", (index,))
            assert store._completed_session_answers(sid) == (index, 0)
            store._db.rollback()
            assert store._completed_session_answers(sid) == (0, 1)
        with store._db:
            store._db.execute("UPDATE " + table + " SET question_index=2")
            assert store._completed_session_answers(sid) == (2, 1)
        assert store._completed_session_answers(sid) == (2, 1)


@pytest.mark.parametrize("word", [False, True])
def test_rollback_revision_reuse_without_intermediate_read(tmp_path, word):
    with StudyStore(tmp_path) as store:
        sid, q = begin(store, word)
        answer(store, sid, q)
        table = "word_spelling_attempt" if word else "session_answer"
        assert store._completed_session_answers(sid) == (0, 1)
        store._db.execute("BEGIN")
        store._db.execute("UPDATE " + table + " SET question_index=1,correct=0")
        assert store._completed_session_answers(sid) == (1, 0)
        store._db.rollback()
        # No cache read between rollback and the different committed write.
        with store._db:
            store._db.execute("UPDATE " + table + " SET question_index=2,correct=1")
        assert store._completed_session_answers(sid) == (2, 1)


def test_cache_is_bounded_and_does_not_enter_backups(tmp_path):
    with StudyStore(tmp_path) as store:
        for _ in range(5):
            sid, _ = begin(store)
            store._completed_session_answers(sid)
        assert len(store._completed_answer_cache[1]) == 2
        assert b"completed_answer_revision" not in store.export_backup()
