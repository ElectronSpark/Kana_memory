"""Prerequisite plans for new lessons, without rewriting saved learning state.

A prerequisite is ready after a real, unassisted successful independent review.
Reading an explanation, completing its guide, and self-reporting prior knowledge
never manufacture such evidence. Under the "breadth first" study orientation
(design 12 Q3) a prerequisite whose teaching is done also counts as met, with
its own status "taught"; independent_ready_ids keeps meaning the evidence. Saved teaching and real work retain their
snapshots; untouched matching bundled lessons receive corrected prerequisites.

Content libraries (design 08 §9.2/§9.3): an optional catalogue_view (the
content_pack EffectiveCatalogue) adds the unresolved reasons "unavailable"
(the library providing it is installed but not enabled) and
"blocked_dependency" (a required reference is missing), and the status
"unavailable" for a prerequisite that never reached readiness. A ready
prerequisite stays ready (history is never revoked). dependency_frontier
splits an enrolment plan into batches without writing anything.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache, partial
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


@lru_cache(maxsize=8192)
def _bundled_dependency_signature(cid):
    """(fingerprint, source ids, abilities, prerequisites) of the bundled lesson, or None.

    The fingerprint may be a zero-argument callable, as it is here: it reads
    every template of the lesson, and effective_dependencies needs it only
    when the bundled prerequisites would add something (design 08 section 6.5).
    """
    # Import lazily: learning_content validates its graph during module loading.
    from .learning_content import learning_item_view
    current = learning_item_view(cid)
    if current is None:
        return None
    return (partial(_bundled_fingerprint, cid), tuple(current.get("source_ids", ())),
            frozenset(current.get("abilities", ())) - {"listening"}, tuple(dependencies_of(current)))


def bundled_dependencies(content_id) -> tuple[str, ...]:
    """The bundled catalogue lesson's current prerequisites (an alias resolves as learning_item_view does).

    () for an id the catalogue does not define or without depends_on. Read from
    the process cache _bundled_dependency_signature keeps, so asking for many
    lessons costs no copy of each lesson after the first time.
    """
    try:
        signature = _bundled_dependency_signature(content_id)
    except (KeyError, TypeError, ValueError):
        return ()
    return () if signature is None else signature[3]


@lru_cache(maxsize=1024)
def _bundled_fingerprint(cid):
    from .learning_content import learning_item_view
    from .material_review import item_fingerprint
    return item_fingerprint(learning_item_view(cid))


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
    # Nothing to add: the answer is the same whether or not the teaching matches, so the
    # fingerprints (every template of both lessons) are compared only when it matters.
    if not set(current).difference(original):
        return original
    if (tuple(content.get("source_ids", ())) != sources
            or frozenset(content.get("abilities", ())) - {"listening"} != abilities
            or item_fingerprint(content) != (authored() if callable(authored) else authored)):
        return original
    preserved = preserves_existing_route(learning, content) if preserved is None else preserved
    if preserved:
        return original
    return list(dict.fromkeys([*original, *current]))


def _view_unavailable(view, cid):
    return view is not None and (view.is_unavailable(cid) or bool(view.missing_required_references(cid)))


TAUGHT = "taught"


class TaughtRule:
    """Design 12 Q3 for one pass over prerequisites.

    Under breadth first a prerequisite whose teaching is done (a scored first
    check, right or wrong) is met; gates.prerequisite_met decides. The
    orientation is read from the learner's store at most once per pass, and only
    when some prerequisite is taught but not independently ready.
    """

    def __init__(self, learning):
        self._store = getattr(learning, "store", None)
        self._met = None

    def met(self, saved) -> bool:
        if saved is None or saved.get("stage") != "done":
            return False
        if self._met is None:
            self._met = self._store is not None and self._read()
        return self._met

    def _read(self) -> bool:
        from .arrangement.rules.gates import prerequisite_met
        from .arrangement.services.preferences import ArrangementPreferences
        return prerequisite_met(ArrangementPreferences(self._store).orientation(),
                                independently_ready=False, teaching_done=True)


def _status(learning, cid, ready_ids, catalogue=None, view=None, taught=None):
    saved = learning.get_item(cid) if learning else None
    source = saved or (catalogue or {}).get(cid) or {}
    title = source.get("title", cid)
    if cid in ready_ids:
        state = "ready"
    elif _view_unavailable(view, cid):
        # The library rule (design 08 section 9.2) comes first under either
        # orientation: only an independent success outlives a disabled library.
        state = "unavailable"
    elif (taught or TaughtRule(learning)).met(saved):
        # Taught but never answered correctly: met, without claiming a success.
        return {"id": cid, "title": title, "status": TAUGHT, "ready": True}
    elif saved is None:
        state = "not_enrolled"
    elif saved["stage"] in {"intro", "guide"}:
        state = "introduction" if saved["stage"] == "intro" else "guided"
    else:
        state = "awaiting_independent_success"
    return {"id": cid, "title": title, "status": state, "ready": cid in ready_ids}


def prerequisite_status(learning, content, *, ready_ids=None, catalogue_view=None):
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
    taught = TaughtRule(learning)
    dependencies = [_status(learning, cid, ready_ids, view=catalogue_view, taught=taught) for cid in dependency_ids]
    missing = [entry for entry in dependencies if not entry["ready"]]
    grandfathered = revised is None and preserves_existing_route(learning, content)
    return {"ready": not missing, "blocked": bool(missing and not waived and not grandfathered),
            "waived": waived, "grandfathered": grandfathered,
            "dependencies": dependencies, "missing": missing}


def build_dependency_plan(target_ids, catalogue, learning_store=None, *, allow_unresolved=False,
                          catalogue_view=None):
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
            if reason == "blocked_dependency":
                raise ValueError("前置内容所在的库缺失：" + str(cid))
            if reason == "unavailable":
                raise ValueError("前置内容所在的库已停用：" + str(cid))
            raise ValueError("教学目录缺少内容：" + str(cid))
        entry = {"id": cid, "reason": reason, "path": path}
        if entry not in unresolved:
            unresolved.append(entry)
        return False

    def visit(cid, path):
        if cid not in catalogue:
            reason = "unavailable" if catalogue_view is not None and catalogue_view.is_unavailable(cid) else "missing"
            return failed(cid, reason, [*path, cid])
        if (catalogue_view is not None and cid not in enrolled
                and catalogue_view.missing_required_references(cid)):
            visited[cid] = False
            return failed(cid, "blocked_dependency", [*path, cid])
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
    taught = TaughtRule(learning_store)
    return {"target_ids": targets, "ordered_ids": ordered,
            "items": [deepcopy(catalogue[cid]) for cid in ordered],
            "to_enroll": [cid for cid in ordered if cid not in enrolled],
            "unmet": [entry for entry in (_status(learning_store, cid, ready_ids, catalogue, catalogue_view, taught)
                                          for cid in ordered if cid not in targets and cid not in ready_ids)
                      if not entry["ready"]],
            "ready_ids": [cid for cid in ordered if cid in ready_ids],
            "unresolved": unresolved}


def dependency_frontier(plan, *, limits, enroll_all=False):
    """Candidate enrolment batches for a build_dependency_plan result (08 §9.3).

    limits: per pool (word, grammar, kana incl. pronunciation rules), e.g.
    content_library_merge.frontier_limits(word_batch=..., grammar_batch=...).
    The first batch is what one "add" action would enrol; later batches stay
    planned for later frontier advances. Pure: nothing is written.
    """
    from .content_library_merge import dependency_frontier as frontier
    items = {item.get("id", item.get("content_id")): item for item in plan["items"]}
    to_enroll = set(plan["to_enroll"])
    enrolled = [cid for cid in plan["ordered_ids"] if cid not in to_enroll]
    family = lambda cid: (items.get(cid) or {}).get("family") or cid.split(":", 1)[0]  # noqa: E731
    return frontier(plan["ordered_ids"], {cid: dependencies_of(item) for cid, item in items.items()},
                    targets=plan["target_ids"], enrolled=enrolled, limits=limits, family_of=family,
                    enroll_all=enroll_all)
