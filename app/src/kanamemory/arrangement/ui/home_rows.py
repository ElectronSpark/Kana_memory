"""The arrangement's rows on the home page (design 12 UI2, UI3 with B10, UI5, UI6): a page part under design 13 C4.

Input: display.HomeRowsModel and callbacks; output: a possibly empty list of
widgets. home_rows are the breadth rows that stand above the due lines in the
primary card; invite_rows is the UI5 card (orientation never chosen: the
sentence and two plain buttons that store the choice), drawn at the top of
the primary card (the home keeps one primary button);
focus_rows is the UI6 focus row (display.FocusRowModel), under every
orientation and only while a focus is set.
"""
from __future__ import annotations

from collections.abc import Callable

import kanamemory.ui_components as u
from ..display import FocusRowModel, HomeRowsModel
from ..model import Orientation
from .section_note import section_note_rows


def home_rows(model: HomeRowsModel, *, on_open_orientation: Callable[[], object],
              on_add_next: Callable[[str], object]) -> list:
    """B10's note with its "加入下一小节" button first, then the route progress and today's minutes (breadth only)."""
    rows = section_note_rows(model.note, on_add_next=on_add_next) if model.note is not None else []
    if model.progress_line:
        rows.append(u.label(model.progress_line, size=13, color=u.MUTED, margin_bottom=2))
    if model.usage_line:
        rows.append(u.button(model.usage_line, lambda widget: on_open_orientation(), compact=True, multiline=True))
    return rows


def invite_rows(model: HomeRowsModel, *, on_open_orientation: Callable[[], object],
                on_choose: Callable[[Orientation], object] | None = None) -> list:
    """The card while the orientation was never chosen (UI5): the sentence, then 改为广度优先 and 保持扎实.

    Each button stores its choice at once (``on_choose``); the sentence opens
    the orientation screen. Both buttons take a share of the row (Android).
    """
    if not model.invite or not model.invite_text:
        return []
    rows = [u.button(model.invite_text, lambda widget: on_open_orientation(), multiline=True)]
    if on_choose is not None:
        rows.append(u.row(u.button(model.choose_breadth, lambda widget: on_choose(Orientation.BREADTH), flex=1,
                                   margin_right=8),
                          u.button(model.keep_thorough, lambda widget: on_choose(Orientation.THOROUGH), flex=1)))
    return rows


def focus_rows(model: FocusRowModel | None, *, on_open_orientation: Callable[[], object],
               on_cancel: Callable[[], object]) -> list:
    """UI6: "重点：…" (it opens the orientation screen) with 取消重点 beside it, then "重点之外到期 N 题".

    Both buttons take a share of the row (3:1). On Android a text widget in a
    row gets no width of its own: without a share 取消重点 was laid out zero
    wide and never seen (native check 2026-11-13).
    """
    if model is None:
        return []
    rows = [u.row(u.button(model.line, lambda widget: on_open_orientation(), compact=True, multiline=True, flex=3,
                           margin_right=8),
                  u.button(model.cancel, lambda widget: on_cancel(), compact=True, flex=1))]
    if model.outside_due_line:
        rows.append(u.label(model.outside_due_line, size=13, color=u.MUTED, margin_bottom=2))
    return rows
