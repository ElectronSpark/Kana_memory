"""Behavioral policy checks; no model calls or second learning state."""
from copy import deepcopy

import pytest

from kanamemory.learning_harness import (POLICY, VERSION, decide_next_step, rank_targets,
                                         selection_mode)


def target(cid, familiarity=.5):
    return {"id": cid, "familiarity": familiarity}


def observation(cid, outcome="incorrect", *, assisted=False, source="local"):
    return {"target_id": cid, "outcome": outcome, "correct": outcome == "correct",
            "source": source, "assisted": assisted}


def completed(cid, outcome="incorrect", *, coached=False, source="local"):
    return {"status": "completed", "question": {"target_id": cid}, "source": source,
            "coached": coached, "result": observation(cid, outcome, source=source)}


def candidate(cid):
    return {"target_id": cid, "candidate_id": "local:" + cid}


def test_modes_survive_a_reconstructed_state_and_have_coverage_slots():
    assert [selection_mode(i) for i in range(8)] == [
        "progress", "targeted", "progress", "independent_check"] * 2
    with pytest.raises(ValueError):
        selection_mode(-1)


def test_independent_check_does_not_depend_on_error_profile_or_familiarity():
    targets = [target("a", 0), target("b", 1), target("c")]
    errors = [observation("a") for _ in range(8)] + [observation("c")]
    recovered = [observation("a", "correct") for _ in range(8)] + [observation("c", "correct")]
    before = rank_targets(targets, errors, completed_count=3)
    after = rank_targets(list(reversed(targets)), recovered,
                         {"targets": [{"target_id": "b", "opportunities": 30, "concern": 1}]},
                         completed_count=3)
    assert [row["id"] for row in before] == [row["id"] for row in after]
    assert before[0]["id"] != "c"


def test_progress_uses_coverage_instead_of_selecting_the_repeated_weak_item():
    targets = [target("weak", 0), target("ordinary", .8), target("last")]
    recent = [observation("weak") for _ in range(6)] + [observation("last")]
    assert rank_targets(targets, recent, completed_count=2)[0]["id"] == "ordinary"
    assert rank_targets(targets, recent, completed_count=1)[0]["id"] == "weak"


def test_targeted_uses_current_evidence_not_old_raw_error_volume():
    targets = [target("many"), target("few"), target("last")]
    evidence = {"targets": [
        {"target_id": "many", "opportunities": 100, "errors": 6, "concern": .06},
        {"target_id": "few", "opportunities": 3, "errors": 2, "concern": .33}]}
    recent = [observation("many") for _ in range(6)] + [observation("last")]
    assert rank_targets(targets, recent, evidence, completed_count=1)[0]["id"] == "few"
    evidence["targets"][1].update(opportunities=10, concern=.01)
    assert rank_targets(targets, recent, evidence, completed_count=1)[0]["id"] == "many"


def test_assistance_and_unverified_model_judgments_do_not_create_error_priority():
    targets = [target("ordinary"), target("assisted"), target("model"), target("last")]
    recent = [observation("assisted", assisted=True), observation("model", source="ai"),
              observation("last", "device_failure")]
    assert rank_targets(targets, recent, completed_count=1)[0]["id"] == "ordinary"


def test_each_independent_success_reduces_fallback_error_emphasis():
    # With one failure each, the target that also succeeded has lower concern.
    targets = [target("recovering"), target("unverified"), target("last")]
    recent = [observation("recovering"), observation("unverified"),
              observation("recovering", "correct"), observation("last", "correct")]
    assert rank_targets(targets, recent, completed_count=1)[0]["id"] == "unverified"


@pytest.mark.parametrize("result,action", [(None, "resume"), ({"correct": True}, "feedback")])
def test_saved_question_or_result_wins_over_a_new_candidate(result, action):
    state = {"completed_count": 3, "entries": [{"status": "pending",
             "question": {"target_id": "saved"}, "draft": {"answer": "草稿"}, "result": result}]}
    context = {"decision": "resume", "candidates": [candidate("other")]}
    frozen = deepcopy((state, context))
    decision = decide_next_step(context, state)
    assert decision["action"] == action and decision["target_id"] == "saved"
    assert (state, context) == frozen


def test_disabled_saved_task_is_waiting_and_is_never_replaced():
    state = {"completed_count": 1, "entries": [{"status": "pending",
             "question": {"target_id": "saved"}, "draft": {"answer": "草稿"}, "result": None}]}
    decision = decide_next_step({"decision": "disabled", "candidates": [candidate("other")]}, state)
    assert decision["action"] == "wait" and decision["source"] == "saved"


def test_one_explanation_is_proposed_then_the_next_target_can_continue():
    state = {"completed_count": 1, "entries": [completed("old")]}
    context = {"decision": "local", "candidates": [candidate("new")]}
    first = decide_next_step(context, state)
    assert first["action"] == "explain" and first["target_id"] == "old"
    state["entries"][0]["coached"] = True
    assert decide_next_step(context, state)["action"] == "practice"


@pytest.mark.parametrize("outcome,source", [("device_failure", "local"),
    ("unresolved", "local"), ("incorrect", "ai"), ("correct", "local")])
def test_unknown_or_device_results_do_not_trigger_a_forgetting_explanation(outcome, source):
    state = {"completed_count": 1, "entries": [completed("old", outcome, source=source)]}
    context = {"decision": "local", "candidates": [candidate("new")]}
    assert decide_next_step(context, state)["action"] == "practice"


def test_new_user_gets_course_instruction_instead_of_an_invented_quiz():
    decision = decide_next_step({"scope": {"targets": []}, "candidates": []},
                                {"completed_count": 0, "entries": []}, {"enrolled_count": 0})
    assert decision["action"] == "instruction" and decision["source"] == "course"
    assert decision["reason"] == "choose_first_learning_section"


def test_formal_pending_queue_and_first_learning_are_explicit_routes():
    context = {"scope": {"targets": []}, "candidates": []}
    state = {"completed_count": 0, "entries": []}
    course = {"enrolled_count": 2, "instruction_target_id": "new", "pending_target_id": "old"}
    decision = decide_next_step(context, state, course)
    assert decision["action"] == "continue_course" and decision["target_id"] == "old"
    course["pending_target_id"] = ""
    decision = decide_next_step(context, state, course)
    assert decision["action"] == "instruction" and decision["target_id"] == "new"


def test_cooldown_is_not_turned_into_a_fresh_quiz_or_a_learning_failure():
    decision = decide_next_step({"decision": "wait", "candidates": []},
                                {"completed_count": 4, "entries": []},
                                {"instruction_target_id": "new", "enrolled_count": 2})
    assert decision["action"] == "wait" and decision["reason"] == "answer_exposure_cooldown"


def test_audit_trace_is_bounded_and_contains_no_answers_or_second_mastery_score():
    decision = decide_next_step({"decision": "local", "candidates": [candidate("a")],
                                "learning_evidence": {"revision": 5}},
                               {"completed_count": 3, "entries": []})
    assert decision == {"policy": POLICY, "version": VERSION, "action": "practice",
        "reason": "error_independent_check", "mode": "independent_check", "target_id": "a",
        "candidate_id": "local:a", "source": "local", "evidence_revision": 5}
