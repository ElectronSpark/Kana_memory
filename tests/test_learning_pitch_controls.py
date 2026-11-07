"""Actual lesson controller, persistent drafts and grading for pitch inputs."""
from copy import deepcopy
from datetime import timedelta
import asyncio
from types import SimpleNamespace

import pytest

from support.app_fixtures import app_module, question_app, setup_app
from support.fake_ui import descendants, text_of
from support.learning_fixtures import learning_app, NOW, click, legacy_dump
from kanamemory.learning_store import LearningStore
from kanamemory.learning_questions import build_learning_question
from kanamemory.learning_ui import LEARNING_PREFS
from kanamemory.pitch_practice import canonical_answer
from kanamemory import pitch_practice as pitch


from support.learning_pitch_controls_support import synthetic_item, prepare, diagrams, button


def test_fill_updates_visible_kana_and_backspace_without_creating_review(learning_app):
    app = learning_app
    _, q = prepare(app)
    before = legacy_dump(app.store)
    assert diagrams(app)[0]._feedback_fragment["levels"] == [None, None, None, None]
    characters = diagrams(app)[0]._feedback_fragment["characters"]
    assert not button(app, "提交").enabled
    click(app, "▔")
    assert diagrams(app)[0]._feedback_fragment["levels"] == ["H", "H", None, None]
    assert diagrams(app)[0]._feedback_fragment["characters"] == characters
    saved = app.learning_store.get_question(q["id"])
    assert saved["draft"]["pitch_state"]["actions"] == ["H"]
    app.learning_store = LearningStore(app.store)
    app.show_learning_question({"question": app.learning_store.get_question(q["id"]), "draft": saved["draft"]})
    assert diagrams(app)[0]._feedback_fragment["levels"] == ["H", "H", None, None]
    click(app, "⌫")
    assert diagrams(app)[0]._feedback_fragment["levels"] == [None, None, None, None]
    assert diagrams(app)[0]._feedback_fragment["characters"] == characters
    assert not app.learning_store.history()
    assert legacy_dump(app.store) == before
    app.submit_learning_answer(app._learning_question_state["answer"])
    assert app.page == "learning_question"


def test_native_pitch_update_reuses_the_visible_diagram(learning_app):
    app = learning_app
    _, q = prepare(app)
    diagram = diagrams(app)[0]
    updates = []

    def update(**data):
        updates.append(deepcopy(data))
        return True

    diagram._update_pitch = update
    click(app, "▔")
    click(app, "⌫")
    assert diagrams(app) == [diagram]
    assert updates[0]["levels"] == ["H", "H", None, None]
    assert updates[1]["levels"] == [None] * 4
    assert app.learning_store.get_question(q["id"])["draft"]["pitch_state"]["actions"] == []
    assert not app.learning_store.history()


def test_saved_pitch_edit_survives_render_failure_without_claiming_unsaved(learning_app):
    app = learning_app
    _, question = prepare(app)
    def fail(**data):
        raise RuntimeError('native render')
    diagrams(app)[0]._update_pitch = fail
    click(app, '▔')
    saved = app.learning_store.get_question(question['id'])
    assert saved['draft']['pitch_state']['actions'] == ['H']
    assert '已保存' in text_of(app.body) and not app.learning_store.history()
    app.show_learning_question({'question': saved, 'draft': saved['draft']})
    assert diagrams(app)[0]._feedback_fragment['levels'] == ['H', 'H', None, None]


def test_incompatible_pitch_update_rebuilds_the_diagram(learning_app):
    app = learning_app
    prepare(app)
    original = diagrams(app)[0]
    original._update_pitch = lambda **data: False
    click(app, "▔")
    assert diagrams(app)[0] is not original
    assert diagrams(app)[0]._feedback_fragment["levels"] == ["H", "H", None, None]


def test_small_kana_join_undo_and_given_boundary_are_reversible(learning_app):
    app = learning_app
    _, q = prepare(app, learner_morae=True, segmentation=True)
    assert "‿" in text_of(app.body) and "分词" not in text_of(app.body)
    assert "│" not in text_of(app.body)
    characters = diagrams(app)[0]._feedback_fragment["characters"]
    click(app, "▔")
    assert button(app, "‿").enabled
    click(app, "‿")
    assert diagrams(app)[0]._feedback_fragment["groups"] == [{"start": 0, "end": 2}]
    assert diagrams(app)[0]._feedback_fragment["characters"] == characters
    click(app, "⌫")
    assert diagrams(app)[0]._feedback_fragment["groups"] == [{"start": 0, "end": 1}]
    assert diagrams(app)[0]._feedback_fragment["characters"] == characters
    click(app, "‿")
    click(app, "▁")
    click(app, "▁")
    assert button(app, "提交").enabled
    click(app, "提交")
    assert app.page == "learning_feedback"
    assert "pp1:" not in text_of(app.body)
    assert "记住了" in text_of(app.body)
    click(app, "记住了")
    assert len(app.learning_store.history()) == 1
    app.learning_store.finalize(q["id"], 3)
    assert len(app.learning_store.history()) == 1


