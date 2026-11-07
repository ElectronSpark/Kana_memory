"""Shared handwriting model ui fixtures and assertions; no collected scenarios."""
from kanamemory import ui_components as component_ui
from unittest.mock import AsyncMock
from kanamemory.navigation import BackStack
from kanamemory.app import KanaMemory
from types import MethodType
from unittest.mock import Mock
from types import SimpleNamespace
from kanamemory import app as app_module
import pytest


class View:
    def __init__(self, *children, text="", on_press=None, enabled=True, **kwargs):
        self.children = list(children)
        self.text, self.on_press, self.enabled = text, on_press, enabled
        self.options = kwargs

    def add(self, *children):
        self.children.extend(children)

    def remove(self, child):
        self.children.remove(child)

    def clear(self):
        self.children.clear()


def descendants(view):
    yield view
    for child in view.children:
        yield from descendants(child)


def text_of(view):
    return "\n".join(str(child.text) for child in descendants(view))


def buttons(view):
    return [child for child in descendants(view) if child.on_press is not None]


@pytest.fixture
def model_app(monkeypatch):
    monkeypatch.setattr(component_ui, "label", lambda text, **kw: View(text=text, **kw))
    monkeypatch.setattr(component_ui, "button", lambda text, handler, **kw: View(text=text, on_press=handler, **kw))
    monkeypatch.setattr(component_ui, "col", lambda *children, **kw: View(*children, **kw))
    monkeypatch.setattr(component_ui, "row", lambda *children, **kw: View(*children, **kw))
    monkeypatch.setattr(app_module.toga, "Canvas", lambda **kw: View(**kw))
    monkeypatch.setattr(app_module.toga, "ScrollContainer", lambda content, **kw: View(content, **kw))
    app = SimpleNamespace(
        page="free_ink", _page_token=8, _ink_revision=2, _ink_busy=False,
        _model_request=0, _model_downloading=False, _model_checking=False, _paint_handle=None,
        model_state="not_downloaded", model_message="", service_message="日语声音已就绪",
        strokes=[[(12.0, 18.0, 1), (20.0, 30.0, 2)]], _active_stroke=None,
        ink_width=360, ink_height=210, free_ink=True, script="hiragana",
        _ink_view_width=360, _ink_view_height=210,
        canvas=View(), ink_model_panel=View(), model_panel=View(),
        ink_status_label=View(), ink_result_box=View(), recognize_button=View(enabled=False),
        settings_message=View(text="日语声音已就绪"), question_notice=View(),
        _navigating=False, _view_key=("show_free_ink", None), _render_key=None,
        _audio_serial=0, question=None, _page_scroll=None, back_stack=BackStack(),
        content_slot=View(View()), title_label=View(text="自由试写识别"),
        platform=SimpleNamespace(model_status=AsyncMock(return_value="ready"),
            download_model=AsyncMock(), recognize=AsyncMock(return_value=["あ"]),
            initialize=AsyncMock(), prepare_tts=AsyncMock(return_value="ready"),
            tts_detail="日语声音已就绪", tts_ready=True, stop=Mock()),
        heading=lambda text: View(text=text), note=lambda text: View(text=text),
        store=Mock(), refresh_practice_setup=Mock(), show_home=Mock(),
        show_settings=Mock(), play_question_audio=Mock(), reveal_answer=Mock(),
        _redraw_ink=Mock(), _schedule_ink_draw=Mock(), _sync_back_handler=Mock(),
        check_answer=Mock(), content_label=lambda text, **kw: View(text=text, **kw),
        alert=Mock(), _checkpoint=Mock(), refresh_home_actions=Mock(),
    )
    for name in ("model_label", "_services_summary", "model_controls", "refresh_model_ui", "check_model",
                 "download_model", "recognize_ink", "confirm_ink", "ink_press",
                 "ink_drag", "ink_release", "ink_resize", "_ink_transform", "_ink_point", "undo_ink", "clear_ink",
                 "show_ink_page", "go_back"):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))

    def screen(name, title, *children, **kwargs):
        app.page, app.title = name, title
        app._page_token += 1
        app.body = View(*children)
        return app.body

    app.screen = screen
    return app
