"""Real course/Tutor/diagnostic persistence; no personal data or model calls."""
from copy import deepcopy
from datetime import timedelta

import pytest

from kanamemory import multi_cloze as cloze
from kanamemory.ai_questions import build_learning_scope
from kanamemory.domain import StudyStore
from kanamemory.learning_questions import KIND_ABILITIES, build_learning_question
from kanamemory.learning_store import LearningStore, validate_backup_rows
from kanamemory.progress_backup import export_backup, import_backup, inspect_backup
from kanamemory.retention_session import DiagnosticStore, validate_database
from kanamemory.tutor_engine import local_question
from kanamemory.tutor_store import AITutorStore, validate_tutor_value
from support.multi_cloze_integration_support import (
    BACKGROUND, CID, KIND, cloze_case, enqueue, scheduling_state, score_primary,
)


def test_partial_draft_and_exact_snapshot_survive_database_close_reopen(cloze_case):
    case = cloze_case
    q = enqueue(case)
    partial = cloze.edit_slot(q, cloze.initial_draft(q), value="は")
    case.learning.save_draft(q["id"], partial)
    frozen = case.store._db.execute("SELECT snapshot_json FROM learning_question WHERE question_id=?", (q["id"],)).fetchone()[0]
    case.store.close()
    with StudyStore(case.path, time_zone="UTC") as reopened:
        learning = LearningStore(reopened)
        assert learning.get_question(q["id"])["draft"] == partial
        assert reopened._db.execute("SELECT snapshot_json FROM learning_question WHERE question_id=?", (q["id"],)).fetchone()[0] == frozen
        validate_backup_rows(reopened._db)


def test_incomplete_or_invalid_submission_cannot_freeze_a_result(cloze_case):
    case = cloze_case
    q = enqueue(case)
    partial = cloze.edit_slot(q, cloze.initial_draft(q), value="は")
    case.learning.save_draft(q["id"], partial)
    before = scheduling_state(case.learning)
    for answer in ("", 'cloze1:["は"]', "はへ"):
        with pytest.raises(ValueError):
            case.learning.submit(q["id"], answer, True, now=case.when)
        assert case.learning.get_question(q["id"])["result"] is None
    assert scheduling_state(case.learning) == before
    assert case.learning.get_question(q["id"])["draft"] == partial


def test_background_error_is_diagnostic_without_any_due_date_change(cloze_case):
    case = cloze_case
    q = enqueue(case)
    before = scheduling_state(case.learning)
    background = scheduling_state(case.learning, BACKGROUND)
    answer = cloze.encode_answer(["は", "で"])
    result = case.learning.submit(q["id"], answer, False, now=case.when)
    assert result["outcome"] == "unresolved" and result["correct"] is None
    assert result["primary_correct"] is True and result["background_errors"] == ["slot2"]
    event = case.learning.finalize(q["id"], rating=None, now=case.when)
    assert not event["scheduled"]
    assert scheduling_state(case.learning) == before
    assert scheduling_state(case.learning, BACKGROUND) == background
    validate_backup_rows(case.store._db)


def test_primary_error_uses_existing_again_settlement_once(cloze_case):
    case = cloze_case
    q = enqueue(case)
    before = scheduling_state(case.learning)
    background = scheduling_state(case.learning, BACKGROUND)
    result = case.learning.submit(q["id"], cloze.encode_answer(["を", "へ"]), True, now=case.when)
    assert result["outcome"] == "incorrect" and result["correct"] is False
    assert result["primary_correct"] is False
    event = case.learning.finalize(q["id"], rating=4, now=case.when)
    assert event["scheduled"] and event["rating"] == 1
    assert scheduling_state(case.learning)["review_count"] == before["review_count"] + 1
    assert case.learning.finalize(q["id"], rating=3, now=case.when) == event
    assert scheduling_state(case.learning, BACKGROUND) == background


def test_correct_answer_freezes_first_result_and_finalizes_only_once(cloze_case):
    case = cloze_case
    q = enqueue(case)
    before = scheduling_state(case.learning)
    result = case.learning.submit(q["id"], q["answer"], False, now=case.when)
    assert result["correct"] is True and len(result["slot_results"]) == 2
    assert case.learning.submit(q["id"], "changed", False, now=case.when) == result
    event = case.learning.finalize(q["id"], rating=3, now=case.when)
    assert event["scheduled"] and event["rating"] == 3
    assert case.learning.finalize(q["id"], rating=4, now=case.when + timedelta(days=1)) == event
    assert scheduling_state(case.learning)["review_count"] == before["review_count"] + 1
    assert case.store._db.execute("SELECT COUNT(*) FROM learning_event WHERE question_id=?", (q["id"],)).fetchone()[0] == 1
    validate_backup_rows(case.store._db)


def test_backup_restores_partial_draft_without_rebuilding_new_content(cloze_case, tmp_path):
    case = cloze_case
    q = enqueue(case)
    partial = cloze.edit_slot(q, cloze.initial_draft(q), value="は")
    case.learning.save_draft(q["id"], partial)
    old = deepcopy(case.learning.get_question(q["id"]))
    backup = export_backup(case.store)
    with StudyStore(tmp_path / "restored", time_zone="UTC") as restored:
        inspect_backup(restored, backup)
        import_backup(restored, backup)
        learning = LearningStore(restored)
        assert learning.get_question(q["id"]) == old
        assert scheduling_state(learning) == scheduling_state(case.learning)
        validate_backup_rows(restored._db)


