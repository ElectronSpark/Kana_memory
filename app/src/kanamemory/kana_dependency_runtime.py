"""Read-only prerequisite evidence for script-specific kana learning.

Queue membership, introductions and familiarity self-reports are not retrieval
evidence. A direct successful review unlocks a foundation permanently; later
lapses do not strand its dependants. Existing assessed targets keep their own
review route, while untouched questions in old queues use the current graph.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import sqlite3
from types import MappingProxyType

from .kana_dependencies import kana_entry_id, practice_kana_dependencies
from .kana_question_policy import _KANA_KINDS


@lru_cache(maxsize=1)
def _bundled_graph():
    from .content import ContentRepository
    with ContentRepository(Path(__file__).with_name("resources") / "content.sqlite3") as repository:
        return MappingProxyType(_with_reading_rules(repository.kana_list(), strict=True))


def _with_reading_rules(rows, *, strict=False):
    from .kana_reading_rules import dependencies_for_kana
    from .learning_content import get_learning_item
    rows = list(rows)
    graph = practice_kana_dependencies(rows, strict=strict)
    available = {}
    for row in rows:
        required = dependencies_for_kana(row)
        for rule in required:
            if rule not in available:
                available[rule] = get_learning_item(rule) is not None
        rules = tuple(rule for rule in required if available[rule])
        for script in ("hiragana", "katakana"):
            identity = kana_entry_id(row["id"], script)
            if graph.get(identity) and rules:
                graph[identity] = tuple(dict.fromkeys((*graph[identity], *rules)))
                for rule in rules:
                    graph.setdefault(rule, ())
    return graph


def kana_dependency_graph(rows=None, *, graph=None):
    """Use the bundled graph by default; explicit inventories never pollute it."""
    if rows is not None and graph is not None:
        raise ValueError("Pass kana rows or a dependency graph, not both")
    if graph is not None:
        return graph
    return _with_reading_rules(rows) if rows is not None else _bundled_graph()


@dataclass(frozen=True)
class KanaDependencyState:
    ready: frozenset[str] = frozenset()
    reviewed: frozenset[str] = frozenset()


def _kana_dependency_state_uncached(store):
    """Read all primary kana evidence in one bounded grouped query.

    Word evidence and guided correction never unlock a spelling. An accepted
    homophone is valid for that listening task, but cannot establish recall of
    a different written constituent (for example じ as evidence for ぢ).
    """
    kinds = sorted(_KANA_KINDS)
    # Search each card's indexed event list only until its first success. A
    # learner's years of later reviews must not be replayed for every screen.
    rows = store.queries.kana_prerequisite_rows(kinds)
    ready, reviewed = set(), set()
    for row in rows:
        identity = kana_entry_id(row["content_id"], row["script"])
        reviewed.add(identity)
        if row["ready"]:
            ready.add(identity)
    return KanaDependencyState(frozenset(ready), frozenset(reviewed))


def kana_dependency_state(store):
    """Reuse exact evidence until its existing connection revision changes.

    The shared monitor uses only connection-local metadata, never backup or
    learner tables. Checkpoint/draft writes do not invalidate it. Transactional
    and query-only inspections use the read-only query and publish no cache.
    """
    if store.in_transaction:
        return _with_taught_rules(store, _kana_dependency_state_uncached(store))
    from .domain import _adaptive_evidence_monitor
    try:
        token = _adaptive_evidence_monitor(store)
    except sqlite3.OperationalError:
        return _with_taught_rules(store, _kana_dependency_state_uncached(store))
    cached = getattr(store, "_kana_dependency_evidence_cache", None)
    if cached is not None and cached[0] == token:
        return _with_taught_rules(store, cached[1])
    result = _kana_dependency_state_uncached(store)
    # A concurrent restore/import must not publish a mixed historical view.
    if _adaptive_evidence_monitor(store) != token:
        store._kana_dependency_evidence_cache = None
        return _with_taught_rules(store, _kana_dependency_state_uncached(store))
    store._kana_dependency_evidence_cache = (token, result)
    return _with_taught_rules(store, result)


def _with_taught_rules(store, state):
    """Teaching permits the first application; it does not award mastery.

    Read these four rows afresh: completing a guide need not create a primary
    review event, so the kana evidence cache cannot own this transition.
    """
    from .kana_reading_rules import READING_RULE_IDS
    if not store.queries.has_course_table():
        return state
    taught = store.queries.taught_course_ids(READING_RULE_IDS)
    return KanaDependencyState(state.ready | taught, state.reviewed)


def _identity(question_or_entry_id):
    if isinstance(question_or_entry_id, str):
        return question_or_entry_id
    get = (question_or_entry_id.get if isinstance(question_or_entry_id, Mapping)
           else lambda key, default=None: getattr(question_or_entry_id, key, default))
    if get("kind") not in _KANA_KINDS or get("script") not in {"hiragana", "katakana"}:
        return None
    return kana_entry_id(get("content_id"), get("script"))


def kana_prerequisite_status(question_or_entry_id, graph, state, *, preserve=False):
    """Return one gate decision; callers may pin a real draft or pending result."""
    identity = _identity(question_or_entry_id)
    dependencies = tuple(graph.get(identity, ()))
    missing = tuple(dependency for dependency in dependencies if dependency not in state.ready)
    grandfathered = bool(identity in state.reviewed or preserve)
    return {"ready": not missing, "blocked": bool(missing and not grandfathered),
            "grandfathered": grandfathered, "dependencies": dependencies, "missing": missing}


def missing_kana_prerequisites(targets, graph, state):
    """Unready prerequisites in stable foundation-first order, without targets.

    Already assessed targets are not retroactively expanded. A missing parent
    that was assessed but never passed remains in the result: callers must
    reuse its existing due review rather than awarding mastery or resetting it.
    """
    if isinstance(targets, str):
        targets = (targets,)
    result, visited, visiting = [], set(), set()

    def visit(identity):
        if identity in visited or identity in state.ready:
            return
        if identity in visiting:
            raise ValueError("假名前置关系成环：" + identity)
        visiting.add(identity)
        if identity not in state.reviewed:
            for dependency in graph.get(identity, ()):
                visit(dependency)
        visiting.remove(identity)
        visited.add(identity)
        result.append(identity)

    for target in targets:
        identity = _identity(target)
        if identity is not None and identity not in state.reviewed:
            for dependency in graph.get(identity, ()):
                visit(dependency)
    return tuple(result)
