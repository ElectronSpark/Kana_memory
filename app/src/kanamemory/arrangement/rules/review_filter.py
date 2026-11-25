"""The learner's own two switches and the extra-practice part as ONE filter (design 12 H8-H10, E9).

Pure. "May this member be served now" is decided here and only here; every
reader asks it: the course queue's collection, the kana lane's head, the
new-content probe, the consolidation deck, extra practice's pool and the
counts. A member is a lesson content id or a kana entry id
("kana:<id>:<script>", rules/focus.kana_member), the identities of the focus
scope.

- ``excluded``: the members of the scopes paused by 暂不复习 (H9). Exclusion
  wins over everything else.
- ``only``: the members of the focus scope while 只做范围内 is on, the scope
  resolves and the learner has not lifted it (做范围外的, H8); None: no limit.
- ``part``: the members of the extra-practice part (E9) while extra practice
  is on with a part; None: no limit.

Work of priority tiers 0-2 (feedback, a started answer, a due short step) is
served wherever it lies (H8, H9; the focus rule's FIRST_SCOPED_TIER): the
filter acts from tier 3 on. So is ``started`` work (section 22, brief J11 F3):
a lesson the learner started by hand (the route page's start of a section or
lesson), its teaching block or probes and every question of an ability up to
and including that ability's first formal rating; after that the filter
applies to it like to any other work. Nothing here hides due work from the
counts: the home shows what it leaves out apart (apart()).
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from datetime import datetime

from ..model import Part
from .focus import FIRST_SCOPED_TIER
from .recent import is_recent


@dataclass(frozen=True)
class ServeFilter:
    """What 继续学习 / 加练 may serve now; build it with serve_filter() (None there means no filter at all)."""

    excluded: frozenset[str] = frozenset()
    only: frozenset[str] | None = None
    part: frozenset[str] | None = None
    # (lesson, ability) of work started by hand and not yet formally rated; (lesson, None): the whole lesson
    # (one without units, until it is read).
    started: frozenset[tuple[str, str | None]] = frozenset()

    def allows(self, member: str | None) -> bool:
        """Whether work of ``member`` may be served from tier 3 on (None: work that belongs to no member)."""
        if member is None:
            return self.only is None and self.part is None
        if member in self.excluded:
            return False
        if self.only is not None and member not in self.only:
            return False
        return self.part is None or member in self.part

    def started_work(self, member: str | None, ability: str | None = None) -> bool:
        """Whether this is work the learner started by hand that is not formally rated yet (served as tiers 0-2)."""
        return member is not None and ((member, ability) in self.started or (member, None) in self.started)

    def serves(self, member: str | None, tier: int | None, ability: str | None = None) -> bool:
        """allows(), except that tiers 0-2 and started work are always served (``tier`` None: not known)."""
        return ((tier is not None and tier < FIRST_SCOPED_TIER) or self.started_work(member, ability)
                or self.allows(member))

    @property
    def limits_to_scope(self) -> bool:
        """Whether 只做范围内 acts in this filter (the scope resolves and it is not lifted)."""
        return self.only is not None


def serve_filter(*, excluded: Iterable[str] = (), only: Collection[str] | None = None, lifted: bool = False,
                 part: Collection[str] | None = None,
                 started: Iterable[tuple[str, str | None]] = ()) -> ServeFilter | None:
    """The filter of the moment; None when nothing restricts (then every reader behaves as before H8-H10).

    ``only``: the focus scope's members when 只做范围内 is on and the scope
    resolves, else None; ``lifted``: 做范围外的 holds (only then has no
    effect); ``part``: the extra-practice part's members, None for 全部 or
    outside extra practice; ``started``: (lesson, ability) pairs of work
    started by hand and not yet formally rated (ServeFilter.started).
    """
    excluded = frozenset(excluded)
    only = None if lifted or only is None else frozenset(only)
    part = None if part is None else frozenset(part)
    if not excluded and only is None and part is None:
        return None
    return ServeFilter(excluded, only, part, frozenset(started))


def allows(serve: ServeFilter | None, member: str | None) -> bool:
    """ServeFilter.allows for an optional filter (None: everything)."""
    return serve is None or serve.allows(member)


def serves(serve: ServeFilter | None, member: str | None, tier: int | None, ability: str | None = None) -> bool:
    """ServeFilter.serves for an optional filter (None: everything)."""
    return serve is None or serve.serves(member, tier, ability)


@dataclass(frozen=True)
class Apart:
    """Due work the learner's switches leave out, counted apart (H10): never inside the main numbers.

    ``outside`` due reviews outside the focus scope while 只做范围内 acts;
    ``paused_parts`` the scopes listed under 暂不复习 and ``paused_due`` the
    due reviews inside them. A due review inside a paused scope counts there
    only (exclusion wins).
    """

    outside: int = 0
    paused_parts: int = 0
    paused_due: int = 0


def apart(due_members: Iterable[str], *, excluded: Collection[str], only: Collection[str] | None,
          paused_parts: int) -> Apart:
    """The two "apart" counts of the home page over the due reviews' members (one entry per due review)."""
    outside = paused = 0
    for member in due_members:
        if member in excluded:
            paused += 1
        elif only is not None and member not in only:
            outside += 1
    return Apart(outside, paused_parts, paused)


def part_lessons(firsts: Iterable[tuple[str, datetime | None]], part: Part, now: datetime, *,
                 study_day: Callable[[datetime], object]) -> frozenset[str] | None:
    """The lessons of a "newly learned" part (E9), or None for a part that is not one of them.

    ``firsts``: (lesson content id, first formal rating of one of its course
    states or None), one pair per state. 今天新学的: a state first rated on the
    study day of ``now`` (``study_day`` maps an instant to its study day);
    最近 14 天新学的: a recent state (rules/recent.is_recent). Kana is not part
    of either in this step.
    """
    if part is Part.TODAY:
        today = study_day(now)
        return frozenset(lesson for lesson, first in firsts
                         if first is not None and first <= now and study_day(first) == today)
    if part is Part.RECENT:
        return frozenset(lesson for lesson, first in firsts if is_recent(first, now))
    return None
