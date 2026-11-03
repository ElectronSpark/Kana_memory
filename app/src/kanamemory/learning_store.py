"""Additive vocabulary/grammar learning state, isolated from legacy kana queues.

The caller supplies reviewed immutable question snapshots. Teaching is exposure;
only a frozen independent answer finalized once can update its ability's FSRS.
No old card, session, word-spelling evidence or due date is migrated in place.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from uuid import uuid4

from fsrs import Card, Rating
from .learning_dispatch_types import LearningCurrent

from .domain import ALGORITHM_VERSION, _utc
from . import ability_teaching as _teaching

SCHEMA_VERSION = 1
TABLES = ("learning_meta", "learning_content", "learning_unit", "learning_question", "learning_event")
ABILITIES = {"word": {"understanding", "reading", "use", "listening"},
             "grammar": {"understanding", "application"}}
STAGES = {"intro", "guide", "bridge", "check", "done"}
ALIASES = {"meaning": "understanding", "usage": "use"}
RETRIEVAL_DELAY_SECONDS = 60
FEEDBACK_COOLDOWN_SECONDS = 60
MAX_JSON_BYTES = 2 * 1024 * 1024
FAMILIARITY_LEVELS = {"new", "some", "familiar"}


class _ExposureTimes(dict):
    """A queue-pass cache; its ordinary mapping remains backwards compatible."""
    tutor = None
    retention = None
    confirmation = None
    additional_teaching = None


def _dump(value):
    result = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(result.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("学习内容或草稿过大。")
    return result


def _load(value):
    return json.loads(value) if value is not None else None


def _identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError("Invalid learning identifier")
    return value


def _ability(value):
    return ALIASES.get(value, value)


def _unit_id(content_id, ability):
    return hashlib.sha256((content_id + "\0" + ability).encode()).hexdigest()


def _latest_due(*values):
    """Queue-only exposure delays never overwrite the actual FSRS due."""
    dates = [datetime.fromisoformat(value).astimezone(timezone.utc) for value in values if value]
    return max(dates).isoformat() if dates else None


def _ability_due(unit, question):
    """A free-retrieval probe replaces the due test, never a second card."""
    due = unit["due_at_utc"]
    probe = unit["probe_due_at_utc"]
    free = _independent_retrieval(question)
    if free and due and probe:
        return min(datetime.fromisoformat(value).astimezone(timezone.utc) for value in (due, probe)).isoformat()
    return due


def _independent_retrieval(question):
    free = question.get("input_mode", question.get("response")) in {"text", "typing", "free_text"}
    if free and "practice" in question:
        from .learning_practice_policy import practice_metadata, question_scaffold
        return practice_metadata(question) is not None and question_scaffold(question) == "retrieval"
    return free


def _paused_practice_guide(content, enabled_kinds):
    if enabled_kinds is None or content["stage"] != "guide":
        return False
    checkpoint = _load(content["checkpoint_json"])
    q = checkpoint.get("guide_question")
    return bool(isinstance(q, dict) and "practice" in q and q.get("kind") not in enabled_kinds)


def ensure_schema(db):
    """Create only new tables, once, using a transaction and a private version."""
    if db.in_transaction:
        raise RuntimeError("Cannot initialize learning tables inside a transaction")
    with db:
        db.execute("BEGIN IMMEDIATE")
        for sql in (
            "CREATE TABLE IF NOT EXISTS learning_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)",
            """CREATE TABLE IF NOT EXISTS learning_content (
                content_id TEXT PRIMARY KEY,family TEXT NOT NULL CHECK(family IN ('word','grammar')),
                revision TEXT NOT NULL,introduction_id TEXT NOT NULL UNIQUE,
                stage TEXT NOT NULL CHECK(stage IN ('intro','guide','bridge','check','done')),
                content_json TEXT NOT NULL,checkpoint_json TEXT NOT NULL,
                exposure_group TEXT NOT NULL,introduced_at_utc TEXT NOT NULL,
                guided_at_utc TEXT,retrieval_at_utc TEXT)""",
            """CREATE TABLE IF NOT EXISTS learning_unit (
                unit_id TEXT PRIMARY KEY,content_id TEXT NOT NULL REFERENCES learning_content,
                ability TEXT NOT NULL,scheduler_state_json TEXT,scheduler_version TEXT NOT NULL,
                due_at_utc TEXT,review_count INTEGER NOT NULL DEFAULT 0 CHECK(review_count>=0),
                instruction_ready INTEGER NOT NULL DEFAULT 0 CHECK(instruction_ready IN (0,1)),
                suspended INTEGER NOT NULL DEFAULT 0 CHECK(suspended IN (0,1)),
                probe_due_at_utc TEXT,last_unassisted_success_at_utc TEXT,
                UNIQUE(content_id,ability))""",
            """CREATE TABLE IF NOT EXISTS learning_question (
                question_id TEXT PRIMARY KEY,unit_id TEXT NOT NULL REFERENCES learning_unit,
                kind TEXT NOT NULL,snapshot_json TEXT NOT NULL,status TEXT NOT NULL
                CHECK(status IN ('pending','completed')),draft_json TEXT NOT NULL,
                result_json TEXT,created_at_utc TEXT NOT NULL,not_before_utc TEXT,
                exposure_group TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS learning_event (
                event_id TEXT PRIMARY KEY,question_id TEXT NOT NULL UNIQUE REFERENCES learning_question,
                unit_id TEXT NOT NULL REFERENCES learning_unit,occurred_at_utc TEXT NOT NULL,
                rating INTEGER CHECK(rating BETWEEN 1 AND 4),role TEXT NOT NULL,
                outcome TEXT NOT NULL,assisted INTEGER NOT NULL CHECK(assisted IN (0,1)),
                state_before_json TEXT,state_after_json TEXT,review_log_json TEXT,
                result_json TEXT NOT NULL)""",
            "CREATE UNIQUE INDEX IF NOT EXISTS learning_one_pending_unit ON learning_question(unit_id) WHERE status='pending'",
            "CREATE INDEX IF NOT EXISTS learning_due ON learning_unit(due_at_utc)",
            "CREATE INDEX IF NOT EXISTS learning_events_unit ON learning_event(unit_id)",
            "CREATE INDEX IF NOT EXISTS learning_events_day ON learning_event(occurred_at_utc,unit_id) WHERE role='primary'",
            """CREATE INDEX IF NOT EXISTS learning_dispatch_finalized ON learning_event(
                julianday(COALESCE(json_extract(result_json,'$.finalized_at_utc'),occurred_at_utc)))
                WHERE role='primary'""",
            """CREATE INDEX IF NOT EXISTS learning_teaching_primary_final ON learning_event(
                unit_id,julianday(COALESCE(json_extract(result_json,'$.finalized_at_utc'),occurred_at_utc)),
                julianday(occurred_at_utc)) WHERE role='primary'""",
            """CREATE INDEX IF NOT EXISTS learning_teaching_primary_occurred ON learning_event(
                unit_id,julianday(occurred_at_utc),
                julianday(COALESCE(json_extract(result_json,'$.finalized_at_utc'),occurred_at_utc))) WHERE role='primary'""",
            """CREATE INDEX IF NOT EXISTS learning_teaching_guided ON learning_content(guided_at_utc,content_id)
                WHERE guided_at_utc IS NOT NULL""",
            """CREATE INDEX IF NOT EXISTS learning_unresolved_finalized ON learning_event(
                julianday(COALESCE(json_extract(result_json,'$.finalized_at_utc'),occurred_at_utc)))
                WHERE role='unresolved'""",
            """CREATE INDEX IF NOT EXISTS learning_ready_evidence ON learning_event(unit_id)
                WHERE role='primary' AND outcome='correct' AND assisted=0 AND rating>1""",
        ):
            db.execute(sql)
        version = db.execute("SELECT value FROM learning_meta WHERE key='schema_version'").fetchone()
        if version is not None and version[0] != str(SCHEMA_VERSION):
            raise ValueError("Unsupported learning schema version")
        db.execute("INSERT OR IGNORE INTO learning_meta VALUES ('schema_version',?)", (str(SCHEMA_VERSION),))


def tables_present(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_meta'").fetchone() is not None


class LearningStore:
    def __init__(self, store):
        self.store = store
        ensure_schema(self._db)

    @property
    def _db(self):
        return self.store._db

    @property
    def queries(self):
        """Shared read contract for real stores and projected dependency views."""
        return self.store.queries

    def _transaction(self):
        if self._db.in_transaction:
            raise RuntimeError("Learning writes require their own transaction")
        self._db.execute("BEGIN IMMEDIATE")

    def enroll(self, items, now=None, *, selected_abilities=None,
               _after_enroll=None, _after_enroll_with_ids=None):
        """Append selected content; re-enrollment never resets it.

        The private hook lets the path service persist its prevalidated
        reservation setting on this same connection and transaction. It must
        not open or commit a transaction. A hook failure rolls back every new
        enrollment in this call; ordinary callers retain their prior behavior.
        """
        from .learning_dependencies import dependencies_of
        if _after_enroll is not None and not callable(_after_enroll):
            raise ValueError("Invalid enrollment transaction hook")
        if (_after_enroll_with_ids is not None and not callable(_after_enroll_with_ids)
                or _after_enroll_with_ids is not None and _after_enroll is not None):
            raise ValueError("Invalid enrollment receipt hook")
        if isinstance(items, dict):
            items = [items]
        if selected_abilities is not None and (not isinstance(selected_abilities, dict)
                or any(not isinstance(key, str) or not isinstance(value, list) or not value
                       or any(not isinstance(a, str) for a in value)
                       or len(set(value)) != len(value) for key, value in selected_abilities.items())):
            raise ValueError("Choose a nonempty ability list for each selected content")
        now = _utc(now)
        normalized = []
        for source in items:
            from .personal_content_versions import original_source
            item = original_source(self.store, source)
            dependencies_of(item)
            cid = _identifier(item.get("id", item.get("content_id")))
            family = "word" if item.get("family") == "vocab" else item.get("family")
            abilities = list(dict.fromkeys(_ability(a) for a in item.get("abilities", item.get("dimensions", ("understanding",)))))
            if family not in ABILITIES or not abilities or not set(abilities) <= ABILITIES[family]:
                raise ValueError("Invalid learning family or abilities")
            if (not isinstance(item.get("instruction"), dict) or not item["instruction"]
                    or item.get("instruction_missing") or item.get("status") == "instruction_missing"
                    or item.get("instruction_status") == "instruction_missing"):
                raise ValueError("学习材料待补全，不能开始测试。")
            revision = str(item.get("revision", "1"))
            item.update(id=cid, family=family, abilities=abilities, revision=revision)
            if selected_abilities is not None and cid in selected_abilities:
                if not set(selected_abilities[cid]) <= set(abilities):
                    raise ValueError("Selected abilities are not in this teaching snapshot")
            normalized.append((item, _dump(item)))
        if selected_abilities is not None and set(selected_abilities) - {item['id'] for item, _ in normalized}:
            raise ValueError("Selected abilities refer to content outside the admission plan")
        added = []
        with self._db:
            self._transaction()
            for item, encoded in normalized:
                cid = item["id"]
                exists = self._db.execute("SELECT 1 FROM learning_content WHERE content_id=?", (cid,)).fetchone()
                if cid.startswith('local:ai:') and (not exists or selected_abilities is not None and any(
                        self.get_unit(cid, ability) is None for ability in selected_abilities.get(cid, []))):
                    from .material_reference import admission_readiness
                    if not admission_readiness(self.store, item, base_snapshot=True)['ready']:
                        raise ValueError('请先保留材料，并核对本项教案、题目及十条关联例句。')
                if exists:
                    if selected_abilities is not None and cid in selected_abilities:
                        _teaching.append(self._db, item, selected_abilities[cid], now)
                    continue
                self._db.execute("INSERT INTO learning_content VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, item["family"], item["revision"], uuid4().hex, "intro", encoded,
                     _dump({"supports_initial_familiarity": True}), uuid4().hex,
                     now.isoformat(), None, None))
                for ability in (selected_abilities or {}).get(cid, item["abilities"]):
                    self._db.execute("""INSERT INTO learning_unit
                        (unit_id,content_id,ability,scheduler_version) VALUES (?,?,?,?)""",
                        (_unit_id(cid, ability), cid, ability, ALGORITHM_VERSION))
                added.append(cid)
            if _after_enroll is not None:
                _after_enroll(self._db)
            if _after_enroll_with_ids is not None:
                _after_enroll_with_ids(self._db, tuple(added))
        return added

    def enroll_with_prerequisites(self, items, catalogue, now=None):
        """Append a prerequisite-first plan, preserving every existing snapshot."""
        from .learning_dependencies import build_dependency_plan
        if isinstance(items, dict):
            items = [items]
        selected = list(items)
        values = list(catalogue.values() if isinstance(catalogue, dict) else catalogue)
        if len({item["id"] for item in values}) != len(values):
            raise ValueError("教学目录含重复的内容标识。")
        combined = {item["id"]: item for item in values}
        combined.update({item["id"]: item for item in selected})
        plan = build_dependency_plan([item["id"] for item in selected], combined, self)
        return {**plan, "added": self.enroll(plan["items"], now=now)}

    def set_initial_familiarity(self, content_id, levels, *, expected_introduction_id=None,
                                title_answer_exposed=False, now=None, fair_new=False, instruction_scope=None):
        """Save a first-exposure self-report, never a successful review.

        Each explicitly enrolled ability needs its own choice. Familiar content
        can be tested before teaching; a failed placement test returns to the
        teaching flow without pretending that it was a previously learned card.
        Replaying the original choice is harmless, even after real review began.
        """
        from .learning_dependencies import has_started_answer
        now = _utc(now)
        if type(title_answer_exposed) is not bool:
            raise ValueError("Title exposure must be a boolean")
        if not isinstance(levels, dict) or not levels or any(
                not isinstance(key, str) or not isinstance(value, str) or value not in FAMILIARITY_LEVELS
                for key, value in levels.items()):
            raise ValueError("Invalid initial familiarity")
        with self._db:
            self._transaction()
            row = self.instruction_row(content_id, instruction_scope)
            if row is None:
                raise KeyError(content_id)
            if expected_introduction_id is not None and expected_introduction_id != row["introduction_id"]:
                raise ValueError("The introduction is no longer current")
            abilities = set(_teaching.abilities(self._db, row))
            if set(levels) != abilities:
                raise ValueError("Choose familiarity separately for every enrolled ability")
            checkpoint = _load(row["checkpoint_json"])
            previous = checkpoint.get("initial_familiarity")
            if previous is not None:
                if previous.get("levels") != levels:
                    raise ValueError("Initial familiarity has already been saved")
                return self.instruction_item(content_id, instruction_scope)
            if row["stage"] != "intro" or checkpoint.get("guide_answer") is not None or any(self._db.execute(
                    "SELECT 1 FROM learning_event WHERE unit_id=? LIMIT 1", (_unit_id(content_id, a),)).fetchone() for a in abilities):
                raise ValueError("This content has already started learning")
            for pending in self._db.execute("""SELECT q.draft_json,q.result_json,u.ability FROM learning_question q
                    JOIN learning_unit u ON u.unit_id=q.unit_id WHERE u.content_id=? AND q.status='pending'""", (content_id,)):
                if pending['ability'] not in abilities:
                    continue
                if pending["result_json"] is not None or has_started_answer(_load(pending["draft_json"])):
                    raise ValueError("An existing answer or draft must be preserved")
            route = ("full" if "new" in levels.values() else "compact" if "some" in levels.values()
                     or checkpoint.get("instruction_exposed_at_utc")
                     or checkpoint.get("supports_initial_familiarity") is not True else "diagnostic")
            checkpoint["initial_familiarity"] = {"version": 1, "evidence": "self_report",
                "levels": dict(levels), "route": route, "selected_at_utc": now.isoformat(),
                "diagnostic_state": "active" if route == "diagnostic" else "not_requested"}
            if title_answer_exposed:
                checkpoint["initial_familiarity"]["title_answer_exposed"] = True
            _teaching.update(self._db, row, checkpoint_json=_dump(checkpoint))
            from .explicit_learning_start import claim
            claim(self._db, content_id, introduction_id=row['introduction_id'], now=now)
            if route == "diagnostic":
                # Permission to attempt an independent test is not mastery.
                if title_answer_exposed:
                    # Some reading-rule titles already state their answer.
                    # Keep the title useful and skip unnecessary teaching,
                    # but reuse the exposure/interleaving gate before testing.
                    # This timestamp is an exposure anchor, not a guided pass.
                    _teaching.update(self._db, row, stage='bridge', guided_at_utc=now.isoformat(),
                        retrieval_at_utc=(now + timedelta(seconds=RETRIEVAL_DELAY_SECONDS)).isoformat())
                else:
                    _teaching.update(self._db, row, stage='check')
                _teaching.ready_units(self._db, row)
                if fair_new:
                    from .new_dispatch import completed
                    completed(self._db, "course")
        return self.instruction_item(content_id, instruction_scope)

    def ensure_prerequisites(self, catalogue=None, now=None):
        """Append missing prerequisites of already enrolled, untouched lessons.

        All existing content/question snapshots and scheduler rows remain
        unchanged. Call on an explicit continue/add action, never on browsing.
        The caller's normal question builder can queue the returned new IDs.
        """
        from .learning_dependencies import (
            build_dependency_plan, effective_dependencies, preserves_existing_route)
        bundled = catalogue is None
        def membership_key():
            from .personal_content_versions import GENERATION
            return (self.store.get_setting(GENERATION, 0), self._db.execute("PRAGMA data_version").fetchone()[0], tuple(
                (row[0], row[1]) for row in self._db.execute(
                    "SELECT content_id,content_json FROM learning_content ORDER BY content_id")))
        # A closed dependency set stays closed across answer/checkpoint writes.
        # Compare authored snapshots as well as IDs: an import/edit with the
        # same row count must invalidate the proof. Exempt but incomplete sets
        # are never cached, so clearing a real draft can reveal missing bases.
        if bundled and getattr(self, "_closed_prerequisite_membership", None) == membership_key():
            return {"added": [], "target_ids": [], "ordered_ids": [], "unresolved": []}
        saved = self.list_items()
        candidates = [item for item in saved
                      if item["stage"] != "done" and not preserves_existing_route(self, item)
                      and item.get("checkpoint", {}).get("prerequisite_acknowledged") is not True]
        from .personal_graph import dependency_override
        for item in saved:
            revised = dependency_override(self, item)
            if revised and not revised['pending'] and not (item.get('checkpoint', {}).get('prerequisite_acknowledged') is True
                    and item.get('checkpoint', {}).get('prerequisite_version') == revised['version_id']):
                candidates.append(item)
        extra_targets = {cid for cid, value in _teaching.state_map(self._db).items()
            if any(row['stage'] != 'done' and _load(row['checkpoint_json']).get('prerequisite_acknowledged') is not True
                   for row in value['additions'].values())}
        candidates = list({item['id']: item for item in [*candidates, *(row for row in saved if row['id'] in extra_targets)]}.values())
        if not candidates:
            return {"added": [], "target_ids": [], "ordered_ids": [], "unresolved": []}
        if catalogue is None:
            from .learning_content import learning_items
            catalogue = learning_items()
        from types import SimpleNamespace
        projected = [{**row, 'depends_on': effective_dependencies(self,
            {**row, 'checkpoint': {}}, preserved=False)} if row['id'] in extra_targets else row for row in saved]
        view = SimpleNamespace(_db=self._db, queries=self.queries, list_items=lambda: projected, get_item=self.get_item)
        plan = build_dependency_plan([item["id"] for item in candidates], catalogue, view,
                                     allow_unresolved=True)
        added = self.enroll(plan["items"], now=now)
        if bundled:
            originals = {item["id"]: item for item in saved}
            originals.update({item["id"]: item for item in plan["items"] if item["id"] in added})
            # Ignore route waivers when proving closure: these may be removed
            # or a draft may be cleared without changing content_json.
            closed = all(dependency in originals for item in originals.values()
                for dependency in effective_dependencies(self, {**item, "checkpoint": {}}, preserved=False))
            if closed and not plan["unresolved"]:
                self._closed_prerequisite_membership = membership_key()
        return {"added": added,
                "target_ids": plan["target_ids"], "ordered_ids": plan["ordered_ids"],
                "unresolved": plan["unresolved"]}

    def mark_instruction_exposed(self, content_id, *, expected_introduction_id=None, now=None, instruction_scope=None):
        """Remember answer-bearing teaching once; never extend an existing wait."""
        now = _utc(now)
        with self._db:
            self._transaction()
            row = self.instruction_row(content_id, instruction_scope)
            if row is None:
                raise KeyError(content_id)
            if expected_introduction_id is not None and expected_introduction_id != row["introduction_id"]:
                raise ValueError("The introduction is no longer current")
            checkpoint = _load(row["checkpoint_json"])
            if row["stage"] in {"intro", "guide"} and "instruction_exposed_at_utc" not in checkpoint:
                from .explicit_learning_start import claim
                claim(self._db, content_id, introduction_id=row['introduction_id'], now=now)
                checkpoint["instruction_exposed_at_utc"] = now.isoformat()
                _teaching.update(self._db, row, checkpoint_json=_dump(checkpoint))
        return self.instruction_item(content_id, instruction_scope)

    @staticmethod
    def _initial_diagnostic(content, unit):
        profile = _load(content["checkpoint_json"]).get("initial_familiarity", {})
        return (profile.get("route") == "diagnostic" and profile.get("diagnostic_state") == "active"
                and unit['ability'] in profile.get('levels', {})
                and unit["review_count"] == 0 and unit["scheduler_state_json"] is None)

    def prerequisite_status(self, content_id, instruction_scope=None, *, ready_ids=None):
        from .learning_dependencies import prerequisite_status
        if not instruction_scope:
            return prerequisite_status(self, content_id, ready_ids=ready_ids)
        from .learning_dependencies import effective_dependencies, independent_ready_ids, _status
        item = self.instruction_item(content_id, instruction_scope)
        dependencies = effective_dependencies(self, item, preserved=False)
        ready_ids = independent_ready_ids(self) if ready_ids is None else ready_ids
        entries = [_status(self, cid, ready_ids) for cid in dependencies]
        missing = [entry for entry in entries if not entry['ready']]
        waived = item['checkpoint'].get('prerequisite_acknowledged') is True
        from .personal_graph import dependency_override
        revised = dependency_override(self, item)
        if revised:
            if revised['pending']:
                return {'ready': False, 'blocked': True, 'waived': False, 'grandfathered': False,
                        'dependencies': entries, 'missing': missing, 'pending_revision': revised['version_id']}
            waived = waived and item['checkpoint'].get('prerequisite_version') == revised['version_id']
        return {'ready': not missing, 'blocked': bool(missing and not waived), 'waived': waived,
                'grandfathered': False, 'dependencies': entries, 'missing': missing}

    def acknowledge_prerequisites(self, content_id, now=None, *, instruction_scope=None):
        """Explicit prior-knowledge self-report; never create a success event."""
        now = _utc(now)
        with self._db:
            self._transaction()
            row = self.instruction_row(content_id, instruction_scope)
            if row is None:
                raise KeyError(content_id)
            checkpoint = _load(row['checkpoint_json'])
            from .personal_graph import dependency_override
            revised = dependency_override(self, self.instruction_item(content_id, instruction_scope))
            if revised and revised['pending']: raise ValueError('前置关系待核，不能用熟悉程度跳过。')
            version = revised['version_id'] if revised else None
            if checkpoint.get("prerequisite_acknowledged") is not True or checkpoint.get('prerequisite_version') != version:
                checkpoint.update(prerequisite_acknowledged=True,
                    prerequisite_acknowledged_at_utc=now.isoformat(), prerequisite_evidence="self_report")
                if version: checkpoint['prerequisite_version'] = version
                else: checkpoint.pop('prerequisite_version', None)
                _teaching.update(self._db, row, checkpoint_json=_dump(checkpoint))
        return self.instruction_item(content_id, instruction_scope)

    def instruction_row(self, content_id, instruction_scope=None):
        row = self._db.execute('SELECT * FROM learning_content WHERE content_id=?', (content_id,)).fetchone()
        return _teaching.project(self._db, row, instruction_scope)

    def ability_instruction_row(self, content_id, ability):
        return self.instruction_row(content_id, _teaching.scope_for(self._db, content_id, ability))

    def ability_instruction_scope(self, content_id, ability):
        """Resolve the existing introduction scope without exposing the connection."""
        return _teaching.scope_for(self._db, content_id, ability)

    def instruction_item(self, content_id, instruction_scope=None):
        row = self.instruction_row(content_id, instruction_scope)
        if row is None:
            return None
        units = [self.get_unit(content_id, ability) for ability in _teaching.abilities(self._db, row)]
        item = self._item(row, units)
        if instruction_scope:
            item['instruction_scope'] = instruction_scope
        return item

    def get_item(self, content_id):
        row = self._db.execute("SELECT * FROM learning_content WHERE content_id=?", (content_id,)).fetchone()
        if row is None:
            return None
        units = [self.get_unit(content_id, unit["ability"]) for unit in self._db.execute(
            "SELECT ability FROM learning_unit WHERE content_id=? ORDER BY rowid", (content_id,))]
        return self._item(row, units)

    def _item(self, row, units):
        result = _load(row["content_json"])
        from .material_review import reviewed_item
        result = reviewed_item(result)
        from .kana_rule_evidence import teaching_overlay
        result = teaching_overlay(result)
        from .personal_content_versions import resolve as personal_version
        result = personal_version(self.store, result)
        result.update(content_id=row["content_id"], stage=row["stage"], introduction_id=row["introduction_id"],
                      checkpoint=_load(row["checkpoint_json"]), exposure_group=row["exposure_group"],
                      introduced_at_utc=row["introduced_at_utc"], retrieval_at_utc=row["retrieval_at_utc"])
        result["units"] = units
        # Keep the authored ability list intact for template and dependency
        # validation. Only actual units are admitted to teaching/assessment.
        result["enrolled_abilities"] = [unit["ability"] for unit in units]
        return result

    @staticmethod
    def _unit(row):
        result = dict(row)
        result["scheduler_state"] = _load(result["scheduler_state_json"])
        return result

    def get_unit(self, content_id, ability):
        row = self._db.execute("SELECT * FROM learning_unit WHERE content_id=? AND ability=?",
                               (content_id, _ability(ability))).fetchone()
        if row is None:
            return None
        result = self._unit(row)
        result["attempt_count"] = self._db.execute("SELECT COUNT(*) FROM learning_event WHERE unit_id=?", (row["unit_id"],)).fetchone()[0]
        return result

    def list_items(self):
        # Listing every enrolled lesson is on the continuation path. Count
        # attempts once, rather than scanning an ever-growing event history
        # separately for every ability of every lesson. No persistent cache:
        # imports, pauses and writes are visible on the next call.
        attempts = {row[0]: row[1] for row in self._db.execute(
            "SELECT unit_id,COUNT(*) FROM learning_event GROUP BY unit_id")}
        units = {}
        for row in self._db.execute("SELECT * FROM learning_unit ORDER BY rowid"):
            unit = self._unit(row)
            unit["attempt_count"] = attempts.get(row["unit_id"], 0)
            units.setdefault(row["content_id"], []).append(unit)
        return [self._item(row, units.get(row["content_id"], [])) for row in self._db.execute(
            "SELECT * FROM learning_content ORDER BY rowid")]

    def items_needing_questions(self):
        """Read only lessons with a missing ability snapshot, in enrollment order.

        Enrollment creates one unit for every saved ability. Existing pending
        questions already own their drafts, choices and review times, so the
        continuation planner cannot add anything for a fully queued lesson.
        Do not filter by kind, suspension, prerequisite, stage or due time here:
        those decisions still belong to the unchanged planner and dispatcher.
        """
        rows = self._db.execute("""SELECT c.content_id FROM learning_content c
            WHERE EXISTS (SELECT 1 FROM learning_unit u WHERE u.content_id=c.content_id
                AND NOT EXISTS (SELECT 1 FROM learning_question q
                    WHERE q.unit_id=u.unit_id AND q.status='pending'))
            ORDER BY c.rowid""").fetchall()
        return [self.get_item(row[0]) for row in rows]

    def set_suspended(self, content_id, ability, suspended):
        """Pause an ability without replacing a question or rescheduling it."""
        if type(suspended) is not bool:
            raise ValueError("Suspension must be a boolean")
        with self._db:
            self._transaction()
            changed = self._db.execute("UPDATE learning_unit SET suspended=? WHERE content_id=? AND ability=?",
                                       (int(suspended), content_id, _ability(ability)))
            if changed.rowcount != 1:
                raise KeyError((content_id, ability))
        return self.get_unit(content_id, ability)

    def advance_instruction(self, content_id, checkpoint, now=None, *, state=None, fair_new=False, instruction_scope=None):
        """Idempotent teaching transitions. No scheduler or review event here."""
        now = _utc(now)
        if checkpoint not in {"intro_done", "guide_correct", "guide_failed", "prerequisite", "intro", "guide", "bridge", "check"}:
            raise ValueError("Unknown instruction checkpoint")
        with self._db:
            self._transaction()
            row = self.instruction_row(content_id, instruction_scope)
            if row is None:
                raise KeyError(content_id)
            if checkpoint in {"intro_done", "guide", "guide_correct", "bridge", "check"} and self.prerequisite_status(content_id, instruction_scope)["blocked"]:
                raise ValueError("请先学习前置内容，或明确选择已有前置知识。")
            draft = _load(row["checkpoint_json"])
            from .learning_replacement import guide_write_guard
            guide_write_guard(draft, state, checkpoint)
            if state is not None:
                if not isinstance(state, dict):
                    raise ValueError("Instruction checkpoint must be an object")
                if "guide_source_item" in state:
                    raise ValueError("The guide source is owned by the learning store")
                draft.update(state)
            frozen_guide = draft.get("guide_question")
            if (isinstance(frozen_guide, dict) and "practice" in frozen_guide
                    and "guide_source_item" not in draft):
                from .material_review import reviewed_item
                from .kana_rule_evidence import teaching_overlay
                # Freeze teaching content only, before adding stage/units/due or
                # other learner projections. The trusted template registry binds
                # this copy, so a later presentation overlay cannot orphan it.
                from .personal_content_versions import resolve
                draft["guide_source_item"] = resolve(self.store, teaching_overlay(reviewed_item(_load(row["content_json"]))))
            from .pitch_practice import validate_checkpoint
            validate_checkpoint(content_id, row["revision"], draft)
            from .learning_practice_policy import validate_practice_guide
            validate_practice_guide(content_id, row["revision"], draft, item=_load(row["content_json"]))
            if checkpoint in {"intro_done", "guide"} and row["stage"] == "intro":
                _teaching.update(self._db, row, stage='guide')
            elif checkpoint in {"guide_correct", "bridge"} and row["stage"] == "guide":
                _teaching.update(self._db, row, stage='bridge', guided_at_utc=now.isoformat(),
                    retrieval_at_utc=(now+timedelta(seconds=RETRIEVAL_DELAY_SECONDS)).isoformat())
                _teaching.ready_units(self._db, row)
                if fair_new:
                    from .new_dispatch import completed
                    completed(self._db, "course")
            elif checkpoint == "check" and row["stage"] == "bridge":
                if self._retrieval_ready(row, now):
                    _teaching.update(self._db, row, stage='check')
            draft["last_checkpoint"] = checkpoint
            _teaching.update(self._db, row, checkpoint_json=_dump(draft))
        return self.instruction_item(content_id, instruction_scope)

    def _retrieval_ready(self, row, now):
        if row["retrieval_at_utc"] and datetime.fromisoformat(row["retrieval_at_utc"]) <= now:
            return True
        # An intervening independent target, rather than another tap in this
        # same lesson, provides the alternative interleaving opportunity.
        return bool(row["guided_at_utc"] and self._db.execute("""SELECT 1 FROM learning_event e
            JOIN learning_unit u ON u.unit_id=e.unit_id WHERE u.content_id<>?
            AND e.role='primary' AND e.occurred_at_utc>? LIMIT 1""",
            (row["content_id"], row["guided_at_utc"])).fetchone())

    def append_questions(self, questions, now=None):
        with self._db:
            self._transaction()
            return self._append_questions_in_transaction(questions, now=now)

    def _append_questions_in_transaction(self, questions, now=None):
        if not self._db.in_transaction:
            raise RuntimeError("Caller must own the learning transaction")
        """One active snapshot per ability; additions cannot replace a draft."""
        if isinstance(questions, dict):
            questions = [questions]
        now = _utc(now)
        added = []
        for original in questions:
            q = dict(original)
            if (q.get("ai_generated") is True or q.get("tutor_practice") is True
                    or q.get("evidence_mode") == "exposure_only"):
                raise ValueError("Training material cannot enter the independent review queue")
            cid = _identifier(q.get("target_id", q.get("content_id")))
            ability = _ability(q.get("ability", q.get("dimension")))
            kind = _identifier(q.get("kind"))
            unit = self._db.execute("SELECT * FROM learning_unit WHERE content_id=? AND ability=?", (cid, ability)).fetchone()
            if unit is None:
                raise ValueError("Question must target an explicitly enrolled ability")
            from .retention_session import reservation
            if reservation(self.store, unit["unit_id"]) is not None:
                continue
            if self._db.execute("SELECT 1 FROM learning_question WHERE unit_id=? AND status='pending'", (unit["unit_id"],)).fetchone():
                continue
            qid = _identifier(q.get("id", q.get("question_id", uuid4().hex)))
            if self._db.execute("SELECT 1 FROM learning_question WHERE question_id=?", (qid,)).fetchone():
                continue
            if not isinstance(q.get("prompt"), str) or not isinstance(q.get("answer"), str):
                raise ValueError("Invalid question text")
            if q.get("guided") or q.get("stage") in {"intro", "guide"}:
                raise ValueError("Guided questions cannot enter the independent review queue")
            q.update(id=qid, question_id=qid, target_id=cid, ability=ability)
            from .pitch_practice import validate_snapshot
            validate_snapshot(q)
            if 'content_dependencies' in q:
                from .question_dependencies import validate as validate_field_dependencies
                validate_field_dependencies(q['content_dependencies'])
            content = self._db.execute("SELECT * FROM learning_content WHERE content_id=?", (cid,)).fetchone()
            if "revision" in q and str(q["revision"]) != content["revision"]:
                raise ValueError("Question revision differs from the saved learning content")
            from .assessment_contract import for_question as assessment_contract
            from .assessment_policy import assessment_decision
            q["assessment_contract"] = assessment_contract(q)
            q["assessment"] = assessment_decision(_load(content["content_json"]), q, store=self.store)
            self._db.execute("INSERT INTO learning_question VALUES (?,?,?,?,?,?,?,?,?,?)",
                (qid, unit["unit_id"], kind, _dump(q), "pending", "{}", None, now.isoformat(),
                 self.question_not_before(unit, q), content["exposure_group"]))
            added.append(qid)
        return added

    def question_not_before(self, unit, question, *, now=None, exposure_times=None):
        """Read the same effective due/cooldown for a preview or an append."""
        latest = self._db.execute("""SELECT result_json,occurred_at_utc FROM learning_event
            WHERE unit_id=? ORDER BY rowid DESC LIMIT 1""", (unit["unit_id"],)).fetchone()
        result = _load(latest["result_json"]) if latest else {}
        cooldown = result.get("retry_not_before_utc")
        if result.get("scheduled") and result.get("finalized_at_utc"):
            # A delayed rating retains the original observation time in FSRS,
            # but returning to feedback exposes the answer again. Keep the
            # short teaching delay in the queue, never in the memory state.
            cooldown = _latest_due(cooldown, (datetime.fromisoformat(result["finalized_at_utc"])
                + timedelta(seconds=FEEDBACK_COOLDOWN_SECONDS)).isoformat())
        effective_due = _ability_due(unit, question)
        if (latest and effective_due and unit["due_at_utc"]
                and datetime.fromisoformat(effective_due) < datetime.fromisoformat(unit["due_at_utc"])):
            # A legacy choice may finish past its free-retrieval deadline.
            # Feedback exposure still needs a pause before that next probe.
            cooldown = _latest_due(cooldown, (datetime.fromisoformat(latest["occurred_at_utc"])
                + timedelta(seconds=FEEDBACK_COOLDOWN_SECONDS)).isoformat())
        return _latest_due(self.effective_question_due(unit, question, now=now,
            exposure_times=exposure_times), effective_due, cooldown)

    def content_exposure_times(self, now=None):
        """Read active feedback delays once per queue pass, without rewriting due dates."""
        now = _utc(now)
        result = _ExposureTimes()
        for row in self._db.execute("SELECT content_id,checkpoint_json FROM learning_content"):
            value = _load(row["checkpoint_json"]).get("feedback_exposed_at_utc")
            if value:
                until = _utc(datetime.fromisoformat(value)) + timedelta(seconds=FEEDBACK_COOLDOWN_SECONDS)
                if until > now:
                    result[row["content_id"]] = until.isoformat()
        from .tutor_store import exposure_times as tutor_exposure_times
        result.tutor = tutor_exposure_times(self.store, now)
        from .retention_check import exposure_times as retention_exposure_times
        result.retention = retention_exposure_times(self.store, now)
        from .retention_schedule import active_due
        result.confirmation = active_due(self.store, now)
        result.additional_teaching = _teaching.exposure_times(self._db, now)
        return result

    def effective_question_due(self, unit, question, now=None, *, exposure_times=None):
        """Keep another ability of just-seen content out of independent assessment.

        This is a short queue guard, not a new memory observation or an FSRS
        interval. Existing frozen feedback and meaningful drafts remain resumable.
        """
        unit = dict(unit)
        draft = question.get("draft")
        if draft is None:
            draft = _load(unit.get("draft_json")) or {}
        from .learning_dependencies import has_started_answer
        started = has_started_answer(draft)
        frozen = question.get("result") is not None or unit.get("result_json") is not None
        exposure = None
        if not started and not frozen:
            cid = unit["content_id"]
            if exposure_times is not None:
                exposure = exposure_times.get(cid)
            else:
                row = self._db.execute("SELECT checkpoint_json FROM learning_content WHERE content_id=?", (cid,)).fetchone()
                value = _load(row[0]).get("feedback_exposed_at_utc") if row else None
                if value:
                    exposure = (_utc(datetime.fromisoformat(value))
                                + timedelta(seconds=FEEDBACK_COOLDOWN_SECONDS)).isoformat()
        # An external explanation can expose an answer while an old draft is
        # retained. Keep that draft, but do not treat copying into it as an
        # immediate independent success. Explicitly assisted work and frozen
        # feedback can continue. This guard never changes the stored FSRS date.
        if not frozen and not any(draft.get(key) for key in ("assisted", "hint_used", "answer_revealed")):
            from .tutor_store import exposure_times as tutor_exposure_times
            tutor = getattr(exposure_times, "tutor", None)
            if tutor is None:
                tutor = tutor_exposure_times(self.store, now)
            exposure = _latest_due(exposure, tutor.get(unit["content_id"]))
            from .retention_check import exposure_times as retention_exposure_times
            retention = getattr(exposure_times, 'retention', None)
            if retention is None:
                retention = retention_exposure_times(self.store, _utc(now))
            exposure = _latest_due(exposure, retention.get((unit['content_id'], unit['ability'])))
            additional = getattr(exposure_times, 'additional_teaching', None)
            if additional is None:
                additional = _teaching.exposure_times(self._db, _utc(now))
            exposure = _latest_due(exposure, additional.get((unit['content_id'], unit['ability'])))
        scheduled = _ability_due(unit, question)
        floor = question.get("not_before_utc") or unit.get("not_before_utc")
        from .retention_schedule import active_due
        confirmation = getattr(exposure_times, 'confirmation', None)
        if confirmation is None: confirmation = active_due(self.store, _utc(now))
        earlier = confirmation.get(unit.get('unit_id'))
        if earlier and scheduled and datetime.fromisoformat(earlier) < datetime.fromisoformat(scheduled):
            if floor and datetime.fromisoformat(floor) == datetime.fromisoformat(scheduled): floor = None
            scheduled = earlier
        return _latest_due(scheduled, floor, exposure)

    def _mark_feedback_exposed(self, content_id, now):
        """Called inside the answer transaction; revisiting a queue never extends it."""
        row = self._db.execute("SELECT checkpoint_json FROM learning_content WHERE content_id=?", (content_id,)).fetchone()
        checkpoint = _load(row[0])
        checkpoint["feedback_exposed_at_utc"] = now.isoformat()
        self._db.execute("UPDATE learning_content SET checkpoint_json=? WHERE content_id=?",
                         (_dump(checkpoint), content_id))

    def get_question(self, question_id):
        row = self._db.execute("SELECT * FROM learning_question WHERE question_id=?", (question_id,)).fetchone()
        if row is None:
            return None
        result = _load(row["snapshot_json"])
        result.update(id=row["question_id"], question_id=row["question_id"], status=row["status"],
                      draft=_load(row["draft_json"]), result=_load(row["result_json"]),
                      not_before_utc=row["not_before_utc"])
        from .learning_replacement import question_receipt
        replaced = question_receipt(self.store, question_id) if row['status'] == 'completed' and row['result_json'] is None else None
        if replaced:
            result.update(status='superseded', replacement_question_id=replaced['question']['id'])
        from .material_review import reviewed_question
        result = reviewed_question(result)
        from .assessment_contract import for_question as assessment_contract
        result["assessment_contract"] = ((result["result"] or {}).get("assessment_contract")
                                         or assessment_contract(result))
        if result["result"] is None:
            from .assessment_policy import assessment_decision
            content = self._db.execute("SELECT content_json FROM learning_content WHERE content_id=?", (result["target_id"],)).fetchone()
            result["assessment"] = assessment_decision(_load(content[0]), result, store=self.store)
        return result

    def guided_question(self, item, kind, *, variant=1):
        """Resume the example actually answered, without changing its progress.

        Old introductions saved only the answer/result. Reconstruct those from
        their enrolled content, not newly overlaid templates. New introductions
        save the displayed question alongside the answer, so later revisions
        cannot pair a different prompt with the previous feedback.
        """
        from .learning_questions import build_learning_question
        checkpoint = item.get("checkpoint") or {}
        frozen = checkpoint.get("guide_question")
        if isinstance(frozen, dict) and "practice" in frozen:
            from .learning_practice_policy import validate_practice_guide
            validate_practice_guide(item["id"], item["revision"], checkpoint, item=item)
            return _load(_dump(frozen))
        from .pitch_practice import is_pitch_question, validate_checkpoint
        if is_pitch_question(frozen) and ("guide_draft" in checkpoint or checkpoint.get("guide_answer") is not None):
            validate_checkpoint(item.get("id"), item.get("revision"), checkpoint)
            return _load(_dump(frozen))
        if ((checkpoint.get("guide_answer") is not None or 'guide_draft' in checkpoint) and isinstance(frozen, dict)
                and frozen.get("target_id") == item.get("id")
                and frozen.get("revision") == item.get("revision")
                and frozen.get("guided") is True and frozen.get("evidence_mode") == "exposure_only"
                and isinstance(frozen.get("prompt"), str) and isinstance(frozen.get("answer"), str)
                and isinstance(frozen.get("choices"), list) and frozen.get("answer") in frozen["choices"]):
            return _load(_dump(frozen))
        target = item
        if checkpoint.get("guide_answer") is not None:
            row = self._db.execute("SELECT content_json FROM learning_content WHERE content_id=?",
                                   (item["id"],)).fetchone()
            if row:
                target = _load(row[0])
                # Before interactive pitch guides, pronunciation always used
                # the rule/understanding guide. An old answer without a frozen
                # question must not be paired with today's application guide.
                legacy_rules = target.get("question_templates", {}).get("pronunciation_rule", [])
                if (target.get("domain") == "pronunciation" and legacy_rules
                        and not any(is_pitch_question(raw) for raw in legacy_rules)):
                    kind = "pronunciation_rule"
        # Without a frozen question, an answered legacy guide can only be
        # reconstructed from its original material. Do not apply a newer
        # presentation correction beside an already-saved answer/result.
        question = build_learning_question(target, kind, guided=True, variant=variant,
                                          apply_review=checkpoint.get("guide_answer") is None)
        if frozen is None and checkpoint.get("guide_answer") is None:
            from .answer_input import available, system_japanese_enabled
            from .learning_questions import practice_templates
            system_input = system_japanese_enabled(self.store)
            if not available(question, system_japanese=system_input):
                templates = practice_templates(target)
                for index in range(len((templates or {}).get(kind, []))):
                    candidate = build_learning_question(target, kind, guided=True, variant=index)
                    if available(candidate, system_japanese=system_input):
                        return candidate
        return question

    def guided_sequence(self, item, kinds, *, known_background_ids=()):
        """Read or prepare a two-example introduction without changing progress."""
        from .learning_practice_policy import guided_practice_sequence, validate_practice_guide
        checkpoint = item.get("checkpoint") or {}
        if "guide_sequence" in checkpoint:
            validate_practice_guide(item["id"], item["revision"], checkpoint, item=item)
            return _load(_dump(checkpoint["guide_sequence"]))
        # Never append an extra teaching phase to an already opened legacy guide.
        if any(key in checkpoint for key in ("guide_question", "guide_answer", "guide_draft")):
            return []
        from .answer_input import system_japanese_enabled
        return guided_practice_sequence(item, kinds, known_background_ids=known_background_ids,
                                        system_japanese=system_japanese_enabled(self.store))

    def advance_guided_sequence(self, content_id, *, question_id=None, instruction_scope=None):
        """Complete one frozen exposure exactly once; never schedule a review."""
        from .learning_practice_policy import validate_practice_guide
        from .learning_questions import grade_learning_answer
        with self._db:
            self._transaction()
            row = self.instruction_row(content_id, instruction_scope)
            if row is None:
                raise KeyError(content_id)
            checkpoint = _load(row["checkpoint_json"])
            validate_practice_guide(content_id, row["revision"], checkpoint, item=_load(row["content_json"]))
            sequence = checkpoint.get("guide_sequence")
            if not sequence:
                raise ValueError("No guided practice sequence")
            if row["stage"] != "guide":
                raise ValueError("Guided practice is not active")
            question = checkpoint["guide_question"]
            if question_id is not None and question_id != question["id"]:
                if question_id in checkpoint["guide_completed_ids"]:
                    return {"finished": len(checkpoint["guide_completed_ids"]) == len(sequence),
                            "item": self.instruction_item(content_id, instruction_scope)}
                raise ValueError("This guided question is not current")
            if grade_learning_answer(question, checkpoint.get("guide_answer", "")).get("status") != "correct":
                raise ValueError("Complete this guided example first")
            index = checkpoint["guide_index"]
            ids = [value["id"] for value in sequence]
            checkpoint["guide_completed_ids"] = ids[:index + 1]
            finished = index == len(sequence) - 1
            if not finished:
                checkpoint["guide_index"] = index + 1
                checkpoint["guide_question"] = sequence[index + 1]
                checkpoint["guide_draft"] = {}
                checkpoint.pop("guide_answer", None)
                checkpoint.pop("guide_correct", None)
            _teaching.update(self._db, row, checkpoint_json=_dump(checkpoint))
        return {"finished": finished, "item": self.instruction_item(content_id, instruction_scope)}

    def save_draft(self, question_id, state):
        with self._db:
            self._transaction()
            return self._save_draft_in_transaction(question_id, state)

    def _save_draft_in_transaction(self, question_id, state, *, diagnostic_context=None):
        if not self._db.in_transaction:
            raise RuntimeError("Caller must own the learning transaction")
        if not isinstance(state, dict):
            raise ValueError("Draft must be an object")
        row = self._db.execute("SELECT * FROM learning_question WHERE question_id=?", (question_id,)).fetchone()
        if row is None:
            raise KeyError(question_id)
        from .retention_session import authorize_course
        authorize_course(self, question_id, diagnostic_context)
        if row["status"] == "pending" and row["result_json"] is None:
            old = _load(row["draft_json"])
            old.update(state)
            # Once an answer has been exposed, toggling settings or
            # saving a fresh input must not erase the assisted condition.
            for key in ("assisted", "answer_revealed", "hint_used"):
                if _load(row["draft_json"]).get(key):
                    old[key] = True
            from .pitch_practice import validate_draft
            validate_draft(_load(row["snapshot_json"]), old)
            self._db.execute("UPDATE learning_question SET draft_json=? WHERE question_id=?", (_dump(old), question_id))
        return self.get_question(question_id)

    def _pending(self, enabled_kinds=None):
        """Internal compatibility alias; external readers use the public API."""
        yield from self.iter_pending_questions(enabled_kinds)

    def iter_pending_questions(self, enabled_kinds=None):
        """Full pending rows in insertion order, excluding paused/reserved units.

        No questions are generated or decoded. The iterator belongs to this
        connection/thread and must be consumed before closing the store.
        """
        kinds = set(enabled_kinds) if enabled_kinds is not None else None
        for row in self._db.execute("""SELECT q.*,u.content_id,u.ability,u.suspended,u.instruction_ready,
                u.due_at_utc,u.probe_due_at_utc,u.review_count,u.scheduler_state_json
                FROM learning_question q JOIN learning_unit u ON u.unit_id=q.unit_id
                WHERE q.status='pending' AND NOT EXISTS (SELECT 1 FROM app_setting a
                    WHERE a.key='retention_reservation_v1:' || q.unit_id) ORDER BY q.rowid"""):
            if row["suspended"] or kinds is not None and row["kind"] not in kinds:
                continue
            yield row

    def _dispatch_pending(self, enabled_kinds=None):
        """Read small sort fields; hydrate only the selected question later.

        Preserve absent-vs-explicit-null input_mode, since the former falls
        back to response. Queue snapshots, drafts and result bytes are never
        changed by this projection; _pending stays the full-row reader.
        """
        kinds = set(enabled_kinds) if enabled_kinds is not None else None
        for row in self._db.execute("""SELECT q.question_id,q.unit_id,q.kind,q.status,
                q.draft_json,q.result_json,q.created_at_utc,q.not_before_utc,q.exposure_group,
                CASE WHEN json_type(q.snapshot_json,'$.practice') IS NOT NULL THEN
                    json_set(CASE WHEN json_type(q.snapshot_json,'$.input_mode') IS NULL
                    THEN json_object('response',json_extract(q.snapshot_json,'$.response'))
                    ELSE json_object('input_mode',json_extract(q.snapshot_json,'$.input_mode')) END,
                        '$.practice',json_extract(q.snapshot_json,'$.practice'),
                        '$.prompt',json_extract(q.snapshot_json,'$.prompt'))
                    ELSE CASE WHEN json_type(q.snapshot_json,'$.input_mode') IS NULL
                    THEN json_object('response',json_extract(q.snapshot_json,'$.response'))
                    ELSE json_object('input_mode',json_extract(q.snapshot_json,'$.input_mode')) END
                END AS snapshot_json,
                u.content_id,u.ability,u.suspended,u.instruction_ready,u.due_at_utc,u.probe_due_at_utc,
                u.review_count,u.scheduler_state_json
                FROM learning_question q JOIN learning_unit u ON u.unit_id=q.unit_id
                WHERE q.status='pending' AND NOT EXISTS (SELECT 1 FROM app_setting a
                    WHERE a.key='retention_reservation_v1:' || q.unit_id) ORDER BY q.rowid"""):
            if row['suspended'] or kinds is not None and row['kind'] not in kinds:
                continue
            yield row

    def current(self, enabled_kinds=None, now=None, *, fair_new=False, teaching_only=False,
                teaching_content_ids=None, include_unassessed=False, prefer_unassessed=False) -> LearningCurrent | None:
        """Read a tagged dispatch state without consuming or rebuilding questions.

        Normal mode returns a course head or an explicit blocking/wait state;
        None means no available work. Teaching-only probes return eligible heads
        or None (no eligible teaching head), omitting waiting/disabled states.
        include_unassessed requires teaching_only and unrestricted content IDs;
        prefer_unassessed chooses between its additional/introduction pools.
        fair_new changes dispatch priority, never saved order or due times.
        """
        from .learning_dependencies import independent_ready_ids, prerequisite_status, has_started_answer
        from .review_dispatch import review_priority, reviews_today
        now = _utc(now)
        if include_unassessed and (not teaching_only or teaching_content_ids is not None):
            raise ValueError("New-ability service requires an unrestricted teaching probe")
        review_day, daily_counts = reviews_today(self.store, now, course=True)
        waiting, blocked, dependencies = [], [], {}
        blocked_material = []
        ready_ids = independent_ready_ids(self)
        exposure_times = self.content_exposure_times(now)
        def has_text(value):
            return isinstance(value, str) and bool(value.strip())

        ability_states = _teaching.state_map(self._db)
        content_rows = {content["content_id"]: content for content in self._db.execute(
            """SELECT content_id,stage,checkpoint_json,retrieval_at_utc,guided_at_utc
               FROM learning_content""")}
        def content_row_for(row):
            cid = row['content_id']
            state = ability_states.get(cid)
            scope = row['ability'] if state and row['ability'] in state['additions'] else None
            return _teaching.project(self._db, content_rows[cid], scope, state=state)

        prior_primary = {row[0] for row in self._db.execute(
            "SELECT DISTINCT unit_id FROM learning_event WHERE role='primary'")} if include_unassessed else set()

        def unassessed(row):
            content = content_row_for(row)
            return (include_unassessed and content["stage"] == "done" and row["review_count"] == 0
                    and row["instruction_ready"] and row["unit_id"] not in prior_primary)

        def dispatch_priority(row):
            if row["result_json"] is not None:
                return (0, 0)
            draft = _load(row["draft_json"])
            # Empty text, audio/presentation marks and empty composition
            # controls are not evidence that the learner started an answer.
            if row["instruction_ready"] and has_started_answer(draft):
                return (1, 0)
            content = content_row_for(row)
            checkpoint = _load(content['checkpoint_json'])
            if content['stage'] == 'guide' and has_text(checkpoint.get('guide_answer')) and type(checkpoint.get('guide_correct')) is bool:
                return (1, 0)
            if content["stage"] in {"intro", "guide"}:
                started = (content["stage"] == "guide"
                           or bool(_load(content["checkpoint_json"]).get("instruction_exposed_at_utc")))
                return (4, -1 if started else 0) if fair_new else (5, 0)
            return review_priority(row, due=_ability_due(row, _load(row["snapshot_json"])), now=now,
                day_start=review_day, reviewed_today=daily_counts.get(row["unit_id"], 0))

        # A newly due step can interrupt untouched new lessons, but never a
        # frozen answer or real saved input. Sorting is read-only and stable;
        # question snapshots and their original insertion order stay intact.
        def service_priority(row):
            priority = dispatch_priority(row)
            if not include_unassessed or priority[0] < 4:
                return (0, priority)
            category = unassessed(row)
            return (int(category != prefer_unassessed), priority)

        service_heads = {}
        def chosen_service_head():
            preferred = "additional" if prefer_unassessed else "introduction"
            opened = service_heads.get("introduction", {}).get("new_dispatch_continue")
            selected_pool = "introduction" if opened else preferred if preferred in service_heads else next(iter(service_heads))
            return {**service_heads[selected_pool], "selected_course_pool": selected_pool,
                "service_pool_candidates": {pool: {
                    "content_id": head["content"]["id"], "question_id": head["question"]["id"],
                    "ability": head["question"]["ability"], "kind": head["question"]["kind"]}
                    for pool, head in service_heads.items()}}

        for row in sorted(self._dispatch_pending(enabled_kinds), key=service_priority):
            content_row = content_row_for(row)
            if _paused_practice_guide(content_row, enabled_kinds):
                continue
            teaching_stages = {"intro", "guide", "bridge", "check"} if teaching_content_ids is not None else {"intro", "guide"}
            if teaching_only and (content_row["stage"] not in teaching_stages and not unassessed(row)
                    or row["result_json"] is not None
                    or (teaching_content_ids is not None and row["content_id"] not in teaching_content_ids)):
                continue
            if include_unassessed and ("additional" if unassessed(row) else "introduction") in service_heads:
                continue
            scope = dict(content_row).get('instruction_scope')
            content = self.instruction_item(row["content_id"], scope)
            q = self.get_question(row["question_id"])
            if q['result'] is None and self.content_question_blocked(content, q):
                blocked_material.append(q['id'])
                continue
            result = {"content": content, "question": q, "draft": q["draft"], "result": q["result"],
                      "dispatch_priority": dispatch_priority(row)}
            dependency_key = (content['id'], scope)
            if dependency_key not in dependencies:
                dependencies[dependency_key] = self.prerequisite_status(content['id'], scope, ready_ids=ready_ids)
            dependency = dependencies[dependency_key]
            if dependency["blocked"]:
                blocked.append({**result, "dependency_status": dependency, "prerequisites": dependency["missing"]})
                continue
            if q["result"] is not None and q["result"].get("initial_diagnostic"):
                # A failed placement test now needs teaching, but its frozen
                # feedback must be saved before returning to that introduction.
                return {**result, "type": "question", "stage": "check"}
            if content["stage"] in {"intro", "guide"}:
                if teaching_only:
                    due = self.effective_question_due(row, q, now, exposure_times=exposure_times)
                    if due and datetime.fromisoformat(due) > now:
                        continue
                if fair_new:
                    result["new_dispatch_continue"] = (content["stage"] == "guide"
                        or bool(content["checkpoint"].get("instruction_exposed_at_utc")))
                head = {**result, "type": "instruction", "stage": content["stage"]}
                if not include_unassessed:
                    return head
                service_heads.setdefault("introduction", head)
                if len(service_heads) == 2:
                    return chosen_service_head()
                continue
            if content["stage"] == "bridge" and not self._retrieval_ready(content_row, now):
                waiting.append(content["retrieval_at_utc"])
                continue
            due = self.effective_question_due(row, q, now, exposure_times=exposure_times)
            if due and datetime.fromisoformat(due) > now and q["result"] is None:
                waiting.append(due)
                continue
            if unassessed(row):
                result.update(protected_unassessed_ability=True, service_reason="first_ability_check",
                              new_dispatch_continue=False)
            head = {**result, "type": "question", "stage": "check"}
            if not include_unassessed:
                return head
            service_heads.setdefault("additional", head)
            if len(service_heads) == 2:
                return chosen_service_head()
        if include_unassessed and service_heads:
            return chosen_service_head()
        if teaching_only:
            return None
        if blocked_material and not blocked and not waiting:
            return {'type': 'content_blocked', 'question_ids': blocked_material, 'counts': self.counts(enabled_kinds, now)}
        if blocked:
            return {**blocked[0], "type": "prerequisite", "counts": self.counts(enabled_kinds, now),
                    "due_at_utc": min(waiting) if waiting else None}
        if waiting:
            return {"type": "waiting", "due_at_utc": min(waiting), "counts": self.counts(enabled_kinds, now)}
        counts = self.counts(enabled_kinds, now)
        if counts.get('diagnostic'):
            owned = self._db.execute("SELECT json_extract(value_json,'$.episode_id') FROM app_setting WHERE key GLOB 'retention_reservation_v1:*' LIMIT 1").fetchone()
            return {'type': 'diagnostic', 'episode_id': owned[0], 'counts': counts}
        if counts["disabled"] or counts["unavailable_content"]:
            return {"type": "disabled", "counts": counts}
        return None

    def counts(self, enabled_kinds=None, now=None):
        from .learning_dependencies import independent_ready_ids, prerequisite_status
        from functools import lru_cache
        from types import SimpleNamespace
        # Invocation-local read projection: prerequisites use content and SQL
        # evidence, never units or per-ability attempt counts. No cross-call cache.
        @lru_cache(maxsize=64)
        def content_row(content_id):
            return self._db.execute("SELECT * FROM learning_content WHERE content_id=?", (content_id,)).fetchone()
        @lru_cache(maxsize=64)
        def dependency_item(content_id):
            content = content_row(content_id)
            return None if content is None else self._item(content, [])
        dependencies_view = SimpleNamespace(_db=self._db, queries=self.queries, get_item=dependency_item)
        now = _utc(now)
        counts = {"ready": 0, "waiting": 0, "instruction": 0, "pending": 0, "disabled": 0,
                  "blocked": 0, "blocked_content": 0, "blocked_material": 0, "blocked_input": 0,
                  "content": self._db.execute("SELECT COUNT(*) FROM learning_content").fetchone()[0], "next_due_at_utc": None,
                  "unavailable_content": self._db.execute("""SELECT COUNT(*) FROM learning_content c
                    WHERE c.stage<>'done' AND NOT EXISTS (SELECT 1 FROM learning_question q JOIN learning_unit u
                    ON u.unit_id=q.unit_id WHERE u.content_id=c.content_id AND q.status='pending')""").fetchone()[0]}
        visible = {row["question_id"]: row for row in self._pending(enabled_kinds)}
        counts["pending"] = self._db.execute("SELECT COUNT(*) FROM learning_question WHERE status='pending'").fetchone()[0]
        reserved = self._db.execute("""SELECT COUNT(*) FROM learning_question q WHERE q.status='pending'
            AND EXISTS (SELECT 1 FROM app_setting a WHERE a.key='retention_reservation_v1:' || q.unit_id)""").fetchone()[0]
        if reserved: counts['diagnostic'] = reserved
        counts["disabled"] = counts["pending"] - len(visible) - reserved
        teaching, blocked_content, dependencies = set(), set(), {}
        ready_ids = independent_ready_ids(self)
        exposure_times = self.content_exposure_times(now)
        ability_states = _teaching.state_map(self._db)
        from .answer_input import available, system_japanese_enabled
        system_input = system_japanese_enabled(self.store)
        for row in visible.values():
            # Decode only if this row reaches a consumer, at most once per row.
            # These three readers do not mutate the persisted question snapshot.
            snapshot = None
            def question():
                nonlocal snapshot
                if snapshot is None:
                    snapshot = _load(row['snapshot_json'])
                return snapshot
            state = ability_states.get(row['content_id'])
            scope = row['ability'] if state and row['ability'] in state['additions'] else None
            content = _teaching.project(self._db, content_row(row["content_id"]), scope, state=state)
            teaching_item = {**_load(content['content_json']), 'stage': content['stage'], 'checkpoint': _load(content['checkpoint_json'])}
            if row['result_json'] is None and self.content_question_blocked(teaching_item, question()):
                counts['blocked_material'] += 1
                continue
            if _paused_practice_guide(content, enabled_kinds):
                counts["disabled"] += 1
                continue
            dependency_key = (row['content_id'], scope)
            if dependency_key not in dependencies:
                dependencies[dependency_key] = (self.prerequisite_status(row['content_id'], scope, ready_ids=ready_ids)
                    if scope else prerequisite_status(dependencies_view, dependency_item(row["content_id"]), ready_ids=ready_ids))
            if dependencies[dependency_key]["blocked"]:
                counts["blocked"] += 1
                blocked_content.add(row["content_id"])
                continue
            if row["result_json"] is not None and _load(row["result_json"]).get("initial_diagnostic"):
                counts["ready"] += 1
                continue
            if content["stage"] in {"intro", "guide"}:
                teaching.add((content["content_id"], scope))
                continue
            if row['result_json'] is None and not available(question(), system_japanese=system_input):
                counts['blocked_input'] += 1
                continue
            due = self.effective_question_due(row, question(), now, exposure_times=exposure_times)
            if content["stage"] == "bridge" and not self._retrieval_ready(content, now):
                due = max(due or "", content["retrieval_at_utc"])
            if due and datetime.fromisoformat(due) > now and row["result_json"] is None:
                counts["waiting"] += 1
                if counts["next_due_at_utc"] is None or due < counts["next_due_at_utc"]:
                    counts["next_due_at_utc"] = due
            else:
                counts["ready"] += 1
        counts["instruction"] = len(teaching)
        counts["blocked_content"] = len(blocked_content)
        return counts

    def content_question_blocked(self, item, question):
        from .personal_content_versions import question_blocked
        from .material_validity import blocked_question
        guide = item.get('checkpoint', {}).get('guide_question') if item.get('stage') == 'guide' else None
        return (question_blocked(self.store, item, question) or blocked_question(self.store, item, question)
                or bool(guide and (question_blocked(self.store, item, guide) or blocked_question(self.store, item, guide))))

    def submit(self, question_id, answered_text="", correct=None, *, assisted=False,
               outcome=None, now=None, duration_ms=0, presentation=None):
        with self._db:
            self._transaction()
            return self._submit_in_transaction(question_id, answered_text, correct, assisted=assisted, outcome=outcome, now=now, duration_ms=duration_ms, presentation=presentation)

    def _submit_in_transaction(self, question_id, answered_text="", correct=None, *, assisted=False,
               outcome=None, now=None, duration_ms=0, presentation=None, diagnostic_context=None):
        if not self._db.in_transaction:
            raise RuntimeError("Caller must own the learning transaction")
        """Freeze first answer. No scheduling until explicit finalization."""
        now = _utc(now)
        if not isinstance(answered_text, str) or len(answered_text) > 10000:
            raise ValueError("Invalid answer")
        if correct is not None and type(correct) is not bool or type(assisted) is not bool:
            raise ValueError("Invalid result")
        if type(duration_ms) is not int or duration_ms < 0:
            raise ValueError("Invalid duration")
        row = self._db.execute("SELECT * FROM learning_question WHERE question_id=?", (question_id,)).fetchone()
        if row is None:
            raise KeyError(question_id)
        from .learning_replacement import assert_answerable
        assert_answerable(self.store, question_id)
        from .retention_session import authorize_course, formal_ready, expose_in_transaction, handoff
        diagnostic = authorize_course(self, question_id, diagnostic_context)
        from .learning_replacement import observation_only
        transferred_handoff = handoff(self.store, question_id)
        transferred = bool(transferred_handoff) or observation_only(self.store, question_id)
        if row["result_json"] is not None:
            return _load(row["result_json"])
        unit = self._db.execute("SELECT * FROM learning_unit WHERE unit_id=?", (row["unit_id"],)).fetchone()
        content = self.ability_instruction_row(unit['content_id'], unit['ability'])
        scope = dict(content).get('instruction_scope')
        if not diagnostic and self.prerequisite_status(unit["content_id"], scope)["blocked"]:
            raise ValueError("请先学习前置内容，或明确选择已有前置知识。")
        if not diagnostic and (not unit["instruction_ready"] or content["stage"] in {"intro", "guide"} or content["stage"] == "bridge" and not self._retrieval_ready(content, now)):
            raise ValueError("Complete teaching and interleaving before independent assessment")
        snapshot = _load(row["snapshot_json"])
        enabled = self.store.get_setting("learning_preferences_v1", {})
        if isinstance(enabled, dict) and isinstance(enabled.get("enabled"), list) and row["kind"] not in enabled["enabled"]:
            raise ValueError("This question type is currently disabled")
        due = self.effective_question_due({**dict(unit), **dict(row)}, snapshot, now)
        if unit["suspended"] or not diagnostic and due and datetime.fromisoformat(due) > now:
            raise ValueError("This ability is not currently due")
        draft = _load(row["draft_json"])
        assisted = assisted or any(draft.get(k) for k in ("assisted", "answer_revealed", "hint_used"))
        outcome = outcome or ("correct" if correct is True else "incorrect" if correct is False else "unresolved")
        spelling_diagnosis = {}
        from .pitch_practice import is_pitch_question, grade_answer as grade_pitch_answer
        if is_pitch_question(snapshot) and outcome not in {"revealed", "cancelled", "device_failure"}:
            judgement = grade_pitch_answer(snapshot, answered_text)
            if judgement["status"] in {"empty", "invalid"}:
                raise ValueError(judgement["message"])
            outcome, correct = judgement["status"], judgement["correct"]
            if "pitch_errors" in judgement:
                spelling_diagnosis["pitch_errors"] = judgement["pitch_errors"]
        if (snapshot.get("kind") in {"vocab_reading", "vocab_recall", "vocab_orthography"}
                and outcome in {"correct", "incorrect", "unresolved"} and not assisted):
            from .learning_questions import grade_learning_answer
            judgement = grade_learning_answer(snapshot, answered_text)
            spelling_diagnosis = {key: judgement[key] for key in (
                "error_category", "error_type", "reading_correct", "spelling_correct", "accepted_spelling")
                if key in judgement}
            if judgement.get("error_category") or judgement.get("accepted_spelling"):
                outcome, correct = judgement["status"], judgement["correct"]
        if snapshot.get("audio_text") and not draft.get("audio_ok") and outcome != "cancelled":
            # An explicit reveal can still expose the answer, but a failed
            # or unplayed mandatory stimulus never proves forgetting.
            outcome, correct = "device_failure", None
        if outcome not in {"correct", "incorrect", "revealed", "unresolved", "device_failure", "invalid", "cancelled"}:
            raise ValueError("Invalid outcome")
        if outcome == "correct" and correct is not True or outcome == "incorrect" and correct is not False:
            raise ValueError("Contradictory result")
        result = {"answered_text": answered_text, "correct": correct, "assisted": bool(assisted),
            "outcome": outcome, "duration_ms": duration_ms, "submitted_at_utc": now.isoformat(),
            "presentation": presentation or {}, **spelling_diagnosis}
        from .assessment_policy import assessment_decision
        from .retention_sources import guarded, assessment_item
        source_guard = guarded(self.store, question_id)
        if diagnostic:
            episode, entry = diagnostic
            material = assessment_item(self.store, snapshot)
            formal = (episode['purpose'] == 'check_and_adjust' and entry['purpose'] == 'check_and_adjust' and entry['phase'] == 'baseline'
                      and not assisted and outcome in {'correct', 'incorrect'} and formal_ready(self, entry, now))
            decision = assessment_decision(material, snapshot,
                purpose=entry['purpose'], exposed=bool(assisted), formal_check=formal, store=self.store)
            if not formal:
                decision['settlement_channel'] = 'observation'
                # A material pretest can borrow an enrolled course opportunity.
                # Its shared receipt must still explicitly prohibit scheduling,
                # just like a pretest for a not-yet-enrolled candidate.
                result['scheduled'] = False
        else:
            material = assessment_item(self.store, snapshot) if source_guard else _load(content["content_json"])
            decision = assessment_decision(material, snapshot, store=self.store)
        if transferred:
            decision = {**decision, 'settlement_channel': 'observation', 'reason': 'diagnostic_observation'}
        result["assessment"] = decision
        if decision["settlement_channel"] != "formal" and outcome in {"correct", "incorrect", "revealed"}:
            result["observed_judgement"] = {"outcome": outcome, "correct": correct}
            result.update(outcome="unresolved", correct=None)
        if decision["settlement_channel"] == "formal" and self._initial_diagnostic(content, unit):
            result["initial_diagnostic"] = True
            if outcome in {"incorrect", "revealed"} or assisted and outcome == "correct":
                # A self-report has never established this ability's
                # memory. A failed placement is a teaching need, not a
                # lapse of a previously learned card or a hidden Again.
                result.update(outcome="unresolved", diagnostic_outcome=outcome,
                              diagnostic_needs_teaching=True)
                checkpoint = _load(content["checkpoint_json"])
                checkpoint["initial_familiarity"].update(route="full", diagnostic_state="needs_teaching",
                    diagnostic_failed_at_utc=now.isoformat(), diagnostic_ability=unit["ability"])
                _teaching.update(self._db, content, stage='intro', checkpoint_json=_dump(checkpoint))
        from .material_review import reviewed_question, QUESTION_VIEW_FIELDS
        presented = reviewed_question(snapshot)
        if presented.get("material_review"):
            # Freeze the material actually displayed for this new answer.
            # Previously graded evidence and the original queue are intact.
            result["material_view"] = {k: presented[k] for k in QUESTION_VIEW_FIELDS if k in presented}
        from .assessment_contract import for_question as assessment_contract, observations
        from .assessment_parts import aggregate
        result["assessment_contract"] = assessment_contract(presented)
        result["observations"] = observations(snapshot, result)
        result["unit_observations"] = aggregate(result["observations"])
        self._db.execute("UPDATE learning_question SET result_json=? WHERE question_id=?", (_dump(result), question_id))
        if outcome != "cancelled":
            if diagnostic or transferred: expose_in_transaction(self.store, snapshot, now)
            else: self._mark_feedback_exposed(unit["content_id"], now)
        if not diagnostic and not transferred and content["stage"] == "bridge" and not result.get("diagnostic_needs_teaching"):
            _teaching.update(self._db, content, stage='check')
        return result

    def finalize(self, question_id, rating=None, now=None, *, fair_new=False):
        with self._db:
            self._transaction()
            return self._finalize_in_transaction(question_id, rating, now=now, fair_new=fair_new)

    def _finalize_in_transaction(self, question_id, rating=None, now=None, *, fair_new=False, diagnostic_context=None):
        if not self._db.in_transaction:
            raise RuntimeError("Caller must own the learning transaction")
        now = _utc(now)
        if rating is not None and (type(rating) is not int or rating not in (1, 2, 3, 4)):
            raise ValueError("Invalid rating")
        from .learning_replacement import assert_answerable
        assert_answerable(self.store, question_id)
        existing = self._db.execute("SELECT result_json FROM learning_event WHERE question_id=?", (question_id,)).fetchone()
        if existing:
            return _load(existing[0])
        row = self._db.execute("SELECT * FROM learning_question WHERE question_id=?", (question_id,)).fetchone()
        if row is None:
            raise KeyError(question_id)
        if row["result_json"] is None:
            raise ValueError("Submit the first answer before rating")
        from .retention_session import authorize_course, formal_ready, expose_in_transaction, handoff
        diagnostic = authorize_course(self, question_id, diagnostic_context)
        from .learning_replacement import observation_only, original_opportunity
        transferred_handoff = handoff(self.store, question_id)
        transferred = bool(transferred_handoff) or observation_only(self.store, question_id)
        answer = _load(row["result_json"])
        from .assessment_contract import validate_result
        validate_result(_load(row["snapshot_json"]), answer)
        unit = self._db.execute("SELECT * FROM learning_unit WHERE unit_id=?", (row["unit_id"],)).fetchone()
        content = self._db.execute("SELECT content_json FROM learning_content WHERE content_id=?", (unit["content_id"],)).fetchone()
        snapshot = _load(row["snapshot_json"])
        from .assessment_policy import settlement_decision
        formal_check = bool(diagnostic and diagnostic[0]['purpose'] == 'check_and_adjust' and diagnostic[1]['purpose'] == 'check_and_adjust'
            and not answer['assisted'] and formal_ready(self, diagnostic[1], now, settling=True))
        material = _load(content['content_json'])
        from .retention_sources import guarded, assessment_item
        source_guard = guarded(self.store, question_id)
        if diagnostic or source_guard:
            material = assessment_item(self.store, snapshot)
        decision = settlement_decision(material, snapshot, answer.get("assessment"), formal_check=formal_check, store=self.store)
        if transferred or diagnostic and not formal_check:
            decision['settlement_channel'] = 'observation'
        # Recheck under BEGIN IMMEDIATE. Keep the immutable first answer;
        # a later invalidation changes its adjudication, never its history.
        event_outcome = answer["outcome"]
        if decision["settlement_channel"] != "formal" and event_outcome in {"correct", "incorrect", "revealed"}:
            event_outcome = "unresolved"
        from .assessment_opportunity import identity, existing as existing_opportunity
        opportunity_id = identity("course", original_opportunity(self.store, question_id))
        if existing_opportunity(self._db, opportunity_id, unit["content_id"], unit["ability"]) is not None:
            if event_outcome in {"correct", "incorrect", "revealed"}:
                event_outcome = "unresolved"
            decision = {**decision, "settlement_channel": "observation", "reason": "opportunity_consumed"}
        before = _load(unit["scheduler_state_json"])
        scheduled = event_outcome in {"correct", "incorrect", "revealed"}
        if scheduled and (answer["assisted"] or answer["outcome"] in {"incorrect", "revealed"}):
            rating = 1
        elif scheduled and rating is None:
            raise ValueError("Independent success requires a rating")
        else:
            rating = rating if scheduled else None
        submitted_at = _utc(datetime.fromisoformat(answer["submitted_at_utc"]))
        if submitted_at > now:
            raise ValueError("Finalization precedes answer")
        after, log = before, None
        if scheduled:
            if unit["scheduler_version"] != ALGORITHM_VERSION:
                raise ValueError("Unsupported scheduler version")
            if before and before.get("last_review") and datetime.fromisoformat(before["last_review"]) > submitted_at:
                raise ValueError("Review time precedes the saved review")
            # The answer was frozen before feedback appeared. Time spent
            # reading feedback, leaving the app or choosing a rating is
            # not evidence of remembering over a longer interval.
            card = Card.from_dict(before) if before else Card(card_id=int(unit["unit_id"][:15], 16), due=submitted_at)
            updated, review = self.store.scheduler.review_card(card, Rating(rating), review_datetime=submitted_at,
                review_duration=answer["duration_ms"])
            after, log = updated.to_dict(), review.to_dict()
            q = _load(row["snapshot_json"])
            free = _independent_retrieval(q)
            probe = updated.due.isoformat() if free or unit["probe_due_at_utc"] is None else min(unit["probe_due_at_utc"], updated.due.isoformat())
            self._db.execute("""UPDATE learning_unit SET scheduler_state_json=?,due_at_utc=?,review_count=review_count+1,
                probe_due_at_utc=?,last_unassisted_success_at_utc=CASE WHEN ? THEN ? ELSE last_unassisted_success_at_utc END
                WHERE unit_id=?""", (_dump(after), updated.due.isoformat(), probe,
                int(free and rating > 1 and not answer["assisted"]), submitted_at.isoformat(), unit["unit_id"]))
            _teaching.update(self._db, self.ability_instruction_row(unit['content_id'], unit['ability']), stage='done')
        result = {"question_id": question_id, "unit_id": unit["unit_id"], "target_id": unit["content_id"],
            "ability": unit["ability"], "scheduled": scheduled, "rating": rating,
            "due_at_utc": after["due"] if after else None,
            "review_count": unit["review_count"] + int(scheduled), "answer": answer,
            "finalized_at_utc": now.isoformat(), "assessment": decision}
        result["attempt_count"] = self._db.execute("SELECT COUNT(*) FROM learning_event WHERE unit_id=?", (unit["unit_id"],)).fetchone()[0] + 1
        if not scheduled and event_outcome in {"unresolved", "device_failure", "invalid"}:
            # Feedback has exposed a model answer. A new variant may be
            # queued, but cannot immediately turn copying into a successful
            # independent review. This is a queue delay, not an FSRS update.
            result["retry_not_before_utc"] = (now + timedelta(seconds=FEEDBACK_COOLDOWN_SECONDS)).isoformat()
        event_id = uuid4().hex
        self._db.execute("INSERT INTO learning_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, question_id, unit["unit_id"], (submitted_at if scheduled else now).isoformat(), rating,
             "primary" if scheduled else "unresolved", event_outcome, int(answer["assisted"]),
             _dump(before) if before is not None else None, _dump(after) if after is not None else None,
             _dump(log) if log is not None else None, _dump(result)))
        if scheduled:
            from .retention_schedule import after_formal_event
            after_formal_event(self.store, unit['unit_id'], event_id, submitted_at,
                               episode_id=diagnostic[0]['id'] if formal_check else None)
        from .learning_evidence import LearningEvidenceStore, reviewed_question
        snapshot = _load(row["snapshot_json"])
        from .retention_sources import trusted_question
        LearningEvidenceStore(self.store).record_attempt("course:" + event_id, snapshot, answer,
            source="course", role="primary" if scheduled else "exposure", now=submitted_at,
            trusted=scheduled and (trusted_question(self.store, snapshot) if diagnostic or source_guard else reviewed_question(snapshot)), opportunity_id=opportunity_id,
            part_ids=tuple(p["part_id"] for p in answer.get("observations", [])) or ("answer",),
            episode_id=diagnostic[0]['id'] if diagnostic else transferred_handoff['episode_id'] if transferred_handoff else '')
        self._db.execute("UPDATE learning_question SET status='completed' WHERE question_id=?", (question_id,))
        if answer["outcome"] != "cancelled":
            if diagnostic or transferred: expose_in_transaction(self.store, snapshot, now)
            else: self._mark_feedback_exposed(unit["content_id"], now)
        if fair_new and scheduled and not unit["review_count"]:
            from .new_dispatch import completed
            completed(self._db, "course")
        return result

    def history(self, content_id=None):
        sql = "SELECT e.* FROM learning_event e JOIN learning_unit u ON u.unit_id=e.unit_id"
        rows = self._db.execute(sql + (" WHERE u.content_id=?" if content_id else "") + " ORDER BY e.rowid",
                                (content_id,) if content_id else ())
        return [_load(row["result_json"]) for row in rows]

    def practice_history(self, content_id, ability, limit=24):
        """Bounded completed snapshots, oldest first; no draft/scheduler writes."""
        from .learning_practice_policy import HISTORY_LIMIT
        if type(limit) is not int or not 1 <= limit <= HISTORY_LIMIT:
            raise ValueError("Practice history limit must be between 1 and 24")
        unit_id = _unit_id(_identifier(content_id), _ability(ability))
        rows = self._db.execute("""SELECT e.*,q.snapshot_json FROM (
            SELECT rowid AS sequence_number,* FROM learning_event
            WHERE unit_id=? ORDER BY rowid DESC LIMIT ?
            ) e JOIN learning_question q ON q.question_id=e.question_id
            ORDER BY e.sequence_number""", (unit_id, limit)).fetchall()
        result = []
        fields = ("target_id", "revision", "ability", "kind", "variant", "input_mode",
                  "prompt", "task", "answer", "practice")
        for row in rows:
            snapshot, event = _load(row["snapshot_json"]), _load(row["result_json"])
            answer = event.get("answer") or {}
            result.append({"event_id": row["event_id"], "question_id": row["question_id"],
                "occurred_at_utc": row["occurred_at_utc"], "role": row["role"],
                "outcome": row["outcome"], "correct": answer.get("correct"),
                "assisted": bool(row["assisted"]), "rating": row["rating"],
                "scheduled": bool(event.get("scheduled")),
                "question": {key: snapshot[key] for key in fields if key in snapshot}})
        return result


def validate_backup_rows(db):
    """Validate imported extension rows in the caller's isolated database."""
    from .learning_dependencies import dependencies_of
    version = db.execute("SELECT value FROM learning_meta WHERE key='schema_version'").fetchone()
    if version is None or version[0] != str(SCHEMA_VERSION):
        raise ValueError("Invalid learning schema")
    for table in TABLES:
        for row in db.execute("SELECT * FROM " + table):
            for key in row.keys():
                if key.endswith("_json") and row[key] is not None:
                    _dump(_load(row[key]))  # finite JSON and bounded snapshots
    _teaching.validate_database(db)
    for row in _teaching.rows(db):
        item = _load(row["content_json"])
        dependencies_of(item)
        if item.get("id") != row["content_id"] or item.get("family") != row["family"] or row["stage"] not in STAGES:
            raise ValueError("Invalid learning content")
        checkpoint = _load(row["checkpoint_json"])
        if not isinstance(checkpoint, dict):
            raise ValueError("Invalid instruction state")
        from .pitch_practice import validate_checkpoint
        validate_checkpoint(row["content_id"], row["revision"], checkpoint)
        from .learning_practice_policy import validate_practice_guide
        validate_practice_guide(row["content_id"], row["revision"], checkpoint, item=item,
                                validate_result=False)
        if "supports_initial_familiarity" in checkpoint and type(checkpoint["supports_initial_familiarity"]) is not bool:
            raise ValueError("Invalid initial familiarity support")
        if "instruction_exposed_at_utc" in checkpoint:
            _utc(datetime.fromisoformat(checkpoint["instruction_exposed_at_utc"]))
        if "feedback_exposed_at_utc" in checkpoint:
            _utc(datetime.fromisoformat(checkpoint["feedback_exposed_at_utc"]))
        if "initial_familiarity" in checkpoint:
            profile = checkpoint["initial_familiarity"]
            abilities = set(_teaching.abilities(db, row))
            if (not isinstance(profile, dict) or profile.get("version") != 1
                    or profile.get("evidence") != "self_report" or not isinstance(profile.get("levels"), dict)
                    or set(profile["levels"]) != abilities
                    or any(not isinstance(value, str) or value not in FAMILIARITY_LEVELS for value in profile["levels"].values())
                    or profile.get("route") not in {"full", "compact", "diagnostic"}
                    or profile.get("diagnostic_state") not in {"active", "not_requested", "needs_teaching"}):
                raise ValueError("Invalid initial familiarity self-report")
            if profile["diagnostic_state"] == "active" and (profile["route"] != "diagnostic"
                    or set(profile["levels"].values()) != {"familiar"}
                    or checkpoint.get("supports_initial_familiarity") is not True):
                raise ValueError("Invalid initial diagnostic permission")
            _utc(datetime.fromisoformat(profile["selected_at_utc"]))
            if "title_answer_exposed" in profile and type(profile["title_answer_exposed"]) is not bool:
                raise ValueError("Invalid diagnostic title exposure")
            if profile["diagnostic_state"] == "needs_teaching":
                if profile.get("diagnostic_ability") not in abilities or profile["route"] != "full":
                    raise ValueError("Invalid diagnostic teaching checkpoint")
                _utc(datetime.fromisoformat(profile["diagnostic_failed_at_utc"]))
        if "prerequisite_acknowledged" in checkpoint:
            if type(checkpoint["prerequisite_acknowledged"]) is not bool:
                raise ValueError("Invalid prerequisite self-report")
            if checkpoint["prerequisite_acknowledged"]:
                if checkpoint.get("prerequisite_evidence") != "self_report":
                    raise ValueError("Prior-knowledge acknowledgement is not review evidence")
                _utc(datetime.fromisoformat(checkpoint["prerequisite_acknowledged_at_utc"]))
        if 'prerequisite_version' in checkpoint:
            from .retention_ai import _hash
            if not _hash(checkpoint['prerequisite_version']) or checkpoint.get('prerequisite_acknowledged') is not True:
                raise ValueError('Invalid prerequisite acknowledgement version')
        _utc(datetime.fromisoformat(row["introduced_at_utc"]))
        for name in ("guided_at_utc", "retrieval_at_utc"):
            if row[name] is not None:
                _utc(datetime.fromisoformat(row[name]))
    for row in db.execute("SELECT u.*,c.family FROM learning_unit u JOIN learning_content c ON c.content_id=u.content_id"):
        if row["ability"] not in ABILITIES[row["family"]] or row["unit_id"] != _unit_id(row["content_id"], row["ability"]):
            raise ValueError("Invalid learning unit")
        if row["scheduler_version"] != ALGORITHM_VERSION:
            raise ValueError("Invalid learning scheduler")
        state = _load(row["scheduler_state_json"])
        if state is not None:
            card = Card.from_dict(state)
            if card.due.isoformat() != row["due_at_utc"] or row["review_count"] < 1:
                raise ValueError("Invalid learning due")
        elif row["due_at_utc"] is not None or row["review_count"]:
            raise ValueError("Unreviewed ability cannot have a schedule")
        events = list(db.execute("SELECT * FROM learning_event WHERE unit_id=? AND role='primary' ORDER BY rowid", (row["unit_id"],)))
        history_matches = not events or events[-1]["state_after_json"] == row["scheduler_state_json"]
        if events and not history_matches and state is not None:
            from .learning_evidence import auxiliary_due_matches
            formal = _load(events[-1]["state_after_json"])
            history_matches = ({key: value for key, value in formal.items() if key != "due"}
                == {key: value for key, value in state.items() if key != "due"}
                and auxiliary_due_matches(db, "course", row["unit_id"], events[-1], row["due_at_utc"]))
        if len(events) != row["review_count"] or not history_matches:
            raise ValueError("Learning history disagrees with its scheduler state")
        for key in ("probe_due_at_utc", "last_unassisted_success_at_utc"):
            if row[key] is not None:
                _utc(datetime.fromisoformat(row[key]))
    for row in db.execute("SELECT q.*,u.content_id,u.ability FROM learning_question q JOIN learning_unit u ON u.unit_id=q.unit_id"):
        q, draft, result = _load(row["snapshot_json"]), _load(row["draft_json"]), _load(row["result_json"])
        if 'content_dependencies' in q:
            from .question_dependencies import validate as validate_field_dependencies
            validate_field_dependencies(q['content_dependencies'])
        if q.get("id") != row["question_id"] or q.get("target_id") != row["content_id"] or q.get("ability") != row["ability"] or q.get("kind") != row["kind"]:
            raise ValueError("Invalid learning question")
        if not isinstance(draft, dict) or result is not None and not isinstance(result, dict):
            raise ValueError("Invalid learning draft")
        from .assessment_contract import validate_result
        validate_result(q, result)
        from .pitch_practice import validate_snapshot, validate_draft
        validate_snapshot(q)
        validate_draft(q, draft)
        _utc(datetime.fromisoformat(row["created_at_utc"]))
        if row["not_before_utc"] is not None:
            _utc(datetime.fromisoformat(row["not_before_utc"]))
    if db.execute("SELECT unit_id FROM learning_question WHERE status='pending' GROUP BY unit_id HAVING COUNT(*)>1").fetchone():
        raise ValueError("Duplicate pending ability")
    for row in db.execute("SELECT e.*,q.status FROM learning_event e JOIN learning_question q ON q.question_id=e.question_id"):
        result = _load(row["result_json"])
        if row["status"] != "completed" or result.get("question_id") != row["question_id"] or result.get("unit_id") != row["unit_id"]:
            raise ValueError("Invalid learning event")
        if bool(result.get("scheduled")) != (row["role"] == "primary") or result.get("rating") != row["rating"]:
            raise ValueError("Invalid learning evidence")
        _utc(datetime.fromisoformat(row["occurred_at_utc"]))
        if "finalized_at_utc" in result:
            finalized = _utc(datetime.fromisoformat(result["finalized_at_utc"]))
            submitted = _utc(datetime.fromisoformat(result["answer"]["submitted_at_utc"]))
            if finalized < submitted or finalized < datetime.fromisoformat(row["occurred_at_utc"]):
                raise ValueError("Finalization precedes answer")
        if "retry_not_before_utc" in result:
            expected = datetime.fromisoformat(row["occurred_at_utc"]) + timedelta(seconds=FEEDBACK_COOLDOWN_SECONDS)
            if (row["role"] != "unresolved" or row["outcome"] not in {"unresolved", "device_failure", "invalid"}
                    or datetime.fromisoformat(result["retry_not_before_utc"]) != expected):
                raise ValueError("Invalid answer-feedback cooldown")
        if row["rating"] is not None:
            Card.from_dict(_load(row["state_after_json"]))
            if (row["assisted"] or row["outcome"] in {"incorrect", "revealed"}) and row["rating"] != 1:
                raise ValueError("Assisted answer cannot be a successful independent review")
        elif row["state_before_json"] != row["state_after_json"]:
            raise ValueError("Unresolved evidence cannot alter the schedule")
    from .learning_replacement import validate_database as validate_replacements
    retired = validate_replacements(db)
    if any(row[0] not in retired for row in db.execute("""SELECT q.question_id FROM learning_question q
            LEFT JOIN learning_event e ON q.question_id=e.question_id
            WHERE q.status='completed' AND e.event_id IS NULL""")):
        raise ValueError("Completed question lacks finalization or an exact replacement receipt")
