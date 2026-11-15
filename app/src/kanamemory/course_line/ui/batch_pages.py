"""The batch introduction's pages (design 13 C4): a display model and one action callback in, the page's
children out.

Each function returns the children in the order and with the sizes, colours
and primary flags the page has; the outer layer hands them to screen() with
the model's page name and title. Rows that shared builders make from the app
(the review hint, the card's body, the question's prompt, the next content)
are handed in already built and stand where the page has them. Every button
hands its BatchAction to ``on_action(action, button)`` (button: the pressed
control). The pages keep no state, see no app object, store or service, and
decide nothing the model does not carry; the quiz page returns the two things
its answer needs afterwards (its choice buttons and its result column).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import kanamemory.ui_components as u
from kanamemory.answer_feedback import build_answer_feedback
from kanamemory.question_instruction_ui import instruction_widgets
from ..batch_display import (AbilityUnavailableModel, BatchButton, CardPageModel, CardUnavailableModel, QuizPageModel,
                             QuizResultModel, QuizSettledModel, QuizUnavailableModel)
from ..batch_model import BatchAction

OnAction = Callable[[BatchAction, object], object]


@dataclass(eq=False)
class QuizView:
    """The quiz page as drawn: its children, the column an answer's result goes into, its choice buttons."""

    children: list
    result: object
    buttons: list


def _button(button: BatchButton, on_action: OnAction, **style):
    action = button.action
    return u.button(button.caption, lambda widget: on_action(action, widget), **style)


def card_unavailable_page(model: CardUnavailableModel, *, on_action: OnAction) -> list:
    """说明卡暂不可用: the text, 回到今日."""
    return [u.label(model.text),
            _button(model.home, on_action, primary=True)]


def card_page(model: CardPageModel, *, hint_rows: Sequence, body_rows: Sequence, on_action: OnAction) -> list:
    """新项说明: the card's position, the review hint's rows, the card's body, 下一项, 我已经会了, 稍后再学."""
    return [u.label(model.position, size=13, color=u.MUTED),
            *hint_rows,
            *body_rows,
            _button(model.next, on_action, primary=True),
            _button(model.known, on_action),
            _button(model.later, on_action)]


def quiz_unavailable_page(model: QuizUnavailableModel, *, on_action: OnAction) -> list:
    """批小测暂不可用: the text, 题型设置, 回到今日."""
    return [u.label(model.text),
            _button(model.settings, on_action, primary=True),
            _button(model.home, on_action)]


def quiz_settled_page(model: QuizSettledModel, *, on_action: OnAction) -> QuizView:
    """批小测 once every item is settled: the text, 继续. The view's result column is not on the page and it
    has no choice buttons."""
    return QuizView([u.label(model.text, size=18),
                     _button(model.finish, on_action, primary=True)],
                    u.col(), [])


def quiz_page(model: QuizPageModel, *, hint_rows: Sequence, prompt, on_action: OnAction) -> QuizView:
    """批小测 asking one question: the heading, the review hint's rows, the prompt, the cue above the choices,
    one button per choice, the (empty) result column."""
    result = u.col()
    buttons = [_button(choice, on_action, multiline=True, text_role=model.role) for choice in model.choices]
    return QuizView([u.label(model.heading, size=13, color=u.MUTED),
                     *hint_rows,
                     prompt,
                     *instruction_widgets(u, model.question, model.known),
                     *buttons,
                     result],
                    result, buttons)


def show_quiz_result(result, buttons: Sequence, model: QuizResultModel, *, card_rows: Sequence,
                     on_action: OnAction) -> None:
    """After an answer: the choice buttons stop answering and the result column shows the verdict, the right
    answer (when wrong), the explanation (when the question has one), the card again (when wrong) and 继续."""
    for button in buttons:
        button.enabled = False
    result.clear()
    result.add(build_answer_feedback({"correct": model.correct}))
    if not model.correct:
        result.add(u.label(model.answer, bold=True, text_role=model.answer_role))
    if model.explanation is not None:
        result.add(u.label(model.explanation))
    if not model.correct:
        # The card again; the item then waits at the end of the quiz unless
        # the store released it (design 12 Q2, breadth first).
        result.add(u.label(model.card_heading, size=13, color=u.MUTED))
        result.add(u.col(*card_rows))
    result.add(_button(model.proceed, on_action, primary=True))


def ability_unavailable_page(model: AbilityUnavailableModel, *, next_content_rows: Sequence,
                             on_action: OnAction) -> list:
    """新能力暂不可用: the text, the next content's rows, 回到今日."""
    return [u.label(model.text),
            *next_content_rows,
            _button(model.home, on_action)]
