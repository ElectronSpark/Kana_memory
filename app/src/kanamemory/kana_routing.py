"""One scheduling authority for explicitly activated kana abilities.

Raw cards and reviews remain historical facts. Only live scheduling consumers
use this projection. Queue tokens freeze the routing identity, never a copy of
the due date: auxiliary adjustments and real reviews may legitimately change it.
"""
from __future__ import annotations

import json
from datetime import datetime

from .kana_ability_bridge import BRIDGE_SETTING, SKILL_TO_ABILITY, ability_unit_id


ROUTE_KEY = "kana_ability_route_v1"
PROBE_SKILLS = {"reading": "kana_romaji", "spelling": "romaji_kana_full"}


def active_units(store):
    db = store._db
    # Repeated identity lookups need only a small committed-version token.
    # total_changes is monotonic even after rollback; external commits are
    # covered by data_version. Never publish or reuse this cache in a write
    # transaction, where the existing raw-value path remains authoritative.
    version = None
    if not db.in_transaction:
        version = (db.total_changes, db.execute('PRAGMA data_version').fetchone()[0])
        committed = getattr(store, '_committed_kana_routes', None)
        if committed is not None and committed[0] == version:
            return committed[1]
    row = store._db.execute("SELECT value_json FROM app_setting WHERE key=?", (BRIDGE_SETTING,)).fetchone()
    raw = row[0] if row else None
    cached = getattr(store, "_active_kana_routes", None)
    if cached is not None and cached[0] == raw:
        units = cached[1]
    else:
        value = json.loads(raw) if raw is not None else {}
        units = {key: unit for key, unit in value.get("units", {}).items() if unit.get("status") in {"active", "planned"}}
    # Compare stored bytes whenever the committed token cannot be reused.
    # Transactional reads never replace the separately committed projection.
    store._active_kana_routes = (raw, units)
    if version is not None and not db.in_transaction and version == (
            db.total_changes, db.execute('PRAGMA data_version').fetchone()[0]):
        store._committed_kana_routes = version, units
    return units


def unit_for(store, content_id, skill, script):
    ability = SKILL_TO_ABILITY.get(skill)
    return active_units(store).get(ability_unit_id(content_id, script, ability)) if ability else None


def token(unit, skill):
    identity = ({"native_session_id": unit["native_session_id"]} if unit.get("origin") == "native"
                else {"bridge_event_id": unit["bridge_event_id"]})
    return {"version": 1, "unit_id": unit["unit_id"], **identity,
            "carrier_card_id": unit["carrier_card_id"], "skill": skill}


def _unrouted_pending(store):
    from .session_questions import iter_rows
    result = set()
    for checkpoint in store._db.execute("SELECT session_id,question_index FROM session_checkpoint"):
        for question in iter_rows(store._db, checkpoint[0], checkpoint[1]):
            if ROUTE_KEY not in question.get("metadata", {}):
                result.add((question["content_id"], question["script"]))
            result.update((cid, question["script"])
                          for cid in question.get("metadata", {}).get("context_kana_ids", ()))
    return result


def prospective_identities(store, identities):
    """Read-only queue/admission identities, including genuinely new abilities."""
    units = active_units(store)
    known = {(r[0], r[1], SKILL_TO_ABILITY.get(r[2])) for r in store._db.execute(
        "SELECT content_id,script,skill FROM study_card")}
    result, fresh = {}, {}
    for cid, skill, script in identities:
        ability = SKILL_TO_ABILITY.get(skill)
        uid = ability_unit_id(cid, script, ability) if ability else None
        key = cid, skill, script
        result[key] = uid if uid in units else key
        if uid not in units and ability not in {None, "legacy_combined"} and (cid, script, ability) not in known:
            fresh[key] = uid
    legacy_pending = _unrouted_pending(store) if fresh else set()
    for key, uid in fresh.items():
        if (key[0], key[2]) not in legacy_pending:
            result[key] = uid
    return result


