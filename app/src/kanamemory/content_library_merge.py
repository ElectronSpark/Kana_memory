"""Pure multi-library merge rules (design 08 phase 3/4: sections 6.1-6.3, 9.1-9.4).

The built-in policy table of section 6.2 decides, per namespace key, how
enabled libraries combine: items_by_id (same id: same fingerprint dedupes,
different fingerprint resolves by priority unless an accepted override),
map_by_key (same rule on plain keys), contribution (additive, never
overwriting; extension rows only take effect where their consumers exist) and
singleton (core only). A manifest may declare a stricter policy, never a
looser one. EffectiveCatalogue is the read-only result of one startup merge:
which library owns each id, which required references are missing, retires,
aliases and accepted overrides. Nothing here touches Toga, the platform, the
network, files or the learner database; inputs are plain decoded payloads.

Parallel study routes (design 10 sections 6.1/10.1, WP-R4a): the namespace
``study_routes`` holds {"routes": {route_id: spec}} merged items_by_id on
route_id. A route_id must start with ``route:<library_id>:`` of the library
that provides it; other rows are dropped (never raised) and reported in
MergeReport.refused_entries and EffectiveCatalogue.refused_routes.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json

CORE_LIBRARY_ID = "kanamemory.core"
POLICIES = ("contribution", "map_by_key", "items_by_id", "singleton")
STRICTNESS = {name: index for index, name in enumerate(POLICIES)}

# Section 6.2. Keys are the top-level keys of a namespace payload.
SINGLETON_KEYS = frozenset({
    "STAGES", "PATH_VERSION", "COHERENCE_RULES", "GRAMMAR_ROUTE_CONFIG", "PRONUNCIATION_ROUTE_CONFIG",
    "FORM_BASES", "TE_FORMS", "CONTENT_REVISION", "VOCABULARY_REVISION", "REVISION", "revision", "version",
})
CONTRIBUTION_KEYS = frozenset({
    "SEMANTIC_REQUIREMENTS", "FORM_CONSUMERS", "WORD_TOPICS", "GRAMMAR_TOPICS", "UNIT_CONTEXT_TITLES",
    "VOCABULARY_WEAVE", "SMALL_UNIT_PLANS", "SOURCES", "SOURCE_RECORDS",
})
# Top-level keys whose dict keys (or row ids) are content ids.
ITEM_TABLE_KEYS = frozenset({"_WORDS", "_GRAMMAR", "_ITEMS", "WORD_LESSONS", "items"})
# Item tables that hold learning-catalogue items (``items`` holds per-id templates).
CATALOGUE_TABLE_KEYS = frozenset({"_WORDS", "_GRAMMAR", "_ITEMS", "WORD_LESSONS"})
CATALOGUE_NAMESPACES = ("learning_content", "grammar_curriculum", "exam_grammar_curriculum",
                        "usage_grammar_curriculum", "pronunciation_curriculum", "vocabulary_catalog")
NAMESPACE_POLICIES = {
    **{name: "items_by_id" for name in (
        "learning_content", "grammar_curriculum", "exam_grammar_curriculum", "usage_grammar_curriculum",
        "pronunciation_curriculum", "vocabulary_catalog", "example_catalogue", "example_expansion_grammar_a",
        "example_expansion_grammar_a_tail", "example_expansion_grammar_b", "example_expansion_pronunciation",
        "example_expansion_words", "example_expansion_signatures", "structured_lessons_grammar_a",
        "structured_lessons_grammar_b", "structured_lessons_pronunciation", "structured_lessons_words",
        "lesson_diagrams_grammar_a", "lesson_diagrams_grammar_b", "lesson_diagrams_words_sounds",
        "learning_questions", "varied_practice", "context_choice", "word_sound_mix", "question_contexts",
        "reading_annotations", "semantic_assessments", "pitch_sequences")},
    **{name: "map_by_key" for name in ("presentation_metadata", "foundation_content",
                                        "sentence_annotation_overrides", "vocabulary_lessons")},
    "course_prerequisites": "contribution",
    "study_path_catalog": "contribution",
    "study_routes": "items_by_id",
}
ROUTE_NAMESPACE = "study_routes"
ROUTE_TABLE_KEY = "routes"
ROUTE_ID_PREFIX = "route:"
KEY_POLICIES = {
    **{("course_prerequisites", key): "singleton" for key in (
        "VERSION", "TE_FORMS", "NAI_FORMS", "VERB_PLAIN_FORMS", "PLAIN_FORMS", "NONPAST_FORMS", "FORM_BASES")},
    **{("study_path_catalog", key): "singleton" for key in (
        "PATH_VERSION", "STAGES", "COHERENCE_RULES", "GRAMMAR_ROUTE_CONFIG", "PRONUNCIATION_ROUTE_CONFIG",
        # MAIN_ROUTE_ORDER: the given beginning of the core main route (main_route_order).
        "COHERENCE_GROUPS", "EXAM_COHERENCE", "MAIN_ROUTE_ORDER")},
    **{("vocabulary_lessons", key): "singleton" for key in ("VOCABULARY_REVISION", "SOURCE_ID")},
}


def route_prefix(library_id):
    """The route_id prefix a library may use: ``route:<library_id>:``."""
    return f"{ROUTE_ID_PREFIX}{library_id}:"


def route_library_id(route_id):
    """The library_id a well-formed route_id names, or None."""
    if not isinstance(route_id, str) or not route_id.startswith(ROUTE_ID_PREFIX):
        return None
    library_id, separator, key = route_id[len(ROUTE_ID_PREFIX):].partition(":")
    return library_id if separator and library_id and key.strip() else None


def route_id_problem(library_id, route_id, spec=None):
    """Why a study_routes row of library_id is refused at merge, or None.

    Only identity is judged here (prefix, a non-empty key, a mapping spec whose
    own route_id, when given, agrees); the full spec check is
    study_routes.validate_route_spec (WP-R1).
    """
    if not isinstance(route_id, str) or len(route_id) > 512 or "\x00" in route_id:
        return "route_id is not text"
    if route_library_id(route_id) != library_id:
        return "route_id must start with " + route_prefix(library_id)
    if spec is not None and not isinstance(spec, Mapping):
        return "route spec is not a mapping"
    if isinstance(spec, Mapping) and "route_id" in spec and spec["route_id"] != route_id:
        return "route spec names another route_id"
    return None


def filter_route_payload(library_id, payload):
    """(payload with only well-formed routes of library_id, refused rows).

    refused rows: (library_id, key, route_id|"*", reason). The input is never
    mutated; a payload without refused rows is returned as is.
    """
    if not isinstance(payload, Mapping):
        return payload, ((library_id, "*", "*", "study_routes payload is not a mapping"),)
    table = payload.get(ROUTE_TABLE_KEY)
    if table is None:
        return payload, ()
    if not isinstance(table, Mapping):
        return {**payload, ROUTE_TABLE_KEY: {}}, ((library_id, ROUTE_TABLE_KEY, "*", "routes is not a mapping"),)
    refused, kept = [], {}
    for route_id, spec in table.items():
        problem = route_id_problem(library_id, route_id, spec)
        if problem:
            refused.append((library_id, ROUTE_TABLE_KEY, str(route_id)[:200], problem))
        else:
            kept[route_id] = spec
    if not refused:
        return payload, ()
    return {**payload, ROUTE_TABLE_KEY: kept}, tuple(refused)


class ContentMergeError(ValueError):
    """The effective catalogue cannot be built; the caller falls back to core only."""

    def __init__(self, message, library_ids=()):
        super().__init__(message)
        self.library_ids = tuple(library_ids)


def builtin_policy(namespace, key):
    """The section 6.2 policy of one top-level key (no manifest declaration)."""
    if (namespace, key) in KEY_POLICIES:
        return KEY_POLICIES[(namespace, key)]
    if key in SINGLETON_KEYS:
        return "singleton"
    if key in CONTRIBUTION_KEYS:
        return "contribution"
    if namespace in NAMESPACE_POLICIES:
        policy = NAMESPACE_POLICIES[namespace]
        return "map_by_key" if policy == "contribution" else policy
    return "items_by_id" if key in ITEM_TABLE_KEYS else "map_by_key"


def effective_policy(namespace, key, declared=()):
    """(policy, refused) for one key; declared is [(library_id, policy)].

    A declaration may only tighten the built-in policy. Looser declarations
    are refused and reported; singleton is accepted from the core library only.
    """
    policy, refused = builtin_policy(namespace, key), []
    for library_id, wanted in declared:
        if wanted not in STRICTNESS or wanted == "singleton" and library_id != CORE_LIBRARY_ID:
            refused.append((library_id, namespace, wanted, policy))
        elif STRICTNESS[wanted] < STRICTNESS[builtin_policy(namespace, key)]:
            refused.append((library_id, namespace, wanted, builtin_policy(namespace, key)))
        elif STRICTNESS[wanted] > STRICTNESS[policy]:
            policy = wanted
    return policy, refused


def canonical(value):
    """A JSON-safe canonical form that keeps tuple/list/set distinct."""
    if isinstance(value, Mapping):
        return {"m": sorted(([str(k), canonical(v)] for k, v in value.items()), key=lambda pair: pair[0])}
    if isinstance(value, (set, frozenset)):
        return {"s": sorted((canonical(v) for v in value), key=lambda item: json.dumps(item, sort_keys=True))}
    if isinstance(value, tuple):
        return {"t": [canonical(v) for v in value]}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _same(left, right, fingerprint=None):
    if fingerprint is not None:
        return fingerprint(left) == fingerprint(right)
    return left is right or digest(left) == digest(right)


def _is_row_table(value):
    return (isinstance(value, (list, tuple)) and value
            and all(isinstance(row, Mapping) and isinstance(row.get("id"), str) for row in value))


def _revision_clash(left, right):
    return (isinstance(left, Mapping) and isinstance(right, Mapping) and "revision" in left and "revision" in right
            and str(left["revision"]) == str(right["revision"]))


@dataclass(frozen=True)
class MergeReport:
    conflicts: tuple = ()           # (key, entry_key, winner_library, loser_library)
    ignored_singletons: tuple = ()  # (library_id, key)
    contributed: tuple = ()         # (library_id, key, count)
    duplicates: tuple = ()          # (key, entry_key, kept_library, same_content_library)  rule a
    overridden: tuple = ()          # (key, entry_key, winner_library, replaced_library)   rule b, accepted
    revision_conflicts: tuple = ()  # (key, entry_key, winner_library, loser_library)       rule c
    refused_policies: tuple = ()    # (library_id, namespace, declared, kept)
    contributions: tuple = ()       # (key, entry_key|None, library_id, added items|None)
    refused_entries: tuple = ()     # (library_id, key, entry_key, reason)  study_routes prefix rule


class _Collector:
    def __init__(self):
        self.rows = {name: [] for name in MergeReport.__dataclass_fields__}

    def add(self, name, row):
        if row not in self.rows[name]:
            self.rows[name].append(row)

    def report(self):
        return MergeReport(**{name: tuple(rows) for name, rows in self.rows.items()})


def _sequence_items(value):
    return tuple(value) if isinstance(value, (list, tuple, set, frozenset)) else None


def _union(current, extra):
    """current + unseen items of extra, keeping current's container type."""
    seen = {digest(item) for item in current}
    added = [item for item in extra if digest(item) not in seen and not seen.add(digest(item))]
    if not added:
        return current, ()
    if isinstance(current, (set, frozenset)):
        return type(current)([*current, *added]), tuple(added)
    merged = [*current, *added]
    return (tuple(merged) if isinstance(current, tuple) else merged), tuple(added)


