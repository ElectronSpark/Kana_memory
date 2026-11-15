"""The one place tests reach the state of the kana practice line (design 13 S6).

Tests read and set what the practice line keeps through these functions, never through the app object's own
attributes, so the line can keep its state where it likes. First part: the evidence of one attempt — whether
the answer or a hint was seen, the submitted text and its verdict, whether a heard question was played to its
end, when the question page was drawn and when the answer was submitted — and the two sets that mark the
session's attempts as assisted or answered. An attempt is named by ``(session id, question index)``.
"""
from __future__ import annotations

import importlib
import sys

from kanamemory import kana_attempt_runtime as _line
from kanamemory import kana_session_runtime as _position
from kanamemory.kana_line.attempt import AttemptMarks, KanaAttempt
from kanamemory.kana_line.session import KanaSession


# --- the evidence of the attempt on the page ----------------------------------------------------------------------

def answer_revealed(app):
    """Whether this attempt saw the answer or a hint (it then counts as assisted)."""
    return _line.revealed(app)


def set_answer_revealed(app, value):
    _line.set_revealed(app, value)


def answered_text(app):
    """The submitted answer, None before a submit."""
    return _line.answered_text(app)


def set_answered_text(app, value):
    _line.set_answered_text(app, value)


def answer_correct(app):
    """The verdict of the submitted answer."""
    return _line.correct(app)


def set_answer_correct(app, value):
    _line.set_correct(app, value)


def question_audio_ok(app):
    """Whether the question's sound was played to its end (always true for a question that is not heard)."""
    return _line.audio_ok(app)


def set_question_audio_ok(app, value):
    _line.set_audio_ok(app, value)


def question_started(app):
    """The monotonic time at which the question page was drawn."""
    return _line.started(app)


def set_question_started(app, value):
    _line.set_started(app, value)


def answer_submitted_at(app):
    """The UTC time of the first submit as ISO text, None before it."""
    return _line.submitted_at(app)


def set_answer_submitted_at(app, value):
    _line.set_submitted_at(app, value)


def answer_duration_ms(app):
    """Milliseconds from the page's drawing to the first submit."""
    return _line.duration_ms(app)


def set_answer_duration_ms(app, value):
    _line.set_duration_ms(app, value)


# --- the marks of the session's attempts ---------------------------------------------------------------------------

def _own_marks(app):
    """The app object's marks; a hand-made object that has none yet gets empty ones."""
    if not isinstance(getattr(app, "_kana_marks", None), AttemptMarks):
        _line.new_marks(app)
    return _line.marks(app)


def assisted_attempts(app):
    """The set of attempts that saw help; the line's own set, changed in place."""
    return _line.assisted(app)


def set_assisted_attempts(app, value):
    _own_marks(app).assisted = value


def answered_attempts(app):
    """The set of attempts already submitted; the line's own set, changed in place."""
    return _line.answered(app)


def set_answered_attempts(app, value):
    _own_marks(app).answered = value


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
    "answer_revealed": "revealed", "answered_text": "answered_text", "answer_correct": "correct",
    "question_audio_ok": "audio_ok", "question_started": "started",
    "answer_submitted_at": "submitted_at", "answer_duration_ms": "duration_ms",
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
    return {"_kana_attempt": KanaAttempt(**{_KEPT[name]: value for name, value in fields.items()})}


# --- the position of the session -----------------------------------------------------------------------------------

def session_id(app):
    """The id of the session whose queue the practice holds, None when it holds none."""
    return _position.session_id(app)


def set_session_id(app, value):
    _position.set_session_id(app, value)


def questions(app):
    """The queue: the session's questions in their order; the practice's own list."""
    return _position.questions(app)


def set_questions(app, value):
    _position.set_questions(app, value)


def question_index(app):
    """The place in the queue of the question on the page (the queue's length once it is finished)."""
    return _position.question_index(app)


def set_question_index(app, value):
    _position.set_question_index(app, value)


def session_right(app):
    """How many answers of the session were right so far."""
    return _position.session_right(app)


def set_session_right(app, value):
    _position.set_session_right(app, value)


def question(app):
    """The question on the page, None when the practice holds none."""
    return _position.question(app)


def set_question(app, value):
    _position.set_question(app, value)


def script(app):
    """The script of the question on the page: hiragana or katakana."""
    return _position.script(app)


def set_script(app, value):
    _position.set_script(app, value)


POSITION = ("session_id", "questions", "question_index", "session_right", "question", "script")

