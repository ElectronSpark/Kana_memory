"""Fixed kana coordinates and non-destructive automatic v1 boundaries."""
from copy import deepcopy
import json

import pytest

from kanamemory import pitch_practice as pitch
from kanamemory.learning_store import LearningStore, validate_backup_rows
from support.learning_store_fixtures import START, stores, ready
from support.pitch_practice_support import item
from support.pitch_practice_support import question
from support.pitch_practice_support import perform


def edit(q, actions, draft=None):
    draft = pitch.initial_draft(q) if draft is None else draft
    for action in actions:
        draft = pitch.editor_action(q, draft, action)
    return draft


@pytest.mark.parametrize("old_actions", [
    ["H"], ["H", "merge", "L"], ["H", "merge", "L", "split"],
    ["H", "merge", "split", "L"], ["H", "L", "split", "L", "L"],
])
def test_legacy_boundary_normalization_preserves_all_pitch_grouping_and_source(old_actions):
    q = question(learner=True, segmentation=True)
    old = {**perform(q, old_actions), "assisted": True, "extra": {"unchanged": 1}}
    q_before, old_before = deepcopy(q), deepcopy(old)
    current = pitch.editor_draft(q, old)
    assert q == q_before and old == old_before
    assert [a for a in current["pitch_state"]["actions"] if a != "split"] == [
        a for a in old_actions if a != "split"]
    assert current["assisted"] and current["extra"] == old["extra"]
    pitch.validate_draft(q, old)  # Both original and current are valid backups.
    pitch.validate_draft(q, current)
    assert pitch.editor_draft(q, current) == current
    assert not pitch.state_view(q, old)["can_split"]


def test_current_editor_never_requires_split_and_undo_skips_automatic_boundary():
    q = question(learner=True, segmentation=True)
    joined = edit(q, ["H", "merge"])
    word = edit(q, ["L"], joined)
    assert word["pitch_state"]["actions"] == ["H", "merge", "L", "split"]
    assert pitch.editor_action(q, word, "undo") == joined
    with pytest.raises(ValueError, match="无需分词"):
        pitch.editor_action(q, word, "split")
    complete = edit(q, ["L"], word)
    assert complete["answer"] == q["answer"]
    assert pitch.editor_action(q, complete, "undo") == word
    assert pitch.editor_action(q, word, "clear") == pitch.initial_draft(q)


def test_wrong_small_kana_split_and_wrong_pitch_are_not_repaired():
    q = question(learner=True, segmentation=True)
    wrong = edit(q, ["L", "H", "L", "H"])
    assert pitch.grade_answer(q, wrong["answer"])["pitch_errors"] == ["morae"]
    shown = pitch.fixed_view(q, wrong)
    assert shown["characters"] == list("きょうが")
    assert shown["groups"] == [{"start": i, "end": i + 1} for i in range(4)]
    assert shown["levels"] == ["L", "H", "L", "H"]


def test_characters_and_token_spans_stay_fixed_across_every_edit_and_undo():
    q = question(learner=True, segmentation=True)
    draft = pitch.initial_draft(q)
    original = pitch.fixed_view(q, draft)
    assert original["characters"] == list("きょうが")
    assert original["groups"] == [] and original["levels"] == [None] * 4
    assert original["active_span"] == {"start": 0, "end": 1}
    states = []
    for action in ["H", "merge", "L", "L", "undo", "undo", "undo"]:
        draft = pitch.editor_action(q, draft, action)
        view = pitch.fixed_view(q, draft)
        states.append(view)
        assert view["characters"] == original["characters"]
        assert view["tokens"] == original["tokens"]
    assert states[0]["levels"] == ["H", None, None, None]
    assert states[1]["levels"] == ["H", "H", None, None]
    assert states[1]["groups"] == [{"start": 0, "end": 2}]
    assert states[1]["active_span"] == {"start": 2, "end": 3}
    assert states[3]["complete"] and states[3]["active_span"] is None
    assert states[-1]["levels"] == states[0]["levels"]