def _merge_mapping(key, layers, policy, collect, fingerprint, accepted):
    first_id, first = layers[0]
    result, owner = dict(first), {entry: first_id for entry in first}
    for library_id, table in layers[1:]:
        added = 0
        for entry_key, entry in table.items():
            if entry_key not in result:
                result[entry_key], owner[entry_key] = entry, library_id
                added += 1
                if policy == "contribution":
                    collect.add("contributions", (key, entry_key, library_id, _sequence_items(entry)))
                continue
            current = result[entry_key]
            if policy == "contribution":
                if _sequence_items(current) is not None and _sequence_items(entry) is not None:
                    merged, new = _union(current, entry)
                    if new:
                        result[entry_key] = merged
                        collect.add("contributions", (key, entry_key, library_id, new))
                        added += 1
                    continue
                if isinstance(current, Mapping) and isinstance(entry, Mapping):
                    merged, new = dict(current), []
                    for sub_key, sub_value in entry.items():
                        if sub_key not in merged:
                            merged[sub_key] = sub_value
                            new.append(sub_key)
                        elif not _same(merged[sub_key], sub_value):
                            collect.add("conflicts", (key, entry_key, owner[entry_key], library_id))
                    if new:
                        result[entry_key] = merged
                        collect.add("contributions", (key, entry_key, library_id, tuple(new)))
                        added += 1
                    continue
            item_fingerprint = fingerprint if policy == "items_by_id" else None
            if _same(current, entry, item_fingerprint):
                collect.add("duplicates", (key, entry_key, owner[entry_key], library_id))
            elif policy != "contribution" and (library_id, entry_key) in accepted:
                collect.add("overridden", (key, entry_key, library_id, owner[entry_key]))
                result[entry_key], owner[entry_key] = entry, library_id
            else:
                collect.add("conflicts", (key, entry_key, owner[entry_key], library_id))
                if policy == "items_by_id" and _revision_clash(current, entry):
                    collect.add("revision_conflicts", (key, entry_key, owner[entry_key], library_id))
        if added:
            collect.add("contributed", (library_id, key, added))
    return result


