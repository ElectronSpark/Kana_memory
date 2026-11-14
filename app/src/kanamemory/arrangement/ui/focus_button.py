"""设为重点 on the page of a route, stage, unit, section or content library (design 12 UI6, H6): a page part.

Input: display.FocusButtonModel (or None: no button) and two callbacks;
output: widgets. While the page's object is the focus scope the model's
lines say so and the button cancels it; otherwise the button sets it. The
callbacks change nothing here: the wiring maps them to the focus service.
"""
from __future__ import annotations

from collections.abc import Callable

import kanamemory.ui_components as u
from ..display import FocusButtonModel
from ..model import FocusScope


def focus_button(model: FocusButtonModel | None, *, on_set: Callable[[FocusScope], object],
                 on_cancel: Callable[[], object]) -> list:
    """The lines (only while this object is the focus) and one compact button."""
    if model is None:
        return []
    lines = [u.label(line, size=13, color=u.GREEN if index == 0 else u.MUTED)
             for index, line in enumerate(model.lines)]
    action = (lambda widget: on_cancel()) if model.current else (lambda widget: on_set(model.scope))
    return [*lines, u.button(model.caption, action, compact=True, multiline=True)]
