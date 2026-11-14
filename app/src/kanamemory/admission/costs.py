"""AdmissionCosts: the two caches of the add page's workload line, for one learner store (design 13 S2).

Formerly two attributes of the app object (_question_plan_preview and
_admission_existing_work), with the same keys and the same invalidation. The
rule of 07 holds: a new input either writes the learner database (and so
changes the token) or joins the key. Nothing is kept inside an open write: a
rollback would leave the same revision numbers with other data behind them.
"""
from __future__ import annotations

from collections.abc import Callable, Hashable
from datetime import datetime, timedelta


class AdmissionCosts:
    """The question-plan preview (shared with the home counts) and the existing work of one store."""

    def __init__(self, store):
        self._store = store
        self._plans = None      # (token, {content id: plans}) of course_planning.preview_question_plans
        self._existing = None   # ((token, *key), now, value) of admission_workload's existing work

    def token(self) -> tuple | None:
        """Changes with every write to the learner database (own or another connection's); None: do not keep."""
        store = self._store
        if getattr(store, "in_transaction", True) is not False or not callable(getattr(store, "read_revision", None)):
            return None
        return (id(store), *store.read_revision())

    def plan_preview(self) -> dict | None:
        """The dict of course_planning.preview_question_plans for the database as it is now; None: keep nothing.

        One dict per database token: the home counts and the add page's
        existing work share the plans of the lessons that lack a question,
        until any write.
        """
        token = self.token()
        if token is None:
            return None
        if self._plans is None or self._plans[0] != token:
            self._plans = (token, {})
        return self._plans[1]

    def existing_work(self, key: tuple[Hashable, ...], now: datetime, read: Callable[[], dict], *,
                      seconds: float) -> dict:
        """The value ``read`` gives, kept ``seconds`` while the database and ``key`` stay the same.

        A kept value serves a call whose ``now`` is 0 to ``seconds`` after
        the call that read it; the saved times are absolute, so the caller
        counts them against its own ``now``.
        """
        token = self.token()
        full = (token, *key)
        kept = self._existing
        if (token is not None and kept is not None and kept[0] == full
                and timedelta(0) <= now - kept[1] <= timedelta(seconds=seconds)):
            return kept[2]
        value = read()
        self._existing = (full, now, value) if token is not None else None
        return value
