"""Guided reading converts only at submission, retaining resumable draft state.

Synthetic controls exercise the production answer controller and temporary
SQLite store. Native keyboard/layout behavior is a separate verification layer.
"""
from copy import deepcopy
from types import MethodType
from unittest.mock import Mock

import pytest

from kanamemory import ui_components
from kanamemory.app import KanaMemory
from kanamemory.feedback_lifecycle import FeedbackLifecycleMixin
from kanamemory.learning_questions import build_learning_question
from kanamemory.learning_store import LearningStore, validate_backup_rows
from support.app_fixtures import question_app, setup_app
from support.fake_ui import View, descendants, text_of
from support.learning_fixtures import LEARNING_PREFS, NOW, click, learning_app


def synthetic_lesson():
    def template(prompt, answer, context, mode, choices):
        return {
            "prompt": prompt,
            "task": "写出目标词读音。" if mode == "text" else "选出目标词写法。",
            "answer": answer, "accepted_answers": [answer], "input_mode": mode,
            "choices": choices, "strict": True, "feedback": "合成测试说明。",
            "practice": {
                "schema_version": 1,
                "task_family": "contextual_reading" if mode == "text" else "contextual_orthography",
                "context_id": context,
                "scaffold": "retrieval" if mode == "text" else "recognition",
                "target_spans": [{"start": 0, "end": 1, "quote": prompt[:1]}],
                "background_ids": [], "reviewed": True,
            },
        }

    return {
        "id": "word:synthetic-guide-conversion", "revision": "synthetic-conversion-v1",
        "family": "word", "title": "猫", "reading": "ねこ", "meaning": "猫",
        "abilities": ["use", "reading"], "depends_on": [],
        "instruction": {"rule": "合成词汇说明。", "example": "猫がいます。"},
        "question_templates": {
            "vocab_orthography": [template("ねこがいます。", "猫", "synthetic-a", "choice", ["猫", "犬"])],
            "vocab_reading": [template("猫が寝ています。", "ねこ", "synthetic-b", "text", [])],
        },
    }


def mount_saved_guide(app, content_id):
    """Use the real controller without loading the whole course presentation."""
    app._learning_item = app.learning_store.get_item(content_id)
    checkpoint = app._learning_item["checkpoint"]
    app._learning_guide = checkpoint["guide_question"]
    app._learning_guide_result = ui_components.col()
    app._page_scroll = None
    app.screen("learning_guide", "跟着做")
    app.body.add(*app._learning_guide_controls(app._learning_guide, checkpoint["guide_draft"]),
                 app._learning_guide_result)
    if checkpoint.get("guide_answer") is not None:
        app.render_learning_guide_result(checkpoint["guide_correct"])
    return app._learning_guide_context


@pytest.fixture
def guided(learning_app):
    app = learning_app
    item = synthetic_lesson()
    app.store.set_setting(LEARNING_PREFS, {"enabled": list(item["question_templates"])})
    app.learning_store.enroll(item, now=NOW)
    app.learning_store.set_initial_familiarity(item["id"], {"use": "new", "reading": "new"})
    app.learning_store.advance_instruction(item["id"], "intro_done", now=NOW)
    sequence = [build_learning_question(item, kind, guided=True)
                for kind in ("vocab_orthography", "vocab_reading")]
    app.learning_store.advance_instruction(item["id"], "guide", now=NOW, state={
        "guide_sequence": sequence, "guide_index": 1,
        "guide_completed_ids": [sequence[0]["id"]], "guide_question": sequence[1],
        "guide_draft": {"demonstration_open": True, "custom_note": "synthetic-note"},
    })
    mount_saved_guide(app, item["id"])
    return app, item


def enter(app, value):
    context = app._learning_guide_context
    click(app, "罗马音")
    context["input"].value = value
    context["input"].on_change(context["input"])
    return context


def checkpoint(app, item):
    return app.learning_store.get_item(item["id"])["checkpoint"]


def assert_exposure_only(app, item, before_units):
    assert app.learning_store.get_item(item["id"])["units"] == before_units
    assert app.learning_store.history() == []
    assert app.learning_store.get_item(item["id"])["stage"] == "guide"
    validate_backup_rows(app.store._db)