def _merge_rows(key, layers, policy, collect, fingerprint, accepted):
    first_id, first = layers[0]
    rows, owner = list(first), {row["id"]: first_id for row in first}
    position = {row["id"]: index for index, row in enumerate(rows)}
    for library_id, table in layers[1:]:
        added = 0
        for row in table:
            if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
                continue
            entry_key = row["id"]
            if entry_key not in position:
                position[entry_key], owner[entry_key] = len(rows), library_id
                rows.append(row)
                added += 1
                if policy == "contribution":
                    collect.add("contributions", (key, entry_key, library_id, None))
                continue
            current = rows[position[entry_key]]
            if _same(current, row, fingerprint if policy == "items_by_id" else None):
                collect.add("duplicates", (key, entry_key, owner[entry_key], library_id))
            elif policy != "contribution" and (library_id, entry_key) in accepted:
                collect.add("overridden", (key, entry_key, library_id, owner[entry_key]))
                rows[position[entry_key]], owner[entry_key] = row, library_id
            else:
                collect.add("conflicts", (key, entry_key, owner[entry_key], library_id))
                if policy == "items_by_id" and _revision_clash(current, row):
                    collect.add("revision_conflicts", (key, entry_key, owner[entry_key], library_id))
        if added:
            collect.add("contributed", (library_id, key, added))
    return tuple(rows) if isinstance(first, tuple) else rows


