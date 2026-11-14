"""Persistence for the batch introduction cursor and its size preference.

Writes only inside the caller's transaction; LearningStore owns every
transaction and the per-item checkpoints. No unit, question or event here.
"""
from __future__ import annotations

import json

from . import intro_batch as rules


def _setting(db, key):
    row = db.execute("SELECT value_json FROM app_setting WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def _put(db, key, value):
    if not db.in_transaction:
        raise RuntimeError("Batch introduction writes require the learner action transaction")
    db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
               (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))


def read(db) -> dict | None:
    raw = _setting(db, rules.STATE_SETTING)
    return None if raw is None else rules.validate_state(raw)


def write(db, state) -> None:
    _put(db, rules.STATE_SETTING, rules.validate_state(state))


def clear(db) -> None:
    if not db.in_transaction:
        raise RuntimeError("Batch introduction writes require the learner action transaction")
    db.execute("DELETE FROM app_setting WHERE key=?", (rules.STATE_SETTING,))


def read_sizes(db) -> dict:
    return rules.read_sizes(_setting(db, rules.SIZE_SETTING))


def write_sizes(db, sizes) -> None:
    _put(db, rules.SIZE_SETTING, rules.validate_sizes(sizes))


def validate_database(db) -> None:
    """Cross-check the active batch against the saved introductions."""
    read_sizes(db)
    state = read(db)
    if state is None:
        return
    checks = set(state["checks"])
    frozen = {}
    for entry in state["entries"]:
        cid = entry["content_id"]
        row = db.execute("SELECT family,stage,introduction_id,checkpoint_json FROM learning_content WHERE content_id=?",
                         (cid,)).fetchone()
        if row is None or row[0] != state["family"] or row[2] != entry["introduction_id"]:
            raise ValueError("Batch introduction lost its saved item")
        checkpoint = json.loads(row[3])
        if not isinstance(checkpoint, dict) or checkpoint.get(rules.ROUTE_KEY) != rules.ROUTE:
            raise ValueError("Batch introduction item lacks its route marker")
        allowed = {"intro", "bridge", "check"} if cid in checks else {"intro", "guide"}
        if row[1] not in allowed:
            raise ValueError("Batch introduction item has an unexpected stage")
        item = checkpoint.get(rules.ITEM_KEY)
        if item is not None:
            item = rules.validate_item_state(item, content_id=cid)
            if item["batch_id"] != state["batch_id"]:
                raise ValueError("Batch quiz checkpoint belongs to another batch")
            frozen[cid] = item
        if state["phase"] == "quiz" and cid not in checks and (item is None or row[1] != "guide"):
            raise ValueError("Batch quiz item lacks its frozen questions")
    queued = {head["content_id"]: head["question_id"] for head in state["quiz_queue"]}
    for head in state["quiz_queue"]:
        if head["question_id"] not in {q["id"] for q in frozen.get(head["content_id"], {}).get("questions", ())}:
            raise ValueError("Batch quiz queue refers to an unfrozen question")
    if state["phase"] == "quiz":
        for cid in rules.quiz_items(state):
            item = frozen[cid]
            # Unfinished items wait with the question the rules chose; finished ones leave,
            # and so does an item released after missing each of its questions (design 12 Q2).
            if (cid in queued and (item["correct"] or queued[cid] != rules.queued_question(item))
                    or cid not in queued and not item["correct"] and not rules.released(item)):
                raise ValueError("Batch quiz queue disagrees with its answers")
