"""The course question being answered: its session and what the answering service answers (design 13).

Pure: no Toga, no store, no app object; store-side values are typed ``dict`` or ``object``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(eq=False)
class AnswerSession:
    """The state of the course question on the page: the one being answered or, on its result page, the one
    just answered.

    The app side keeps one of these and REPLACES it whenever a field is assigned; it never edits one in
    place. The draft dict and the lists inside are the line's own and are changed in place exactly where the
    line always changed them. So a page kept for Back holds the session of its own moment, with the same draft
    and lists inside.

    ``state``, ``parts``, ``used`` and ``audio_ok`` are None until a question page first sets them (``used``:
    only a question answered with blocks does).
    """

    question: dict | None = None      # the snapshot being answered or just answered
    state: dict | None = None         # its draft: what was typed or chosen so far and the aids used
    answer: str = ""                  # the text the answer field starts with
    parts: list | None = None         # the blocks chosen so far, in order
    used: list | None = None          # the positions of the offered blocks the chosen ones use
    audio_ok: bool | None = None      # the question's audio has played successfully
    prompt_known: tuple = (None, None)                                # (the question whose prompt the page drew, the known ids it was drawn with or None)
    feedback_view: tuple = field(default_factory=lambda: (None, {}))  # (question id, the result as its page showed it)


# --- opening a question (CourseAnswering.settle_open) -------------------------------------------------------

@dataclass(frozen=True)
class OpenCancelled:
    """A skip interrupted between its two writes was finished now: the line goes on, no result page."""


@dataclass(frozen=True)
class OpenAnswered:
    """The question already has a saved answer: its result page."""

    result: dict


@dataclass(frozen=True)
class OpenAsk:
    """The question is to be answered."""

    session: AnswerSession   # with what its page starts from: the audio mark, the field's text, the blocks


# --- a change of the chosen blocks (CourseAnswering.edit_parts) ---------------------------------------------

@dataclass(frozen=True)
class EditKept:
    """Nothing to change (no block there)."""


@dataclass(frozen=True)
class EditFailed:
    """The draft could not be saved (the error is already noted for diagnostics)."""

    error: Exception


@dataclass(frozen=True)
class EditLeft:
    """The saved question is no longer waiting for an answer: the line goes on."""


@dataclass(frozen=True)
class Edited:
    """The change is saved."""

    draft: dict   # the draft as the store holds it now


# --- the submit (CourseAnswering.grade, CourseAnswering.record) ----------------------------------------------

@dataclass(frozen=True)
class Refused:
    """The answer is not taken; nothing was written."""

    message: str   # what the learner is told under the answer controls


@dataclass(frozen=True)
class Graded:
    """The answer is judged and ready to be recorded."""

    result: dict   # what the judge says
    outcome: str   # what the record calls it: the judge's status, "revealed" or "device_failure"


@dataclass(frozen=True)
class Busy:
    """The database was locked: nothing was written (the error is noted) and the same press can be repeated."""


@dataclass(frozen=True)
class Recorded:
    """The answer is the question's first answer now."""

    result: dict   # the result as the store saved it


# --- the rating (CourseAnswering.rate) -------------------------------------------------------------------------

@dataclass(frozen=True)
class Rated:
    """The question is finished with its rating."""

    item: object   # the lesson of the question as stored now, for the queueing that follows


# --- the result pages' buttons -----------------------------------------------------------------------------------

class AnswerActionKind(Enum):
    """What a button of the result page or its detail page does; AnswerAction's docstring names each call."""

    RATE = "rate"
    TUTOR = "tutor"
    DETAIL = "detail"
    BACK = "back"


@dataclass(frozen=True)
class AnswerAction:
    """What one button of the result pages does, with the values of its drawing.

    The outer layer makes the call when the button is pressed; "button" is the pressed control:
    RATE    finish_learning_question(rating, question_id=question_id)
    TUTOR   open_tutor_question(source="learning", question_id=question_id, after_answer=True)
    DETAIL  learning_feedback_detail()
    BACK    go_back(button)
    """

    kind: AnswerActionKind
    rating: str | None = None        # RATE: "again", "hard", "good" or "easy"
    question_id: str | None = None   # RATE, TUTOR: the question of the drawing
