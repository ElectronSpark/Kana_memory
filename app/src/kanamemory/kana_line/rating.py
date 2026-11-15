"""Rating a kana practice answer without a page: the check of the rating, the save, and what failed (design 13).

KanaRating takes the store and, as callables, what only the app side knows: the clock, the fair order of new
items, the fingerprint of a question, the spelling plan of a word answer, the queue's kind policy, and the
romaji parts the learner chose. It takes no app object and imports no Toga.
"""
from __future__ import annotations

from collections.abc import Callable

from ..draft_debounce import is_database_locked
from .attempt import KanaAttempt
from .rating_model import BUSY, NOT_SAVED, ONLY_AGAIN, RETRY, UNSCHEDULED, Rated, Refused, is_word, review_call
from .session import KanaSession


class KanaRating:
    """One rating of the question on the page.

    ``now()``: the time of saving. ``fair_new()``: whether new items take their fair order.
    ``fingerprint(question)``: what the record keeps of the question. ``word_plan(answer)``: (kana rows, analysis,
    corrections) of a word answer. ``set_kind_policy(session id, kinds)``: the kinds the queue goes on with when
    corrections are appended. ``word_submission(answer)``: the romaji parts and their plan when the learner
    chose them part by part, else nothing. ``record_error(area, action, error)``: notes an error for diagnostics.
    """

    def __init__(self, store, *, now: Callable, fair_new: Callable[[], bool], fingerprint: Callable,
                 word_plan: Callable, set_kind_policy: Callable, word_submission: Callable, record_error: Callable):
        self._store = store
        self._now = now
        self._fair_new = fair_new
        self._fingerprint = fingerprint
        self._word_plan = word_plan
        self._set_kind_policy = set_kind_policy
        self._word_submission = word_submission
        self.record_error = record_error

    def rate(self, question, session: KanaSession, attempt: KanaAttempt, rating) -> Refused | Rated:
        """Save the answer with ``rating``.

        The question, the position and the attempt are taken as they are handed in: the record is put together
        from this one moment. A word answer is saved without a rating; an attempt that saw help takes only
        "again" (1). Nothing is saved when the store does not schedule the answer, is busy (the same rating can
        be pressed again) or fails: the answer says so and the position must stay.
        """
        word = is_word(question)
        if word:
            rating = None
        if attempt.need("revealed") and rating != 1 and not word:
            return Refused(ONLY_AGAIN)
        try:
            extra = {}
            finalized_at = self._now()
            if word:
                rows, _, corrections = self._word_plan(attempt.need("answered_text"))
                if corrections:
                    self._set_kind_policy(session.need("session_id"),
                                          corrections[0].metadata["practice_config"]["kinds"])
                extra = {"kana_rows": rows, "spelling_corrections": corrections,
                         **self._word_submission(attempt.need("answered_text"))}
            record = self._store.record_review   # found before its arguments are read, as a call finds it
            call = review_call(question, session, attempt, rating, finalized_at=finalized_at,
                               fair_new=self._fair_new, fingerprint=self._fingerprint)
            result = record(*call.args, **call.kwargs, **extra)
            if not result["scheduled"]:
                return Refused(UNSCHEDULED + result.get("reason", RETRY))
        except Exception as exc:
            if is_database_locked(exc):
                # The transaction rolled back; the same rating can simply be retried.
                self.record_error("practice_rating", "commit", exc)
                return Refused(BUSY)
            return Refused(f"{NOT_SAVED}{exc}")
        return Rated(result)
