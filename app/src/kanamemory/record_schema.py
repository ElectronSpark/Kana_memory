"""One current learner schema, with a single transactional local upgrade gate.

The aggregate session column is accepted only by this one-time converter.
Normal runtime code always uses session_question and schema version 1.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

VERSION = 1
TABLES = ("learner_record_meta", "app_setting", "study_session", "session_question",
          "study_card", "review_event", "practice_attempt", "session_checkpoint",
          "session_answer", "word_spelling_attempt")
SESSION_COLUMNS = ("session_id", "started_at_utc", "timezone", "random_seed", "policy_version")
SESSION_DEFINITION = """(
    session_id TEXT PRIMARY KEY,started_at_utc TEXT NOT NULL,timezone TEXT NOT NULL,
    random_seed TEXT NOT NULL,policy_version TEXT NOT NULL)"""
BASE_DDL = (
    "CREATE TABLE IF NOT EXISTS learner_record_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY,value_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS study_session " + SESSION_DEFINITION,
    """CREATE TABLE IF NOT EXISTS session_question (
        session_id TEXT NOT NULL REFERENCES study_session,question_index INTEGER NOT NULL CHECK(question_index>=0),
        snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)),PRIMARY KEY(session_id,question_index))""",
    """CREATE TABLE IF NOT EXISTS study_card (
        card_id TEXT PRIMARY KEY,content_id TEXT NOT NULL,content_version TEXT NOT NULL,
        skill TEXT NOT NULL,script TEXT NOT NULL,scheduler_state_json TEXT NOT NULL,
        scheduler_version TEXT NOT NULL,due_at_utc TEXT NOT NULL,
        introduced_at_utc TEXT,review_count INTEGER NOT NULL DEFAULT 0,
        suspended INTEGER NOT NULL DEFAULT 0,UNIQUE(content_id,skill,script))""",
    """CREATE TABLE IF NOT EXISTS review_event (
        event_id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES study_session,
        card_id TEXT NOT NULL REFERENCES study_card,occurred_at_utc TEXT NOT NULL,
        rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 4),answered_text TEXT,
        assisted INTEGER NOT NULL,outcome TEXT NOT NULL,duration_ms INTEGER NOT NULL,
        state_before_json TEXT NOT NULL,state_after_json TEXT NOT NULL,
        algorithm_version TEXT NOT NULL,review_log_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS practice_attempt (
        attempt_id TEXT PRIMARY KEY,content_id TEXT NOT NULL,skill TEXT NOT NULL,
        script TEXT NOT NULL,occurred_at_utc TEXT NOT NULL,outcome TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS session_checkpoint (
        session_id TEXT PRIMARY KEY REFERENCES study_session,updated_at_utc TEXT NOT NULL,
        question_index INTEGER NOT NULL,session_right INTEGER NOT NULL,
        assisted_indices_json TEXT NOT NULL,state_json TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS session_answer (
        session_id TEXT NOT NULL REFERENCES study_session,question_index INTEGER NOT NULL,
        event_id TEXT NOT NULL UNIQUE REFERENCES review_event,correct INTEGER NOT NULL,
        PRIMARY KEY(session_id,question_index))""",
    """CREATE TABLE IF NOT EXISTS word_spelling_attempt (
        attempt_id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES study_session,
        question_index INTEGER NOT NULL,content_id TEXT NOT NULL,skill TEXT NOT NULL,
        script TEXT NOT NULL,occurred_at_utc TEXT NOT NULL,answered_text TEXT NOT NULL,
        assisted INTEGER NOT NULL CHECK(assisted IN (0,1)),
        correct INTEGER NOT NULL CHECK(correct IN (0,1)),duration_ms INTEGER NOT NULL,
        evidence_json TEXT NOT NULL,UNIQUE(session_id,question_index))""",
    "CREATE INDEX IF NOT EXISTS card_due ON study_card(due_at_utc,skill,script)",
    "CREATE INDEX IF NOT EXISTS reviews_time ON review_event(occurred_at_utc)",
    "CREATE INDEX IF NOT EXISTS reviews_card ON review_event(card_id)",
    """CREATE INDEX IF NOT EXISTS reviews_finalized_card ON review_event(card_id)
        WHERE instr(review_log_json, '"finalized_at_utc"')>0""",
    """CREATE INDEX IF NOT EXISTS reviews_dispatch_finalized ON review_event(
        julianday(COALESCE(json_extract(review_log_json,'$.finalized_at_utc'),occurred_at_utc)))""",
    """CREATE INDEX IF NOT EXISTS reviews_card_first_finalized ON review_event(card_id,
        julianday(COALESCE(json_extract(review_log_json,'$.finalized_at_utc'),occurred_at_utc)))""",
    "CREATE INDEX IF NOT EXISTS spelling_time ON word_spelling_attempt(occurred_at_utc)",
)


def create_base(db):
    """App-owned current DDL for new/staging databases; never commit the caller."""
    for sql in BASE_DDL:
        db.execute(sql)
    db.execute("INSERT OR IGNORE INTO learner_record_meta VALUES ('schema_version',?)", (str(VERSION),))


def _columns(db, table):
    return tuple(row[1] for row in db.execute("PRAGMA table_info(" + table + ")"))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("旧题目快照含重复字段，未转换学习记录。")
        result[key] = value
    return result


def iter_legacy_questions(text):
    """Split an inert JSON array while retaining each object's exact bytes."""
    if not isinstance(text, str):
        raise ValueError("旧题目快照无效。")
    from .session_questions import MAX_SESSION_BYTES
    if len(text.encode("utf8")) > MAX_SESSION_BYTES:
        raise ValueError("旧题目快照超过转换上限。")
    decoder = json.JSONDecoder(object_pairs_hook=_unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("旧快照含非有限数值。")))
    length, index = len(text), 0
    while index < length and text[index].isspace():
        index += 1
    if index == length or text[index] != "[":
        raise ValueError("旧题目快照必须是数组。")
    index += 1
    while index < length and text[index].isspace():
        index += 1
    if index < length and text[index] == "]":
        index += 1
    else:
        while True:
            start = index
            value, index = decoder.raw_decode(text, index)
            if not isinstance(value, dict):
                raise ValueError("旧题目快照必须包含对象。")
            yield text[start:index]
            while index < length and text[index].isspace():
                index += 1
            if index == length:
                raise ValueError("旧题目快照被截断。")
            marker = text[index]
            index += 1
            if marker == "]":
                break
            if marker != ",":
                raise ValueError("旧题目快照无效。")
            while index < length and text[index].isspace():
                index += 1
    if text[index:].strip():
        raise ValueError("旧题目快照含额外数据。")


def _recovery_copy(db, directory):
    filename = next((row[2] for row in db.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not filename:
        return None
    directory = Path(directory or Path(filename).parent).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    path = directory / ("learning-before-format-" + token + ".sqlite3")
    temporary = directory / (".record-recovery-" + token + ".tmp")
    try:
        # BEGIN IMMEDIATE on the live connection freezes writers. A second
        # read connection sees the latest committed WAL snapshot and can back
        # it up without invoking backup() on our write-transaction connection.
        source, target = sqlite3.connect(str(filename)), None
        try:
            target = sqlite3.connect(str(temporary))
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("无法验证格式转换前的恢复副本。")
        finally:
            source.close()
            if target is not None:
                target.close()
        # Windows requires a writable descriptor for fsync. SQLite has already
        # closed this newly created file; no existing learner file is opened here.
        with temporary.open("r+b") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return str(path)


def _validate_ordinals(db):
    if db.execute("""SELECT 1 FROM session_question GROUP BY session_id
        HAVING MIN(question_index)!=0 OR MAX(question_index)+1!=COUNT(*) LIMIT 1""").fetchone():
        raise ValueError("题目顺序不连续，未转换学习记录。")
    for table in ("session_answer", "word_spelling_attempt"):
        if _columns(db, table) and db.execute("SELECT 1 FROM " + table + " a LEFT JOIN session_question q "
                "ON q.session_id=a.session_id AND q.question_index=a.question_index "
                "WHERE q.session_id IS NULL LIMIT 1").fetchone():
            raise ValueError("作答记录没有对应题目，未转换学习记录。")
    if _columns(db, "session_checkpoint") and db.execute("""SELECT 1 FROM session_checkpoint c
        WHERE c.question_index<0 OR c.question_index>(SELECT COUNT(*) FROM session_question q
        WHERE q.session_id=c.session_id) LIMIT 1""").fetchone():
        raise ValueError("练习进度超出题单，未转换学习记录。")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("学习记录引用不完整，未转换。")


def ensure_current(db, data_dir=None):
    """Initialize or upgrade once. Failure leaves the original schema intact."""
    if db.in_transaction:
        raise RuntimeError("Record initialization requires its own transaction")
    meta = _columns(db, "learner_record_meta")
    columns = _columns(db, "study_session")
    if meta:
        row = db.execute("SELECT value FROM learner_record_meta WHERE key='schema_version'").fetchone()
        if row is None or row[0] != str(VERSION):
            raise ValueError("不支持此学习记录版本，请使用对应版本的应用。")
        if columns != SESSION_COLUMNS or not _columns(db, "session_question"):
            raise ValueError("学习记录结构与版本标记不一致。")
        return {"converted": False, "schema_version": VERSION}
    if columns and columns != (*SESSION_COLUMNS, "question_snapshot_json"):
        raise ValueError("未知的学习记录结构，未进行转换。")
    original_fk = db.execute("PRAGMA foreign_keys").fetchone()[0]
    # Rebuilding a parent table with the same final name avoids requiring the
    # newer SQLite DROP COLUMN feature and preserves all existing FK targets.
    db.execute("PRAGMA foreign_keys=OFF")
    recovery, count = None, 0
    try:
        db.execute("BEGIN IMMEDIATE")
        if columns:
            if _columns(db, "session_question"):
                raise ValueError("旧数据库包含不完整转换结果，已停止。")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("原学习数据库校验失败，未转换。")
            recovery = _recovery_copy(db, data_dir)
            db.execute(BASE_DDL[3])
            for sid, raw in db.execute("SELECT session_id,question_snapshot_json FROM study_session ORDER BY rowid"):
                for index, question in enumerate(iter_legacy_questions(raw)):
                    db.execute("INSERT INTO session_question VALUES (?,?,?)", (sid, index, question))
                    count += 1
            _validate_ordinals(db)
            db.execute("CREATE TABLE _record_session_current " + SESSION_DEFINITION)
            fields = ",".join(SESSION_COLUMNS)
            db.execute("INSERT INTO _record_session_current(rowid," + fields + ") SELECT rowid," + fields + " FROM study_session ORDER BY rowid")
            db.execute("DROP TABLE study_session")
            db.execute("ALTER TABLE _record_session_current RENAME TO study_session")
        create_base(db)
        _validate_ordinals(db)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute("PRAGMA foreign_keys=" + str(original_fk))
    return {"converted": bool(columns), "schema_version": VERSION,
            "session_questions": count, "recovery_path": recovery}