def merge_key(namespace, key, layers, *, policy, collect, fingerprint=None, accepted=frozenset()):
    """Merge one top-level key; layers [(library_id, value)] in priority order."""
    first_id, first = layers[0]
    if len(layers) == 1:
        return first
    if policy == "singleton":
        for library_id, _ in layers[1:]:
            collect.add("ignored_singletons", (library_id, key))
        return first
    values = [value for _, value in layers]
    if all(isinstance(value, Mapping) for value in values):
        return _merge_mapping(key, layers, policy, collect, fingerprint, accepted)
    if all(_is_row_table(value) or isinstance(value, (list, tuple)) and not value for value in values) \
            and any(_is_row_table(value) for value in values):
        return _merge_rows(key, layers, policy, collect, fingerprint, accepted)
    if all(isinstance(value, (list, tuple)) for value in values):
        result = first
        for library_id, value in layers[1:]:
            result, added = _union(result, value)
            if added:
                collect.add("contributed", (library_id, key, len(added)))
                if policy == "contribution":
                    collect.add("contributions", (key, None, library_id, added))
        return result
    for library_id, value in layers[1:]:
        if policy == "contribution" or not _same(first, value):
            collect.add("conflicts", (key, None, first_id, library_id))
    return first


def merge_namespace(layers, *, namespace=None, declared_policies=(), fingerprint=None, overrides_accepted=()):
    """Merge plain namespace payloads in priority order (sections 6.2/6.3).

    layers: [(library_id, payload)], highest priority first (None payloads are
    skipped). declared_policies: [(library_id, policy)] from the manifests'
    merge_policies for this namespace. overrides_accepted: (library_id, id)
    pairs that the user accepted for ids the library declares in overrides.
    Returns (merged, MergeReport); inputs are never mutated.
    """
    layers = [(library_id, payload) for library_id, payload in layers if payload is not None]
    if not layers:
        return None, MergeReport()
    collect = _Collector()
    if namespace == ROUTE_NAMESPACE:
        filtered = []
        for library_id, payload in layers:
            payload, refused = filter_route_payload(library_id, payload)
            for row in refused:
                collect.add("refused_entries", row)
            filtered.append((library_id, payload))
        layers = filtered
    first_id, first = layers[0]
    if len(layers) == 1:
        return first, collect.report()
    if not isinstance(first, Mapping) or any(not isinstance(payload, Mapping) for _, payload in layers[1:]):
        for library_id, _ in layers[1:]:
            collect.add("ignored_singletons", (library_id, "*"))
        return first, collect.report()
    accepted = frozenset(overrides_accepted)
    merged = {}
    keys = list(first)
    for _, payload in layers[1:]:
        keys.extend(key for key in payload if key not in keys)
    for key in keys:
        present = [(library_id, payload[key]) for library_id, payload in layers if key in payload]
        policy, refused = effective_policy(namespace, key, declared_policies)
        for row in refused:
            collect.add("refused_policies", row)
        if policy == "singleton" and present[0][0] != first_id:
            for library_id, _ in present:
                collect.add("ignored_singletons", (library_id, key))
            continue
        merged[key] = merge_key(namespace, key, present, policy=policy, collect=collect,
                                fingerprint=fingerprint, accepted=accepted)
    return merged, collect.report()


def catalogue_items(payload):
    """(content_id, item) pairs from the learning-catalogue tables of one payload."""
    found = []
    if not isinstance(payload, Mapping):
        return found
    for key in sorted(CATALOGUE_TABLE_KEYS):
        table = payload.get(key)
        if isinstance(table, Mapping):
            found.extend((cid, item) for cid, item in table.items() if isinstance(cid, str) and isinstance(item, Mapping))
        elif isinstance(table, (list, tuple)):
            found.extend((row["id"], row) for row in table
                         if isinstance(row, Mapping) and isinstance(row.get("id"), str))
    return found


def _dependencies(item):
    value = item.get("depends_on", ()) if isinstance(item, Mapping) else ()
    return [cid for cid in value if isinstance(cid, str)] if isinstance(value, (list, tuple)) else []


# ---------------------------------------------------------------- effective catalogue

