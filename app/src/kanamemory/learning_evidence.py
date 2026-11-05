"""Auditable observations for teaching; never an alternative mastery score.

Only app-owned submission paths write evidence. Read projections stay bounded
and include successes as well as errors. Auxiliary practice may move the due
date within the formal review's shared envelope, without fabricating an FSRS
review or changing its memory parameters.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from . import app_clock
from hashlib import sha256
import json
import math
import re

TABLES = ("evidence_meta", "learning_evidence", "learning_evidence_pattern", "learning_aux_adjustment")
FORMAT = "kanamemory-learning-evidence"
VERSION = 1
AUXILIARY_STEP = .10
AUXILIARY_CAP = .25
_SECRET = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value, limit=1000):
    return _SECRET.sub("[已移除密钥]", str(value or ""))[:limit]


def _now(value=None):
    value = value or app_clock.utc_now()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Evidence clock requires a timezone")
    return value.astimezone(timezone.utc)


def tables_present(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_meta'").fetchone() is not None


def ensure_schema(db):
    """Add new tables without copying or changing existing learning records."""
    if db.in_transaction:
        raise RuntimeError("Evidence initialization requires its own transaction")
    with db:
        db.execute("BEGIN IMMEDIATE")
        for sql in (
            "CREATE TABLE IF NOT EXISTS evidence_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)",
            """CREATE TABLE IF NOT EXISTS learning_evidence (
                event_id TEXT PRIMARY KEY,target_id TEXT NOT NULL,ability TEXT NOT NULL,
                kind TEXT NOT NULL,content_revision TEXT NOT NULL,source TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('primary','auxiliary','exposure')),
                condition_key TEXT NOT NULL,conditions_json TEXT NOT NULL,
                occurred_at_utc TEXT NOT NULL,outcome TEXT NOT NULL,
                assisted INTEGER NOT NULL CHECK(assisted IN (0,1)),
                eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
                snapshot_json TEXT NOT NULL,retracted INTEGER NOT NULL DEFAULT 0 CHECK(retracted IN (0,1)),
                retraction_reason TEXT NOT NULL DEFAULT '',retracted_at_utc TEXT)""",
            """CREATE TABLE IF NOT EXISTS learning_evidence_pattern (
                pattern_id TEXT PRIMARY KEY,target_id TEXT NOT NULL,ability TEXT NOT NULL,kind TEXT NOT NULL,
                condition_key TEXT NOT NULL,conditions_json TEXT NOT NULL,
                opportunities INTEGER NOT NULL,successes INTEGER NOT NULL,errors INTEGER NOT NULL,
                concern REAL NOT NULL,last_at_utc TEXT NOT NULL,
                UNIQUE(target_id,ability,kind,condition_key))""",
            """CREATE TABLE IF NOT EXISTS learning_aux_adjustment (
                adjustment_id TEXT PRIMARY KEY,event_id TEXT NOT NULL,target_id TEXT NOT NULL,
                ability TEXT NOT NULL,card_kind TEXT NOT NULL CHECK(card_kind IN ('course','kana')),
                card_id TEXT NOT NULL,anchor_event_id TEXT NOT NULL,occurred_at_utc TEXT NOT NULL,
                result TEXT NOT NULL CHECK(result IN ('correct','incorrect')),fingerprint TEXT NOT NULL,
                base_due_at_utc TEXT NOT NULL,base_last_review_at_utc TEXT NOT NULL,
                before_due_at_utc TEXT NOT NULL,after_due_at_utc TEXT NOT NULL,weight REAL NOT NULL,
                retracted INTEGER NOT NULL DEFAULT 0 CHECK(retracted IN (0,1)),
                UNIQUE(card_kind,card_id,anchor_event_id,fingerprint))""",
            "CREATE INDEX IF NOT EXISTS evidence_target_time ON learning_evidence(target_id,occurred_at_utc DESC)",
            "CREATE INDEX IF NOT EXISTS evidence_context ON learning_evidence(target_id,ability,kind,condition_key,occurred_at_utc,event_id)",
            "CREATE INDEX IF NOT EXISTS evidence_pattern_target ON learning_evidence_pattern(target_id,concern DESC)",
            "CREATE INDEX IF NOT EXISTS evidence_adjustment_anchor ON learning_aux_adjustment(card_kind,card_id,anchor_event_id)",
            "CREATE INDEX IF NOT EXISTS evidence_adjustment_event ON learning_aux_adjustment(event_id)",
            """CREATE UNIQUE INDEX IF NOT EXISTS evidence_opportunity_unit
                ON learning_evidence(json_extract(snapshot_json,'$.opportunity.id'),target_id,ability)
                WHERE json_extract(snapshot_json,'$.opportunity.id') IS NOT NULL""",
        ):
            db.execute(sql)
        version = db.execute("SELECT value FROM evidence_meta WHERE key='schema_version'").fetchone()
        if version is not None and version[0] != str(VERSION):
            raise ValueError("Unsupported learning evidence schema")
        db.execute("INSERT OR IGNORE INTO evidence_meta VALUES ('schema_version',?)", (str(VERSION),))
        db.execute("INSERT OR IGNORE INTO evidence_meta VALUES ('revision','0')")
        from . import semantic_errors
        has_index = db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_error_pattern'").fetchone()
        semantic_errors.create_indexes(db)
        if not has_index:
            semantic_errors.rebuild(db)


def reviewed_question(question):
    """A model or imported snapshot cannot promote itself to trusted evidence."""
    if question.get("ai_generated") or question.get("guided"):
        return False
    try:
        from .learning_content import get_learning_item
        from .learning_questions import build_learning_question
        item = get_learning_item(question.get("target_id"))
        if item is None or item.get("ai_generated") or str(item["revision"]) != str(question.get("revision")):
            return False
        canonical = build_learning_question(item, question["kind"], variant=question.get("variant", 0))
        from .multi_cloze import validate_snapshot as validate_cloze_snapshot
        validate_cloze_snapshot(question)
        if isinstance(question.get("pitch_practice"), dict) and question["pitch_practice"].get("version") in (2, 3):
            from .pitch_practice import validate_snapshot
            validate_snapshot(question)
        fields = ("target_id", "revision", "kind", "ability", "prompt", "answer", "accepted_answers", "choices", "input_mode",
                  "pitch_practice", "multi_cloze")
        return (all(question.get(key) == canonical.get(key) for key in fields)
            and all(question[key] == canonical.get(key) for key in ("learning_conditions", "learning_condition_source", "learning_condition_revision") if key in question))
    except (ValueError, KeyError, TypeError):
        return False


def _condition_key(conditions, revision=""):
    # Old records keep their original identity. Context-versioned observations
    # form a separate group; no rewrite or replay of saved learner history.
    payload = [conditions, revision] if revision else conditions
    return sha256(_json(payload).encode()).hexdigest()[:24]


def _conditions(question, trusted):
    # These are curriculum-authored conditions, not all co-occurring words.
    raw = question.get("learning_conditions", []) if trusted else []
    if not isinstance(raw, list) or len(raw) > 8 or any(not isinstance(v, str) or not v or len(v) > 160 for v in raw):
        raw = []
    conditions = sorted(set(raw))
    revision = question.get("learning_condition_revision", "") if trusted else ""
    return _condition_key(conditions, revision), conditions


def _pattern_id(target_id, ability, kind, condition_key):
    return sha256(_json([target_id, ability, kind, condition_key]).encode()).hexdigest()


def _bump(db):
    db.execute("UPDATE evidence_meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")


def _update_pattern(db, row, rebuild=False):
    args = (row["target_id"], row["ability"], row["kind"], row["condition_key"])
    pid = _pattern_id(*args)
    old = None if rebuild else db.execute("SELECT * FROM learning_evidence_pattern WHERE pattern_id=?", (pid,)).fetchone()
    if old and row["occurred_at_utc"] <= old["last_at_utc"]:
        return _update_pattern(db, row, rebuild=True)
    if old:
        count, successes, errors, concern = old["opportunities"], old["successes"], old["errors"], old["concern"]
        events = [row]
    else:
        count = successes = errors = 0
        concern = 0.
        events = db.execute("""SELECT * FROM learning_evidence
            WHERE target_id=? AND ability=? AND kind=? AND condition_key=? AND eligible=1 AND retracted=0
            ORDER BY occurred_at_utc,event_id""", args).fetchall()
    last = None
    for event in events:
        if not event["eligible"] or event["retracted"]:
            continue
        count += 1
        correct = event["outcome"] == "correct"
        successes += int(correct)
        errors += int(not correct)
        # A transparent teaching salience rule, never a memory probability.
        concern = concern * .60 if correct else concern + .25 * (1 - concern)
        last = event["occurred_at_utc"]
    if last is None:
        db.execute("DELETE FROM learning_evidence_pattern WHERE pattern_id=?", (pid,))
        return
    db.execute("""INSERT INTO learning_evidence_pattern VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(pattern_id) DO UPDATE SET opportunities=excluded.opportunities,
        successes=excluded.successes,errors=excluded.errors,concern=excluded.concern,last_at_utc=excluded.last_at_utc""",
        (pid, *args, row["conditions_json"], count, successes, errors, concern, last))


def _course_anchor(db, target_id, ability):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_unit'").fetchone():
        return None
    unit = db.execute("SELECT * FROM learning_unit WHERE content_id=? AND ability=? AND suspended=0", (target_id, ability)).fetchone()
    if unit is None or not unit["review_count"] or not unit["scheduler_state_json"]:
        return None
    event = db.execute("""SELECT * FROM learning_event WHERE unit_id=? AND role='primary'
        ORDER BY occurred_at_utc DESC,rowid DESC LIMIT 1""", (unit["unit_id"],)).fetchone()
    if event is None:
        return None
    return unit, event


def bounded_due(base_due, last_review, before_due, outcome, now, weight=AUXILIARY_STEP):
    if outcome not in {"correct", "incorrect"}:
        raise ValueError("Invalid auxiliary outcome")
    if base_due <= last_review or now < last_review or before_due <= now:
        return None
    interval = base_due - last_review
    lower, upper = base_due - interval * AUXILIARY_CAP, base_due + interval * AUXILIARY_CAP
    if not lower <= before_due <= upper:
        return None
    delta = interval * min(AUXILIARY_CAP, max(0., weight))
    candidate = before_due + delta if outcome == "correct" else before_due - delta
    candidate = max(now, min(upper, max(lower, candidate)))
    return candidate if candidate != before_due else None


def remaining_budget(db, card_kind, card_id, anchor_event_id, base_due, last_review):
    """Absolute movement across auxiliary sources, not just the net offset.

    Legacy word audits remain authoritative without rewriting their dates.
    Any older effective adjustment conservatively exhausts this anchor.
    """
    seconds = (base_due - last_review).total_seconds()
    if seconds <= 0:
        return 0.
    used = sum(abs((datetime.fromisoformat(row[1]) - datetime.fromisoformat(row[0])).total_seconds()) / seconds
        for row in db.execute("""SELECT before_due_at_utc,after_due_at_utc FROM learning_aux_adjustment
            WHERE card_kind=? AND card_id=? AND anchor_event_id=? AND retracted=0""",
            (card_kind, card_id, anchor_event_id)))
    if card_kind == "kana":
        legacy = db.execute("""SELECT 1 FROM word_spelling_attempt w,json_each(w.evidence_json,'$.schedule_adjustments') a
            WHERE json_extract(a.value,'$.card_id')=? AND json_extract(a.value,'$.anchor_event_id')=?
            AND json_extract(a.value,'$.evidence_event_id') IS NULL LIMIT 1""", (card_id, anchor_event_id)).fetchone()
        if legacy:
            return 0.
    return max(0., AUXILIARY_CAP - used)


def record_adjustment(db, *, event_id, target_id, ability, card_kind, card_id, anchor_event_id,
                      now, outcome, fingerprint, base_due, last_review, before_due, after_due, weight):
    """Write the shared audit in the caller's transaction; no scheduler writes."""
    if not db.in_transaction:
        raise RuntimeError("Auxiliary adjustment requires an existing transaction")
    event = db.execute("SELECT * FROM learning_evidence WHERE event_id=?", (event_id,)).fetchone()
    from .assessment_opportunity import auxiliary_weight
    if (event is None or event["target_id"] != target_id or event["ability"] != ability
            or not event["eligible"] or event["role"] != "auxiliary" or event["retracted"]
            or event["outcome"] != outcome or type(weight) not in (int, float)
            or not 0 < weight <= auxiliary_weight(event, AUXILIARY_CAP) + 1e-9):
        raise ValueError("Auxiliary adjustment requires its qualified opportunity allocation")
    # Changing the formal anchor cannot reuse an already consumed answer.
    if db.execute("SELECT 1 FROM learning_aux_adjustment WHERE event_id=?", (event_id,)).fetchone():
        return False
    aid = sha256(_json([card_kind, card_id, anchor_event_id, fingerprint]).encode()).hexdigest()
    existing = db.execute("SELECT 1 FROM learning_aux_adjustment WHERE adjustment_id=?", (aid,)).fetchone()
    if existing:
        return False
    db.execute("INSERT INTO learning_aux_adjustment VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
        (aid, event_id, target_id, ability, card_kind, card_id, anchor_event_id, now.isoformat(), outcome,
         fingerprint, base_due.isoformat(), last_review.isoformat(), before_due.isoformat(), after_due.isoformat(), weight))
    return True


