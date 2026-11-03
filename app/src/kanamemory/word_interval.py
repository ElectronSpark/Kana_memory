"""Bounded due-date adjustments from auxiliary romanization word spelling.

This is a product policy layered over genuine FSRS reviews, not fractional
FSRS ratings. Only due dates change; independent memory parameters and logs
remain the scheduler's own observations.
"""
from __future__ import annotations

from datetime import datetime
import json


WORD_INTERVAL_POLICY = "word_kana_due_quarter_v1"
WORD_INTERVAL_WEIGHT = 0.25
VISUAL_INTERVAL_SKILLS = frozenset({
    "kana_choice", "kana_romaji", "romaji_kana", "romaji_kana_full",
})


def calculate_interval_due(*, base_due: datetime, base_last_review: datetime,
                           before_due: datetime, result: str,
                           now: datetime) -> datetime | None:
    """Calculate a quarter-interval change, capped around the real-review base.

    A failure can make an upcoming card due now. Already-due cards stay due;
    short learning steps are excluded by the caller. A zero change returns
    None, so the audit records only real schedule changes.
    """
    if result not in {"correct", "incorrect"}:
        raise ValueError("Invalid word interval result")
    if any(value.tzinfo is None or value.utcoffset() is None
           for value in (base_due, base_last_review, before_due, now)):
        raise ValueError("Word interval dates must have time zones")
    if base_due <= base_last_review or now < base_last_review or before_due <= now:
        return None
    delta = (base_due - base_last_review) * WORD_INTERVAL_WEIGHT
    lower, upper = base_due - delta, base_due + delta
    # Unknown external changes must not be silently replaced by our policy.
    if not lower <= before_due <= upper:
        return None
    candidate = before_due + delta if result == "correct" else before_due - delta
    candidate = max(now, min(upper, max(lower, candidate)))
    return None if candidate == before_due else candidate


def _used_reading_anchors(store, expected, script):
    """Only a previously effective adjustment consumes this reading/anchor.

    Exact anchor IDs handle independent reviews made within the same clock
    tick. Hints, ambiguous answers, old-version history and capped no-ops do
    not manufacture an interval observation.
    """
    used = set()
    for row in store._db.execute("""SELECT evidence_json FROM word_spelling_attempt
            WHERE script=? AND json_valid(evidence_json)
            AND json_extract(evidence_json,'$.expected')=?
            AND json_type(evidence_json,'$.schedule_adjustments')='array'""",
            (script, expected)):
        evidence = json.loads(row[0])
        for item in evidence["schedule_adjustments"]:
            if (isinstance(item, dict) and item.get("policy") == WORD_INTERVAL_POLICY
                    and isinstance(item.get("card_id"), str)
                    and isinstance(item.get("anchor_event_id"), str)):
                used.add((item["card_id"], item["anchor_event_id"]))
    return used


