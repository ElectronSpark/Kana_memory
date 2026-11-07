"""Enlarged practice pages keep all controls reachable without stealing pen strokes."""
from kanamemory import ui_components as component_ui
from dataclasses import replace
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, call

import pytest


from kanamemory import app as ui
from kanamemory import text_layout
from kanamemory.app import KanaMemory
from kanamemory.domain import Question


class View:
    def __init__(self, *children, text="", handler=None, kind="box", **options):
        self.children, self.text, self.on_press = list(children), text, handler
        self.kind, self.options = kind, options
        self.value = options.get("value", "")

    def add(self, *children):
        self.children.extend(children)

    def clear(self):
        self.children.clear()


def descendants(view):
    yield view
    for child in view.children:
        yield from descendants(child)


@pytest.fixture
def focused_app(monkeypatch):
    monkeypatch.setattr(component_ui, "label", lambda text, **kw: View(text=text, kind="label", **kw))
    monkeypatch.setattr(component_ui, "button", lambda text, handler, **kw: View(text=text, handler=handler, kind="button", **kw))
    monkeypatch.setattr(component_ui, "col", lambda *children, **kw: View(*children, **kw))
    monkeypatch.setattr(component_ui, "row", lambda *children, **kw: View(*children, kind="row", **kw))
    monkeypatch.setattr(ui.toga, "ScrollContainer", lambda content, **kw: View(content, kind="scroll", **kw))
    monkeypatch.setattr(ui.toga, "Canvas", lambda **kw: View(kind="canvas", **kw))
    monkeypatch.setattr(ui.toga, "TextInput", lambda **kw: View(kind="input", **kw))
    monkeypatch.setattr(component_ui, "selection", lambda **kw: View(kind="selection", **kw))
    monkeypatch.setattr(text_layout, "font_scale", lambda: 1.0)
    app = SimpleNamespace(
        main_window=SimpleNamespace(size=SimpleNamespace(width=360, height=820)),
        question=Question("kana-304b", "romaji_kana_full", "hiragana", "", "きゃ",
                          options=["き", "ゃ", "な", "は", "あ", "い"],
                          metadata={"romaji": "kya", "meaning_hint": "测试词义"}),
        question_notice=View(text=""), research_enabled=False, script="hiragana",
        repo=SimpleNamespace(get_word=lambda word_id: None),
        note=lambda text: View(text=text), mixed_label=lambda *parts, **kw: View(text="".join(map(str, parts))),
        append_word_key=Mock(), delete_word_key=Mock(), check_answer=Mock(),
        play_question_audio=Mock(), reveal_answer=Mock(), model_controls=lambda **kw: View(text="模型"),
        _ink_revision=0, _ink_busy=False, model_state="ready", _paint_handle=None,
        _redraw_ink=Mock(), _schedule_ink_draw=Mock(), _checkpoint=Mock(),
        recognize_ink=Mock(), ink_resize=Mock(), undo_ink=Mock(), clear_ink=Mock(),
        # Android pages register a native MotionEvent listener as well as the
        # Canvas compatibility callbacks exercised by these gesture tests.
        platform=SimpleNamespace(bind_ink_touch=Mock()),
    )
    for method in ("ink_press", "ink_drag", "ink_release", "ink_touch_batch", "_ink_point", "_ink_transform"):
        setattr(app, method, MethodType(getattr(KanaMemory, method), app))

    def screen(name, title, *children, **kwargs):
        app.page, app.title, app.body = name, title, View(*children)
        app.scroll_page = kwargs.get("scroll", True)

    app.screen = screen
    return app


@pytest.mark.parametrize("kind", ["romaji_kana_full", "word_meaning_kana", "word_kana"])
@pytest.mark.parametrize("scale,height,scroll", [(1.0, 820, False), (1.3, 820, True), (2.0, 820, True), (1.0, 600, True)])
def test_word_questions_scroll_as_a_whole_when_space_is_constrained(focused_app, monkeypatch, kind, scale, height, scroll):
    app = focused_app
    monkeypatch.setattr(text_layout, "font_scale", lambda: scale)
    app.main_window.size.height = height
    app.question = replace(app.question, kind=kind)
    KanaMemory.show_word_question(app, "测试")
    # Word prompts reserve variable space for pitch alternatives even at 100%.
    scroll = scroll or kind in {"word_meaning_kana", "word_kana"}
    assert app.scroll_page is scroll
    nodes = list(descendants(app.body))
    assert sum(node.kind == "scroll" for node in nodes) == (0 if scroll else 1)
    controls = {node.text: node for node in nodes if node.kind == "button"}
    assert {"删除末字", "提交答案", "显示答案"} <= controls.keys()
    controls["き"].on_press(None)
    app.append_word_key.assert_called_once_with("き")
    app.word_answer = "きゃ"
    controls["提交答案"].on_press(None)
    app.check_answer.assert_called_once_with("きゃ")


