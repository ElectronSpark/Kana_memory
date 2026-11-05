"""Prerequisite plans for new lessons, without rewriting saved learning state.

A prerequisite is ready after a real, unassisted successful independent review.
Reading an explanation, completing its guide, and self-reporting prior knowledge
never manufacture such evidence. Saved teaching and real work retain their
snapshots; untouched matching bundled lessons receive corrected prerequisites.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json


def dependencies_of(item):
    value = item.get("depends_on", [])
    if (not isinstance(value, (list, tuple))
            or any(not isinstance(cid, str) or not cid.strip() for cid in value)
            or len(set(value)) != len(value)):
        raise ValueError("语法前置关系必须是互不重复的内容标识。")
    return list(value)


def _catalogue(items):
    result = {}
    values = items.values() if isinstance(items, dict) else items
    for item in values:
        cid = item.get("id", item.get("content_id"))
        if not isinstance(cid, str) or not cid.strip() or cid in result:
            raise ValueError("教学目录含无效或重复的内容标识。")
        dependencies_of(item)
        result[cid] = item
    return result


def validate_catalog(items):
    """Return a stable prerequisite-first ordering; reject gaps and cycles."""
    from .knowledge_graph import prerequisite_order
    return prerequisite_order(_catalogue(items))


def independent_ready_ids(learning):
    """An earlier successful review is a foundation, not a mastery guarantee.

Later lapses or suspension do not revoke a prerequisite and strand advanced
reviews. This flag is derived from actual events, never from FSRS due dates.
"""
    return {row[0] for row in learning.queries.independent_prerequisite_rows()}


def has_started_answer(draft):
    """Only actual typed/composed input counts; audio and view flags do not."""
    if not isinstance(draft, dict):
        return False
    has_text = lambda value: isinstance(value, str) and bool(value.strip())
    parts = draft.get("parts")
    cloze = draft.get('cloze_state')
    cloze_values = cloze.get('values') if isinstance(cloze, dict) else None
    return (has_text(draft.get("answer")) or has_text(draft.get("text"))
            or isinstance(parts, list) and any(has_text(part) for part in parts)
            or isinstance(cloze_values, dict) and any(has_text(value) for value in cloze_values.values()))


def preserves_existing_route(learning, content):
    """Keep real assessed content, drafts and frozen results on their old route."""
    cid = content.get("id", content.get("content_id"))
    if content.get("stage") == "done" or learning.queries.course_has_primary_event(cid):
        return True
    for row in learning.queries.course_pending_drafts(cid):
        if row["result_json"] is not None or has_started_answer(json.loads(row["draft_json"])):
            return True
    return False


@lru_cache(maxsize=1024)
def _bundled_dependency_signature(cid):
    # Import lazily: learning_content validates its graph during module loading.
    from .learning_content import get_learning_item
    from .material_review import item_fingerprint
    current = get_learning_item(cid)
    if current is None:
        return None
    return (item_fingerprint(current), tuple(current.get("source_ids", ())),
            frozenset(current.get("abilities", ())), tuple(dependencies_of(current)))


def effective_dependencies(learning, content, *, preserved=None):
    """Overlay reviewed bundled prerequisites without rewriting saved teaching.

    An edited/custom/AI snapshot with a colliding ID is not a bundled lesson.
    Exact teaching, provenance and abilities must agree before applying the
    current additive dependency graph. Real work retains its original route.
    """
    original = dependencies_of(content)
    from .personal_graph import dependency_override
    revised = dependency_override(learning, content)
    if revised is not None: return list(revised['ids'])
    if learning is None or content.get("checkpoint", {}).get("prerequisite_acknowledged") is True:
        return original
    cid = content.get("id", content.get("content_id"))
    signature = _bundled_dependency_signature(cid)
    if signature is None:
        return original
    from .material_review import item_fingerprint
    authored, sources, abilities, current = signature
    if (item_fingerprint(content) != authored
            or tuple(content.get("source_ids", ())) != sources
            or frozenset(content.get("abilities", ())) != abilities):
        return original
    if not set(current).difference(original):
        return original
    preserved = preserves_existing_route(learning, content) if preserved is None else preserved
    if preserved:
        return original
    return list(dict.fromkeys([*original, *current]))


def _status(learning, cid, ready_ids, catalogue=None):
    saved = learning.get_item(cid) if learning else None
    source = saved or (catalogue or {}).get(cid) or {}
    title = source.get("title", cid)
    if cid in ready_ids:
        state = "ready"
    elif saved is None:
        state = "not_enrolled"
    elif saved["stage"] in {"intro", "guide"}:
        state = "introduction" if saved["stage"] == "intro" else "guided"
    else:
        state = "awaiting_independent_success"
    return {"id": cid, "title": title, "status": state, "ready": cid in ready_ids}


def prerequisite_status(learning, content, *, ready_ids=None):
    """Explain readiness for the stored lesson without mutating any queue."""
    if isinstance(content, str):
        content = learning.get_item(content)
        if content is None:
            raise KeyError("Unknown enrolled learning content")
    dependency_ids = effective_dependencies(learning, content)
    checkpoint = content.get("checkpoint", {})
    from .personal_graph import dependency_override
    revised = dependency_override(learning, content)
    waived = (checkpoint.get("prerequisite_acknowledged") is True and (revised is None or
              checkpoint.get('prerequisite_version') == revised['version_id']))
    if revised and revised['pending']:
        return {'ready': False, 'blocked': True, 'waived': False, 'grandfathered': False,
                'dependencies': [], 'missing': [], 'pending_revision': revised['version_id']}
    # Most vocabulary has no prerequisites. It must not scan all historical
    # successes for each answer merely to return an empty dependency list.
    if not dependency_ids:
        return {"ready": True, "blocked": False, "waived": waived,
                "grandfathered": False, "dependencies": [], "missing": []}
    ready_ids = independent_ready_ids(learning) if ready_ids is None else ready_ids
    dependencies = [_status(learning, cid, ready_ids) for cid in dependency_ids]
    missing = [entry for entry in dependencies if not entry["ready"]]
    grandfathered = revised is None and preserves_existing_route(learning, content)
    return {"ready": not missing, "blocked": bool(missing and not waived and not grandfathered),
            "waived": waived, "grandfathered": grandfathered,
            "dependencies": dependencies, "missing": missing}


def build_dependency_plan(target_ids, catalogue, learning_store=None, *, allow_unresolved=False):
    """Build a reviewable, stable enrollment plan from prerequisite to target.