def _apply_course_adjustment(db, event, question, now):
    pair = _course_anchor(db, event["target_id"], event["ability"])
    if pair is None:
        return
    unit, anchor = pair
    from .domain import ALGORITHM_VERSION
    base, state = json.loads(anchor["state_after_json"]), json.loads(unit["scheduler_state_json"])
    if (unit["scheduler_version"] != ALGORITHM_VERSION or base.get("state") != 2 or state.get("state") != 2
            or not base.get("last_review") or anchor["occurred_at_utc"] > event["occurred_at_utc"]
            or {k: v for k, v in state.items() if k != "due"} != {k: v for k, v in base.items() if k != "due"}
            or state["due"] != unit["due_at_utc"]):
        return
    base_due, last, before = (datetime.fromisoformat(value) for value in (base["due"], base["last_review"], state["due"]))
    from .assessment_opportunity import auxiliary_weight
    weight = min(auxiliary_weight(event), remaining_budget(db, "course", unit["unit_id"], anchor["event_id"], base_due, last))
    after = bounded_due(base_due, last, before, event["outcome"], now, weight)
    if after is None:
        return
    from .tutor_store import question_fingerprint
    fingerprint = question_fingerprint(question)
    if not record_adjustment(db, event_id=event["event_id"], target_id=event["target_id"], ability=event["ability"],
            card_kind="course", card_id=unit["unit_id"], anchor_event_id=anchor["event_id"], now=now,
            outcome=event["outcome"], fingerprint=fingerprint, base_due=base_due, last_review=last,
            before_due=before, after_due=after, weight=weight):
        return
    state["due"] = after.isoformat()
    # Keep an earlier independent free-recall probe; auxiliary success must not postpone it.
    db.execute("UPDATE learning_unit SET scheduler_state_json=?,due_at_utc=? WHERE unit_id=?",
               (_json(state), after.isoformat(), unit["unit_id"]))