@pytest.mark.parametrize("width,scale", [(360, 1.0), (360, 2.0), (320, 2.0), (280, 2.0)])
def test_full_kana_table_preserves_compounds_and_scrolls_without_shrinking_text(focused_app, monkeypatch, width, scale):
    app = focused_app
    monkeypatch.setattr(text_layout, "font_scale", lambda: scale)
    app.main_window.size.width = width
    app.question = replace(app.question, options=["きゃ", "ヴァ", "ティェ", "しょ", "きゃ", "あ", "い"])
    KanaMemory.show_word_question(app, "测试")
    keys = [node for node in descendants(app.body) if node.kind == "button" and node.text in app.question.options]
    # Full tables group by vowel/consonant; candidate lists preserve saved order.
    assert len(keys) == len(set(app.question.options))
    assert {node.text for node in keys} == set(app.question.options)
    # Three 18pt kana occupy 72sp before system font scaling, plus padding.
    minimum = 72 * scale + 12
    assert all(node.options["width"] >= minimum for node in keys)
    rows = [node for node in descendants(app.body) if node.kind == "row"
            and any(key in keys for key in descendants(node))]
    assert rows and len(rows[0].children) == 5
    assert all(len(row.children) <= 5 for row in rows)
    assert any(node.kind == "scroll" and node.options.get("horizontal")
               for node in descendants(app.body))
    for key in keys:
        key.on_press(None)
    assert app.append_word_key.call_args_list == [call(key.text) for key in keys]


@pytest.mark.parametrize("free", [False, True])
@pytest.mark.parametrize("scale,height,model,scroll", [
    (1.0, 820, "ready", False), (1.3, 820, "ready", True),
    (2.0, 820, "ready", True), (1.0, 600, "ready", True),
    (1.0, 820, "not_downloaded", True),
])
def test_ink_controls_and_candidates_do_not_compete_for_remaining_height(focused_app, monkeypatch, free, scale, height, model, scroll):
    app = focused_app
    monkeypatch.setattr(text_layout, "font_scale", lambda: scale)
    app.main_window.size.height, app.model_state = height, model
    app.question = replace(app.question, kind="romaji_handwriting")
    KanaMemory.show_ink_page(app, free, "测试")
    assert app.scroll_page is scroll
    nodes = list(descendants(app.body))
    assert sum(node.kind == "scroll" for node in nodes) == (0 if scroll else 1)
    assert app.canvas.options["style"].height == 210
    app.ink_result_box.add(View(text="候选一"), View(text="候选二"))
    assert {"候选一", "候选二"} <= {node.text for node in descendants(app.body)}
    assert ("显示答案" in {node.text for node in nodes}) is (not free)


def test_scrollable_canvas_reserves_drawing_gesture_and_releases_parent(focused_app, monkeypatch):
    app = focused_app
    monkeypatch.setattr(text_layout, "font_scale", lambda: 2.0)
    monkeypatch.setattr(ui, "current_platform", "android")
    monkeypatch.setattr(component_ui, "current_platform", "android")
    KanaMemory.show_ink_page(app, True, "自由试写")
    app.platform.bind_ink_touch.assert_called_once()
    canvas, on_samples = app.platform.bind_ink_touch.call_args.args
    assert canvas is app.canvas and callable(on_samples)
    parent = Mock()
    app.canvas._impl = SimpleNamespace(native=SimpleNamespace(getParent=lambda: parent))
    app.canvas.options["on_press"](app.canvas, 20, 30)
    app.canvas.options["on_drag"](app.canvas, 20, 90)
    app.canvas.options["on_release"](app.canvas, 20, 150)
    assert parent.requestDisallowInterceptTouchEvent.call_args_list == [call(True), call(False)]
    assert [[point[:2] for point in stroke] for stroke in app.strokes] == [[(20, 30), (20, 90), (20, 150)]]
    app._checkpoint.assert_called_once_with()
    assert app._active_stroke is None


def test_canvas_releases_parent_even_if_release_callback_fails(focused_app, monkeypatch):
    app = focused_app
    monkeypatch.setattr(text_layout, "font_scale", lambda: 2.0)
    monkeypatch.setattr(ui, "current_platform", "android")
    monkeypatch.setattr(component_ui, "current_platform", "android")
    KanaMemory.show_ink_page(app, True, "自由试写")
    parent = Mock()
    app.canvas._impl = SimpleNamespace(native=SimpleNamespace(getParent=lambda: parent))
    app.ink_release = Mock(side_effect=RuntimeError("test"))
    with pytest.raises(RuntimeError, match="test"):
        app.canvas.options["on_release"](app.canvas, 20, 30)
    parent.requestDisallowInterceptTouchEvent.assert_called_once_with(False)
