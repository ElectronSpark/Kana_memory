"""One-way conversion preserves learner facts and discards the old storage."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from kanamemory import record_schema, session_questions
from kanamemory.domain import Question, StudyStore

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def question(symbol="あ", roman="a"):
    return Question("kana-3042" if roman == "a" else "kana-3044", "kana_romaji", "hiragana",
                    symbol, roman, [roman], metadata={"nested": [True, 1, 1.0]})


def legacy_database(path, *, malformed=False):
    """Build a synthetic old on-disk store without importing an old runtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    for index, sql in enumerate(record_schema.BASE_DDL):
        if index in (0, 3):
            continue
        if index == 2:
            sql = "CREATE TABLE study_session " + record_schema.SESSION_DEFINITION[:-1] + ",question_snapshot_json TEXT NOT NULL)"
        db.execute(sql)
    first = json.dumps(question().to_dict(), ensure_ascii=False, indent=2)
    second = json.dumps(question("い", "i").to_dict(), ensure_ascii=False)
    raw = "[\n " + first + ",\n\t" + second + "\n]"
    db.execute("INSERT INTO study_session(rowid,session_id,started_at_utc,timezone,random_seed,policy_version,question_snapshot_json) VALUES (42,?,?,?,?,?,?)",
               ("unfinished", NOW.isoformat(), "UTC", "random", "policy", "[{}" if malformed else raw))
    state = json.dumps({"page":"question", "strokes":[[[1.25,2.5,3],[4,5,6]]], "answered_text":"a"})
    db.execute("INSERT INTO session_checkpoint VALUES (?,?,?,?,?,?)", ("unfinished", NOW.isoformat(), 0, 0, "[]", state))
    db.execute("INSERT INTO app_setting VALUES ('study_path_v1',?)", ('{"reserved_entries":["example"]}',))
    db.commit()
    db.close()
    return [first, second], state


def columns(db, table):
    return tuple(row[1] for row in db.execute("PRAGMA table_info("+table+")"))


def test_new_store_only_creates_current_schema(tmp_path):
    with StudyStore(tmp_path, time_zone="UTC") as store:
        assert store.record_upgrade["converted"] is False
        assert columns(store._db,"study_session") == record_schema.SESSION_COLUMNS
        assert columns(store._db,"session_question") == ("session_id","question_index","snapshot_json")
        assert store._db.execute("SELECT value FROM learner_record_meta WHERE key='schema_version'").fetchone()[0] == "1"
        assert not store._db.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall()


def test_local_upgrade_preserves_frozen_bytes_order_draft_rowid_and_recovery(tmp_path):
    old, state = legacy_database(tmp_path / "progress.sqlite3")
    with StudyStore(tmp_path, time_zone="UTC") as store:
        assert store.record_upgrade["converted"] is True
        assert store.record_upgrade["session_questions"] == 2
        assert columns(store._db,"study_session") == record_schema.SESSION_COLUMNS
        assert store._db.execute("SELECT rowid FROM study_session").fetchone()[0] == 42
        assert [row[0] for row in store._db.execute("SELECT snapshot_json FROM session_question ORDER BY question_index")] == old
        assert store._db.execute("SELECT state_json FROM session_checkpoint").fetchone()[0] == state
        assert store.get_setting("study_path_v1") == {"reserved_entries":["example"]}
        assert store.load_session_progress()["state"]["strokes"] == [[[1.25,2.5,3],[4,5,6]]]
        assert store._db.execute("PRAGMA foreign_key_check").fetchall() == []
        backup = Path(store.record_upgrade["recovery_path"])
        assert backup.is_file()
        with sqlite3.connect(backup) as recovered:
            assert "question_snapshot_json" in columns(recovered,"study_session")
            assert recovered.execute("SELECT state_json FROM session_checkpoint").fetchone()[0] == state
    backups = set(tmp_path.glob("learning-before-format-*.sqlite3"))
    with StudyStore(tmp_path, time_zone="UTC") as store:
        assert store.record_upgrade == {"converted":False,"schema_version":1}
        assert len(store.get_session_questions("unfinished")) == 2
    assert set(tmp_path.glob("learning-before-format-*.sqlite3")) == backups


