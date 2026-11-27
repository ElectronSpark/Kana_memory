"""ShareDispatcher: which kind of work goes next under the breadth-first orientation (design 12 B2-B4).

Application layer without storage: it joins the learner's preferences, the
study clock and the share rules, and asks a CandidateSource for the heads.
The new-content head is expensive to find, so it is asked for only when it
could be chosen: when no review is waiting, when new content would go before
the review units that have a candidate (shares.prefer_new over those units),
or when the focus scope leaves no review candidate inside it.

Recent course states' due reviews and consolidation decks never reach this
rule (design 12 N4 revised): they are served before it.

Inside new content and inside course review the time is shared between the
word line and the grammar line (design 12 LS4-LS7): line_turn hands one
dispatch a LineTurn over the learner's line tilt; the three units and their
weights are untouched by it.
"""
from __future__ import annotations

from datetime import datetime

from ..model import Decision, LineUsage, Orientation, Unit, Usage
from ..ports import CandidateSource, EventSource, LessonLines
from ..rules import clock, lines, shares
from .preferences import ArrangementPreferences


class ShareDispatcher:
    """Decides between new content, course review and kana practice by time share."""

    def __init__(self, preferences: ArrangementPreferences, events: EventSource, lessons: LessonLines | None = None):
        self._preferences = preferences
        self._events = events
        self._lessons = lessons   # the enrolled lessons by line (design 12 LS5-LS7); None: no line share

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
        """The line behind inside new content by the learner's line tilt (design 12 LS4, LS8), or None.

        None unless the orientation is breadth first, and when both lines
        have used the same time per weight (also both none). ``breadth``: the
        caller already knows the orientation (no settings read); None reads it.
        """
        if not (self.active() if breadth is None else breadth):
            return None
        return lines.behind(self.line_usage(now), Unit.NEW, self.line_weights())

    def line_weights(self) -> tuple[int, int]:
        """(word, grammar) of the learner's line tilt (one settings read; 正常 when none is stored)."""
        return lines.weights_of(self._preferences.lines())

    def line_turn(self, now: datetime, *, memo=None, round_id=None) -> LineTurn | None:
        """What one breadth-first dispatch needs to share new content and course review by line (LS5-LS7).

        None without a lesson source (the dispatch then is the one without the line share). Nothing is
        read here: the object reads the line usage, the weights and the lessons when first asked.
        ``memo`` (services.probe_memo.ProbeMemo) and ``round_id``: a read for a line that found nothing
        is remembered there for the round and RETRY_AFTER, and not made again meanwhile.
        """
        if self._lessons is None:
            return None
        return LineTurn(lambda: self.line_usage(now), self.line_weights, self._lessons, memo=memo,
                        round_id=round_id, now=now)

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


class LineTurn:
    """The line share of ONE breadth-first dispatch (design 12 LS5-LS7), handed to the new-content probe
    (first_teaching_dispatch.share_probe) and to the course-review step as one small object.

    The probe learns no arrangement internals: it asks the line of a lesson, whether the other line is
    strictly behind (new_turn / review_turn: the other line, or None), that line's enrolled lessons, and
    the word lessons a grammar lesson waits for (waiting). Each read is made at most once, and only
    when asked: the line usage when a line is first compared, the weights with it, the enrolled
    lessons' lines when first needed, the route positions and the untaught lessons only for the guard.
    """

    WORD, GRAMMAR = lines.WORD, lines.GRAMMAR

    def __init__(self, usage, weights, lessons: LessonLines, *, memo=None, round_id=None, now=None):
        # usage(): the LineUsage of new content and course review now; weights(): (word, grammar) of the tilt.
        self._usage_of, self._weights_of, self._lessons = usage, weights, lessons
        self._memo, self._round, self._now = memo, round_id, now
        self._usage: LineUsage | None = None
        self._weights: tuple[int, int] | None = None
        self._lines: dict[str, str] | None = None
        self._by_line: dict[str, frozenset[str]] = {}

    def _map(self) -> dict[str, str]:
        if self._lines is None:
            self._lines = self._lessons.lines()
        return self._lines

    def line_of(self, content_id: str | None) -> str:
        """The line of a lesson; the word line when it is not enrolled (design 12 LS2)."""
        return self._map().get(content_id, lines.WORD)

    def lessons(self, line: str) -> frozenset[str]:
        """The enrolled lessons of ``line``."""
        if line not in self._by_line:
            self._by_line[line] = frozenset(cid for cid, value in self._map().items() if value == line)
        return self._by_line[line]

    def _turn(self, unit: Unit, line: str) -> str | None:
        if self._usage is None:
            self._usage, self._weights = self._usage_of(), self._weights_of()
        other = lines.turn(self._usage, unit, line, self._weights)
        # No read can matter when the other line has no enrolled lesson, nor while a read for it found nothing
        # in this round within RETRY_AFTER (the probe memo): today's order holds then.
        if other is None or not self.lessons(other):
            return None
        if self._memo is not None and self._memo.line_futile(self._round, unit.value, other, self._now):
            return None
        return other

    def found_nothing(self, unit: str, line: str) -> None:
        """The read for ``line`` inside ``unit`` ("new" or "course") found no candidate: remember it in the memo."""
        if self._memo is not None:
            self._memo.line_found_nothing(self._round, unit, line, self._now)

    def new_turn(self, line: str) -> str | None:
        """The other line when it is strictly behind ``line`` inside new content and has enrolled lessons."""
        return self._turn(Unit.NEW, line)

    def review_turn(self, line: str) -> str | None:
        """The other line when it is strictly behind ``line`` inside course review and has enrolled lessons."""
        return self._turn(Unit.COURSE, line)

    def waiting(self, line: str, content_id: str) -> frozenset[str]:
        """Design 12 LS6, the guard: the word lessons a lesson of ``line`` must not overtake.

        For a grammar lesson: the enrolled word lessons that lie BEFORE it on the core route and are not
        taught yet; for a word lesson nothing. The rule is one-sided on purpose: the route puts a grammar
        lesson after the words its questions use, while a word lesson has questions that need no
        sentence; a two-sided guard would always pick the earlier lesson and the share would do nothing.
        A lesson without a position on the route neither guards nor is guarded.
        """
        if line != lines.GRAMMAR:
            return frozenset()
        positions = self._lessons.positions()
        place = positions.get(content_id)
        if place is None:
            return frozenset()
        before = frozenset(cid for cid in self.lessons(lines.WORD) if positions.get(cid, place) < place)
        return before & self._lessons.untaught() if before else frozenset()
