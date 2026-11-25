"""The 学习方式 screen (design 12 UI1 with D1, D2): a page under design 13 C4.

Input: display.OrientationModel and callbacks; output: the widgets.
Since 2026-11-18 the orientation is two cards, one under the other: the
name, its sentence, and its state -- 当前使用 on the stored one, a button on
the other (改用广度优先 / 改用深度优先) that stores it at once through
``on_choose(mode)``. While nothing is stored the page says so in a full
sentence at the top, no card says 当前使用, both cards have their button (保持
深度优先 for the one in force) and the 广度优先 card carries 推荐. No selection
shows an orientation, so nothing on the page can differ from what is in force.
The page keeps display state only (the depth selection and whether the
learner touched it). 确定 applies the depth and, with a display.FocusModel
(``focus``), the focus part (UI6, ui/focus_part), which follows the
orientation in force: on_confirm(orientation, depth[, focus]) receives the
stored Orientation (None when never chosen; the caller then stores nothing).
"""
from __future__ import annotations

from collections.abc import Callable

import kanamemory.ui_components as u
from ..display import (IN_USE, RECOMMENDED, SWITCH_TO, UNCHOSEN_PAGE_TEXT, KEEP_THOROUGH, FocusModel,
                       OrientationModel)
from ..model import Orientation
from ..rules.depth import KanaDepth
from .focus_part import FocusPart

ORIENTATION_HEADING = "学习方式"
DEPTH_HEADING = "假名练到哪一步"
CONFIRM = "确定"


def orientation_cards(model: OrientationModel, on_choose: Callable[[Orientation], object] | None) -> list:
    """The two option cards with their states, and the page's top sentence while nothing is chosen."""
    rows = []
    if model.chosen is None:
        rows.append(u.label(UNCHOSEN_PAGE_TEXT, size=15, bold=True))
    if model.notice:
        rows.append(u.label(model.notice, size=13, color=u.GREEN))
    for option in model.orientations:
        parts = [u.label(option.title, size=16, bold=True, margin_bottom=2)]
        if model.recommended is option.mode:
            parts.append(u.label(RECOMMENDED, size=12, color=u.GREEN, margin_bottom=2))
        parts.append(u.label(option.text, size=13, color=u.MUTED))
        if model.chosen is option.mode:
            parts.append(u.label(IN_USE, size=13, bold=True, color=u.GREEN))
        elif on_choose is not None:
            caption = (KEEP_THOROUGH if model.chosen is None and option.mode is Orientation.THOROUGH
                       else SWITCH_TO[option.mode.value])
            parts.append(u.button(caption, lambda widget, mode=option.mode: on_choose(mode), multiline=True))
        rows.append(u.col(*parts, margin_bottom=12, padding=10, background_color=u.ENTRY_BACKGROUNDS[0]))
    return rows


class _OrientationForm:
    """One screen: the two cards, the depth selection (it follows the orientation in force) and 确定."""

    def __init__(self, model: OrientationModel, on_confirm: Callable[..., object], focus: FocusModel | None = None,
                 on_choose: Callable[[Orientation], object] | None = None, filter_actions: dict | None = None):
        self._model = model
        self._on_confirm = on_confirm
        self._on_choose = on_choose
        self._levels = {option.level: option.title for option in model.depths}
        self._by_title = {option.title: option for option in model.depths}
        self._touched = False   # the learner chose a depth by hand
        self._moving = False    # the page itself is moving the depth selection
        level = model.preselected_depth(model.preselected)
        self.depth = u.selection(items=list(self._by_title), value=self._levels[level], on_change=self._depth_changed)
        self.consequence = u.label(model.depth_option(level).text, size=12, color=u.MUTED)
        self.focus = None if focus is None else FocusPart(focus, model.preselected, **(filter_actions or {}))

    def widgets(self) -> list:
        return [u.label(ORIENTATION_HEADING, size=17, bold=True), *orientation_cards(self._model, self._on_choose),
                u.label(DEPTH_HEADING, size=17, bold=True), self.depth, self.consequence,
                *([] if self.focus is None else [self.focus.box()]),
                u.button(CONFIRM, self._confirm, primary=True)]

    def _mode(self) -> Orientation:
        """The orientation in force (the stored one, else THOROUGH): the depth and the focus part follow it."""
        return self._model.preselected

    def _depth(self):
        return self._by_title.get(self.depth.value, self._model.depth_option(
            self._model.preselected_depth(self._mode())))

    def _show_consequence(self) -> None:
        self.consequence.text = u.wrapped(self._depth().text, 40)

    def _depth_changed(self, widget=None, **kwargs) -> None:
        if not self._moving:
            self._touched = True
        self._show_consequence()

    def _confirm(self, widget=None, **kwargs) -> None:
        if self.focus is None:
            self._on_confirm(self._model.chosen, self._depth().level)
        else:
            self._on_confirm(self._model.chosen, self._depth().level, self.focus.picked())


def orientation_page(model: OrientationModel, *, on_confirm: Callable[..., object],
                     focus: FocusModel | None = None, on_choose: Callable[[Orientation], object] | None = None,
                     on_only: Callable[[bool], object] | None = None, on_restore: Callable[..., object] | None = None,
                     on_pause_add: Callable[[], object] | None = None) -> list:
    """The screen's widgets: the two orientation cards, the depth choice with its D2 sentence, the focus part
    when ``focus`` is given, 确定.

    on_choose(mode): a card's button (stores the orientation at once). on_confirm(orientation, depth) without
    ``focus``; on_confirm(orientation, depth, picked_focus) with it; ``orientation`` is the stored one or None.
    on_only(on) / on_restore(scope) / on_pause_add(): the focus part's 只做范围内 pair and 暂不复习 section (design 12
    H8, H9), each stored at once by the caller; without them those parts are not drawn.
    """
    actions = {name: value for name, value in (("on_only", on_only), ("on_restore", on_restore),
                                                ("on_pause_add", on_pause_add)) if value is not None}
    return _OrientationForm(model, on_confirm, focus, on_choose, actions).widgets()
