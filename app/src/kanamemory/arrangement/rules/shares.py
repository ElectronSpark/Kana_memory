"""Sharing study time by weight among three units side by side (design 12 B4, H1).

Pure. The two settings give three weights: new content = tilt.new, course
review = tilt.review x the course share, kana practice = tilt.review x the
kana share (default 50 : 35 : 15). Among the units that have a candidate,
the one with the smallest used time per weight goes next (ties: new, then
course, then kana); a unit without a candidate does not compete, so the
time it does not need is shared by the others by their own weights -- kana
never inherits the course's review time. A unit of weight 0 (the two "only"
tilts) goes only when no unit of positive weight has a candidate. With a
focus scope, the candidates inside the scope are shared first.

Until 2026-11-18 the time was shared in two levels (new against review,
then course against kana inside review); with only kana due, half of the
time became kana (design 12 T3).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..model import Candidate, Decision, Focus, KanaTilt, Tilt, Unit, Usage, Weights
from .focus import scope_first

# Product parameters (design 12 section 11): starting values.
TILT_WEIGHTS = {Tilt.ONLY_NEW: (100, 0), Tilt.NEW: (70, 30), Tilt.EVEN: (50, 50),
                Tilt.REVIEW: (30, 70), Tilt.ONLY_REVIEW: (0, 100)}
KANA_WEIGHTS = {KanaTilt.LESS: (85, 15), KanaTilt.NORMAL: (70, 30), KanaTilt.MORE: (40, 60)}

# The order of the units for ties.
ORDER = (Unit.NEW, Unit.COURSE, Unit.KANA)


def weights_for(focus: Focus | None = None) -> Weights:
    """The weights of the breadth-first orientation under the learner's tilt."""
    focus = focus or Focus()
    new, review = TILT_WEIGHTS[focus.tilt]
    course, kana = KANA_WEIGHTS[focus.kana]
    return Weights(new, review, course, kana)


def unit_weights(weights: Weights) -> dict[Unit, int]:
    """The three units' weights side by side, as whole numbers (new x 100 : review x course : review x kana)."""
    return {Unit.NEW: weights.new * (weights.course + weights.kana),
            Unit.COURSE: weights.review * weights.course,
            Unit.KANA: weights.review * weights.kana}


def _used(usage: Usage, unit: Unit) -> float:
    return {Unit.NEW: usage.new, Unit.COURSE: usage.course, Unit.KANA: usage.kana}[unit]


def choose_unit(usage: Usage, weights: Weights, present: Iterable[Unit]) -> Unit | None:
    """The unit that goes next among ``present`` (the units with a candidate), or None for none."""
    present = set(present)
    units = [unit for unit in ORDER if unit in present]
    if not units:
        return None
    by_unit = unit_weights(weights)
    pool = [unit for unit in units if by_unit[unit] > 0]
    if not pool:
        # Only units of weight 0 are left (an "only" tilt): they share by their own level's weights.
        by_unit = {Unit.NEW: 1, Unit.COURSE: max(weights.course, 1), Unit.KANA: max(weights.kana, 1)}
        pool = units
    best = pool[0]
    for unit in pool[1:]:
        # used(unit) / w(unit) < used(best) / w(best), without dividing; equal keeps the earlier unit
        if _used(usage, unit) * by_unit[best] < _used(usage, best) * by_unit[unit]:
            best = unit
    return best


def prefer_new(usage: Usage, weights: Weights, reviews: Iterable[Unit] = (Unit.COURSE, Unit.KANA)) -> bool:
    """Would new content go next if it had a candidate, beside review candidates of ``reviews``?"""
    return choose_unit(usage, weights, (Unit.NEW, *reviews)) is Unit.NEW


def prefer_course(usage: Usage, weights: Weights) -> bool:
    """Would course review go before kana practice if both had a candidate and new content none?"""
    return choose_unit(usage, weights, (Unit.COURSE, Unit.KANA)) is Unit.COURSE


def decide(usage: Usage, weights: Weights, candidates: Sequence[Candidate]) -> Decision:
    """Pick the candidate that goes next.

    ``candidates`` holds at most one candidate per unit. The reason names the
    step that settled it: "none", "only" (a single candidate), "scope" (the
    scope left a single candidate) or "share".
    """
    by_unit = {}
    for candidate in candidates:
        if candidate.unit in by_unit:
            raise ValueError("At most one candidate per unit")
        by_unit[candidate.unit] = candidate
    if not by_unit:
        return Decision(None, "none")
    if len(by_unit) == 1:
        return Decision(next(iter(by_unit.values())), "only")
    eligible = {candidate.unit: candidate for candidate in scope_first(tuple(by_unit.values()))}
    if len(eligible) == 1:
        return Decision(next(iter(eligible.values())), "scope")
    return Decision(eligible[choose_unit(usage, weights, eligible)], "share")