def reserve_new_units(store, items, session_id):
    """Declare a new ability in the queue transaction, without creating a card."""
    if not store._db.in_transaction:
        raise RuntimeError("New kana abilities require the queue transaction")
    from .kana_ability_bridge import _load, _write
    saved = _load(store)
    existing = {(r["content_id"], r["script"], SKILL_TO_ABILITY.get(r["skill"]))
                for r in store._db.execute("SELECT content_id,script,skill FROM study_card")}
    candidates = {}
    for item in items:
        ability = SKILL_TO_ABILITY.get(item["kind"])
        if ability is None or ability == "legacy_combined":
            continue
        uid = ability_unit_id(item["content_id"], item["script"], ability)
        if uid not in saved["units"] and (item["content_id"], item["script"], ability) not in existing:
            candidates.setdefault(uid, item)
    if not candidates:
        return
    legacy_pending = _unrouted_pending(store)
    changed = False
    for uid, item in candidates.items():
        cid, script, skill = item["content_id"], item["script"], item["kind"]
        if (cid, script) in legacy_pending:
            continue
        saved["units"][uid] = {"unit_id": uid, "content_id": cid, "script": script,
            "ability": SKILL_TO_ABILITY[skill], "status": "planned", "origin": "native",
            "native_session_id": session_id, "carrier_card_id": store._card_key(cid, skill, script),
            "carrier_skill": skill, "legacy_card_ids": [], "legacy_skills": [], "attempts": {}}
        changed = True
    if changed:
        _write(store, saved)


def projected_card(unit, row, skill, *, include_probes=True):
    result = {**dict(row), "skill": skill, "carrier_skill": unit["carrier_skill"],
              "ability_unit_id": unit["unit_id"], "ability": unit["ability"],
              "mode_reviewed": skill in unit.get("mode_reviews", unit.get("legacy_skills", ())),
              "last_kind": unit.get("last_kind", unit["carrier_skill"])}
    probe = probe_due(unit)
    if include_probes and probe and skill == PROBE_SKILLS.get(unit["ability"]):
        # Shared short steps always retain their actual learning/relearning due.
        state = json.loads(row["scheduler_state_json"])
        if state.get("state") == 2:
            result["base_due_at_utc"] = row["due_at_utc"]
            result["probe_due_at_utc"] = probe
            result["due_at_utc"] = min((row["due_at_utc"], probe), key=datetime.fromisoformat)
    return result


def probe_due(unit):
    if unit["ability"] not in PROBE_SKILLS:
        return None
    if unit.get("probe_due_at_utc"):
        return unit["probe_due_at_utc"]
    # Earlier opt-in records predate probe metadata. Preserve their oldest
    # documented recall deadline; never claim an unobserved input success.
    dates = list(unit.get("legacy_due_at_bridge", {}).values())
    if unit.get("scheduler_state", {}).get("due"):
        dates.append(unit["scheduler_state"]["due"])
    return min(dates, key=datetime.fromisoformat) if dates else None


def initialize_legacy_support(store, unit):
    free = PROBE_SKILLS.get(unit["ability"])
    if free:
        dates = [unit["scheduler_state"]["due"]]
        if unit["carrier_skill"] != free:
            previous = store.get_card(unit["content_id"], free, unit["script"])
            if previous:
                dates.append(previous["due_at_utc"])
        unit["probe_due_at_utc"] = min(dates, key=datetime.fromisoformat)
    unit["last_kind"] = unit["carrier_skill"]
    unit["mode_reviews"] = {}
    for cid, skill in zip(unit["legacy_card_ids"], unit["legacy_skills"]):
        last = store._db.execute("SELECT event_id FROM review_event WHERE card_id=? ORDER BY occurred_at_utc DESC,rowid DESC LIMIT 1", (cid,)).fetchone()
        if last:
            unit["mode_reviews"][skill] = last[0]


def identity(store, content_id, skill, script):
    unit = unit_for(store, content_id, skill, script)
    return unit["unit_id"] if unit else (content_id, skill, script)


def current_identities(store, keys):
    """Resolve one read operation's keys using current, not prospective, routes.

    Returned IDs are detached scalars. A later action must resolve again; this
    projection does not authorize queue writes or bypass transaction checks.
    """
    keys = set(keys)
    if not keys:
        return {}
    units = active_units(store)
    result = {}
    for cid, skill, script in keys:
        ability = SKILL_TO_ABILITY.get(skill)
        unit = units.get(ability_unit_id(cid, script, ability)) if ability else None
        result[cid, skill, script] = unit["unit_id"] if unit else (cid, skill, script)
    return result


