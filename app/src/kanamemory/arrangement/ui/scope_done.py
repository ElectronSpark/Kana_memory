"""范围内的都做完了 (design 12 H8): a page under design 13 C4.

Input: display.ScopeDoneModel and callbacks; output: the widgets. Nothing is
decided here: on_extra() starts extra practice inside the scope, on_lift()
lifts 只做范围内 for this sitting and goes on with 继续学习, on_back() leaves.
"""
from __future__ import annotations

from collections.abc import Callable

import kanamemory.ui_components as u
from ..display import ScopeDoneModel


def scope_done_page(model: ScopeDoneModel, *, on_extra: Callable[[], object], on_lift: Callable[[], object],
                    on_back: Callable[[], object]) -> list:
    """The page: the hint, 范围外到期 N 题 (none at 0), 继续加练（范围内） (only when offered), 做范围外的, 回到今日."""
    rows = [u.label(model.hint, size=15)]
    if model.outside_line is not None:
        rows.append(u.label(model.outside_line, size=15, bold=True))
    if model.extra is not None:
        rows.append(u.button(model.extra, lambda widget: on_extra(), primary=True, multiline=True))
    rows.append(u.button(model.lift, lambda widget: on_lift(), primary=model.extra is None, multiline=True))
    rows.append(u.button(model.back, lambda widget: on_back()))
    return rows
