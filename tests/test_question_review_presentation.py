"""Exact instructional cues do not migrate memory, drafts or prior feedback."""
from copy import deepcopy
import json

import pytest

from kanamemory import material_review
from kanamemory.learning_questions import build_learning_question, grade_learning_answer
from kanamemory.learning_store import LearningStore
from kanamemory.question_support import prompt_audio_text, prompt_support
from support.learning_store_fixtures import stores, START, ready


KIND = "pronunciation_application"
OLD_TASK = "Choose the spelling of the synthetic practice token."
NEW_TASK = "Use the provided romanization kakka to choose its spelling."


def synthetic_item():
    return {
        "id": "pronunciation:test-review-presentation", "revision": "test-v1",
        "family": "grammar", "domain": "pronunciation", "title": "Synthetic rule",
        "meaning": "Test material only", "reading": "", "abilities": ["application"],
        "prerequisites": [], "instruction": {"rule": "A test-only spelling rule.", "example": "カッカ"},
        "question_templates": {KIND: [{
            "prompt": "Synthetic token", "task": OLD_TASK,
            "answer": "カッカ", "choices": ["カッカ", "カカ", "カツカ"],
            "feedback": "A synthetic spelling result.", "safe": False,
            "text_roles": {"prompt": "ui", "answer": "study"},
        }]},
    }


@pytest.fixture
def correction(monkeypatch):
    data = {
        "revision": "test-review-v1",
        "questions": [{
            "match": {"target_id": synthetic_item()["id"], "kind": KIND,
                      "revision": "test-v1", "domain": "pronunciation",
                      "prompt": "Synthetic token", "task": OLD_TASK, "answer": "カッカ"},
            "replacements": {"task": [{"before": OLD_TASK, "after": NEW_TASK}]},
        }],
    }
    monkeypatch.setattr(material_review, "review_data", lambda: data)
    return data


@pytest.mark.parametrize("guided", [False, True])
def test_new_questions_and_guides_receive_exact_task_cue_without_audio_or_ruby(correction, guided):
    item = synthetic_item()
    original = deepcopy(item)
    raw = build_learning_question(item, KIND, guided=guided, apply_review=False)
    current = build_learning_question(item, KIND, guided=guided)
    assert current["task"] == NEW_TASK
    assert current["material_review"] == correction["revision"]
    assert {k: v for k, v in current.items() if k not in {"task", "material_review"}} == {
        k: v for k, v in raw.items() if k != "task"}
    assert item == original
    assert prompt_audio_text(current) is None
    assert not current["audio_text"] and not current["optional_audio_text"]
    assert not current["support"]["segments"]
    support = prompt_support(current)
    assert not support["furigana_visible"] and not support["accent_visible"]


@pytest.mark.parametrize("field,value", [
    ("prompt", "Another token"), ("task", "User-edited instruction"),
    ("answer", "カカ"), ("target_id", "pronunciation:other"), ("revision", "test-v2"),
])
def test_nonmatching_questions_do_not_get_an_answer_derived_hint(correction, field, value):
    raw = build_learning_question(synthetic_item(), KIND, apply_review=False)
    raw[field] = value
    assert material_review.reviewed_question(raw) == raw


def test_existing_queue_gets_cue_without_rewriting_draft_due_or_snapshot(stores, correction):
    store, learning = stores
    item = synthetic_item()
    learning.enroll(item, now=START)
    when = ready(learning, item["id"])
    raw = build_learning_question(item, KIND, apply_review=False)
    learning.append_questions(raw, now=START)
    learning.save_draft(raw["id"], {"answer": "カカ", "unsubmitted": True})
    before = tuple(store._db.iterdump())
    view = learning.get_question(raw["id"])
    assert view["task"] == NEW_TASK and view["draft"]["answer"] == "カカ"
    assert view["answer"] == raw["answer"] and view["choices"] == raw["choices"]
    assert tuple(store._db.iterdump()) == before
    learning.submit(raw["id"], raw["answer"], True, now=when)
    learning.finalize(raw["id"], 3, now=when)
    frozen = learning.get_question(raw["id"])
    assert frozen["task"] == NEW_TASK
    assert frozen["result"]["material_view"]["task"] == NEW_TASK
    saved_snapshot = json.loads(store._db.execute(
        "SELECT snapshot_json FROM learning_question WHERE question_id=?", (raw["id"],)).fetchone()[0])
    assert saved_snapshot["task"] == OLD_TASK
    correction["questions"][0]["replacements"]["task"][0]["after"] = "A later revision"
    due = learning.get_unit(item["id"], "application")
    assert LearningStore(store).get_question(raw["id"]) == frozen
    assert learning.get_unit(item["id"], "application") == due


