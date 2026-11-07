"""Ink is visible during a pen gesture without another button waking Android."""
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from toga.colors import Color
from toga.widgets.canvas import Canvas, State

ROOT = Path(__file__).resolve().parents[1]
from kanamemory import app as app_module
from kanamemory.app import GREEN, KanaMemory


from support.live_ink_rendering_support import Handle, AndroidIdleLoop, RecordingContext, RecordingCanvas, ink_app


@pytest.mark.parametrize("page", ["free_ink", "ink_question"])
def test_pen_movement_is_visible_before_release_or_recognition(ink_app, page):
    app = ink_app
    app.page = page
    app.ink_press(app.canvas, 20, 30)
    app.loop.idle_turn()
    assert app.canvas.ink == [(("move", 20, 30), ("line", 20.5, 30.5))]

    app.ink_drag(app.canvas, 45, 60)
    app.loop.idle_turn()
    assert app.canvas.ink == [(("move", 20, 30), ("line", 45, 60))]
    assert app._active_stroke is app.strokes[0]
    app.platform.recognize.assert_not_called()
    app._checkpoint.assert_not_called()


def test_multiple_touch_points_in_one_turn_render_once_without_losing_points(ink_app):
    app = ink_app
    initial_frames = len(app.canvas.frames)
    app.ink_press(app.canvas, 10, 20)
    for point in [(20, 30), (35, 45), (40, 65)]:
        app.ink_drag(app.canvas, *point)
    app.loop.idle_turn()

    assert len(app.canvas.frames) == initial_frames + 1
    assert app.canvas.ink == [(("move", 10, 20), ("line", 20, 30),
                               ("line", 35, 45), ("line", 40, 65))]
    assert len(app.strokes[0]) == 4
    app.loop.idle_turn()
    assert len(app.canvas.frames) == initial_frames + 1


def test_release_renders_last_sample_and_checkpoints_complete_stroke(ink_app):
    app = ink_app
    app.ink_press(app.canvas, 30, 40)
    app.ink_drag(app.canvas, 45, 60)
    app.loop.idle_turn()
    app.ink_release(app.canvas, 70, 80)
    app.loop.idle_turn()

    assert app.canvas.ink == [(("move", 30, 40), ("line", 45, 60), ("line", 70, 80))]
    assert app._active_stroke is None
    app._checkpoint.assert_called_once()


@pytest.mark.parametrize("action", ["undo_ink", "clear_ink"])
def test_undo_and_clear_update_canvas_before_recognition_and_pending_draw(ink_app, action):
    app = ink_app
    app.ink_press(app.canvas, 10, 20)
    app.ink_release(app.canvas, 25, 30)
    app.loop.idle_turn()
    first_stroke = app.canvas.ink[0]
    app.ink_press(app.canvas, 60, 70)
    app.ink_drag(app.canvas, 80, 90)
    # Undo/clear while a redraw is queued must not resurrect removed ink.
    getattr(app, action)()
    expected = [first_stroke] if action == "undo_ink" else []
    assert app.canvas.ink == expected
    app.loop.idle_turn()
    assert app.canvas.ink == expected
    assert app._active_stroke is None
    app.platform.recognize.assert_not_called()


def test_leaving_page_cancels_queued_draw_before_another_ink_canvas_is_shown(ink_app):
    app = ink_app
    old_canvas = app.canvas
    old_frame_count = len(old_canvas.frames)
    app.ink_press(old_canvas, 20, 30)
    app.screen("home", "首页", scroll=False)
    # Reenter before the old scheduled callback has had a chance to execute.
    # Without cancellation it could mistakenly paint this unrelated canvas.
    app.canvas = RecordingCanvas()
    app.page = "free_ink"
    app.strokes = []
    app.loop.idle_turn()
    assert len(old_canvas.frames) == old_frame_count
    assert app.canvas.frames == []
    app.ink_press(app.canvas, 80, 90)
    app.loop.idle_turn()
    assert app.canvas.ink == [(("move", 80, 90), ("line", 80.5, 90.5))]


def test_live_paint_and_resize_preserve_recognition_coordinates_and_timestamps(ink_app, monkeypatch):
    app = ink_app
    moments = iter([1.0, 1.025, 1.060])
    monkeypatch.setattr(app_module.time, "monotonic", lambda: next(moments))
    app.ink_press(app.canvas, 30, 40)
    app.loop.idle_turn()
    app.ink_drag(app.canvas, 50, 60)
    app.loop.idle_turn()
    app.ink_release(app.canvas, 70, 80)
    app.loop.idle_turn()
    expected = [[(30.0, 40.0, 1000), (50.0, 60.0, 1025), (70.0, 80.0, 1060)]]
    assert app.strokes == expected
    app.ink_resize(app.canvas, 720, 420)
    app.loop.idle_turn()
    assert app.canvas.ink == [(("move", 60, 80), ("line", 100, 120), ("line", 140, 160))]
    assert app.strokes == expected
    # Restore the real clock before asyncio creates and runs its own loop.
    monkeypatch.undo()
    asyncio.run(app.recognize_ink())
    app.platform.recognize.assert_awaited_once_with(expected, 360, 210)
    assert app.strokes == expected
