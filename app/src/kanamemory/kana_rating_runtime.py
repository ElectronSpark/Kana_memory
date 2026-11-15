"""Rating a kana practice answer on the app side: the service built from the app, and what follows a rating
(design 13).

rate(app, K, rating) is the rating of the answer on the result page, after the page's own guards: it builds the
rating service (kana_line.rating) over the app's store, hands it the question, the position and the attempt as
the app holds them at that moment, alerts when nothing was saved, and after a saved rating reads the app
again: the position moves on, the queue is read again where the rating asks for it, and the next question is
dispatched.

This module reads the app object and imports neither Toga nor the control components nor the app module: the
App class arrives as ``K`` from the entry method, and each of its helpers is looked up on it at the moment it
is called, so a helper replaced on the class reaches this code. The store is the one the app holds when the
service is built.
"""
from __future__ import annotations

from . import kana_attempt_runtime as kana_attempt
from . import kana_session_runtime as kana_session
from .kana_line import rating_model
from .kana_line.rating import KanaRating
from .kana_line.rating_model import Refused


def _now():
    from .domain import utc_now
    return utc_now()


def _fingerprint(question):
    from .session_content import fingerprint
    return fingerprint(question)


def _record_error(*args):
    from .diagnostic_events import record_error
    return record_error(*args)


def service(app, K) -> KanaRating:
    """The rating service over this app's store, built for one call (nothing is kept)."""
    return KanaRating(
        app.store, now=_now, fair_new=lambda: K._lazy_practice_questions(app), fingerprint=_fingerprint,
        word_plan=lambda answer: K._word_spelling_plan(app, answer),
        set_kind_policy=lambda session_id, kinds: K._set_queue_kind_policy(app, session_id, kinds),
        word_submission=lambda answer: K._word_romaji_submission(app, answer),
        record_error=_record_error)


def rate(app, K, rating) -> None:
    """Save the rating of the answer on the result page and go on; alert and stay when nothing was saved."""
    question = kana_session.question(app)
    rated = service(app, K).rate(question, kana_session.session(app), kana_attempt.attempt(app), rating)
    if isinstance(rated, Refused):
        app.alert(rated.message)
        return
    # What follows is decided by what the practice holds now, after the record was written.
    question = kana_session.question(app)
    kana_session.set_session(app, rating_model.advanced(kana_session.session(app), kana_attempt.attempt(app), question))
    if rating_model.reread_queue(question, rated.result):
        kana_session.set_questions(app, (app.store.get_session_questions(kana_session.session_id(app), lazy=True)
                                         if K._lazy_practice_questions(app) else
                                         app.store.get_session_questions(kana_session.session_id(app))))
    K._dispatch_after_answer(app)