@dataclass(frozen=True)
class ExtensionLibrary:
    """One enabled, checked extension library: its manifest and decoded payloads."""
    library_id: str
    manifest: dict
    payloads: dict  # namespace -> decoded payload


@dataclass(frozen=True)
class UnavailableLibrary:
    """An installed library that is not part of the effective catalogue."""
    library_id: str
    provided_ids: tuple
    reason: str  # disabled | damaged | failed


class EffectiveCatalogue:
    """Read-only result of one startup merge (process constant, see 08 §6.1).

    Interface for availability consumers (08 phase 1 remainder):
      library_of(content_id) -> library_id | None
      missing_required_references(content_id) -> tuple[str, ...]
    plus is_unavailable, retirement, alias_target, providers_of, blocked_ids.

    Study routes (design 10, WP-R4a): study_routes() lists the extension
    libraries' accepted (route_id, library_id, spec) rows in priority order;
    route_library(route_id) names the providing library; refused_routes holds
    (library_id, key, route_id, reason) rows dropped by the prefix rule.
    """

    def __init__(self, *, library_ids=(CORE_LIBRARY_ID,), owners=None, providers=None, core_ids=None,
                 missing=None, dropped_optional=None, alias_rewrites=None, retired=None, aliases=None,
                 unavailable=None, missing_libraries=None, accepted_overrides=(), payloads=None,
                 declared_policies=None, failed=None, diagnostics=(), token="", fallback=False,
                 route_owners=None, refused_routes=(), max_dependency_depth=0):
        self.library_ids = tuple(library_ids)
        self._owners = dict(owners or {})  # extension-owned ids only
        self._providers = {cid: tuple(libs) for cid, libs in (providers or {}).items()}
        self._core_ids = core_ids  # frozenset or a zero-argument callable (lazy)
        self._missing = {cid: tuple(ids) for cid, ids in (missing or {}).items() if ids}
        self.dropped_optional = {cid: tuple(ids) for cid, ids in (dropped_optional or {}).items() if ids}
        self.alias_rewrites = {cid: tuple(pairs) for cid, pairs in (alias_rewrites or {}).items() if pairs}
        self._retired = dict(retired or {})
        self._aliases = dict(aliases or {})
        self._unavailable = dict(unavailable or {})
        self.missing_libraries = {lib: tuple(ids) for lib, ids in (missing_libraries or {}).items() if ids}
        self.accepted_overrides = frozenset(accepted_overrides)
        self.payloads = payloads or {}  # library_id -> namespace -> cleaned payload
        self.declared_policies = declared_policies or {}  # namespace -> [(library_id, policy)]
        self.failed = dict(failed or {})
        self.diagnostics = tuple(diagnostics)
        self.token = token
        self.fallback = fallback
        self._route_owners = dict(route_owners or {})  # route_id -> library_id (extension libraries)
        self.refused_routes = tuple(refused_routes)
        self.max_dependency_depth = int(max_dependency_depth)  # longest extension prerequisite chain

    # -- core id lookup (lazy: decoding the core catalogue is done once, on demand)
    def core_ids(self):
        if callable(self._core_ids):
            self._core_ids = frozenset(self._core_ids())
        return self._core_ids or frozenset()

    @property
    def extension_ids(self):
        return tuple(lib for lib in self.library_ids if lib != CORE_LIBRARY_ID)

    def library_of(self, content_id):
        """The library whose version of content_id is effective, or None.

        None for ids no enabled library provides (orphans, dictionary-only words,
        kana, local:* material): callers keep their own rule for those.
        """
        if content_id in self._owners:
            return self._owners[content_id]
        return CORE_LIBRARY_ID if content_id in self.core_ids() else None

    def missing_required_references(self, content_id):
        """Required prerequisites (transitively) absent from the effective catalogue.

        Non-empty means blocked_dependency (08 §7.2/§9.1): the item stays in the
        catalogue for display but must not be newly enrolled. Already-learned
        progress is unaffected.
        """
        return self._missing.get(content_id, ())

    def blocked_ids(self):
        return frozenset(self._missing)

    def providers_of(self, content_id):
        """Every enabled library that defines content_id, in priority order."""
        if content_id in self._providers:
            return self._providers[content_id]
        return (CORE_LIBRARY_ID,) if content_id in self.core_ids() else ()

    def is_unavailable(self, content_id):
        """True when an installed but disabled/damaged library provides the id."""
        return content_id in self._unavailable and self.library_of(content_id) is None

    def unavailable_library(self, content_id):
        return self._unavailable.get(content_id) if self.library_of(content_id) is None else None

    def retirement(self, content_id):
        """{'library_id', 'replaced_by', 'replacement_available', 'reason'} or None."""
        row = self._retired.get(content_id)
        if row is None:
            return None
        owner = self.library_of(content_id)
        if owner is not None and owner != row["library_id"]:
            return None
        replaced = row.get("replaced_by")
        return {**row, "replacement_available": bool(replaced) and self.library_of(replaced) is not None}

    def alias_target(self, content_id):
        """The new id an enabled library declares for an old id (new enrolment only)."""
        row = self._aliases.get(content_id)
        return row[0] if row else None

    def aliases(self):
        return {old: new for old, (new, _) in self._aliases.items()}

    def route_library(self, route_id):
        """The extension library whose study_routes row route_id is, or None."""
        return self._route_owners.get(route_id)

    def study_route_ids(self):
        return tuple(self._route_owners)

    def study_routes(self):
        """((route_id, library_id, spec), ...) of extension libraries, priority order.

        Specs come from the cleaned payloads (prefix rule applied); they are
        the stored objects, so callers copy before changing anything. Core
        library rows (if the core pack ever carries study_routes) are read
        through content_pack.load_namespace("study_routes").
        """
        rows = []
        for route_id, library_id in self._route_owners.items():
            table = self.payloads.get(library_id, {}).get(ROUTE_NAMESPACE, {})
            table = table.get(ROUTE_TABLE_KEY, {}) if isinstance(table, Mapping) else {}
            if route_id in table:
                rows.append((route_id, library_id, table[route_id]))
        return tuple(rows)


