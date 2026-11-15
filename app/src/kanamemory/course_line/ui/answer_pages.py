"""The result of a course answer (作答结果) and its detail page (design 13 C4): a display model and one action
callback in, the page's children out.

Each function returns the children in the order and with the sizes, colours
and flags the page has; the outer layer hands them to screen() with the
model's page name and title. What shared builders make from the app (the
answer's control and its speaker, the learner's notes, the detail page's rows)
is handed in already built and stands where the page has it. Every button
hands its AnswerAction to ``on_action(action, button)`` (button: the pressed
control). The pages keep no state, see no app object, store or service, and
decide nothing the model does not carry.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

import kanamemory.ui_components as u
from kanamemory.answer_feedback import build_answer_feedback
from kanamemory.answer_result_ui import entered_answer_row
from ..answer_display import AnswerButton, DetailModel, FeedbackModel
from ..answer_model import AnswerAction

OnAction = Callable[[AnswerAction, object], object]


def _button(button: AnswerButton, on_action: OnAction, **style):
    action = button.action
    return u.button(button.caption, lambda widget: on_action(action, widget), **style)


def feedback_page(model: FeedbackModel, *, answer, speaker, notes: Sequence, on_action: OnAction) -> list:
    """作答结果: the verdict, the answer (with its speaker beside it when there is one), the Japanese that was
    asked, the line about teaching first, the entered answer, the pitch judge's remark, the explanation, the
    learner's notes, 问 AI and 详细 at the right, why the answer does not count, and the rating buttons in two
    rows — or 保存并继续 alone."""
    children = [build_answer_feedback(model.result, structure_error=model.structure_error),
                u.row(answer, speaker) if speaker else answer]
    if model.asked:
        children.append(u.label(model.asked, size=15, color=u.MUTED, text_role=model.asked_role))
    if model.needs_teaching is not None:
        children.append(u.label(model.needs_teaching, size=14, color=u.MUTED))
    if model.entered_shown is not None:
        children.append(entered_answer_row(u, model.entered_shown, text_role=model.answer_role))
    if model.diagnosis is not None:
        children.append(u.label(model.diagnosis, size=13, color=u.MUTED))
    if model.explanation is not None:
        children.append(u.label(model.explanation))
    children.extend(notes)
    children.append(u.row(u.col(flex=1),
                          _button(model.tutor, on_action, compact=True, width=72, font_size=12),
                          _button(model.detail, on_action, compact=True, width=72, font_size=12)))
    if model.unresolved is not None:
        children.append(u.label(model.unresolved, size=13, color=u.MUTED))
    if model.ratings is not None:
        children.extend([u.row(*[_button(button, on_action, flex=1, multiline=True) for button in model.ratings[:2]]),
                         u.row(*[_button(button, on_action, flex=1, multiline=True) for button in model.ratings[2:]])])
    else:
        children.append(_button(model.save, on_action, primary=True))
    return children


def feedback_detail_page(model: DetailModel, *, rows: Sequence, on_action: OnAction) -> list:
    """详细: the lesson's title, the handed-in rows, 返回结果."""
    return [u.label(model.item_title, size=22, text_role=model.title_role),
            *rows,
            _button(model.back, on_action, primary=True)]