def representatives(cards, now):
    """Choose one allowed actual mode per live ability; legacy rows stay apart.

    A strictly earlier input deadline wins. At an equal deadline, alternate
    modes where available; another choice cannot postpone the input deadline.
    Callers first filter settings, prerequisite/stimulus and mode availability.
    """
    grouped = {}
    for card in cards:
        key = card.get("ability_unit_id") or (card["content_id"], card["skill"], card["script"])
        grouped.setdefault(key, []).append(card)
    def priority(card):
        due = datetime.fromisoformat(card["due_at_utc"])
        return (due > now, due if due > now else now,
                0 if card.get("base_due_at_utc") and due < datetime.fromisoformat(card["base_due_at_utc"]) else 1,
                card["skill"] == card.get("last_kind"), card["skill"])
    return [min(group, key=priority) for group in grouped.values()]


def candidate_cards(store, kinds, now):
    """Allowed current modes; actual question eligibility is checked by builders."""
    from .domain import _adaptive_stages, _adaptive_new_kinds
    result, stages = [], {}
    for card in project_cards(store, aliases=True):
        if card["skill"] not in kinds or card["suspended"] or card["review_count"] <= 0:
            continue
        if card.get("mode_reviewed") is False:
            script = card["script"]
            if script not in stages:
                stages[script] = _adaptive_stages(store, script, now)
            if card["skill"] not in _adaptive_new_kinds(kinds, stages[script].get(card["content_id"], 0)):
                continue
        result.append(card)
    return result


def deduplicate_questions(store, questions, now, *, new_abilities=False):
    """Only newly generated spaced questions; never consume a saved suffix."""
    units = active_units(store)
    if not units and not new_abilities:
        return questions
    cards = {(c["content_id"], c["skill"], c["script"]): c for c in project_cards(store, aliases=True, units=units)}
    future_keys = prospective_identities(store, ((q.content_id, q.kind, q.script) for q in questions)) if new_abilities else {}
    groups, chosen = {}, set()
    for q in questions:
        ability = SKILL_TO_ABILITY.get(q.kind)
        uid = ability_unit_id(q.content_id, q.script, ability) if ability else None
        key = future_keys.get((q.content_id, q.kind, q.script),
                              uid if uid in units else (q.content_id, q.kind, q.script))
        groups.setdefault(key, []).append(q)
    for group in groups.values():
        actual = [cards[q.content_id, q.kind, q.script] for q in group if (q.content_id, q.kind, q.script) in cards]
        if actual:
            row = representatives(actual, now)[0]
            chosen.add(id(next(q for q in group if q.kind == row["skill"])))
        else:
            # Builders already filtered scaffolding. Keep their mixed order;
            # choosing the lowest-level mode here hides every unlocked input.
            chosen.add(id(group[0]))
    return [q for q in questions if id(q) in chosen]


def bind_questions(store, items):
    """Bind newly accepted questions only; never rewrite a saved queue suffix."""
    units = active_units(store)
    if not units:
        return items
    result = []
    for item in items:
        ability = SKILL_TO_ABILITY.get(item["kind"])
        unit = units.get(ability_unit_id(item["content_id"], item["script"], ability)) if ability else None
        if unit:
            item = {**item, "metadata": {**item.get("metadata", {}), ROUTE_KEY: token(unit, item["kind"])}}
        result.append(item)
    return result


def bind_encoded(store, items, encoded):
    bound = bind_questions(store, items)
    if bound is items:
        return items, encoded
    return bound, tuple(old_text if before is after else json.dumps(after, ensure_ascii=False, allow_nan=False)
                        for before, after, old_text in zip(items, bound, encoded))


def resolve_review(store, content_id, skill, script, question=None):
    unit = unit_for(store, content_id, skill, script)
    frozen = (question or {}).get("metadata", {}).get(ROUTE_KEY)
    if frozen is not None and (not isinstance(frozen, dict) or type(frozen.get("version")) is not int
                               or unit is None or frozen != token(unit, skill)):
        raise ValueError("Saved kana ability route does not match its current authority")
    if unit and question is not None and frozen is None:
        raise ValueError("An unfinished legacy question cannot be rerouted")
    if unit:
        row = store._db.execute("SELECT * FROM study_card WHERE card_id=?", (unit["carrier_card_id"],)).fetchone()
        if row is None and unit["status"] == "planned" and unit.get("origin") == "native":
            return unit
        if row is None or (row["content_id"], row["script"], row["skill"]) != (
                content_id, script, unit["carrier_skill"]):
            raise ValueError("Kana ability carrier is missing or inconsistent")
    return unit


