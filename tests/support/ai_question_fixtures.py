"""Shared ai question fixtures; no test scenarios or autouse registration."""
from datetime import datetime, timedelta, timezone
import json
import pytest
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.learning_content import get_learning_item
from kanamemory.learning_questions import build_learning_question
from kanamemory.ai_questions import AI_CHOICE_KINDS, build_learning_scope


START = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


CID = "grammar:copula:polite"


@pytest.fixture
def learning(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        yield LearningStore(store)


def teach(learning, cid=CID):
    item = get_learning_item(cid)
    learning.enroll(item, now=START)
    learning.advance_instruction(cid, "intro_done", now=START)
    learning.advance_instruction(cid, "guide_correct", now=START)
    learning.advance_instruction(cid, "check", now=START + timedelta(minutes=2))
    return item


def score(learning, cid=CID, kind="grammar_meaning", *, correct=True, assisted=False, revealed=False):
    item = learning.get_item(cid)
    q = build_learning_question(item, kind)
    unit = learning.get_unit(cid, q["ability"])
    # Another ability may still be cooling down after the prior answer even
    # when its own card has no FSRS due date yet.
    due = learning.effective_question_due(unit, q)
    when = datetime.fromisoformat(due) if due else START + timedelta(minutes=2)
    q["id"] = "seed:" + cid + ":" + kind + ":" + str(unit["review_count"])
    learning.append_questions(q, now=when)
    learning.submit(q["id"], q["answer"], correct if not revealed else None,
                    assisted=assisted, outcome="revealed" if revealed else None, now=when)
    return learning.finalize(q["id"], 3 if correct and not revealed else 1, now=when)


def scope(learning, kinds=AI_CHOICE_KINDS):
    return build_learning_scope(learning, kinds, now=START + timedelta(days=10))


def learn_question_background(learning, question):
    """Independent prior answers, not merely acknowledging a prerequisite list."""
    from kanamemory.learning_questions import question_kinds
    for cid in question.get("practice", {}).get("background_ids", []):
        if cid == question["target_id"]:
            continue
        item = get_learning_item(cid)
        assert item is not None
        learning.enroll(item, now=START)
        if learning.prerequisite_status(cid)["blocked"]:
            learning.acknowledge_prerequisites(cid, now=START)
        teach(learning, cid)
        for kind in dict.fromkeys(question_kinds(item)):
            score(learning, cid, kind)


def scheduling_rows(store):
    """Generated answers may add unqualified observations, never scheduling.

    Normalize exactly those expected rows and their one revision increment;
    every formal event, aggregate, adjustment, existing observation and queue
    stays in the comparison.
    """
    rows = database_rows(store)
    raw = list(store._db.execute("SELECT * FROM learning_evidence WHERE source='ai'"))
    for row in raw:
        assert row['eligible'] == 0 and row['role'] == 'exposure' and row['retracted'] == 0
        assert json.loads(row['snapshot_json'])['opportunity']['channel'] == 'observation'
    rows['learning_evidence'] = [row for row in rows['learning_evidence'] if row[5] != 'ai']
    rows['evidence_meta'] = [(key, str(int(value)-len(raw)) if key == 'revision' else value)
                             for key, value in rows['evidence_meta']]
    return rows


def proposal(learning, kind="grammar_meaning", **changes):
    result = {"target_id": CID, "revision": learning.get_item(CID)["revision"], "kind": kind,
        "prompt": "あれは学校です。", "choices": ["礼貌地说明身份或类别", "询问地点", "否定存在"],
        "answer": "礼貌地说明身份或类别", "explanation": "名词后接です，作出礼貌的肯定判断。"}
    if kind == "grammar_cloze":
        result.update(prompt="あれは学校（　）。", choices=["です", "ます", "いる"], answer="です")
    result.update(changes)
    return result


def database_rows(store):
    return {name: [tuple(row) for row in store._db.execute('SELECT * FROM "' + name + '" ORDER BY rowid')]
        for name, in store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
