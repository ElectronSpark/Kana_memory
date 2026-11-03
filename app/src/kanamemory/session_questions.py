"""Current per-question session storage; no aggregate legacy column or dual writes."""
from __future__ import annotations

import json
from .session_snapshot_cache import FrozenRow

MAX_SESSION_BYTES = 64 * 1024 * 1024


def _encoded(value):
    return _encoding(value)[0]


def _encoding(value):
    if not isinstance(value, dict):
        raise ValueError("题目快照必须是对象。")
    if isinstance(value, FrozenRow):
        return value.encoding()
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return text, len(text.encode('utf8'))


def iter_rows(db, session_id, start=0):
    """Yield frozen payloads in stable ordinal order without rebuilding an array."""
    if type(start) is not int or start < 0:
        raise ValueError("Invalid question position")
    for row in db.execute("SELECT snapshot_json FROM session_question WHERE session_id=? AND question_index>=? ORDER BY question_index",
                          (session_id, start)):
        yield json.loads(row[0])


def load(db, session_id):
    if db.execute("SELECT 1 FROM study_session WHERE session_id=?", (session_id,)).fetchone() is None:
        raise ValueError("Unknown session_id")
    return list(iter_rows(db, session_id))


def replace(db, session_id, questions, *, reader=None):
    """Update only changed ordinals, inside the caller's atomic business write.

    Ordinals are stable references for committed answers. Callers already limit
    reordering to the unfinished suffix. Unchanged frozen payload strings keep
    their original bytes, including ones preserved by the one-time converter.
    """
    if not db.in_transaction:
        raise RuntimeError("Session questions require the caller's transaction")
    if db.execute("SELECT 1 FROM study_session WHERE session_id=?", (session_id,)).fetchone() is None:
        raise ValueError("Unknown session_id")
    encoded, total = [], 2
    for question in questions:
        text, size = _encoding(question)
        total += size + 1
        if total > MAX_SESSION_BYTES:
            raise ValueError("练习队列数据过大，请完成当前队列后再添加。")
        encoded.append((question, text))
    current = (reader.read(session_id)[0] if reader is not None else
               dict(db.execute("SELECT question_index,snapshot_json FROM session_question WHERE session_id=?", (session_id,))))
    changed = []
    for index, (question, text) in enumerate(encoded):
        old = current.get(index)
        # JSON distinguishes bool/number even though Python equality does not.
        if reader is not None:
            if index in current and _encoded(old) == text:
                continue
        elif old == text or old is not None and _encoded(json.loads(old)) == text:
            continue
        changed.append((session_id, index, text))
    db.executemany("""INSERT INTO session_question VALUES (?,?,?)
        ON CONFLICT(session_id,question_index) DO UPDATE SET snapshot_json=excluded.snapshot_json""", changed)
    db.execute("DELETE FROM session_question WHERE session_id=? AND question_index>=?", (session_id, len(encoded)))


def append(db, session_id, questions):
    """Append without reading or rewriting any historical question payload."""
    return append_encoded(db, session_id, (_encoded(question) for question in questions))


def append_encoded(db, session_id, rows):
    """Commit already serialized worker rows without serializing on the UI thread."""
    if not db.in_transaction:
        raise RuntimeError("Session questions require the caller's transaction")
    if db.execute("SELECT 1 FROM study_session WHERE session_id=?", (session_id,)).fetchone() is None:
        raise ValueError("Unknown session_id")
    count, last, size = db.execute("""SELECT COUNT(*),MAX(question_index),
        COALESCE(SUM(length(CAST(snapshot_json AS BLOB))+1),0)
        FROM session_question WHERE session_id=?""", (session_id,)).fetchone()
    if count and last != count - 1:
        raise ValueError("题目顺序不连续。")
    values, total = [], size + 2
    for offset, text in enumerate(rows):
        if not isinstance(text, str) or not isinstance(json.loads(text), dict):
            raise ValueError("题目快照必须是对象。")
        total += len(text.encode("utf8")) + 1
        if total > MAX_SESSION_BYTES:
            raise ValueError("练习队列数据过大，请完成当前队列后再添加。")
        values.append((session_id, count + offset, text))
    db.executemany("INSERT INTO session_question VALUES (?,?,?)", values)
