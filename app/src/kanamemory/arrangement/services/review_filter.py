"""ReviewFilterService: the learner's 只做范围内 and 暂不复习, and the extra-practice part, as one filter (design 12 H8-H10, E9).

Application layer, no SQL. The setting (study_review_filter_v1) is read and
written through ArrangementPreferences; the scopes become members through the
focus service and its catalogue (only: the focus scope's contents, its
enrolled prerequisites included; paused: each paused scope's own members,
ScopeCatalog.members, so pausing a part never pauses the earlier lessons it
builds on). current(now) is the filter of the moment (rules/review_filter.
ServeFilter, None when nothing restricts) that every reader asks.

Work the learner started by hand (section 22, brief J11 F3) comes from
``started`` (the assembly reads it from the explicit-start record and the
first formal ratings, explicit_learning_start.started_work): it is asked
only when a filter restricts, and served as tiers 0-2 are until each of its
abilities is formally rated.

Memory of this launch only, nothing written:
- 做范围外的 (lift): 只做范围内 has no effect until the next sitting break
  (rules/clock.sitting_break: more than 30 minutes since the last
  "继续学习", or another study day) or the next app start (a new object).
- the extra-practice part: set right before extra practice starts with a part;
  ExtraSession.stop() forgets it (the assembly hands set_part(None) to the
  session as its on_stop), so every way the mode ends (结束加练, the home
  page, nothing left, a failed start) clears it.

serving(store, filter) puts the filter of one "继续学习" (or one home refresh)
where the two lanes' owners read it (serving_filter): LearningStore.current
and counts, StudyStore.prepare_next_question, the queue availability and the
home previews. Outside such a block they behave as before (the single-lane
entries 课程学习 and a kana practice opened by itself are not filtered).
"""
from __future__ import annotations

from collections.abc import Callable, Collection, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime

from ..model import FocusScope, ReviewFilterSetting
from ..rules.clock import sitting_break
from ..rules.review_filter import Apart, ServeFilter, apart, section_offered, serve_filter
from ..settings import MAX_EXCLUDED
from .focus import FocusService
from .preferences import ArrangementPreferences

_ATTRIBUTE = "_arrangement_serve_filter"


@contextmanager
def serving(store, value: ServeFilter | None) -> Iterator[None]:
    """Make ``value`` the filter the lanes of ``store`` read inside this block (nested blocks restore the outer)."""
    previous = getattr(store, _ATTRIBUTE, None)
    setattr(store, _ATTRIBUTE, value)
    try:
        yield
    finally:
        setattr(store, _ATTRIBUTE, previous)


def serving_filter(store) -> ServeFilter | None:
    """The filter of the "继续学习" (or home refresh) running on ``store``; None outside one."""
    return getattr(store, _ATTRIBUTE, None)


