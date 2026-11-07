"""Practice summaries stay in the page and retain safe, reusable navigation."""
from kanamemory import ui_components as component_ui
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import app as app_module
from kanamemory.app import KanaMemory, PRACTICE_PAGES
from kanamemory.navigation import BackStack, page_view


class View:
    def __init__(self, *children, text="", on_press=None, content=None, style=None, **kwargs):
        self.children = list(children) + ([content] if content is not None else [])
        self.text, self.on_press, self.style = text, on_press, style
        self.content, self.position = content, (0, 0)
        self.options = kwargs

    def add(self, *children):
        self.children.extend(children)

    def clear(self):
        self.children.clear()


def descendants(node):
    yield node
    for child in node.children:
        yield from descendants(child)


def action_in(slot, caption):
    return next(view for view in descendants(slot) if view.text == caption and view.on_press)


@pytest.fixture
def inline_app(monkeypatch):
    monkeypatch.setattr(component_ui, "col", lambda *children, **kw: View(*children, **kw))
    monkeypatch.setattr(component_ui, "row", lambda *children, **kw: View(*children, **kw))
    monkeypatch.setattr(component_ui, "label", lambda text, **kw: View(text=text, **kw))
    monkeypatch.setattr(component_ui, "button", lambda text, handler, **kw: View(text=text, on_press=handler, **kw))
    monkeypatch.setattr(app_module.toga, "ScrollContainer", View)
    app = SimpleNamespace(
        page="feedback", _view_key=("check_answer", None), _render_key=None,
        _page_token=0, _preview_pending=None, _paint_handle=None, _navigating=False,
        _ink_revision=0, _audio_serial=0, _active_stroke=None, _page_scroll=None,
        title_label=View(), subtitle_label=View(), content_slot=View(),
        platform=Mock(), loop=SimpleNamespace(call_soon=lambda callback: callback()),
        back_stack=BackStack(), session_id="session-1", question_index=0,
        question=SimpleNamespace(content_id="kana-304b"),
        store=SimpleNamespace(is_content_available=lambda content_id: True,
                              load_session_progress=lambda **kw: None),
        questions=[object()], session_right=0, answer_revealed=False, question_notice=View(),
        answered_text="ka", answer_correct=True, _assisted_attempts=set(),
        _answered_attempts={("session-1", 0)}, _checkpoint=Mock(),
        entry_card=lambda *children, **kw: View(*children, **kw),
        content_label=Mock(side_effect=AssertionError("Summary must not create content links")),
        mixed_label=Mock(side_effect=AssertionError("Summary must not create content links")),
        alert=Mock(), detail_visits=[],
    )
    app.platform.preview_index = AsyncMock()
    for name in ("screen", "_capture_page", "_sync_back_handler", "go_back", "_attempt_key",
                 "_content_page_key", "_mark_lookup_assisted", "_preview_link", "_show_practice_preview"):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))

    @page_view(detail=True)
    def show_kana_detail(self, kana_id):
        self.detail_visits.append(kana_id)
        self.screen("kana_detail", "假名详情", View(text=kana_id))

    app.show_kana_detail = MethodType(show_kana_detail, app)
    app.screen("feedback", "作答结果", *(View(text=text) for text in
               ("✓", "ka", "评分说明", "记住了")))
    return app


@pytest.mark.parametrize("page", sorted(PRACTICE_PAGES))
def test_every_practice_page_shows_plain_inline_summary_without_modal(inline_app, page):
    app = inline_app
    app.screen(page, "练习", View(text="原有内容"), scroll=page not in {"word_question", "ink_question"})
    original = (app._page_token, app._view_key, list(app.back_stack.pages))
    navigate = Mock()
    app._preview_link(("show_kana_detail", "ka"), "假名速览", "平假名：か\n片假名：カ\n罗马音：ka", navigate)
    assert len(app.practice_preview_slot.children) == 1
    assert "平假名：か" in "\n".join(view.text for view in descendants(app.practice_preview_slot))
    app.platform.preview_index.assert_not_called()
    app.content_label.assert_not_called()
    app.mixed_label.assert_not_called()
    navigate.assert_not_called()
    assert (app._page_token, app._view_key, app.back_stack.pages) == original
    action_in(app.practice_preview_slot, "详细").on_press(None)
    navigate.assert_called_once()


def test_replacing_card_invalidates_old_details_and_collapse_buttons(inline_app):
    app = inline_app
    first, second = Mock(), Mock()
    app._show_practice_preview(("show_kana_detail", "ka"), "速览", "か", first)
    old_detail = action_in(app.practice_preview_slot, "详细")
    old_close = action_in(app.practice_preview_slot, "收起")
    app._show_practice_preview(("show_kana_detail", "na"), "速览", "な", second)
    new_card = app.practice_preview_slot.children[0]
    old_detail.on_press(None)
    old_close.on_press(None)
    first.assert_not_called()
    second.assert_not_called()
    assert app.practice_preview_slot.children == [new_card]
    action_in(app.practice_preview_slot, "详细").on_press(None)
    second.assert_called_once()
    action_in(app.practice_preview_slot, "收起").on_press(None)
    assert not app.practice_preview_slot.children


