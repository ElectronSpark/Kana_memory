"""P1: places in the model's question background kept for known content that is fading (design 12 section 8).

Pure: no Toga, database or clock. The caller computes each candidate's due flag
at its own instant and hands the candidates in the ordinary order (highest
familiarity first). Only the choice and order of the capped list change; the
items themselves are returned untouched.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NamedTuple

from ..model import Orientation

# One place in every RESERVE_EVERY is kept for fading content (8 of 24).
RESERVE_EVERY = 3


class BackgroundCandidate(NamedTuple):
    """One known item that may serve as background: the item, its familiarity, and
    whether some unit of it is due (a due time at or before the caller's now)."""

    item: Any
    familiarity: float
    due: bool


def select_background(orientation: Orientation, candidates: Sequence[BackgroundCandidate],
                      limit: int | None) -> list:
    """The background items in request order.

    ``candidates`` are in the ordinary order. Without a limit, or under THOROUGH,
    the result is the ordinary order cut to the limit, exactly the rule the app
    had before. Under BREADTH with a limit, up to ``limit // RESERVE_EVERY``
    places go to due candidates, least familiar first (ties keep the ordinary
    order); they stand at every third place (the 3rd, 6th, ...) so trimming from
    the end does not remove them first. Every other place takes the next
    ordinary candidate not already chosen. Places left unused by too few due
    candidates go back to the ordinary order, so the list is never shorter than
    the ordinary cut, and no candidate appears twice.
    """
    ordinary = [candidate.item for candidate in candidates]
    if limit is None:
        return ordinary
    reserved_places = limit // RESERVE_EVERY if orientation is Orientation.BREADTH else 0
    due = [index for index, candidate in enumerate(candidates) if candidate.due]
    due.sort(key=lambda index: candidates[index].familiarity)  # stable: ties keep the ordinary order
    reserved = due[:reserved_places]
    if not reserved:
        return ordinary[:limit]
    taken = set(reserved)
    rest = [index for index in range(len(candidates)) if index not in taken]
    chosen: list[int] = []
    for place in range(1, min(limit, len(candidates)) + 1):
        if reserved and (place % RESERVE_EVERY == 0 or not rest):
            chosen.append(reserved.pop(0))
        else:
            chosen.append(rest.pop(0))
    return [candidates[index].item for index in chosen]