_SETTERS.update({
    "session_id": set_session_id, "questions": set_questions, "question_index": set_question_index,
    "session_right": set_session_right, "question": set_question, "script": set_script,
})


def kept_position(**fields):
    """What a practice page kept for Back holds of the session's position: the entries of its saved bindings that
    give these values (named like the readers above) back to the practice when the learner returns to it."""
    return {"_kana_session": KanaSession(**fields)}


# --- the whole position at once, and what a kept practice page holds of it -----------------------------------------

_POSITION_READERS = {
    "session_id": session_id, "questions": questions, "question_index": question_index,
    "session_right": session_right, "question": question, "script": script,
}


def position(app):
    """Where the practice stands, as one dict named like the readers above: the session, its queue, the place in
    the queue, the right answers so far, the question on the page and its script."""
    return {name: _POSITION_READERS[name](app) for name in POSITION}


def kept_values(page):
    """What a practice page kept for Back holds of the session's position, named like the readers above (only the
    values it holds)."""
    kept = page.bindings.get("_kana_session")
    held = kept.set_fields() if isinstance(kept, KanaSession) else {}
    return {name: held[name] for name in POSITION if name in held}


# --- what the practice line asks of other modules ------------------------------------------------------------------

_ASKED = {
    "summarize_queue_availability": "kanamemory.practice_availability",
    "should_introduce": "kanamemory.kana_learning_intro",
    "intro_wait_until": "kanamemory.kana_learning_intro",
    "current_route_next": "kanamemory.study_path_runtime",
}


def replace_asked(monkeypatch, name, value):
    """Replace one of the functions the practice line asks by name: what a queue can still offer
    (``summarize_queue_availability``), whether a kana is introduced first and until when its introduction
    waits (``should_introduce``, ``intro_wait_until``), the route's next step (``current_route_next``). The
    replacement reaches the line wherever it finds the function: in the module that owns it and in every
    module of the app that took the name from there."""
    current = getattr(importlib.import_module(_ASKED[name]), name)
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("kanamemory") and getattr(module, name, None) is current:
            monkeypatch.setattr(module, name, value)


# --- the typed draft of the answer on the page ---------------------------------------------------------------------

def romaji_answer(app):
    """The romaji typed so far for a kana question."""
    return app.romaji_answer


def set_romaji_answer(app, value):
    app.romaji_answer = value


def word_answer(app):
    """The kana typed so far for a word."""
    return app.word_answer


def set_word_answer(app, value):
    app.word_answer = value


def word_romaji_parts(app):
    """The romaji parts chosen so far for a word that is answered part by part; the practice's own list."""
    return app.word_romaji_parts


def set_word_romaji_parts(app, value):
    app.word_romaji_parts = value


def word_romaji_plan(app):
    """The plan of the parts of a word that is answered part by part."""
    return app.word_romaji_plan


def set_word_romaji_plan(app, value):
    app.word_romaji_plan = value


def word_romaji_unit_mode(app):
    """Whether the word on the page is answered part by part."""
    return app.word_romaji_unit_mode


def set_word_romaji_unit_mode(app, value):
    app.word_romaji_unit_mode = value


def word_conversion_source(app):
    """The romaji typed into the converter of a word question and not yet taken into the answer."""
    return app.word_conversion_source


def set_word_conversion_source(app, value):
    app.word_conversion_source = value


def word_conversion_convention(app):
    """How the converter reads romaji: by reading or by keyboard."""
    return app.word_conversion_convention


def set_word_conversion_convention(app, value):
    app.word_conversion_convention = value


def word_used_conversion(app):
    """Whether the converter showed kana for the question on the page (the answer is then no unaided recall)."""
    return app.word_used_conversion


def set_word_used_conversion(app, value):
    app.word_used_conversion = value


DRAFT = ("romaji_answer", "word_answer", "word_romaji_parts", "word_romaji_plan", "word_romaji_unit_mode",
         "word_conversion_source", "word_conversion_convention", "word_used_conversion")

_SETTERS.update({
    "romaji_answer": set_romaji_answer, "word_answer": set_word_answer,
    "word_romaji_parts": set_word_romaji_parts, "word_romaji_plan": set_word_romaji_plan,
    "word_romaji_unit_mode": set_word_romaji_unit_mode, "word_conversion_source": set_word_conversion_source,
    "word_conversion_convention": set_word_conversion_convention, "word_used_conversion": set_word_used_conversion,
})


def kept_draft(**fields):
    """What a question page kept for Back holds of the typed draft: the entries of its saved bindings that give
    these values (named like the readers above) back to the practice when the learner returns to it."""
    return dict(fields)
