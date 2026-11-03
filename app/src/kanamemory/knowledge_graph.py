"""Typed content relationships. Edges describe teaching, never award credit.

No spelling similarity, co-occurrence or model assertion can merge memories.
The prerequisite adapter keeps the existing stable order and error behavior.
Other relation types require explicitly supplied, versioned content records.
"""
from hashlib import sha256
import json

RELATION_TYPES = {"alias", "prerequisite", "component_of", "confusable_with", "co_occurs", "transfer_candidate"}
RELATION_KEYS = {"id", "type", "source", "target", "abilities", "conditions", "revision", "provenance"}


def relation(kind, source, target, *, revision, provenance, abilities=(), conditions=()):
    value = {"type": kind, "source": source, "target": target, "abilities": list(abilities),
             "conditions": list(conditions), "revision": str(revision), "provenance": provenance}
    value["id"] = sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    validate_relation(value)
    return value


def validate_relation(value, known_ids=None):
    if not isinstance(value, dict) or set(value) != RELATION_KEYS:
        raise ValueError("Invalid knowledge relation fields")
    if (not isinstance(value["type"], str) or value["type"] not in RELATION_TYPES
            or any(not isinstance(value[k], str) or not value[k] or len(value[k]) > 1024
                   for k in ("id", "source", "target", "revision", "provenance"))
            or value["source"] == value["target"]):
        raise ValueError("Invalid or self-referencing knowledge relation")
    for field in ("abilities", "conditions"):
        entries = value[field]
        if (not isinstance(entries, list) or len(entries) > 16
                or any(not isinstance(v, str) or not v or len(v) > 256 for v in entries)
                or len(set(entries)) != len(entries)):
            raise ValueError("Invalid knowledge relation scope")
    expected = sha256(json.dumps({k: v for k, v in value.items() if k != "id"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if value["id"] != expected:
        raise ValueError("Knowledge relation fingerprint differs")
    if known_ids is not None and not {value["source"], value["target"]} <= set(known_ids):
        raise ValueError("Knowledge relation references missing content")


def prerequisite_relations(item):
    from .learning_dependencies import dependencies_of
    cid = item.get("id", item.get("content_id"))
    # This proves a declared teaching dependency, not its linguistic truth.
    return [relation("prerequisite", cid, dep, revision=item.get("revision", "1"),
                     provenance="content.depends_on", abilities=item.get("abilities", ()))
            for dep in dependencies_of(item)]


def prerequisite_order(catalogue):
    order, visited, active = [], set(), set()
    def visit(cid, path):
        if cid not in catalogue:
            raise ValueError("教学目录缺少前置内容：" + cid)
        if cid in active:
            raise ValueError("语法前置关系成环：" + " → ".join([*path, cid]))
        if cid in visited:
            return
        active.add(cid)
        from .learning_dependencies import dependencies_of
        for dep in dependencies_of(catalogue[cid]):
            # Preserve the useful cycle error even for a self-edge.
            visit(dep, [*path, cid])
        active.remove(cid)
        visited.add(cid)
        prerequisite_relations(catalogue[cid])
        order.append(cid)
    for cid in catalogue:
        visit(cid, [])
    return order


def validate_relations(relations, catalogue):
    """Format/closure audit, not authority to alter scheduling identities."""
    seen, aliases = set(), {}
    dependencies = {cid: {**item, "depends_on": list(item.get("depends_on", []))} for cid, item in catalogue.items()}
    for value in relations:
        validate_relation(value, catalogue)
        key = (value["type"], value["source"], value["target"], tuple(value["abilities"]), tuple(value["conditions"]))
        if key in seen:
            raise ValueError("Duplicate knowledge relation")
        seen.add(key)
        if value["type"] == "prerequisite" and value["target"] not in dependencies[value["source"]]["depends_on"]:
            dependencies[value["source"]]["depends_on"].append(value["target"])
        if value["type"] == "alias":
            source = value["source"]
            if source in aliases:
                raise ValueError("Ambiguous alias routing")
            aliases[source] = value["target"]
    for source in aliases:
        chain = set()
        while source in aliases:
            if source in chain:
                raise ValueError("Alias cycle")
            chain.add(source)
            source = aliases[source]
    prerequisite_order(dependencies)
    return True


def question_relations(question):
    """Only exact reviewed references become background associations."""
    from .learning_conditions import for_question
    facts = for_question(question)
    result = []
    for label in facts.get("learning_conditions", ()):
        if label.startswith("with:"):
            result.append(relation("co_occurs", question["target_id"], label[5:],
                revision=facts["learning_condition_revision"],
                provenance=facts["learning_condition_source"]))
    return result