def _accepted(manifests, overrides_accepted):
    declared = {(lib, row["id"]) for lib, manifest in manifests.items() for row in manifest.get("overrides", ())}
    return frozenset(pair for pair in overrides_accepted if pair in declared)


def build_effective_catalogue(*, core_items, extensions, overrides_accepted=(), unavailable=(), fingerprint=None,
                              core_manifest=None, token=""):
    """Resolve ownership, references and aliases for enabled extension libraries.

    core_items: content id -> core catalogue item. extensions: ExtensionLibrary
    rows in priority order. Raises ContentMergeError for a prerequisite cycle
    through extension content; the caller then falls back to core only.
    Missing optional prerequisites are dropped from the extension items' cleaned
    payloads; missing required (or undeclared) ones are dropped there too and
    recorded, so the catalogue still validates while the items stay blocked.
    """
    library_ids = (CORE_LIBRARY_ID, *[ext.library_id for ext in extensions])
    manifests = {ext.library_id: ext.manifest for ext in extensions}
    accepted = _accepted(manifests, overrides_accepted)
    core_ids = frozenset(core_items)
    owners, providers, items = {}, {cid: [CORE_LIBRARY_ID] for cid in core_ids}, {}
    diagnostics = []
    for ext in extensions:
        found = {}
        for namespace in sorted(ext.payloads):
            for cid, item in catalogue_items(ext.payloads[namespace]):
                found.setdefault(cid, item)
        for cid in ext.manifest.get("provides", {}).get("items", ()):
            found.setdefault(cid, None)
        for cid, item in found.items():
            providers.setdefault(cid, [])
            if ext.library_id not in providers[cid]:
                providers[cid].append(ext.library_id)
            current = owners.get(cid, CORE_LIBRARY_ID if cid in core_ids else None)
            if current is None or (ext.library_id, cid) in accepted:
                owners[cid] = ext.library_id
                if item is not None:
                    items[cid] = item
            elif current != ext.library_id:
                diagnostics.append(("lower_priority", ext.library_id, cid, current))
    effective_ids = core_ids | set(owners)
    aliases = {}
    for ext in extensions:
        for old, new in ext.manifest.get("aliases", {}).items():
            aliases.setdefault(old, (new, ext.library_id))
    retired = {}
    for ext in extensions:
        for row in ext.manifest.get("retires", ()):
            retired.setdefault(row["id"], {"library_id": ext.library_id, "replaced_by": row.get("replaced_by"),
                                           "reason": row.get("reason", "")})
    missing, dropped, rewrites, cleaned_deps = {}, {}, {}, {}
    for cid, owner in owners.items():
        if cid not in items:
            continue
        references = {row["id"]: row["requirement"] for row in manifests[owner].get("references", ())}
        kept = []
        for dependency in _dependencies(items[cid]):
            if dependency in effective_ids:
                kept.append(dependency)
            elif dependency in aliases and aliases[dependency][0] in effective_ids:
                kept.append(aliases[dependency][0])
                rewrites.setdefault(cid, []).append((dependency, aliases[dependency][0]))
            elif references.get(dependency) == "optional":
                dropped.setdefault(cid, []).append(dependency)
            else:
                missing.setdefault(cid, []).append(dependency)
                if dependency not in references:
                    diagnostics.append(("undeclared_reference", owner, cid, dependency))
        kept = list(dict.fromkeys(kept))
        if kept != _dependencies(items[cid]):
            cleaned_deps[cid] = kept
    graph = {}
    for cid in effective_ids:
        if cid in owners:
            graph[cid] = cleaned_deps.get(cid, _dependencies(items.get(cid) or {}))
        else:
            graph[cid] = [d for d in _dependencies(core_items.get(cid) or {}) if d in effective_ids]
    _reject_cycles(graph, owners)
    blocked, depth = _blocked_closure(graph, owners, missing)
    present = set(library_ids)
    missing_libraries = {ext.library_id: [d["library_id"] for d in ext.manifest.get("depends_libraries", ())
                                          if d["library_id"] not in present] for ext in extensions}
    unavailable_ids = {}
    for row in unavailable:
        for cid in row.provided_ids:
            if cid not in effective_ids:
                unavailable_ids.setdefault(cid, row.library_id)
    declared = {}
    for library_id, manifest in [(CORE_LIBRARY_ID, core_manifest or {}), *manifests.items()]:
        for namespace, policy in manifest.get("merge_policies", {}).items():
            declared.setdefault(namespace, []).append((library_id, policy))
    payloads = {ext.library_id: _clean_payloads(ext, owners, cleaned_deps) for ext in extensions}
    route_owners, refused_routes = {}, []
    for ext in extensions:
        if ROUTE_NAMESPACE not in payloads[ext.library_id]:
            continue
        cleaned, refused = filter_route_payload(ext.library_id, payloads[ext.library_id][ROUTE_NAMESPACE])
        payloads[ext.library_id][ROUTE_NAMESPACE] = cleaned
        for row in refused:
            refused_routes.append(row)
            diagnostics.append(("route_refused", row[0], row[2], row[3]))
        table = cleaned.get(ROUTE_TABLE_KEY, {}) if isinstance(cleaned, Mapping) else {}
        for route_id in table:
            route_owners.setdefault(route_id, ext.library_id)
    return EffectiveCatalogue(
        library_ids=library_ids, owners=owners, providers=providers, core_ids=core_ids,
        missing={cid: ids for cid, ids in blocked.items() if ids}, dropped_optional=dropped,
        alias_rewrites=rewrites, retired=retired, aliases=aliases, unavailable=unavailable_ids,
        missing_libraries=missing_libraries, accepted_overrides=accepted, payloads=payloads,
        declared_policies=declared, diagnostics=diagnostics, token=token, route_owners=route_owners,
        refused_routes=refused_routes, max_dependency_depth=max(depth.values(), default=0))