def test_presegmented_and_identify_views_expose_only_their_intended_information():
    q = question()
    view = pitch.fixed_view(q, pitch.initial_draft(q))
    assert view["levels"] == [None] * 4
    assert view["groups"][0] == {"start": 0, "end": 2}
    assert view["active_span"] == {"start": 0, "end": 2}
    wrong = edit(q, ["L"])
    assert pitch.fixed_view(q, wrong)["levels"] == ["L", "L", None, None]
    assert "number" not in pitch.fixed_view(q, wrong)
    identify = question(mode="pitch_identify")
    assert pitch.fixed_view(identify)["levels"] == ["H", "H", "L", "L"]
    assert "number" not in pitch.fixed_view(identify)
    assert pitch.fixed_view(identify, reveal=True)["number"] == 1
    with pytest.raises(ValueError):
        pitch.fixed_view(q, wrong, answer=q["answer"])


def test_legacy_saved_draft_queue_due_and_frozen_result_survive_read_only_editor(stores, monkeypatch):
    store, learning = stores
    q = question(learner=True, segmentation=True)
    lesson = item()
    learning.enroll(lesson, now=START)
    learning.append_questions(q, now=START)
    when = ready(learning, lesson["id"])
    old = perform(q, ["H", "merge", "split", "L"])
    learning.save_draft(q["id"], old)
    before = learning.get_question(q["id"])
    unit_before = learning.get_unit(lesson["id"], "application")
    current = pitch.editor_draft(q, before["draft"])
    assert current != old
    assert LearningStore(store).get_question(q["id"]) == before
    assert learning.get_unit(lesson["id"], "application") == unit_before
    for _ in range(2):
        store.import_backup(store.export_backup())
        assert LearningStore(store).get_question(q["id"])["draft"] == old
    completed = edit(q, ["L"], current)
    learning.save_draft(q["id"], completed)
    result = learning.submit(q["id"], completed["answer"], False, now=when)
    assert result["correct"]
    frozen = deepcopy(learning.get_question(q["id"]))
    pitch.fixed_view(q, answer=result["answer"] if "answer" in result else completed["answer"])
    assert learning.get_question(q["id"]) == frozen
    event = learning.finalize(q["id"], 3, now=when)
    assert learning.finalize(q["id"], 1, now=when) == event
    assert len(learning.history()) == 1
    validate_backup_rows(store._db)


def test_legacy_wrong_frozen_boundaries_remain_inspectable_without_regrading():
    q = question(learner=True, segmentation=True)
    old = perform(q, ["H", "merge", "split", "L", "L"])
    answer = old["answer"]
    assert pitch.grade_answer(q, answer)["pitch_errors"] == ["boundaries"]
    pitch.fixed_view(q, answer=answer)
    assert old["answer"] == answer
    assert json.loads(answer[len(pitch.PREFIX):])["boundaries"] == [1]
    assert pitch.grade_answer(q, answer)["pitch_errors"] == ["boundaries"]


def test_explicit_wrong_grouping_is_not_hidden_even_in_a_presegmented_question():
    q = question()
    answer = pitch.PREFIX + json.dumps({"mode": "pitch_fill", "morae": ["き", "ょ", "う", "が"],
        "levels": ["H", "L", "L", "L"], "boundaries": [3]}, ensure_ascii=False)
    view = pitch.fixed_view(q, answer=answer)
    assert view["groups"] == [{"start": i, "end": i + 1} for i in range(4)]
    assert view["levels"] == ["H", "L", "L", "L"]


def test_unreviewed_sentence_v2_is_not_accepted_as_a_production_question():
    q = question()
    q["pitch_practice"]["version"] = 2
    with pytest.raises(ValueError, match="Unsupported"):
        pitch.canonical_answer(q)
