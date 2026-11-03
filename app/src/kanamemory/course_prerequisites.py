"""Reviewed form requirements for the bundled course catalogue.

These are authored teaching prerequisites, not automatic word matching. The
target's own new construction is taught in its introduction; incidental words
and unrelated constructions in an example need local help, not enrollment.
Sources describe the Japanese forms, not a uniquely mandated curriculum order:
https://www.coelang.tufs.ac.jp/mt/ja/gmod/contents/explanation/038.html
https://www.coelang.tufs.ac.jp/mt/ja/gmod/contents/explanation/042.html
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


def requirements_for(content_id):
    """Return the reviewed minimum requirement set for a bundled identity."""
    result = list(SEMANTIC_REQUIREMENTS.get(content_id, ()))
    for family, targets in FORM_CONSUMERS.items():
        if content_id in targets:
            result.extend(FORM_BASES[family])
    return tuple(dict.fromkeys(result))


def apply_course_prerequisites(catalogue):
    """Complete only the supplied in-memory catalogue; never touch saved rows.

    Original dependencies remain as an audit baseline. Current dependencies
    control admission and route order; learning identities never change.
    """
    from .learning_dependencies import validate_catalog
    declared = set(SEMANTIC_REQUIREMENTS)
    for targets in FORM_CONSUMERS.values():
        declared.update(targets)
    missing = declared - set(catalogue)
    if missing:
        raise ValueError("Reviewed prerequisite consumer is missing: " + min(missing))
    for identity, item in catalogue.items():
        existing = list(item.get("depends_on", []))
        item.setdefault("curriculum_route_dependencies", existing.copy())
        item["depends_on"] = list(dict.fromkeys([*existing, *requirements_for(identity)]))
        item["prerequisite_revision"] = VERSION
    validate_catalog(catalogue)


def audit_course_prerequisites(catalogue):
    """Versioned, deterministic coverage evidence, without learner information."""
    from .learning_dependencies import validate_catalog
    order = validate_catalog(catalogue)
    ancestors = {}
    for identity in order:
        parents = catalogue[identity].get("depends_on", [])
        ancestors[identity] = set(parents)
        for parent in parents:
            ancestors[identity].update(ancestors[parent])
    gaps = {cid: sorted(set(requirements_for(cid)) - ancestors[cid])
            for cid in catalogue if set(requirements_for(cid)) - ancestors[cid]}
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
            "reviewed_requirements": list(requirements_for(cid)),
            "additional_dependencies": [p for p in catalogue[cid].get("depends_on", [])
                if p not in catalogue[cid].get("curriculum_route_dependencies", [])]}
            for cid in order]}
