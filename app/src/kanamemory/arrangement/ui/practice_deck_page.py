"""The practice deck's pages and the UI8 result rows (design 12 E5, UI8; a page under design 13 C4).

Input: display models (DeckQuestionModel, DeckResultModel, DeckEndModel,
PracticeResultModel) and callbacks; output: widgets for the caller's screen()
(header 练习). Display state only: the typed text and whether a self-check
answer has been shown. Nothing here judges, records or decides the next page:
on_answer(text), on_reveal(), on_self_check(knew), on_next(), on_leave() and
on_finish() go to the wiring, which asks the deck service. No rating is ever
offered; a miss reads 稍后再练一次 (the model's words).
"""
from __future__ import annotations

from collections.abc import Callable

import kanamemory.ui_components as u
from kanamemory.furigana_ui import cells_widget
from ..display import DECK_ANSWER_PREFIX, DeckEndModel, DeckQuestionModel, DeckResultModel, PracticeResultModel
from ..model import DeckAnswerMode


def _answer(line: str, cells, *, size: int):
    """The answer line: with cells (a course answer), 答案： then the standard spelling with kana above it."""
    if not line or not callable(cells):
        return u.label(line, size=size, text_role="study")
    shown = tuple(cells())
    if not any(cell[1] or cell[2:] for cell in shown):   # (surface, kana) or (surface, kana, pitch)
        widget = u.label(DECK_ANSWER_PREFIX + "".join(cell[0] for cell in shown), size=size,
                         text_role="study")
        widget._answer_cells = shown
        return widget
    return u.col(u.label(DECK_ANSWER_PREFIX, size=14, color=u.MUTED, margin_bottom=2),
                 cells_widget(None, shown, size=size, margin_bottom=0), margin_bottom=8)


SHOW_HINT, HIDE_HINT = "中文提示", "收起提示"   # the question page's captions (question_instruction_ui)


def _cue_rows(view) -> list:
    """A task's view (cue, its text role, the Chinese hint, folded) as the question page lays it out (design 12
    N12; question_instruction_ui.instruction_layout, whose captions and sizes it keeps): the cue, then the
    hint as a line under it, or behind 中文提示 when folded. What is visible is decided by the view."""
    text, role, hint, folded = view
    rows = [u.label(text, size=13, color=u.MUTED, text_role=role)] if text else []
    if hint and not folded:
        rows.append(u.label(hint, size=13, color=u.MUTED))
    elif hint:
        holder, line = u.col(), u.label(hint, size=13, color=u.MUTED)

        def toggle(widget=None, **kwargs):
            if line in holder.children:
                holder.remove(line)
                widget.text = SHOW_HINT
            else:
                holder.add(line)
                widget.text = HIDE_HINT
        rows.extend((u.button(SHOW_HINT, toggle, compact=True, font_size=12), holder))
    return rows


def practice_result_rows(model: PracticeResultModel, *, on_continue: Callable[[], object] | None = None) -> list:
    """The verdict, the further sentences and, with ``on_continue``, the one button that goes on."""
    rows = []
    if model.verdict:
        rows.append(u.label(model.verdict, size=18, bold=True, color=u.GREEN if model.correct else u.INK))
    rows.extend(u.label(line, size=14) for line in model.lines)
    if on_continue is not None:
        rows.append(u.button(model.continue_caption, lambda widget: on_continue(), primary=True))
    return rows


