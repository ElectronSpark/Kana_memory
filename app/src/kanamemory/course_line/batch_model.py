"""What the batch introduction (成批引入) answers and what a button of its pages does (design 13).

The service (course_line.batch) returns these values; the app side performs
them. Pure: no Toga, no store, no app object; store-side values are typed
``object`` or ``dict``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# --- what the batch does next (BatchIntroduction.advance) ----------------------------------------------------

@dataclass(frozen=True)
class BatchOver:
    """No batch is open: the line goes on."""


@dataclass(frozen=True)
class BatchClosed:
    """Every card was "我已经会了": the empty quiz was started and the batch completed (both are written);
    the line goes on."""


@dataclass(frozen=True)
class CardStep:
    """The card at the saved cursor."""

    entry: dict   # the batch's entry of that card (content_id, introduction_id, ...)


@dataclass(frozen=True)
class QuizStep:
    """The quiz."""


# --- opening a card (BatchIntroduction.open_card) ------------------------------------------------------------

@dataclass(frozen=True)
class CardUnavailable:
    """The item is gone, was introduced anew or may not be taught: nothing was written."""


@dataclass(frozen=True)
class CardOpened:
    """The card is shown from now on: its exposure is recorded."""

    item: dict    # the store's answer to the exposure
    entry: dict   # a copy of the entry, as the page keeps it
    index: int    # the card's position in the batch, from 0
    total: int    # how many cards the batch has


# --- opening the quiz (BatchIntroduction.open_quiz) ----------------------------------------------------------

@dataclass(frozen=True)
class QuizUnavailable:
    """The quiz could not be started; nothing was written."""

    message: str


@dataclass(frozen=True)
class QuizSettled:
    """Every item of the quiz is settled: the page 本批小测已完成."""

    item: object   # the quiz's last item


@dataclass(frozen=True)
class QuizPaused:
    """The quiz's frozen head is of a closed kind: it is neither skipped nor answered."""

    item: object   # the head's item (None when the kind was found closed at the answer)


@dataclass(frozen=True)
class QuizAsked:
    """The quiz's frozen head, to be asked."""

    item: dict
    question: dict
    known: object   # the ids a sentence of this batch may use (one read per page)
    labels: dict    # what each choice shows (display only: the answer stays the raw choice)
    role: str       # the text role of the choices


# --- answering (BatchIntroduction.answer) --------------------------------------------------------------------

@dataclass(frozen=True)
class QuizStale:
    """The store refused the answer: the saved state decides what is shown."""


@dataclass(frozen=True)
class QuizAnswered:
    """The answer is recorded."""

    correct: bool


# --- help on a formal question of a batch item (BatchIntroduction.question_help) ----------------------------

@dataclass(frozen=True)
class BatchHelp:
    hint: str | None   # the one line for the first question of a later ability, else None
    card: bool         # whether the card may be opened from the question (an early question)


# --- the pages' buttons ----------------------------------------------------------------------------------------

class BatchActionKind(Enum):
    """What a button of the batch introduction's pages does; BatchAction's docstring names the call of each."""

    CARD_NEXT = "card_next"
    CARD_KNOWN = "card_known"
    LEAVE = "leave"
    LEARNING_SETTINGS = "learning_settings"
    QUIZ_ANSWER = "quiz_answer"
    QUIZ_CONTINUE = "quiz_continue"
    QUIZ_FINISH = "quiz_finish"


@dataclass(frozen=True)
class BatchAction:
    """What one button of the batch introduction's pages does, with the values of its drawing.

    The outer layer makes the call when the button is pressed; "button" is the pressed control:
    CARD_NEXT          choose_intro_card("next", expected=entry)
    CARD_KNOWN         choose_intro_card("known", expected=entry)
    LEAVE              leave_learning(button)
    LEARNING_SETTINGS  show_learning_settings(button)
    QUIZ_ANSWER        the answer ``answer`` to ``question`` — the question object of the drawing: an answer
                       counts only while that very object is the one on the page
    QUIZ_CONTINUE      show_intro_batch(button)        (the next question, or whatever the batch does next)
    QUIZ_FINISH        complete the batch when its quiz is settled, then hand the line back
    """

    kind: BatchActionKind
    entry: dict | None = None      # CARD_NEXT, CARD_KNOWN: the entry of the drawing
    question: dict | None = None   # QUIZ_ANSWER: the question of the drawing
    answer: str | None = None      # QUIZ_ANSWER: the raw choice
