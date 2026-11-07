"""Actual controls remain selectable without expanding AI authorship or credit."""
from copy import deepcopy
from datetime import timedelta

import pytest

from kanamemory import learning_questions, pitch_practice as pitch
from kanamemory.ai_question_capabilities import capability_catalog, validate_catalog
from kanamemory.ai_questions import AI_CHOICE_KINDS, build_learning_scope
from kanamemory.ai_service import AIServiceError
from kanamemory.domain import SKILLS
from kanamemory.learning_practice_policy import practice_candidates
from kanamemory.learning_questions import KIND_ABILITIES, build_learning_question
from kanamemory.tutor_engine import local_question
from kanamemory.tutor_service import make_tutor_request, validate_tutor_request
from kanamemory.tutor_store import AITutorStore, validate_tutor_value
from support.learning_practice_policy_support import lesson as varied_lesson, raw
from support.learning_store_fixtures import START, ready, stores
from support.pitch_alignment_support import (
    CORRECT as ALIGNMENT_ACTIONS, lesson as alignment_lesson, registry as alignment_registry,
)
from support.pitch_practice_support import item as pitch_item, template
from support.pitch_sequence_support import (
    CORRECT as SEQUENCE_ACTIONS, lesson as sequence_lesson, registry as sequence_registry,
)
from support.tutor_service_support import context as service_context


ENABLED = tuple(KIND_ABILITIES)
REQUEST_ID = "coverage_contract_0123456789"


def test_catalogue_covers_every_real_kind_without_authorizing_more_generation():
    enabled = ["vocab_reading", "pronunciation_application", "word_romaji", "kana_handwriting"]
    value = capability_catalog(enabled, [{"kind": "vocab_reading"}, {"kind": "word_romaji"}])
    indexed = {row["kind"]: row for row in value}
    assert len(KIND_ABILITIES) == 19 and len(SKILLS) == 9
    assert len(value) == 28 and set(indexed) == set(KIND_ABILITIES) | SKILLS
    assert {k for k, row in indexed.items() if row["route"] == "course"} == set(KIND_ABILITIES)
    assert {k for k, row in indexed.items() if row["route"] == "native"} == SKILLS
    assert {k for k, row in indexed.items() if row["generation"]} == set(AI_CHOICE_KINDS)
    assert len(AI_CHOICE_KINDS) == 6
    assert {k for k, row in indexed.items() if row["enabled"]} == set(enabled)
    assert {k for k, row in indexed.items() if row["candidate_available"]} == {"vocab_reading", "word_romaji"}
    assert validate_catalog(value) == value
    forged = deepcopy(value)
    next(row for row in forged if row["kind"] == "kana_handwriting")["generation"] = True
    with pytest.raises(ValueError):
        validate_catalog(forged)


def _marked_context():
    value = service_context()
    value["question"]["practice"] = {
        "schema_version": 1, "task_family": "choose_context", "context_id": "book",
        "scaffold": "recognition", "background_ids": [], "reviewed": True,
        "target_spans": [{"start": 3, "end": 4, "quote": "本"}],
    }
    return value


def test_request_keeps_public_target_anchors_in_current_and_candidate_questions():
    context = _marked_context()
    before = deepcopy(context)
    request = make_tutor_request(context, "next", REQUEST_ID)
    for question in (request["question"], request["candidates"][0]["question"]):
        assert question["presentation"]["target_spans"] == [{"start": 3, "end": 4, "quote": "本"}]
        assert question["presentation"]["task_family"] == "choose_context"
        assert question["presentation"]["scaffold"] == "recognition"
        assert question["prompt"] == "これは本です。"
        assert "secret_timestamp" not in question
    assert context == before
    assert validate_tutor_request(request) == request


@pytest.mark.parametrize("target", ["question", "candidate"])
@pytest.mark.parametrize("span", [
    {"start": -1, "end": 4, "quote": "本"},
    {"start": 3, "end": 99, "quote": "本"},
    {"start": 3, "end": 4, "quote": "答"},
    {"start": True, "end": 4, "quote": "れは本"},
])
def test_wire_rejects_invalid_public_target_spans(target, span):
    request = make_tutor_request(_marked_context(), "next", REQUEST_ID)
    question = request["question"] if target == "question" else request["candidates"][0]["question"]
    question["presentation"]["target_spans"] = [span]
    with pytest.raises(AIServiceError):
        validate_tutor_request(request)
    source = _marked_context()
    source["question"]["presentation"] = deepcopy(question["presentation"])
    with pytest.raises(AIServiceError):
        make_tutor_request(source, "next", REQUEST_ID)


def test_request_rejects_hidden_transcript_with_public_target_span():
    context = _marked_context()
    public = make_tutor_request(context, "next", REQUEST_ID)["question"]["presentation"]
    context["question"]["presentation"] = {**public, "hidden_transcript": True}
    with pytest.raises(AIServiceError):
        make_tutor_request(context, "next", REQUEST_ID)


