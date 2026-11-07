"""Opening a report must not replay, reveal or lose a retained learning screen."""
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from kanamemory import feedback_lifecycle as module
from kanamemory.app import KanaMemory
from kanamemory.feedback_lifecycle import FeedbackLifecycleMixin
from kanamemory.feedback_context import context_sha256
from kanamemory.navigation import BackStack, PageState
from support.fake_ui import View
from support.detail_header_support import chrome


@pytest.fixture
def app(monkeypatch):
    draft = View(text="input"); draft.value = "きっ"
    content = View(draft)
    original = PageState(("show_question", None), "question", "拼写", content,
                         {"word_answer": "きっ", "_practice_context": True})
    context = {"page": {"name": "question", "title": "拼写"}, "completeness": {"complete": True, "warnings": []}}
    monkeypatch.setattr(module, "capture_feedback_context", lambda *a, **k: context.copy())
    monkeypatch.setattr(KanaMemory, "_update_chrome", lambda self: None)
    original_back = PageState(("show_home", None), "home", "首页", View())
    result = SimpleNamespace(page="question", _view_key=original.key, _page_token=4,
        _audio_serial=3, _ink_revision=1, _feedback_return_stack=[], _question_started=100,
        _capture_page=lambda: original, platform=SimpleNamespace(capture_feedback_screenshot=lambda: b"png"),
        _checkpoint=Mock(), _sync_back_handler=Mock(), back_stack=BackStack(),
        content_slot=View(content), title_label=View(text="拼写"), _review_wait_handle=None,
        loop=SimpleNamespace(call_soon=lambda fn, *args: fn(*args)), alert=Mock(),
        _answered_attempts=set(), _assisted_attempts=set(), word_answer="きっ")
    result.back_stack.pages.append(original_back)
    for name, value in vars(FeedbackLifecycleMixin).items():
        if callable(value):
            setattr(result, name, MethodType(value, result))
    def render(context, screenshot):
        result._report_editor = {"context": context, "screenshot": screenshot}
        result.page = "report_editor"
        result.content_slot.clear()
        result.content_slot.add(View(text="report"))
    result.render_feedback_editor = render
    return result, original, draft


def test_report_returns_exact_widgets_draft_stack_and_ungraded_attempt(app):
    state, original, draft = app
    prior_stack = list(state.back_stack.pages)
    state.begin_page_feedback()
    context = state._report_editor["context"]
    assert context["sha256"] == context_sha256(context)
    assert context["screenshot"]["available"]
    assert state.page == "report_editor"
    state.return_from_feedback()
    assert state.content_slot.children == [original.content]
    assert state.content_slot.children[0].children[0] is draft
    assert draft.value == state.word_answer == "きっ"
    assert state.back_stack.pages == prior_stack
    assert state._view_key == original.key
    assert state._answered_attempts == state._assisted_attempts == set()


@pytest.mark.parametrize("page", ["question", "feedback", "ink_question", "session_wait"])
def test_feedback_return_keeps_strokes_and_compensates_only_report_duration(app, monkeypatch, page):
    state, original, draft = app
    state.page = original.name = page
    stroke = [[1, 2, 3], [4, 5, 6]]
    state._active_stroke = stroke
    state.refresh_model_ui = Mock()
    state._schedule_ink_draw = Mock()
    tick = Mock()
    monkeypatch.setattr(KanaMemory, "_tick_review_wait", tick)
    now = [200]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    state.begin_page_feedback()
    state._active_stroke = None
    now[0] = 215
    state.return_from_feedback()
    assert state._active_stroke is stroke
    assert state._question_started == 115
    assert draft.value == "きっ" and state.content_slot.children == [original.content]
    assert state._assisted_attempts == state._answered_attempts == set()
    assert state._checkpoint.call_count == 1  # Entry saves the draft; return does not replay it.
    assert tick.call_count == int(page == "session_wait")
    assert state._schedule_ink_draw.call_count == int(page == "ink_question")


