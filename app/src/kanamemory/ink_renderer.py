"""Incremental Toga ink paths with one native invalidation per rendered frame.

The model's coordinate/timestamp samples remain untouched. Toga's public
Canvas.line_to() invalidates the native view on every call, so update its
documented drawing-action list in a batch instead of replaying those helpers.
"""
from dataclasses import dataclass
from functools import lru_cache

from toga.widgets.canvas import LineTo, MoveTo, Rect, Stroke


@lru_cache(maxsize=1)
def _android_path_type():
    # Imported only when Android actually draws the canvas, not on desktop or
    # during recognition. This is a display cache, never a source of ink data.
    from android.graphics import Path
    return Path


class _InkStroke(Stroke):
    def _draw(self, context):
        if type(context).__module__ != "toga_android.widgets.canvas":
            return super()._draw(context)
        # Toga otherwise reconstructs Path through Python/Java for every old
        # LineTo on every frame. Keep the native path and append only new points.
        path = getattr(self, "_native_path", None)
        if path is None:
            path = self._native_path = _android_path_type()()
            self._native_count = 0
            self._native_last_action = None
        actions, count = self.drawing_actions, self._native_count
        if count > len(actions) or (count and actions[count - 1] is not self._native_last_action):
            # The temporary half-pixel tap line was replaced by a real point.
            path.reset()
            count = 0
        for action in actions[count:]:
            if isinstance(action, MoveTo):
                path.moveTo(action.x, action.y)
            elif isinstance(action, LineTo):
                path.lineTo(action.x, action.y)
            else:
                # Keep Toga's full drawing contract if a future caller adds
                # another action type instead of assuming it is a line.
                return super()._draw(context)
        self._native_count = len(actions)
        self._native_last_action = actions[-1] if actions else None
        context.save()
        try:
            context.set_stroke_style(self.stroke_style)
            context.set_line_width(self.line_width)
            context.native.drawPath(path, context.state.stroke)
        finally:
            context.restore()


@dataclass
class _StrokePath:
    source: list
    path: Stroke
    count: int


class InkRenderer:
    def __init__(self, canvas, color):
        self.canvas = canvas
        self.color = color
        self._transform = None
        self._grid = None
        self._paths = []

    def render(self, strokes, width, height, transform):
        scale, left, top = transform
        geometry = (width, height, scale, left, top)
        if geometry != self._transform:
            self._transform = geometry
            self._paths = []
            self._grid = Stroke(stroke_style="#D7E5DC", line_width=1)
            self._grid.drawing_actions[:] = [
                Rect(left + 1, top + 1, max(1, width * scale - 2), max(1, height * scale - 2)),
                MoveTo(left, top + height * scale / 2),
                LineTo(left + width * scale, top + height * scale / 2),
            ]
        del self._paths[len(strokes):]
        for index, stroke in enumerate(strokes):
            cached = self._paths[index] if index < len(self._paths) else None
            if not stroke:
                cached = None
            elif cached is None or cached.source is not stroke or len(stroke) < cached.count:
                path = _InkStroke(stroke_style=self.color, line_width=4)
                path.drawing_actions.append(MoveTo(left + stroke[0][0] * scale,
                                                  top + stroke[0][1] * scale))
                cached = _StrokePath(stroke, path, 1)
                if len(stroke) == 1:
                    # A tap has a visible short mark until its second sample.
                    path.drawing_actions.append(LineTo(left + stroke[0][0] * scale + .5,
                                                       top + stroke[0][1] * scale + .5))
            if cached is not None and len(stroke) > cached.count:
                if cached.count == 1:
                    del cached.path.drawing_actions[1:]
                cached.path.drawing_actions.extend(
                    LineTo(left + x * scale, top + y * scale)
                    for x, y, timestamp in stroke[cached.count:])
                cached.count = len(stroke)
            if index < len(self._paths):
                self._paths[index] = cached
            else:
                self._paths.append(cached)
        self.canvas.root_state.drawing_actions[:] = [
            self._grid, *(cached.path for cached in self._paths if cached is not None)]
        self.canvas.redraw()