def test_appended_practice_variants_require_independent_background_knowledge(monkeypatch):
    item = varied_lesson()
    templates = deepcopy(item["question_templates"])
    templates["grammar_cloze"].extend(raw(f"context-{index}") for index in range(3, 7))
    templates["grammar_cloze"].append(raw("late-context", background=["word:unfamiliar"]))
    monkeypatch.setattr(learning_questions, "practice_templates", lambda item: deepcopy(templates))
    original = deepcopy(item)
    no_background = list(practice_candidates(item, ["grammar_cloze"]))
    assert {row["variant"] for row in no_background} == set(range(7))
    ready_background = list(practice_candidates(item, ["grammar_cloze"],
                                               known_background_ids=["word:unfamiliar"]))
    assert {row["variant"] for row in ready_background} == set(range(8))
    question = build_learning_question(item, "grammar_cloze", variant=7)
    assert question["practice"]["context_id"] == "late-context"
    assert question["practice"]["background_ids"] == ["word:unfamiliar"]
    assert item == original
    assert list(practice_candidates(item, [])) == []


@pytest.fixture(params=["v1-fill", "v1-identify", "v2", "v3"])
def pitch_case(request, stores, sequence_registry, alignment_registry):
    store, learning = stores
    mode = request.param
    if mode == "v2":
        item, actions = sequence_lesson(sequence_registry), list(SEQUENCE_ACTIONS)
    elif mode == "v3":
        item, actions = alignment_lesson(alignment_registry), list(ALIGNMENT_ACTIONS)
    else:
        item = pitch_item(template("pitch_identify" if mode == "v1-identify" else "pitch_fill"))
        actions = ["H", "L", "L"]
    kind = "pronunciation_application"
    learning.enroll(item, now=START)
    q = build_learning_question(item, kind)
    learning.append_questions(q, now=START)
    when = ready(learning, item["id"])
    learning.submit(q["id"], q["answer"], True, now=when)
    learning.finalize(q["id"], 3, now=when)
    now = when + timedelta(days=30)
    tutor = AITutorStore(store)
    tutor.start()
    saved = learning.get_item(item["id"])
    scope = build_learning_scope(learning, ENABLED, now=now, supported_kinds=ENABLED, _items=[saved])
    question = local_question(saved, kind)
    row = tutor.append_question(question, scope, ENABLED, now=now)
    return store, learning, tutor, scope, row, actions, mode, now


def _pitch_draft(question, actions, mode, *, complete=True, wrong=False):
    draft = pitch.initial_draft(question)
    if mode == "v1-identify":
        draft = pitch.set_selection(question, draft, "mora_count", 3 if wrong else 2)
        return pitch.set_selection(question, draft, "accent", 1) if complete else draft
    actions = list(actions if complete else actions[:1])
    if wrong:
        actions[0] = "H" if actions[0] == "L" else "L"
    for action in actions:
        draft = pitch.editor_action(question, draft, action)
    return draft


def test_pitch_snapshot_draft_restart_and_incomplete_submission_are_atomic(pitch_case):
    store, learning, tutor, scope, row, actions, mode, now = pitch_case
    q = row["question"]
    partial = _pitch_draft(q, actions, mode, complete=False)
    assert pitch.grade_answer(q, partial["answer"])["status"] == "empty"
    tutor.save_draft(row["id"], partial)
    reopened = AITutorStore(store)
    assert reopened.current()["question"] == q
    assert reopened.current()["draft"] == {**partial, "presented": False}
    before = reopened.snapshot()
    history = deepcopy(learning.history())
    evidence = store._db.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0]
    with pytest.raises(ValueError, match="完成"):
        reopened.submit(row["id"], partial["answer"], ENABLED, scope, now=now)
    with pytest.raises(ValueError):
        reopened.save_draft(row["id"], {**partial, "answer": q["answer"]})
    forged = deepcopy(before)
    forged["entries"][-1]["question"]["answer"] = "pp1:{}"
    with pytest.raises(ValueError):
        validate_tutor_value(forged)
    assert reopened.snapshot() == before and learning.history() == history
    assert store._db.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0] == evidence


@pytest.mark.parametrize("outcome", ["correct", "incorrect", "revealed"])
def test_pitch_result_and_completion_are_idempotent_without_formal_review(pitch_case, outcome):
    store, learning, tutor, scope, row, actions, mode, now = pitch_case
    q = row["question"]
    draft = _pitch_draft(q, actions, mode, wrong=outcome == "incorrect")
    if outcome == "revealed":
        draft = pitch.initial_draft(q)
    tutor.save_draft(row["id"], draft)
    history = deepcopy(learning.history())
    before = store._db.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0]
    result = tutor.submit(row["id"], draft["answer"], ENABLED, scope,
                          revealed=outcome == "revealed", now=now)
    assert result["outcome"] == outcome
    assert result["correct"] is (None if outcome == "revealed" else outcome == "correct")
    assert result["assisted"] is (outcome == "revealed")
    assert result["training_only"] and result["scheduled"] is False
    assert tutor.submit(row["id"], "changed", [], {}, revealed=True, now=now) == result
    reopened = AITutorStore(store)
    assert reopened.complete(row["id"], now=now) == result
    assert reopened.complete(row["id"], now=now) == result
    assert reopened.current() is None and reopened.snapshot()["completed_count"] == 1
    assert learning.history() == history
    assert store._db.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0] == before + 1