def test_wrong_mora_split_is_visible_and_feedback_is_not_machine_json(learning_app):
    app = learning_app
    _, q = prepare(app, learner_morae=True)
    for caption in ("▔", "▁", "▁", "▁"):
        click(app, caption)
    assert diagrams(app)[0]._feedback_fragment["characters"] == list("きょうが")
    assert len(diagrams(app)[0]._feedback_fragment["groups"]) == 4
    click(app, "提交")
    rendered = text_of(app.body)
    assert "pp1:" not in rendered and "我的回答" in rendered
    assert "保存并继续" in rendered and "记住了" not in rendered
    assert len(diagrams(app)[0]._feedback_fragment["groups"]) == 3
    assert app._learning_answer_audio_text(q) == ""


def test_identify_hides_number_and_requires_two_independent_choices(learning_app):
    app = learning_app
    _, q = prepare(app, mode="pitch_identify")
    chart = diagrams(app)[0]._feedback_fragment
    assert chart["number"] is None and chart["show_counts"] is False
    pickers = [node for node in descendants(app.body) if getattr(node, "on_change", None) and getattr(node, "items", None)]
    assert len(pickers) == 2
    pickers[0].value = "2 拍"
    pickers[0].on_change(pickers[0])
    assert not button(app, "提交").enabled
    pickers[1].value = "1 型"
    pickers[1].on_change(pickers[1])
    assert button(app, "提交").enabled
    click(app, "提交")
    assert "记住了" in text_of(app.body)
    assert diagrams(app)[0]._feedback_fragment["show_counts"] is True
    assert diagrams(app)[0]._feedback_fragment["groups"] == [
        {"start": 0, "end": 2}, {"start": 2, "end": 3}, {"start": 3, "end": 4}]


def test_reveal_partial_pitch_is_assisted_and_has_no_encoded_answer_in_ui(learning_app):
    app = learning_app
    item, q = prepare(app)
    click(app, "▔")
    click(app, "显示答案")
    rendered = text_of(app.body)
    assert "pp1:" not in rendered and "记住了" not in rendered
    assert "保存并继续" in rendered
    result = app.learning_store.get_question(q["id"])["result"]
    assert result["assisted"] is True
    assert not app.learning_store.get_unit(item["id"], "understanding")["review_count"]


def test_pitch_guide_draft_survives_reopen_and_remains_exposure_only(learning_app):
    app = learning_app
    item = synthetic_item()
    app.learning_store.enroll(item)
    app.learning_store.advance_instruction(item["id"], "intro_done")
    app.show_learning_instruction(app.learning_store.get_item(item["id"]))
    assert app.page == "learning_guide"
    assert app._learning_guide["input_mode"] == "pitch_fill"
    click(app, "▔")
    original_id = app._learning_guide["id"]
    app.learning_store = LearningStore(app.store)
    app.show_learning_instruction(app.learning_store.get_item(item["id"]))
    assert app._learning_guide["id"] == original_id
    assert diagrams(app)[-1]._feedback_fragment["levels"] == ["H", "H", None, None]
    click(app, "▁")
    click(app, "▁")
    click(app, "提交")
    assert "继续" in text_of(app._learning_guide_result)
    assert not app.learning_store.history()
    assert not any(unit["review_count"] for unit in app.learning_store.get_item(item["id"])["units"])


def legacy_completed_boundary_draft(q):
    """Historically valid input: correct mora/pitch, misplaced manual split."""
    draft = {**pitch.initial_draft(q), "assisted": False, "custom_note": "保留"}
    for action in ["H", "merge", "split", "L", "L"]:
        draft = pitch.apply_action(q, draft, action)
    assert pitch.grade_answer(q, draft["answer"])["pitch_errors"] == ["boundaries"]
    return draft


def test_legacy_completed_guide_submission_saves_normalized_answer_and_draft_together(learning_app):
    app = learning_app
    item = synthetic_item(learner_morae=True, segmentation=True)
    app.learning_store.enroll(item)
    app.learning_store.advance_instruction(item["id"], "intro_done")
    q = build_learning_question(item, "pronunciation_application", guided=True, variant=1)
    old = legacy_completed_boundary_draft(q)
    app.learning_store.advance_instruction(item["id"], "guide",
        state={"guide_question": q, "guide_draft": old})
    before = app.learning_store.get_item(item["id"])["checkpoint"]
    app.show_learning_instruction(app.learning_store.get_item(item["id"]))
    # Opening the new editor alone must not migrate the stored old draft.
    assert app.learning_store.get_item(item["id"])["checkpoint"] == before
    assert app._learning_pitch_context["draft"]["answer"] == q["answer"]
    click(app, "提交")
    saved = app.learning_store.get_item(item["id"])["checkpoint"]
    assert saved["guide_answer"] == saved["guide_draft"]["answer"] == q["answer"]
    assert saved["guide_correct"] is True and saved["guide_question"] == q
    assert saved["guide_draft"]["custom_note"] == "保留"
    assert saved["guide_draft"]["pitch_state"]["actions"] == ["H", "merge", "L", "split", "L"]
    assert not app.learning_store.history()
    app.learning_store = LearningStore(app.store)
    app.show_learning_instruction(app.learning_store.get_item(item["id"]))
    assert not button(app, "提交").enabled
    assert app._learning_pitch_context["draft"]["answer"] == saved["guide_answer"]
    assert not any(unit["review_count"] for unit in app.learning_store.get_item(item["id"])["units"])


