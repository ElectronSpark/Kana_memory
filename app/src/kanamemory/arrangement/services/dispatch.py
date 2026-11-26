"""ShareDispatcher: which kind of work goes next under the breadth-first orientation (design 12 B2-B4).

Application layer without storage: it joins the learner's preferences, the
study clock and the share rules, and asks a CandidateSource for the heads.
The new-content head is expensive to find, so it is asked for only when it
could be chosen: when no review is waiting, when new content would go before
the review units that have a candidate (shares.prefer_new over those units),
or when the focus scope leaves no review candidate inside it.

Recent course states' due reviews and consolidation decks never reach this
rule (design 12 N4 revised): they are served before it.
"""
from __future__ import annotations

from datetime import datetime

from ..model import Decision, LineUsage, Orientation, Unit, Usage
from ..ports import CandidateSource, EventSource
from ..rules import clock, lines, shares
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

    def line_usage(self, now: datetime) -> LineUsage:
        """Charged seconds per (unit, study line) over the line window that ends at ``now`` (design 12 LS3)."""
        return lines.line_usage(self._events.clock_events(now), now)

    def line_behind(self, now: datetime, *, breadth: bool | None = None) -> str | None:
        """The line behind inside new content by the default weights (design 12 LS8), or None.

        None unless the orientation is breadth first, and when both lines
        have used the same time per weight (also both none). ``breadth``: the
        caller already knows the orientation (no settings read); None reads it.
        """
        if not (self.active() if breadth is None else breadth):
            return None
        return lines.behind(self.line_usage(now), Unit.NEW)

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
