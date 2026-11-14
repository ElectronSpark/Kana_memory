"""The four kinds of focus scope, one small strategy class each (design 12 H1, M2 开闭).

Every kind answers three questions about one scope id, reading through a
ScopeReads: ``members`` (the content the scope itself names: lesson content
ids and kana entry ids "kana:<id>:<script>"; None when what it names is gone
or disabled), ``title`` (the name to show, None when it cannot be named) and
``sections`` (for B10: the route id and the sections of the scope in route
order with their states, None for a kind that has no sections). A new kind
is one more class registered in KINDS.

Scope ids are model.FocusScope's: a route is its route id; a node is
"<route id>|<node id>" (FocusScope.node / split_node: a stage, unit or
section of that route); a library is its library id; a family is one of
FAMILIES.
"""
from __future__ import annotations

from collections.abc import Iterator

from ..model import FocusScope
from .scope_reads import FAMILIES, ScopeReads


def _entries(tree) -> Iterator[dict]:
    for stage in tree.get("stages", ()):
        for unit in stage.get("units", ()):
            for section in unit.get("sections", ()):
                yield from section.get("entries", ())


def _members(tree, reads: ScopeReads) -> frozenset[str]:
    """Lesson content ids and kana entry ids of a route tree or a node of one (reference pages are not studied).

    Each entry's own prerequisites are noted on ``reads`` for the prerequisite walk.
    """
    found = set()
    for entry in _entries(tree):
        if entry.get("kind") in {"learning", "kana"}:
            identity = entry["content_id"] if entry["kind"] == "learning" else entry["id"]
            found.add(identity)
            reads.note_entry(identity, entry)
    return frozenset(found)


def _find(tree, node_id):
    """The stage, unit or section of ``tree`` with ``node_id``, wrapped so _entries/_sections walk it; else None."""
    for stage in tree.get("stages", ()):
        if stage.get("id") == node_id:
            return {"stages": [stage]}, stage
        for unit in stage.get("units", ()):
            if unit.get("id") == node_id:
                return {"stages": [{"units": [unit]}]}, unit
            for section in unit.get("sections", ()):
                if section.get("id") == node_id:
                    return {"stages": [{"units": [{"sections": [section]}]}]}, section
    return None


def _sections(tree) -> list[dict]:
    return [section for stage in tree.get("stages", ()) for unit in stage.get("units", ())
            for section in unit.get("sections", ())]


class RouteScope:
    """A study route (core, library or personal); None when it is gone, its library unusable, or it is disabled."""

    kind = "route"

    @staticmethod
    def usable(route_id: str, reads: ScopeReads):
        """The service when ``route_id`` is a usable, enabled route, else None (the core route is always enabled)."""
        service = reads.routes()
        # A service without the route API (a standalone double) names no route that could be resolved.
        if service is None or not isinstance(route_id, str) or not callable(getattr(service, "route_set", None)):
            return None
        try:
            if route_id not in service.route_set() or not service.route_state(route_id)["enabled"]:
                return None
        except (KeyError, ValueError):
            return None
        return service

    @classmethod
    def tree(cls, route_id: str, reads: ScopeReads):
        service = cls.usable(route_id, reads)
        return None if service is None else service.route_set().route(route_id)

    def members(self, scope_id: str, reads: ScopeReads) -> frozenset[str] | None:
        tree = self.tree(scope_id, reads)
        return None if tree is None else _members(tree, reads)

    def title(self, scope_id: str, reads: ScopeReads) -> str | None:
        tree = self.tree(scope_id, reads)
        return None if tree is None else str(tree.get("title") or scope_id)

    def sections(self, scope_id: str, reads: ScopeReads):
        service = self.usable(scope_id, reads)
        if service is None:
            return None
        return scope_id, _sections(_overview(service, scope_id))


def _overview(service, route_id):
    """StudyPathService.overview of a route: the tree with entry and section states."""
    from ...study_routes import CORE_ROUTE_ID
    return service.overview() if route_id == CORE_ROUTE_ID else service.overview(route_id=route_id)


def _split(scope_id: str):
    return FocusScope.split_node(scope_id) or (None, None)


class NodeScope:
    """A stage, unit or section of a route ("<route id>|<node id>"); None when the route or the node is gone."""

    kind = "node"

    def _node(self, scope_id: str, reads: ScopeReads):
        route_id, node_id = _split(scope_id)
        tree = None if route_id is None else RouteScope.tree(route_id, reads)
        return None if tree is None else _find(tree, node_id)

    def members(self, scope_id: str, reads: ScopeReads) -> frozenset[str] | None:
        found = self._node(scope_id, reads)
        return None if found is None else _members(found[0], reads)

    def title(self, scope_id: str, reads: ScopeReads) -> str | None:
        found = self._node(scope_id, reads)
        return None if found is None else str(found[1].get("title") or _split(scope_id)[1])

    def sections(self, scope_id: str, reads: ScopeReads):
        route_id, node_id = _split(scope_id)
        service = None if route_id is None else RouteScope.usable(route_id, reads)
        found = None if service is None else _find(_overview(service, route_id), node_id)
        return None if found is None else (route_id, _sections(found[0]))


class LibraryScope:
    """A content library: the enrolled lessons it provides (kana belong to no course library).

    None when the library is not installed, disabled or damaged. Content
    without progress is not listed: it has nothing to schedule, and the
    prerequisites are followed from the enrolled lessons.
    """

    kind = "library"

    @staticmethod
    def usable(library_id: str, reads: ScopeReads) -> bool:
        from ...content_availability import library_usable
        from ...content_library_rules import CORE_LIBRARY_ID
        registry = reads.availability().registry
        if registry is None:
            return library_id == CORE_LIBRARY_ID   # before the first start wrote a registry, only the core exists
        return library_usable(registry, library_id)

    def members(self, scope_id: str, reads: ScopeReads) -> frozenset[str] | None:
        from ...content_availability import ALWAYS_AVAILABLE_PREFIXES, library_of_content
        from ...content_library_rules import CORE_LIBRARY_ID
        if not self.usable(scope_id, reads):
            return None
        catalogue = reads.availability().catalogue
        found = set()
        for content_id in reads.lessons():
            owner = library_of_content(catalogue, content_id)
            if owner is None and not content_id.startswith(ALWAYS_AVAILABLE_PREFIXES):
                owner = CORE_LIBRARY_ID
            if owner == scope_id:
                found.add(content_id)
        return frozenset(found)

    def title(self, scope_id: str, reads: ScopeReads) -> str | None:
        return reads.library_titles().get(scope_id) if self.usable(scope_id, reads) else None

    def sections(self, scope_id: str, reads: ScopeReads):
        return None


class FamilyScope:
    """One family of course content (词汇、语法、读音): the enrolled lessons of that family."""

    kind = "family"
    TITLES = {"word": "词汇", "grammar": "语法", "pronunciation": "读音"}

    def members(self, scope_id: str, reads: ScopeReads) -> frozenset[str] | None:
        if scope_id not in FAMILIES:
            return None
        return frozenset(content_id for content_id, lesson in reads.lessons().items() if lesson.family == scope_id)

    def title(self, scope_id: str, reads: ScopeReads) -> str | None:
        return self.TITLES.get(scope_id)

    def sections(self, scope_id: str, reads: ScopeReads):
        return None


KINDS = {kind.kind: kind for kind in (RouteScope(), NodeScope(), LibraryScope(), FamilyScope())}
