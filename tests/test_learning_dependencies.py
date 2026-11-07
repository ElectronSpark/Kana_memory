"""Prerequisite evidence, ordering and preservation of real saved review state."""
from datetime import timedelta
import json
from pathlib import Path
import sys

import pytest
from record_test_support import decode_current, encode_current

from kanamemory.learning_dependencies import build_dependency_plan, validate_catalog
from kanamemory.learning_store import LearningStore
from kanamemory.learning_records import learning_records_snapshot
from support.learning_store_fixtures import START, stores, item, question, ready, legacy_dump
from kanamemory.domain import Question


from support.learning_dependencies_support import lesson, queued


def test_dag_plan_is_stable_and_diamond_dependencies_added_only_once(stores):
    _, learning = stores
    lessons = [lesson("d", ["b", "c"]), lesson("b", ["a"]), lesson("c", ["a"]), lesson("a")]
    assert validate_catalog(lessons) == ["a", "b", "c", "d"]
    plan = learning.enroll_with_prerequisites([lessons[0]], lessons, now=START)
    assert plan["ordered_ids"] == plan["added"] == ["a", "b", "c", "d"]
    assert plan["to_enroll"] == ["a", "b", "c", "d"]
    assert [x["id"] for x in plan["unmet"]] == ["a", "b", "c"]
    assert learning.enroll_with_prerequisites([lessons[0]], lessons, now=START)["added"] == []
    assert [x["id"] for x in learning.list_items()] == ["a", "b", "c", "d"]


@pytest.mark.parametrize("lessons,match", [
    ([lesson("a", ["missing"])], "缺少"),
    ([lesson("a", ["a"])], "成环"),
    ([lesson("a", ["b"]), lesson("b", ["c"]), lesson("c", ["a"])], "成环"),
    ([lesson("a"), lesson("a")], "重复"),
    ([lesson("a", ["b", "b"]), lesson("b")], "重复"),
])
def test_invalid_curriculum_cannot_partly_enroll(stores, lessons, match):
    _, learning = stores
    with pytest.raises(ValueError, match=match):
        validate_catalog(lessons)
    assert learning.list_items() == []


def test_missing_or_cyclic_enrollment_is_atomic(stores):
    _, learning = stores
    for lessons in ([lesson("a", ["missing"])], [lesson("a", ["b"]), lesson("b", ["a"])]):
        with pytest.raises(ValueError):
            learning.enroll_with_prerequisites([lessons[0]], lessons, now=START)
        assert learning.list_items() == []


def test_guide_does_not_unlock_prerequisite_but_independent_success_does(stores):
    _, learning = stores
    a, b = lesson("a"), lesson("b", ["a"])
    learning.enroll_with_prerequisites([b], [b, a], now=START)
    # Deliberately append the blocked target first: it cannot hide the base.
    queued(learning, b)
    qa = queued(learning, a)
    assert learning.current(now=START)["content"]["id"] == "a"
    assert learning.counts(now=START)["instruction"] == 1
    assert learning.counts(now=START)["blocked"] == 1
    with pytest.raises(ValueError, match="前置"):
        learning.advance_instruction("b", "intro_done", now=START)
    after_guide = ready(learning, "a")
    current = learning.current(now=START)
    assert current["type"] == "prerequisite"
    assert current["prerequisites"][0]["status"] == "awaiting_independent_success"
    assert current["due_at_utc"] == (START + timedelta(seconds=60)).isoformat()
    assert learning.history() == []
    learning.submit(qa, "right", True, now=after_guide)
    assert learning.prerequisite_status("b")["blocked"]  # Unrated is not evidence.
    learning.finalize(qa, 3, now=after_guide)
    assert learning.prerequisite_status("b")["ready"]
    assert learning.current(now=after_guide)["content"]["id"] == "b"
    assert learning.counts(now=after_guide)["blocked"] == 0


@pytest.mark.parametrize("outcome,correct,assisted,rating", [
    ("incorrect", False, False, 3), ("correct", True, True, 3),
    ("revealed", None, False, 3), ("unresolved", None, False, None),
    ("correct", True, False, 1), ("device_failure", None, False, None),
])
def test_failure_exposure_unknown_or_again_never_claims_prerequisite_success(stores, outcome, correct, assisted, rating):
    _, learning = stores
    a, b = lesson("a"), lesson("b", ["a"])
    learning.enroll([a, b], now=START)
    qa = queued(learning, a)
    when = ready(learning, "a")
    learning.submit(qa, "answer", correct, outcome=outcome, assisted=assisted, now=when)
    learning.finalize(qa, rating, now=when)
    assert learning.prerequisite_status("b")["blocked"]