class ReviewFilterService:
    """The two switches of one learner store, the lift and the extra-practice part."""

    def __init__(self, preferences: ArrangementPreferences, focus: FocusService, *,
                 study_day: Callable[[datetime], str], restart: Callable[[], object] | None = None,
                 started: Callable[[], Collection[tuple[str, str | None]]] | None = None,
                 learned: Callable[[], Collection[str]] | None = None):
        self._preferences = preferences
        self._focus = focus
        self._study_day = study_day
        self._restart = restart
        self._started = started   # (lesson, ability) of work started by hand, not yet formally rated
        self._learned = learned   # the members with a formally rated state (design 12 E11)
        self._lifted_at: datetime | None = None   # the last "继续学习" since 做范围外的
        self._part: frozenset[str] | None = None
        self._extra = False       # 加练 is on: the filter serves only what is already learned (E11)

    # --- the setting ----------------------------------------------------------------------------------

    def setting(self) -> ReviewFilterSetting:
        """The stored switches (one settings read); off and nothing paused when none is stored."""
        return self._preferences.review_filter()

    def _store(self, setting: ReviewFilterSetting) -> ReviewFilterSetting:
        if setting == self.setting():
            return setting
        stored = self._preferences.set_review_filter(setting)
        if self._restart is not None:
            self._restart()
        return stored

    def set_only(self, on: bool) -> ReviewFilterSetting:
        """Turn 只做范围内 on or off (written at once, one rearrangement of the round); the lift is forgotten."""
        if type(on) is not bool:
            raise ValueError("A switch value is required")
        self._lifted_at = None
        return self._store(replace(self.setting(), only=on))

    def pause(self, scope: FocusScope) -> ReviewFilterSetting:
        """Add ``scope`` to 暂不复习 (nothing when it is listed already); ValueError beyond MAX_EXCLUDED."""
        if not isinstance(scope, FocusScope):
            raise ValueError("A scope is required")
        setting = self.setting()
        if scope in setting.excluded:
            return setting
        if len(setting.excluded) >= MAX_EXCLUDED:
            raise ValueError("Too many paused scopes")
        return self._store(replace(setting, excluded=(*setting.excluded, scope)))

    def restore(self, scope: FocusScope) -> ReviewFilterSetting:
        """恢复: take ``scope`` off 暂不复习; its states and due times were never touched."""
        setting = self.setting()
        return self._store(replace(setting, excluded=tuple(item for item in setting.excluded if item != scope)))

    # --- memory of this launch ------------------------------------------------------------------------

    def lift(self, now: datetime) -> None:
        """做范围外的: 只做范围内 rests until the next sitting break or app start (nothing is written)."""
        self._lifted_at = now

    def lifted(self, now: datetime) -> bool:
        """Whether 做范围外的 still holds at ``now``."""
        last = self._lifted_at
        return last is not None and not sitting_break(last, self._study_day(last), now, self._study_day(now))

    def touch(self, now: datetime) -> None:
        """A "继续学习" at ``now``: the lift lasts while the sitting does, and ends at its break."""
        if self._lifted_at is not None:
            self._lifted_at = now if self.lifted(now) else None

    def set_part(self, members: Collection[str] | None) -> None:
        """The part extra practice starts with (its members; None for 全部 and when the mode stops)."""
        self._part = None if members is None else frozenset(members)

    def part(self) -> frozenset[str] | None:
        return self._part

    def begin_extra(self, members: Collection[str] | None) -> None:
        """加练 starts with the part of these members (None: 全部): from now on the filter serves only what is
        already learned (design 12 E11), within the part."""
        self.set_part(members)
        self._extra = True

    def end_extra(self) -> None:
        """加练 has ended (ExtraSession.stop, through the assembly's on_stop): the part and the E11 limit go."""
        self.set_part(None)
        self._extra = False

    @property
    def in_extra(self) -> bool:
        return self._extra

    # --- the filter -----------------------------------------------------------------------------------

    def paused_members(self, setting: ReviewFilterSetting | None = None) -> frozenset[str]:
        """The members of every paused scope that resolves (one that no longer resolves pauses nothing)."""
        setting = self.setting() if setting is None else setting
        members: set[str] = set()
        for scope in setting.excluded:
            members |= self._focus.catalog.members(scope) or frozenset()
        return frozenset(members)

    def only_members(self, setting: ReviewFilterSetting | None = None) -> frozenset[str] | None:
        """The focus scope's contents while 只做范围内 is on and the scope resolves (the lift not looked at)."""
        setting = self.setting() if setting is None else setting
        return self._focus.scope_contents() if setting.only else None

    def only_acts(self, now: datetime) -> bool:
        """Whether 只做范围内 limits 继续学习 at ``now`` (on, the scope resolves, not lifted)."""
        return not self.lifted(now) and self.only_members() is not None

    def current(self, now: datetime, *, with_part: bool = True) -> ServeFilter | None:
        """The filter of ``now``: paused members, the focus scope under 只做范围内 (unless lifted) and, while extra
        practice runs with one, its part (``with_part`` False: without it), and the work started by hand that it
        serves all the same (ServeFilter.started). None when nothing restricts."""
        setting = self.setting()
        extra = with_part and self._extra and self._learned is not None
        if not setting.only and not setting.excluded and (not with_part or self.part() is None) and not extra:
            return None
        value = serve_filter(excluded=self.paused_members(setting), only=self.only_members(setting),
                             lifted=self.lifted(now), part=self.part() if with_part else None,
                             learned=frozenset(self._learned()) if extra else None)
        if value is None or self._started is None or value.learned_only:
            return value
        return replace(value, started=frozenset(self._started()))

    def offers_section(self, serve: ServeFilter | None, route_id: str, section_id: str) -> bool:
        """Whether 继续学习's pages and the home may offer this next section under ``serve`` (design 12 H12): its own
        members (ScopeCatalog.members of the node scope) through rules/review_filter.section_offered. Without a
        filter nothing is read. ``route_id``: the section's route (the caller names the core route too)."""
        if serve is None:
            return True
        members = self._focus.catalog.members(FocusScope.node(route_id, section_id))
        return section_offered(serve, members)

    def next_offer(self, serve: ServeFilter | None, route_id: str, overview, others=()):
        """The next section 继续学习's end and wait pages and the home offer under ``serve``: (route id, section) of
        the current route's next section when it is offered, else of the first other route's (``others``: rows
        with "route_id" and "next_section"); None when none is (design 12 H12: the same answer for both)."""
        section = (overview or {}).get("next_section")
        if section and self.offers_section(serve, route_id, str(section["id"])):
            return route_id, section
        for row in others:
            other = row.get("next_section")
            if other and self.offers_section(serve, row["route_id"], str(other["id"])):
                return row["route_id"], other
        return None

    def apart(self, now: datetime, due_members: Collection[str]) -> Apart:
        """The home page's two apart counts over the due reviews' members (H10)."""
        setting = self.setting()
        only = self.only_members(setting) if not self.lifted(now) else None
        return apart(due_members, excluded=self.paused_members(setting), only=only,
                     paused_parts=len(setting.excluded))
