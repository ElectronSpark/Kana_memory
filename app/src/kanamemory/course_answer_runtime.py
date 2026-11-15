"""Answering a course question (答题) on the app side: the session the app keeps, and the service built from the
app.

The state of the question on the page is one AnswerSession on the app, reached
only through answer_session(app). It is replaced, never edited, when a field
of it is assigned (assign, set_draft, set_session); the draft and the lists
inside are changed in place where the line always changed them. A page kept
for Back therefore holds the session of its own moment.

service(app) builds the answering service (course_line.answer) for one call
from the app's stores. The functions below it are the moments of a question
being answered that do not draw: opening it, an aid, the audio mark.

This module reads the app object and imports neither Toga nor the control
components nor the app module; every page method and line entry is looked up
on the app object at the moment of the call.
"""
from __future__ import annotations

from dataclasses import replace

from .course_line.answer import CourseAnswering
from .course_line.answer_model import AnswerSession, OpenAnswered, OpenCancelled


def answer_session(app) -> AnswerSession:
    """The session of the course question on the page (an empty one before the first question)."""
    return getattr(app, "_course_answer", None) or AnswerSession()


def set_session(app, session: AnswerSession) -> None:
    app._course_answer = session


def assign(app, **fields) -> None:
    """Assign fields of the session: the app gets a NEW session with them (the one before is left as it was,
    for whatever page still holds it)."""
    app._course_answer = replace(answer_session(app), **fields)


def set_draft(app, draft) -> None:
    """The line goes on with ``draft`` for the question on the page."""
    assign(app, state=draft)


def shown_question(app, guided=False):
    """The question an answer control belongs to must still be this one: the guide's question of a teaching
    step (``guided``), else the course question on the page."""
    return getattr(app, "_learning_guide", None) if guided else answer_session(app).question


def _fair_new(app):
    from .new_dispatch import enabled
    return enabled(app)


def _early_submit(app, question_id):
    from .extra_practice_runtime import early_submit
    return early_submit(app, question_id)


def _record_error(*args):
    from .diagnostic_events import record_error
    return record_error(*args)


def service(app) -> CourseAnswering:
    """The answering service over this app's stores, built for one call (nothing is kept)."""
    return CourseAnswering(app.learning_store, app.store, fair_new=lambda: _fair_new(app),
                           early_submit=lambda question_id: _early_submit(app, question_id),
                           record_error=_record_error)


def open_question(app, current) -> bool:
    """Open the question of ``current`` (the course head). True: it is to be answered and the caller draws
    its page; False: the line went on (an interrupted skip was finished) or its result page was shown."""
    answering = service(app)
    set_session(app, answering.begin(answer_session(app), current))
    # Each question starts without an answer field; answer_controls sets one.
    app._learning_input = None
    opened = answering.settle_open(answer_session(app), current)
    if isinstance(opened, OpenCancelled):
        app.continue_added_learning()
        return False
    if isinstance(opened, OpenAnswered):
        app.show_learning_feedback(opened.result)
        return False
    set_session(app, opened.session)
    return True


def assist(app) -> None:
    """An aid is about to be shown (the teaching reference, the card, the answer): the draft is marked assisted
    and saved before anything is shown."""
    answering = service(app)
    session = answering.mark_assisted(answer_session(app))
    set_session(app, session)
    answering.save_draft(session.question["id"], session.state)


def audio_played(app, question) -> None:
    """The audio of ``question`` has played on the question page: the mark is saved, then kept. A failing save
    raises with nothing changed."""
    set_session(app, service(app).audio_played(answer_session(app), question))


def keep_draft(app, question_id, draft) -> None:
    """Save ``draft`` for the question and go on with it (the pitch editor's own draft)."""
    service(app).save_draft(question_id, draft)
    set_draft(app, draft)
