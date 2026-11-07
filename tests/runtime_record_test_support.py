"""Current schema fixtures; aggregate strings exist only for test assertions."""
import json

from kanamemory import session_questions


def snapshot_json(db, session_id):
    return "[" + ",".join(row[0] for row in db.execute(
        "SELECT snapshot_json FROM session_question WHERE session_id=? ORDER BY question_index",
        (session_id,))) + "]"


def write_snapshot(db, session_id, encoded):
    if not db.in_transaction:
        db.execute("BEGIN")
    session_questions.replace(db, session_id, json.loads(encoded))
