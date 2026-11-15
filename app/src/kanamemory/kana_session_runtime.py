"""The position of the kana practice session on the app side (design 13).

Which queue the practice holds and where the learner is in it — the session id,
the questions, the place of the question on the page, the count of right
answers, that question and its script — is one KanaSession on the app, reached
only through this module. It is replaced, never edited, when a field of it is
assigned (assign, set_session and the set_* functions); the queue inside is the
line's own list. A practice page kept for Back holds the position of its own
moment, and Back gives back the fields that page held (page_restore).

A field that was never set since the app started is UNSET: the readers below
raise AttributeError for it, as the attribute access they replace did, and
session(app).get(name, default) gives a default, as getattr with a default did.

This module reads the app object and imports neither Toga nor the control
components nor the app module.
"""
from __future__ import annotations

from dataclasses import replace

from .kana_line.session import KanaSession


def session(app) -> KanaSession:
    """The position the app holds (one with nothing set before the app's start gave it one)."""
    value = getattr(app, "_kana_session", None)
    return value if isinstance(value, KanaSession) else KanaSession()


def set_session(app, value: KanaSession) -> None:
    app._kana_session = value


def assign(app, **fields) -> None:
    """Assign fields of the position: the app gets a NEW session object with them (the one before is left as it
    was, for whatever page still holds it)."""
    app._kana_session = replace(session(app), **fields)


def kept_attempt_key(bindings) -> tuple:
    """(session id, question index) of the position a kept page's bindings hold; None for what it does not hold."""
    kept = bindings.get("_kana_session")
    if not isinstance(kept, KanaSession):
        return (None, None)
    return (kept.get("session_id"), kept.get("question_index"))


def _read(app, name: str):
    return session(app).need(name)


def session_id(app):
    """The id of the session whose queue is held, None when none is."""
    return _read(app, "session_id")


def set_session_id(app, value) -> None:
    assign(app, session_id=value)


def questions(app):
    """The queue: the session's questions in their order; the line's own list."""
    return _read(app, "questions")


def set_questions(app, value) -> None:
    assign(app, questions=value)


def question_index(app):
    """The place in the queue of the question on the page (the queue's length once it is finished)."""
    return _read(app, "question_index")


def set_question_index(app, value) -> None:
    assign(app, question_index=value)


def session_right(app):
    """How many answers of the session were right so far."""
    return _read(app, "session_right")


def set_session_right(app, value) -> None:
    assign(app, session_right=value)


def question(app):
    """The question on the page, None when the practice holds none."""
    return _read(app, "question")


def set_question(app, value) -> None:
    assign(app, question=value)


def script(app):
    """The script of the question on the page: hiragana or katakana."""
    return _read(app, "script")


def set_script(app, value) -> None:
    assign(app, script=value)