def test_feedback_content_refresh_short_circuits_old_widget_mount(app, monkeypatch):
    from kanamemory import personal_content_refresh
    state, original, _ = app
    state.begin_page_feedback()
    replacement = View(text="current content")
    def refresh(app, previous):
        assert previous is original and app.page == original.name
        app.content_slot.clear()
        app.content_slot.add(replacement)
        return True
    monkeypatch.setattr(personal_content_refresh, "refresh_return", refresh)
    state.return_from_feedback()
    assert state.content_slot.children == [replacement]


def test_missing_screenshot_is_explicit_and_does_not_block_report(app):
    state, _, _ = app
    state.platform.capture_feedback_screenshot = Mock(side_effect=RuntimeError("device failed"))
    state.begin_page_feedback()
    assert state.page == "report_editor"
    context = state._report_editor["context"]
    assert not context["completeness"]["complete"]
    assert context["completeness"]["warnings"]
    assert not context["screenshot"]["available"]


def test_nested_report_preserves_unsaved_outer_note(app):
    state, original, _ = app
    state.begin_page_feedback()
    outer = state._report_editor
    outer["draft"] = "outer note"
    report_page = PageState(state._view_key, "report_editor", "记录反馈", state.content_slot.children[0],
                           {"_report_editor": outer})
    state._capture_page = lambda: report_page
    state.begin_page_feedback()
    state.return_from_feedback()
    assert state._report_editor is outer
    assert state._report_editor["draft"] == "outer note"
    assert state.page == "report_editor"
    state.return_from_feedback()
    assert state.content_slot.children[0] is original.content


def test_feedback_screen_checkpoint_never_rewrites_learning_state():
    state = SimpleNamespace(page="report_editor", store=Mock())
    assert KanaMemory._checkpoint(state) is None
    assert state.store.mock_calls == []


def test_detail_return_refreshes_list_without_growing_back_stack(app):
    state, _, _ = app
    page = PageState(("show_feedback_manager", None), "report_list", "反馈记录", View())
    state.back_stack.pages.append(page)
    state._feedback_manager_state = {"status": "open", "query": "字", "offset": 12}
    state._render_feedback_manager = Mock()
    state.return_from_feedback_detail()
    assert len(state.back_stack.pages) == 1
    state._render_feedback_manager.assert_called_once_with(status="open", query="字", offset=12)


@pytest.mark.parametrize("page", ["report_editor", "report_list", "report_detail"])
def test_report_chrome_has_small_back_and_editor_cannot_navigate_away(chrome, page):
    chrome.screen(page, "反馈", View(text="内容"))
    assert chrome.header_back_slot.children == [chrome.header_back_button]
    assert chrome.header_title_box.children == [chrome.title_label]
    assert chrome.title_label.style.font_size == 18
    assert (chrome.navigation in chrome.root.children) == (page == "report_list")


@pytest.mark.parametrize("name,refresh", [("practice_setup", "refresh_practice_setup"), ("word_drill_setup", "refresh_word_drill_setup"), ("settings", "refresh_model_ui")])
def test_return_refreshes_controls_after_background_work_finishes(app, name, refresh):
    state, original, draft = app
    original.name = name
    state.settings_message = View(text="正在检查")
    state.service_message = "已就绪"
    setattr(state, refresh, Mock())
    state.begin_page_feedback()
    state.return_from_feedback()
    getattr(state, refresh).assert_called_once()
    assert state.content_slot.children[0].children[0] is draft
    if name == "settings":
        assert state.settings_message.text == "已就绪"


def test_failed_learning_checkpoint_does_not_leave_original_page(app):
    state, original, _ = app
    state._checkpoint.return_value = False
    state.begin_page_feedback()
    assert state.page == "question"
    assert state.content_slot.children == [original.content]
    assert state._feedback_return_stack == []


def test_missing_dialog_screenshot_does_not_substitute_underlying_page(app):
    state, _, _ = app
    state.platform.capture_feedback_screenshot = Mock(return_value=b"wrong-window")
    state.begin_page_feedback(overlay={"kind": "ConfirmDialog"})
    state.platform.capture_feedback_screenshot.assert_not_called()
    assert state._report_editor["screenshot"] is None
