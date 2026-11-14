"""Reviewed form requirements for the bundled course catalogue.

These are authored teaching prerequisites, not automatic word matching. The
target's own new construction is taught in its introduction; incidental words
and unrelated constructions in an example need local help, not enrollment.
Sources describe the Japanese forms, not a uniquely mandated curriculum order:
https://www.coelang.tufs.ac.jp/mt/ja/gmod/contents/explanation/038.html
https://www.coelang.tufs.ac.jp/mt/ja/gmod/contents/explanation/042.html

Content libraries (design 08 §6.2/§9.1 L2): the core rules stay strict (a
missing core consumer is an error). Rows an extension library contributed to
SEMANTIC_REQUIREMENTS/FORM_CONSUMERS are additive and only take effect for
consumers and requirements present (and not blocked) in the effective
catalogue; skipped rows are recorded as a diagnostic, never raised.
"""
from collections import defaultdict


from .content_pack import load_namespace

_RULES = load_namespace("course_prerequisites", {})
VERSION = _RULES.get('VERSION', "")
REFERENCE_NOTES = _RULES.get('REFERENCE_NOTES', ())
TE_FORMS = _RULES.get('TE_FORMS', ())
NAI_FORMS = _RULES.get('NAI_FORMS', ())
VERB_PLAIN_FORMS = _RULES.get('VERB_PLAIN_FORMS', ())
PLAIN_FORMS = _RULES.get('PLAIN_FORMS', ())
NONPAST_FORMS = _RULES.get('NONPAST_FORMS', ())
FORM_CONSUMERS = _RULES.get('FORM_CONSUMERS', {})
FORM_BASES = _RULES.get('FORM_BASES', {})
SEMANTIC_REQUIREMENTS = _RULES.get('SEMANTIC_REQUIREMENTS', {})


_SKIPPED = []  # last apply's skipped extension contributions (diagnostics, bounded)


def requirements_for(content_id, *, rules=None):
    """Return the reviewed minimum requirement set for a bundled identity.

    rules: (semantic, consumers) as returned by active_rules; default the
    module tables (identical to the core rules when no extension contributes).
    """
    semantic, consumers = rules if rules is not None else (SEMANTIC_REQUIREMENTS, FORM_CONSUMERS)
    result = list(semantic.get(content_id, ()))
    for family, targets in consumers.items():
        if content_id in targets:
            result.extend(FORM_BASES[family])
    return tuple(dict.fromkeys(result))


def active_rules(catalogue, *, contributions, semantic=None, consumers=None, bases=None, blocked=None):
    """Contribution semantics for extension rows (pure).

    contributions: (key, entry_key, library_id, added items|None) rows from the
    namespace merge. Returns (semantic, consumers, skipped) where skipped lists
    (library_id, key, entry_key, item, reason) for rows that did not apply.
    """
    semantic = SEMANTIC_REQUIREMENTS if semantic is None else semantic
    consumers = FORM_CONSUMERS if consumers is None else consumers
    bases = FORM_BASES if bases is None else bases
    rows = [row for row in contributions if row[0] in {"SEMANTIC_REQUIREMENTS", "FORM_CONSUMERS"}]
    if not rows:
        return semantic, consumers, ()
    present = set(catalogue)
    is_blocked = blocked or (lambda cid: False)
    semantic = {key: list(value) for key, value in semantic.items()}
    consumers = {key: list(value) for key, value in consumers.items()}
    skipped = []
    for key, entry_key, library_id, items in rows:
        table = semantic if key == "SEMANTIC_REQUIREMENTS" else consumers
        added = list(items) if items is not None else list(table.get(entry_key, ()))
        for item in added:
            if key == "SEMANTIC_REQUIREMENTS":
                consumer, requirement = entry_key, item
                reason = ("missing_consumer" if consumer not in present else "missing_requirement"
                          if requirement not in present else "blocked_requirement" if is_blocked(requirement) else None)
            else:
                consumer = item
                reason = ("missing_consumer" if consumer not in present else "unknown_form"
                          if entry_key not in bases else None)
            if reason is None:
                continue
            skipped.append((library_id, key, entry_key, item, reason))
            if item in table.get(entry_key, ()):
                table[entry_key].remove(item)
        if entry_key in table and not table[entry_key]:
            del table[entry_key]
    return ({k: tuple(v) for k, v in semantic.items()}, {k: tuple(v) for k, v in consumers.items()}, tuple(skipped))


def _contributions():
    from .content_pack import effective_catalogue, namespace_contributions
    rows = namespace_contributions("course_prerequisites")
    catalogue = effective_catalogue()
    return rows, (lambda cid: bool(catalogue.missing_required_references(cid)))


def skipped_contributions():
    """Extension rule rows the last apply_course_prerequisites left out."""
    return tuple(_SKIPPED)


