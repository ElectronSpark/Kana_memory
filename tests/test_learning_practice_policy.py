"""Future selection changes; immutable work and memory state do not."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

from kanamemory.learning_practice_policy import (
    select_practice_plan, question_scaffold, guided_practice_sequence, practice_candidates, _fingerprint,
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
    # Design 11 §5 D4/D5 (R1): the only template is served by the relaxed pass, still flagged as
    # one unmet requirement; enrollment never makes the background known.
    assert choose(item) == {"kind": "grammar_cloze", "variant": 0, "input_mode": "choice"}
    assert [c["relaxed"] for c in practice_candidates(item, ["grammar_cloze"])] == [1]
    assert choose(item, state={"known_background_ids": ["word:unknown"]})["variant"] == 0
    assert "relaxed" not in next(practice_candidates(item, ["grammar_cloze"], known_background_ids=["word:unknown"]))


def test_malformed_metadata_is_not_a_legacy_escape_hatch():
    item = lesson()
    item["question_templates"] = {"grammar_cloze": [raw("x")]}
    item["question_templates"]["grammar_cloze"][0]["practice"]["reviewed"] = False
    assert choose(item) is None


@pytest.mark.parametrize("extra", [
    {"opens_after_reviews": 0}, {"opens_after_reviews": 40}, {"scenario_id": "s"},
    {"scenario_id": "station_ticket_office", "opens_after_reviews": 6}, {"scenario_id": "a" * 64},
])
def test_pacing_keys_are_accepted_practice_metadata(extra):
    from kanamemory.learning_questions import validate_practice
    template = raw("x")
    template["practice"].update(extra)
    assert validate_practice(template, target_id=CID) == template["practice"]


@pytest.mark.parametrize("extra", [
    {"opens_after_reviews": -1}, {"opens_after_reviews": 41}, {"opens_after_reviews": True},
    {"opens_after_reviews": 2.0}, {"opens_after_reviews": "2"}, {"opens_after_reviews": None},
    {"scenario_id": ""}, {"scenario_id": "Station"}, {"scenario_id": "1st"}, {"scenario_id": "a-b"},
    {"scenario_id": "a" * 65}, {"scenario_id": "station\n"}, {"scenario_id": 7}, {"scenario_id": None},
    {"opens_after": 2}, {"scenario": "station"},
])
def test_malformed_pacing_keys_are_rejected_like_any_other_practice_metadata(extra):
    from kanamemory.learning_questions import validate_practice
    template = raw("x")
    template["practice"].update(extra)
    with pytest.raises(ValueError):
        validate_practice(template, target_id=CID)
    item = lesson()
    item["question_templates"] = {"grammar_cloze": [template]}
    assert choose(item) is None  # Malformed metadata is not a legacy escape hatch.


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


# ---- Listening tasks that differ only in what is read aloud

HEARD = "vocab_audio_meaning"
# Literal digests from before the audio text joined the listening fingerprint. Do not edit to follow a change:
# completed history is matched to candidates by these values.
FORMER_FINGERPRINTS = [
    ({"kind": "vocab_meaning", "prompt": "切符", "task": "这个词在本课中的意思是？", "answer": "票",
      "choices": ["票", "邮票"], "audio_text": "きっぷ"},
     "9334e805b26ccf1c7b0b2ec6bf8d068969f8ae7d43e040a4ba53e3586aadc931"),
    ({"kind": "vocab_reading", "input_mode": "text", "prompt": "切符", "task": "写出假名读法。", "answer": "きっぷ"},
     "cbae639fb8ece7a0f23b202db7c9ce0895dd7b16825a117f0ddedd74c4c1a542"),
    ({"kind": "grammar_cloze", "input_mode": "choice", "prompt": "本を読んで（　）。", "task": "", "answer": "います",
      "choices": ["います", "でした"]},
     "0db15459e9cd7ec721a86f30055f832b0f6b2a87532742f38163d417a682ae86"),
    ({"kind": "vocab_context_choice", "prompt": "（　）を買います。", "task": "文脈で選ぶ", "answer": "切符",
      "choices": ["切符", "切手"]},
     "2e8e2c740c9572b40cb9fa22f30c9e0ea9426eb8b4cbbbba9cea1ba0af47bb34"),
    ({"kind": "vocab_audio_word", "prompt": "音声を聞いて、単語を選んでください。", "task": "聞いて、単語を選ぶ",
      "answer": "切符", "choices": ["切符", "切手", "雑誌", "火"], "audio_text": "きっぷ"},
     "3c65894a2ac46893c5708c4937cd3fa6b50a71f0dabdca21bc05e7b22ff1cbe9"),
    ({"kind": "vocab_reading_choice", "prompt": "切符", "task": "読み方", "answer": "きっぷ",
      "choices": ["きっぷ", "きぷ", "ぎっぷ", "きっふ"]},
     "255963296f5d343f931f11e97c4889278f405c82f049d171bcf233cf984040e4"),
    ({"prompt": "x", "answer": "y", "audio_text": "z"},
     "9a4718e008391506feb232c6db95effed7a1579c8aefa32cfea69438d7f1a967"),
    # A listening question without audio text (an old or projected snapshot) keeps its former digest too.
    ({"kind": HEARD, "prompt": "音声", "task": "听音频，选择意思。", "answer": "票", "choices": ["票", "邮票"]},
     "6203618ab35f5473fe4f61d81a36cf2c21d3b5a33a19ce5525c16f71b942a0dc"),
]


def heard(audio, **extra):
    return {"prompt": "音声", "task": "听音频，选择意思。", "answer": "票", "choices": ["票", "邮票"],
            "audio_text": audio, "feedback": "说明", **extra}


def heard_lesson(*audio, kind=HEARD):
    return {"id": "word:policy-heard", "revision": "1", "family": "word", "title": "切符", "reading": "きっぷ",
            "meaning": "票", "abilities": ["listening"], "instruction": {"rule": "名词。", "example": "切符"},
            "question_templates": {kind: [heard(text) for text in audio]}}


@pytest.fixture
def no_word_pool(monkeypatch):
    """Keep the generated sound kinds out: the lesson has only its authored listening templates."""
    from kanamemory import learning_questions
    monkeypatch.setattr(learning_questions, "_word_pool", lambda: ())


@pytest.mark.parametrize("question, digest", FORMER_FINGERPRINTS, ids=lambda value: None)
def test_fingerprints_without_a_heard_stimulus_keep_their_former_digests(question, digest):
    assert _fingerprint(question) == digest


@pytest.mark.parametrize("kind, former", [
    (HEARD, "6203618ab35f5473fe4f61d81a36cf2c21d3b5a33a19ce5525c16f71b942a0dc"),
    ("listening_comprehension", "590aa0d16343d57979085b4448a3b68b171876f28244ea72ab9d3f67b32c4957")])
def test_heard_stimulus_fingerprint_includes_the_normalized_audio_text(kind, former):
    first, second = (_fingerprint(dict(heard(text), kind=kind)) for text in ("きっぷ", "切符を 買います。"))
    assert len({first, second, former}) == 3
    assert _fingerprint(dict(heard(" きっ ぷ\n"), kind=kind)) == first  # normalized like every other field
    # Choice order and options are not the stimulus of these kinds.
    assert _fingerprint(dict(heard("きっぷ"), kind=kind, choices=["邮票", "票", "车"])) == first


@pytest.mark.parametrize("kind", [HEARD, "listening_comprehension"])
def test_templates_differing_only_in_audio_text_are_separate_candidates(no_word_pool, kind):
    item = heard_lesson("きっぷ", "きって", "きっぷ", kind=kind)
    candidates = list(practice_candidates(item, [kind]))
    # The third template repeats the first one's audio text: still one stimulus.
    assert [c["variant"] for c in candidates] == [0, 1]
    assert len({c["fingerprint"] for c in candidates}) == 2
    for candidate in candidates:
        question = build_learning_question(item, kind, variant=candidate["variant"])
        assert question["audio_text"] and _fingerprint(question) == candidate["fingerprint"]


def test_an_answered_listening_task_is_recognised_by_its_audio_text(no_word_pool):
    item = heard_lesson("きっぷ", "きって")

    def answered(variant):
        snapshot = build_learning_question(item, HEARD, variant=variant)
        return {"question": snapshot, "correct": True, "assisted": False, "outcome": "correct",
                "scheduled": True, "role": "primary"}
    state = {"attempt_count": 0}  # No rotation: only the recent stimulus decides.
    assert select_practice_plan(item, "listening", [HEARD], state, [])["variant"] == 0
    assert select_practice_plan(item, "listening", [HEARD], state, [answered(0)])["variant"] == 1
    assert select_practice_plan(item, "listening", [HEARD], state, [answered(1)])["variant"] == 0
