"""Incremental rendering keeps every ink sample without per-point invalidation."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from toga.widgets.canvas import Canvas, State

from support.live_ink_rendering_support import RecordingCanvas
from kanamemory.app import GREEN
from kanamemory.ink_renderer import InkRenderer
from kanamemory import ink_renderer


def full_redraw(canvas, strokes, width, height, transform):
    """The previous renderer, retained only as a frame-equivalence oracle."""
    scale, left, top = transform
    canvas.root_state.drawing_actions.clear()
    canvas.stroke_style = "#D7E5DC"
    canvas.line_width = 1
    canvas.begin_path()
    canvas.rect(left + 1, top + 1, max(1, width * scale - 2), max(1, height * scale - 2))
    canvas.move_to(left, top + height * scale / 2)
    canvas.line_to(left + width * scale, top + height * scale / 2)
    canvas.stroke()
    canvas.stroke_style = GREEN
    canvas.line_width = 4
    for stroke in strokes:
        if not stroke:
            continue
        canvas.begin_path()
        canvas.move_to(left + stroke[0][0] * scale, top + stroke[0][1] * scale)
        if len(stroke) == 1:
            canvas.line_to(left + stroke[0][0] * scale + .5, top + stroke[0][1] * scale + .5)
        for x, y, _ in stroke[1:]:
            canvas.line_to(left + x * scale, top + y * scale)
        canvas.stroke()
    canvas.redraw()


@pytest.mark.parametrize("transform", [(1, 0, 0), (2, 0, 0), (.6, 52, 8)])
def test_frames_equal_previous_renderer_including_taps_and_empty_strokes(transform):
    canvas, previous = RecordingCanvas(), RecordingCanvas()
    renderer = InkRenderer(canvas, GREEN)
    strokes = []
    for next_stroke in ([], [(4, 8, 100)], [(10, 20, 110), (20, 40, 130)],
                        [(80, 40, 140), (70, 90, 160), (6, 9, 170)]):
        strokes.append(next_stroke)
        source = deepcopy(strokes)
        count = len(canvas.frames)
        renderer.render(strokes, 360, 210, transform)
        full_redraw(previous, strokes, 360, 210, transform)
        assert len(canvas.frames) == count + 1
        assert canvas.frames[-1] == previous.frames[-1]
        assert strokes == source


def test_append_preserves_prior_actions_and_tap_mark_is_removed():
    canvas = RecordingCanvas()
    renderer = InkRenderer(canvas, GREEN)
    strokes = [[(10, 20, 100)]]
    renderer.render(strokes, 360, 210, (1, 0, 0))
    grid, path = canvas.root_state.drawing_actions
    start = path.drawing_actions[0]
    strokes[0].extend([(20, 30, 110), (40, 80, 120)])
    renderer.render(strokes, 360, 210, (1, 0, 0))
    old_line = path.drawing_actions[1]
    assert canvas.ink == [(("move", 10, 20), ("line", 20, 30), ("line", 40, 80))]
    strokes[0].append((70, 90, 130))
    strokes.append([(8, 7, 140)])
    renderer.render(strokes, 360, 210, (1, 0, 0))
    assert canvas.root_state.drawing_actions[0] is grid
    assert canvas.root_state.drawing_actions[1] is path
    assert path.drawing_actions[0] is start
    assert path.drawing_actions[1] is old_line


def test_resize_undo_clear_and_restore_do_not_leave_stale_paths():
    canvas, expected = RecordingCanvas(), RecordingCanvas()
    renderer = InkRenderer(canvas, GREEN)
    strokes = [[(10, 20, 100), (20, 30, 110)], [(40, 50, 120)]]
    for current, transform in [(strokes, (1, 0, 0)), (strokes, (1.5, 3, 10)),
                               (strokes[:1], (1.5, 3, 10)), ([], (1.5, 3, 10)),
                               (deepcopy(strokes), (1.5, 3, 10)),
                               ([strokes[0][:1]], (1.5, 3, 10))]:
        renderer.render(current, 360, 210, transform)
        full_redraw(expected, current, 360, 210, transform)
        assert canvas.frames[-1] == expected.frames[-1]


def test_long_stroke_appends_one_action_and_invalidates_once_per_frame():
    # Real Toga Canvas with a counted backend; no native/UI dependency.
    canvas = object.__new__(Canvas)
    canvas._root_state = State()
    calls = []
    canvas._impl = SimpleNamespace(redraw=lambda: calls.append(1))
    renderer = InkRenderer(canvas, GREEN)
    strokes = [[(10, 20, 100), (20, 30, 110)]]
    renderer.render(strokes, 360, 210, (1, 0, 0))
    path = canvas.root_state.drawing_actions[1]
    first_line = path.drawing_actions[1]
    for index in range(2, 5000):
        strokes[0].append((index % 300, index % 180, 100 + index * 10))
        renderer.render(strokes, 360, 210, (1, 0, 0))
        assert len(path.drawing_actions) == index + 1
        assert path.drawing_actions[1] is first_line
    assert len(calls) == 4999
    assert len(strokes[0]) == len(path.drawing_actions) == 5000
    assert len(canvas.root_state.drawing_actions) == 2


class NativePath:
    instances = []

    def __init__(self):
        self.commands = []
        self.calls = []
        self.instances.append(self)

    def moveTo(self, x, y):
        self.calls.append(("move", x, y))
        self.commands.append(("move", x, y))

    def lineTo(self, x, y):
        self.calls.append(("line", x, y))
        self.commands.append(("line", x, y))

    def reset(self):
        self.calls.append(("reset",))
        self.commands.clear()


class AndroidContext:
    __module__ = "toga_android.widgets.canvas"

    def __init__(self):
        self.paths = []
        self.state = SimpleNamespace(stroke=SimpleNamespace())
        self.native = SimpleNamespace(drawPath=self.draw_path)
        self.save_count = self.restore_count = 0

    def save(self):
        self.save_count += 1

    def restore(self):
        self.restore_count += 1

    def set_stroke_style(self, color):
        self.state.stroke.color = color

    def set_line_width(self, width):
        self.state.stroke.width = width

    def draw_path(self, path, paint):
        self.paths.append((tuple(path.commands), paint.color, paint.width))


def test_android_reuses_compiled_path_and_only_appends_new_points(monkeypatch):
    monkeypatch.setattr(ink_renderer, "_android_path_type", lambda: NativePath)
    NativePath.instances = []
    canvas = RecordingCanvas()
    renderer = InkRenderer(canvas, GREEN)
    strokes = [[(10, 20, 100), (20, 30, 110), (40, 50, 120)]]
    renderer.render(strokes, 360, 210, (1, 0, 0))
    action = canvas.root_state.drawing_actions[1]
    context = AndroidContext()
    action._draw(context)
    native_path = NativePath.instances[0]
    expected = [("move", 10, 20), ("line", 20, 30), ("line", 40, 50)]
    assert native_path.calls == expected
    assert context.paths[-1][0] == tuple(expected)
    for _ in range(100):
        action._draw(context)
    # A redraw (even with a newly created Context) never replays old points.
    other_context = AndroidContext()
    action._draw(other_context)
    assert native_path.calls == expected
    strokes[0].append((60, 70, 130))
    renderer.render(strokes, 360, 210, (1, 0, 0))
    action._draw(context)
    assert native_path.calls == [*expected, ("line", 60, 70)]
    assert len(NativePath.instances) == 1
    assert context.save_count == context.restore_count == 102
    assert context.paths[-1][2] == 4


def test_android_tap_resize_and_replaced_strokes_reset_native_geometry(monkeypatch):
    monkeypatch.setattr(ink_renderer, "_android_path_type", lambda: NativePath)
    NativePath.instances = []
    canvas = RecordingCanvas()
    renderer = InkRenderer(canvas, GREEN)
    strokes = [[(10, 20, 100)]]
    context = AndroidContext()

    def draw():
        for action in canvas.root_state.drawing_actions[1:]:
            action._draw(context)

    renderer.render(strokes, 360, 210, (1, 0, 0))
    draw()
    native_path = NativePath.instances[0]
    assert native_path.commands == [("move", 10, 20), ("line", 10.5, 20.5)]
    strokes[0].append((30, 40, 110))
    renderer.render(strokes, 360, 210, (1, 0, 0))
    draw()
    assert native_path.commands == [("move", 10, 20), ("line", 30, 40)]
    assert native_path.calls.count(("reset",)) == 1
    renderer.render(strokes, 360, 210, (2, 5, 8))
    draw()
    assert len(NativePath.instances) == 2
    assert NativePath.instances[1].commands == [("move", 25, 48), ("line", 65, 88)]
    renderer.render([[(90, 80, 120), (70, 60, 130)]], 360, 210, (2, 5, 8))
    draw()
    assert len(NativePath.instances) == 3
    assert context.paths[-1][0] == (("move", 185, 168), ("line", 145, 128))
    renderer.render([], 360, 210, (2, 5, 8))
    count = len(context.paths)
    draw()
    assert len(context.paths) == count
