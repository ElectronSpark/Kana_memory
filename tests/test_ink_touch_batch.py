"""Android's historical samples reach both the visible path and saved draft."""
from copy import deepcopy
from types import MethodType
from unittest.mock import Mock

import pytest

from support.live_ink_rendering_support import ink_app
from kanamemory import app as app_module
from kanamemory.app import KanaMemory


@pytest.fixture
def batch_app(ink_app, monkeypatch):
    app = ink_app
    app.page = "ink_question"
    app.ink_touch_batch = MethodType(KanaMemory.ink_touch_batch, app)
    app._schedule_ink_draw = Mock(wraps=app._schedule_ink_draw)
    monkeypatch.setattr(app_module.time, "monotonic", lambda: 1.0)
    return app


def test_history_keeps_all_curve_points_and_timing_with_one_schedule_per_batch(batch_app):
    app = batch_app
    app.ink_touch_batch(app.canvas, "down", [(10, 20, 100)])
    app.loop.idle_turn()
    app._schedule_ink_draw.reset_mock()
    initial_frames = len(app.canvas.frames)
    samples = [(18, 25, 105), (27, 18, 110), (35, 37, 119), (44, 20, 130)]
    app.ink_touch_batch(app.canvas, "move", samples)
    app._schedule_ink_draw.assert_called_once_with()
    app._checkpoint.assert_not_called()
    assert len(app.loop.ready) == 1
    app.loop.idle_turn()
    assert len(app.canvas.frames) == initial_frames + 1
    assert app.strokes == [[(10.0, 20.0, 1000),
                           (18.0, 25.0, 1005), (27.0, 18.0, 1010),
                           (35.0, 37.0, 1019), (44.0, 20.0, 1030)]]
    assert app.canvas.ink == [(("move", 10, 20), ("line", 18, 25),
                               ("line", 27, 18), ("line", 35, 37), ("line", 44, 20))]
    assert samples == [(18, 25, 105), (27, 18, 110), (35, 37, 119), (44, 20, 130)]


def test_multiple_events_before_idle_render_one_complete_frame_and_up_saves(batch_app):
    app = batch_app
    initial_frames = len(app.canvas.frames)
    saved = []
    app._checkpoint.side_effect = lambda: saved.append((deepcopy(app.strokes), app._active_stroke))
    app.ink_touch_batch(app.canvas, "down", [(10, 20, 100)])
    app.ink_touch_batch(app.canvas, "move", [(15, 35, 110), (20, 40, 120)])
    app.ink_touch_batch(app.canvas, "up", [(25, 30, 130), (30, 20, 140)])
    assert app._schedule_ink_draw.call_count == 3
    assert len(app.loop.ready) == 1
    app._checkpoint.assert_called_once_with()
    assert saved == [(app.strokes, None)]
    assert [point[2] for point in app.strokes[0]] == [1000, 1010, 1020, 1030, 1040]
    app.loop.idle_turn()
    assert len(app.canvas.frames) == initial_frames + 1
    assert app.canvas.ink == [(("move", 10, 20), ("line", 15, 35),
                               ("line", 20, 40), ("line", 25, 30), ("line", 30, 20))]


def test_restored_later_epoch_stays_monotonic_and_preserves_each_gesture_intervals(batch_app):
    app = batch_app
    # A draft may have been saved before reboot, with a much later uptime.
    old_stroke = [(1.0, 2.0, 80000), (3.0, 4.0, 90000)]
    app.strokes = [old_stroke, []]
    app.ink_touch_batch(app.canvas, "down", [(10, 20, 500)])
    app.ink_touch_batch(app.canvas, "up", [(20, 40, 521), (30, 60, 555)])
    app.ink_touch_batch(app.canvas, "down", [(50, 60, 600)])
    app.ink_touch_batch(app.canvas, "up", [(70, 80, 625)])
    assert app.strokes[0] == old_stroke
    assert [p[2] for p in app.strokes[-2]] == [90001, 90022, 90056]
    assert [p[2] for p in app.strokes[-1]] == [90057, 90082]
    timestamps = [p[2] for stroke in app.strokes for p in stroke]
    assert timestamps == sorted(timestamps)


