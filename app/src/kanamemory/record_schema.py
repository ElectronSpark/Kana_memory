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
from threading import Lock
from uuid import uuid4

VERSION = 1


class LearnerConnection(sqlite3.Connection):
    """A progress.sqlite3 connection that remembers its own verified schema facts.

    ``schema_facts`` belongs to this one connection (and so to the one thread
    that owns it). It holds only positive facts ("table X exists") learned
    outside a transaction. App code never drops the tables it records; the one
    rebuild (ensure_current's conversion) clears the facts. Negative probes are
    never cached, so a later migration is always noticed.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.schema_facts = {}


# Process-wide record of schema checks that already passed, keyed by component
# and database file identity; the value is PRAGMA schema_version when the check
# passed. Any DDL (a migration, an added table or index) bumps schema_version,
# so the next connection re-verifies instead of trusting the old result.
_VERIFIED: dict[tuple, int] = {}
_VERIFIED_LOCK = Lock()
_MAX_VERIFIED = 64


def _file_identity(db):
    facts = getattr(db, "schema_facts", None)
    if facts is not None and "file" in facts:
        return facts["file"]
    filename = next((row[2] for row in db.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not filename:
        return None  # :memory: or a temporary database: never cached.
    try:
        path = os.path.normcase(os.path.realpath(filename))
        status = os.stat(path)
    except OSError:
        return None
    identity = (path, status.st_dev, status.st_ino)
    if facts is not None:
        facts["file"] = identity
    return identity


def schema_key(db):
    """(file identity, schema_version) for the process cache, or None if uncacheable."""
    identity = _file_identity(db)
    if identity is None:
        return None
    return identity, db.execute("PRAGMA main.schema_version").fetchone()[0]


def schema_verified(component, key) -> bool:
    if key is None:
        return False
    with _VERIFIED_LOCK:
        return _VERIFIED.get((component, key[0])) == key[1]


def mark_schema_verified(component, db) -> None:
    """Record a passed check at the database's current schema_version."""
    key = schema_key(db)
    if key is None or db.in_transaction:
        return
    with _VERIFIED_LOCK:
        _VERIFIED.pop((component, key[0]), None)
        _VERIFIED[(component, key[0])] = key[1]
        while len(_VERIFIED) > _MAX_VERIFIED:
            _VERIFIED.pop(next(iter(_VERIFIED)))


def forget_schema(db=None) -> None:
    """Drop cached checks for one database (or all); also its connection facts."""
    facts = getattr(db, "schema_facts", None)
    if facts is not None:
        identity = facts.get("file")
        facts.clear()
        if identity is not None:
            facts["file"] = identity
    identity = None if db is None else _file_identity(db)
    with _VERIFIED_LOCK:
        if db is None:
            _VERIFIED.clear()
        else:
            for key in [key for key in _VERIFIED if key[1] == identity]:
                del _VERIFIED[key]


def object_present(db, name) -> bool:
    """sqlite_master probe for an app-owned table/index, cached per connection.

    Only a positive answer read outside a transaction is remembered (a rolled
    back CREATE must not leave a stale fact). Plain connections always query.
    """
    facts = getattr(db, "schema_facts", None)
    if facts is not None and facts.get(("object", name)):
        return True
    present = db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None
    if present and facts is not None and not db.in_transaction:
        facts[("object", name)] = True
    return present
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
    # A file already verified at this exact schema_version (by any connection in
    # this process) skips the three table_info probes; a migration bumps the
    # version and forces a full re-check.
    if schema_verified("record", schema_key(db)):
        return {"converted": False, "schema_version": VERSION}
    meta = _columns(db, "learner_record_meta")
    columns = _columns(db, "study_session")
    if meta:
        row = db.execute("SELECT value FROM learner_record_meta WHERE key='schema_version'").fetchone()
        if row is None or row[0] != str(VERSION):
            raise ValueError("不支持此学习记录版本，请使用对应版本的应用。")
        if columns != SESSION_COLUMNS or not _columns(db, "session_question"):
            raise ValueError("学习记录结构与版本标记不一致。")
        mark_schema_verified("record", db)
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
        # The conversion rebuilt study_session (or created the base tables):
        # forget every cached fact about this file before trusting it again.
        forget_schema(db)
    mark_schema_verified("record", db)
    return {"converted": bool(columns), "schema_version": VERSION,
            "session_questions": count, "recovery_path": recovery}
