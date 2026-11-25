"""DeckPresenter: the sentences of the practice deck and of practice-only answers (design 12 UI8, design 13 C2).

Pure. Composes the display models of display.py from the deck's cards and
outcomes; the page only lays them out. A practice answer never shows a
rating: a right answer says 答对了, a miss says 稍后再练一次, and a confirmed miss
that moved the next formal review adds 下次复习提前了一点.
"""
from __future__ import annotations

from ..display import (DECK_ANSWER_PREFIX, DECK_DONE_TITLE, DECK_LEAVE, DECK_MISSES_NOTE, DECK_NEXT,
                       PRACTICE_ADVANCED,
                       PRACTICE_CORRECT,
                       PRACTICE_HEADER, PRACTICE_ONLY_MOVED_NOTE, PRACTICE_ONLY_NOTE, PRACTICE_RETRY, PRACTISED_LINE,
                       DeckEndModel, DeckQuestionModel, DeckResultModel, PracticeResultModel)
from ..model import DeckAnswerMode, DeckCard, DeckOutcome, PracticeEffect

ASK_AGAIN = "再答一次"


class DeckPresenter:
    """Display models of one deck (``note``: the sentence under the first question).

    The default note is the one of the deck at the end of a batch: the misses
    of that batch's practice-only answers, practised once more. ``header`` and
    ``leave_caption``: the page header and the leave button of the question
    and result pages (extra practice: 加练 and 结束加练).
    """

    HEADER = PRACTICE_HEADER

    def __init__(self, note: str = DECK_MISSES_NOTE, *, header: str = PRACTICE_HEADER,
                 leave_caption: str = DECK_LEAVE, last_caption: str = DECK_NEXT):
        """``last_caption``: the way on after the deck's last question (design 12 N13): 再来一组 in extra
        practice, 继续学习 in the consolidation deck; the batch's deck keeps 下一题 (its end page follows)."""
        self._note = note
        self._header, self._leave, self._last = header, leave_caption, last_caption

    def question(self, card: DeckCard, pending: int) -> DeckQuestionModel:
        """The question page; ``pending`` is the number of items still waiting behind it."""
        texts = card.texts
        progress = f"第 {card.number} 题" + (f" · 还有 {pending} 题" if pending else " · 最后一题")
        return DeckQuestionModel(
            header=self._header, progress=progress, prompt=texts.prompt, mode=card.mode, task=texts.task,
            cue=texts.cue,
            note=self._note if card.number == 1 else "",
            options=texts.options if card.mode is DeckAnswerMode.CHOICE else (),
            audio_text=texts.audio_text,
            answer=_answer_line(texts.answer) if card.mode is DeckAnswerMode.SELF_CHECK else "",
            answer_cells=texts.answer_cells if card.mode is DeckAnswerMode.SELF_CHECK else None,
            leave_caption=self._leave)

    def result(self, outcome: DeckOutcome, *, last: bool = False, more: bool = True) -> DeckResultModel:
        """The result page of one deck answer: UI8 result, the answer and its explanation.

        ``last``: nothing of the deck waits behind this answer (design 12 N13): the way on is the deck's
        last caption, and none when ``more`` is False (the mode cannot serve more; its leave button stays).
        """
        texts = outcome.card.texts
        scored = outcome.correct is not None
        if scored and last:
            return DeckResultModel(header=self._header, result=practice_result(outcome.correct, outcome.effect,
                                                                                outcome.message),
                                   answer_line=_answer_line(texts.answer), answer_cells=texts.answer_cells,
                                   explanation=texts.explanation, scored=True, leave_caption=self._leave,
                                   next_caption=self._last if more else None)
        return DeckResultModel(header=self._header, result=practice_result(outcome.correct, outcome.effect,
                                                                            outcome.message),
                               answer_line=_answer_line(texts.answer) if scored else "",
                               answer_cells=texts.answer_cells if scored else None,
                               explanation=texts.explanation if scored else "",
                               scored=scored, leave_caption=self._leave,
                               **({} if scored else {"next_caption": ASK_AGAIN}))

    @staticmethod
    def end(served: int, correct: int) -> DeckEndModel:
        return DeckEndModel(header=PRACTICE_HEADER, title=DECK_DONE_TITLE,
                            summary=f"练习 {served} 题 · 答对 {correct} 题" if served else "没有需要练习的题。")


def practice_result(correct: bool | None, effect: PracticeEffect | None, message: str = "", *,
                    practice_only_note: bool = False) -> PracticeResultModel:
    """UI8 for one practice-only answer (the deck's, or a kana queue answer that is practice only).

    ``message`` (the judge's note) is shown only for an answer it could not
    score; a scored answer's explanation is shown by the page's own line.
    ``practice_only_note``: add the sentence that this answer was practice only
    (on the queue's result page, where a rating would otherwise be offered).
    It says the schedule is unchanged only when no confirmed miss moved it;
    after a move it is followed by 下次复习提前了一点.
    """
    if correct is None:
        return PracticeResultModel("", (message,) if message else (), None)
    moved = effect is PracticeEffect.ADVANCE
    lines = []
    if practice_only_note:
        lines.append(PRACTICE_ONLY_MOVED_NOTE if moved else PRACTICE_ONLY_NOTE)
    if moved:
        lines.append(PRACTICE_ADVANCED)
    return PracticeResultModel(PRACTICE_CORRECT if correct else PRACTICE_RETRY, tuple(lines), correct)


def practised_line(count: int) -> str:
    """The end page's line for a batch's practice-only answers; empty when there were none."""
    return PRACTISED_LINE.format(count=count) if count > 0 else ""


def _answer_line(answer: str) -> str:
    return DECK_ANSWER_PREFIX + answer if answer else ""
