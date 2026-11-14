"""Extra practice's own page parts (design 12 E1, UI7; pages under design 13 C4).

Input: display models (ExtraButtonModel, ExtraIntroModel) and callbacks;
output: widgets. Nothing is decided here: on_start() goes to the wiring,
which switches the mode on (or first shows the page 加练), on_back() leaves.
"""
from __future__ import annotations

from collections.abc import Callable

import kanamemory.ui_components as u
from ..display import ExtraButtonModel, ExtraIntroModel


def extra_button_rows(model: ExtraButtonModel | None, *, on_start: Callable[[], object]) -> list:
    """继续加练 for an end or wait page of 继续学习; nothing without a model (nothing can be practised)."""
    if model is None:
        return []
    return [u.button(model.caption, lambda widget: on_start())]


def extra_intro_page(model: ExtraIntroModel, *, on_start: Callable[[], object],
                     on_back: Callable[[], object]) -> list:
    """The page 加练 before the first start in a launch: what extra practice counts, 开始加练 and 回到今日."""
    return [u.label(model.text, size=17),
            u.button(model.start_caption, lambda widget: on_start(), primary=True),
            u.button(model.back_caption, lambda widget: on_back())]
