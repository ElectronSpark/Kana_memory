"""ScopeReads: what one focus-scope resolution reads from the store, each read at most once (design 12 WP-H).

Lifetime: one resolution at one store revision (StoreScopeCatalog makes a
new one whenever the revision changes). Every read is lazy and memoized, so a
scope kind reads only what it needs. No SQL here: the store's query owner
(StudyQueries.course_scope_rows, reviewed_kana_rows), the store's public
settings reader, the content availability view, the bundled kana dependency
graph, the process cache of the bundled lesson prerequisites
(learning_dependencies.bundled_dependencies), the personal-version owners
(prerequisite_overrides) and the study path service the app built
(``routes``, None without one) answer.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

# The three families of course content a scope can name (design 12 H1: 词汇、语法、读音).
FAMILIES = ("word", "grammar", "pronunciation")

_UNREAD = object()


@dataclass(frozen=True)
class Lesson:
    """An enrolled lesson as the scope needs it: its family (FAMILIES) and saved prerequisites."""

    content_id: str
    family: str
    depends_on: tuple[str, ...]


def family_of(family: str, domain) -> str:
    """The scope family of a lesson row: pronunciation lessons are stored as grammar with that domain."""
    return "pronunciation" if domain == "pronunciation" else family


def _dependencies(value) -> tuple[str, ...]:
    if value is None:
        return ()
    try:
        items = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return ()
    if isinstance(items, str) or not isinstance(items, Sequence):
        return ()
    return tuple(item for item in items if isinstance(item, str))


class ScopeReads:
    """Memoized reads of one resolution; ``routes`` gives the app's StudyPathService or None."""

    def __init__(self, store, routes: Callable[[], object] | None = None):
        self.store = store
        self._routes = routes
        self._values: dict[str, object] = {}
        # Prerequisites route entries carry (the catalogue the route was built from), noted by the route kinds.
        self.entry_dependencies: dict[str, tuple[str, ...]] = {}

    def note_entry(self, identity: str, entry) -> None:
        """Remember a route entry's own prerequisites (its depends_on) for the prerequisite walk."""
        found = _dependencies(entry.get("depends_on"))
        if found:
            self.entry_dependencies[identity] = found

    def _once(self, name: str, read: Callable[[], object]):
        value = self._values.get(name, _UNREAD)
        if value is _UNREAD:
            value = self._values[name] = read()
        return value

    def routes(self):
        """The study path service, or None when the arrangement was built without one."""
        return self._once("routes", lambda: None if self._routes is None else self._routes())

    def lessons(self) -> Mapping[str, Lesson]:
        """Every enrolled lesson, in enrolment order."""
        return self._once("lessons", lambda: {
            row["content_id"]: Lesson(row["content_id"], family_of(row["family"], row["domain"]),
                                      _dependencies(row["depends_on"]))
            for row in self.store.queries.course_scope_rows()})

    def enrolled_kana(self) -> frozenset[str]:
        """Kana entry ids ("kana:<id>:<script>") the learner added to a route or has reviewed."""
        def read():
            from ...study_path import PATH_SETTING, validate_path_state
            state = validate_path_state(self.store.get_setting(PATH_SETTING, {
                "version": 1, "added_sections": [], "reserved_entries": [], "reference_read": []}))
            reserved = {entry for entry in state["reserved_entries"] if entry.startswith("kana:")}
            reviewed = {f"kana:{row['content_id']}:{row['script']}" for row in self.store.queries.reviewed_kana_rows()}
            return frozenset(reserved | reviewed)
        return self._once("enrolled_kana", read)

    def kana_graph(self) -> Mapping[str, tuple[str, ...]]:
        """The kana prerequisite graph the kana lane gates on (kana_dependency_runtime, bundled)."""
        def read():
            from ...kana_dependency_runtime import kana_dependency_graph
            return kana_dependency_graph()
        return self._once("kana_graph", read)

    def availability(self):
        """This revision's content availability view (catalogue, live library registry, learned rows)."""
        def read():
            from ...learning_store import availability_view
            return availability_view(self.store)
        return self._once("availability", read)

    def library_titles(self) -> Mapping[str, str]:
        def read():
            from ...content_library_service import library_titles
            return library_titles(self.store)
        return self._once("library_titles", read)

    def prerequisite_overrides(self) -> Mapping[str, tuple[str, ...]]:
        """Enrolled lessons whose prerequisites a personal version revises, with the prerequisites it gives.

        The owners decide: personal_content_versions.changed_resources (the
        small index of active heads and holds: one settings scan for each)
        lists the lessons with a revised "prerequisites" field, and
        personal_graph.dependency_override (as LearningStore's blocking uses
        it) resolves each listed enrolled lesson; nothing is read per member.
        """
        def read():
            from ...personal_content_versions import changed_resources
            lessons = self.lessons()
            listed = [identity for (kind, identity), fields in changed_resources(self.store).items()
                      if kind == "lesson" and "prerequisites" in fields and identity in lessons]
            if not listed:
                return {}
            from ...learning_store import LearningStore
            from ...personal_graph import dependency_override
            learning, found = LearningStore.reader(self.store), {}
            for identity in listed:
                item = learning.get_item(identity)
                revised = dependency_override(learning, item) if item is not None else None
                if revised is not None:
                    found[identity] = _dependencies(revised["ids"])
            return found
        return self._once("prerequisite_overrides", read)

    def catalogue_dependencies(self, content_id: str) -> tuple[str, ...]:
        """The catalogue item's current prerequisites (the bundled overlay effective_dependencies adds).

        Read from the process cache learning_dependencies keeps of the bundled
        lessons (the catalogue is a process constant), not per revision.
        """
        from ...learning_dependencies import bundled_dependencies
        return bundled_dependencies(content_id)
