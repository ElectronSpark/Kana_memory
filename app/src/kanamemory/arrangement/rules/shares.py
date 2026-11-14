"""Sharing study time by weight in two levels (design 12 B4, H1).

Pure. Level one: new content against review. Level two, inside review:
course against kana. At each level the side with the smaller used time per
weight goes next; a side without a candidate yields everything to the other.
With a focus scope, the candidates inside the scope are shared first.
"""
from __future__ import annotations

from collections.abc import Sequence

from ..model import Candidate, Decision, Focus, KanaTilt, Side, Tilt, Unit, Usage, Weights
from .focus import scope_first

# Product parameters (design 12 section 11): starting values.
TILT_WEIGHTS = {Tilt.ONLY_NEW: (100, 0), Tilt.NEW: (70, 30), Tilt.EVEN: (50, 50),
                Tilt.REVIEW: (30, 70), Tilt.ONLY_REVIEW: (0, 100)}
KANA_WEIGHTS = {KanaTilt.LESS: (85, 15), KanaTilt.NORMAL: (70, 30), KanaTilt.MORE: (40, 60)}


def weights_for(focus: Focus | None = None) -> Weights:
    """The weights of the breadth-first orientation under the learner's tilt."""
    focus = focus or Focus()
    new, review = TILT_WEIGHTS[focus.tilt]
    course, kana = KANA_WEIGHTS[focus.kana]
    return Weights(new, review, course, kana)


def prefer_new(usage: Usage, weights: Weights) -> bool:
    """Would new content go next if both sides had a candidate? (Ties go to new content.)"""
    if weights.new == 0:
        return False
    if weights.review == 0:
        return True
    # usage.new / weights.new <= usage.review / weights.review, without dividing
    return usage.new * weights.review <= usage.review * weights.new


def prefer_course(usage: Usage, weights: Weights) -> bool:
    """Would course review go before kana practice if both had a candidate? (Ties go to the course.)"""
    if weights.course == 0:
        return False
    if weights.kana == 0:
        return True
    return usage.course * weights.kana <= usage.kana * weights.course


def choose_side(usage: Usage, weights: Weights, *, has_new: bool, has_review: bool) -> Side | None:
    if has_new and has_review:
        return Side.NEW if prefer_new(usage, weights) else Side.REVIEW
    return Side.NEW if has_new else Side.REVIEW if has_review else None


def choose_review(usage: Usage, weights: Weights, *, has_course: bool, has_kana: bool) -> Unit | None:
    if has_course and has_kana:
        return Unit.COURSE if prefer_course(usage, weights) else Unit.KANA
    return Unit.COURSE if has_course else Unit.KANA if has_kana else None


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
    side = choose_side(usage, weights, has_new=Unit.NEW in eligible,
                       has_review=Unit.COURSE in eligible or Unit.KANA in eligible)
    if side is Side.NEW:
        return Decision(eligible[Unit.NEW], "share")
    unit = choose_review(usage, weights, has_course=Unit.COURSE in eligible, has_kana=Unit.KANA in eligible)
    return Decision(eligible[unit], "share")
