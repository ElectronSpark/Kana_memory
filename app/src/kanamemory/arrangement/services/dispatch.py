"""ShareDispatcher: which kind of work goes next under the breadth-first orientation (design 12 B2-B4).

Application layer without storage: it joins the learner's preferences, the
study clock and the share rules, and asks a CandidateSource for the heads.
The new-content head is expensive to find, so it is asked for only when it
could be chosen: when no review is waiting, when new content would go before
the review units that have a candidate (shares.prefer_new over those units),
or when the focus scope leaves no review candidate inside it.
"""
from __future__ import annotations

from datetime import datetime

from ..model import Decision, Orientation, Usage
from ..ports import CandidateSource, EventSource
from ..rules import clock, shares
from .preferences import ArrangementPreferences


class ShareDispatcher:
    """Decides between new content, course review and kana practice by time share."""

    def __init__(self, preferences: ArrangementPreferences, events: EventSource):
        self._preferences = preferences
        self._events = events

    def active(self) -> bool:
        """True when the learner chose breadth first; otherwise the caller keeps the rule it had."""
        return self._preferences.orientation() is Orientation.BREADTH

    def usage(self, now: datetime) -> Usage:
        """Charged seconds per unit over the sliding window that ends at ``now``."""
        return clock.window_usage(self._events.clock_events(now), now)

    def today(self, now: datetime, day: str) -> Usage:
        """Charged seconds per unit on one study day, for display."""
        return clock.day_usage(self._events.clock_events(now), day)

    def decide(self, source: CandidateSource, now: datetime) -> Decision:
        """The candidate that goes next, probing the new-content head only when it could win."""
        weights = shares.weights_for(self._preferences.focus())
        usage = self.usage(now)
        reviews = tuple(source.review_candidates())
        could_win = (not reviews or shares.prefer_new(usage, weights, (c.unit for c in reviews))
                     or not any(candidate.in_scope for candidate in reviews))
        new = source.new_candidate() if could_win else None
        return shares.decide(usage, weights, (*reviews, *((new,) if new is not None else ())))
