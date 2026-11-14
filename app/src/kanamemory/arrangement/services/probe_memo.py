"""ProbeMemo: a fruitless new-content probe is not repeated on every dispatch (design 12 WP-B3).

Probing for the new-content head costs several milliseconds; under breadth
first it is asked for before nearly every review while new content is behind
its share. When it found nothing, or its head could not be promoted, the
same round is asked again only after a while. In memory only, one per store
object (StoreArrangement); a new round, a clock that moved back or a new
store object forgets it. No storage, no rule.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# How long "nothing to teach" is believed within one round (clock time).
RETRY_AFTER = timedelta(seconds=60)


class ProbeMemo:
    """Remembers, for one practice round, that probing for new content is futile for now."""

    def __init__(self):
        self._round: object = None
        self._since: datetime | None = None
        self._until: datetime | None = None   # None with _since set: the rest of the round

    def futile(self, round_id, now: datetime) -> bool:
        """True when a probe at ``now`` in round ``round_id`` would repeat a fruitless one."""
        if self._since is None:
            return False
        if round_id is None or round_id != self._round or now < self._since:
            self.forget()
            return False
        return self._until is None or now < self._until

    def found_nothing(self, round_id, now: datetime) -> None:
        """A probe found no candidate: do not probe again for RETRY_AFTER in this round."""
        self._remember(round_id, now, now + RETRY_AFTER)

    def refused(self, round_id, now: datetime) -> None:
        """The candidate's promotion was refused: do not probe again in this round."""
        self._remember(round_id, now, None)

    def forget(self) -> None:
        self._round = self._since = self._until = None

    def _remember(self, round_id, now, until):
        if round_id is None:
            return
        self._round, self._since, self._until = round_id, now, until
