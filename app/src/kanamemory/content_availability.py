"""Read-time availability of learned content (design 08 sections 7.2 and 8).

Pure rules: no Toga, platform, network or database. The Store builds one
AvailabilityView per read action from the effective catalogue (a process
constant), the library registry and the learner's content rows; nothing here
is persisted and progress rows are never changed.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace

from .content_library_rules import CORE_LIBRARY_ID

AVAILABLE = "available"
ORPHANED = "orphaned"
BLOCKED_DEPENDENCY = "blocked_dependency"
RETIRED = "retired"
STATES = (AVAILABLE, ORPHANED, BLOCKED_DEPENDENCY, RETIRED)
UNAVAILABLE_STATES = frozenset({ORPHANED, BLOCKED_DEPENDENCY, RETIRED})

# Personal/AI material belongs to no library (its own material gates apply).
ALWAYS_AVAILABLE_PREFIXES = ("local:",)
# Only these families come from libraries; other dependency ids (kana:...)
# are served by the non-removable dictionary layer of the core library.
LIBRARY_FAMILIES = ("word:", "grammar:", "pronunciation:")

# One wording per library state for every page (review B-06): availability
# tags, the home page, routes and the library list use these constants.
LIBRARY_DISABLED_TEXT = "所属内容库已停用"
LIBRARY_MISSING_TEXT = "所属内容库未安装"
LIBRARY_DAMAGED_TEXT = "所属内容库已损坏"
# Disabling acts on dispatch at once (the registry is read live); the merged
# catalogue and the route set are process constants until the next start.
LIBRARY_CHANGE_NOTE = "停用后立即不再派题，重启后从目录与路线中移除"

STATE_TEXT = {ORPHANED: LIBRARY_DISABLED_TEXT, BLOCKED_DEPENDENCY: "依赖缺失", RETIRED: "已被取代"}
REFUSALS = {
    ORPHANED: "这项内容所属的内容库已停用或已移除。学习记录保留，重新启用该库后可继续。",
    BLOCKED_DEPENDENCY: "这项内容依赖的前置内容所在的内容库已停用或缺失，暂时不能继续。",
    RETIRED: "这项内容已被内容库中的新版本取代，不能再开始或追加练习。",
}
LIBRARY_DISABLED_REASON = "library_disabled"


@dataclass(frozen=True)
class Catalogue:
    """What the effective catalogue serves in this process.

    ids: content ids the merged catalogue defines. providers: content id ->
    extension library id (core items are absent). retires: content id ->
    replaced_by (or None) from loaded library manifests. word_exists answers
    whether a dictionary word id exists in the core dictionary layer (None:
    unknown, dictionary-only words are then taken as available). ready_ids:
    contents whose prerequisite eligibility was already earned. dependencies:
    content id -> depends_on of the catalogue item (used when a row brings none).
    effective: the process's content_pack.effective_catalogue() (duck-typed:
    library_of, missing_required_references, retirement); when given it is the
    authority for owning library, retirement and blocked references, and the
    providers/retires mappings are only consulted without it.
    """
    ids: frozenset = frozenset()
    providers: Mapping = field(default_factory=dict)
    retires: Mapping = field(default_factory=dict)
    dependencies: Mapping = field(default_factory=dict)
    word_exists: Callable | None = None
    ready_ids: frozenset = frozenset()
    effective: object = None

    def provider(self, content_id):
        """Extension library serving ``content_id`` (None for core items and non-library ids)."""
        if self.effective is not None:
            owner = self.effective.library_of(content_id)
            return None if owner in (None, CORE_LIBRARY_ID) else owner
        return self.providers.get(content_id)

    def replaced_by(self, content_id):
        """(declared, replacement id or None) of a library retirement."""
        if self.effective is not None:
            row = self.effective.retirement(content_id)
            return (row is not None, row.get("replaced_by") if row else None)
        return (content_id in self.retires, self.retires.get(content_id))

    def missing_references(self, content_id):
        """Required references the merge found missing (08 section 9.1 L4)."""
        if self.effective is None:
            return ()
        return tuple(self.effective.missing_required_references(content_id))


def library_state_text(registry, library_id):
    """LIBRARY_DISABLED/MISSING/DAMAGED_TEXT for a library that serves nothing now, else None."""
    if registry is None:
        return None
    entry = next((row for row in registry.get("libraries", ()) if row.get("library_id") == library_id), None)
    if entry is None:
        return LIBRARY_MISSING_TEXT
    if entry.get("state") == "damaged":
        return LIBRARY_DAMAGED_TEXT
    return None if entry.get("enabled") else LIBRARY_DISABLED_TEXT


def library_usable(registry, library_id):
    """True while the live registry lets library_id serve content (enabled, not damaged)."""
    return _library_enabled(registry, library_id)


def _library_enabled(registry, library_id):
    if registry is None:
        return True
    for entry in registry.get("libraries", ()):
        if entry.get("library_id") == library_id:
            return bool(entry.get("enabled")) and entry.get("state") != "damaged"
    return False


def dictionary_word_id(content_id):
    """word:<word_id>:<reading>:<sense_id> -> word_id (dictionary-only items)."""
    if not isinstance(content_id, str) or not content_id.startswith("word:"):
        return None
    parts = content_id[len("word:"):].rsplit(":", 2)
    return parts[0] if len(parts) == 3 and parts[0] else None


def _dictionary_served(catalogue, content_id, dictionary_only):
    word_id = dictionary_word_id(content_id)
    if word_id is None:
        return False
    if catalogue.word_exists is None:
        # The dictionary layer is not removable (08 N4): without a lookup a
        # snapshot that says it came from the dictionary keeps being served.
        return bool(dictionary_only)
    try:
        return bool(catalogue.word_exists(word_id))
    except (LookupError, ValueError, TypeError):
        return False


def served(catalogue, registry, content_id):
    """True when some enabled layer serves ``content_id`` (dependency check)."""
    if not isinstance(content_id, str):
        return False
    if content_id.startswith(ALWAYS_AVAILABLE_PREFIXES) or not content_id.startswith(LIBRARY_FAMILIES):
        return True
    if content_id in catalogue.ids:
        provider = catalogue.provider(content_id)
        return provider is None or _library_enabled(registry, provider)
    return _dictionary_served(catalogue, content_id, False)


def missing_dependencies(catalogue, registry, depends_on):
    """Library-family dependency ids that no enabled layer serves."""
    if not isinstance(depends_on, (list, tuple)):
        return ()
    return tuple(dep for dep in depends_on if isinstance(dep, str) and not served(catalogue, registry, dep))


def _dependencies(catalogue, content_id, item_row):
    value = item_row.get("depends_on") if isinstance(item_row, Mapping) else None
    return value if value is not None else catalogue.dependencies.get(content_id, ())


def content_availability(catalogue, registry, item_row):
    """available | orphaned | blocked_dependency | retired for one content row.

    item_row: a mapping with ``content_id`` and optionally ``depends_on``
    (else the catalogue item's list) and ``dictionary_only``. local:* material is
    always available; so is everything while ``registry`` is None (a learner
    database never opened under library management keeps the single-pack
    behaviour; every App start writes the registry). A dictionary-only word is
    available while the
    dictionary layer has its word. A dependency that no enabled layer serves
    blocks the item unless its prerequisite eligibility was already earned
    (historical eligibility is never revoked, 08 section 9.2).
    """
    content_id = item_row.get("content_id") if isinstance(item_row, Mapping) else None
    if not isinstance(content_id, str) or not content_id:
        return ORPHANED
    if registry is None or content_id.startswith(ALWAYS_AVAILABLE_PREFIXES):
        return AVAILABLE
    declared, replaced_by = catalogue.replaced_by(content_id)
    if (declared and isinstance(replaced_by, str) and replaced_by != content_id
            and served(catalogue, registry, replaced_by)):
        return RETIRED
    if content_id in catalogue.ids:
        provider = catalogue.provider(content_id)
        if provider is not None and not _library_enabled(registry, provider):
            return ORPHANED
    elif not _dictionary_served(catalogue, content_id, item_row.get("dictionary_only") is True):
        return ORPHANED
    missing = (*missing_dependencies(catalogue, registry, _dependencies(catalogue, content_id, item_row)),
               *catalogue.missing_references(content_id))
    if any(dep not in catalogue.ready_ids for dep in missing):
        return BLOCKED_DEPENDENCY
    return AVAILABLE


@dataclass(frozen=True)
class AvailabilityView:
    """One read action's {content id -> state} projection; never persisted.

    ``states`` holds every learned row; state_of() also answers for content
    that has no progress yet (a new enrollment target).
    """
    catalogue: Catalogue
    registry: Mapping | None
    states: Mapping

    @property
    def unavailable_ids(self) -> frozenset:
        return frozenset(cid for cid, state in self.states.items() if state in UNAVAILABLE_STATES)

    @property
    def orphaned_ids(self) -> frozenset:
        return frozenset(cid for cid, state in self.states.items() if state == ORPHANED)

    def state_of(self, content_id, item_row=None):
        if content_id in self.states:
            return self.states[content_id]
        row = {"content_id": content_id, **({} if item_row is None else dict(item_row))}
        return content_availability(self.catalogue, self.registry, row)

    def available(self, content_id, item_row=None) -> bool:
        return self.state_of(content_id, item_row) == AVAILABLE

    def counts(self) -> dict:
        result = dict.fromkeys(STATES, 0)
        for state in self.states.values():
            result[state] += 1
        return result

    def refusal(self, content_id, item_row=None):
        """User-visible reason a write is refused, or None when allowed."""
        state = self.state_of(content_id, item_row)
        return REFUSALS.get(state)


def library_of_content(catalogue, content_id):
    """The library a learned id belongs to for per-library counts, or None.

    The owning extension library of the merged catalogue, else the installed
    but unloaded library (disabled or damaged at start) that provides it.
    Core items and ids no library names give None.
    """
    owner = catalogue.provider(content_id)
    if owner is not None:
        return owner
    effective = catalogue.effective
    unloaded = getattr(effective, "unavailable_library", None) if effective is not None else None
    return unloaded(content_id) if callable(unloaded) else None


def build_view(catalogue, registry, rows: Iterable, *, ready_ids=None) -> AvailabilityView:
    """Project learned rows; ``ready_ids`` (callable) runs only if a dependency is missing."""
    rows = [dict(row) for row in rows]
    if ready_ids is not None and any(missing_dependencies(catalogue, registry, _dependencies(catalogue, row["content_id"], row))
                                     or catalogue.missing_references(row["content_id"]) for row in rows):
        catalogue = replace(catalogue, ready_ids=frozenset(ready_ids()))
    states = {row["content_id"]: content_availability(catalogue, registry, row) for row in rows}
    return AvailabilityView(catalogue=catalogue, registry=registry, states=states)


def state_text(state):
    return STATE_TEXT.get(state, "")
