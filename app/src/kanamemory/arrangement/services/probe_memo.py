"""ProbeMemo: a fruitless new-content probe is not repeated on every dispatch (design 12 WP-B3).

Probing for the new-content head costs several milliseconds; under breadth
first it is asked for before nearly every review while new content is behind
its share. When it found nothing, or its head could not be promoted, the
same round is asked again only after RETRY_AFTER (never for the rest of the
round). It also remembers what the current round was re-arranged for
(design 12 B5): new content, or due course reviews, the frozen round could
not show get one re-arrangement, never a second one for the same content or
units; the check for due course reviews outside the round runs at most once
per RETRY_AFTER, and so does a line-share read that found nothing for one
line and unit (design 12 LS5, LS7). In memory only,
one per store object (StoreArrangement); a new round, a clock that moved
back or a new store object forgets the probe; a new store object also
forgets the re-arrangements. No storage, no rule.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# How long "nothing to teach" or a refused promotion is believed within one round (clock time).
RETRY_AFTER = timedelta(seconds=60)


class ProbeMemo:
    """Remembers, for one practice round, that probing for new content is futile for now."""

    def __init__(self):
        self._round: object = None
        self._since: datetime | None = None
        self._until: datetime | None = None
        self._rearranged: set = set()   # identities a round was already re-arranged for
        self._reviews: tuple | None = None   # (round, since, until) of the last check for outside reviews
        self._noted: object = None           # the round whose 深度优先 hold line was shown (G3)
        self._lines: dict = {}               # (unit, line) -> (round, since, until): a futile line-share read (LS5, LS7)

    def first_note(self, round_id) -> bool:
        """True the first time it is asked for round ``round_id`` (never without a round): G3's line once per round."""
        if round_id is None or round_id == self._noted:
            return False
        self._noted = round_id
        return True

    def futile(self, round_id, now: datetime) -> bool:
        """True when a probe at ``now`` in round ``round_id`` would repeat a fruitless one."""
        if self._since is None:
            return False
        if round_id is None or round_id != self._round or now < self._since:
            self.forget()
            return False
        return now < self._until

    def found_nothing(self, round_id, now: datetime) -> None:
        """A probe found no candidate: do not probe again for RETRY_AFTER in this round."""
        self._remember(round_id, now)

    def refused(self, round_id, now: datetime) -> None:
        """The candidate's promotion was refused (after its re-arrangement): RETRY_AFTER as well."""
        self._remember(round_id, now)

    def may_rearrange(self, identities) -> bool:
        """Whether ``identities`` (new content the round cannot show) include one not re-arranged for yet."""
        return bool(set(identities) - self._rearranged)

    def rearranged(self, identities) -> None:
        """The round is re-arranged for ``identities``: never again for them on this store object."""
        self._rearranged.update(identities)

    def reviews_checked_recently(self, round_id, now: datetime) -> bool:
        """True while a check for course reviews outside round ``round_id`` is under RETRY_AFTER old."""
        if self._reviews is None or round_id is None:
            return False
        checked_round, since, until = self._reviews
        return checked_round == round_id and since <= now < until

    def reviews_checked(self, round_id, now: datetime) -> None:
        """A check for course reviews outside the round ran: the next one in this round after RETRY_AFTER."""
        if round_id is not None:
            self._reviews = (round_id, now, now + RETRY_AFTER)

    def line_futile(self, round_id, unit: str, line: str, now: datetime) -> bool:
        """True while a read for ``line`` inside ``unit`` ("new" or "course") found nothing in round ``round_id``
        under RETRY_AFTER ago (design 12 LS5, LS7): the read is not made again and today's order holds."""
        entry = self._lines.get((unit, line))
        if entry is None or round_id is None:
            return False
        checked_round, since, until = entry
        return checked_round == round_id and since <= now < until

    def line_found_nothing(self, round_id, unit: str, line: str, now: datetime) -> None:
        """A read for ``line`` inside ``unit`` found no candidate (or its promotion was refused): not again
        in this round for RETRY_AFTER. Nothing is kept without a round."""
        if round_id is not None:
            self._lines[(unit, line)] = (round_id, now, now + RETRY_AFTER)

    def forget(self) -> None:
        self._round = self._since = self._until = None

    def _remember(self, round_id, now):
        if round_id is None:
            return
        self._round, self._since, self._until = round_id, now, now + RETRY_AFTER