Saved teaching snapshots take precedence. Only untouched snapshots that exactly
match bundled teaching receive its corrected additive prerequisite overlay.
"""
    catalogue = _catalogue(catalogue)
    if isinstance(target_ids, str):
        target_ids = [target_ids]
    targets = list(dict.fromkeys(target_ids))
    saved_items = learning_store.list_items() if learning_store else []
    enrolled = {item["id"] for item in saved_items}
    if learning_store:
        for cid, item in list(catalogue.items()):
            catalogue[cid] = {**item, 'depends_on': effective_dependencies(learning_store, item)}
        for saved in saved_items:
            catalogue[saved["id"]] = {**saved, "depends_on": effective_dependencies(learning_store, saved)}
    # Validate only the requested closure. An unrelated restored personal
    # snapshot may name a retired prerequisite and must not prevent adding a
    # valid local lesson. validate_catalog remains the full bundled audit.
    ordered, visited, visiting, unresolved = [], {}, set(), []

    def failed(cid, reason, path):
        if not allow_unresolved:
            if reason == "cycle":
                raise ValueError("语法前置关系成环：" + " → ".join(path))
            raise ValueError("教学目录缺少内容：" + str(cid))
        entry = {"id": cid, "reason": reason, "path": path}
        if entry not in unresolved:
            unresolved.append(entry)
        return False

    def visit(cid, path):
        if cid not in catalogue:
            return failed(cid, "missing", [*path, cid])
        if cid in visiting:
            return failed(cid, "cycle", [*path, cid])
        if cid in visited:
            return visited[cid]
        visiting.add(cid)
        valid = True
        for dependency in dependencies_of(catalogue[cid]):
            valid = visit(dependency, [*path, cid]) and valid
        visiting.remove(cid)
        visited[cid] = valid
        # Repair may keep an already enrolled unresolved target blocked, but
        # must not introduce a new incomplete or cyclic teaching dependency.
        if valid or cid in enrolled:
            ordered.append(cid)
        return valid

    for cid in targets:
        visit(cid, [])
    ready_ids = independent_ready_ids(learning_store) if learning_store else set()
    return {"target_ids": targets, "ordered_ids": ordered,
            "items": [deepcopy(catalogue[cid]) for cid in ordered],
            "to_enroll": [cid for cid in ordered if cid not in enrolled],
            "unmet": [_status(learning_store, cid, ready_ids, catalogue)
                      for cid in ordered if cid not in targets and cid not in ready_ids],
            "ready_ids": [cid for cid in ordered if cid in ready_ids],
            "unresolved": unresolved}
