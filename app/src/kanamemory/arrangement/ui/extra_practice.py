"""Extra practice's own page parts (design 12 E1, UI7; pages under design 13 C4).

Input: display models (ExtraButtonModel, ExtraPartsModel) and callbacks;
output: widgets. Nothing is decided here: the button's on_start() goes to the
wiring, which shows the page 加练; a part's on_start(part) switches the mode on
with that part (design 12 E9); on_back() leaves.
"""
from __future__ import annotations

from collections.abc import Callable

import kanamemory.ui_components as u
from ..display import ExtraButtonModel, ExtraPartsModel


def extra_button_rows(model: ExtraButtonModel | None, *, on_start: Callable[[], object]) -> list:
    """继续加练 for an end or wait page of 继续学习; nothing without a model (nothing can be practised)."""
    if model is None:
        return []
    return [u.button(model.caption, lambda widget: on_start())]


def extra_parts_page(model: ExtraPartsModel, *, on_start: Callable[[str], object],
                     on_back: Callable[[], object]) -> list:
    """The page 加练 (design 12 E9): its explanation, each part with what can be practised now (a button when
    something can, a line when nothing can), 回到今日. on_start(part value) starts the mode with that part."""
    rows = [u.label(model.text, size=17)]
    for option in model.parts:
        if option.startable:
            rows.append(u.button(option.caption, lambda widget, part=option.part: on_start(part), multiline=True))
        else:
            rows.append(u.label(option.caption, size=14, color=u.MUTED))
    rows.append(u.button(model.back_caption, lambda widget: on_back()))
    return rows

