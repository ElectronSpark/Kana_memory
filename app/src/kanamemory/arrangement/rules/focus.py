"""The learner's focus scope as an ordering, never a filter (design 12 H2, H3).

Pure. A scope is handed in as the set of content ids it covers; None means
no scope. Content outside the scope is never hidden: it sorts later inside a
lane, and between the kinds of work it is served once nothing inside the
scope is left.

Identity form of the set (WP-H): a lesson is its content id; a kana state is
``kana_member(content_id, script)``, "kana:<content id>:<script>" (the study
path's kana entry id and kana_dependencies.kana_entry_id), so membership is
per script.
"""
from __future__ import annotations

from collections.abc import Collection, Sequence

from ..model import Candidate

# Priority tiers 0-2 (feedback, a started answer, short steps) are never reordered by the focus (H5).
FIRST_SCOPED_TIER = 3


def kana_member(content_id: str, script: str) -> str:
    """The scope-set identity of a kana state: the study path's kana entry id."""
    return f"kana:{content_id}:{script}"


def in_scope(content_id: str, scope_contents: Collection[str] | None) -> bool:
    """True without a scope; otherwise membership."""
    return scope_contents is None or content_id in scope_contents


def kana_in_scope(content_id: str, script: str, scope_contents: Collection[str] | None) -> bool:
    """in_scope for a kana state (content id and script)."""
    return scope_contents is None or kana_member(content_id, script) in scope_contents


def sort_component(content_id: str, scope_contents: Collection[str] | None) -> int:
    """0 inside the scope (or without one), 1 outside: one more component of a lane's sort key."""
    return 0 if in_scope(content_id, scope_contents) else 1


def scoped_priority(priority: tuple, component: int, *, held: bool = False) -> tuple:
    """A lane's collection sort key with the scope component after the tier (H3).

    ``priority`` is the lane's (tier, ...) key. Tiers 0-2 keep their key
    unchanged; from tier 3 on, ``component`` (sort_component) follows the
    tier, so inside the scope comes first within the due-review tier and the
    new-work tiers, ahead of the due time or band. ``held`` (an opened
    teaching block that goes on) counts as inside. Callers apply this only
    when a scope is set: without one the key keeps its original shape.
    """
    if not priority or priority[0] < FIRST_SCOPED_TIER:
        return tuple(priority)
    return (priority[0], 0 if held else component, *priority[1:])


def inside_first(kana_inside: bool, course_inside: bool) -> str | None:
    """Between two lane heads: the lane whose head is inside the scope when only one is, else None."""
    if kana_inside == course_inside:
        return None
    return "kana" if kana_inside else "course"


def scope_first(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    """The candidates inside the scope when there are any, otherwise all of them."""
    inside = tuple(candidate for candidate in candidates if candidate.in_scope)
    return inside or tuple(candidates)
