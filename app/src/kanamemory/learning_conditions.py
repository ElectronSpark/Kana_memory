"""Exact reviewed example associations, not inferred linguistic causes.

Only immutable examples and span-pinned references from the content review
pack participate. The cache stores IDs, revisions and exact text, not learner
history. Unknown/custom/model material remains unclassified.
"""
from functools import wraps
from hashlib import sha256
import json
from threading import RLock

from .content_caches import content_cache


def _single_flight(function):
    """Cache one result; concurrent first callers wait for the one computation.

    lru_cache lets a second thread recompute while the first is still running.
    Here the startup warm-up thread and an early main-thread question share a
    single run. Failures are not cached. cache_clear() matches lru_cache.
    A clear from another thread waits for a running computation and drops it;
    one from inside it (a catalogue rebuilt while it reads: content_caches)
    does not wait for itself, and that computation's result is not kept.
    """
    lock, state, missing = RLock(), {"generation": 0}, object()

    @wraps(function)
    def cached():
        value = state.get("value", missing)
        if value is not missing:
            return value
        with lock:
            if "value" in state:
                return state["value"]
            generation = state["generation"]
            value = function()
            if state["generation"] == generation:
                state["value"] = value
            return value

    def cache_clear():
        with lock:
            state.pop("value", None)
            state["generation"] += 1

    cached.cache_clear = cache_clear
    return cached


def _reviewed_targets(items, *, copies=True):
    """{item id: revision} of reviewed targets, via the precomputed review index.

    Equal to testing each fingerprint against the target_fingerprints list
    (WP6 builds that list into a set once per review data object). An item
    the list does not name cannot be a target whatever its fingerprint, so
    it is neither copied nor fingerprinted.
    """
    from .material_review import (has_reviewed_targets, is_reviewed_target, item_fingerprint,
                                  example_source_item, example_source_view)
    source = example_source_item if copies else example_source_view
    return {item["id"]: str(item["revision"]) for item in items
            if has_reviewed_targets(item["id"])
            and is_reviewed_target(item["id"], item_fingerprint(source(item)))}


@content_cache
@_single_flight
def _reviewed_index():
    return _build_reviewed_index(copies=False)


def _build_reviewed_index(*, copies):
    """The reviewed-example index. It only reads the pack, so it is built from read-only views of the
    lessons, the examples and the review data (copies=False); copies=True builds it from the pack's
    independent copies, the reference the equivalence test compares with. Equal key by key."""
    from .learning_content import learning_item_views
    from . import example_catalogue, learning_content
    build = example_catalogue.bundled_example_pack
    # Read only: no copy of every lesson with all its templates; without copies (and with the bundled
    # pack builder, which only reads them) not of its other fields either.
    # (A replaced builder or reader of the views, a fixture's own lessons, keeps its call without arguments.)
    shared = (not copies and build is example_catalogue.BUNDLED_EXAMPLE_PACK
              and learning_item_views is learning_content.OWN_ITEM_VIEWS)
    items = learning_item_views(shared=True) if shared else learning_item_views()
    known = _reviewed_targets(items, copies=copies)
    # This applies exact retirements/replacements to original course examples
    # as well as additions. Keep only the compact ID/text index after building.
    # A replaced pack builder (a fixture's own pack) keeps its one-argument call.
    pack = (build(items, copies=False) if not copies and build is example_catalogue.BUNDLED_EXAMPLE_PACK
            else build(items))
    examples = {row["id"]: row for row in pack["examples"]}
    by_example, proofs = {}, {}
    for ref in pack["references"]:
        example = examples.get(ref.get("example_id"))
        spans = ref.get("spans", [])
        if (example is None or ref.get("target_id") not in known
                or ref.get("example_revision") != example.get("revision") or not spans):
            continue
        text = example["text"]
        if any(type(s.get("start")) is not int or type(s.get("end")) is not int
               or not 0 <= s["start"] < s["end"] <= len(text) or text[s["start"]:s["end"]] != s.get("quote") for s in spans):
            continue
        by_example.setdefault(example["id"], set()).add(ref["target_id"])
        # A proof is needed only for an example of two or more targets: without copies it is computed then.
        proofs.setdefault(example["id"], []).append(_proof(ref) if copies else ref)
    index = {}
    for eid, ids in by_example.items():
        if len(ids) > 1:
            text = examples[eid]["text"]
            # A text collision with different reviewed identities is skipped.
            refs = tuple(sorted((cid, known[cid]) for cid in ids))
            digests = proofs[eid] if copies else [_proof(ref) for ref in proofs[eid]]
            context_revision = sha256(json.dumps([eid, examples[eid]["revision"], refs,
                sorted(digests)], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            value = (eid, refs, context_revision)
            index[text] = value if text not in index or index[text] == value else None
    return index


def _proof(ref):
    return sha256(json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def for_question(question):
    from .learning_questions import learning_order_translation_source
    if question.get("ai_generated") or question.get("guided") or question.get('personal_rubric'):
        return {}
    if question.get('input_mode') == 'multi_cloze':
        from .learning_questions import reviewed_practice_context
        from .multi_cloze import specification
        if reviewed_practice_context(question) is None:
            return {}
        spec = specification(question)
        backgrounds = sorted({slot['target_id'] for slot in spec['slots'] if slot['role'] == 'background'})
        if not backgrounds or any(len(cid) > 155 for cid in backgrounds):
            return {}
        source = question['practice_source']
        return {'learning_conditions': ['with:' + cid for cid in backgrounds],
                'learning_condition_source': question['practice']['context_id'],
                'learning_condition_revision': sha256(json.dumps([source, spec], ensure_ascii=False,
                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    text = (question.get("audio_text") if question.get("requires_audio")
            else learning_order_translation_source(question))
    match = _reviewed_index().get(text) if isinstance(text, str) else None
    if match is None:
        return {}
    eid, refs, context_revision = match
    target = question.get("target_id")
    if (target, str(question.get("revision"))) not in refs:
        return {}
    conditions = ["with:" + cid for cid, _ in refs if cid != target and len(cid) <= 155]
    # A prefix can collapse two different contexts into the same diagnosis.
    # Over-budget/overlong associations remain ordinary practice instead.
    if len(conditions) > 8 or len(conditions) != len(refs) - 1:
        return {}
    return {"learning_conditions": conditions, "learning_condition_source": eid,
            "learning_condition_revision": context_revision} if conditions else {}


def candidate_context(question):
    """Versioned local selection facts; unknown material has no inferred context.

    Helped/revealed tasks and recent exposure are filtered by the caller before
    selection. This descriptor grants no scoring or scheduling authority.
    Re-derive associations from the reviewed text instead of trusting fields
    supplied by an imported question or a model.
    """
    if question.get("assessment_blocked_reason"):
        return None
    facts = for_question(question)
    if not facts:
        return None
    return {"version": 1, "target_id": question["target_id"],
            "revision": str(question["revision"]), "ability": question["ability"],
            "kind": question["kind"], "source": facts["learning_condition_source"],
            "condition_revision": facts["learning_condition_revision"],
            "conditions": sorted(facts["learning_conditions"])}
