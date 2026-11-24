"""Results stored by the judge before reading forms (2026-11-18) stay valid: a stored verdict is never re-judged.

A guide answer 学校へいきます stored as not correct is correct under the widened judge; the stored checkpoint
must still load, advance and replay (design 03: a verdict, once stored, never changes; only a verdict given
in the write being checked is compared with the judge). Synthetic lesson, temporary SQLite store; the
readings of the answer are given directly (no course content).
"""
from datetime import datetime, timezone
import json

import pytest

from kanamemory import learning_questions as lq
from kanamemory import result_readings
from kanamemory.domain import StudyStore
from kanamemory.learning_practice_policy import validate_practice_guide
from kanamemory.learning_store import LearningStore

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
ANSWER, KANA, MIXED = "学校へ行きます", "がっこうへいきます", "学校へいきます"
SPANS = {ANSWER: [(0, 2, "がっこう"), (3, 4, "い")]}


def lesson():
    template = {
        "prompt": "学校へ行く。（礼貌体）", "task": "改成礼貌体，写出整句。", "answer": ANSWER,
        "accepted_answers": [ANSWER, KANA], "input_mode": "text", "choices": [], "strict": False,
        "feedback": "合成测试说明。",
        "practice": {"schema_version": 1, "task_family": "form_transformation", "context_id": "synthetic-a",
                     "scaffold": "retrieval", "target_spans": [{"start": 0, "end": 3, "quote": "学校へ"}], "background_ids": [], "reviewed": True},
    }
    return {"id": "grammar:synthetic-reading-form", "revision": "synthetic-reading-form-v1",
            "family": "grammar", "title": "ます形", "abilities": ["application"], "depends_on": [],
            "instruction": {"rule": "合成语法说明。", "example": ANSWER},
            "question_templates": {"grammar_transform": [template]}}


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(result_readings, "annotation_reader", lambda question: lambda text: SPANS.get(text, []))
    lq._READING_FORMS.clear()
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        learning = LearningStore(store)
        item = lesson()
        learning.enroll(item, now=NOW)
        learning.advance_instruction(item["id"], "intro_done", now=NOW)
        yield learning, item
    lq._READING_FORMS.clear()


def guide(item):
    return lq.build_learning_question(item, "grammar_transform", guided=True)


def test_the_two_judgements_differ_for_this_answer(stores):
    _, item = stores
    question = guide(item)
    assert lq.grade_learning_answer(question, MIXED)["status"] == "correct"
    assert lq.grade_learning_answer(question, MIXED, reading_forms=False)["status"] != "correct"
    assert lq.grade_learning_answer(question, KANA, reading_forms=False)["status"] == "correct"


def _store_as_recorded(learning, item, state):
    """The checkpoint as an earlier app version recorded it (written directly: no judge of today gave it)."""
    row = learning._db.execute("SELECT checkpoint_json FROM learning_content WHERE content_id=?",
                               (item["id"],)).fetchone()
    checkpoint = {**json.loads(row[0]), **state}
    with learning._db:
        learning._db.execute("UPDATE learning_content SET checkpoint_json=? WHERE content_id=?",
                             (json.dumps(checkpoint, ensure_ascii=False), item["id"]))


@pytest.mark.parametrize("stored, valid", [
    (False, True),   # an old record: judged by the earlier judge
    (True, True),    # a new record: judged by the widened judge
])
def test_a_stored_guide_result_loads_advances_and_replays(stores, stored, valid):
    learning, item = stores
    question = guide(item)
    state = {"guide_question": question, "guide_answer": MIXED, "guide_correct": stored,
             "guide_draft": {"answer": MIXED}}
    if stored:
        # learning_store.advance_instruction validates the checkpoint it stores (validate_practice_guide)
        learning.advance_instruction(item["id"], "guide", now=NOW, state=state)
    else:
        _store_as_recorded(learning, item, state)
    saved = learning.get_item(item["id"])
    assert saved["checkpoint"]["guide_correct"] is stored  # stored as it was
    # guided_question re-reads the frozen guide and validates its stored result
    assert learning.guided_question(saved, "grammar_transform") == question
    # the event replay (learning_replacement) and every other caller go through the same check
    validate_practice_guide(item["id"], item["revision"], saved["checkpoint"], item=item)
    learning.advance_instruction(item["id"], "guide", now=NOW, state=state)  # stored again unchanged


def test_a_new_verdict_must_agree_with_the_judge_and_a_stored_one_is_kept(stores):
    learning, item = stores
    question = guide(item)
    for answer, given in ((ANSWER, False), ("学校にいきます", True), (MIXED, False)):
        checkpoint = {"guide_question": question, "guide_answer": answer, "guide_correct": given}
        with pytest.raises(ValueError, match="disagrees"):
            validate_practice_guide(item["id"], item["revision"], checkpoint, item=item, judged_now=True)
        validate_practice_guide(item["id"], item["revision"], checkpoint, item=item)  # stored: kept as it is
    with pytest.raises(ValueError, match="disagrees"):  # the store gives a new verdict only through the judge
        learning.advance_instruction(item["id"], "guide", now=NOW, state={
            "guide_question": question, "guide_answer": ANSWER, "guide_correct": False,
            "guide_draft": {"answer": ANSWER}})
    with pytest.raises(ValueError, match="disagrees"):  # a right verdict without an answer was never given
        validate_practice_guide(item["id"], item["revision"], {
            "guide_question": question, "guide_answer": " ", "guide_correct": True}, item=item)
