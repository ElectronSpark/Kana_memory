"""Bounded auxiliary reading-rule evidence, never transferred kana ratings.

Writes join the caller's answer/queue transaction. The four rule states live in
the ordinary backed-up settings; immutable observations use the existing evidence
tables. No word memory unit, sibling kana rating, or synthetic FSRS review is made.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from . import app_clock
from hashlib import sha256
import json
import unicodedata

from .kana_reading_rules import (READING_RULE_IDS, SMALL_KANA_RULE, SOKUON_RULE,
    LONG_VOWEL_RULE, NASAL_RULE, SMALL_COMBINING_KANA, TEACHING_RULE_IDS, explicit_reading_rules)

SETTING = "kana_rule_probes_v1"
VERSION = 1
PROBE_LIMIT = 2
COOLDOWN_SECONDS = 60
REOPEN_HOURS = 24
EVIDENCE_COOLDOWN_SECONDS = 60
KIND = "kana_rule_application"


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _now(value=None):
    value = value or app_clock.utc_now()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Rule evidence requires a timezone-aware clock")
    return value.astimezone(timezone.utc)


def _text(value, limit=256):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError("Invalid rule evidence identity")
    return value


def _context(value):
    # The same combination in the other script is not a different rule context.
    value = unicodedata.normalize("NFC", _text(value))
    if value.startswith("kana:"):
        value = _text(value[5:])
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in value)


def _event_id(event_id, rule_id):
    return "kana-rule:" + sha256(_dump([_text(event_id), rule_id]).encode()).hexdigest()


def small_kana_context(reading):
    """One distinct contracted form; two words with it are one transfer context."""
    if not isinstance(reading, str) or not reading:
        return None
    reading = _context(reading)
    forms = {reading[index-1:index+1] for index, char in enumerate(reading)
             if char in SMALL_COMBINING_KANA and index and "ぁ" <= reading[index-1] <= "ゖ"
             and reading[index-1] not in SMALL_COMBINING_KANA}
    return next(iter(forms)) if len(forms) == 1 else None


def _transaction(store):
    if not store._db.in_transaction:
        raise RuntimeError("Rule writes require the caller's transaction")


def validate_rule_probe_state(value):
    """Strict optional setting validation; old backups need no migration."""
    if (not isinstance(value, dict) or set(value) != {"version", "rules"}
            or type(value["version"]) is not int or value["version"] != VERSION
            or not isinstance(value["rules"], dict) or not set(value["rules"]) <= set(READING_RULE_IDS)):
        raise ValueError("Invalid reading-rule probe state")
    for rule, entry in value["rules"].items():
        if not isinstance(entry, dict) or set(entry) != {
                "trigger_event_id", "trigger_context_id", "triggered_at_utc", "not_before_utc", "probes"}:
            raise ValueError("Invalid reading-rule trigger")
        _text(entry["trigger_event_id"])
        _text(entry["trigger_context_id"])
        start = _now(datetime.fromisoformat(entry["triggered_at_utc"]))
        after = _now(datetime.fromisoformat(entry["not_before_utc"]))
        if after < start or not isinstance(entry["probes"], list) or len(entry["probes"]) > PROBE_LIMIT:
            raise ValueError("Invalid reading-rule probe limit")
        contexts, ids = set(), set()
        for probe in entry["probes"]:
            if not isinstance(probe, dict) or set(probe) != {
                    "probe_id", "context_id", "content_id", "kind", "script", "source", "status",
                    "reserved_at_utc", "completed_at_utc", "outcome"}:
                raise ValueError("Invalid reading-rule reservation")
            for field in ("probe_id", "context_id", "content_id", "kind"):
                _text(probe[field])
            if (probe["context_id"] in contexts or probe["probe_id"] in ids
                    or probe["context_id"] == entry["trigger_context_id"]
                    or probe["script"] not in {"hiragana", "katakana"}
                    or probe["source"] not in {"kana", "word"}
                    or probe["status"] not in {"reserved", "completed"}):
                raise ValueError("Duplicate or invalid reading-rule probe")
            reserved = _now(datetime.fromisoformat(probe["reserved_at_utc"]))
            if reserved < start:
                raise ValueError("Reading-rule reservation precedes trigger")
            if probe["status"] == "reserved":
                if probe["completed_at_utc"] is not None or probe["outcome"] is not None:
                    raise ValueError("Unfinished reading-rule reservation has a result")
            elif (probe["outcome"] not in {"correct", "incorrect", "unresolved", "revealed", "cancelled"}
                    or _now(datetime.fromisoformat(probe["completed_at_utc"])) < reserved):
                raise ValueError("Invalid reading-rule completion")
            contexts.add(probe["context_id"])
            ids.add(probe["probe_id"])
    return value


def _state(store):
    row = store._db.execute("SELECT value_json FROM app_setting WHERE key=?", (SETTING,)).fetchone()
    return validate_rule_probe_state(json.loads(row[0])) if row else {"version": VERSION, "rules": {}}


def _save(store, state):
    validate_rule_probe_state(state)
    store._db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                      (SETTING, _dump(state)))


def _conditions(context_id):
    return ["context:" + sha256(context_id.encode()).hexdigest()]


HISTORY_PREFIX = 'kana_rule_probe_history_v1:'
RETIRED_PREFIX = 'kana_rule_probe_retired_v1:'


def retire_probe_question(store, receipt):
    """Keep a spent reservation, recording why its original task cannot run."""
    _transaction(store)
    marker = receipt['entry']['question'].get('metadata', {}).get('kana_rule_probe_v1')
    if not marker or not active_probe(store, marker): return
    value = {'version': 1, 'retirement_id': receipt['id'], 'marker': deepcopy(marker)}
    store._db.execute('INSERT OR IGNORE INTO app_setting VALUES (?,?)',
        (RETIRED_PREFIX + marker['probe_id'], _dump(value)))


def _retired_probe(store, probe):
    return store._db.execute('SELECT 1 FROM app_setting WHERE key=?',
        (RETIRED_PREFIX + probe['probe_id'],)).fetchone() is not None


def _finished_pair(store, entry):
    return len(entry['probes']) == PROBE_LIMIT and all(
        p['status'] == 'completed' or _retired_probe(store, p) for p in entry['probes'])


def validate_probe_retirements(db):
    from .content_queue import PREFIX
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (RETIRED_PREFIX + '*',)):
        r = json.loads(raw)
        if set(r) != {'version', 'retirement_id', 'marker'} or r['version'] != 1:
            raise ValueError('Invalid retired probe source')
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (PREFIX + r['retirement_id'],)).fetchone()
        receipt = json.loads(row[0]) if row else {}
        marker = receipt.get('entry', {}).get('question', {}).get('metadata', {}).get('kana_rule_probe_v1')
        if not marker or marker != r['marker'] or key != RETIRED_PREFIX + marker['probe_id']:
            raise ValueError('Retired probe lacks its archived question')


def _archive_trigger(store, rule, entry, successor, now):
    value = {'version': 1, 'rule_id': rule, 'entry': deepcopy(entry),
             'successor_event_id': successor, 'replaced_at_utc': now.isoformat()}
    key = HISTORY_PREFIX + sha256(_dump([rule, entry['trigger_event_id']]).encode()).hexdigest()
    store._db.execute('INSERT INTO app_setting VALUES (?,?)', (key, _dump(value)))


def validate_probe_history(db):
    """Old reservations remain auditable when a new genuine error starts a pair."""
    from .content_history import contribution_valid
    from types import SimpleNamespace
    validate_probe_retirements(db)
    store = SimpleNamespace(_db=db)
    archived = {}
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (HISTORY_PREFIX + '*',)):
        value = json.loads(raw)
        if set(value) != {'version', 'rule_id', 'entry', 'successor_event_id', 'replaced_at_utc'} or value['version'] != 1:
            raise ValueError('Invalid archived rule trigger')
        rule, entry = value['rule_id'], value['entry']
        validate_rule_probe_state({'version': VERSION, 'rules': {rule: entry}})
        expected = HISTORY_PREFIX + sha256(_dump([rule, entry['trigger_event_id']]).encode()).hexdigest()
        successor = db.execute('SELECT * FROM learning_evidence WHERE event_id=?', (value['successor_event_id'],)).fetchone()
        invalid = not contribution_valid(db, entry['trigger_event_id'], rule, 'application')
        retired_pair = (_finished_pair(store, entry) and any(_retired_probe(store, p) for p in entry['probes'])
            and _now(datetime.fromisoformat(value['replaced_at_utc'])) >= _now(datetime.fromisoformat(entry['triggered_at_utc'])) + timedelta(hours=REOPEN_HOURS))
        if (key != expected or not (invalid or retired_pair)
                or not successor or successor['target_id'] != rule or successor['ability'] != 'application'
                or successor['kind'] != KIND or successor['assisted']
                or successor['outcome'] not in {'incorrect', 'unresolved'}
                or successor['occurred_at_utc'] != value['replaced_at_utc']
                or _now(datetime.fromisoformat(value['replaced_at_utc'])) < _now(datetime.fromisoformat(entry['triggered_at_utc']))):
            raise ValueError('Archived rule trigger lacks its independent successor')
        archived[(rule, entry['trigger_event_id'])] = value
    for (rule, start), value in archived.items():
        seen = {start}; cursor = value['successor_event_id']
        while (rule, cursor) in archived:
            if cursor in seen: raise ValueError('Archived rule trigger cycle')
            seen.add(cursor); cursor = archived[(rule, cursor)]['successor_event_id']
        # A later normally completed pair may have rotated without this
        # archive. Its immutable successor evidence still proves the boundary.
        if cursor == start: raise ValueError('Archived rule trigger cycle')


def active_probe(store, marker):
    """Return a still-supported reservation without changing its spent budget."""
    if not isinstance(marker, dict): return None
    entry = _state(store)['rules'].get(marker.get('rule_id'))
    if not entry or marker.get('trigger_event_id', entry['trigger_event_id']) != entry['trigger_event_id']:
        return None
    probe = next((p for p in entry['probes'] if p['probe_id'] == marker.get('probe_id')
                  and p['context_id'] == marker.get('context_id')), None)
    from .content_history import contribution_valid
    if not probe or _retired_probe(store, probe) or not contribution_valid(store._db, entry['trigger_event_id'], marker['rule_id'], 'application'):
        return None
    return marker


def record_rule_attempt(store, event_id, *, rule_id, context_id, outcome, source,
                        script, now=None, assisted=False, probe_id=None, auxiliary_weight=.10):
    """Record one explicit rule attribution atomically with its actual answer.

    ``unresolved`` means a combination error whose cause is not established: it
    requests a small transfer check, never marks the common rule wrong. Positive
    or negative observations remain auxiliary even when a probe was unassisted.
    """
    _transaction(store)
    if (rule_id not in READING_RULE_IDS or source not in {"kana", "word"}
            or script not in {"hiragana", "katakana"}
            or outcome not in {"correct", "incorrect", "unresolved", "revealed", "cancelled"}
            or type(assisted) is not bool):
        raise ValueError("Invalid reading-rule attribution")
    now, context_id = _now(now), _context(context_id)
    eid = _event_id(event_id, rule_id)
    old = store._db.execute("SELECT * FROM learning_evidence WHERE event_id=?", (eid,)).fetchone()
    if old:
        return {"event_id": eid, "rule_id": rule_id, "already_recorded": True,
                "eligible": bool(old["eligible"]), "outcome": old["outcome"]}
    state = _state(store)
    entry = state["rules"].get(rule_id)
    probe = None
    if probe_id is not None:
        probe = next((p for p in (entry or {}).get("probes", []) if p["probe_id"] == probe_id), None)
        if (probe is None or probe["context_id"] != context_id or probe["source"] != source
                or probe["script"] != script or now < datetime.fromisoformat(probe["reserved_at_utc"])):
            raise ValueError("Rule answer does not match its reserved probe")
        if probe["status"] == "completed":
            return {"rule_id": rule_id, "probe_id": probe_id, "already_recorded": True,
                    "eligible": False, "outcome": probe["outcome"]}
        # A valid independent answer still counts in its own right. A
        # corrected trigger no longer labels that answer as its transfer test.
        if not active_probe(store, {'rule_id': rule_id, 'probe_id': probe_id, 'context_id': context_id}):
            probe, probe_id = None, None
    conditions = _conditions(context_id)
    condition_key = sha256(_dump(conditions).encode()).hexdigest()[:24]
    recent = store._db.execute("""SELECT 1 FROM learning_evidence WHERE target_id=?
        AND ability='application' AND kind=? AND condition_key=? AND eligible=1 AND retracted=0
        AND julianday(occurred_at_utc)>julianday(?) AND julianday(occurred_at_utc)<=julianday(?) LIMIT 1""",
        (rule_id, KIND, condition_key, (now-timedelta(seconds=EVIDENCE_COOLDOWN_SECONDS)).isoformat(), now.isoformat())).fetchone()
    trusted = not recent and not assisted
    available = store.is_content_available(rule_id, now)
    established = None
    if store._db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_unit'").fetchone():
        established = store._db.execute("""SELECT 1 FROM learning_unit u JOIN learning_content c USING(content_id)
            WHERE u.content_id=? AND u.ability='application' AND u.instruction_ready=1
            AND u.suspended=0 AND u.review_count>0 AND c.stage='done'""", (rule_id,)).fetchone()
    from .learning_evidence import LearningEvidenceStore
    record = LearningEvidenceStore(store).record_attempt(eid,
        {"id": eid, "target_id": rule_id, "ability": "application", "kind": KIND,
         "revision": "kana-rule-v1", "prompt": context_id, "answer": context_id,
         "learning_conditions": conditions, "learning_condition_source": "kana-rule-v1",
         "selection_reason": "transfer_probe" if probe_id else "contextual_rule"},
        {"outcome": outcome, "correct": outcome == "correct", "assisted": assisted},
        source=source, role="auxiliary", now=now, trusted=bool(trusted),
        adjust=bool(trusted and available and established), opportunity_id=event_id,
        auxiliary_weight=auxiliary_weight)
    changed = False
    if probe:
        probe.update(status="completed", completed_at_utc=now.isoformat(), outcome=outcome)
        entry["not_before_utc"] = (now + timedelta(seconds=COOLDOWN_SECONDS)).isoformat()
        changed = True
    elif outcome in {"incorrect", "unresolved"} and not assisted and not recent:
        # More failures cannot replenish a running pair or erase a reservation.
        from .content_history import contribution_valid
        invalid_trigger = entry is not None and not contribution_valid(store._db, entry['trigger_event_id'], rule_id, 'application')
        finished = invalid_trigger or entry is None or _finished_pair(store, entry)
        cooled = invalid_trigger or entry is None or now >= datetime.fromisoformat(entry["triggered_at_utc"]) + timedelta(hours=REOPEN_HOURS)
        if finished and cooled:
            if entry and (invalid_trigger or any(_retired_probe(store, p) for p in entry['probes'])):
                _archive_trigger(store, rule_id, entry, eid, now)
            state["rules"][rule_id] = {"trigger_event_id": eid, "trigger_context_id": context_id,
                "triggered_at_utc": now.isoformat(), "not_before_utc": (now + timedelta(seconds=COOLDOWN_SECONDS)).isoformat(),
                "probes": []}
            changed = True
    if changed:
        _save(store, state)
    return {"event_id": eid, "rule_id": rule_id, "eligible": bool(record["eligible"]),
            "outcome": outcome, "probe_id": probe_id, "cooldown": bool(recent)}


def _familiar(store, content_id, script, now, kind=None):
    if not store.is_content_available(content_id, now):
        return False
    cards = [store.get_scheduling_card(content_id, kind, script)] if kind else store.scheduling_cards()
    for card in cards:
        if (card is None or card["content_id"] != content_id or card["script"] != script
                or card["suspended"] or card["review_count"] <= 0):
            continue
        if kind is not None and card["skill"] != kind:
            continue
        if kind is not None:
            latest = store._db.execute("""SELECT e.event_id,e.rating,e.assisted,e.occurred_at_utc FROM review_event e
                JOIN study_card c USING(card_id) WHERE c.content_id=? AND c.script=?
                AND COALESCE(json_extract(e.review_log_json,'$.ability_route.skill'),c.skill)=?
                ORDER BY julianday(e.occurred_at_utc) DESC,e.rowid DESC LIMIT 1""", (content_id, script, kind)).fetchone()
        else:
            latest = store._db.execute("""SELECT event_id,rating,assisted,occurred_at_utc FROM review_event
                WHERE card_id=? ORDER BY julianday(occurred_at_utc) DESC,rowid DESC LIMIT 1""", (card["card_id"],)).fetchone()
        from .content_history import contribution_valid
        if (latest and contribution_valid(store._db, latest['event_id'], content_id)
                and not latest["assisted"] and latest["rating"] > 1 and datetime.fromisoformat(latest["occurred_at_utc"]) <= now):
            return True
    return False


def next_rule_probe(store, candidates, *, enabled_kinds, now=None, reserve=False):
    """Choose at most one already-qualified, familiar transfer context.

    Callers apply their existing prerequisites, adaptive stage and pending-queue
    filter before supplying candidates. ``reserve=True`` joins the queue append
    transaction; a rollback releases the reservation too. Disabled reservations
    are preserved and are not returned here, so they cannot monopolize dispatch.
    """
    now = _now(now)
    if reserve:
        _transaction(store)
    if not isinstance(candidates, (list, tuple)) or len(candidates) > 128:
        raise ValueError("Rule probe candidates must be bounded")
    enabled = set(enabled_kinds)
    state = _state(store)
    for rule in READING_RULE_IDS:
        entry = state["rules"].get(rule)
        from .content_history import contribution_valid
        if entry and not contribution_valid(store._db, entry['trigger_event_id'], rule, 'application'):
            # Keep the original reservation and exposure history. A withdrawn
            # trigger cannot generate another transfer probe or refill a pair.
            continue
        if (entry is None or len(entry["probes"]) >= PROBE_LIMIT
                or now < datetime.fromisoformat(entry["not_before_utc"])
                or not store.is_content_available(rule, now)):
            continue
        used = {entry["trigger_context_id"], *(p["context_id"] for p in entry["probes"])}
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("rule_id") != rule:
                continue
            cid, script, kind = (candidate.get(k) for k in ("content_id", "script", "kind"))
            source = candidate.get("source")
            if (not isinstance(cid, str) or script not in {"hiragana", "katakana"}
                    or kind not in enabled or source not in {"kana", "word"}):
                continue
            context = _context(candidate.get("context_id"))
            if context in used:
                continue
            if rule == SMALL_KANA_RULE and small_kana_context(context) != context:
                # A word ID, script swap or second word containing the same
                # contraction is not a distinct small-kana transfer probe.
                continue
            # A regular question can already have supplied this transfer
            # observation. Do not ask it again merely because it wasn't tagged
            # as our probe. Assisted/unresolved/retracted observations remain
            # exposure, and a future clock record is not evidence available now.
            condition_key = sha256(_dump(_conditions(context)).encode()).hexdigest()[:24]
            tested = store._db.execute("""SELECT 1 FROM learning_evidence WHERE target_id=?
                AND ability='application' AND kind=? AND condition_key=? AND eligible=1 AND retracted=0
                AND julianday(occurred_at_utc)>=julianday(?)
                AND julianday(occurred_at_utc)<=julianday(?) LIMIT 1""",
                (rule, KIND, condition_key, entry["triggered_at_utc"], now.isoformat())).fetchone()
            if tested:
                continue
            if source == "kana":
                if rule != SMALL_KANA_RULE or not _familiar(store, cid, script, now, kind):
                    continue
            else:
                needed = candidate.get("required_kana_ids")
                if (not isinstance(needed, (list, tuple)) or not 0 < len(needed) <= 32
                        or any(not isinstance(value, str) or not _familiar(store, value, script, now) for value in needed)):
                    continue
            identity = "rule-probe:" + sha256(_dump([entry["trigger_event_id"], context]).encode()).hexdigest()
            proposal = {**deepcopy(candidate), "context_id": context, "probe_id": identity,
                        "trigger_event_id": entry["trigger_event_id"]}
            if reserve:
                entry["probes"].append({"probe_id": identity, "context_id": context, "content_id": cid,
                    "kind": kind, "script": script, "source": source, "status": "reserved",
                    "reserved_at_utc": now.isoformat(), "completed_at_utc": None, "outcome": None})
                _save(store, state)
            return proposal
    return None


def word_rule_observations(analysis):
    """Infer only explicit written rules, not a lexical long-vowel guess."""
    if (not isinstance(analysis, dict) or analysis.get("assisted") or analysis.get("evidence_limited")
            or analysis.get("accepted_alternative") or analysis.get("answer_direction") == "romaji"):
        return []
    expected, actual = analysis.get("expected"), analysis.get("actual")
    if not isinstance(expected, str) or not isinstance(actual, str) or not actual or analysis.get("ambiguous"):
        return []
    from .kana_reading_rules import word_reading_rules, reviewed_long_vowel_spans
    rules = word_reading_rules(expected)
    result = []
    for rule in rules:
        outcome = "correct" if analysis.get("correct") and expected == actual else None
        for error in analysis.get("errors", ()):
            left, right = error.get("expected", ""), error.get("actual", "")
            if error.get("type") not in {"size", "missing"} or rule not in explicit_reading_rules(left):
                continue
            signs = {SMALL_KANA_RULE: SMALL_COMBINING_KANA, SOKUON_RULE: {"っ", "ッ"},
                     LONG_VOWEL_RULE: {"ー"}, NASAL_RULE: {"ん", "ン"}}[rule]
            if error["type"] == "missing" and "".join(c for c in left if c not in signs) != right:
                continue
            # A size diagnostic on a whole contracted token must concern its
            # small sign; a voicing-only/base-kana substitution is not this rule.
            if error["type"] == "size" and rule not in {SMALL_KANA_RULE, SOKUON_RULE}:
                continue
            outcome = "incorrect"
        if rule == LONG_VOWEL_RULE and outcome is None:
            for span in reviewed_long_vowel_spans(expected):
                start, end = span["extension_start"], span["extension_end"]
                # Only the reviewed extension was omitted (or duplicated).
                # A different vowel or word elsewhere is not proof of failing
                # duration, even when the whole word's spelling was incorrect.
                if actual in {expected[:start] + expected[end:],
                              expected[:end] + expected[start:end] + expected[end:]}:
                    outcome = "incorrect"
                    break
        if outcome:
            if rule == SMALL_KANA_RULE and outcome == "incorrect" and small_kana_context(expected) is None:
                continue
            result.append({"rule_id": rule, "outcome": outcome})
    return result


def probe_matches_word(probe, expected):
    context = _context(probe.get('context_id'))
    if probe.get('rule_id') == SMALL_KANA_RULE:
        return context in {small_kana_context(expected[index-1:index+1]) for index, char in enumerate(expected)
                           if index and char in SMALL_COMBINING_KANA}
    return context == _context('word:' + expected)


def record_word_rule_evidence(store, event_id, analysis, *, now=None, probe=None):
    """Join an auxiliary whole-word submission; never touch a word unit."""
    _transaction(store)
    probe = active_probe(store, probe)
    if probe:
        if not probe_matches_word(probe, analysis.get('expected', '')):
            raise ValueError("Word does not contain its reserved rule context")
    if analysis.get("used_conversion"):
        if not probe:
            return []
        return [record_rule_attempt(store, event_id, rule_id=probe["rule_id"],
            context_id=probe["context_id"], outcome="cancelled", source="word",
            script=analysis["script"], now=now, assisted=False, probe_id=probe["probe_id"])]
    observations = word_rule_observations(analysis)
    result = []
    for observation in observations:
        selected = probe if probe and probe.get("rule_id") == observation["rule_id"] else None
        result.append(record_rule_attempt(store, event_id, **observation,
            context_id=selected["context_id"] if selected else
                (small_kana_context(analysis["expected"]) if observation["rule_id"] == SMALL_KANA_RULE else None)
                or "word:" + analysis["expected"],
            source="word", script=analysis["script"], now=now,
            probe_id=selected.get("probe_id") if selected else None,
            auxiliary_weight=.10 / max(1, len(observations))))
    if probe and not any(row["rule_id"] == probe.get("rule_id") for row in result):
        result.append(record_rule_attempt(store, event_id, rule_id=probe["rule_id"],
            context_id=probe["context_id"], outcome="revealed" if analysis.get("assisted") else "unresolved",
            source="word", script=analysis["script"], now=now, assisted=bool(analysis.get("assisted")),
            probe_id=probe["probe_id"]))
    return result


def teaching_overlay(item):
    """Refresh reviewed views and future templates, never saved queue snapshots.

    Keep the enrolled revision/exposure group so existing units, drafts and
    scheduling retain their identity. A separate template revision records the
    material used when a subsequent question is built.
    """
    if (item.get("id") not in TEACHING_RULE_IDS or item.get("domain") != "pronunciation"
            or item.get("ai_generated")):
        return item
    from .learning_content import get_learning_item
    current = get_learning_item(item["id"])
    if (not current or current.get("id") != item["id"] or current.get("family") != item.get("family")
            or current.get("domain") != "pronunciation" or current.get("ai_generated")):
        return item
    result = dict(item)
    for key in ("title", "reading", "meaning", "instruction", "examples", "structured_instruction", "source_ids", "source_urls", "source_url"):
        if key in current:
            result[key] = deepcopy(current[key])
        else:
            result.pop(key, None)
    result["teaching_revision"] = current.get("revision")
    if isinstance(current.get("question_templates"), dict):
        result["question_templates"] = deepcopy(current["question_templates"])
        result["question_template_revision"] = current.get("revision")
    return result
