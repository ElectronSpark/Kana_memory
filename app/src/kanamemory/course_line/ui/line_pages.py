"""The six pages the course line draws itself (design 13 C4): a display model and one action callback in,
the page's children out.

Each function returns the children in the order and with the sizes, colours
and primary flags the page has; the outer layer hands them to screen() with
the model's page name and title. Rows that shared builders make from the app
are handed in already built and stand where the page has them. Every button
hands its CourseAction to ``on_action(action, button)`` (button: the pressed
control). The pages keep no state, see no app object, store or service, and
decide nothing the model does not carry.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

import kanamemory.ui_components as u
from ..display import (CourseButton, DoneModel, LibraryOffModel, MaterialBlockedModel, PausedModel,
                       PrerequisiteModel, WaitModel)
from ..model import CourseAction

OnAction = Callable[[CourseAction, object], object]


def _button(button: CourseButton, on_action: OnAction, **style):
    action = button.action
    return u.button(button.caption, lambda widget: on_action(action, widget), **style)


def library_off_page(model: LibraryOffModel, *, next_content_rows: Sequence, on_action: OnAction) -> list:
    """所属内容库已停用: the text, what is kept, 查看内容库 (if the app has the page), the next content's rows,
    回到今日."""
    return [u.label(model.text),
            u.label(model.kept, color=u.MUTED),
            *([_button(model.libraries, on_action)] if model.libraries is not None else []),
            *next_content_rows,
            _button(model.home, on_action)]


def material_blocked_page(model: MaterialBlockedModel, *, replacement_rows: Sequence, next_content_rows: Sequence,
                          on_action: OnAction) -> list:
    """材料待核: the text, what is kept, the replacement action's rows, the next content's rows, 回到今日."""
    return [u.label(model.text),
            u.label(model.kept, color=u.MUTED),
            *replacement_rows,
            *next_content_rows,
            _button(model.home, on_action)]


def paused_page(model: PausedModel, *, next_content_rows: Sequence, on_action: OnAction) -> list:
    """需要日语语音 / 题型已关闭: the text, the note (if any), the settings buttons (the first is primary), the next
    content's rows, 回到今日."""
    first, *others = model.buttons
    return [u.label(model.text),
            *([u.label(model.note, color=u.MUTED)] if model.note is not None else []),
            _button(first, on_action, primary=True),
            *(_button(button, on_action) for button in others),
            *next_content_rows,
            _button(model.home, on_action)]


def done_page(model: DoneModel, *, next_content_rows: Sequence, on_action: OnAction) -> list:
    """课程学习: the text, 开始新一轮, the next content's rows, 回到今日."""
    return [u.label(model.text, size=20),
            _button(model.new_round, on_action, primary=True),
            *next_content_rows,
            _button(model.home, on_action)]


def wait_page(model: WaitModel, *, next_content_rows: Sequence, extra_rows: Sequence, on_action: OnAction) -> list:
    """稍后继续: the due time, the note, 检查并继续, the next content's rows, extra practice's rows, 题型设置,
    回到今日."""
    return [u.label(model.when, size=20),
            u.label(model.note, color=u.MUTED),
            _button(model.check, on_action, primary=True),
            *next_content_rows,
            *extra_rows,
            _button(model.settings, on_action),
            _button(model.home, on_action)]


def prerequisite_page(model: PrerequisiteModel, *, next_content_rows: Sequence, on_action: OnAction) -> list:
    """先巩固前置知识: the item, the text, the due time and its note (or: unavailable and 学习设置), the taught
    dependencies (if any), 检查并继续, 我已了解前置，继续本课, the next content's rows, 回到今日."""
    if model.due_label is not None:
        due = [u.label(model.due_label, size=14), u.label(model.due_note, color=u.MUTED)]
    else:
        due = [u.label(model.unavailable, color=u.MUTED), _button(model.settings, on_action)]
    return [u.label(model.item_title, size=21, bold=True),
            u.label(model.text, size=16),
            *due,
            *([u.label(model.taught_line, size=14, color=u.MUTED)] if model.taught_line is not None else []),
            _button(model.check, on_action, primary=True),
            _button(model.acknowledge, on_action),
            *next_content_rows,
            _button(model.home, on_action)]
