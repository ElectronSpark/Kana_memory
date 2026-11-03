"""Exact reviewed example associations, not inferred linguistic causes.

Only immutable examples and span-pinned references from the content review
pack participate. The cache stores IDs, revisions and exact text, not learner
history. Unknown/custom/model material remains unclassified.
"""
from functools import lru_cache
from hashlib import sha256
import json


@lru_cache(maxsize=1)
def _reviewed_index():
    from .learning_content import learning_items
    from .material_review import review_data, item_fingerprint, example_source_item
    from .example_catalogue import bundled_example_pack
    data = review_data()
    items = learning_items()
    known = {}
    for item in items:
        if item_fingerprint(example_source_item(item)) in data.get("target_fingerprints", {}).get(item["id"], []):
            known[item["id"]] = str(item["revision"])
    # This applies exact retirements/replacements to original course examples
    # as well as additions. Keep only the compact ID/text index after building.
    pack = bundled_example_pack(items)
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
        proofs.setdefault(example["id"], []).append(sha256(json.dumps(ref, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    index = {}
    for eid, ids in by_example.items():
        if len(ids) > 1:
            text = examples[eid]["text"]
            # A text collision with different reviewed identities is skipped.
            refs = tuple(sorted((cid, known[cid]) for cid in ids))
            context_revision = sha256(json.dumps([eid, examples[eid]["revision"], refs,
                sorted(proofs[eid])], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            value = (eid, refs, context_revision)
            index[text] = value if text not in index or index[text] == value else None
    return index


def for_question(question):
    from .learning_questions import learning_order_translation_source
    if question.get("ai_generated") or question.get("guided") or question.get('personal_rubric'):
        return {}
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
