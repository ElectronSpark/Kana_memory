"""Adapter: what the main-route mix reads from the teaching content (design 11 section 16, M5/M10).

read_readiness builds the Readiness of some catalogue items from the
question_dependencies namespace and the reviewed templates: each computation
loads its own namespace objects, a chunk of items at a time, and drops them
(no module-level cache grows). Retired templates (retired_practice_variants:
the retirement table and the cognate rule) and generated sound kinds are left
out; a template whose requirements name an out-of-vocabulary surface can never
be met and is left out too, and so is a template released only after some
reviews (practice.opens_after_reviews above 0). content_identity names the core teaching pack and
the extension libraries this process loaded; None when it cannot be told.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import closing
from pathlib import Path
import re
import sqlite3

from .placement import RULE, WORD, MixCancelled, section_of
from .readiness import Readiness, choice_key

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def mixable_stage_ids(tree) -> tuple:
    """Stage 1 onwards, never an optional stage (M1)."""
    from ..study_path_catalog import STAGES
    first = STAGES[0][0] if STAGES else None
    return tuple(stage["id"] for stage in tree["stages"] if not stage.get("optional") and stage["id"] != first)


def route_library_of(catalogue) -> Callable[[str], str | None]:
    """The provider M8 goes by (design 11 section 16): content_availability.library_of_content, except
    that an id of the effective catalogue's core_ids() is core (None) even when an accepted override
    gives it to a library, so M8 never cuts or defers core content. ``.overridden(content id)`` tells
    such an id (its effective templates are the library's: see overridden_entries). The core ids are
    read once, when the first library-provided id is asked about (never in a core-only install)."""
    from ..content_availability import library_of_content
    effective = getattr(catalogue, "effective", None)
    core = []

    def core_ids():
        if not core:
            core.append(frozenset(effective.core_ids()) if callable(getattr(effective, "core_ids", None))
                        else frozenset())
        return core[0]

    def library_of(content_id):
        owner = library_of_content(catalogue, content_id)
        return None if owner is None or content_id in core_ids() else owner

    def overridden(content_id) -> bool:
        return library_of_content(catalogue, content_id) is not None and content_id in core_ids()
    library_of.overridden = overridden
    return library_of


def overridden_entries(tree, library_of) -> frozenset:
    """Ids of the learning entries that are core for M8 although an extension library provides them (an
    accepted override; route_library_of(...).overridden): their questions are the library's and may need
    library content, which M8(b) must not move past them. Empty for any other library_of."""
    overridden = getattr(library_of, "overridden", None)
    if not callable(overridden):
        return frozenset()
    return frozenset(entry["id"] for stage in tree["stages"] for unit in stage["units"] for section in unit["sections"]
                     for entry in section["entries"]
                     if entry.get("kind") == "learning" and overridden(entry.get("content_id", entry["id"])))


def library_entries(tree, library_of: Callable[[str], str | None] | None) -> dict:
    """entry id -> extension library of every learning entry (none without library_of)."""
    if library_of is None:
        return {}
    result = {}
    for stage in tree["stages"]:
        for unit in stage["units"]:
            for section in unit["sections"]:
                for entry in section["entries"]:
                    if entry.get("kind") == "learning":
                        owner = library_of(entry.get("content_id", entry["id"]))
                        if owner is not None:
                            result[entry["id"]] = owner
    return result


def needed_items(tree, mixable, providers: Mapping[str, str]) -> list:
    """The entries whose readiness the plan reads: those of every movable section (word and
    reading-rule sections) of a mixed stage, and every extension-library entry there (M8(b) checks)."""
    mixable, result = set(mixable), []
    for stage in tree["stages"]:
        if stage["id"] not in mixable:
            continue
        for unit in stage["units"]:
            for section in unit["sections"]:
                movable = section_of(section, providers).kind in (WORD, RULE)
                result.extend(entry["id"] for entry in section["entries"] if entry.get("kind") == "learning"
                              and (movable or entry["id"] in providers))
    return list(dict.fromkeys(result))


def _binding(item, source, identity):
    """learning_questions._practice_binding with the item fingerprint computed once per item."""
    if item.get("ai_generated") or source.get("schema_version") != 1:
        return None
    table = source.get("items", {})
    try:
        extension = table.get(item.get("id"), {}) if item.get("id") in table else {}
    except (KeyError, TypeError):
        return None
    if not isinstance(extension, Mapping) or not extension:
        return None
    fingerprint = identity()
    if extension.get("item_fingerprint") == fingerprint:
        return extension
    old = (extension.get("history") or {}).get(fingerprint)
    return old if isinstance(old, Mapping) and old.get("item_fingerprint") == fingerprint else None


def read_readiness(item_ids: Iterable[str], items: Mapping[str, Mapping], *, chunk: int = 64,
                   load: Callable | None = None, cancelled: Callable[[], bool] | None = None,
                   kinds: Iterable[str] = ()) -> Readiness:
    """Readiness of these catalogue items (content id -> catalogue item or view).

    ``kinds``: the learner's switched-on kinds (params.MixParams.kinds); each template is listed under
    its ability and, when its kind is one of them, under choice_key(ability) too. Meant for a worker
    thread: its shard reads share one read-only connection per pack
    file (content_pack.shard_reads, this thread only, closed at the end). ``cancelled`` is asked
    before each chunk; True raises placement.MixCancelled.
    """
    from .. import content_pack
    from .. import learning_questions as questions
    load = content_pack.load_namespace if load is None else load
    sound = frozenset(questions.SOUND_KINDS)
    providers = _providers(questions)
    ids = [cid for cid in dict.fromkeys(item_ids) if isinstance(items.get(cid), Mapping)]
    table, strings = {}, {}
    switched = frozenset(kinds)
    with content_pack.shard_reads():
        for start in range(0, len(ids), max(1, chunk)):
            if cancelled is not None and cancelled():
                raise MixCancelled()
            dependencies = load(content_pack.QUESTION_DEPENDENCIES, {})
            declared = dependencies.get("items", {}) if dependencies.get("schema_version") == 1 else {}
            varied = load("varied_practice", {})
            provided = {spec.template_source: load(spec.template_source, {}) for spec in providers}
            for cid in ids[start:start + chunk]:
                table[cid] = _item_readiness(items[cid], declared, varied, provided, providers, questions, sound,
                                             content_pack.DEPENDENCY_ID_FIELDS, strings.setdefault,
                                             switched=switched)
            del dependencies, declared, varied, provided  # this chunk's decoded shards go with them
    return Readiness(table)


def _providers(questions):
    """Kinds whose templates a provider takes from a reviewed namespace bound to the exact lesson
    (today context_choice: question_kinds/vocab_context.templates, read here without its copy)."""
    sound = frozenset(questions.SOUND_KINDS)
    return tuple(spec for spec in questions.KIND_REGISTRY.with_template_provider()
                 if spec.kind not in sound and isinstance(spec.template_source, str)
                 and spec.template_source not in ("sound", "none"))


def _item_templates(item, varied, provided, providers, identity) -> dict:
    """kind -> raw templates of one lesson as learning_questions.practice_templates lists them
    (reviewed base and extension, then provider kinds), generated sound kinds left out; read-only.

    A lesson without stored question_templates (the legacy lessons whose base the app builds, or a
    grammar lesson read from GRAMMAR_QUESTIONS) takes practice_templates itself: they are few.
    """
    from .. import learning_questions as questions
    base = item.get("question_templates")
    if base is None:
        sound = questions.SOUND_KINDS
        return {kind: rows for kind, rows in (questions.practice_templates(item) or {}).items() if kind not in sound}
    templates = {}
    if isinstance(base, Mapping):
        for kind, variants in base.items():
            if isinstance(variants, list):
                templates[kind] = list(variants)
    binding = _binding(item, varied, identity)
    if binding:
        for kind, additions in (binding.get("templates") or {}).items():
            if isinstance(additions, list):
                templates.setdefault(kind, []).extend(additions)
    abilities = item.get("abilities")
    for spec in providers:
        if item.get("family") != spec.family or spec.ability not in (abilities or ()):
            continue
        bound = _binding(item, provided[spec.template_source], identity)
        variants = (bound.get("templates") or {}).get(spec.kind) if bound else None
        if isinstance(variants, list) and variants:
            templates[spec.kind] = list(variants)
    return templates


def _item_readiness(item, declared, varied, provided, providers, questions, sound, id_fields, shared, *,
                    switched=frozenset()):
    """key -> [(fingerprint, requirement ids)] of one item's declared templates that can be met, each under
    its ability and, when its kind is switched on (``switched``: the learner's kinds), under
    choice_key(ability) too."""
    from ..material_review import item_fingerprint
    cid = item["id"]
    memo = []

    def identity():
        if not memo:
            memo.append(item_fingerprint(item))
        return memo[0]
    templates = _item_templates(item, varied, provided, providers, identity)
    abilities = item.get("abilities")
    allowed = [kind for kind in questions.question_kinds(item, templates=templates) if kind not in sound]
    prints = {}

    def fingerprint(raw):
        key = id(raw)
        if key not in prints:
            prints[key] = questions.template_fingerprint(raw)
        return prints[key]
    retired = {}
    for kind, variant in questions.retired_practice_variants(item, templates):
        variants = templates.get(kind) or ()
        if 0 <= variant < len(variants):
            retired.setdefault(kind, set()).add(fingerprint(variants[variant]))
    try:
        entry = declared.get(cid) if cid in declared else None
    except (KeyError, TypeError):
        entry = None
    entry = entry if isinstance(entry, Mapping) else {}
    abilities = set(abilities) if isinstance(abilities, list) and abilities else None
    rows = {}
    for kind in allowed:
        ability = questions.KIND_ABILITIES.get(kind)
        if ability is None or (abilities is not None and ability not in abilities):
            continue
        # Q8 (design 11 section 18): only templates open at introduction count; a leveled one is out like a
        # retired one. Only templates that name a level are fingerprinted for this.
        gone = retired.get(kind, set()) | {fingerprint(raw) for raw in templates.get(kind, ()) if _opens_later(raw)}
        never, merged = set(), {}
        # M5 over the switched-on kinds of the ability. Device variants (no system keyboard, speech not
        # ready) would add their own choice keys here, per template; not done (design 11 section 16.10).
        choices = (choice_key(ability),) if kind in switched else ()
        requirements = entry.get(kind)
        for sha, requirement in (requirements.items() if isinstance(requirements, Mapping) else ()):
            if sha in gone or not isinstance(requirement, Mapping):
                continue
            if requirement.get("oov"):
                never.add(sha)
                continue
            merged[sha] = {shared(value, value) for name in id_fields for value in (requirement.get(name) or ())
                           if isinstance(value, str)}
        for raw in templates.get(kind, ()):
            practice = raw.get("practice") if isinstance(raw, Mapping) else None
            background = practice.get("background_ids") if isinstance(practice, Mapping) else None
            if not isinstance(background, list):
                continue
            sha = fingerprint(raw)
            if sha in gone or sha in never:
                continue
            merged.setdefault(sha, set()).update(shared(value, value) for value in background if isinstance(value, str))
        for sha, needed in merged.items():
            needed.discard(cid)
            rows.setdefault(ability, []).append((sha, needed))
            for key in choices:
                rows.setdefault(key, []).append((sha, needed))
    return rows


def _opens_later(raw) -> bool:
    """The template names a release level other than 0 (practice.opens_after_reviews; design 11 section 18,
    Q5/Q8): it is not offered at introduction, so readiness does not count it. A malformed level counts as
    later too (the app's gate refuses malformed practice). Stem-gloss facts are not read: a template with
    an out-of-vocabulary surface stays "never" (Q8 is conservative)."""
    practice = raw.get("practice") if isinstance(raw, Mapping) else None
    if not isinstance(practice, Mapping) or "opens_after_reviews" not in practice:
        return False
    level = practice["opens_after_reviews"]
    return not (type(level) is int and level == 0)


def content_identity():
    """The app build (its question selection reads the content), the core teaching pack (metadata
    content_sha256), the loaded extension libraries (id, version, content_sha256) and the
    registry's accepted overrides, or None when the pack or a library cannot be told."""
    from .. import content_pack
    from ..build_info import BUILD_NUMBER, VERSION
    try:
        path = Path(content_pack.pack_path())
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            metadata = dict(connection.execute(
                "SELECT key, value FROM metadata WHERE key IN ('format','version','profile','content_sha256')"))
    except (OSError, ValueError, sqlite3.Error):
        return None
    digest = metadata.get("content_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        return None
    libraries, overrides, fallback = [], [], False
    try:
        layers = content_pack.library_layers()
        if layers:
            catalogue = content_pack.effective_catalogue()
            manifests = {layer.library_id: layer.manifest for layer in layers if not layer.damaged}
            for library_id in catalogue.extension_ids:
                manifest = manifests.get(library_id)
                if not isinstance(manifest, Mapping) or not _SHA256.fullmatch(str(manifest.get("content_sha256"))):
                    return None
                libraries.append([library_id, manifest.get("library_version"), manifest["content_sha256"]])
            overrides = sorted([str(library_id), str(content_id)]
                               for library_id, content_id in getattr(catalogue, "accepted_overrides", ()))
            fallback = bool(getattr(catalogue, "fallback", False))
    except (OSError, ValueError):
        return None
    return {"app": [VERSION, BUILD_NUMBER],
            "core": [metadata.get("format"), metadata.get("version"), metadata.get("profile"), digest],
            "libraries": libraries, "overrides": overrides, "fallback": fallback}