@pytest.mark.parametrize("tail", [[], [(25, 30, 115), (30, 35, 120)]])
def test_cancel_finishes_and_saves_partial_stroke_without_later_move_joining_it(batch_app, tail):
    app = batch_app
    app.ink_touch_batch(app.canvas, "down", [(10, 20, 100)])
    app.ink_touch_batch(app.canvas, "move", [(20, 25, 110)])
    app._schedule_ink_draw.reset_mock()
    app.ink_touch_batch(app.canvas, "cancel", tail)
    assert app._active_stroke is None
    app._checkpoint.assert_called_once_with()
    assert app._schedule_ink_draw.call_count == bool(tail)
    expected = deepcopy(app.strokes)
    app.ink_touch_batch(app.canvas, "move", [(200, 150, 150)])
    assert app.strokes == expected
    app.loop.idle_turn()
    assert len(app.canvas.ink[0]) == 2 + len(tail)


def test_leaving_canvas_stops_stroke_and_does_not_bridge_reentry(batch_app):
    app = batch_app
    app.ink_touch_batch(app.canvas, "down", [(10, 20, 100)])
    app.ink_touch_batch(app.canvas, "move", [(20, 25, 110), (361, 25, 120), (30, 40, 130)])
    assert app.strokes == [[(10.0, 20.0, 1000), (20.0, 25.0, 1010)]]
    assert app._active_stroke is None
    app.ink_touch_batch(app.canvas, "up", [(60, 50, 140)])
    app._checkpoint.assert_called_once_with()
    app.ink_touch_batch(app.canvas, "down", [(40, 50, 150)])
    assert len(app.strokes) == 2
    assert app.strokes[1] == [(40.0, 50.0, 1011)]


@pytest.mark.parametrize("points", [[], [(-1, 50, 100)], [(50, 211, 100)]])
def test_empty_or_out_of_bounds_start_never_creates_empty_stroke(batch_app, points):
    app = batch_app
    app.ink_touch_batch(app.canvas, "down", points)
    app.ink_touch_batch(app.canvas, "move", [(20, 30, 105)])
    assert app.strokes == []
    app._schedule_ink_draw.assert_not_called()
    app._checkpoint.assert_not_called()


def test_resize_transform_applies_to_every_historical_sample(batch_app):
    app = batch_app
    app.ink_resize(app.canvas, 720, 210)
    app.loop.idle_turn()
    # The drawing area is centered, with a 180 logical-unit left margin.
    app.ink_touch_batch(app.canvas, "down", [(170, 50, 100)])
    assert app.strokes == []
    app.ink_touch_batch(app.canvas, "down", [(200, 40, 110)])
    app.ink_touch_batch(app.canvas, "up", [(230, 60, 120), (250, 80, 140)])
    assert app.strokes == [[(20.0, 40.0, 1000), (50.0, 60.0, 1010), (70.0, 80.0, 1030)]]
    app.loop.idle_turn()
    assert app.canvas.ink == [(("move", 200, 40), ("line", 230, 60), ("line", 250, 80))]


@pytest.mark.parametrize("reason", ["stale_canvas", "busy", "different_page"])
def test_stale_busy_or_hidden_canvas_does_not_mutate_ink_or_checkpoint(batch_app, reason):
    app = batch_app
    app.strokes = [[(10.0, 20.0, 900)]]
    app._active_stroke = app.strokes[0]
    app._ink_event_offset = 800
    before = deepcopy(app.strokes)
    target = app.canvas
    if reason == "stale_canvas":
        target = object()
    elif reason == "busy":
        app._ink_busy = True
    else:
        app.page = "home"
    for phase in ("down", "move", "up", "cancel"):
        app.ink_touch_batch(target, phase, [(50, 60, 100)])
    assert app.strokes == before
    assert app._active_stroke is app.strokes[0]
    app._schedule_ink_draw.assert_not_called()
    app._checkpoint.assert_not_called()
