"""The one place tests reach the state of the kana practice line (design 13 S6).

Tests read and set what the practice line keeps through these functions, never through the app object's own
attributes, so the line can keep its state where it likes. First part: the evidence of one attempt — whether
the answer or a hint was seen, the submitted text and its verdict, whether a heard question was played to its
end, when the question page was drawn and when the answer was submitted — and the two sets that mark the
session's attempts as assisted or answered. An attempt is named by ``(session id, question index)``.
"""
from __future__ import annotations


# --- the evidence of the attempt on the page ----------------------------------------------------------------------

def answer_revealed(app):
    """Whether this attempt saw the answer or a hint (it then counts as assisted)."""
    return app.answer_revealed


def set_answer_revealed(app, value):
    app.answer_revealed = value


def answered_text(app):
    """The submitted answer, None before a submit."""
    return app.answered_text


def set_answered_text(app, value):
    app.answered_text = value


def answer_correct(app):
    """The verdict of the submitted answer."""
    return app.answer_correct


def set_answer_correct(app, value):
    app.answer_correct = value


def question_audio_ok(app):
    """Whether the question's sound was played to its end (always true for a question that is not heard)."""
    return app.question_audio_ok


def set_question_audio_ok(app, value):
    app.question_audio_ok = value


def question_started(app):
    """The monotonic time at which the question page was drawn."""
    return app._question_started


def set_question_started(app, value):
    app._question_started = value


def answer_submitted_at(app):
    """The UTC time of the first submit as ISO text, None before it."""
    return app._answer_submitted_at


def set_answer_submitted_at(app, value):
    app._answer_submitted_at = value


def answer_duration_ms(app):
    """Milliseconds from the page's drawing to the first submit."""
    return app._answer_duration_ms


def set_answer_duration_ms(app, value):
    app._answer_duration_ms = value


# --- the marks of the session's attempts ---------------------------------------------------------------------------

def assisted_attempts(app):
    """The set of attempts that saw help; the line's own set, changed in place."""
    return app._assisted_attempts


def set_assisted_attempts(app, value):
    app._assisted_attempts = value


def answered_attempts(app):
    """The set of attempts already submitted; the line's own set, changed in place."""
    return app._answered_attempts


def set_answered_attempts(app, value):
    app._answered_attempts = value


# --- hand-made app objects ------------------------------------------------------------------------------------------

_SETTERS = {
    "answer_revealed": set_answer_revealed, "answered_text": set_answered_text, "answer_correct": set_answer_correct,
    "question_audio_ok": set_question_audio_ok, "question_started": set_question_started,
    "answer_submitted_at": set_answer_submitted_at, "answer_duration_ms": set_answer_duration_ms,
    "assisted_attempts": set_assisted_attempts, "answered_attempts": set_answered_attempts,
}


def with_state(app, **fields):
    """A hand-made app object with the given state of the practice line set on it; returns the object."""
    for name, value in fields.items():
        _SETTERS[name](app, value)
    return app


# --- the whole attempt at once, and a kept practice page -----------------------------------------------------------

EVIDENCE = ("answer_revealed", "answered_text", "answer_correct", "question_audio_ok", "question_started",
            "answer_submitted_at", "answer_duration_ms")

_READERS = {
    "answer_revealed": answer_revealed, "answered_text": answered_text, "answer_correct": answer_correct,
    "question_audio_ok": question_audio_ok, "question_started": question_started,
    "answer_submitted_at": answer_submitted_at, "answer_duration_ms": answer_duration_ms,
}

_KEPT = {
    "answer_revealed": "answer_revealed", "answered_text": "answered_text", "answer_correct": "answer_correct",
    "question_audio_ok": "question_audio_ok", "question_started": "_question_started",
    "answer_submitted_at": "_answer_submitted_at", "answer_duration_ms": "_answer_duration_ms",
}


def evidence(app):
    """Everything the practice holds about the attempt on the page, as one dict named like the readers above."""
    return {name: _READERS[name](app) for name in EVIDENCE}


def marks(app):
    """Copies of the two session marks: (assisted attempts, answered attempts)."""
    return set(assisted_attempts(app)), set(answered_attempts(app))


def kept_evidence(**fields):
    """What a practice page kept for Back holds of the attempt of its moment: the entries of its saved bindings
    that give these values (named like the readers above) back to the practice when the learner returns to it."""
    return {_KEPT[name]: value for name, value in fields.items()}