@pytest.mark.parametrize("saved_question", [False, True])
def test_answered_guide_keeps_original_task_and_feedback(stores, correction, saved_question):
    store, learning = stores
    item = synthetic_item()
    old_guide = build_learning_question(item, KIND, guided=True, variant=1, apply_review=False)
    learning.enroll(item, now=START)
    learning.advance_instruction(item["id"], "intro_done", now=START)
    state = {"guide_answer": "カカ", "guide_correct": False}
    if saved_question:
        state["guide_question"] = old_guide
    learning.advance_instruction(item["id"], "guide_failed", now=START, state=state)
    before = tuple(store._db.iterdump())
    restored = LearningStore(store).guided_question(learning.get_item(item["id"]), KIND)
    assert restored == old_guide
    assert restored["task"] == OLD_TASK
    assert "material_review" not in restored
    assert tuple(store._db.iterdump()) == before


def test_unanswered_guide_uses_reviewed_task_without_saving_progress(stores, correction):
    store, learning = stores
    item = synthetic_item()
    learning.enroll(item, now=START)
    learning.advance_instruction(item["id"], "intro_done", now=START)
    before = tuple(store._db.iterdump())
    guide = learning.guided_question(learning.get_item(item["id"]), KIND)
    assert guide["task"] == NEW_TASK and guide["guided"] is True
    assert tuple(store._db.iterdump()) == before


BLOCKED_REASON = "This synthetic legacy question assumes untaught vocabulary; do not reschedule."


@pytest.mark.parametrize("answer", ["カッカ", "カカ", "カツカ"])
def test_exact_retired_question_neither_correct_nor_wrong_option_changes_schedule(stores, correction, answer):
    store, learning = stores
    entry = correction["questions"][0]
    entry["exact_values"] = {"assessment_blocked_reason": {"before": None, "after": BLOCKED_REASON}}
    item = synthetic_item()
    learning.enroll(item, now=START)
    when = ready(learning, item["id"])
    raw = build_learning_question(item, KIND, apply_review=False)
    learning.append_questions(raw, now=START)
    before = learning.get_unit(item["id"], "application")
    displayed = learning.get_question(raw["id"])
    assert displayed["task"] == NEW_TASK
    assert material_review.reviewed_question(displayed) == displayed
    result = grade_learning_answer(displayed, answer)
    assert result == {"status": "unresolved", "correct": None, "scorable": False,
                      "message": BLOCKED_REASON}
    learning.submit(raw["id"], answer, result["correct"], outcome=result["status"], now=when)
    learning.finalize(raw["id"], now=when)
    after = learning.get_unit(item["id"], "application")
    assert after["scheduler_state"] == before["scheduler_state"]
    assert after["due_at_utc"] == before["due_at_utc"]
    frozen = learning.get_question(raw["id"])
    assert frozen["result"]["material_view"]["assessment_blocked_reason"] == BLOCKED_REASON
    correction["questions"] = []
    assert LearningStore(store).get_question(raw["id"]) == frozen
    assert grade_learning_answer(frozen, answer)["scorable"] is False


def test_untrusted_assessment_exemption_is_ignored_and_completed_question_is_not_repaired(correction):
    question = build_learning_question(synthetic_item(), KIND, apply_review=False)
    question.update(prompt="Not an exact authored template", assessment_blocked_reason=BLOCKED_REASON)
    assert grade_learning_answer(question, question["answer"])["correct"] is True
    assert grade_learning_answer(question, "カカ")["correct"] is False
    assert grade_learning_answer(question, "")["status"] == "empty"
    completed = build_learning_question(synthetic_item(), KIND, apply_review=False)
    completed.update(status="completed", result={"answered_text": "カカ", "correct": False})
    correction["questions"][0]["exact_values"] = {
        "assessment_blocked_reason": {"before": None, "after": BLOCKED_REASON}}
    assert material_review.reviewed_question(completed) == completed


def test_new_question_exemption_is_idempotent_but_unknown_task_cannot_claim_it(correction):
    correction["questions"][0]["exact_values"] = {
        "assessment_blocked_reason": {"before": None, "after": BLOCKED_REASON}}
    question = build_learning_question(synthetic_item(), KIND)
    assert question["task"] == NEW_TASK
    assert material_review.reviewed_question(question) == question
    assert grade_learning_answer(question, question["answer"])["scorable"] is False
    question["task"] = "A different, unreviewed instruction"
    assert grade_learning_answer(question, question["answer"])["correct"] is True
