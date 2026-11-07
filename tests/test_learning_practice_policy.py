"""Future selection changes; immutable work and memory state do not."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

from kanamemory.learning_practice_policy import (
    select_practice_plan, question_scaffold, guided_practice_sequence,
)
from kanamemory.learning_questions import build_learning_question

from support.learning_practice_policy_support import CID, raw, lesson, observed, choose


def test_new_content_begins_with_recognition_but_does_not_only_rotate_kinds():
    assert choose() == {"kind": "grammar_cloze", "variant": 0, "input_mode": "choice"}
    item = lesson()
    result = choose(item, [observed(item, "grammar_cloze", 0)], {"attempt_count": 1})
    assert result == {"kind": "grammar_transform", "variant": 0, "input_mode": "text"}


def test_two_recognition_successes_require_enabled_actual_retrieval():
    item = lesson()
    history = [observed(item, "grammar_cloze", n) for n in (0, 1)]
    assert choose(item, history, {"attempt_count": 2}) == {
        "kind": "grammar_transform", "variant": 1, "input_mode": "text"}


def test_cued_typing_does_not_claim_independent_production():
    item = lesson()
    history = [observed(item, "grammar_cloze", 0), observed(item, "grammar_transform", 0)]
    plan = choose(item, history, {"attempt_count": 2})
    assert plan["variant"] == 1 and plan["kind"] == "grammar_transform"
    assert question_scaffold(dict(raw("x", scaffold="retrieval"), kind="vocab_recall")) == "recognition"


def test_disabled_retrieval_does_not_create_a_question_or_change_due():
    item = lesson()
    history = [observed(item, "grammar_cloze", n) for n in (0, 1)]
    state = {"due_at_utc": "2031-01-01T00:00:00+00:00", "probe_due_at_utc": "2027-01-01T00:00:00+00:00"}
    before = deepcopy(state)
    result = choose(item, history, state, ["grammar_cloze"])
    assert result["kind"] == "grammar_cloze" and state == before
    assert select_practice_plan(item, "application", [], state, history) is None


def test_actual_error_returns_support_with_another_context():
    item = lesson()
    history = [observed(item, "grammar_transform", 1, correct=False)]
    result = choose(item, history)
    assert result == {"kind": "grammar_transform", "variant": 0, "input_mode": "text"}


def test_assisted_success_does_not_promote_scaffolding():
    item = lesson()
    history = [observed(item, "grammar_cloze", 0, assisted=True)]
    result = choose(item, history)
    assert result["kind"] == "grammar_cloze" and result["variant"] != 0


def test_exposure_only_success_is_not_independent_production_evidence():
    item = lesson()
    history = [observed(item, "grammar_cloze", n) for n in (0, 1)]
    for row in history:
        row.update(role="exposure", scheduled=False)
    result = choose(item, history)
    assert result["kind"] == "grammar_cloze" and result["variant"] == 2


def test_same_kind_changes_actual_context_and_not_only_choice_order():
    item = lesson()
    history = [observed(item, "grammar_cloze", 0)]
    result = choose(item, history, kinds=["grammar_cloze"])
    assert result["variant"] == 1
    duplicate = deepcopy(item["question_templates"]["grammar_cloze"][0])
    duplicate["choices"].reverse()
    item["question_templates"]["grammar_cloze"].insert(1, duplicate)
    result = choose(item, history, kinds=["grammar_cloze"])
    assert result["variant"] == 2


def test_unknown_background_is_not_silently_assumed_from_enrollment():
    item = lesson()
    item["question_templates"] = {"grammar_cloze": [raw("x", background=["word:unknown"])]}
    assert choose(item) is None
    assert choose(item, state={"known_background_ids": ["word:unknown"]})["variant"] == 0


def test_malformed_metadata_is_not_a_legacy_escape_hatch():
    item = lesson()
    item["question_templates"] = {"grammar_cloze": [raw("x")]}
    item["question_templates"]["grammar_cloze"][0]["practice"]["reviewed"] = False
    assert choose(item) is None


def test_legacy_templates_still_work_without_metadata_and_are_not_mutated():
    item = lesson()
    for values in item["question_templates"].values():
        for value in values:
            value.pop("practice")
    before = deepcopy(item)
    result = choose(item)
    assert result["kind"] == "grammar_cloze" and item == before


def test_legacy_text_probe_keeps_original_retrieval_classification():
    item = lesson()
    for values in item['question_templates'].values():
        for value in values:
            value.pop('practice')
    state = {'attempt_count': 2, 'probe_due_at_utc': '2026-10-31T12:00:00+00:00',
             'due_at_utc': '2026-11-03T12:00:00+00:00'}
    result = choose(item, state=state)
    assert result['kind'] == 'grammar_transform' and result['input_mode'] == 'text'
    assert question_scaffold({'kind':'grammar_transform','input_mode':'text'}) == 'retrieval'
    # Explicitly reviewed new tasks still require independent retrieval evidence.
    assert question_scaffold(dict(raw('x', mode='text', scaffold='cued'), kind='grammar_transform')) == 'cued'


def test_coverage_slot_visits_an_uncovered_family_instead_of_repeating_recall():
    item = lesson()
    history = [observed(item, "grammar_transform", 1), observed(item, "grammar_transform", 2),
               observed(item, "grammar_transform", 0)]
    result = choose(item, history, {"attempt_count": 3})
    assert result["kind"] == "grammar_cloze"


def test_foreign_ability_and_revision_do_not_diagnose_current_task():
    item = lesson()
    history = [observed(item, "grammar_transform", 2, correct=False)]
    history[0]["question"]["ability"] = "understanding"
    assert choose(item, history) == choose(item)
    history[0]["question"].update(ability="application", revision="other")
    assert choose(item, history) == choose(item)


def test_selector_uses_registered_append_provider_without_reindexing(monkeypatch):
    from kanamemory import learning_questions
    item = lesson()
    extra = raw("added", mode="text", scaffold="retrieval", family="produce")
    combined = deepcopy(item["question_templates"])
    combined["grammar_transform"].append(extra)
    monkeypatch.setattr(learning_questions, "practice_templates", lambda target: combined, raising=False)
    history = [observed(item, "grammar_transform", i) for i in (1, 2)]
    assert choose(item, history, {"attempt_count": 2})["variant"] == 3


def test_only_latest_24_completed_observations_affect_selection():
    item = lesson()
    recent = [observed(item, "grammar_cloze", n % 3) for n in range(24)]
    old = [observed(item, "grammar_transform", 0, correct=False)] * 100
    assert choose(item, old + recent, {"attempt_count": 24}) == choose(item, recent, {"attempt_count": 24})
