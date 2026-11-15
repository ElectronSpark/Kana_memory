"""The batch introduction's pages (成批引入: the cards 新项说明 and the quiz 批小测): every fixed text and
the display models (design 13).

Pure: no Toga, no store, no app object. The rows that shared builders make
from the app (the review hint, the card's body, the question's prompt, the
next content) are not part of these models: the outer layer builds them and
hands them to the page.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..answer_result_ui import shown_answer, shown_explanation
from ..learning_questions import KIND_LABELS, READING_CHOICE_KIND
from ..next_step.display import HOME, WAIT_PAGE
from ..question_support import learning_text_role
from .batch_model import BatchAction, BatchActionKind
from .display import KIND_SETTINGS

CARD_PAGE = "learning_card"
QUIZ_PAGE = "learning_batch_quiz"
REVIEW_HINT = "有到期复习，建议先复习"
QUIZ_PAUSED = "未完成题目已保留，重新开启对应题型后可继续。"
CARD_BUTTON = "说明卡"
ABILITY_NAMES = {"understanding": "理解", "reading": "读法", "listening": "听辨", "use": "使用",
                 "application": "应用"}
KIND_CUES = {READING_CHOICE_KIND: "先选读音", "vocab_reading": "写出读法"}
# 说明卡暂不可用
CARD_UNAVAILABLE_TITLE = "说明卡暂不可用"
CARD_UNAVAILABLE_TEXT = "这一项的说明已变化，原有进度已保存。"
# 新项说明
CARD_TITLE = "新项说明"
CARD_POSITION = "第 {number} / {total} 项"
CARD_NEXT = "下一项"
CARD_KNOWN = "我已经会了"
CARD_LATER = "稍后再学"
# 批小测暂不可用
QUIZ_UNAVAILABLE_TITLE = "批小测暂不可用"
QUIZ_UNAVAILABLE_TEXT = "说明卡进度已保存。"
NO_QUIZ_KIND = "这一批里有内容暂时没有可用的小测题型，请检查题型设置。"
# 批小测
QUIZ_TITLE = "批小测"
QUIZ_SETTLED_TEXT = "本批小测已完成。"
CONTINUE = "继续"
# 新能力暂不可用
ABILITY_UNAVAILABLE_TITLE = "新能力暂不可用"
ABILITY_UNAVAILABLE_TEXT = "原有进度已保存。"


def ability_hint(question):
    """One line for the first formal question of a later ability."""
    ability = ABILITY_NAMES.get(question.get("ability"), "练习")
    return f"新能力：{ability} — {KIND_CUES.get(question.get('kind'), KIND_LABELS.get(question.get('kind'), '练习'))}"


@dataclass(frozen=True)
class BatchButton:
    """A button of the batch introduction's pages: its caption and what it does."""

    caption: str
    action: BatchAction


@dataclass(frozen=True)
class CardUnavailableModel:
    """The page 说明卡暂不可用."""

    page: str
    title: str
    text: str
    home: BatchButton


@dataclass(frozen=True)
class CardPageModel:
    """The page 新项说明 of one card (the review hint and the card's body are handed-in rows)."""

    page: str
    title: str
    position: str
    next: BatchButton
    known: BatchButton
    later: BatchButton


@dataclass(frozen=True)
class QuizUnavailableModel:
    """The page 批小测暂不可用."""

    page: str
    title: str
    text: str
    settings: BatchButton
    home: BatchButton


@dataclass(frozen=True)
class QuizSettledModel:
    """The page 批小测 once every item is settled."""

    page: str
    title: str
    text: str
    finish: BatchButton


@dataclass(frozen=True)
class QuizPageModel:
    """The page 批小测 with one question (the review hint and the prompt are handed-in rows)."""

    page: str
    title: str
    heading: str
    question: dict                     # for the cue above the choices, which the page builds
    known: object                      # the ids a sentence of this batch may use
    role: str                          # the text role of the choices
    choices: tuple[BatchButton, ...]   # in the question's order


