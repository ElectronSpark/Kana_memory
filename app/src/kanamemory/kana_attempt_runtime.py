"""The attempt of the kana practice line on the app side: the evidence and the marks the app keeps (design 13).

What happened in the attempt on the page is one KanaAttempt on the app, reached
only through this module. It is replaced, never edited, when a field of it is
assigned (assign, set_attempt and the set_* functions), so a practice page kept
for Back holds the attempt of its own moment and Back restores it whole. A
field that was never set since the app started is UNSET: the readers below
raise AttributeError for it, as the attribute access they replace did, and
attempt(app).get(name, default) gives a default, as getattr with a default did.

The marks of the session's attempts (which saw help, which were submitted) are
one AttemptMarks on the app, reached through marks(app). They are facts of the
attempts: no page binding names them, so Back does not roll them back. The two
sets are changed in place, and replaced where the line always replaced them.

This module reads the app object and imports neither Toga nor the control
components nor the app module.
"""
from __future__ import annotations

from dataclasses import replace

from .kana_line.attempt import AttemptMarks, KanaAttempt


# --- the evidence of the attempt on the page ----------------------------------------------------------------------

def attempt(app) -> KanaAttempt:
    """The attempt on the page (one with nothing set before the first question)."""
    value = getattr(app, "_kana_attempt", None)
    return value if isinstance(value, KanaAttempt) else KanaAttempt()


def set_attempt(app, value: KanaAttempt) -> None:
    app._kana_attempt = value


def assign(app, **fields) -> None:
    """Assign fields of the attempt: the app gets a NEW attempt with them (the one before is left as it was,
    for whatever page still holds it)."""
    app._kana_attempt = replace(attempt(app), **fields)


def _read(app, name: str):
    return attempt(app).need(name)


def revealed(app):
    """Whether this attempt saw the answer or a hint."""
    return _read(app, "revealed")


def set_revealed(app, value) -> None:
    assign(app, revealed=value)


def answered_text(app):
    """The submitted answer, None before a submit."""
    return _read(app, "answered_text")


def set_answered_text(app, value) -> None:
    assign(app, answered_text=value)


def correct(app):
    """The verdict of the submitted answer."""
    return _read(app, "correct")


def set_correct(app, value) -> None:
    assign(app, correct=value)


def audio_ok(app):
    """Whether a heard question was played to its end (true for a question that is not heard)."""
    return _read(app, "audio_ok")


def set_audio_ok(app, value) -> None:
    assign(app, audio_ok=value)


def started(app):
    """The monotonic time at which the question page was drawn."""
    return _read(app, "started")


def set_started(app, value) -> None:
    assign(app, started=value)


def submitted_at(app):
    """The UTC time of the first submit as ISO text, None before it."""
    return _read(app, "submitted_at")


def set_submitted_at(app, value) -> None:
    assign(app, submitted_at=value)


def duration_ms(app):
    """Milliseconds from the drawing of the page to the first submit."""
    return _read(app, "duration_ms")


def set_duration_ms(app, value) -> None:
    assign(app, duration_ms=value)


# --- the marks of the session's attempts ---------------------------------------------------------------------------

def marks(app) -> AttemptMarks:
    """The marks of the session's attempts (AttributeError when the app object has none, as the two sets were)."""
    return app._kana_marks


def new_marks(app) -> AttemptMarks:
    """Give the app empty marks (the app's start)."""
    app._kana_marks = AttemptMarks()
    return app._kana_marks


def assisted(app) -> set:
    """The attempts that saw help; the line's own set."""
    return marks(app).assisted


def set_assisted(app, value) -> None:
    marks(app).assisted = value


def answered(app) -> set:
    """The attempts already submitted; the line's own set."""
    return marks(app).answered


def set_answered(app, value) -> None:
    marks(app).answered = value