def _overlay(catalogue, rules, originals):
    for identity, item in catalogue.items():
        existing = list(originals[identity])
        item.setdefault("curriculum_route_dependencies", existing.copy())
        item["depends_on"] = list(dict.fromkeys([*existing, *requirements_for(identity, rules=rules)]))
        item["prerequisite_revision"] = VERSION


def apply_course_prerequisites(catalogue, *, contributions=None, blocked=None):
    """Complete only the supplied in-memory catalogue; never touch saved rows.

    Original dependencies remain as an audit baseline. Current dependencies
    control admission and route order; learning identities never change.
    contributions/blocked default to the effective catalogue's extension rows.
    """
    from .learning_dependencies import validate_catalog
    if contributions is None:
        contributions, blocked = _contributions()
    semantic, consumers, skipped = active_rules(catalogue, contributions=contributions, blocked=blocked)
    declared = set(semantic)
    for targets in consumers.values():
        declared.update(targets)
    missing = declared - set(catalogue)
    if missing:
        raise ValueError("Reviewed prerequisite consumer is missing: " + min(missing))
    originals = {identity: list(item.get("depends_on", [])) for identity, item in catalogue.items()}
    _overlay(catalogue, (semantic, consumers), originals)
    try:
        validate_catalog(catalogue)
    except ValueError as error:
        if not any(row[0] in {"SEMANTIC_REQUIREMENTS", "FORM_CONSUMERS"} for row in contributions):
            raise
        # Extension rules made the graph invalid: they are contributions, so
        # drop them all, keep the strict core rules, and record why.
        from . import diagnostic_events
        diagnostic_events.record_error("content_library", "refresh", error)
        skipped = (*skipped, *[(row[2], row[0], row[1], None, "invalid_graph") for row in contributions
                               if row[0] in {"SEMANTIC_REQUIREMENTS", "FORM_CONSUMERS"}])
        semantic, consumers = _core_rules(contributions)
        _overlay(catalogue, (semantic, consumers), originals)
        validate_catalog(catalogue)
    if skipped:
        from . import diagnostic_events
        diagnostic_events.record_error("content_library", "refresh", ValueError("extension prerequisite rows skipped"))
    _SKIPPED[:] = list(skipped)[:1000]
    return {"skipped": skipped}


def _core_rules(contributions):
    """The module tables minus every row an extension library added."""
    semantic = {key: list(value) for key, value in SEMANTIC_REQUIREMENTS.items()}
    consumers = {key: list(value) for key, value in FORM_CONSUMERS.items()}
    for key, entry_key, _, items in contributions:
        table = semantic if key == "SEMANTIC_REQUIREMENTS" else consumers if key == "FORM_CONSUMERS" else None
        if table is None or entry_key not in table:
            continue
        if items is None:
            del table[entry_key]
            continue
        table[entry_key] = [value for value in table[entry_key] if value not in items]
        if not table[entry_key]:
            del table[entry_key]
    return {k: tuple(v) for k, v in semantic.items()}, {k: tuple(v) for k, v in consumers.items()}


def audit_course_prerequisites(catalogue):
    """Versioned, deterministic coverage evidence, without learner information."""
    from .learning_dependencies import validate_catalog
    order = validate_catalog(catalogue)
    contributions, blocked = _contributions()
    rules = active_rules(catalogue, contributions=contributions, blocked=blocked)[:2]
    ancestors = {}
    for identity in order:
        parents = catalogue[identity].get("depends_on", [])
        ancestors[identity] = set(parents)
        for parent in parents:
            ancestors[identity].update(ancestors[parent])
    gaps = {cid: sorted(set(requirements_for(cid, rules=rules)) - ancestors[cid])
            for cid in catalogue if set(requirements_for(cid, rules=rules)) - ancestors[cid]}
    categories = defaultdict(int)
    for item in catalogue.values():
        categories[item.get("domain") or item["family"]] += 1
    return {"format": "kanamemory-course-prerequisite-audit", "version": 1,
        "revision": VERSION, "catalogue_counts": dict(categories),
        "scope": "Bundled authored lesson rules and local question targets; not every background word in every example.",
        "policy": "Hard prerequisites are form or semantic knowledge needed to answer. Incidental content needs local support. The exact ordering is an app teaching decision, not an official mandated sequence.",
        "references": list(REFERENCE_NOTES),
        "item_count": len(catalogue), "edge_count": sum(len(x.get("depends_on", [])) for x in catalogue.values()),
        "unresolved_requirements": gaps,
        "items": [{"id": cid, "depends_on": catalogue[cid].get("depends_on", []),
            "reviewed_requirements": list(requirements_for(cid, rules=rules)),
            "additional_dependencies": [p for p in catalogue[cid].get("depends_on", [])
                if p not in catalogue[cid].get("curriculum_route_dependencies", [])]}
            for cid in order]}
