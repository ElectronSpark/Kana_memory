"""Exact, versioned corrections to bundled teaching material.

Corrections are presentation overlays, never learner-state migrations. Stored
content, answers, review events and due dates remain intact. A changed/imported
record is repaired only when its complete teaching fingerprint matches a
reviewed old record. Example and reference replacements get new identities.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path

ITEM_FIELDS = ("id", "revision", "family", "domain", "title", "reading", "meaning",
               "instruction", "question_templates", "source_urls")
PRESENTATION_FIELDS = (*ITEM_FIELDS, "source_ids", "register")
PRESENTATION_REPLACEMENTS = frozenset({"title", "reading", "meaning", "instruction", "question_templates",
                                      "source_ids", "source_urls", "register", "question_template_revision"})
QUESTION_VIEW_FIELDS = ("answer", "accepted_answers", "choices", "options", "feedback", "guide", "task",
                        "retired_ambiguous_answers", "material_review", "assessment_blocked_reason")


def without_order_translations(value):
    """Ignore only the optional display extension when comparing teaching.

    Ordinary text, translations, dependencies and evidence remain untouched.
    For legacy JSON without this field, serialization is byte-for-byte the
    original fingerprint input. This is a detached view, never a saved rewrite.
    """
    if isinstance(value, dict):
        return {key: without_order_translations(part) for key, part in value.items()
                if key != "order_translation"}
    if isinstance(value, list):
        return [without_order_translations(part) for part in value]
    if isinstance(value, tuple):
        return tuple(without_order_translations(part) for part in value)
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(without_order_translations(value), ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def item_fingerprint(item):
    return fingerprint({key: item.get(key) for key in ITEM_FIELDS})


def presentation_fingerprint(item):
    """Exact teaching and provenance, excluding transient learner state."""
    return fingerprint({key: item.get(key) for key in PRESENTATION_FIELDS})


def question_explanation_fingerprint(question):
    """Exact old public task/solution/explanation, independent of option order."""
    fields = ("target_id", "revision", "kind", "family", "domain", "prompt", "task", "answer",
              "input_mode", "feedback")
    value = {key: question.get(key) for key in fields}
    for key in ("choices", "options", "accepted_answers"):
        values = question.get(key, [])
        if not isinstance(values, list) or any(not isinstance(part, str) for part in values):
            return None
        value[key] = sorted(values)  # Retain multiplicity; only UI ordering is irrelevant.
    return fingerprint(value)


@lru_cache(maxsize=1)
def review_data():
    path = Path(__file__).with_name("resources") / "learning_material_review.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != "kanamemory-material-review" or data.get("version") != 1:
        raise ValueError("Unsupported bundled material review")
    return data


def reviewed_item(item):
    result = deepcopy(item)
    entry = review_data().get("items", {}).get(item.get("id"))
    # A later exact correction can also recognize a previously shipped
    # reviewed snapshot. Identity alone never authorizes a replacement.
    if entry and item_fingerprint(item) in entry.get("input_sha256s", [entry["before_sha256"]]):
        result.update(deepcopy(entry["replacement_fields"]))
        result["material_review"] = review_data()["revision"]
    # A distinct extension changes introductions and *future* templates only.
    # Existing question snapshots never pass through this function, and the
    # enrolled revision/units/queues/due times are not written or replaced.
    presentation = review_data().get("lesson_presentations", {}).get("items", {}).get(item.get("id"))
    if (presentation and not result.get("ai_generated")
            and presentation_fingerprint(result) in presentation.get("input_sha256s", [])):
        fields = presentation.get("replacement_fields", {})
        if not isinstance(fields, dict) or not set(fields) <= PRESENTATION_REPLACEMENTS:
            raise ValueError("Invalid exact lesson presentation replacement")
        candidate = {**result, **deepcopy(fields)}
        if presentation_fingerprint(candidate) != presentation.get("after_sha256"):
            raise ValueError("Lesson presentation replacement fingerprint mismatch")
        result = candidate
    return result


def example_source_item(item):
    """Use the pinned authored examples, then apply their reviewed replacements."""
    result = deepcopy(item)
    entry = review_data().get("items", {}).get(item.get("id"))
    if entry and item_fingerprint(item) == entry["after_sha256"]:
        result.update(deepcopy(entry["original_fields"]))
    return result


def reviewed_plan(item_id, plan):
    entry = review_data().get("plans", {}).get(item_id)
    return (deepcopy(entry["replacement"]) if entry and fingerprint(plan) == entry["before_sha256"]
            else deepcopy(plan))


def reviewed_question(question):
    """Correct only ungraded questions with a matching public template.

    Frozen results/history keep the original evidence. Choice order and drafts
    are retained: replacements occupy the same slot, and the answer is never
    inferred from what the learner selected.
    """
    result = deepcopy(question)
    # Only an exact local correction or a frozen previously reviewed result
    # may exempt an attempt. Imported/model-authored flags cannot opt out of
    # assessment on their own.
    result.pop("assessment_blocked_reason", None)
    frozen = (question.get("result") or {}).get("material_view")
    if isinstance(frozen, dict):
        result.update({k: deepcopy(v) for k, v in frozen.items() if k in QUESTION_VIEW_FIELDS})
        return result
    if question.get("status") == "completed" or question.get("result") is not None:
        return result
    if question.get('personal_rubric', {}).get('method') == 'reviewed-exact-rubric-v1':
        # The app-owned assessment gate independently reconstructs this exact
        # receipt-backed rubric. A package overlay must not silently replace it;
        # an imported marker without the receipt remains blocked by that gate.
        return result
    if question.get("domain") == "pronunciation":
        explanations = review_data().get("question_explanations", {})
        change = explanations.get("items", {}).get(question_explanation_fingerprint(question))
        if change:
            # This extension cannot alter an answer or the learner's input.
            # Future material uses new templates; only this exact old wording
            # gets the plain-language explanation while it remains pending.
            if not isinstance(change.get("feedback"), str) or not change["feedback"].strip():
                raise ValueError("Invalid exact question explanation")
            result["feedback"] = change["feedback"]
            result["material_review"] = explanations["revision"]
    for entry in review_data().get("questions", []):
        def matches_field(key, value):
            actual = question.get(key)
            if actual == value:
                return True
            # A new question can already contain this exact reviewed task.
            # Permit only the declared before/after instruction pair; every
            # identity, stimulus, answer and other matching field stays exact.
            if key != "task":
                return False
            changes = list(entry.get("replacements", {}).get("task", []))
            exact = entry.get("exact_values", {}).get("task")
            if isinstance(exact, dict):
                changes.append(exact)
            return any(change.get("before") == value and change.get("after") == actual
                       for change in changes)
        if any(not matches_field(key, value) for key, value in entry["match"].items()):
            continue
        for key, changes in entry.get("replacements", {}).items():
            old = result.get(key)
            if isinstance(old, str):
                for change in changes:
                    if old == change["before"]:
                        old = change["after"]
                result[key] = old
            elif isinstance(old, list):
                result[key] = [next((c["after"] for c in changes if value == c["before"]), value)
                               for value in old]
        for key, change in entry.get("exact_values", {}).items():
            if result.get(key) == change["before"]:
                result[key] = deepcopy(change["after"])
        if entry.get("retired_ambiguous_answers"):
            result["retired_ambiguous_answers"] = deepcopy(entry["retired_ambiguous_answers"])
        result["material_review"] = review_data()["revision"]
    return result


def reviewed_pack(pack, *, include_additions=False, known_targets=None):
    """Replace only exact known old records; unrelated imported data survives."""
    data = review_data()
    # Both record lists are rebuilt below. Copy only their sibling metadata
    # here; copying the full pack first discards a second copy of every record.
    # Metadata and every published record still own their mutable children.
    result = deepcopy({key: value for key, value in pack.items()
                       if key not in {"examples", "references"}})
    examples, references = {}, {}
    source_examples = {row["id"]: row for row in pack["examples"]}
    payload_changes = {entry["before_payload_sha256"]: entry
                       for entry in data.get("references", {}).values()
                       if entry.get("before_payload_sha256")}
    for source in pack["examples"]:
        change = data.get("examples", {}).get(source["id"])
        row = (change["replacement"] if change and fingerprint(source) in change.get("input_sha256s", [change["before_sha256"]])
               else source)
        if row is not None:
            examples[row["id"]] = deepcopy(row)
    for source in pack["references"]:
        change = data.get("references", {}).get(source["id"])
        exact_match = change and fingerprint(source) in change.get("input_sha256s", [change["before_sha256"]])
        if not exact_match:
            # Importers can reassign IDs. Retiring an exact old annotation must
            # not depend on its ID, but a genuinely edited annotation survives.
            change = payload_changes.get(fingerprint({k: v for k, v in source.items() if k != "id"}))
            exact_match = change is not None
        # Do not migrate a reference away from user-edited source material.
        if change and change.get("example_before_sha256"):
            example = source_examples.get(source["example_id"])
            if example is None or fingerprint(example) != change["example_before_sha256"]:
                change = None
        row = change["replacement"] if change and exact_match else source
        if row is not None:
            references[row["id"]] = deepcopy(row)
    if include_additions:
        targets = set(known_targets) if known_targets is not None else None
        if isinstance(known_targets, dict):
            targets = {cid for cid, item in known_targets.items() if isinstance(item, dict)
                       and item_fingerprint(item) in data.get("target_fingerprints", {}).get(cid, [])}
        for row in data.get("added_references", []):
            if (targets is None or row["target_id"] in targets) and row["id"] not in references:
                references[row["id"]] = deepcopy(row)
        used = {row["example_id"] for row in references.values()}
        for row in data.get("added_examples", []):
            if row["id"] in used and row["id"] not in examples:
                examples[row["id"]] = deepcopy(row)
    # Imported snapshots may retain an old, modified reference to old text.
    # Keep that immutable text too instead of inventing a replacement anchor.
    used = {row["example_id"] for row in references.values()}
    for source in pack["examples"]:
        if source["id"] in used and source["id"] not in examples:
            examples[source["id"]] = deepcopy(source)
    result["examples"] = sorted(examples.values(), key=lambda row: row["id"])
    result["references"] = sorted(references.values(), key=lambda row: row["id"])
    return result
