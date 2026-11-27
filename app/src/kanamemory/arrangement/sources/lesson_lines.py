"""StoreLessonLines: the enrolled lessons by study line and their places on the core route (design 12 LS5-LS7).

Implements ports.LessonLines over a StudyStore. The lines are the study
clock's own map of the enrolled lessons (StoreEventSource.lesson_lines:
re-read only when the enrolled lessons change). The positions come from the
static core route order (StudyPathService.route_set().entries(CORE_ROUTE_ID),
in route order; a learning entry carries its content_id): the map is built
once per route set and kept while the service hands out that same route
set. "Not taught yet" is StudyQueries.untaught_course_ids. Reads only; no SQL
here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping


class StoreLessonLines:
    """ports.LessonLines of one store; ``routes()`` gives the app's StudyPathService (None: no positions)."""

    def __init__(self, store, events, routes: Callable[[], object] | None = None):
        self._store = store
        self._events = events
        self._routes = routes
        self._route_set: object = None
        self._positions: Mapping[str, int] = {}

    def lines(self) -> Mapping[str, str]:
        return self._events.lesson_lines()

    def positions(self) -> Mapping[str, int]:
        service = self._routes() if self._routes is not None else None
        route_set = service.route_set() if service is not None else None
        if route_set is None:
            return {}
        if route_set is not self._route_set:
            from ...study_routes import CORE_ROUTE_ID
            try:
                entries = route_set.entries(CORE_ROUTE_ID)
            except KeyError:
                entries = {}
            positions: dict[str, int] = {}
            for entry in entries.values():
                content_id = entry.get("content_id") if entry.get("kind") == "learning" else None
                if content_id:
                    positions.setdefault(content_id, len(positions))
            self._route_set, self._positions = route_set, positions
        return self._positions

    def untaught(self) -> frozenset[str]:
        return frozenset(self._store.queries.untaught_course_ids())