def test_ready_prerequisite_is_not_revoked_by_pause_or_later_lapse(stores):
    _, learning = stores
    a, b = lesson("a"), lesson("b", ["a"])
    learning.enroll([a, b], now=START)
    qa = queued(learning, a)
    when = ready(learning, "a")
    learning.submit(qa, "right", True, now=when)
    event = learning.finalize(qa, 3, now=when)
    learning.set_suspended("a", "understanding", True)
    assert learning.prerequisite_status("b")["ready"]
    learning.set_suspended("a", "understanding", False)
    queued(learning, a, "a:q2")
    from datetime import datetime
    when = datetime.fromisoformat(event["due_at_utc"])
    learning.submit("a:q2", "wrong", False, now=when)
    learning.finalize("a:q2", 1, now=when)
    assert learning.prerequisite_status("b")["ready"]


def test_explicit_prior_knowledge_is_idempotent_and_not_success_evidence(stores):
    _, learning = stores
    a, b, c = lesson("a"), lesson("b", ["a"]), lesson("c", ["b"])
    learning.enroll([a, b, c], now=START)
    before = [learning.get_unit(x, "understanding") for x in ("a", "b", "c")]
    saved = learning.acknowledge_prerequisites("b", now=START)
    assert learning.acknowledge_prerequisites("b", now=START + timedelta(days=1)) == saved
    assert learning.prerequisite_status("b")["waived"]
    assert not learning.prerequisite_status("b")["ready"]
    assert not learning.prerequisite_status("b")["blocked"]
    assert learning.prerequisite_status("c")["blocked"]
    assert learning.history() == []
    assert before == [learning.get_unit(x, "understanding") for x in ("a", "b", "c")]


def test_old_content_and_queue_are_not_rewritten_or_given_new_catalogue_dependencies(stores):
    store, learning = stores
    old = item("old", title="原有课程")  # 0.8.0 snapshot has no depends_on.
    learning.enroll(old, now=START)
    qid = queued(learning, old)
    when = ready(learning, "old")
    learning.save_draft(qid, {"text": "unfinished"})
    snapshot = learning.get_question(qid)
    original_content = learning.get_item("old")
    old_unit = learning.get_unit("old", "understanding")
    replacement = lesson("old", ["new_base"], revision="new")
    plan = learning.enroll_with_prerequisites([replacement], [replacement, lesson("new_base")], now=when)
    assert plan["ordered_ids"] == ["old"]
    assert plan["added"] == []
    assert learning.get_item("new_base") is None
    assert learning.get_item("old") == original_content
    assert learning.get_question(qid) == snapshot
    assert learning.get_unit("old", "understanding") == old_unit
    assert learning.current(now=when)["question"] == snapshot


def test_disable_reopen_and_backup_keep_blocked_queue_and_existing_draft(stores):
    store, learning = stores
    a, b = lesson("a"), lesson("b", ["a"])
    learning.enroll_with_prerequisites([b], [a, b], now=START)
    qa = queued(learning, a)
    qb = queued(learning, b, kind="grammar_cloze", ability="application")
    when = ready(learning, "a")
    learning.save_draft(qa, {"text": "old partial answer"})
    snapshots = [learning.get_question(qid) for qid in (qa, qb)]
    units = [learning.get_unit("a", "understanding"), learning.get_unit("b", "application")]
    store.set_setting("learning_preferences_v1", {"enabled": ["grammar_cloze"]})
    state = learning.current({"grammar_cloze"}, now=when)
    assert state["type"] == "prerequisite"
    assert state["counts"]["disabled"] == 1
    assert state["counts"]["blocked"] == 1
    backup = store.export_backup()
    store.import_backup(backup)
    restored = LearningStore(store)
    assert [restored.get_question(qid) for qid in (qa, qb)] == snapshots
    assert [restored.get_unit("a", "understanding"), restored.get_unit("b", "application")] == units
    store.set_setting("learning_preferences_v1", {"enabled": ["grammar_meaning", "grammar_cloze"]})
    assert restored.current({"grammar_meaning", "grammar_cloze"}, now=when)["question"] == snapshots[0]
    assert restored.prerequisite_status("b")["blocked"]


