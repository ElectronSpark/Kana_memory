"""Answer hints are secondary actions; saved waits can return home directly."""
from kanamemory import ui_components as component_ui
from datetime import timedelta
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from support.answer_option_ui_support import preview_app
from support.answer_option_ui_support import render
from support.focused_navigation_support import focused_app
from support.focused_navigation_support import resolve_confirmation
from support.joint_practice_ui_support import queued_app
from support.fake_ui import View, descendants, text_of
from support.app_fixtures import question_app, setup_app
from support.review_wait_ui_support import clock
from support.review_wait_ui_support import start_wait
from support.review_wait_ui_support import waiting_app
from support.text_layout_support import leaf
from kanamemory import app as app_module
from kanamemory.app import KanaMemory
from kanamemory.navigation import PageState
from kanamemory.text_layout import TextPack
from toga.style import Pack
from travertino.node import Node


KINDS = ("kana_choice", "romaji_kana", "kana_romaji", "romaji_kana_full",
         "word_meaning_kana", "word_kana", "romaji_handwriting", "kana_handwriting")


def control(app, caption):
    return next(node for node in descendants(app.body)
                if node.text == caption and node.on_press)


@pytest.fixture
def actions_app(question_app, monkeypatch):
    app = question_app

    def button(text, handler, **options):
        view = View(text=text, on_press=handler, **options)
        view.options = options
        return view

    monkeypatch.setattr(component_ui, "button", button)
    monkeypatch.setattr(app_module.toga, "Canvas", lambda **kw: View(**kw))
    app._ink_revision, app._ink_busy = 0, False
    app.model_state, app._paint_handle = "ready", None
    app._redraw_ink = Mock()
    for method in ("ink_press", "ink_drag", "ink_release", "ink_resize", "undo_ink", "clear_ink"):
        setattr(app, method, Mock())
    app.recognize_ink = Mock()
    app.show_ink_page = MethodType(KanaMemory.show_ink_page, app)
    return app


@pytest.mark.parametrize("kind", KINDS)
def test_answer_hint_precedes_answering_area_and_is_a_secondary_control(actions_app, kind):
    app = actions_app
    render(app, kind)
    nodes = list(descendants(app.body))
    hint = control(app, "显示答案")
    parents = [node for node in nodes if hint in node.children]
    assert len(parents) == 1 and parents[0] is not app.body
    assert hint.options.get("compact") is True
    assert hint.options.get("font_size") == 12
    assert hint.options.get("height") == 40
    assert hint.options.get("flex") == 1
    assert not hint.options.get("primary", False)
    assert app.body.children[-1] is not parents[0]

    if kind == "kana_romaji":
        answer_area = app.romaji_input
        assert nodes.index(hint) < nodes.index(control(app, "提交答案"))
    elif kind in {"word_meaning_kana", "word_kana", "romaji_kana_full"}:
        answer_area = app.word_answer_label
        assert nodes.index(hint) < nodes.index(control(app, "提交答案"))
        assert nodes.index(hint) < nodes.index(control(app, app.question.options[0]))
    elif kind in {"romaji_handwriting", "kana_handwriting"}:
        answer_area = app.canvas
        assert control(app, "识别笔迹") is app.recognize_button
        app.recognize_button.on_press(None)
        app.recognize_ink.assert_called_once_with(None)
        assert not app.answer_revealed
    else:
        answer_area = control(app, app.question.options[0])
    assert nodes.index(hint) < nodes.index(answer_area)


@pytest.mark.parametrize("scale", [1.0, 1.3, 2.0])
@pytest.mark.parametrize("width", [288, 328, 408])
def test_android_hint_has_usable_width_with_real_pack_layout(monkeypatch, scale, width):
    def box(direction, *children, **style):
        node = Node(Pack(direction=direction, **style), children=list(children))
        node.style._assigned_applicator = SimpleNamespace(node=node)
        return node

    hints = []

    def android_button(text, handler, *, compact=False, **style):
        # The production Android helper removes fixed height and gives text a
        # flexible intrinsic width of zero; Pack must allocate row width to it.
        style.pop("height")
        hint, measurements = leaf(TextPack(**style), text_length=len(text), font_scale=scale)
        hints.append((hint, measurements))
        return hint

    monkeypatch.setattr(component_ui, "col", lambda *children, **style: box("column", *children, **style))
    monkeypatch.setattr(component_ui, "row", lambda *children, **style: box("row", *children, **style))
    monkeypatch.setattr(component_ui, "button", android_button)
    layout = app_module.answer_hint(SimpleNamespace(question=object()))
    layout.style._layout_node(width, 0, True, False)
    hint, measurements = hints[0]
    assert hint.layout.content_width == width // 2
    assert measurements[-1] == width / 2
    # Four 12pt CJK glyphs use roughly 64sp before Android's font scaling.
    assert hint.layout.content_width >= 64 * scale
    assert layout.children[0].layout.content_width + hint.layout.content_width == width


@pytest.mark.parametrize("kind", KINDS)
def test_moved_hint_still_records_assistance_and_offers_only_again(actions_app, kind):
    app = actions_app
    render(app, kind)
    app.question_audio_ok = True
    control(app, "显示答案").on_press(None)
    assert app.page == "feedback" and app.answer_revealed
    assert not app.answer_correct and app.answered_text == ""
    assert app._attempt_key() in app._assisted_attempts
    assert control(app, "保存并继续" if kind in {"word_meaning_kana", "word_kana"} else "再学一次").enabled
    for rating in ("有些困难", "记住了", "很轻松"):
        assert rating not in text_of(app.body)
    app.store.record_review.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