def test_frozen_guided_multi_gap_is_exposure_and_retains_partial_draft(cloze_case):
    case = cloze_case
    guide = build_learning_question(case.item, KIND, guided=True)
    assert guide["input_mode"] == "multi_cloze" and guide["evidence_mode"] == "exposure_only"
    partial = cloze.edit_slot(guide, cloze.initial_draft(guide), value="は")
    checkpoint = {"guide_question": guide, "guide_draft": partial}
    cloze.validate_checkpoint(CID, case.item["revision"], checkpoint)
    with pytest.raises(ValueError):
        case.learning.append_questions(guide, now=case.when)
    assert scheduling_state(case.learning)["review_count"] == 0


def tutor_case(case):
    score_primary(case)
    now = case.when + timedelta(days=30)
    enabled = tuple(KIND_ABILITIES)
    scope = build_learning_scope(case.learning, enabled, now=now, supported_kinds=enabled)
    tutor = AITutorStore(case.store)
    tutor.start()
    q = local_question(case.learning.get_item(CID), KIND)
    row = tutor.append_question(q, scope, enabled, now=now)
    return tutor, row, scope, enabled, now


def test_tutor_resumes_partial_multi_gap_and_rejects_incomplete_submit(cloze_case):
    case = cloze_case
    tutor, row, scope, enabled, now = tutor_case(case)
    q = row["question"]
    partial = cloze.edit_slot(q, cloze.initial_draft(q), value="は")
    tutor.save_draft(row["id"], partial)
    reopened = AITutorStore(case.store)
    assert reopened.current()["draft"] == {**partial, "presented": False}
    before = reopened.snapshot()
    with pytest.raises(ValueError):
        reopened.submit(row["id"], "", enabled, scope, now=now)
    assert reopened.snapshot() == before
    validate_tutor_value(before)


@pytest.mark.parametrize("values,outcome", [(["は", "へ"], "correct"), (["は", "で"], "unresolved")])
def test_tutor_uses_auxiliary_policy_and_preserves_component_diagnostics(cloze_case, values, outcome):
    case = cloze_case
    tutor, row, scope, enabled, now = tutor_case(case)
    before = scheduling_state(case.learning)
    background = scheduling_state(case.learning, BACKGROUND)
    history = deepcopy(case.learning.history())
    result = tutor.submit(row["id"], cloze.encode_answer(values), enabled, scope, now=now)
    assert result["outcome"] == outcome and result["scheduled"] is False and result["training_only"]
    assert result["primary_correct"] is True and len(result["slot_results"]) == 2
    assert tutor.submit(row["id"], "changed", [], {}, now=now) == result
    assert tutor.complete(row["id"], now=now) == result
    assert tutor.complete(row["id"], now=now) == result
    assert case.learning.history() == history
    assert scheduling_state(case.learning)["review_count"] == before["review_count"]
    assert scheduling_state(case.learning, BACKGROUND) == background
    if outcome == "unresolved":
        assert scheduling_state(case.learning) == before
    validate_tutor_value(tutor.snapshot())


@pytest.mark.parametrize("purpose", ["check_only", "check_and_adjust"])
def test_diagnostic_multi_gap_draft_restore_and_scope_owned_settlement(cloze_case, tmp_path, purpose):
    case = cloze_case
    score_primary(case)
    case.when += timedelta(days=30)
    q = enqueue(case, "diagnostic")
    service = DiagnosticStore(case.store, case.learning)
    episode = service.start([CID], [KIND], "synthetic-cloze-" + purpose, purpose=purpose, now=case.when)
    assert len(episode["entries"]) == 1
    row = episode["entries"][0]
    assert row["course_question_id"] == q["id"] and row["question"]["input_mode"] == "multi_cloze"
    partial = cloze.edit_slot(q, cloze.initial_draft(q), value="は")
    service.save_draft(episode["id"], row["id"], partial)
    backup = export_backup(case.store)
    with StudyStore(tmp_path / "diagnostic-restored", time_zone="UTC") as restored:
        inspect_backup(restored, backup)
        import_backup(restored, backup)
        learning = LearningStore(restored)
        resumed = DiagnosticStore(restored, learning)
        current = resumed.current(episode["id"], [KIND])
        assert current["draft"] == partial
        before = scheduling_state(learning)
        background = scheduling_state(learning, BACKGROUND)
        with pytest.raises(ValueError):
            resumed.submit(episode["id"], row["id"], "", [KIND], now=case.when)
        result = resumed.submit(episode["id"], row["id"], q["answer"], [KIND], now=case.when)
        assert result.get("observed_judgement", result)["correct"] is True
        final = resumed.advance(episode["id"], row["id"], [KIND], rating=3, now=case.when)
        scheduled = final["entries"][0]["settlement"]["scheduled"]
        assert scheduled is (purpose == "check_and_adjust")
        assert scheduling_state(learning)["review_count"] == before["review_count"] + int(scheduled)
        assert scheduling_state(learning, BACKGROUND) == background
        validate_database(restored._db)
