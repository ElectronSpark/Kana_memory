"""A verdict, once stored, never changes when the answer overlay or the judge changes (design 03, rule T3).

Made-up lesson and learner in a temporary SQLite store. The overlay row of the question's own template is
the test's (answer_policy.source._OVERLAY), and the template is located by a fixed sha, so no course
content is read. Every app start ends all pending course questions (practice_archive): a restart between
showing and answering and one between answering and rating are both covered.
"""
from copy import deepcopy
import json

import pytest

from kanamemory import content_caches
from kanamemory.answer_policy import source
from kanamemory.learning_questions import grade_learning_answer
from kanamemory.learning_store import LearningStore, validate_backup_rows
from kanamemory.practice_archive import archive_previous_rounds, archived_attempts, archived_question
from support.learning_store_fixtures import START, item, question, ready, stores  # noqa: F401 (a fixture)

CID = "grammar:te_iru:progressive"
SHA = "e" * 64
TYPED = dict(ability="application", kind="grammar_transform", prompt="読む → ＿＿（正在读）", answer="よんでいる",
             accepted_answers=["よんでいる"], input_mode="text", strict=False, feedback="て形＋いる。")


@pytest.fixture
def overlay(monkeypatch):
    rows = {}
    monkeypatch.setattr(source, "_OVERLAY", {"schema_version": 1, "items": {CID: {"grammar_transform": rows}}})
    monkeypatch.setattr(source, "own_template_sha", lambda q: SHA if q.get("target_id") == CID else None)
    yield rows
    content_caches.clear_all()


def page_submit(learning, qid, answer, when):
    """What a page does: judge the snapshot now, then submit the verdict."""
    judged = grade_learning_answer(snapshot_of(learning, qid), answer)
    correct = None if judged["status"] == "unresolved" else judged["status"] == "correct"
    return learning.submit(qid, answer, correct, outcome=judged["status"], now=when)


def snapshot_of(learning, qid):
    return json.loads(learning._db.execute("SELECT snapshot_json FROM learning_question WHERE question_id=?",
                                           (qid,)).fetchone()[0])


def test_restart_between_showing_and_answering_keeps_the_question_and_judges_with_the_policy_then(stores, overlay):
    store, learning = stores
    learning.enroll(item(), now=START)
    when = ready(learning)
    learning.append_questions(question(qid="q1", **TYPED), now=when)
    learning.save_draft("q1", {"presented": True})  # what the question page stores when it shows the question
    shown = snapshot_of(learning, "q1")
    overlay[SHA] = {"accepted": ["よんでます"]}  # the overlay changes while the question is open
    assert snapshot_of(learning, "q1") == shown  # nothing is copied into the stored question
    archive_previous_rounds(store, now=when)  # the next app start ends the open question
    assert json.loads(archived_question(store, "q1")["before"]["snapshot_json"]) == shown
    learning = LearningStore(store)
    learning.append_questions(question(qid="q2", **TYPED), now=when)
    again = snapshot_of(learning, "q2")
    fields = ("prompt", "answer", "accepted_answers", "strict", "known_wrong", "feedback", "input_mode")
    assert {k: again.get(k) for k in fields} == {k: shown.get(k) for k in fields}
    assert "よんでます" not in json.dumps(again, ensure_ascii=False)  # built questions never carry the overlay
    assert page_submit(learning, "q2", "よんでます", when)["outcome"] == "correct"  # the policy at submission


CHANGES = ["row_removed", "row_says_strict", "row_now_lists_it_wrong"]


def answered(stores, overlay):
    store, learning = stores
    learning.enroll(item(), now=START)
    when = ready(learning)
    learning.append_questions(question(qid="q1", **TYPED), now=when)
    overlay[SHA] = {"accepted": ["よんでます"]}
    result = page_submit(learning, "q1", "よんでます", when)
    assert result["outcome"] == "correct" and result["correct"] is True
    return store, learning, when, result


def change_overlay(overlay, change, learning):
    if change == "row_removed":
        overlay.clear()
    elif change == "row_says_strict":
        overlay[SHA] = {"strict": True}
    else:
        overlay[SHA] = {"known_wrong": [{"answer": "よんでます", "feedback": "要用ている形。"}]}
    assert grade_learning_answer(snapshot_of(learning, "q1"), "よんでます")["status"] != "correct"


@pytest.mark.parametrize("change", CHANGES)
def test_restart_between_answering_and_rating_keeps_the_stored_verdict(stores, overlay, change):
    store, learning, when, result = answered(stores, overlay)
    change_overlay(overlay, change, learning)
    archive_previous_rounds(store, now=when)  # the next app start ends the answered, unrated question
    learning = LearningStore(store)
    receipt = archived_question(store, "q1")
    assert json.loads(receipt["before"]["result_json"]) == result
    assert [entry["result"]["outcome"] for entry in archived_attempts(store)] == ["correct"]
    validate_backup_rows(store._db)  # the stored verdict is accepted as it is
    with pytest.raises(ValueError):
        learning.submit("q1", "よんでます", False, outcome="incorrect", now=when)  # no second verdict


@pytest.mark.parametrize("change", CHANGES)
def test_a_rated_verdict_survives_restart_resubmission_history_and_backup_validation(stores, overlay, change):
    store, learning, when, result = answered(stores, overlay)
    saved = learning.finalize("q1", 3, now=when)
    assert saved["scheduled"] and saved["rating"] == 3
    change_overlay(overlay, change, learning)
    archive_previous_rounds(store, now=when)
    learning = LearningStore(store)
    validate_backup_rows(store._db)
    assert learning.submit("q1", "よんでいる", False, outcome="incorrect", now=when) == result  # repeated
    assert LearningStore(store).finalize("q1", 1, now=when) == saved
    assert [entry.get("outcome", entry.get("answer", {}).get("outcome")) for entry in learning.history()] == ["correct"]


def test_a_stored_guide_verdict_is_not_judged_again_when_the_guide_continues(stores, overlay, monkeypatch):
    from kanamemory.learning_replacement import guide_write_guard
    checkpoint = {"guide_replacement_id": "r", "guide_question": deepcopy(question(qid="g", **TYPED)),
                  "guide_answer": "よんでます", "guide_correct": True}
    # The verdict was given under a row that has since been removed: the current judge disagrees.
    assert grade_learning_answer(checkpoint["guide_question"], "よんでます")["status"] != "correct"
    guide_write_guard(checkpoint, None, "guide_correct")  # the stored verdict lets the guide continue
    with pytest.raises(ValueError):
        guide_write_guard(dict(checkpoint, guide_correct=False), None, "guide_correct")
