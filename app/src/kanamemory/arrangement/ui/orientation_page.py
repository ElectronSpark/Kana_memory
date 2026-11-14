"""The 学习取向 screen (design 12 UI1 with D1, D2): a page under design 13 C4.

Input: display.OrientationModel and one callback; output: the widgets. The
page keeps display state only (the two current selections and whether the
learner touched the depth choice). It writes nothing: on_confirm(orientation,
depth) receives the chosen Orientation and the chosen KanaDepth, or None for
"keep the current switches"; the caller applies them and decides where to go.
With a display.FocusModel (``focus``) the screen has its third part, 学习重点
(UI6, ui/focus_part), which follows the orientation picked on the screen;
on_confirm then receives a third argument, the Focus as picked.
"""
from __future__ import annotations

from collections.abc import Callable

import kanamemory.ui_components as u
from ..display import FocusModel, OrientationModel
from ..model import Orientation
from ..rules.depth import KanaDepth
from .focus_part import FocusPart

ORIENTATION_HEADING = "学习方式"
DEPTH_HEADING = "假名练到哪一步"
CONFIRM = "确定"


class _OrientationForm:
    """The two selections of one screen; changing the orientation moves the depth until the learner touches it."""

    def __init__(self, model: OrientationModel, on_confirm: Callable[..., object], focus: FocusModel | None = None):
        self._model = model
        self._on_confirm = on_confirm
        self._modes = {option.title: option.mode for option in model.orientations}
        self._titles = {option.mode: option.title for option in model.orientations}
        self._levels = {option.level: option.title for option in model.depths}
        self._by_title = {option.title: option for option in model.depths}
        self._touched = False   # the learner chose a depth by hand
        self._moving = False    # the page itself is moving the depth selection
        level = model.preselected_depth(model.preselected)
        self.orientation = u.selection(items=list(self._modes), value=self._titles[model.preselected],
                                       on_change=self._orientation_changed)
        self.depth = u.selection(items=list(self._by_title), value=self._levels[level], on_change=self._depth_changed)
        self.consequence = u.label(model.depth_option(level).text, size=12, color=u.MUTED)
        self.focus = None if focus is None else FocusPart(focus, model.preselected)

    def widgets(self) -> list:
        explanations = []
        for option in self._model.orientations:
            explanations += [u.label(option.title, size=15, bold=True, margin_bottom=2),
                             u.label(option.text, size=13, color=u.MUTED)]
        return [u.label(ORIENTATION_HEADING, size=17, bold=True), *explanations, self.orientation,
                u.label(DEPTH_HEADING, size=17, bold=True), self.depth, self.consequence,
                *([] if self.focus is None else [self.focus.box()]),
                u.button(CONFIRM, self._confirm, primary=True)]

    def _mode(self) -> Orientation:
        return self._modes.get(self.orientation.value, self._model.preselected)

    def _depth(self):
        return self._by_title.get(self.depth.value, self._model.depth_option(
            self._model.preselected_depth(self._mode())))

    def _show_consequence(self) -> None:
        self.consequence.text = u.wrapped(self._depth().text, 40)

    def _orientation_changed(self, widget=None, **kwargs) -> None:
        if self.focus is not None:
            self.focus.follow(self._mode())
        if self._touched:
            return
        self._moving = True
        try:
            self.depth.value = self._levels[self._model.preselected_depth(self._mode())]
        finally:
            self._moving = False
        self._show_consequence()

    def _depth_changed(self, widget=None, **kwargs) -> None:
        if not self._moving:
            self._touched = True
        self._show_consequence()

    def _confirm(self, widget=None, **kwargs) -> None:
        if self.focus is None:
            self._on_confirm(self._mode(), self._depth().level)
        else:
            self._on_confirm(self._mode(), self._depth().level, self.focus.picked())


def orientation_page(model: OrientationModel, *, on_confirm: Callable[..., object],
                     focus: FocusModel | None = None) -> list:
    """The screen's widgets: both orientations with their sentences, the depth choice with its D2 sentence,
    the focus part when ``focus`` is given, 确定.

    on_confirm(orientation, depth) without ``focus``; on_confirm(orientation,
    depth, picked_focus) with it.
    """
    return _OrientationForm(model, on_confirm, focus).widgets()