@pytest.mark.parametrize("raw,normalized,correct", [
    ("neko", "ねこ", True),
    ("NEKO", "ねこ", True),
    ("ねko", "ねこ", True),
    ("inu", "いぬ", False),
    ("ねこ", "ねこ", True),
])
def test_complete_answer_saves_one_consistent_submission_without_formal_scoring(guided, raw, normalized, correct):
    app, item = guided
    before_units = deepcopy(app.learning_store.get_item(item["id"])["units"])
    frozen = deepcopy(app._learning_guide)
    context = enter(app, raw)
    assert checkpoint(app, item)["guide_draft"]["answer"] == raw
    assert context["submit"].enabled
    click(app, "提交")
    saved = checkpoint(app, item)
    assert saved["guide_answer"] == saved["guide_draft"]["answer"] == normalized
    assert saved["guide_correct"] is correct
    assert saved["guide_question"] == frozen
    assert saved["guide_draft"]["demonstration_open"] is True
    assert saved["guide_draft"]["custom_note"] == "synthetic-note"
    assert context["draft"] == saved["guide_draft"]
    assert context["locked"] is correct
    assert context["input"].enabled is not correct
    assert_exposure_only(app, item, before_units)


def test_wrong_romaji_can_be_edited_and_resubmitted_after_reopening(guided):
    app, item = guided
    before_units = deepcopy(app.learning_store.get_item(item["id"])["units"])
    enter(app, "inu")
    click(app, "提交")
    app.learning_store = LearningStore(app.store)
    context = mount_saved_guide(app, item["id"])
    assert context["input"].value == "いぬ"
    assert not context["locked"]
    assert "合成测试说明。" in text_of(app._learning_guide_result)
    enter(app, "neko")
    assert checkpoint(app, item)["guide_answer"] is None
    click(app, "提交")
    assert checkpoint(app, item)["guide_correct"] is True
    assert checkpoint(app, item)["guide_answer"] == checkpoint(app, item)["guide_draft"]["answer"] == "ねこ"
    assert_exposure_only(app, item, before_units)


@pytest.mark.parametrize("raw", ["nek", "neko"])
def test_unsent_raw_draft_and_question_survive_reopening(guided, raw):
    app, item = guided
    before_units = deepcopy(app.learning_store.get_item(item["id"])["units"])
    old = enter(app, raw)
    before = deepcopy(checkpoint(app, item))
    app.learning_store = LearningStore(app.store)
    current = mount_saved_guide(app, item["id"])
    assert current["input"].value == raw
    assert current["input"].readonly is False
    assert checkpoint(app, item) == before
    old["input"].value = "stale"
    old["input"].on_change(old["input"])
    old["submit"].on_press(old["submit"])
    assert checkpoint(app, item) == before
    click(app, "提交")
    if raw == "nek":
        assert "请补全罗马音" in current["status"].text
        assert checkpoint(app, item) == before
    else:
        assert checkpoint(app, item)["guide_correct"] is True
        assert checkpoint(app, item)["guide_draft"]["answer"] == "ねこ"
    assert_exposure_only(app, item, before_units)


@pytest.mark.parametrize("raw", ["nek", "neko"])
def test_feedback_round_trip_keeps_raw_draft_and_live_submission_controls(guided, raw):
    app, item = guided
    context = enter(app, raw)
    before = deepcopy(checkpoint(app, item))
    before_units = deepcopy(app.learning_store.get_item(item["id"])["units"])
    original = app.body
    app.content_slot = View(original)
    app.title_label = View(text="跟着做")
    app._view_key = ("continue_learning", None)
    app._feedback_return_stack = []
    app._ink_revision = app._audio_serial = 0
    app._sync_back_handler = Mock()
    app._capture_page = MethodType(KanaMemory._capture_page, app)
    for name in ("begin_page_feedback", "return_from_feedback", "_restore_feedback_page"):
        setattr(app, name, MethodType(getattr(FeedbackLifecycleMixin, name), app))

    def render_editor(*args):
        app.screen("report_editor", "记录反馈", View())
        app.content_slot.clear()
        app.content_slot.add(app.body)

    app.render_feedback_editor = render_editor
    app.begin_page_feedback(screenshot=b"synthetic-screenshot")
    assert app.page == "report_editor"
    app._learning_guide = {"id": "unrelated-report-view"}
    app.return_from_feedback()
    assert app.page == "learning_guide"
    assert app.content_slot.children == [original]
    assert context["input"] in list(descendants(app.content_slot))
    assert context["input"].value == raw
    assert checkpoint(app, item) == before
    assert app._feedback_return_stack == []
    context["submit"].on_press(context["submit"])
    if raw == "nek":
        assert "请补全罗马音" in context["status"].text
        assert checkpoint(app, item) == before
    else:
        assert checkpoint(app, item)["guide_correct"] is True
        assert checkpoint(app, item)["guide_draft"]["answer"] == "ねこ"
    assert_exposure_only(app, item, before_units)
