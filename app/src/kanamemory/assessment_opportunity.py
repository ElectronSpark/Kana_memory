"""One app-owned opportunity per canonical target ability, across all lanes.

The ledger lives in existing evidence snapshots. Old observations are not
reinterpreted or replayed. New claims are made in the caller's transaction.
"""
from hashlib import sha256
import json
import math

VERSION = 1
TOTAL_AUXILIARY_WEIGHT = .10
WORD_UNIT_WEIGHT_CAP = .25  # Preserve the established dedicated spelling policy.


def identity(source, record_id):
    if not isinstance(record_id, str) or not record_id:
        raise ValueError("Missing app-owned assessment identity")
    return source + ":" + sha256(record_id.encode()).hexdigest()


def existing(db, opportunity_id, target, ability):
    if not db.in_transaction:
        raise RuntimeError("Opportunity checks require the settlement transaction")
    return db.execute("""SELECT * FROM learning_evidence
        WHERE json_extract(snapshot_json,'$.opportunity.id')=? AND target_id=? AND ability=?""",
        (opportunity_id, target, ability)).fetchone()


def allocation(db, opportunity_id, *, role, eligible, source, budget_scope, requested_weight):
    if (not isinstance(opportunity_id, str) or not 1 <= len(opportunity_id) <= 256
            or any(ord(c) < 32 for c in opportunity_id)):
        raise ValueError("Invalid assessment opportunity")
    if not isinstance(budget_scope, str) or budget_scope not in {"shared", "word_unit"} or budget_scope == "word_unit" and source != "word":
        raise ValueError("Only dedicated word spelling has a per-unit exception")
    if (type(requested_weight) not in (int, float) or not math.isfinite(requested_weight)
            or not 0 <= requested_weight <= (WORD_UNIT_WEIGHT_CAP if budget_scope == "word_unit" else TOTAL_AUXILIARY_WEIGHT)):
        raise ValueError("Invalid auxiliary opportunity allocation")
    if not eligible or role != "auxiliary":
        return 0.
    if budget_scope == "word_unit":
        return requested_weight
    used = db.execute("""SELECT COALESCE(SUM(json_extract(snapshot_json,'$.opportunity.weight')),0)
        FROM learning_evidence WHERE json_extract(snapshot_json,'$.opportunity.id')=?
        AND json_extract(snapshot_json,'$.opportunity.budget_scope')='shared'""", (opportunity_id,)).fetchone()[0]
    # Retraction does not replenish an already observed opportunity.
    return max(0., min(requested_weight, TOTAL_AUXILIARY_WEIGHT - used))


def metadata(opportunity_id, *, role, budget_scope, weight, part_ids=("answer",)):
    result = {"version": VERSION, "id": opportunity_id,
              "channel": {"primary": "formal", "auxiliary": "auxiliary", "exposure": "observation"}[role],
              "budget_scope": budget_scope, "weight": weight, "part_ids": list(part_ids)}
    validate_metadata(result)
    return result


def validate_metadata(value):
    if (not isinstance(value, dict) or set(value) != {"version", "id", "channel", "budget_scope", "weight", "part_ids"}
            or type(value["version"]) is not int or value["version"] != VERSION
            or not isinstance(value["id"], str) or not 1 <= len(value["id"]) <= 256
            or any(ord(c) < 32 for c in value["id"])
            or not isinstance(value["channel"], str) or value["channel"] not in {"formal", "auxiliary", "observation"}
            or not isinstance(value["budget_scope"], str) or value["budget_scope"] not in {"shared", "word_unit"}
            or type(value["weight"]) not in (int, float) or not math.isfinite(value["weight"])
            or not 0 <= value["weight"] <= (WORD_UNIT_WEIGHT_CAP if value["budget_scope"] == "word_unit" else TOTAL_AUXILIARY_WEIGHT)
            or value["channel"] != "auxiliary" and value["weight"] != 0
            or not isinstance(value["part_ids"], list) or not 1 <= len(value["part_ids"]) <= 16
            or any(not isinstance(p, str) or not p or len(p) > 80 for p in value["part_ids"])
            or len(set(value["part_ids"])) != len(value["part_ids"])):
        raise ValueError("Invalid assessment opportunity metadata")


def auxiliary_weight(event, fallback=.10):
    snapshot = json.loads(event["snapshot_json"])
    return snapshot.get("opportunity", {}).get("weight", fallback)
