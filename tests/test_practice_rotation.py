"""Large append-only banks stay reachable without more learner-state storage."""
from copy import deepcopy
import json

from kanamemory.domain import StudyStore
from kanamemory.learning_practice_policy import select_practice_plan
from kanamemory.learning_questions import build_learning_question
from kanamemory.learning_store import LearningStore
from support.learning_practice_policy_support import lesson
from support.learning_practice_policy_support import raw
from support.learning_practice_policy_support import observed
from support.learning_practice_policy_support import choose
from support.learning_store_fixtures import START, ready


def large_lesson(size=72):
    item = lesson()
    item["question_templates"] = {
        "grammar_cloze": [raw(f"place-{n:03}") for n in range(size)]}
    return item


def run_short_sessions(item, attempts, *, kinds=None, first_count=0):
    """Three responses per session; a serialized bounded cursor crosses sessions."""
    history, plans = [], []
    for count in range(first_count, first_count + attempts):
        state = {"attempt_count": count}
        plan = select_practice_plan(item, "application",
            kinds or list(item["question_templates"]), state, history)
        assert plan is not None
        # Refreshes and restarts do not advance a not-yet-completed attempt.
        assert select_practice_plan(item, "application",
            kinds or list(item["question_templates"]), state, history) == plan
        plans.append(plan)
        history.append(observed(item, plan["kind"], plan["variant"]))
        history = history[-24:]
        if (count + 1) % 3 == 0:
            history = json.loads(json.dumps(history))
    return plans, history


def test_appended_contexts_beyond_recent_history_window_are_not_starved():
    item = large_lesson()
    plans, history = run_short_sessions(item, 144)
    assert {p["variant"] for p in plans} == set(range(72))
    assert len(history) == 24
    assert all(a["variant"] != b["variant"] for a, b in zip(plans, plans[1:]))


def test_large_existing_attempt_counter_keeps_new_contexts_reachable():
    item = large_lesson()
    plans, _ = run_short_sessions(item, 144, first_count=9000)
    assert {p["variant"] for p in plans} == set(range(72))


def test_family_and_operation_rotation_survive_many_short_sessions():
    item = lesson()
    item["question_templates"] = {
        "grammar_cloze": [raw(f"scene-{n}", family="choose_context") for n in range(12)],
        "grammar_transform": [raw(f"saying-{n}", mode="text", scaffold="retrieval", family="produce")
                              for n in range(12)],
        "grammar_reorder": [raw(f"ordering-{n}", mode="reorder", scaffold="cued", family="reconstruct")
                            for n in range(12)],
    }
    plans, _ = run_short_sessions(item, 288)
    assert {p["input_mode"] for p in plans[:12]} == {"choice", "text", "reorder"}
    for kind in item["question_templates"]:
        assert {p["variant"] for p in plans if p["kind"] == kind} == set(range(12))


def test_disabling_and_reenabling_modes_does_not_reset_record_or_force_them():
    item = lesson()
    plans, history = run_short_sessions(item, 12)
    state = {"attempt_count": 12, "due_at_utc": "2030-01-01T00:00:00+00:00",
             "probe_due_at_utc": "2029-12-31T00:00:00+00:00"}
    unchanged = deepcopy((item, state, history))
    for _ in range(4):
        plan = select_practice_plan(item, "application", ["grammar_cloze"], state, history)
        assert plan["input_mode"] == "choice"
        assert select_practice_plan(item, "application", [], state, history) is None
    enabled = list(item["question_templates"])
    assert select_practice_plan(item, "application", enabled, state, history)["input_mode"] == "text"
    assert (item, state, history) == unchanged


def test_one_old_error_does_not_make_followup_a_permanent_family():
    item = lesson()
    history = [observed(item, "grammar_transform", 1, correct=False)]
    plans = []
    for count in range(1, 17):
        plan = choose(item, history, {"attempt_count": count})
        plans.append(plan)
        history.append(observed(item, plan["kind"], plan["variant"]))
    assert plans[0]["variant"] == 0 and plans[0]["kind"] == "grammar_transform"
    assert {p["kind"] for p in plans[-8:]} == {"grammar_cloze", "grammar_transform"}
    assert {p["variant"] for p in plans if p["kind"] == "grammar_transform"} >= {0, 1, 2}


def test_new_bank_planning_never_replaces_pending_snapshot_draft_or_due(tmp_path):
    path = tmp_path / "synthetic-rotation"
    item = lesson()
    with StudyStore(path, time_zone="UTC") as store:
        learning = LearningStore(store)
        learning.enroll(item, now=START)
        q = build_learning_question(item, "grammar_cloze", variant=0)
        learning.append_questions(q, now=START)
        when = ready(learning, item["id"], START)
        learning.save_draft(q["id"], {"answer": q["choices"][0]})
        frozen = learning.get_question(q["id"])
        unit = learning.get_unit(item["id"], "application")
        before = list(store._db.iterdump())
        expanded = large_lesson()
        for _ in range(12):
            select_practice_plan(expanded, "application", ["grammar_cloze"], unit,
                                 learning.practice_history(item["id"], "application"))
        assert list(store._db.iterdump()) == before
        assert learning.current({"grammar_cloze"}, now=when)["question"] == frozen
    with StudyStore(path, time_zone="UTC") as store:
        learning = LearningStore(store)
        assert learning.get_question(q["id"]) == frozen
        assert learning.get_unit(item["id"], "application") == unit
