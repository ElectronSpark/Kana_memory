"""Shared live ink rendering fixtures and assertions; no collected scenarios."""
from kanamemory import ui_components as component_ui
from unittest.mock import AsyncMock
from toga.widgets.canvas import Canvas
from toga.colors import Color
from kanamemory.app import GREEN
from kanamemory.app import KanaMemory
from types import MethodType
from unittest.mock import Mock
from types import SimpleNamespace
from toga.widgets.canvas import State
from kanamemory import app as app_module
from copy import deepcopy
import pytest


class Handle:
    def __init__(self, callback, args):
        self.callback, self.args = callback, args
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        if not self.cancelled:
            self.callback(*self.args)


class AndroidIdleLoop:
    """Model the idle-handler wakeup relevant to Toga's Android event loop.

    Touch callbacks run on the UI thread. A delayed asyncio callback alone
    does not enqueue an Android idle-handler wakeup; ready work does. The
    timer is deliberately considered due at the next awakened turn so this
    checks scheduling, rather than relying on elapsed wall-clock timing.
    """
    def __init__(self):
        self.ready, self.delayed = [], []

    def call_soon(self, callback, *args):
        handle = Handle(callback, args)
        self.ready.append(handle)
        return handle

    def call_later(self, delay, callback, *args):
        handle = Handle(callback, args)
        self.delayed.append(handle)
        return handle

    def idle_turn(self):
        if not self.ready:
            return
        pending = self.ready + self.delayed
        self.ready, self.delayed = [], []
        for handle in pending:
            handle.run()


class RecordingContext:
    """Run real Toga drawing actions against an observable native context."""
    def __init__(self):
        self.completed = []
        self.path = []
        self.stroke_style = None
        self.line_width = None
        self.saved = []

    def save(self):
        self.saved.append((self.stroke_style, self.line_width))

    def restore(self):
        self.stroke_style, self.line_width = self.saved.pop()

    def set_stroke_style(self, value):
        self.stroke_style = value

    def set_line_width(self, value):
        self.line_width = value

    def begin_path(self):
        self.path = []

    def rect(self, *values):
        self.path.append(("rect", *values))

    def move_to(self, x, y):
        self.path.append(("move", x, y))

    def line_to(self, x, y):
        self.path.append(("line", x, y))

    def stroke(self):
        self.completed.append(
            (self.stroke_style, self.line_width, tuple(self.path)))


class RecordingCanvas(Canvas):
    """Use actual Toga dispatch; count every native redraw, not just the last."""
    def __init__(self):
        self._root_state = State()
        self.frames = []
        self._impl = SimpleNamespace(redraw=self.record_frame)

    def record_frame(self):
        context = RecordingContext()
        self.root_state._draw(context)
        self.frames.append(deepcopy(context.completed))

    @property
    def ink(self):
        if not self.frames:
            return []
        return [path for color, width, path in self.frames[-1] if color == Color.parse(GREEN)]


@pytest.fixture
def ink_app(monkeypatch):
    app = SimpleNamespace(
        page="free_ink", _page_token=3, _ink_revision=2, _ink_busy=False,
        _paint_handle=None, loop=AndroidIdleLoop(), canvas=RecordingCanvas(),
        ink_width=360, ink_height=210, _ink_view_width=360, _ink_view_height=210,
        strokes=[], _active_stroke=None, ink_result_box=Mock(),
        ink_status_label=SimpleNamespace(text=""), _checkpoint=Mock(),
        model_state="ready", free_ink=True, recognize_button=SimpleNamespace(),
        platform=SimpleNamespace(stop=Mock(), recognize=AsyncMock(return_value=[])),
        title_label=SimpleNamespace(text=""), subtitle_label=SimpleNamespace(text=""),
        content_slot=Mock(),
    )
    for name in ("ink_press", "ink_drag", "ink_release", "ink_resize",
                 "_ink_transform", "_ink_point", "_schedule_ink_draw", "_redraw_ink",
                 "undo_ink", "clear_ink", "screen", "recognize_ink"):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))
    monkeypatch.setattr(component_ui, "col", lambda *children, **kwargs: children)
    app._redraw_ink()
    assert app.canvas.ink == []
    return app
