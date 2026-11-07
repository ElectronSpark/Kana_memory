"""Reviewed pitch models, reversible input and existing review/backup contracts."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from kanamemory import pitch_practice as pitch
from kanamemory.learning_dependencies import has_started_answer
from kanamemory.learning_questions import build_learning_question, grade_learning_answer
from kanamemory.learning_store import LearningStore, validate_backup_rows
from kanamemory.question_support import prompt_audio_text, prompt_support
from support.learning_store_fixtures import START, stores, ready, legacy_dump


from support.pitch_practice_support import template, item, question, perform


def test_presegmented_input_keeps_small_kana_one_mora_and_particle_separate():
    q = question()
    initial = pitch.initial_draft(q)
    assert not has_started_answer(initial)
    assert pitch.state_view(q, initial)["levels"] == [None, None, None]
    first = perform(q, ["H"])
    view = pitch.state_view(q, first)
    assert has_started_answer(first)
    assert view["morae"] == ["きょ", "う", "が"]
    assert view["levels"] == ["H", None, None]
    assert view["next_text"] == "うが" and not view["complete"]
    assert pitch.grade_answer(q, first["answer"])["status"] == "empty"
    complete = perform(q, ["H", "L", "L"])
    assert complete["answer"] == q["answer"]
    assert pitch.grade_answer(q, complete["answer"])["correct"]
    assert pitch.state_view(q, complete)["word_mora_count"] == 2
    assert pitch.apply_action(q, complete, "undo") == perform(q, ["H", "L"])
    assert pitch.apply_action(q, complete, "clear") == initial


def test_advanced_merge_inherits_pitch_and_undo_reverses_every_action():
    q = question(learner=True, segmentation=True)
    first = perform(q, ["H"])
    assert pitch.state_view(q, first)["can_merge"]
    joined = perform(q, ["merge"], first)
    assert pitch.state_view(q, joined)["morae"] == ["きょ"]
    assert pitch.state_view(q, joined)["levels"] == ["H"]
    assert pitch.apply_action(q, joined, "undo") == first
    word = perform(q, ["H", "merge", "L"])
    split = perform(q, ["split"], word)
    assert pitch.state_view(q, split)["cursor"] == pitch.state_view(q, word)["cursor"]
    assert pitch.apply_action(q, split, "undo") == word
    full = perform(q, ["L"], split)
    assert pitch.grade_answer(q, full["answer"])["correct"]
    wrong = perform(q, ["H", "L", "L", "split", "L"])
    verdict = pitch.grade_answer(q, wrong["answer"])
    assert verdict["status"] == "incorrect" and "morae" in verdict["pitch_errors"]
    # The wrong split remains visibly wrong instead of being autocorrected.
    assert pitch.state_view(q, wrong)["morae"] == ["き", "ょ", "う", "が"]


@pytest.mark.parametrize("morae,actions", [(["き", "っ", "て"], ["H"]),
    (["ほ", "ん"], ["H"]), (["ケ", "ー", "キ"], ["H"]),
    (["と", "う"], ["H"]), (["きょ", "う"], ["H", "merge", "L"])])
def test_only_an_explicitly_authored_small_kana_join_is_available(morae, actions):
    q = question(learner=True, morae=morae)
    draft = perform(q, actions)
    assert not pitch.state_view(q, draft)["can_merge"]
    with pytest.raises(ValueError):
        pitch.apply_action(q, draft, "merge")


def test_no_segmentation_button_action_when_disabled_and_flags_survive_editing():
    q = question()
    draft = {**pitch.initial_draft(q), "assisted": True, "presented": True, "custom": {"a": 1}}
    result = perform(q, ["H"], draft)
    assert all(result[key] == draft[key] for key in ("assisted", "presented", "custom"))
    assert not pitch.state_view(q, result)["can_split"]
    with pytest.raises(ValueError):
        pitch.apply_action(q, result, "split")
    assert draft["pitch_state"]["actions"] == []


def test_two_selections_are_one_complete_answer_and_particle_is_not_counted():
    q = question(mode="pitch_identify")
    draft = pitch.set_selection(q, pitch.initial_draft(q), "mora_count", 2)
    assert has_started_answer(draft)
    assert pitch.grade_answer(q, draft["answer"])["status"] == "empty"
    draft = pitch.set_selection(q, draft, "accent", 1)
    assert draft["answer"] == q["answer"]
    assert pitch.grade_answer(q, draft["answer"])["correct"]
    incorrect = pitch.set_selection(q, draft, "mora_count", 3)
    assert pitch.grade_answer(q, incorrect["answer"])["pitch_errors"] == ["mora_count"]
    view = pitch.state_view(q, draft)
    assert 2 in view["mora_options"] and 1 in view["accent_options"]
    assert "number" not in view["display"]


@pytest.mark.parametrize("accent", [0, 2])
def test_identification_rejects_ambiguous_flat_or_final_without_particle(accent):
    with pytest.raises(ValueError, match="following particle"):
        template("pitch_identify", particle="", accent=accent)
    # Fill tasks state the given number, so word-internal modelling is valid.
    q = question(particle="", accent=accent)
    assert pitch.grade_answer(q, q["answer"])["correct"]


@pytest.mark.parametrize("change", [
    {"morae": ["き", "ょう"]}, {"morae": ["きょう"]}, {"morae": ["きょ", "ぅ"]},
    {"accent": True}, {"accent": -1}, {"accent": 3}, {"version": 2},
    {"allow_segmentation": 1}, {"particle": "が", "particle_morae": []},
])
def test_malformed_authored_models_are_rejected(change):
    raw = template()
    raw["pitch_practice"].update(change)
    with pytest.raises(ValueError):
        pitch.canonical_answer(raw)


def test_builder_keeps_same_units_and_pitch_guidance_without_machine_code():
    lesson = item()
    guided = build_learning_question(lesson, "pronunciation_application", guided=True)
    assert guided["input_mode"] == "pitch_fill"
    assert guided["guided"] and guided["evidence_mode"] == "exposure_only"
    assert pitch.PREFIX not in guided["guide"]
    assert "きょ 高" in guided["guide"]
    for kind, ability in (("pronunciation_rule", "understanding"), ("pronunciation_application", "application")):
        q = build_learning_question(lesson, kind)
        assert q["target_id"] == lesson["id"] and q["ability"] == ability
        assert grade_learning_answer(q, q["answer"])["correct"]
        assert not q["choices"] and q["tests_pitch"]
        assert prompt_audio_text(q) is None
        assert not prompt_support(q)["accent_visible"]
        assert not prompt_support(q)["furigana_visible"]
        assert not q["audio_text"]


def test_display_never_fills_in_solution_or_implicit_accent_number():
    q = question()
    assert pitch.display_data(q)["levels"] == [None, None, None]
    partial = perform(q, ["L"])
    shown = pitch.display_data(q, partial["answer"])
    assert shown["levels"] == ["L", None, None]  # Even when first pitch is wrong.
    assert "number" not in shown
    assert pitch.display_data(q, reveal=True)["levels"] == ["H", "L", "L"]
    assert pitch.display_data(q, reveal=True)["number"] == 1


def test_snapshot_and_draft_validation_reject_unbound_or_modified_state():
    q = question()
    draft = perform(q, ["H"])
    for bad in ({**draft, "answer": q["answer"]},
                {"answer": q["answer"]},
                {**draft, "pitch_state": {"version": 1, "actions": ["unknown"]}}):
        with pytest.raises(ValueError):
            pitch.validate_draft(q, bad)
    altered = deepcopy(q)
    altered["pitch_practice"]["accent"] = 0
    with pytest.raises(ValueError):
        pitch.validate_snapshot(altered)
    assert pitch.grade_answer(q, "pp1:{}")["status"] == "invalid"
    assert grade_learning_answer(q, "")["status"] == "empty"


def test_trusted_evidence_compares_full_pitch_metadata(monkeypatch):
    from kanamemory import learning_content, learning_evidence
    lesson = item()
    monkeypatch.setattr(learning_content, "get_learning_item", lambda cid: deepcopy(lesson))
    q = build_learning_question(lesson, "pronunciation_application")
    assert learning_evidence.reviewed_question(q)
    q["pitch_practice"]["word"] = "違う言葉"
    assert not learning_evidence.reviewed_question(q)


def test_review_draft_restart_backup_and_only_one_formal_review(stores, monkeypatch):
    store, learning = stores
    lesson, q = item(), question()
    learning.enroll(lesson, now=START)
    learning.append_questions(q, now=START)
    when = ready(learning, lesson["id"])
    unchanged = legacy_dump(store)
    draft = perform(q, ["H"])
    learning.save_draft(q["id"], draft)
    assert LearningStore(store).get_question(q["id"])["draft"] == draft
    with pytest.raises(ValueError, match="完成"):
        learning.submit(q["id"], draft["answer"], True, now=when)
    assert learning.get_question(q["id"])["result"] is None
    assert learning.history() == []
    # Repeated current-format restoration must keep the same partial state.
    for _ in range(2):
        store.import_backup(store.export_backup())
        assert LearningStore(store).get_question(q["id"])["draft"] == draft
    other_before = learning.get_unit(lesson["id"], "understanding")
    completed = perform(q, ["L", "L"], draft)
    learning.save_draft(q["id"], completed)
    result = learning.submit(q["id"], completed["answer"], False, now=when)
    assert result["correct"] is True  # Caller cannot forge the grade.
    assert learning.submit(q["id"], "pp1:{}", False, now=when) == result
    event = learning.finalize(q["id"], 3, now=when)
    assert learning.finalize(q["id"], 1, now=when + timedelta(days=1)) == event
    assert len(learning.history()) == 1
    assert learning.get_unit(lesson["id"], "understanding") == other_before
    assert legacy_dump(store) == unchanged
    validate_backup_rows(store._db)


def test_guide_partial_freezes_its_actual_question_across_restart_and_content_change(stores):
    store, learning = stores
    lesson = item()
    learning.enroll(lesson, now=START)
    q = build_learning_question(lesson, "pronunciation_application", guided=True, variant=1)
    draft = perform(q, ["H"])
    learning.advance_instruction(lesson["id"], "guide", state={"guide_draft": draft, "guide_question": q}, now=START)
    current = LearningStore(store).get_item(lesson["id"])
    current["question_templates"]["pronunciation_application"] = [template(morae=["は", "し"], word="箸")]
    assert learning.guided_question(current, "pronunciation_rule") == q
    assert learning.history() == []
    store.import_backup(store.export_backup())
    assert LearningStore(store).get_item(lesson["id"])["checkpoint"]["guide_draft"] == draft
    assert learning.guided_question(learning.get_item(lesson["id"]), "pronunciation_application") == q


def test_malformed_pitch_checkpoint_or_import_is_rejected_atomically(stores):
    store, learning = stores
    lesson, q = item(), question()
    learning.enroll(lesson, now=START)
    learning.append_questions(q, now=START)
    before = learning.get_question(q["id"])
    with pytest.raises(ValueError):
        learning.save_draft(q["id"], {"answer": q["answer"]})
    assert learning.get_question(q["id"]) == before
    guide = build_learning_question(lesson, "pronunciation_application", guided=True)
    bad = perform(guide, ["H"])
    bad["answer"] = guide["answer"]
    with pytest.raises(ValueError):
        learning.advance_instruction(lesson["id"], "guide", state={"guide_question": guide, "guide_draft": bad}, now=START)
    assert learning.get_item(lesson["id"])["stage"] == "intro"
    # Validate an isolated restore candidate, never a real user's database.
    with store._db:
        store._db.execute("UPDATE learning_question SET draft_json=? WHERE question_id=?",
                          (json.dumps(bad, ensure_ascii=False), q["id"]))
    with pytest.raises(ValueError):
        validate_backup_rows(store._db)


def test_legacy_answer_only_guide_keeps_original_rule_when_ui_now_prefers_application(stores):
    _, learning = stores
    lesson = item()
    old = {"input_mode": "choice", "prompt": "旧讲解例子", "answer": "旧规则", "choices": ["旧规则", "其他"],
           "feedback": "原说明", "text_roles": {"prompt": "ui", "answer": "ui"}}
    lesson["question_templates"]["pronunciation_rule"] = [old]
    learning.enroll(lesson, now=START)
    original = build_learning_question(lesson, "pronunciation_rule", guided=True, variant=1)
    learning.advance_instruction(lesson["id"], "guide", now=START,
        state={"guide_answer": original["answer"], "guide_correct": True})
    restored = learning.guided_question(learning.get_item(lesson["id"]), "pronunciation_application")
    assert restored == original


def test_error_explanation_starts_at_mora_alignment_not_multiple_shifted_pitch_errors():
    q = question(learner=True)
    wrong = perform(q, ["L", "H", "L", "H"])
    verdict = pitch.grade_answer(q, wrong["answer"])
    assert verdict["pitch_errors"] == ["morae"]
    assert "きょ／う" in verdict["message"] and "2 拍" in verdict["message"]
    q = question()
    wrong = perform(q, ["H", "L", "H"])
    verdict = pitch.grade_answer(q, wrong["answer"])
    assert verdict["pitch_errors"] == ["levels"] and "助词「が」应标低" in verdict["message"]