class _QuestionForm:
    """One question: options, a text field or the self-check steps; holds only what is on the screen."""

    def __init__(self, model: DeckQuestionModel, *, on_answer: Callable[[str], object],
                 on_reveal: Callable[[], object], on_self_check: Callable[[bool], object],
                 on_play: Callable[[str], object] | None, on_leave: Callable[[], object]):
        self._model = model
        self._on_answer, self._on_reveal, self._on_self_check = on_answer, on_reveal, on_self_check
        self._on_play, self._on_leave = on_play, on_leave
        self._self_check = u.col()
        self.field = None

    def widgets(self) -> list:
        model = self._model
        rows = [u.label(model.progress, size=12, color=u.MUTED)]
        if model.note:
            rows.append(u.label(model.note, size=13, color=u.MUTED))
        if model.cue is not None:
            # Design 12 N12: the task as the ordinary question page shows it (short cue, gloss or fold).
            rows.extend(_cue_rows(model.cue()))
        elif model.task:
            rows.append(u.label(model.task, size=14))
        # Design 12 N12: the prompt as the ordinary question page draws it (readings, emphasised target);
        # a view that gives None (or none given): the prompt as a plain label.
        drawn = model.prompt_view() if model.prompt_view is not None else None
        rows.extend([u.label(model.prompt, size=22, text_role="study")] if drawn is None else drawn)
        if model.audio_text and self._on_play is not None:
            rows.append(u.button(model.play_caption, lambda widget: self._on_play(model.audio_text)))
        if model.mode is DeckAnswerMode.CHOICE:
            rows.extend(u.button(option, lambda widget, value=option: self._on_answer(value), text_role="study")
                        for option in model.options)
            rows.append(u.button(model.reveal_caption, lambda widget: self._on_reveal()))
        elif model.mode is DeckAnswerMode.TEXT:
            self.field = u.toga.TextInput(placeholder=model.input_hint, style=u.text_style(font_size=18, margin_bottom=8))
            rows.extend([self.field,
                         u.button(model.submit_caption, lambda widget: self._submit(), primary=True),
                         u.button(model.reveal_caption, lambda widget: self._on_reveal())])
        else:
            self._self_check.add(u.button(model.show_answer_caption, lambda widget: self._show_answer(), primary=True))
            rows.append(self._self_check)
        rows.append(u.button(model.leave_caption, lambda widget: self._on_leave()))
        return rows

    def _submit(self) -> None:
        self._on_answer(str(self.field.value or ""))

    def _show_answer(self) -> None:
        self._self_check.clear()
        self._self_check.add(_answer(self._model.answer, self._model.answer_cells, size=20),
                             u.button(self._model.self_right_caption, lambda widget: self._on_self_check(True),
                                      primary=True),
                             u.button(self._model.self_wrong_caption, lambda widget: self._on_self_check(False)))


def deck_question_page(model: DeckQuestionModel, *, on_answer: Callable[[str], object],
                       on_reveal: Callable[[], object], on_self_check: Callable[[bool], object],
                       on_leave: Callable[[], object], on_play: Callable[[str], object] | None = None) -> list:
    """One deck question; the caller shows it under model.header."""
    return _QuestionForm(model, on_answer=on_answer, on_reveal=on_reveal, on_self_check=on_self_check,
                         on_play=on_play, on_leave=on_leave).widgets()


def deck_result_page(model: DeckResultModel, *, on_next: Callable[[], object],
                     on_leave: Callable[[], object]) -> list:
    """The result of one deck answer: UI8 result, the answer and its explanation, 下一题 and 结束练习."""
    rows = practice_result_rows(model.result)
    if model.answer_line:
        rows.append(_answer(model.answer_line, model.answer_cells, size=18))
    if model.explanation:
        rows.append(u.label(model.explanation, size=14, color=u.MUTED))
    if model.next_caption:   # none after the last question when the mode cannot serve more (design 12 N13)
        rows.append(u.button(model.next_caption, lambda widget: on_next(), primary=True))
    rows.append(u.button(model.leave_caption, lambda widget: on_leave()))
    return rows


def deck_end_page(model: DeckEndModel, *, on_finish: Callable[[], object]) -> list:
    """The end of a deck."""
    return [u.label(model.title, size=20, bold=True), u.label(model.summary, size=15),
            u.button(model.finish_caption, lambda widget: on_finish(), primary=True)]