MAX_DEPENDENCY_DEPTH = 512  # install precheck only: longer extension chains are refused (review B-01)


def offline_merge_problems(*, core_items, extensions, overrides_accepted=(), max_depth=MAX_DEPENDENCY_DEPTH):
    """Blocking reasons of a trial build_effective_catalogue for the install precheck.

    A prerequisite cycle, a chain deeper than max_depth or any other failure
    of the merge is returned as display text (never raised): the pack would
    make the startup merge fall back to the core library, so it is refused
    before install. An empty tuple means the trial merge succeeded.
    """
    try:
        catalogue = build_effective_catalogue(core_items=core_items, extensions=extensions,
                                              overrides_accepted=overrides_accepted)
    except ContentMergeError as error:
        return ("依赖成环，合并会失败：" + (str(error).splitlines()[0] if str(error) else "前置关系成环")[:200],)
    except Exception as error:  # noqa: BLE001 - any merge failure is reported as blocking, never installed
        return ("离线合并失败：" + type(error).__name__,)
    if catalogue.max_dependency_depth > max_depth:
        return (f"前置依赖链过深（{catalogue.max_dependency_depth} 层，上限 {max_depth} 层）",)
    return ()


def _blocked_closure(graph, owners, missing):
    """(content id -> transitive missing required ids, content id -> chain depth), iteratively.

    The graph is acyclic here (_reject_cycles ran first). Nodes are finished in
    post-order, so every prerequisite is final before its dependants: the
    same result as the former recursive closure, without Python's recursion
    limit on long extension chains (review B-01). Only extension-owned items
    follow their prerequisites; core items contribute their own (empty) list.
    """
    blocked, depth = {}, {}
    for start in owners:
        if start in blocked:
            continue
        stack = [(start, False)]
        while stack:
            cid, expanded = stack.pop()
            if cid in blocked:
                continue
            children = graph.get(cid, ()) if cid in owners else ()
            if not expanded:
                stack.append((cid, True))
                stack.extend((child, False) for child in reversed(children) if child not in blocked)
                continue
            result = list(missing.get(cid, ()))
            longest = 0
            for child in children:
                result.extend(blocked.get(child, ()))
                longest = max(longest, depth.get(child, 0) + 1)
            blocked[cid] = tuple(dict.fromkeys(result))
            depth[cid] = longest
    return blocked, depth