@dataclass(frozen=True)
class QuizResultModel:
    """What the quiz page shows under its choices after an answer."""

    correct: bool
    answer: str | None          # the right answer as a result shows it, when the answer was wrong
    answer_role: str | None     # its text role
    explanation: str | None     # the question's explanation, when it has one
    card_heading: str | None    # the line above the card shown again, when the answer was wrong
    proceed: BatchButton        # 继续


@dataclass(frozen=True)
class AbilityUnavailableModel:
    """The page 新能力暂不可用 (the next content is a handed-in row)."""

    page: str
    title: str
    text: str
    home: BatchButton


def _button(caption: str, kind: BatchActionKind, **values) -> BatchButton:
    return BatchButton(caption, BatchAction(kind, **values))


def card_unavailable_model() -> CardUnavailableModel:
    """The model of 说明卡暂不可用."""
    return CardUnavailableModel(page=WAIT_PAGE, title=CARD_UNAVAILABLE_TITLE, text=CARD_UNAVAILABLE_TEXT,
                                home=_button(HOME, BatchActionKind.LEAVE))


def card_model(index: int, total: int, entry: dict) -> CardPageModel:
    """The model of the card at position ``index`` (from 0) of ``total``; its two choices carry ``entry``."""
    return CardPageModel(page=CARD_PAGE, title=CARD_TITLE, position=CARD_POSITION.format(number=index + 1, total=total),
                         next=_button(CARD_NEXT, BatchActionKind.CARD_NEXT, entry=entry),
                         known=_button(CARD_KNOWN, BatchActionKind.CARD_KNOWN, entry=entry),
                         later=_button(CARD_LATER, BatchActionKind.LEAVE))


def quiz_unavailable_model() -> QuizUnavailableModel:
    """The model of 批小测暂不可用."""
    return QuizUnavailableModel(page=WAIT_PAGE, title=QUIZ_UNAVAILABLE_TITLE, text=QUIZ_UNAVAILABLE_TEXT,
                                settings=_button(KIND_SETTINGS, BatchActionKind.LEARNING_SETTINGS),
                                home=_button(HOME, BatchActionKind.LEAVE))


def quiz_settled_model() -> QuizSettledModel:
    """The model of 批小测 once every item is settled."""
    return QuizSettledModel(page=QUIZ_PAGE, title=QUIZ_TITLE, text=QUIZ_SETTLED_TEXT,
                            finish=_button(CONTINUE, BatchActionKind.QUIZ_FINISH))


def quiz_model(question: dict, labels: dict, role: str, known) -> QuizPageModel:
    """The model of 批小测 asking ``question``: one button per choice, showing ``labels``' text for it and
    answering with the raw choice and this very question object."""
    return QuizPageModel(
        page=QUIZ_PAGE, title=QUIZ_TITLE, heading=QUIZ_TITLE, question=question, known=known, role=role,
        choices=tuple(_button(labels.get(choice, choice), BatchActionKind.QUIZ_ANSWER, question=question, answer=choice)
                      for choice in question.get("choices", [])))


def quiz_result_model(question: dict, *, correct: bool, done: bool) -> QuizResultModel:
    """What is shown after an answer to ``question``; ``done``: the quiz has no question left."""
    return QuizResultModel(
        correct=correct,
        answer=None if correct else shown_answer(question),
        answer_role=None if correct else learning_text_role(question, "answer"),
        explanation=shown_explanation(question, question["feedback"]) if question.get("feedback") else None,
        card_heading=None if correct else CARD_BUTTON,
        proceed=_button(CONTINUE, BatchActionKind.QUIZ_FINISH if done else BatchActionKind.QUIZ_CONTINUE))


def ability_unavailable_model() -> AbilityUnavailableModel:
    """The model of 新能力暂不可用."""
    return AbilityUnavailableModel(page=WAIT_PAGE, title=ABILITY_UNAVAILABLE_TITLE, text=ABILITY_UNAVAILABLE_TEXT,
                                   home=_button(HOME, BatchActionKind.LEAVE))
