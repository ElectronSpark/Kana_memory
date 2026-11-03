"""Opt-in bridge from legacy kana directions to shared ability baselines.

Registration and preparation never alter old cards, events or queues. Activation
is deliberately separate: callers must route BOTH scheduling and auxiliary
updates through the carrier before enabling a bridge. Until then all existing
learning continues with its legacy rules. No numeric FSRS states are averaged.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from uuid import uuid4

from fsrs import Card, Rating

from .domain import ALGORITHM_VERSION, WORD_SKILLS, CONTENT_CONTROLS_SETTING, _utc


BRIDGE_SETTING = "kana_ability_bridges_v1"
SKILL_TO_ABILITY = {
    "kana_choice": "reading", "kana_romaji": "reading",
    "romaji_kana": "spelling", "romaji_kana_full": "spelling",
    "romaji_handwriting": "handwriting", "kana_handwriting": "legacy_combined",
}


def _load(store):
    value = store.get_setting(BRIDGE_SETTING, {})
    return value if isinstance(value, dict) and value.get("version") == 1 and isinstance(value.get("units"), dict) else {"version": 1, "units": {}}


def _write(store, value):
    store._db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                      (BRIDGE_SETTING, json.dumps(value, ensure_ascii=False, allow_nan=False)))


def ability_unit_id(content_id, script, ability):
    return "kana-ability:" + hashlib.sha256(json.dumps([content_id, script, ability]).encode()).hexdigest()[:32]


def _pending(store, content_id, script):
    from .session_questions import iter_rows
    for row in store._db.execute("SELECT session_id,question_index FROM session_checkpoint"):
        for question in iter_rows(store._db, row["session_id"], row["question_index"]):
            if question["script"] == script and (question["content_id"] == content_id
                    or content_id in question.get("metadata", {}).get("context_kana_ids", [])):
                return True
    return False


def _reason(store, unit):
    if unit["ability"] == "legacy_combined":
        return "combined_evidence_needs_diagnosis"
    if _pending(store, unit["content_id"], unit["script"]):
        return "unfinished_queue"
    if store.get_setting(CONTENT_CONTROLS_SETTING, {}).get(unit["content_id"], {}).get("suspended"):
        return "paused_content"
    rows = store._db.execute("SELECT * FROM study_card WHERE content_id=? AND script=? AND review_count>0",
                             (unit["content_id"], unit["script"])).fetchall()
    for card in rows:
        if SKILL_TO_ABILITY.get(card["skill"]) != unit["ability"]:
            continue
        if card["suspended"]:
            return "paused_member"
        if card["scheduler_version"] != ALGORITHM_VERSION:
            return "scheduler_version"
        if json.loads(card["scheduler_state_json"]).get("state") != 2:
            return "short_learning_steps"
    if len({c["content_version"] for c in rows if SKILL_TO_ABILITY.get(c["skill"]) == unit["ability"]}) > 1:
        return "content_version"
    return None


def register_legacy_map(store):
    """Audit capability identities without rescheduling anything."""
    db = store._db
    if db.in_transaction:
        raise RuntimeError("Bridge registration requires its own transaction")
    with db:
        db.execute("BEGIN IMMEDIATE")
        saved = _load(store)
        groups = {}
        for row in db.execute("SELECT * FROM study_card WHERE review_count>0 ORDER BY card_id"):
            ability = SKILL_TO_ABILITY.get(row["skill"])
            if ability is None:
                continue
            uid = ability_unit_id(row["content_id"], row["script"], ability)
            groups.setdefault(uid, []).append(dict(row))
        for uid, cards in groups.items():
            first = cards[0]
            unit = saved["units"].get(uid) or {
                "unit_id": uid, "content_id": first["content_id"], "script": first["script"],
                "ability": SKILL_TO_ABILITY[first["skill"]], "status": "deferred", "attempts": {},
            }
            if (unit["status"] in {"ready", "active"}
                    and unit.get("legacy_card_ids") != [card["card_id"] for card in cards]):
                if unit["status"] == "active":
                    raise ValueError("Active kana ability has an unexpected legacy member")
                unit.update(status="deferred", deferred_reason="legacy_direction_changed")
            unit["legacy_card_ids"] = [card["card_id"] for card in cards]
            unit["legacy_skills"] = [card["skill"] for card in cards]
            if unit["status"] == "deferred":
                unit["deferred_reason"] = _reason(store, unit) or "await_independent_review"
            saved["units"][uid] = unit
        _write(store, saved)
    return saved


def _members(store, unit):
    return [dict(row) for row in store._db.execute(
        "SELECT * FROM study_card WHERE content_id=? AND script=? AND review_count>0 ORDER BY card_id",
        (unit["content_id"], unit["script"])) if SKILL_TO_ABILITY.get(row["skill"]) == unit["ability"]]


def _fingerprint(store, unit):
    # Include live auxiliary due, pause, version and every member's real anchor.
    rows = _members(store, unit)
    for row in rows:
        latest = store._db.execute("SELECT event_id FROM review_event WHERE card_id=? ORDER BY occurred_at_utc DESC,rowid DESC LIMIT 1",
                                   (row["card_id"],)).fetchone()
        row["latest_event_id"] = latest[0] if latest else None
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def _prepare_review(store, event_id, *, require_registered):
    db = store._db
    row = db.execute("""SELECT e.*,c.content_id,c.script,c.skill,c.scheduler_state_json
        FROM review_event e JOIN study_card c USING(card_id) WHERE e.event_id=?""", (event_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown independent review")
    ability = SKILL_TO_ABILITY.get(row["skill"])
    if ability is None or ability == "legacy_combined":
        return {"status": "deferred", "reason": "not_a_separable_kana_ability"}
    saved = _load(store)
    uid = ability_unit_id(row["content_id"], row["script"], ability)
    unit = saved["units"].get(uid)
    if unit is None:
        if require_registered:
            raise ValueError("Register the legacy capability mapping first")
        unit = {"unit_id": uid, "content_id": row["content_id"], "script": row["script"],
                "ability": ability, "status": "deferred", "attempts": {}}
        saved["units"][uid] = unit
    if unit.get("anchor_event_id") == event_id:
        return dict(unit)
    if unit["status"] in {"active", "planned"}:
        raise ValueError("An active shared ability must use its dedicated review API")
    cards = _members(store, unit)
    unit.update(legacy_card_ids=[c["card_id"] for c in cards], legacy_skills=[c["skill"] for c in cards])
    reason = _reason(store, unit)
    if row["assisted"] or row["outcome"] not in {"correct", "incorrect", "answered", "self_rated"}:
        reason = "not_independent"
    latest = db.execute("SELECT event_id FROM review_event WHERE card_id=? ORDER BY occurred_at_utc DESC,rowid DESC LIMIT 1",
                        (row["card_id"],)).fetchone()
    if row["scheduler_state_json"] != row["state_after_json"] or latest is None or latest[0] != event_id:
        reason = "review_is_not_current_anchor"
    candidates = []
    for card in cards:
        before = card["card_id"] == row["card_id"]
        state = json.loads(row["state_before_json"] if before else card["scheduler_state_json"])
        if card["suspended"] or state.get("state") != 2:
            reason = "paused_or_short_learning_steps"
        due = state["due"] if before else card["due_at_utc"]
        candidates.append((datetime.fromisoformat(due), card["card_id"]))
    if not candidates or min(candidates)[1] != row["card_id"]:
        reason = "reviewed_card_was_not_earliest_due"
    if reason not in (None, "unfinished_queue"):
        unit.update(status="deferred", deferred_reason=reason)
        unit.pop("candidate_event_id", None)
        unit.pop("candidate_fingerprint", None)
    else:
        unit.update(status="deferred" if reason else "ready", deferred_reason=reason or "await_shared_routing",
                    carrier_card_id=row["card_id"], carrier_skill=row["skill"],
                    anchor_event_id=event_id, bridge_event_id=event_id,
                    candidate_event_id=event_id, candidate_fingerprint=_fingerprint(store, unit),
                    scheduler_state=json.loads(row["state_after_json"]),
                    bridge_at_utc=row["occurred_at_utc"], legacy_due_at_bridge={cid: due.isoformat() for due, cid in candidates})
    _write(store, saved)
    return dict(unit)


def bridge_after_review(store, event_id):
    """Prepare from an already committed independent review, never score twice."""
    if store._db.in_transaction:
        raise RuntimeError("Bridge preparation requires its own transaction")
    with store._db:
        store._db.execute("BEGIN IMMEDIATE")
        return _prepare_review(store, event_id, require_registered=True)


def _activate_ready(store, unit):
    if unit["status"] == "active":
        return
    if unit["status"] != "ready" or _reason(store, unit):
        raise ValueError("Ability bridge is not ready")
    cards = _members(store, unit)
    if ([c["card_id"] for c in cards] != unit["legacy_card_ids"]
            or any(c["suspended"] for c in cards)
            or unit.get("candidate_fingerprint") and _fingerprint(store, unit) != unit["candidate_fingerprint"]):
        raise ValueError("Ability members changed after bridge preparation")
    carrier = next((c for c in cards if c["card_id"] == unit["carrier_card_id"]), None)
    if carrier is None or json.loads(carrier["scheduler_state_json"]) != unit["scheduler_state"]:
        raise ValueError("Carrier changed after bridge preparation")
    event = store._db.execute("SELECT * FROM review_event WHERE event_id=?", (unit.get("anchor_event_id"),)).fetchone()
    latest = store._db.execute("SELECT event_id FROM review_event WHERE card_id=? ORDER BY occurred_at_utc DESC,rowid DESC LIMIT 1",
                               (unit["carrier_card_id"],)).fetchone()
    if (event is None or latest is None or event["event_id"] != latest[0]
            or event["card_id"] != unit["carrier_card_id"] or event["assisted"]
            or event["outcome"] not in {"correct", "incorrect", "answered", "self_rated"}
            or json.loads(event["state_after_json"]) != unit["scheduler_state"]
            or unit.get("bridge_event_id") != event["event_id"]
            or unit.get("candidate_event_id", event["event_id"]) != event["event_id"]):
        raise ValueError("Bridge candidate lacks its current independent review")
    from .kana_routing import initialize_legacy_support
    initialize_legacy_support(store, unit)
    unit.update(status="active", deferred_reason=None)
    unit.pop("candidate_event_id", None)
    unit.pop("candidate_fingerprint", None)


def activate_bridge(store, unit_id, *, routing_ready=False):
    """Explicit integration entry; repeated activation is a read-only no-op."""
    if routing_ready is not True:
        raise ValueError("Shared scheduling and auxiliary routing must be connected first")
    db = store._db
    if db.in_transaction:
        raise RuntimeError("Bridge activation requires its own transaction")
    with db:
        db.execute("BEGIN IMMEDIATE")
        saved = _load(store)
        unit = saved["units"].get(unit_id)
        if unit is None:
            raise ValueError("Ability bridge is not ready")
        if unit["status"] == "active":
            return dict(unit)
        _activate_ready(store, unit)
        _write(store, saved)
    return dict(unit)


def maintain_bridges(store, *, reviewed_event_id=None):
    """At queue commit boundaries, activate only previously earned candidates.

    The final queued task may be a contextual word or another topic. It never
    supplies a new formal score. Changed members (including auxiliary changes)
    invalidate the candidate; a subsequent genuine review can prepare another.
    """
    if not store._db.in_transaction:
        with store._db:
            store._db.execute("BEGIN IMMEDIATE")
            return maintain_bridges(store, reviewed_event_id=reviewed_event_id)
    if reviewed_event_id:
        _prepare_review(store, reviewed_event_id, require_registered=False)
    saved = _load(store)
    changed = False
    for unit in saved["units"].values():
        if unit["status"] not in {"deferred", "ready"} or not unit.get("candidate_event_id"):
            continue
        if _fingerprint(store, unit) != unit.get("candidate_fingerprint"):
            unit.update(status="deferred", deferred_reason="members_changed_after_candidate")
            unit.pop("candidate_event_id", None)
            unit.pop("candidate_fingerprint", None)
            changed = True
        elif not _reason(store, unit):
            unit["status"] = "ready"
            _activate_ready(store, unit)
            changed = True
    if changed:
        _write(store, saved)


def effective_carrier(store, content_id, script, skill):
    """Resolve aliases only for explicitly activated groups; legacy otherwise."""
    ability = SKILL_TO_ABILITY.get(skill)
    if ability is None:
        return None
    unit = _load(store)["units"].get(ability_unit_id(content_id, script, ability))
    return dict(unit) if unit and unit["status"] == "active" else None


def auxiliary_anchor(store, card_id):
    """Collapse every activated sibling to one carrier / real-review anchor.

    Callers must deduplicate this pair before applying the existing 0.25 cap;
    no past word attempt is replayed or re-awarded by activating a bridge.
    """
    for unit in _load(store)["units"].values():
        if unit["status"] == "active" and card_id in unit["legacy_card_ids"]:
            return unit["carrier_card_id"], unit["anchor_event_id"]
    return None


def record_ability_review(store, unit_id, rating, *, attempt_id, answered_text=None, now=None):
    """Execute one independent standalone bridge review atomically and once.

    Pending legacy queues are refused. Existing queued answers keep their old
    checkpoint contract; an adopter must not grade them through both paths.
    """
    if type(rating) is not int or rating not in (1, 2, 3, 4) or not isinstance(attempt_id, str) or not attempt_id:
        raise ValueError("A valid rating and unique independent attempt are required")
    now = _utc(now)
    db = store._db
    if db.in_transaction:
        raise RuntimeError("Shared review requires its own transaction")
    with db:
        db.execute("BEGIN IMMEDIATE")
        saved = _load(store)
        unit = saved["units"].get(unit_id)
        if not unit or unit["status"] != "active":
            raise ValueError("Shared ability routing is not active")
        if attempt_id in unit["attempts"]:
            return {**unit["attempts"][attempt_id], "already_recorded": True}
        if _pending(store, unit["content_id"], unit["script"]):
            raise ValueError("Pending legacy questions must finish before a shared review")
        carrier = db.execute("SELECT * FROM study_card WHERE card_id=?", (unit["carrier_card_id"],)).fetchone()
        if (carrier is None or carrier["suspended"] or not store.is_content_available(unit["content_id"], now)
                or json.loads(carrier["scheduler_state_json"]) != unit["scheduler_state"]):
            raise ValueError("Shared carrier changed or is unavailable")
        card = Card.from_dict(unit["scheduler_state"])
        if card.last_review and card.last_review > now:
            raise ValueError("Review cannot precede the current anchor")
        before = card.to_json()
        updated, log = store.scheduler.review_card(card, Rating(rating), review_datetime=now)
        event_id, session_id = str(uuid4()), str(uuid4())
        after = updated.to_json()
        db.execute("INSERT INTO study_session VALUES (?,?,?,?,?)",
                   (session_id, now.isoformat(), store.time_zone, attempt_id, "kana-ability-bridge-v1"))
        db.execute("""UPDATE study_card SET scheduler_state_json=?,due_at_utc=?,review_count=review_count+1
            WHERE card_id=?""", (after, updated.due.isoformat(), unit["carrier_card_id"]))
        evidence = json.loads(log.to_json())
        evidence["ability_bridge"] = {"unit_id": unit_id, "attempt_id": attempt_id, "carrier_skill": unit["carrier_skill"]}
        db.execute("INSERT INTO review_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (event_id, session_id, unit["carrier_card_id"], now.isoformat(), rating, answered_text,
                    0, "self_rated", 0, before, after, ALGORITHM_VERSION, json.dumps(evidence)))
        result = {"event_id": event_id, "unit_id": unit_id, "due_at_utc": updated.due.isoformat(), "rating": rating}
        unit["attempts"][attempt_id] = result
        _write(store, saved)
        from .kana_routing import finish_review
        finish_review(store, unit, event_id, json.loads(after), skill=unit["carrier_skill"], independent=True)
    return result
