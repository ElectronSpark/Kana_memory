"""The stop pages of the teaching steps and the page 开始前 (design 13 C4): a display model and one action
callback in, the page's children out.

Each function returns the children in the order and with the sizes, colours and flags the page has; the outer
layer hands them to screen() with the model's page name and title. Rows that shared builders make from the app
are handed in already built and stand where the page has them. Every button hands its TeachingAction to
``on_action(action, button)`` (button: the pressed control). The pages keep no state, see no app object, store
or service, and decide nothing the model does not carry; the page 引导例题暂不可用 returns its status line beside
its children, because a refused press writes into it.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import kanamemory.ui_components as u
from ..teaching_display import (FamiliarityModel, GuideBlockedModel, GuidePausedModel, GuideUnavailableModel,
                                MaterialNotReadyModel, TeachingButton)
from ..teaching_model import TeachingAction

OnAction = Callable[[TeachingAction, object], object]


@dataclass(eq=False)
class UnavailableView:
    """The page 引导例题暂不可用 as drawn: its children and its status line."""

    children: list
    status: object


def _button(button: TeachingButton, on_action: OnAction, **style):
    action = button.action
    return u.button(button.caption, lambda widget: on_action(action, widget), **style)


def guide_blocked_page(model: GuideBlockedModel, *, replacement_rows: Sequence, next_content_rows: Sequence,
                       on_action: OnAction) -> list:
    """引导题待核: the text, the replacement action's rows, 查看更新后的说明, the next content's rows, 回到今日."""
    return [u.label(model.text),
            *replacement_rows,
            _button(model.detail, on_action),
            *next_content_rows,
            _button(model.home, on_action)]


def material_not_ready_page(model: MaterialNotReadyModel, *, on_action: OnAction) -> list:
    """学习材料尚不完整 / 材料待核验: the text, 查看材料 or 预览草稿, 回到今日."""
    return [u.label(model.text),
            _button(model.detail, on_action),
            _button(model.home, on_action)]


def guide_paused_page(model: GuidePausedModel, *, on_action: OnAction) -> list:
    """引导已保留: the text, 继续其他学习, 练习设置, 回到今日."""
    return [u.label(model.text, size=16),
            _button(model.go_on, on_action, primary=True),
            _button(model.settings, on_action, compact=True),
            _button(model.home, on_action)]


def guide_unavailable_page(model: GuideUnavailableModel, *, on_action: OnAction) -> UnavailableView:
    """引导例题暂不可用: the lesson's title, why and what can be done, the status line, 直接开始练习, 跳到下一项,
    回到今日."""
    status = u.label("", size=13, color=u.MUTED)
    return UnavailableView([u.label(model.item_title, size=21, bold=True, text_role=model.title_role),
                            u.label(model.text, size=16),
                            u.label(model.note, size=13, color=u.MUTED),
                            status,
                            _button(model.practice, on_action, primary=True),
                            _button(model.skip, on_action),
                            _button(model.home, on_action)],
                           status)


def familiarity_page(model: FamiliarityModel, *, on_action: OnAction) -> list:
    """开始前: the lesson's title, the ability asked, 初学 / 有印象 / 已熟悉."""
    return [u.label(model.item_title, size=24, bold=True, text_role=model.title_role),
            u.label(model.ability, size=18, bold=True),
            *(_button(level, on_action) for level in model.levels)]
