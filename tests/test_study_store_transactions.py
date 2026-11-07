"""StudyStore writes own their transaction, like LearningStore._transaction (B5).

Joining a caller's open transaction let `with self._db:` commit the caller's
unrelated, not-yet-finished writes when the nested write finished.
"""
import pytest

from kanamemory.domain import Question, StudyStore, prepare_session_snapshot


def questions():
    return [Question("kana-3042", "kana_choice", "hiragana", "あ", "a")]


def outer_write(store):
    store._db.execute("BEGIN IMMEDIATE")
    store._db.execute("INSERT INTO app_setting VALUES ('outer_marker', '1')")


def outer_committed(store):
    return store._db.execute("SELECT 1 FROM app_setting WHERE key='outer_marker'").fetchone() is not None


def test_nested_checkpoint_save_cannot_commit_the_callers_transaction(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        sid = store.start_session(questions())
        store.save_session_progress(sid, 0, state={"page": "question"})
        before = store.load_session_progress()
        outer_write(store)
        with pytest.raises(RuntimeError, match="own transaction"):
            store.save_session_progress(sid, 0, state={"page": "question", "answered_text": "x"})
        if store._db.in_transaction:
            store._db.rollback()
        assert not outer_committed(store)
        assert store.load_session_progress() == before


def test_nested_session_start_cannot_commit_the_callers_transaction(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        prepared = prepare_session_snapshot(questions(), 0)
        outer_write(store)
        with pytest.raises(RuntimeError, match="own transaction"):
            store.start_prepared_session(prepared)
        if store._db.in_transaction:
            store._db.rollback()
        assert not outer_committed(store)
        assert store.load_session_progress() is None
        assert store._db.execute("SELECT COUNT(*) FROM study_session").fetchone()[0] == 0


def test_unnested_writes_still_commit(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        sid = store.start_prepared_session(prepare_session_snapshot(questions(), 0))
        store.save_session_progress(sid, 0, state={"page": "question", "answered_text": "x"})
        assert not store._db.in_transaction
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        assert store.load_session_progress()["state"]["answered_text"] == "x"