def _reject_cycles(graph, owners):
    state = {}
    for start in sorted(owners):
        if state.get(start) == 2:
            continue
        stack, path = [(start, iter(graph.get(start, ())))], [start]
        state[start] = 1
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                state[node] = 2
                stack.pop()
                path.pop()
                continue
            if state.get(child) == 1:
                cycle = path[path.index(child):] + [child]
                raise ContentMergeError("Prerequisite cycle through library content: " + " → ".join(cycle),
                                        tuple(dict.fromkeys(owners[cid] for cid in cycle if cid in owners)))
            if state.get(child) is None and child in graph:
                state[child] = 1
                stack.append((child, iter(graph.get(child, ()))))
                path.append(child)


def _clean_payloads(ext, owners, cleaned_deps):
    """Copies of ext payloads whose owned catalogue items carry resolved depends_on."""
    result = {}
    for namespace, payload in ext.payloads.items():
        if not isinstance(payload, Mapping):
            result[namespace] = payload
            continue
        copy = dict(payload)
        for key in CATALOGUE_TABLE_KEYS & set(payload):
            table = payload[key]

            def fixed(cid, item):
                if owners.get(cid) == ext.library_id and cid in cleaned_deps and isinstance(item, Mapping):
                    deps = item.get("depends_on")
                    kind = tuple if isinstance(deps, tuple) else list
                    return {**item, "depends_on": kind(cleaned_deps[cid])}
                return item
            if isinstance(table, Mapping):
                copy[key] = {cid: fixed(cid, item) for cid, item in table.items()}
            elif isinstance(table, (list, tuple)):
                rows = [fixed(row.get("id"), row) if isinstance(row, Mapping) else row for row in table]
                copy[key] = tuple(rows) if isinstance(table, tuple) else rows
        result[namespace] = copy
    return result


def core_only(*, core_ids, unavailable=(), failed=None, token="", fallback=False, diagnostics=()):
    """The effective catalogue when no extension library is (or can be) merged."""
    unavailable_ids = {}
    for row in unavailable:
        for cid in row.provided_ids:
            unavailable_ids.setdefault(cid, row.library_id)
    return EffectiveCatalogue(core_ids=core_ids, unavailable=unavailable_ids, failed=failed, token=token,
                              fallback=fallback, diagnostics=diagnostics)


# ---------------------------------------------------------------- dependency frontier (§9.3)

FRONTIER_POOLS = {"word": "word", "grammar": "grammar", "kana": "kana", "pronunciation": "kana"}


def frontier_limits(*, word_batch, grammar_batch, kana=5):
    """Suggested K of 08 §9.3: vocabulary batch + grammar batch + kana 5."""
    return {"word": max(0, int(word_batch)), "grammar": max(0, int(grammar_batch)), "kana": max(0, int(kana))}


def dependency_frontier(ordered_ids, dependencies, *, targets, enrolled=(), limits, family_of, enroll_all=False):
    """Split a prerequisite-first plan into enrolment batches (08 §9.3).

    ordered_ids: the plan's stable prerequisite-first order (build_dependency_plan
    ordered_ids). dependencies: id -> prerequisite ids. A prerequisite joins a
    batch only once all of its own prerequisites are enrolled or in an earlier
    position of the same batch; each family pool (word, grammar, kana incl.
    pronunciation rules) takes at most its limit per batch. Targets always
    join the first batch (enrolled as intro; their gate stays the prerequisite
    status). Returns {'batches': [[ids]], 'first': [ids], 'planned': [ids],
    'counts': {pool: n}}; nothing is written anywhere.
    """
    targets = list(dict.fromkeys(targets))
    done = set(enrolled)
    pending = [cid for cid in dict.fromkeys(ordered_ids) if cid not in done and cid not in targets]
    batches = []
    while pending:
        if enroll_all:
            batches.append(list(pending))
            done.update(pending)
            break
        used, batch = {}, []
        for cid in pending:
            pool = FRONTIER_POOLS.get(family_of(cid), "other")
            limit = limits.get(pool, max(limits.values(), default=0))
            if used.get(pool, 0) >= limit:
                continue
            if any(dep not in done and dep not in batch for dep in dependencies.get(cid, ())
                   if dep in pending):
                continue
            batch.append(cid)
            used[pool] = used.get(pool, 0) + 1
        if not batch:
            batch = [pending[0]]  # never stall: a zero limit still advances one item
        batches.append(batch)
        done.update(batch)
        pending = [cid for cid in pending if cid not in done]
    new_targets = [cid for cid in targets if cid not in set(enrolled)]
    if batches:
        batches[0] = [*batches[0], *new_targets]
    elif new_targets:
        batches = [new_targets]
    counts = {}
    for cid in (batches[0] if batches else []):
        pool = FRONTIER_POOLS.get(family_of(cid), "other")
        counts[pool] = counts.get(pool, 0) + 1
    return {"batches": batches, "first": batches[0] if batches else [],
            "planned": [cid for batch in batches[1:] for cid in batch], "counts": counts}
