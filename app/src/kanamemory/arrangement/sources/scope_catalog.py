"""StoreScopeCatalog: a focus scope as content, title, next section and choices (design 12 H1, H3, H6, B10, UI6).

Implements ports.ScopeCatalog over one learner store. ``contents(scope)``
is the scope's own members plus their enrolled prerequisites (``members(scope)``:
without them, design 12 H9), as a set of
lesson content ids and kana entry ids ("kana:<id>:<script>", rules/focus.
kana_member); None when the route, node or library it names is gone or
disabled, and then everything behaves as without a scope (the stored
setting is never rewritten here: this module does not write).

Routes and nodes need the app's StudyPathService: ``routes`` is a callable
that returns it (study_path_runtime hands over the app's own provider);
without one, route and node scopes resolve to None.

Cache: results are kept per store revision (StudyStore.read_revision: every
local write, rollback or other connection's commit renews it; the route
settings, the library registry and the enrolled lessons the app's service
is built from are all stored, so a new service comes with a new revision);
inside a transaction nothing is kept. Only the last revision is kept, and the
service is asked for only when a route or node scope is resolved. Nothing is
read while no scope is asked about.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..model import FocusScope
from .scope_kinds import FAMILIES, KINDS, FamilyScope
from .scope_prerequisites import enrolled_prerequisites
from .scope_reads import ScopeReads


@dataclass(frozen=True)
class NextSection:
    """The next section to offer inside a scope (B10): its route, id and title."""

    route_id: str
    section_id: str
    title: str


@dataclass(frozen=True)
class ScopeChoice:
    """A scope the orientation screen offers, with its title."""

    scope: FocusScope
    title: str


class StoreScopeCatalog:
    """Focus scopes of one learner store; read-only."""

    def __init__(self, store, routes: Callable[[], object] | None = None):
        self._store = store
        self._routes = routes
        self._token = None
        self._reads: ScopeReads | None = None
        self._results: dict = {}

    def _state(self) -> ScopeReads:
        """This revision's reads; a new revision (or a transaction) starts afresh."""
        if getattr(self._store, "in_transaction", False):
            self._token = None
            return ScopeReads(self._store, self._routes)
        token = tuple(self._store.read_revision())
        if token != self._token or self._reads is None:
            self._token, self._reads, self._results = token, ScopeReads(self._store, self._routes), {}
        return self._reads

    def _cached(self, name: str, scope: FocusScope, compute):
        reads = self._state()
        if self._token is None:
            return compute(reads)
        key = (name, scope.kind, scope.id)
        if key not in self._results:
            self._results[key] = compute(reads)
        return self._results[key]

    def contents(self, scope: FocusScope) -> frozenset[str] | None:
        """Members of the scope and their enrolled prerequisites; None when it does not resolve."""
        def compute(reads):
            members = KINDS[scope.kind].members(scope.id, reads)
            return None if members is None else members | enrolled_prerequisites(members, reads)
        return self._cached("contents", scope, compute)

    def members(self, scope: FocusScope) -> frozenset[str] | None:
        """The scope's own members, without their prerequisites (design 12 H9); None when it does not resolve."""
        return self._cached("members", scope, lambda reads: KINDS[scope.kind].members(scope.id, reads))

    def title(self, scope: FocusScope) -> str | None:
        """The scope's name to show (route, node, library or family title); None when it does not resolve."""
        return self._cached("title", scope, lambda reads: KINDS[scope.kind].title(scope.id, reads))

    def next_section(self, scope: FocusScope) -> NextSection | None:
        """B10 inside the scope: its first required section with content not yet added, in route order.

        Only a route or node scope has sections; None for the other kinds,
        when it does not resolve, or when every section is added.
        """
        def compute(reads):
            found = KINDS[scope.kind].sections(scope.id, reads)
            if found is None:
                return None
            route_id, sections = found
            section = next((section for section in sections
                            if section.get("new_count") and not section.get("optional")), None)
            return None if section is None else NextSection(route_id, str(section["id"]), str(section["title"]))
        return self._cached("next_section", scope, compute)

    def choices(self) -> tuple[ScopeChoice, ...]:
        """The scopes the orientation screen offers: each enabled route, each usable library, each family.

        Routes come only when the catalogue has the study path service. Node
        scopes are set from the route pages instead.
        """
        reads = self._state()
        found = []
        service = reads.routes()
        if callable(getattr(service, "route_set", None)):
            for route_id in service.route_set().route_ids():
                scope = FocusScope("route", route_id)
                title = self.title(scope)
                if title is not None:
                    found.append(ScopeChoice(scope, title))
        registry = reads.availability().registry
        libraries = [row["library_id"] for row in (registry or {}).get("libraries", ())] if registry else []
        from ...content_library_rules import CORE_LIBRARY_ID
        for library_id in dict.fromkeys([CORE_LIBRARY_ID, *libraries]):
            scope = FocusScope("library", library_id)
            title = self.title(scope)
            if title is not None:
                found.append(ScopeChoice(scope, title))
        found.extend(ScopeChoice(FocusScope("family", family), FamilyScope.TITLES[family]) for family in FAMILIES)
        return tuple(found)
