"""添加… under 暂不复习 (design 12 H9): a page under design 13 C4.

Input: display.PauseChooserModel and callbacks; output: the widgets. The same
choices as the focus scope (routes, libraries, families, the stored focus
scope), minus the parts already paused. Display state only: the selection.
on_pick(scope) stores it at once (the caller); on_back() goes back.
"""
from __future__ import annotations

from collections.abc import Callable

import kanamemory.ui_components as u
from ..display import PauseChooserModel
from ..model import FocusScope


def pause_chooser_page(model: PauseChooserModel, *, on_pick: Callable[[FocusScope], object],
                       on_back: Callable[[], object]) -> list:
    """The note, the selection of the parts that can be paused, 暂不复习这一部分 and 返回."""
    scopes = {option.title: option.scope for option in model.options}
    rows = [u.label(model.text, size=15)]
    if scopes:
        choice = u.selection(items=list(scopes), value=next(iter(scopes)))

        def pick(widget):
            scope = scopes.get(choice.value)
            if scope is not None:
                on_pick(scope)
        rows += [choice, u.button(model.pick, pick, primary=True)]
    rows.append(u.button(model.back, lambda widget: on_back()))
    return rows