class LearningEvidenceStore:
    def __init__(self, store):
        self.store = store
        self._db = store._db

    def record_attempt(self, event_id, question, result, *, source, role="auxiliary", now=None,
                       trusted=False, adjust=False, opportunity_id=None, budget_scope="shared",
                       auxiliary_weight=AUXILIARY_STEP, part_ids=("answer",), episode_id=""):
        """Called only by trusted local submit paths, inside their transaction."""
        db = self._db
        if not db.in_transaction or not tables_present(db):
            raise RuntimeError("Evidence writes require initialized tables and a transaction")
        if not isinstance(event_id, str) or not event_id or len(event_id) > 256:
            raise ValueError("Invalid evidence identity")
        if source not in {"course", "tutor", "kana", "word", "ai", "diagnostic"} or role not in {"primary", "auxiliary", "exposure"}:
            raise ValueError("Invalid evidence source")
        if not isinstance(episode_id, str) or len(episode_id) > 256:
            raise ValueError('Invalid evidence episode identity')
        existing = db.execute("SELECT * FROM learning_evidence WHERE event_id=?", (event_id,)).fetchone()
        if existing:
            return dict(existing)
        now = _now(now)
        target, ability, kind = (question.get(k) for k in ("target_id", "ability", "kind"))
        if any(not isinstance(v, str) or not v or len(v) > 256 for v in (target, ability, kind)):
            raise ValueError("Invalid evidence target")
        from . import assessment_opportunity as opportunities
        opportunity_id = opportunity_id or event_id
        prior = opportunities.existing(db, opportunity_id, target, ability)
        if prior is not None:
            return dict(prior)
        requested_role = role
        outcome = result.get("outcome", result.get("status", "unresolved"))
        assisted = bool(result.get("assisted") or result.get("revealed") or question.get("guided"))
        eligible = bool(role != "exposure" and trusted and not assisted and outcome in {"correct", "incorrect"}
                        and type(result.get("correct")) is bool and result["correct"] == (outcome == "correct"))
        if not eligible:
            role = "exposure"
        weight = opportunities.allocation(db, opportunity_id, role=role, eligible=eligible, source=source,
            budget_scope=budget_scope, requested_weight=auxiliary_weight)
        if trusted and source in {"course", "tutor"}:
            from .learning_conditions import for_question
            verified = for_question(question)
            if "learning_conditions" not in question:
                question = {**{key: value for key, value in question.items()
                              if not key.startswith("learning_condition")}, **verified}
            elif question.get("learning_condition_revision") and any(
                    question.get(field) != verified.get(field) for field in
                    ("learning_conditions", "learning_condition_source", "learning_condition_revision")):
                # An old/mutated snapshot may remain an ordinary attempt, but
                # cannot assert the current version of its surrounding items.
                question = {key: value for key, value in question.items() if key != "learning_condition_revision"}
        elif "learning_condition_revision" in question:
            question = {key: value for key, value in question.items() if key != "learning_condition_revision"}
        key, conditions = _conditions(question, trusted)
        snapshot = {"question_id": _text(question.get("id", question.get("question_id")), 256),
            "prompt": _text(question.get("prompt")), "answer": _text(question.get("answer")),
            "answered_text": _text(result.get("answered_text")),
            "error_category": _text(result.get("error_category"), 160),
            "trusted": bool(trusted), "selection_reason": _text(question.get("selection_reason"), 160),
            "condition_source": _text(question.get("learning_condition_source"), 256)}
        if trusted and question.get("learning_condition_revision"):
            snapshot["condition_revision"] = question["learning_condition_revision"]
        if episode_id:
            snapshot['episode_id'] = source + ':' + episode_id
        # Preserve the actual immutable assessment for later content correction.
        # Oversized tasks retain their existing durable queue provenance instead.
        frozen_question = json.loads(_json(question))
        if len(_json(frozen_question).encode('utf-8')) <= 64 * 1024:
            snapshot['assessment_question'] = frozen_question
        snapshot["opportunity"] = opportunities.metadata(opportunity_id, role=requested_role,
            budget_scope=budget_scope, weight=weight, part_ids=part_ids)
        if trusted and source in {'course', 'tutor'}:
            from . import semantic_errors
            semantic = semantic_errors.snapshot(question, {**result, 'assisted': assisted})
            if semantic:
                snapshot['semantic_errors'] = semantic
        db.execute("INSERT INTO learning_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'',NULL)",
            (event_id, target, ability, kind, _text(question.get("revision", "1"), 160), source, role,
             key, _json(conditions), now.isoformat(), _text(outcome, 80), int(assisted), int(eligible), _json(snapshot)))
        row = db.execute("SELECT * FROM learning_evidence WHERE event_id=?", (event_id,)).fetchone()
        if eligible:
            _update_pattern(db, row)
            if adjust and role == "auxiliary":
                _apply_course_adjustment(db, row, question, now)
        if 'semantic_errors' in snapshot:
            from . import semantic_errors
            latest = db.execute('SELECT MAX(occurred_at_utc) FROM learning_evidence WHERE target_id=? AND event_id<>?', (target, event_id)).fetchone()[0]
            if latest and now.isoformat() <= latest:
                semantic_errors.rebuild(db, target)
            else:
                semantic_errors.index_event(db, row)
        _bump(db)
        return dict(row)

    def context(self, target_ids, now=None, limit=24):
        result = {"format": FORMAT, "version": VERSION, "revision": 0, "targets": []}
        db = self._db
        if not tables_present(db):
            return result
        now = _now(now)
        limit = min(24, max(0, int(limit)))
        ids = list(dict.fromkeys(v for v in target_ids if isinstance(v, str)))[:limit]
        result["revision"] = int(db.execute("SELECT value FROM evidence_meta WHERE key='revision'").fetchone()[0])
        for target in ids:
            totals = db.execute("""SELECT COALESCE(SUM(opportunities),0),COALESCE(SUM(successes),0),
                COALESCE(SUM(errors),0) FROM learning_evidence_pattern WHERE target_id=?""", (target,)).fetchone()
            rows = db.execute("""SELECT * FROM learning_evidence_pattern WHERE target_id=?
                ORDER BY concern DESC,last_at_utc DESC,pattern_id LIMIT 4""", (target,)).fetchall()
            if not rows:
                continue
            recent = db.execute("""SELECT * FROM learning_evidence_pattern WHERE target_id=?
                ORDER BY last_at_utc DESC,pattern_id LIMIT 4""", (target,)).fetchall()
            # Old-version errors must not consume every bounded slot. Retain
            # the latest observation (including counterevidence), then rank a
            # small union by current decayed salience, never hydrate all history.
            pool = {row["pattern_id"]: row for row in [*rows, *recent]}
            def salience(row):
                age = max(0, (now - datetime.fromisoformat(row["last_at_utc"])).total_seconds() / 86400)
                return -row["concern"] * math.exp(-age / 90), row["pattern_id"]
            latest = recent[0]
            rows = [latest, *sorted((row for pid, row in pool.items() if pid != latest["pattern_id"]), key=salience)[:3]]
            patterns = []
            for row in rows:
                age = max(0, (now - datetime.fromisoformat(row["last_at_utc"])).total_seconds() / 86400)
                concern = row["concern"] * math.exp(-age / 90)
                args = (target, row["ability"], row["kind"], row["condition_key"])
                versions = db.execute("""SELECT MIN(content_revision),MAX(content_revision),
                    MIN(COALESCE(json_extract(snapshot_json,'$.condition_revision'),''))
                    FROM learning_evidence WHERE target_id=? AND ability=? AND kind=?
                    AND condition_key=? AND retracted=0 AND eligible=1""", args).fetchone()
                revision = versions[0] if versions[0] == versions[1] else ""
                examples = []
                # Latest state is mandatory; recent success is retrieved even if old errors are vivid.
                for event in db.execute("""SELECT * FROM learning_evidence WHERE target_id=? AND ability=? AND kind=?
                        AND condition_key=? AND retracted=0 AND eligible=1 ORDER BY occurred_at_utc DESC,event_id DESC LIMIT 3""", args):
                    snap = json.loads(event["snapshot_json"])
                    examples.append({"event_id": event["event_id"], "outcome": event["outcome"],
                        "assisted": bool(event["assisted"]), "source": event["source"],
                        "occurred_at_utc": event["occurred_at_utc"], "answered_text": snap["answered_text"], "answer": snap["answer"],
                        "condition_source": snap.get("condition_source", ""),
                        "prompt": snap.get("prompt", "")[:240], "error_category": snap.get("error_category", "")[:160]})
                patterns.append({"condition_key": row["condition_key"], "ability": row["ability"], "kind": row["kind"],
                    "content_revision": revision,
                    "condition_revision": versions[2],
                    "conditions": json.loads(row["conditions_json"]), "opportunities": row["opportunities"],
                    "successes": row["successes"], "errors": row["errors"], "concern": round(concern, 6),
                    "last_at_utc": row["last_at_utc"], "status": "stale" if age > 90 else "tentative" if row["opportunities"] < 3
                    else "improving" if row["concern"] < .15 else "active", "examples": examples})
            result["targets"].append({"target_id": target, "opportunities": totals[0], "successes": totals[1], "errors": totals[2],
                "concern": max((p["concern"] for p in patterns), default=0), "patterns": patterns})
            from . import semantic_errors
            semantic = semantic_errors.context(db, target, now)
            if semantic:
                result['targets'][-1]['semantic_patterns'] = semantic
                semantic_abilities = {p['ability'] for p in semantic}
                result['targets'][-1]['concern'] = max((p['concern'] for p in
                    [*semantic, *(p for p in patterns if p['ability'] not in semantic_abilities)]), default=0.)
        return result

    def retract(self, event_id, reason, now=None):
        """Retract an attribution, replay its aggregate and current-anchor effects."""
        db = self._db
        if db.in_transaction:
            raise RuntimeError("Evidence retraction requires its own transaction")
        with db:
            db.execute("BEGIN IMMEDIATE")
            return self.retract_in_transaction(event_id, reason, now=now)

    def retract_in_transaction(self, event_id, reason, now=None):
        """Join content adoption's transaction; raw answers and allocations stay.

        The public method retains its own-transaction contract. This internal
        entry lets heads, adjudications, aggregates and due projections commit
        together, including rollback on a later failure.
        """
        db = self._db
        if not db.in_transaction:
            raise RuntimeError("Evidence retraction requires an active transaction")
        now = _now(now)
        reason = _text(reason, 500).strip()
        if not reason:
            raise ValueError("Retraction requires a reason")
        row = db.execute("SELECT * FROM learning_evidence WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(event_id)
        if row["retracted"]:
            return False
        db.execute("UPDATE learning_evidence SET retracted=1,retraction_reason=?,retracted_at_utc=? WHERE event_id=?",
                   (reason, now.isoformat(), event_id))
        affected = db.execute("SELECT * FROM learning_aux_adjustment WHERE event_id=? AND retracted=0", (event_id,)).fetchall()
        db.execute("UPDATE learning_aux_adjustment SET retracted=1 WHERE event_id=?", (event_id,))
        for adjustment in affected:
            _replay_adjustments(db, adjustment, now, event_id)
        _update_pattern(db, row, rebuild=True)
        from . import semantic_errors
        semantic_errors.rebuild(db, row['target_id'])
        _bump(db)
        return True


def _replay_adjustments(db, adjustment, now, just_retracted):
    """Never subtract an old delta from a card that has a newer formal review."""
    if adjustment["card_kind"] == "course":
        pair = _course_anchor(db, adjustment["target_id"], adjustment["ability"])
        if pair is None:
            return
        unit, anchor = pair
        table, identity = "learning_unit", "unit_id"
    else:
        unit = db.execute("SELECT * FROM study_card WHERE card_id=?", (adjustment["card_id"],)).fetchone()
        anchor = db.execute("SELECT * FROM review_event WHERE card_id=? ORDER BY occurred_at_utc DESC,rowid DESC LIMIT 1",
                            (adjustment["card_id"],)).fetchone()
        table, identity = "study_card", "card_id"
    if unit is None or anchor is None or anchor["event_id"] != adjustment["anchor_event_id"]:
        return
    state = json.loads(unit["scheduler_state_json"])
    base = json.loads(anchor["state_after_json"])
    if {k: v for k, v in state.items() if k != "due"} != {k: v for k, v in base.items() if k != "due"}:
        return
    all_adjustments = db.execute("""SELECT * FROM learning_aux_adjustment WHERE card_kind=? AND card_id=?
        AND anchor_event_id=? ORDER BY occurred_at_utc,rowid""",
        (adjustment["card_kind"], adjustment["card_id"], adjustment["anchor_event_id"])).fetchall()
    base_due, last = datetime.fromisoformat(base["due"]), datetime.fromisoformat(base["last_review"])
    def replay(include_withdrawn=False):
        due, used = base_due, 0.
        for item in all_adjustments:
            if not item["retracted"] or include_withdrawn and item["event_id"] == just_retracted:
                candidate = bounded_due(base_due, last, due, item["result"], datetime.fromisoformat(item["occurred_at_utc"]),
                    min(item["weight"], max(0., AUXILIARY_CAP - used))) or due
                used += abs((candidate - due).total_seconds()) / (base_due - last).total_seconds()
                due = candidate
        return due
    # An external due edit is never silently overwritten by replay. After an
    # earlier retraction the current projection, rather than old audit tail,
    # is the expected state.
    if datetime.fromisoformat(state["due"]) != replay(True):
        return
    due = replay()
    # If the original item is already due, withdrawing an error must not postpone it.
    if datetime.fromisoformat(state["due"]) <= now and due > now:
        due = datetime.fromisoformat(state["due"])
    state["due"] = due.isoformat()
    db.execute(f"UPDATE {table} SET scheduler_state_json=?,due_at_utc=? WHERE {identity}=?",
               (_json(state), due.isoformat(), adjustment["card_id"]))


def auxiliary_due_matches(db, card_kind, card_id, anchor, actual_due):
    """A backup may differ from its formal log only by its audited projection."""
    if not tables_present(db):
        return False
    rows = db.execute("""SELECT a.*,e.retracted_at_utc FROM learning_aux_adjustment a
        JOIN learning_evidence e ON e.event_id=a.event_id WHERE a.card_kind=? AND a.card_id=?
        AND a.anchor_event_id=? ORDER BY a.occurred_at_utc,a.rowid""",
        (card_kind, card_id, anchor["event_id"])).fetchall()
    if not rows:
        return False
    state = json.loads(anchor["state_after_json"])
    base, last = datetime.fromisoformat(state["due"]), datetime.fromisoformat(state["last_review"])
    due, used = base, 0.
    for row in rows:
        if not row["retracted"]:
            candidate = bounded_due(base, last, due, row["result"], datetime.fromisoformat(row["occurred_at_utc"]),
                min(row["weight"], max(0., AUXILIARY_CAP - used))) or due
            used += abs((candidate - due).total_seconds()) / (base - last).total_seconds()
            due = candidate
    actual = datetime.fromisoformat(actual_due)
    if actual == due:
        return True
    # Withdrawing an error never moves an already-due card later. Retain that
    # exact historical audited timestamp, not an arbitrary earlier date.
    return actual <= due and any(row["retracted_at_utc"] and actual <= datetime.fromisoformat(row["retracted_at_utc"])
        and actual_due in {row["before_due_at_utc"], row["after_due_at_utc"]} for row in rows)


def validate_backup_rows(db, *, kana_routes=None):
    if not tables_present(db):
        return
    if db.execute("SELECT value FROM evidence_meta WHERE key='schema_version'").fetchone()[0] != str(VERSION):
        raise ValueError("Invalid evidence version")
    revision = int(db.execute("SELECT value FROM evidence_meta WHERE key='revision'").fetchone()[0])
    if revision < 0:
        raise ValueError("Invalid evidence revision")
    opportunities, weights = set(), {}
    for row in db.execute("SELECT * FROM learning_evidence"):
        _now(datetime.fromisoformat(row["occurred_at_utc"]))
        conditions = json.loads(row["conditions_json"])
        if (not isinstance(conditions, list) or len(conditions) > 8
                or any(not isinstance(v, str) or not v or len(v) > 160 for v in conditions)
                or conditions != sorted(set(conditions))):
            raise ValueError("Invalid evidence conditions")
        snapshot = json.loads(row["snapshot_json"])
        if (not isinstance(snapshot, dict) or len(row["snapshot_json"].encode()) > 128 * 1024 or _SECRET.search(row["snapshot_json"])
                or not {"question_id", "prompt", "answer", "answered_text", "error_category", "trusted", "selection_reason"} <= set(snapshot)
                or set(snapshot) - {"question_id", "prompt", "answer", "answered_text", "error_category", "trusted", "selection_reason", "condition_source", "condition_revision", "opportunity", "semantic_errors", "episode_id", "assessment_question"}
                or type(snapshot.get("trusted")) is not bool
                or any(not isinstance(v, str) or len(v) > 1000 for k, v in snapshot.items() if k not in {"trusted", "opportunity", "semantic_errors", "assessment_question"})):
            raise ValueError("Invalid evidence snapshot")
        if 'assessment_question' in snapshot:
            q = snapshot['assessment_question']
            if (not isinstance(q, dict) or len(_json(q).encode('utf-8')) > 64 * 1024
                    or any(q.get(key) != row[key] for key in ('target_id', 'ability', 'kind'))
                    or _text(q.get('revision', '1'), 160) != row['content_revision']
                    or _text(q.get('id', q.get('question_id')), 256) != snapshot['question_id']
                    or _text(q.get('answer')) != snapshot['answer'] or _text(q.get('prompt')) != snapshot['prompt']):
                raise ValueError('Invalid frozen assessment provenance')
        if 'semantic_errors' in snapshot:
            from .semantic_errors import validate_snapshot
            validate_snapshot(snapshot['semantic_errors'], row, snapshot)
        if "opportunity" in snapshot:
            from .assessment_opportunity import validate_metadata, TOTAL_AUXILIARY_WEIGHT
            op = snapshot["opportunity"]
            validate_metadata(op)
            key = (op["id"], row["target_id"], row["ability"])
            if key in opportunities or op["budget_scope"] == "word_unit" and row["source"] != "word":
                raise ValueError("Duplicate or invalid assessment opportunity")
            opportunities.add(key)
            if op["weight"] and (not row["eligible"] or row["role"] != "auxiliary"):
                raise ValueError("Unqualified opportunity weight")
            if op["budget_scope"] == "shared":
                weights[op["id"]] = weights.get(op["id"], 0.) + op["weight"]
                if weights[op["id"]] > TOTAL_AUXILIARY_WEIGHT + 1e-9:
                    raise ValueError("Opportunity auxiliary budget exceeded")
        context_revision = snapshot.get("condition_revision", "")
        if (context_revision and (not re.fullmatch(r"[0-9a-f]{64}", context_revision)
                                  or not snapshot["trusted"] or not conditions)
                or row["condition_key"] != _condition_key(conditions, context_revision)):
            raise ValueError("Invalid evidence context identity")
        if row["source"] not in {"course", "tutor", "kana", "word", "ai", "diagnostic"}:
            raise ValueError("Invalid evidence source")
        if row["eligible"] and (not snapshot["trusted"] or row["assisted"] or row["role"] == "exposure" or row["outcome"] not in {"correct", "incorrect"}):
            raise ValueError("Invalid eligible evidence")
        if row["retracted"]:
            _now(datetime.fromisoformat(row["retracted_at_utc"]))
            if not row["retraction_reason"]:
                raise ValueError("Missing evidence retraction reason")
        elif row["retracted_at_utc"] is not None or row["retraction_reason"]:
            raise ValueError("Invalid evidence retraction")
    expected_patterns = {}
    for row in db.execute("SELECT * FROM learning_evidence WHERE eligible=1 AND retracted=0 ORDER BY occurred_at_utc,event_id"):
        pid = _pattern_id(row["target_id"], row["ability"], row["kind"], row["condition_key"])
        agg = expected_patterns.setdefault(pid, [0, 0, 0, 0., None, json.loads(row["conditions_json"])])
        correct = row["outcome"] == "correct"
        agg[0] += 1
        agg[1 if correct else 2] += 1
        agg[3] = agg[3] * .6 if correct else agg[3] + .25 * (1 - agg[3])
        agg[4] = row["occurred_at_utc"]
    for row in db.execute("SELECT * FROM learning_evidence_pattern"):
        expected = expected_patterns.pop(row["pattern_id"], None)
        if (expected is None or row["pattern_id"] != _pattern_id(row["target_id"], row["ability"], row["kind"], row["condition_key"])
                or [row["opportunities"], row["successes"], row["errors"]] != expected[:3]
                or not math.isclose(row["concern"], expected[3], abs_tol=1e-12) or row["last_at_utc"] != expected[4]
                or json.loads(row["conditions_json"]) != expected[5]):
            raise ValueError("Invalid evidence aggregate")
    if expected_patterns:
        raise ValueError("Missing evidence aggregate")
    if kana_routes is None:
        from .kana_routing import validate_database
        kana_routes = validate_database(db)
    used = {}
    for row in db.execute("SELECT * FROM learning_aux_adjustment"):
        event = db.execute("SELECT * FROM learning_evidence WHERE event_id=?", (row["event_id"],)).fetchone()
        if (event is None or not event["eligible"] or event["role"] != "auxiliary"
                or (row["target_id"], row["ability"], row["result"], row["retracted"])
                != (event["target_id"], event["ability"], event["outcome"], event["retracted"])):
            raise ValueError("Invalid auxiliary evidence reference")
        from .assessment_opportunity import auxiliary_weight
        if row["weight"] > auxiliary_weight(event, fallback=AUXILIARY_CAP) + 1e-9:
            raise ValueError("Auxiliary adjustment exceeds its opportunity allocation")
        base, last, before, after, when = (_now(datetime.fromisoformat(row[field])) for field in
            ("base_due_at_utc", "base_last_review_at_utc", "before_due_at_utc", "after_due_at_utc", "occurred_at_utc"))
        if (not 0 < row["weight"] <= AUXILIARY_CAP or row["occurred_at_utc"] != event["occurred_at_utc"]
                or bounded_due(base, last, before, row["result"], when, row["weight"]) != after):
            raise ValueError("Invalid auxiliary due adjustment")
        kind, card, anchor = row["card_kind"], row["card_id"], row["anchor_event_id"]
        table = "learning_event" if kind == "course" else "review_event"
        original = db.execute("SELECT * FROM " + table + " WHERE event_id=?", (anchor,)).fetchone()
        if kind == "course":
            owner = db.execute("SELECT content_id,ability FROM learning_unit WHERE unit_id=?", (card,)).fetchone()
            owned = (owner is not None and (owner["content_id"], owner["ability"]) == (row["target_id"], row["ability"])
                     and original is not None and original["unit_id"] == card and original["role"] == "primary")
        else:
            owner = db.execute("SELECT content_id,script,skill FROM study_card WHERE card_id=?", (card,)).fetchone()
            ability_owned = owner is not None and owner["script"] + ":" + owner["skill"] == row["ability"]
            if owner is not None and not ability_owned:
                from .kana_ability_bridge import SKILL_TO_ABILITY, ability_unit_id
                ability = SKILL_TO_ABILITY.get(owner["skill"])
                unit = kana_routes.get(ability_unit_id(owner["content_id"], owner["script"], ability))
                ability_owned = (unit is not None and unit["status"] == "active"
                    and unit["carrier_card_id"] == card and owner["script"] + ":" + ability == row["ability"])
            owned = (owner is not None and owner["content_id"] == row["target_id"] and ability_owned
                     and original is not None and original["card_id"] == card)
        if (not owned or not original["state_after_json"]
                or _now(datetime.fromisoformat(original["occurred_at_utc"])) > when):
            raise ValueError("Invalid auxiliary card or formal anchor ownership")
        if any(json.loads(original["state_after_json"]).get(field) != value for field, value in
                (("due", row["base_due_at_utc"]), ("last_review", row["base_last_review_at_utc"]))):
            raise ValueError("Invalid auxiliary formal anchor")
        if not row["retracted"]:
            key = (kind, card, anchor)
            used[key] = used.get(key, 0.) + abs((after - before).total_seconds()) / (base - last).total_seconds()
            if used[key] > AUXILIARY_CAP + 1e-9:
                raise ValueError("Auxiliary absolute movement exceeds budget")