def test_retired_hint_cannot_reveal_a_new_question(actions_app, kind):
    app = actions_app
    old_question, _ = render(app, kind)
    old_hint = control(app, "显示答案")
    new_question, _ = render(app, kind)
    assert new_question is not old_question
    before, page = app.body, app.page
    old_hint.on_press(None)
    assert app.body is before and app.page == page
    assert not app.answer_revealed and not app._assisted_attempts
    assert not app._answered_attempts
    app.store.record_review.assert_not_called()


@pytest.mark.parametrize("kind", ["kana_romaji", "romaji_kana_full", "word_meaning_kana"])
def test_submit_remains_distinct_and_grades_the_entered_answer(actions_app, kind):
    app = actions_app
    render(app, kind)
    if kind == "kana_romaji":
        app.romaji_input.value = app.question.answer
        app.romaji_input.on_change(app.romaji_input)
    else:
        for symbol in app.question.answer:
            app.append_word_key(symbol)
    control(app, "提交答案").on_press(None)
    assert app.page == "feedback" and app.answer_correct
    assert not app.answer_revealed and not app._assisted_attempts
    assert control(app, "保存并继续" if kind == "word_meaning_kana" else "记住了").enabled


@pytest.fixture
def saved_wait_app(waiting_app):
    app = waiting_app
    app.main_window.dialog = AsyncMock()
    app.navigation, app.header_back_slot = View(), View()
    app.root = View(app.content_slot, app.navigation)
    app.header_back_button = View(text="‹", on_press=lambda widget: KanaMemory.go_back(app))
    app._practice_context = False
    app._navigating, app._render_key = False, None
    for method in ("_capture_page", "show_question", "show_home"):
        setattr(app, method, MethodType(getattr(KanaMemory, method), app))
    app._stats_summary, app._services_summary = Mock(return_value="统计"), Mock(return_value="服务")
    for method in ("show_practice_setup", "show_kana", "show_free_ink", "show_cards"):
        setattr(app, method, Mock())
    previous_screen = app.screen

    def screen(*args, **kwargs):
        result = previous_screen(*args, **kwargs)
        if app._page_scroll is not None:
            app._page_scroll.position = (0, 0)
        return result

    app.screen = screen
    return app


@pytest.mark.parametrize("entry", ["home_button", "system_back", "header_back"])
def test_saved_wait_returns_directly_and_preserves_pending_schedule(saved_wait_app, clock, entry):
    app = saved_wait_app
    app.store.record_review("previous", "kana_choice", "hiragana", 3,
                            now=clock.now - timedelta(days=1))
    due = clock.now + timedelta(seconds=39)
    start_wait(app, due)
    before = app.store.load_session_progress()
    reviews = app.store.review_history("previous")
    timer = app._review_wait_handle
    app.back_stack.pages.append(PageState(("show_words", None), "words", "词库", View()))
    if entry == "home_button":
        control(app, "回到今日").on_press(None)
    elif entry == "system_back":
        assert KanaMemory.go_back(app) is True
    else:
        app.header_back_button.on_press(None)
    assert app.page == "home" and not app.back_stack.pages
    assert app.navigation in app.root.children
    assert not app.header_back_slot.children
    assert timer.cancelled and app._review_wait_handle is None
    assert not app.loop.confirmations
    app.main_window.dialog.assert_not_called()
    assert not app._leave_confirmation_active and not app._returning_home
    after = app.store.load_session_progress()
    for key in ("session_id", "questions", "question_index", "session_right", "assisted_indices", "state"):
        assert after[key] == before[key]
    assert after["questions"][0].metadata["not_before_utc"] == due.isoformat()
    assert app.store.review_history("previous") == reviews
    assert app.store.stats()["total_reviews"] == 1
    # The actual decorated show_home must not reinsert the departed waiting
    # page after the synchronous exit helper has cleared navigation history.
    KanaMemory.go_back(app)
    assert app.page == "home" and not app.back_stack.pages


def test_failed_save_keeps_waiting_page_without_confirmation_or_history_loss(saved_wait_app, clock):
    app = saved_wait_app
    start_wait(app, clock.now + timedelta(seconds=39))
    app.back_stack.pages.append(PageState(("show_words", None), "words", "词库", View()))
    before, history, timer = app.body, list(app.back_stack.pages), app._review_wait_handle
    # An unchanged checkpoint is not rewritten (A5); another write makes this
    # save necessary, so its failure must keep the page.
    app.store.set_setting("unrelated_marker", 1)
    app.store.save_session_progress = Mock(side_effect=OSError("disk full"))
    control(app, "回到今日").on_press(None)
    assert app.page == "session_wait" and app.body is before
    assert app.back_stack.pages == history
    assert app._review_wait_handle is timer and not timer.cancelled
    assert "未完成练习暂未保存" in app.alert.call_args.args[0]
    assert not app.loop.confirmations
    app.main_window.dialog.assert_not_called()
    assert app.store.stats()["total_reviews"] == 0


@pytest.mark.parametrize("feedback", [False, True])
def test_active_answer_and_feedback_still_require_exit_confirmation(focused_app, feedback):
    app = focused_app
    question, values = render(app)
    if feedback:
        app.choose_practice_answer(values[0], question)
    before, page = app.body, app.page
    app.go_back()
    assert app.page == page and app.body is before
    assert len(app.pending_confirmation) == 1
    resolve_confirmation(app, False)
    assert app.page == page and app.body is before
    app.go_back()
    resolve_confirmation(app, True)
    assert app.page == "home"
    assert app.main_window.dialog.await_count == 2
    app.store.record_review.assert_not_called()
