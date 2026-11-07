"""Original synthetic sentence contours; never asserted as Japanese evidence."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from kanamemory import pitch_practice as pitch, pitch_sequence as sequence
from kanamemory.learning_questions import build_learning_question, grade_learning_answer
from kanamemory.learning_store import LearningStore, validate_backup_rows
from kanamemory.question_support import prompt_audio_text, prompt_support
from support.learning_store_fixtures import START, stores, ready, legacy_dump


from support.pitch_sequence_support import synthetic_spec, registry, register, lesson, CORRECT


def question(registry, **kwargs):
    item = lesson(registry, **kwargs)
    return build_learning_question(item, next(iter(item["question_templates"])))


def perform(q, actions, draft=None):
    current = pitch.initial_draft(q) if draft is None else draft
    for action in actions:
        current = pitch.editor_action(q, current, action)
    return current


def test_full_sentence_with_separate_source_kana_mora_offsets_and_atomic_edits(registry):
    q = question(registry)
    initial = pitch.initial_draft(q)
    partial = perform(q, ["L", "merge", "H"])
    view = pitch.state_view(q, partial)
    assert view["cursor"] == 4 and view["remaining"] == "ねこがいる。"
    assert grade_learning_answer(q, partial["answer"])["status"] == "empty"
    before = deepcopy(q)
    complete = perform(q, CORRECT)
    assert pitch.state_view(q, complete)["complete"]
    assert complete["answer"] == q["answer"]
    assert pitch.grade_answer(q, complete["answer"])["correct"]
    for _ in CORRECT:
        complete = pitch.editor_action(q, complete, "undo")
    assert complete == initial and q == before


def test_split_changes_only_color_spans_not_characters_public_layout_or_pitch(registry):
    q = question(registry)
    initial = pitch.fixed_view(q, pitch.initial_draft(q))
    assert [t["start"] for t in initial["tokens"]] == [0, 3, 4, 9]
    before = perform(q, CORRECT[:5])
    after = pitch.editor_action(q, before, "split")
    a, b = pitch.fixed_view(q, before), pitch.fixed_view(q, after)
    assert all(a[key] == b[key] for key in ("characters", "groups", "levels", "wrap_units", "cursor", "active_span"))
    assert any(token["start"] == 6 for token in b["tokens"])
    assert pitch.editor_action(q, after, "undo") == before
    assert initial["levels"] == [None] * 10 and "number" not in initial
    assert all(b["levels"][index] is None for index in (3, 6, 7, 8, 9))
    assert not prompt_audio_text(q) and not prompt_support(q).get("safe")


def test_paired_alternative_patterns_accepted_but_unreviewed_cross_product_rejected(registry):
    q = question(registry)
    alternate = ["H", "merge", "L", "H", "L", "L", "split", "H", "L"]
    verdict = pitch.grade_answer(q, perform(q, alternate)["answer"])
    assert verdict["correct"] and verdict["matched_pattern_id"] == "pattern:b"
    mixed = list(CORRECT); mixed[0], mixed[2] = "H", "L"
    verdict = pitch.grade_answer(q, perform(q, mixed)["answer"])
    assert verdict["correct"] is False and verdict["pitch_errors"] == ["levels"]


def test_grouping_before_boundaries_before_pitch_diagnosis(registry):
    q = question(registry)
    wrong_group = perform(q, ["H", "L", "H", "L", "H", "L", "H", "L"])
    assert pitch.grade_answer(q, wrong_group["answer"])["pitch_errors"] == ["morae"]
    wrong_boundary = perform(q, ["L", "merge", "H", "H", "split", "L", "L", "H", "L"])
    assert pitch.grade_answer(q, wrong_boundary["answer"])["pitch_errors"] == ["boundaries"]
    wrong_level = list(CORRECT); wrong_level[-1] = "H"
    assert pitch.grade_answer(q, perform(q, wrong_level)["answer"])["pitch_errors"] == ["levels"]


def test_pitch_only_understanding_mode_has_given_boundaries_and_no_fake_segmentation(registry):
    q = question(registry, spec=synthetic_spec(mode="pitch", learner=False), kind="pronunciation_rule")
    assert q["ability"] == "understanding"
    partial = perform(q, ["L"])
    assert pitch.state_view(q, partial)["cursor"] == 2
    assert not pitch.state_view(q, partial)["can_split"]
    with pytest.raises(ValueError):
        pitch.editor_action(q, partial, "split")
    complete = perform(q, ["L", "H", "H", "L", "L", "H", "L"])
    assert complete["answer"] == q["answer"] and pitch.grade_answer(q, complete["answer"])["correct"]


@pytest.mark.parametrize("field,value", [("version", 3), ("representation", "intramora_contour"),
    ("task_mode", "segmentation"), ("characters", list("今日")), ("mora_spans", [{"start": 0, "end": 10}]),
    ("wrap_units", [{"start": 1, "end": 10}]), ("accent_phrases", [{"id": "x", "start": 0, "end": 3}])])
def test_unrepresentable_or_malformed_spec_is_rejected_even_if_self_signed(registry, field, value):
    spec = synthetic_spec(); spec[field] = value
    register(registry, spec)
    with pytest.raises(ValueError):
        pitch.canonical_answer({"input_mode": "pitch_fill", "pitch_practice": spec})


def test_approved_flag_and_digest_do_not_replace_trusted_registration(registry):
    q = question(registry)
    registry["records"].clear()
    with pytest.raises(ValueError, match="trusted"):
        pitch.validate_snapshot(q)
    registry["records"].append(deepcopy(q["pitch_practice"]))
    registry["sources"].clear()
    with pytest.raises(ValueError, match="source"):
        pitch.validate_snapshot(q)


def test_unknown_context_and_different_context_alternative_are_rejected(registry):
    for unknown in (True, False):
        spec = synthetic_spec()
        spec["reviewed_patterns"][1]["context_id"] = "context:other"
        if not unknown:
            spec["contexts"].append({"id": "context:other", "prompt_zh": "另一语境", "source_refs": ["source:synthetic"]})
        register(registry, spec)
        with pytest.raises(ValueError, match="context"):
            pitch.canonical_answer({"input_mode": "pitch_fill", "pitch_practice": spec})


def test_old_reviewed_version_remains_usable_after_catalogue_adds_new_version(registry):
    q = question(registry)
    draft = perform(q, CORRECT[:4])
    newer = deepcopy(q["pitch_practice"])
    newer["reviewed_patterns"][0]["levels"][0] = "H"
    register(registry, newer)
    pitch.validate_snapshot(q)
    assert pitch.editor_draft(q, draft) == draft
    assert pitch.grade_answer(q, perform(q, CORRECT)["answer"])["correct"]
    changed = deepcopy(q); changed["pitch_practice"] = newer
    with pytest.raises(ValueError, match="different"):
        pitch.validate_draft(changed, draft)


def test_tampered_draft_or_snapshot_never_silently_repairs(registry):
    q = question(registry)
    for mutate in (lambda d: d.update(answer=q["answer"]),
                   lambda d: d["pitch_state"].update(context_id="other"),
                   lambda d: d["pitch_state"].update(actions=["split"]),
                   lambda d: d["pitch_state"].update(spec_sha256="0" * 64)):
        bad = pitch.initial_draft(q); mutate(bad)
        with pytest.raises(ValueError):
            pitch.validate_draft(q, bad)
    for key, value in (("audio_text", "朗读答案"), ("prompt", "不同内容"), ("ability", "understanding"),
                       ("task", "另一种没有审核的语境")):
        bad = deepcopy(q); bad[key] = value
        with pytest.raises(ValueError):
            pitch.validate_snapshot(bad)
    assert pitch.grade_answer(q, "pp2:{}")["status"] == "invalid"


@pytest.mark.parametrize("path,value", [(("selected_pattern_id",), []),
    (("tokens", 0, "role"), []), (("reviewed_patterns", 0, "context_id"), {}),
    (("review", "source_refs"), [None]), (("mora_spans", 0, "start"), True),
    (("lexical_accents",), [{"token_id": [], "mora_count": 1, "number": 0, "scope": "dictionary"}])])
def test_damaged_json_containers_raise_validation_error_not_runtime_type_error(registry, path, value):
    spec = synthetic_spec(); target = spec
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    register(registry, spec)
    with pytest.raises(ValueError):
        pitch.specification({"input_mode": "pitch_fill", "pitch_practice": spec})


@pytest.mark.parametrize("kind", ["pronunciation_application", "pronunciation_rule"])
def test_real_store_restart_current_backup_and_one_target_review(registry, stores, monkeypatch, kind):
    store, learning = stores
    item = lesson(registry, spec=synthetic_spec(mode="pitch" if kind == "pronunciation_rule" else "pitch_segmentation"), kind=kind)
    q = build_learning_question(item, kind)
    actions = CORRECT if kind == "pronunciation_application" else [action for action in CORRECT if action != "split"]
    learning.enroll(item, now=START); learning.append_questions(q, now=START)
    when = ready(learning, item["id"])
    legacy = legacy_dump(store)
    other_ability = "understanding" if kind == "pronunciation_application" else "application"
    other = learning.get_unit(item["id"], other_ability)
    partial = perform(q, actions[:4], {**pitch.initial_draft(q), "custom": {"note": "keep"}})
    learning.save_draft(q["id"], partial)
    assert LearningStore(store).get_question(q["id"])["draft"] == partial
    with pytest.raises(ValueError, match="整句"):
        learning.submit(q["id"], partial["answer"], True, now=when)
    assert learning.history() == []
    from kanamemory import learning_content
    monkeypatch.setattr(learning_content, "get_learning_item", lambda cid: deepcopy(item))
    for _ in range(2):
        store.import_backup(store.export_backup())
        assert LearningStore(store).get_question(q["id"])["draft"] == partial
    complete = perform(q, actions[4:], partial)
    learning.save_draft(q["id"], complete)
    result = learning.submit(q["id"], complete["answer"], False, now=when)
    assert result["correct"] is True
    assert learning.submit(q["id"], "wrong", False, now=when) == result
    event = learning.finalize(q["id"], 3, now=when)
    assert learning.finalize(q["id"], 1, now=when + timedelta(days=1)) == event
    assert len(learning.history()) == 1
    assert learning.get_unit(item["id"], other_ability) == other
    assert legacy_dump(store) == legacy
    assert store._db.execute("SELECT COUNT(*) FROM learning_evidence").fetchone()[0] == 1
    assert store._db.execute("SELECT COUNT(*) FROM learning_evidence WHERE eligible=1").fetchone()[0] == 1
    validate_backup_rows(store._db)


def test_guide_snapshot_draft_and_assistance_survive_new_registry_version(registry, stores):
    store, learning = stores
    item = lesson(registry)
    learning.enroll(item, now=START)
    q = build_learning_question(item, "pronunciation_application", guided=True)
    partial = perform(q, ["L", "merge"])
    partial["hint_used"] = True
    learning.advance_instruction(item["id"], "guide", state={"guide_question": q, "guide_draft": partial}, now=START)
    newer = deepcopy(q["pitch_practice"]); newer["reviewed_patterns"][0]["levels"][0] = "H"; register(registry, newer)
    current = LearningStore(store).get_item(item["id"])
    assert learning.guided_question(current, "pronunciation_rule") == q
    store.import_backup(store.export_backup())
    assert learning.get_item(item["id"])["checkpoint"]["guide_draft"] == partial
    assert not learning.history()


def test_unregistered_backup_rejected_without_altering_existing_rows(registry, stores):
    store, learning = stores
    item = lesson(registry); q = build_learning_question(item, "pronunciation_application")
    learning.enroll(item, now=START); learning.append_questions(q, now=START)
    partial = perform(q, ["L"]); learning.save_draft(q["id"], partial)
    payload = store.export_backup()
    before = [tuple(r) for r in store._db.execute("SELECT * FROM learning_question")]
    registry["records"].clear()
    with pytest.raises(ValueError):
        store.import_backup(payload)
    assert [tuple(r) for r in store._db.execute("SELECT * FROM learning_question")] == before