def apply_word_interval_adjustments(store, spelling, script, now, *, event_id=None) -> list[dict]:
    """Update eligible kana due dates inside the caller's existing transaction.

    The caller persists the returned audit array with its word attempt. There
    is deliberately no commit, rollback, word-card creation or review event.
    """
    from .domain import ALGORITHM_VERSION, WORD_ROMAJI_POLICY, normalize_answer
    from .word_spelling import reading_evidence_unit
    from .kana_reading_rules import rule_for_symbol
    from .kana_question_policy import direction_allowed, _legacy_symbol
    from .learning_evidence import LearningEvidenceStore, bounded_due, remaining_budget, record_adjustment
    from hashlib import sha256
    from uuid import uuid4

    if not store._db.in_transaction:
        raise RuntimeError("Word interval changes require an existing transaction")
    if (spelling.get("practice_policy") != WORD_ROMAJI_POLICY
            or spelling.get("script") != script or spelling.get("assisted")
            or spelling.get("accepted_alternative") or not spelling.get("expected")):
        return []
    expected = normalize_answer(spelling["expected"])
    from .kana_routing import active_units, project_cards
    routes = active_units(store)
    event_id = event_id or "word:" + uuid4().hex
    adjustments, seen, used_reading = [], set(), None
    for unit in spelling.get("units", ()):
        content_id, result = unit.get("content_id"), unit.get("result")
        if (not isinstance(content_id, str) or content_id in seen
                or result not in {"correct", "incorrect"} or not reading_evidence_unit(unit)):
            continue
        seen.add(content_id)
        # These standalone signs now refer to common reading-rule lessons.
        # Their old cards/logs remain portable; new contextual spelling must
        # not silently reschedule the retired glyph-specific memories.
        if rule_for_symbol(unit.get("symbol")) or rule_for_symbol(_legacy_symbol(content_id)):
            continue
        if not store.is_content_available(content_id, now):
            continue
        cards = store._db.execute("""SELECT * FROM study_card
            WHERE content_id=? AND script=? AND suspended=0 AND review_count>0""",
            (content_id, script)).fetchall()
        for row in project_cards(store, cards, units=routes, include_probes=False):
            if (row["skill"] not in VISUAL_INTERVAL_SKILLS or row["scheduler_version"] != ALGORITHM_VERSION
                    or row["suspended"] or row["review_count"] <= 0
                    or not direction_allowed(content_id, row["skill"], script)):
                continue
            evidence_ability = script + ":" + row.get("ability", row["skill"])
            evidence_id = event_id + ":" + row["card_id"]
            from .assessment_opportunity import existing as prior_opportunity
            if prior_opportunity(store._db, event_id, content_id, evidence_ability) is not None:
                continue
            LearningEvidenceStore(store).record_attempt(evidence_id,
                {"target_id": content_id, "ability": evidence_ability, "kind": "word_spelling",
                 "revision": row["content_version"], "answer": unit.get("symbol", ""), "prompt": expected},
                {"correct": result == "correct", "outcome": result, "assisted": False,
                 "answered_text": spelling.get("actual", "")}, source="word", trusted=True, now=now,
                 opportunity_id=event_id, budget_scope="word_unit", auxiliary_weight=WORD_INTERVAL_WEIGHT)
            state = json.loads(row["scheduler_state_json"])
            if state.get("state") != 2 or not state.get("last_review"):
                continue
            anchor = store._db.execute("""SELECT event_id,state_after_json FROM review_event
                WHERE card_id=? ORDER BY occurred_at_utc DESC,rowid DESC LIMIT 1""",
                (row["card_id"],)).fetchone()
            if anchor is None:
                continue
            base = json.loads(anchor["state_after_json"])
            if (base.get("state") != 2 or not base.get("last_review")
                    or {k: v for k, v in state.items() if k != "due"}
                    != {k: v for k, v in base.items() if k != "due"}):
                continue
            base_due = datetime.fromisoformat(base["due"])
            last_review = datetime.fromisoformat(base["last_review"])
            before_due = datetime.fromisoformat(state["due"])
            if before_due != datetime.fromisoformat(row["due_at_utc"]):
                continue
            weight = min(WORD_INTERVAL_WEIGHT, remaining_budget(store._db, "kana", row["card_id"], anchor["event_id"], base_due, last_review))
            after_due = bounded_due(base_due, last_review, before_due, result, now, weight)
            if after_due is None:
                continue
            # One historical scan per word, even when several units each have
            # four established visual directions. Do not rescan all attempts
            # once per affected card as the learner's history grows.
            if used_reading is None:
                used_reading = _used_reading_anchors(store, expected, script)
            if (row["card_id"], anchor["event_id"]) in used_reading:
                continue
            if not record_adjustment(store._db, event_id=evidence_id, target_id=content_id,
                    ability=evidence_ability, card_kind="kana", card_id=row["card_id"],
                    anchor_event_id=anchor["event_id"], now=now, outcome=result,
                    fingerprint="word:" + sha256((script + ":" + expected).encode()).hexdigest(),
                    base_due=base_due, last_review=last_review, before_due=before_due, after_due=after_due, weight=weight):
                continue
            after_text = after_due.isoformat()
            state["due"] = after_text
            store._db.execute("""UPDATE study_card SET scheduler_state_json=?,due_at_utc=?
                WHERE card_id=?""", (json.dumps(state, ensure_ascii=False, allow_nan=False),
                after_text, row["card_id"]))
            adjustments.append({"policy": WORD_INTERVAL_POLICY, "weight": weight,
                "evidence_event_id": evidence_id,
                "card_id": row["card_id"], "content_id": content_id,
                "skill": row["skill"], "script": script,
                "anchor_event_id": anchor["event_id"], "result": result,
                "before_due_at_utc": before_due.isoformat(),
                "after_due_at_utc": after_text, "base_due_at_utc": base_due.isoformat(),
                "base_interval_seconds": (base_due - last_review).total_seconds()})
    return adjustments
