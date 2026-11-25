"""ExtraParts: the parts the page 加练 offers, each with what can be practised now (design 12 E9).

Application layer, no SQL. One pool read without the part (ports.ExtraStates
.read(now, with_part=False): the learner's 只做范围内 and 暂不复习 still apply)
and one read of the lessons' first formal ratings answer every part:

- 今天新学的 / 最近 14 天新学的: the lessons with a course state first rated on
  this study day / a recent course state (rules/review_filter.part_lessons;
  kana is not part of them in this step);
- 学习重点范围内: the focus scope's contents, only while the scope resolves;
- 全部: everything extra practice serves (as before E9).

A part's count is the states of the pool inside it that extra practice can
offer at ``now`` (rules/extra.eligible: every learned state, without a time
condition, design 12 E10), the same test as the button 继续加练 and the home's
entry (ExtraSession.learned, D6). members(part, now) is what the review
filter receives when the mode starts with that part (None for 全部).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from ..model import ExtraEntry, Lane, Part
from ..rules.extra import eligible
from ..rules.focus import kana_member
from ..rules.review_filter import part_lessons


@dataclass(frozen=True)
class PartCount:
    """One part of the page 加练 with the number of its states that can be practised now."""

    part: Part
    count: int


def _member(entry: ExtraEntry) -> str:
    ref = entry.ref
    return kana_member(ref.content_id, ref.script) if ref.lane is Lane.KANA else ref.content_id


class ExtraParts:
    """The page 加练's parts over one learner store's pool."""

    def __init__(self, read: Callable[[datetime], Iterable[ExtraEntry]],
                 firsts: Callable[[], Iterable[tuple[str, datetime | None]]], *,
                 study_day: Callable[[datetime], str], scope: Callable[[], frozenset[str] | None]):
        """``read(now)``: the pool without the part; ``firsts()``: (lesson, first formal rating) per course state;
        ``scope()``: the focus scope's contents, None without one that resolves."""
        self._read, self._firsts, self._study_day, self._scope = read, firsts, study_day, scope

    def members(self, part: Part, now: datetime, *, firsts=None) -> frozenset[str] | None:
        """The members of ``part`` at ``now`` (None for 全部, and for 学习重点范围内 without a live scope)."""
        part = Part(part)
        if part is Part.ALL:
            return None
        if part is Part.SCOPE:
            return self._scope()
        return part_lessons(self._firsts() if firsts is None else firsts, part, now, study_day=self._study_day)

    def counts(self, now: datetime) -> tuple[PartCount, ...]:
        """The parts in page order with their counts; 学习重点范围内 only while the focus scope resolves."""
        entries = list(self._read(now))
        open_keys = {view.key for view in eligible([entry.view for entry in entries], now)}
        practicable = [entry for entry in entries if entry.ref.key in open_keys]
        firsts = list(self._firsts())
        found = []
        for part in (Part.TODAY, Part.RECENT, Part.SCOPE, Part.ALL):
            if part is Part.SCOPE and self._scope() is None:
                continue
            members = self.members(part, now, firsts=firsts)
            count = (len(practicable) if members is None
                     else sum(_member(entry) in members for entry in practicable))
            found.append(PartCount(part, count))
        return tuple(found)