def project_cards(store, rows=None, *, aliases=False, units=None, include_probes=True):
    """Fresh live states, once per ability, optionally expanded by question kind.

    Projection is in memory and does not create fictitious alias cards/events.
    All input rows must be complete; filtering happens AFTER this projection.
    """
    rows = list(store._db.execute("SELECT * FROM study_card")) if rows is None else list(rows)
    units = active_units(store) if units is None else units
    if not units:
        return [dict(row) for row in rows]
    result, seen = [], set()
    by_id = {row["card_id"]: dict(row) for row in rows}
    for raw in rows:
        row = dict(raw)
        ability = SKILL_TO_ABILITY.get(row["skill"])
        unit = units.get(ability_unit_id(row["content_id"], row["script"], ability)) if ability else None
        if unit is None:
            result.append(row)
            continue
        uid = unit["unit_id"]
        if uid in seen:
            continue
        seen.add(uid)
        carrier = by_id.get(unit["carrier_card_id"])
        if carrier is None:
            carrier = store._db.execute("SELECT * FROM study_card WHERE card_id=?", (unit["carrier_card_id"],)).fetchone()
        if carrier is None:
            raise ValueError("Kana ability carrier is missing")
        carrier = dict(carrier)
        kinds = [kind for kind, target in SKILL_TO_ABILITY.items() if target == ability]
        projected = [projected_card(unit, carrier, kind, include_probes=include_probes) for kind in kinds]
        for current in projected:
            current["history_card_ids"] = list(unit["legacy_card_ids"])
            current["history_review_count"] = sum(by_id[cid]["review_count"]
                for cid in unit["legacy_card_ids"] if cid in by_id)
        if aliases:
            result.extend(projected)
        else:
            result.append(min(projected, key=lambda c: (datetime.fromisoformat(c["due_at_utc"]),
                                                        c["skill"] != unit["carrier_skill"])))
    return result


def finish_review(store, unit, event_id, state, *, skill, independent):
    """Advance the real anchor inside the existing answer transaction."""
    if not store._db.in_transaction:
        raise RuntimeError("Ability anchor updates require the answer transaction")
    value = store.get_setting(BRIDGE_SETTING)
    current = value["units"][unit["unit_id"]]
    probe = probe_due(current)
    if current["ability"] in PROBE_SKILLS:
        if probe is None or independent and skill == PROBE_SKILLS[current["ability"]]:
            probe = state["due"]
        else:
            probe = min((probe, state["due"]), key=datetime.fromisoformat)
        current["probe_due_at_utc"] = probe
    if current["status"] == "planned":
        current.update(legacy_card_ids=[current["carrier_card_id"]], legacy_skills=[current["carrier_skill"]])
    current.setdefault("mode_reviews", {})[skill] = event_id
    current["last_kind"] = skill
    current.update(status="active", anchor_event_id=event_id, scheduler_state=state)
    store._db.execute("UPDATE app_setting SET value_json=? WHERE key=?",
                     (json.dumps(value, ensure_ascii=False, allow_nan=False), BRIDGE_SETTING))