def test_legacy_completed_reveal_saves_normalized_draft_and_keeps_assisted(learning_app):
    app = learning_app
    item, q = prepare(app, learner_morae=True, segmentation=True)
    old = legacy_completed_boundary_draft(q)
    app.learning_store.save_draft(q["id"], old)
    saved_before = deepcopy(app.learning_store.get_question(q["id"]))
    unit_before = app.learning_store.get_unit(item["id"], "application")
    app.show_learning_question({"question": q, "draft": saved_before["draft"]})
    assert app.learning_store.get_question(q["id"]) == saved_before
    click(app, "显示答案")
    saved = app.learning_store.get_question(q["id"])
    assert saved["draft"]["answer"] == saved["result"]["answered_text"] == q["answer"]
    assert saved["draft"]["assisted"] is True and saved["result"]["assisted"] is True
    assert saved["draft"]["custom_note"] == "保留"
    assert saved["result"]["outcome"] == "revealed"
    assert app.page == "learning_feedback" and "记住了" not in text_of(app.body)
    assert not app.learning_store.history()
    assert app.learning_store.get_unit(item["id"], "application") == unit_before


def test_legacy_completed_hint_return_keeps_normalized_draft_without_duplicate_review(learning_app):
    app = learning_app
    item, q = prepare(app, learner_morae=True, segmentation=True)
    old = legacy_completed_boundary_draft(q)
    app.learning_store.save_draft(q["id"], old)
    draft = app.learning_store.get_question(q["id"])["draft"]
    unit_before = app.learning_store.get_unit(item["id"], "application")
    app.show_learning_question({"question": q, "draft": draft})
    click(app, "再看讲解")
    saved = deepcopy(app.learning_store.get_question(q["id"]))
    assert app.page == "learning_prerequisite"
    assert saved["draft"]["answer"] == q["answer"] and saved["draft"]["assisted"] is True
    assert saved["draft"]["custom_note"] == "保留" and saved["result"] is None
    click(app, "返回题目")
    assert app.page == "learning_question"
    assert app._learning_pitch_context["draft"]["answer"] == q["answer"]
    assert app.learning_store.get_question(q["id"]) == saved
    assert app.learning_store.get_unit(item["id"], "application") == unit_before
    assert not app.learning_store.history()
    click(app, "提交")
    result = app.learning_store.get_question(q["id"])["result"]
    assert result["correct"] is True and result["assisted"] is True
    assert "记住了" not in text_of(app.body)
    click(app, "保存并继续")
    assert len(app.learning_store.history()) == 1
    app.learning_store.finalize(q["id"], 1)
    assert len(app.learning_store.history()) == 1


def pending_guide_scroll(app):
    app.page = "learning_guide"
    app._learning_guide = {"id": "synthetic-scroll"}
    app._learning_guide_result = SimpleNamespace(layout=SimpleNamespace(absolute_content_top=900))
    scroll = SimpleNamespace(content=SimpleNamespace(layout=SimpleNamespace(absolute_content_top=40)),
                             vertical_position=0)
    app._page_scroll = scroll
    tasks = []
    app.loop = SimpleNamespace(create_task=tasks.append)
    app._reveal_learning_guide_result()
    assert len(tasks) == 1
    return scroll, tasks[0]


def test_guide_result_scroll_uses_layout_after_wrap_settles(learning_app):
    app = learning_app
    scroll, task = pending_guide_scroll(app)
    app._learning_guide_result.layout.absolute_content_top = 1320
    asyncio.run(task)
    assert scroll.vertical_position == 1272
    assert not app.learning_store.history()


@pytest.mark.parametrize("changed", ["page", "question", "result", "scroll"])
def test_delayed_guide_scroll_never_moves_a_different_page(learning_app, changed):
    app = learning_app
    scroll, task = pending_guide_scroll(app)
    if changed == "page":
        app.page = "home"
    elif changed == "question":
        app._learning_guide = {"id": "other-question"}
    elif changed == "result":
        app._learning_guide_result = SimpleNamespace()
    else:
        app._page_scroll = SimpleNamespace()
    asyncio.run(task)
    assert scroll.vertical_position == 0
