"""The six pages the kana practice draws itself on the way to a question (design 13 C4): a display model and
one action callback in, the page's children out.

Each function returns the children in the order and with the sizes, colours
and primary flags the page has; the outer layer hands them to screen() with
the model's page name and title. Rows that shared builders make from the app
(a note, the line of kana symbols, the route's next step, extra practice) are
handed in already built and stand where the page has them. Every button hands
its KanaAction to ``on_action(action, button)`` (button: the pressed control).
The pages keep no state, see no app object, store or service, and decide
nothing the model does not carry.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import NamedTuple

import kanamemory.ui_components as u
from ..display import (BatchDoneModel, ContentUpdatedModel, KanaButton, KindsPausedModel, PrerequisiteModel,
                       ReviewWaitModel, WordDoneModel)
from ..model import KanaAction

OnAction = Callable[[KanaAction, object], object]

# How the line of kana symbols of the prerequisite page looks; the app's builder of study text draws it.
SYMBOLS_STYLE = {"size": 26, "color": u.GREEN, "text_role": "study"}


def _button(button: KanaButton, on_action: OnAction, **style):
    action = button.action
    # Like the page methods these buttons used to call, the handler takes whatever else a press hands it.
    return u.button(button.caption, lambda widget=None, **_: on_action(action, widget), **style)


def word_done_page(model: WordDoneModel, *, saved_row, on_action: OnAction) -> list:
    """本组单词完成: the result, the note, 再练一组单词, 回到今日."""
    return [u.label(model.result, size=24, bold=True),
            saved_row,
            _button(model.again, on_action, primary=True),
            _button(model.home, on_action)]


def batch_done_page(model: BatchDoneModel, *, next_rows: Sequence, extra_rows: Sequence, on_action: OnAction) -> list:
    """本批完成: the summary, the route's next step, extra practice, 查看复习记录, 回到今日."""
    return [u.label(model.summary, size=24, bold=True),
            *next_rows,
            *extra_rows,
            _button(model.records, on_action),
            _button(model.home, on_action)]


def content_updated_page(model: ContentUpdatedModel, *, kept_row, on_action: OnAction) -> list:
    """题目内容已更新: the note, 使用更新后的题目, 练习设置, 回到今日."""
    return [kept_row,
            _button(model.use_new, on_action, primary=True),
            _button(model.setup, on_action),
            _button(model.home, on_action)]


def kinds_paused_page(model: KindsPausedModel, *, kept_row, on_action: OnAction) -> list:
    """题型已暂停: the note, 练习设置, 回到今日."""
    return [kept_row,
            _button(model.setup, on_action, primary=True),
            _button(model.home, on_action)]


def prerequisite_page(model: PrerequisiteModel, *, symbols_row, on_action: OnAction) -> list:
    """先完成前置假名 / 先学所需规则: the kana waited for, a button per rule's lesson, what is kept, 检查并继续,
    练习设置, 回到今日."""
    return [symbols_row,
            *(_button(rule, on_action, primary=True, multiline=True) for rule in model.rules),
            u.label(model.kept, size=15, color=u.MUTED),
            _button(model.check, on_action, primary=True),
            _button(model.setup, on_action),
            _button(model.home, on_action)]


class ReviewWaitPage(NamedTuple):
    """The review wait's children, and the two controls its timer and its check go on using."""

    children: list
    status: object   # the label that tells how long the wait still is
    check: object    # the button 检查并继续复习


def review_wait_page(model: ReviewWaitModel, *, saved_row, next_rows: Sequence, extra_rows: Sequence,
                     on_action: OnAction) -> ReviewWaitPage:
    """本轮先歇一会儿: how long the wait still is, when the next question can start, the note, 检查并继续复习, the
    route's next step, extra practice, 回到今日."""
    status = u.label(model.status, size=20, bold=True, color=u.GREEN)
    check = _button(model.check, on_action, primary=True)
    children = [status,
                u.label(model.due, size=15, color=u.MUTED),
                saved_row,
                check,
                *next_rows,
                *extra_rows,
                _button(model.home, on_action)]
    return ReviewWaitPage(children, status, check)