def validate_database(db):
    """Validate routing identities before accepting a portable learner record."""
    setting = db.execute("SELECT value_json FROM app_setting WHERE key=?", (BRIDGE_SETTING,)).fetchone()
    if setting is None:
        return {}
    value = json.loads(setting[0])
    if (not isinstance(value, dict) or type(value.get("version")) is not int
            or value.get("version") != 1 or not isinstance(value.get("units"), dict)):
        raise ValueError("Invalid kana ability mapping")
    units = value["units"]
    for uid, unit in units.items():
        if (not isinstance(unit, dict) or unit.get("unit_id") != uid
                or unit.get("ability") not in set(SKILL_TO_ABILITY.values())
                or uid != ability_unit_id(unit.get("content_id"), unit.get("script"), unit.get("ability"))
                or unit.get("status") not in {"deferred", "ready", "active", "planned"}
                or not isinstance(unit.get("legacy_card_ids"), list)
                or any(not isinstance(cid, str) for cid in unit["legacy_card_ids"])
                or (not unit["legacy_card_ids"] and unit.get("status") != "planned")
                or len(set(unit["legacy_card_ids"])) != len(unit["legacy_card_ids"])):
            raise ValueError("Invalid kana ability identity")
        native = unit.get("origin") == "native"
        if native:
            from .domain import StudyStore
            if (unit["status"] not in {"planned", "active"}
                    or SKILL_TO_ABILITY.get(unit.get("carrier_skill")) != unit["ability"]
                    or unit["ability"] == "legacy_combined"
                    or unit.get("carrier_card_id") != StudyStore._card_key(unit["content_id"], unit["carrier_skill"], unit["script"])
                    or db.execute("SELECT 1 FROM session_question WHERE session_id=? AND json_extract(snapshot_json,'$.metadata.kana_ability_route_v1.unit_id')=?",
                                  (unit.get("native_session_id"), uid)).fetchone() is None):
                raise ValueError("Invalid new kana ability reservation")
        elif unit["status"] == "planned":
            raise ValueError("Only newly reserved kana abilities can be unscored")
        probe = unit.get("probe_due_at_utc")
        if probe is not None:
            parsed = datetime.fromisoformat(probe)
            if parsed.tzinfo is None or unit["ability"] not in PROBE_SKILLS:
                raise ValueError("Invalid kana retrieval deadline")
        if unit["status"] == "planned":
            if (unit["legacy_card_ids"] or unit.get("legacy_skills") or unit.get("anchor_event_id")
                    or unit.get("scheduler_state") or unit.get("probe_due_at_utc")
                    or unit.get("mode_reviews") not in (None, {}) or unit.get("last_kind") is not None
                    or any(SKILL_TO_ABILITY.get(row[0]) == unit["ability"] for row in db.execute(
                        "SELECT skill FROM study_card WHERE content_id=? AND script=?", (unit["content_id"], unit["script"])))):
                raise ValueError("Unscored kana ability already has formal progress")
            continue
        skills = []
        for cid in unit["legacy_card_ids"]:
            card = db.execute("SELECT * FROM study_card WHERE card_id=?", (cid,)).fetchone()
            if card is None or (card["content_id"], card["script"], SKILL_TO_ABILITY.get(card["skill"])) != (
                    unit["content_id"], unit["script"], unit["ability"]):
                raise ValueError("Kana ability member belongs to a different target")
            skills.append(card["skill"])
        if skills != unit.get("legacy_skills"):
            raise ValueError("Kana ability member directions are inconsistent")
        candidate = "candidate_event_id" in unit or "candidate_fingerprint" in unit
        if candidate:
            fingerprint = unit.get("candidate_fingerprint")
            if (unit["status"] not in {"ready", "deferred"} or not isinstance(fingerprint, str)
                    or len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint)
                    or not isinstance(unit.get("candidate_event_id"), str)
                    or unit["candidate_event_id"] != unit.get("bridge_event_id")
                    or unit["candidate_event_id"] != unit.get("anchor_event_id")):
                raise ValueError("Invalid deferred kana bridge candidate")
        if unit["status"] == "deferred" and not candidate:
            continue
        if (unit["ability"] == "legacy_combined" or unit.get("carrier_card_id") not in unit["legacy_card_ids"]
                or unit.get("carrier_skill") != skills[unit["legacy_card_ids"].index(unit["carrier_card_id"])]):
            raise ValueError("Invalid kana ability carrier")
        for field in (("anchor_event_id",) if native else ("bridge_event_id", "anchor_event_id")):
            event = db.execute("SELECT * FROM review_event WHERE event_id=?", (unit.get(field),)).fetchone()
            if event is None or event["card_id"] != unit["carrier_card_id"]:
                raise ValueError("Invalid kana ability review anchor")
            if field == "bridge_event_id" and (event["assisted"] or event["outcome"] not in {"correct", "incorrect", "answered", "self_rated"}):
                raise ValueError("Kana bridge requires an independent review")
            if field == "anchor_event_id" and json.loads(event["state_after_json"]) != unit.get("scheduler_state"):
                raise ValueError("Kana ability formal state does not match its anchor")
        latest = db.execute("SELECT event_id FROM review_event WHERE card_id=? ORDER BY occurred_at_utc DESC,rowid DESC LIMIT 1",
                            (unit["carrier_card_id"],)).fetchone()
        if unit["status"] == "active" and (latest is None or latest[0] != unit["anchor_event_id"]):
            raise ValueError("Kana ability anchor is not the latest real review")
        if unit["status"] == "active":
            actual_ids = {row["card_id"] for row in db.execute(
                "SELECT card_id,skill FROM study_card WHERE content_id=? AND script=? AND review_count>0",
                (unit["content_id"], unit["script"])) if SKILL_TO_ABILITY.get(row["skill"]) == unit["ability"]}
            if actual_ids != set(unit["legacy_card_ids"]):
                raise ValueError("Active kana ability has unregistered direction cards")
            modes = unit.get("mode_reviews", {})
            if not isinstance(modes, dict) or native and not modes:
                raise ValueError("Invalid kana mode history")
            for kind, event_id in modes.items():
                if SKILL_TO_ABILITY.get(kind) != unit["ability"] or not isinstance(event_id, str):
                    raise ValueError("Invalid kana mode history")
                actual = db.execute("""SELECT e.event_id FROM review_event e JOIN study_card c USING(card_id)
                    WHERE c.content_id=? AND c.script=?
                    AND COALESCE(json_extract(e.review_log_json,'$.ability_route.skill'),c.skill)=?
                    ORDER BY e.occurred_at_utc DESC,e.rowid DESC LIMIT 1""",
                    (unit["content_id"], unit["script"], kind)).fetchone()
                if actual is None or actual[0] != event_id:
                    raise ValueError("Kana mode history is not its latest actual review")
            if "last_kind" in unit or native:
                anchor = db.execute("""SELECT COALESCE(json_extract(e.review_log_json,'$.ability_route.skill'),c.skill)
                    FROM review_event e JOIN study_card c USING(card_id) WHERE e.event_id=?""",
                    (unit["anchor_event_id"],)).fetchone()
                if anchor is None or unit.get("last_kind") != anchor[0]:
                    raise ValueError("Kana last mode does not match its review anchor")
    for event in db.execute("""SELECT e.*,c.content_id,c.script,c.skill FROM review_event e
            JOIN study_card c USING(card_id)
            WHERE json_type(e.review_log_json,'$.ability_route') IS NOT NULL"""):
        route = json.loads(event["review_log_json"])["ability_route"]
        unit = units.get(route.get("unit_id")) if isinstance(route, dict) else None
        if (unit is None or type(route.get("version")) is not int or unit["status"] != "active"
                or route != token(unit, route.get("skill"))
                or unit["carrier_card_id"] != event["card_id"]
                or (unit["content_id"], unit["script"]) != (event["content_id"], event["script"])
                or SKILL_TO_ABILITY.get(route.get("skill")) != unit["ability"]):
            raise ValueError("Invalid kana ability event route")
    for row in db.execute("""SELECT snapshot_json FROM session_question
            WHERE json_type(snapshot_json,'$.metadata.kana_ability_route_v1') IS NOT NULL"""):
        question = json.loads(row[0])
        frozen = question["metadata"][ROUTE_KEY]
        unit = units.get(frozen.get("unit_id")) if isinstance(frozen, dict) else None
        if (unit is None or type(frozen.get("version")) is not int or unit["status"] not in {"active", "planned"} or frozen != token(unit, question["kind"])
                or SKILL_TO_ABILITY.get(question["kind"]) != unit["ability"]
                or (question["content_id"], question["script"]) != (unit["content_id"], unit["script"])):
            raise ValueError("Invalid frozen kana ability route")
    return units


def validate_review_route(event, card, question, units):
    route = json.loads(event["review_log_json"]).get("ability_route")
    frozen = question.get("metadata", {}).get(ROUTE_KEY)
    if route is None and frozen is None:
        return False
    if not isinstance(route, dict):
        raise ValueError("Missing kana ability review route")
    unit = units.get(route.get("unit_id"))
    if (unit is None or unit["status"] != "active" or route != token(unit, question["kind"])
            or frozen != route or event["card_id"] != unit["carrier_card_id"]
            or (question["content_id"], question["script"]) != (card["content_id"], card["script"])
            or SKILL_TO_ABILITY.get(question["kind"]) != unit["ability"]):
        raise ValueError("Saved question and kana ability review disagree")
    return True
