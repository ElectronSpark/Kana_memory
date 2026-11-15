"""The course question being answered: its session and what the answering service answers (design 13).

Pure: no Toga, no store, no app object; store-side values are typed ``dict`` or ``object``.
"""
from __future__ import annotations

from dataclasses import dataclass, field


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