def test_add_prerequisites_does_not_modify_old_kana_or_word_tool_queues(stores):
    store, learning = stores
    store.record_review("kana-a", "kana_choice", "hiragana", 3, now=START)
    sid = store.start_session([Question("kana-i", "kana_choice", "hiragana", "い", "i", ["i", "a"])], now=START)
    store.save_session_progress(sid, 0, 0, state={"romaji_answer": "i"})
    before = legacy_dump(store)
    a, b = lesson("a"), lesson("b", ["a"])
    learning.enroll_with_prerequisites([b], [a, b], now=START)
    queued(learning, a)
    queued(learning, b)
    learning.acknowledge_prerequisites("b", now=START)
    assert legacy_dump(store) == before


def test_record_counts_match_blocked_first_learning_and_no_fake_due(stores):
    _, learning = stores
    a, b = lesson("a"), lesson("b", ["a"])
    learning.enroll([a, b], now=START)
    queued(learning, b)
    snapshot = learning_records_snapshot(learning, now=START)
    blocked = next(x for x in snapshot["items"] if x["id"] == "b")
    assert snapshot["due"] == learning.counts(now=START)["ready"] == 0
    assert blocked["dependency_status"]["blocked"]
    assert all(not unit["due"] and unit["prerequisite_blocked"] for unit in blocked["units"])


def test_self_report_and_saved_draft_survive_backup_without_rescheduling(stores):
    store, learning = stores
    a, b = lesson("a"), lesson("b", ["a"])
    learning.enroll([a, b], now=START)
    qb = queued(learning, b)
    learning.acknowledge_prerequisites("b", now=START)
    when = ready(learning, "b")
    learning.save_draft(qb, {"text": "draft after self-report"})
    item_before = learning.get_item("b")
    question_before = learning.get_question(qb)
    store.import_backup(store.export_backup())
    restored = LearningStore(store)
    assert restored.get_item("b") == item_before
    assert restored.get_question(qb) == question_before
    assert restored.current(now=when)["question"] == question_before
    assert restored.history() == []


def test_existing_review_due_and_pending_snapshot_survive_new_catalogue_and_reenable(stores):
    store, learning = stores
    old = item("old", title="原有课程")
    learning.enroll(old, now=START)
    first = queued(learning, old)
    when = ready(learning, "old")
    learning.submit(first, "correct", True, now=when)
    learning.finalize(first, 3, now=when)
    followup = queued(learning, old, "old:due")
    unit = learning.get_unit("old", "understanding")
    snapshot = learning.get_question(followup)
    newer = lesson("old", ["new_base"], revision="v2")
    plan = learning.enroll_with_prerequisites([newer], [newer, lesson("new_base")], now=when)
    assert plan["ordered_ids"] == ["old"]
    assert not plan["added"]
    assert learning.current(set(), now=when)["type"] == "disabled"
    store.import_backup(store.export_backup())
    reopened = LearningStore(store)
    from datetime import datetime
    due = datetime.fromisoformat(unit["due_at_utc"])
    assert reopened.current({"grammar_meaning"}, now=due)["question"] == snapshot
    assert reopened.get_unit("old", "understanding") == unit


@pytest.mark.parametrize("field,value", [
    ("depends_on", "not-an-id-list"),
    ("prerequisite_acknowledged", "true"),
    ("prerequisite_evidence", "independent_success"),
])
def test_malformed_dependency_backup_rejected_without_changing_saved_queue(stores, field, value):
    store, learning = stores
    a, b = lesson("a"), lesson("b", ["a"])
    learning.enroll([a, b], now=START)
    queued(learning, b)
    learning.acknowledge_prerequisites("b", now=START)
    before = learning.get_item("b")
    payload = decode_current(store.export_backup())
    row = next(r for r in payload["tables"]["learning_content"] if r["content_id"] == "b")
    key = "content_json" if field == "depends_on" else "checkpoint_json"
    parsed = json.loads(row[key])
    parsed[field] = value
    row[key] = json.dumps(parsed)
    with pytest.raises(ValueError):
        store.import_backup(encode_current(payload))
    assert learning.get_item("b") == before
