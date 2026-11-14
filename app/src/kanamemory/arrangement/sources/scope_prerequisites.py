"""The enrolled prerequisites of a focus scope's content (design 12 H1: 范围包含所选内容，以及它们已加入的前置).

One job: given the scope's own members (lesson content ids and kana entry
ids), follow prerequisites transitively and return those the learner has
enrolled. Lessons follow their saved prerequisites together with the
prerequisites of the route entry (the catalogue the route was built from),
of the catalogue item (the overlay LearningStore applies to untouched
bundled lessons) and of a personal version that revises them (ScopeReads.
prerequisite_overrides; added to the others, so a prerequisite the version
dropped only keeps its lesson inside the scope); kana follow the kana lane's own graph, which also names the
reading-rule lessons a kana needs. The walk goes through prerequisites that
are not enrolled too, so a transitive enrolled one is found; only enrolled
ones are added. Reads through ScopeReads; no SQL.
"""
from __future__ import annotations

from collections.abc import Iterable

from .scope_reads import ScopeReads


def _prerequisites(identity: str, reads: ScopeReads) -> tuple[str, ...]:
    if identity.startswith("kana:"):
        return tuple(reads.kana_graph().get(identity, ()))
    lesson = reads.lessons().get(identity)
    saved = lesson.depends_on if lesson is not None else ()
    return tuple(dict.fromkeys((*saved, *reads.entry_dependencies.get(identity, ()),
                                *reads.catalogue_dependencies(identity),
                                *reads.prerequisite_overrides().get(identity, ()))))


def enrolled_prerequisites(members: Iterable[str], reads: ScopeReads) -> frozenset[str]:
    """Every enrolled lesson or kana that some member needs first, directly or through others."""
    lessons, kana = reads.lessons(), None
    found, seen = set(), set()
    stack = list(dict.fromkeys(members))
    seen.update(stack)
    while stack:
        for prerequisite in _prerequisites(stack.pop(), reads):
            if prerequisite in seen:
                continue
            seen.add(prerequisite)
            stack.append(prerequisite)
            if prerequisite.startswith("kana:"):
                kana = reads.enrolled_kana() if kana is None else kana
                if prerequisite in kana:
                    found.add(prerequisite)
            elif prerequisite in lessons:
                found.add(prerequisite)
    return frozenset(found)