@pytest.mark.parametrize("change", ["leave_page", "next_question", "new_session", "replace_page"])
def test_old_inline_action_cannot_navigate_another_page_or_attempt(inline_app, change):
    app = inline_app
    navigate = Mock()
    app._show_practice_preview(("show_kana_detail", "ka"), "速览", "か", navigate)
    old_action = action_in(app.practice_preview_slot, "详细")
    if change == "leave_page":
        app.screen("settings", "设置")
    elif change == "next_question":
        app.question_index += 1
    elif change == "new_session":
        app.session_id = "session-2"
    else:
        app.screen("feedback", "新反馈")
    old_action.on_press(None)
    navigate.assert_not_called()


def test_detail_back_restores_card_and_its_detail_action_still_works(inline_app):
    app = inline_app
    app._show_practice_preview(("show_kana_detail", "ka"), "速览", "か",
                               lambda: app.show_kana_detail("ka"))
    slot, feedback = app.practice_preview_slot, app.content_slot.children[0]
    detail = action_in(slot, "详细")
    detail.on_press(None)
    assert app.page == "kana_detail" and app.detail_visits == ["ka"]
    app.go_back()
    assert app.page == "feedback" and app.practice_preview_slot is slot
    assert app.content_slot.children[0] is feedback
    assert app.answered_text == "ka" and app.answer_correct and not app.answer_revealed
    assert not app._assisted_attempts
    detail.on_press(None)
    assert app.page == "kana_detail" and app.detail_visits == ["ka", "ka"]


def test_unknown_record_has_only_inline_text_and_collapse(inline_app):
    app = inline_app
    app._show_practice_preview(None, "答案选项速览", "尚无已收录的假名详情。")
    actions = [view.text for view in descendants(app.practice_preview_slot) if view.on_press]
    assert actions == ["收起"]
    app.alert.assert_not_called()
    app.platform.preview_index.assert_not_called()
    action_in(app.practice_preview_slot, "收起").on_press(None)
    assert not app.practice_preview_slot.children


def test_short_summary_and_details_share_one_row_above_rating_choices(inline_app):
    app = inline_app
    message = "平：か / 片：カ\nka · 清音与拨音"
    app._show_practice_preview(("show_kana_detail", "ka"), "所选假名", message, Mock())
    card = app.practice_preview_slot.children[0]
    assert len(card.children) == 1
    compact_row = card.children[0]
    summary, detail, collapse = compact_row.children
    assert [child.text for child in summary.children] == ["所选假名", message]
    assert summary.options["flex"] == 1
    assert detail.text == "详细" and detail.options["width"] == 52
    assert collapse.text == "收起" and collapse.options["width"] == 48
    assert detail.on_press and collapse.on_press
    assert not any(view.text.startswith("查看") for view in descendants(card))
    body = app.content_slot.children[0].content
    assert body.children[2] is app.practice_preview_slot
    assert body.children[-1].text == "记住了"


def test_external_reference_keeps_its_specific_action_in_compact_row(inline_app):
    app = inline_app
    navigate = Mock()
    app._preview_link(("external_url", "https://example.org/entry"), "例句出处", "来源信息", navigate,
                      action="查看原文")
    actions = [view.text for view in descendants(app.practice_preview_slot) if view.on_press]
    assert actions == ["查看原文", "收起"]
    navigate.assert_not_called()
    action_in(app.practice_preview_slot, "查看原文").on_press(None)
    navigate.assert_called_once()
    app.platform.preview_index.assert_not_called()


def test_feedback_slot_follows_answers_and_precedes_rating_controls(inline_app):
    app = inline_app
    body = app.content_slot.children[0].content
    assert body.children[2] is app.practice_preview_slot
    assert [view.text for view in body.children[:2]] == ["✓", "ka"]
    assert body.children[-1].text == "记住了"


@pytest.mark.parametrize("page", ["word_question", "ink_question"])
def test_keyboard_and_handwriting_summaries_have_bounded_internal_scroll(inline_app, page):
    app = inline_app
    app.screen(page, "练习", View(text="原有操作"), scroll=False)
    app._show_practice_preview(("show_kana_detail", "ka"), "速览", "か", Mock())
    scrolling_card = app.practice_preview_slot.children[0]
    assert scrolling_card.content is not None
    assert scrolling_card.style.height == 120


def test_new_practice_screen_starts_without_previous_summary(inline_app):
    app = inline_app
    app._show_practice_preview(("show_kana_detail", "ka"), "速览", "か", Mock())
    old_slot = app.practice_preview_slot
    app.screen("question", "下一题", View(text="な"))
    assert app.practice_preview_slot is not old_slot
    assert not app.practice_preview_slot.children