def test_malformed_old_snapshot_rolls_back_all_schema_and_keeps_fk(tmp_path):
    legacy_database(tmp_path / "progress.sqlite3", malformed=True)
    db=sqlite3.connect(tmp_path/"progress.sqlite3")
    db.execute("PRAGMA foreign_keys=ON")
    before=list(db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name"))
    with pytest.raises(ValueError):
        record_schema.ensure_current(db,tmp_path)
    assert not db.in_transaction
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert list(db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name")) == before
    assert db.execute("SELECT question_snapshot_json FROM study_session").fetchone()[0] == "[{}"
    db.close()


def test_recovery_failure_prevents_mutation(tmp_path, monkeypatch):
    legacy_database(tmp_path/"progress.sqlite3")
    db=sqlite3.connect(tmp_path/"progress.sqlite3")
    before=list(db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name"))
    def fail(*args):
        raise OSError("recovery storage unavailable")
    monkeypatch.setattr(record_schema,"_recovery_copy",fail)
    with pytest.raises(OSError):
        record_schema.ensure_current(db,tmp_path)
    assert list(db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name")) == before
    db.close()


def test_recovery_copy_includes_committed_wal_and_not_only_main_file(tmp_path):
    legacy_database(tmp_path / "progress.sqlite3")
    writer = sqlite3.connect(tmp_path / "progress.sqlite3")
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO app_setting VALUES ('wal_only','{\"draft\":\"未提交的字迹\"}')")
        writer.commit()
        assert Path(str(tmp_path / "progress.sqlite3") + "-wal").stat().st_size > 0
        with StudyStore(tmp_path) as store:
            assert store.get_setting("wal_only") == {"draft": "未提交的字迹"}
            recovered = sqlite3.connect(store.record_upgrade["recovery_path"])
            try:
                assert json.loads(recovered.execute(
                    "SELECT value_json FROM app_setting WHERE key='wal_only'").fetchone()[0]) == {"draft": "未提交的字迹"}
            finally:
                recovered.close()
    finally:
        writer.close()


def test_out_of_range_legacy_checkpoint_prevents_conversion(tmp_path):
    legacy_database(tmp_path / "progress.sqlite3")
    db = sqlite3.connect(tmp_path / "progress.sqlite3")
    try:
        db.execute("UPDATE session_checkpoint SET question_index=3")
        db.commit()
        with pytest.raises(ValueError, match="超出"):
            record_schema.ensure_current(db, tmp_path)
        assert "question_snapshot_json" in columns(db, "study_session")
        assert not columns(db, "learner_record_meta")
        assert not columns(db, "session_question")
        assert db.execute("SELECT question_index FROM session_checkpoint").fetchone()[0] == 3
    finally:
        db.close()


def test_unknown_schema_refuses_instead_of_downgrading(tmp_path):
    with StudyStore(tmp_path) as store:
        store._db.execute("UPDATE learner_record_meta SET value='99' WHERE key='schema_version'")
        store._db.commit()
    with pytest.raises(ValueError,match="版本"):
        StudyStore(tmp_path)
    with sqlite3.connect(tmp_path/"progress.sqlite3") as db:
        assert db.execute("SELECT value FROM learner_record_meta").fetchone()[0] == "99"


def test_append_keeps_old_question_bytes_and_failed_write_rolls_back(tmp_path):
    old,_=legacy_database(tmp_path/"progress.sqlite3")
    with StudyStore(tmp_path) as store:
        prior=store.get_session_questions("unfinished",lazy=True)
        with store._db:
            store._db.execute("BEGIN")
            session_questions.append(store._db,"unfinished",[question("う","u").to_dict()])
        assert len(prior)==2 and len(store.get_session_questions("unfinished",lazy=True))==3
        assert [row[0] for row in store._db.execute("SELECT snapshot_json FROM session_question ORDER BY question_index LIMIT 2")] == old
        snapshot=session_questions.load(store._db,"unfinished")
        with pytest.raises(RuntimeError), store._db:
            store._db.execute("BEGIN")
            session_questions.replace(store._db,"unfinished",[question().to_dict()])
            raise RuntimeError("test rollback")
        assert session_questions.load(store._db,"unfinished")==snapshot


def test_identical_queue_rows_skip_reparsing_but_preserve_legacy_bytes_and_json_types(tmp_path, monkeypatch):
    old, _ = legacy_database(tmp_path / "progress.sqlite3")
    with StudyStore(tmp_path) as store:
        payload = [question().to_dict() for _ in range(1000)]
        with store._db:
            store._db.execute("BEGIN")
            session_questions.append(store._db, "unfinished", payload)
        snapshots = session_questions.load(store._db, "unfinished")
        calls = []
        loads = json.loads
        def counted(text, *args, **kwargs):
            calls.append(text)
            return loads(text, *args, **kwargs)
        monkeypatch.setattr(session_questions.json, "loads", counted)
        with store._db:
            store._db.execute("BEGIN")
            session_questions.replace(store._db, "unfinished", snapshots)
        # Only the two noncanonical converted rows need the compatibility path.
        assert calls == old
        assert [r[0] for r in store._db.execute("SELECT snapshot_json FROM session_question WHERE question_index<2 ORDER BY question_index")] == old
        snapshots[2]['metadata']['nested'][0] = 1  # True == 1 is insufficient.
        with store._db:
            store._db.execute("BEGIN")
            session_questions.replace(store._db, "unfinished", snapshots)
        saved = loads(store._db.execute("SELECT snapshot_json FROM session_question WHERE question_index=2").fetchone()[0])
        assert type(saved['metadata']['nested'][0]) is int


@pytest.mark.parametrize("raw", ['[{"x":1,"x":2}]', '[null]', '[{}] true', '[{},]', '[{"x":NaN}]'])
def test_legacy_splitter_rejects_ambiguous_or_non_object_data(raw):
    with pytest.raises(ValueError):
        list(record_schema.iter_legacy_questions(raw))
