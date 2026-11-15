"""The result of a course answer (作答结果) and its detail page: every fixed text and the display models
(design 13).

Pure: no Toga, no store, no app object. What shared builders make from the
app (the answer's control with its speaker, the learner's notes, the rows of
the detail page) is not part of these models: the outer layer builds it and
hands it to the page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .answer_model import AnswerAction, AnswerActionKind

FEEDBACK_PAGE = "learning_feedback"
FEEDBACK_TITLE = "作答结果"
DETAIL_PAGE = "learning_feedback_detail"
DETAIL_TITLE = "详细"
# What a submit is refused with, under the answer controls
NEED_AUDIO = "请先成功播放题目。"
NEED_ANSWER = "请先作答。"
NEED_COMPLETE = "请完成作答。"
BUSY_SUBMIT = "数据库正忙，答案尚未提交，请稍后再点提交。"
BUSY_RATING = "数据库正忙，评级尚未保存。请稍后再点一次。"
# 作答结果
NEEDS_TEACHING = "先补学这项内容，再安排独立练习。"
STRUCTURE_WORD = "句末词块后还有内容；本次不调整单词复习。"
STRUCTURE_OTHER = "句末词块后还有内容；本次不调整复习。"
SPELLING_UNRESOLVED = "本次不调整读音复习。"
ASK_AI = "问 AI"
DETAIL = "详细"
SAVE_AND_CONTINUE = "保存并继续"
RATINGS = (("再学一次", "again"), ("有些困难", "hard"), ("记住了", "good"), ("很轻松", "easy"))
# 详细
BACK_TO_RESULT = "返回结果"
# How the answer of a result page is shown
ANSWER_LABEL = "label"            # the answer as text
ANSWER_COMPARISON = "comparison"  # the right spelling against the entered one
ANSWER_SEQUENCE = "sequence"      # the learner's accepted pitch contour
ANSWER_PITCH = "pitch"            # the pitch diagram, revealed

_SELF_GLOSS = re.compile(r"(?P<word>[^（）：:]+)（(?P<reading>[^（）]+)）[：:]\s*(?P<gloss>.+?)[。．.]?")


def without_self_gloss(text):
    """'X（读音）：X' whose gloss only repeats the headword -> 'X（读音）'.

    A display rule for the feedback explanation: other lines, and a gloss
    that differs from the headword, are returned unchanged.
    """
    lines = []
    for line in str(text or "").splitlines():
        match = _SELF_GLOSS.fullmatch(line.strip())
        if match and match["gloss"].strip() == match["word"].strip():
            line = f"{match['word'].strip()}（{match['reading']}）"
        lines.append(line)
    return "\n".join(lines)


@dataclass(frozen=True)
class AnswerButton:
    """A button of the result pages: its caption and what it does."""

    caption: str
    action: AnswerAction


@dataclass(frozen=True)
class FeedbackModel:
    """The page 作答结果 of one answer. A text that is None is not shown."""

    page: str
    title: str
    question: dict                  # the question answered (the builders of the handed-in rows read it)
    result: dict                    # the result as this page shows it (the verdict is drawn from it)
    structure_error: object         # what the verdict says about the order of the blocks, or None
    answer_kind: str                # ANSWER_LABEL, ANSWER_COMPARISON, ANSWER_SEQUENCE or ANSWER_PITCH
    answer_text: str | None         # ANSWER_LABEL: the answer as a result shows it
    answer_role: str                # the text role of the answer and of the entered answer
    entered: object                 # what the learner entered, as stored
    asked: str | None               # the Japanese the question was about, when the page would not show it
    asked_role: str | None
    needs_teaching: str | None
    entered_shown: str | None       # the entered answer as its row shows it
    diagnosis: str | None           # what the pitch judge says about the entered answer
    explanation: str | None
    tutor: AnswerButton
    detail: AnswerButton
    unresolved: str | None          # why this answer does not count
    ratings: tuple[AnswerButton, ...] | None   # 再学一次, 有些困难, 记住了, 很轻松 — or None: only ``save``
    save: AnswerButton | None       # 保存并继续


@dataclass(frozen=True)
class DetailModel:
    """The page 详细 of the answer just shown (everything between its title and its button is handed-in rows)."""

    page: str
    title: str
    item_title: str
    title_role: str
    back: AnswerButton


def detail_model(item: dict) -> DetailModel:
    """The model of 详细 for the lesson ``item`` of the question answered."""
    return DetailModel(page=DETAIL_PAGE, title=DETAIL_TITLE, item_title=item["title"],
                       title_role="study" if item["family"] == "word" else "ui",
                       back=AnswerButton(BACK_TO_RESULT, AnswerAction(AnswerActionKind.BACK)))
