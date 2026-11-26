"""Additive vocabulary/grammar learning state, isolated from legacy kana queues.

The caller supplies reviewed immutable question snapshots. Teaching is exposure;
only a frozen independent answer finalized once can update its ability's FSRS.
No old card, session, word-spelling evidence or due date is migrated in place.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from uuid import uuid4

from fsrs import Card, Rating
from .learning_dispatch_types import LearningCurrent

from .domain import ALGORITHM_VERSION, _utc
from . import ability_teaching as _teaching
from .probe_rules import EVIDENCE as PROBE_EVIDENCE, DECLINED_KEY, MAX_WAIT as _PROBE_MAX_WAIT, OTHER_ANSWERS as _PROBE_OTHER_ANSWERS
from .read_only_lessons import ABILITY_ALIASES, read_only_lessons, retired_ability

SCHEMA_VERSION = 1
TABLES = ("learning_meta", "learning_content", "learning_unit", "learning_question", "learning_event")
ABILITIES = {"word": {"understanding", "reading", "use", "listening"},
             "grammar": {"understanding", "application"}}
STAGES = {"intro", "guide", "bridge", "check", "done"}
ALIASES = ABILITY_ALIASES
WORD_LISTENING_ADMISSION = "word_listening_admission_v1"
WORD_LISTENING_DAILY_LIMIT = 8
# After teaching, a lesson's first formal question waits (_retrieval_delay / retrieval_ready):
# every lesson started under the probe-first flow (profile evidence probe_v1, any route) waits
# PROBE_RETRIEVAL_DELAY_SECONDS or RETRIEVAL_OTHER_ANSWERS answers of other items. Records of earlier
# releases (self_report or no profile) and the card batch wait RETRIEVAL_DELAY_SECONDS or until another
# item has a formal answer.
RETRIEVAL_DELAY_SECONDS = 60
# The probe-first spacing has one definition (design 12 N7): probe_rules.OTHER_ANSWERS and MAX_WAIT.
PROBE_RETRIEVAL_DELAY_SECONDS = int(_PROBE_MAX_WAIT.total_seconds())
RETRIEVAL_OTHER_ANSWERS = _PROBE_OTHER_ANSWERS
FEEDBACK_COOLDOWN_SECONDS = 60
# An unscored (unresolved) answer is a queue pause, never an FSRS update:
# re-asking within a minute only repeats the same unscorable attempt.
UNRESOLVED_RETRY_SECONDS = 4 * 3600
# Backup bound for any saved cooldown: legacy +60 s up to the next local
# midnight after a second consecutive unresolved (<= 25 h with DST).
RETRY_COOLDOWN_MAX_SECONDS = 36 * 3600
MAX_JSON_BYTES = 2 * 1024 * 1024
FAMILIARITY_LEVELS = {"new", "some", "familiar"}
# initial_familiarity.evidence: an earlier self-rating, or a probe (2026-11-21).
PROFILE_EVIDENCE = frozenset({"self_report", PROBE_EVIDENCE})


class _ExposureTimes(dict):
    """A queue-pass cache; its ordinary mapping remains backwards compatible."""
    tutor = None
    retention = None
    confirmation = None
    additional_teaching = None
    first_rated = None   # arrangement.sources.recent.RecentFirsts of the pass (design 12 N1, N2)


class _LearningRead:
    """Course read projection shared by one synchronous continue/home action.

    Only LearningStore.read_operation creates it; callers pass it explicitly
    (``projection=``). It is not a database snapshot, a long-lived cache or a
    write permission: a write on this connection or a commit by another one
    (StudyStore.read_revision) drops every memoized value and renews the
    business moment, transaction reads bypass it, and exiting the action ends
    it. Memoized current()/counts() results are detached deep copies.
    """

    def __init__(self, learning, now):
        self.learning = learning
        self.now = _utc(now)
        self.active = True
        self._token = learning.store.read_revision()
        self._values = {}
        # >0 while a memoized current()/counts() computes: that call already
        # validated the revision, and these reads never write.
        self._depth = 0

    def check_active(self):
        if not self.active:
            raise RuntimeError("Learning read operation has ended")

    def _usable(self):
        """Current revision is unchanged; otherwise reset and renew the moment."""
        self.check_active()
        if self.learning.store.in_transaction:
            return False
        if self._depth:
            return True
        token = self.learning.store.read_revision()
        if token != self._token:
            self._token, self._values = token, {}
            self.now = _utc(None)
        return True

    def moment(self, now=None):
        """The explicit time, else this action's business moment (renewed after writes)."""
        if now is not None:
            return _utc(now)
        self._usable()
        return self.now

    def _shared(self, key, compute):
        if not self._usable():
            return compute()
        if key not in self._values:
            self._values[key] = compute()
        return self._values[key]

    def ready_ids(self):
        from .learning_dependencies import independent_ready_ids
        return self._shared(("ready_ids",), lambda: frozenset(independent_ready_ids(self.learning)))

    def exposure_times(self, now):
        return self._shared(("exposure_times", now.isoformat()),
                            lambda: self.learning.content_exposure_times(now))

    def ability_states(self):
        return self._shared(("ability_states",), self.learning.store.teaching_queries.ability_states)

    def content_rows(self):
        return self._shared(("content_rows",), self.learning._dispatch_content_rows)

    def availability(self):
        """This action's content availability (08 section 8.1); live inside transactions."""
        return self._shared(("availability",),
                            lambda: availability_view(self.learning.store, ready_ids=self.ready_ids))

    def personal_settings(self):
        """Every personal-content head/hold key of this revision, or None to read live.

        personal_content_versions/_compensation/personal_question look the
        attached operation up on their store argument (``_learning_read``);
        transaction reads and ended operations read each key live. Values equal
        per-key get_setting reads (WP7a). Per lookup only this connection's
        change counter is compared (no SQL): a local write or rollback renews
        the revision and drops every memoized value, as _usable does. Another
        connection's commit is seen at this operation's next revision check
        (moment/ready_ids/current/counts entry), the same bound as values
        shared inside one memoized current()/counts() computation.
        """
        db = self.learning._db
        if not self.active or db.in_transaction:
            return None
        key = ("personal_settings",)
        # The change counter as StudyStore.read_revision counts it (the token's first part), without its SQL.
        changes = db.total_changes - getattr(self.learning.store, "_untracked_changes", 0)
        if self._depth == 0 and (key not in self._values or changes != self._token[0]):
            self._usable()
        if key not in self._values:
            from .personal_content_versions import SettingIndex
            self._values[key] = SettingIndex(db)
        return self._values[key]

    def seed(self, key, value, revision):
        """Publish a value computed elsewhere (a worker connection) for ``key``.

        Accepted only while this connection's revision still equals the
        ``revision`` the value was requested at and no transaction is open;
        returns False otherwise (the caller then computes inline as usual).
        The key gets the same practice-round suffix as result().
        """
        from copy import deepcopy
        from .practice_round import course_order_token
        if not self._usable() or self._depth or self._token != tuple(revision):
            return False
        self._values[(*key, course_order_token(self.learning.store))] = deepcopy(value)
        return True

    def result(self, key, compute):
        """Memoize a dispatch/count result for this revision and practice-round plan."""
        from copy import deepcopy
        from .practice_round import course_order_token
        if not self._usable():
            return compute()
        key = (*key, course_order_token(self.learning.store))
        if key not in self._values:
            before = self._token
            self._depth += 1
            try:
                value = compute()
            finally:
                self._depth -= 1
            # The plan can change while computing (first freeze); key it after.
            # Never publish a value computed across a revision change.
            key = (*key[:-1], course_order_token(self.learning.store))
            if self._usable() and self._token == before:
                self._values[key] = deepcopy(value)
            return value
        return deepcopy(self._values[key])


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


READ_ONLY_QUESTION = "read_only:"


def _read_only_row(row):
    return isinstance(row, dict) and row.get("read_only") is True


def _read_only_stand_in(content_id, position):
    """A dispatch row for an unread read-only lesson: it names the lesson, nothing else (no unit, no question)."""
    return {"question_id": READ_ONLY_QUESTION + content_id, "content_id": content_id, "read_only": True,
            "position": position, "unit_id": None, "ability": None, "kind": None, "status": "pending",
            "result_json": None, "draft_json": None, "instruction_ready": 0, "review_count": 0,
            "suspended": 0, "snapshot_json": None}


def read_only_head_question(content_id):
    """The stand-in question of a read-only lesson's dispatch head (J6 R1): no row, never answered.

    Dispatch heads carry a question; this one only names the lesson, so frozen priorities, promotions and
    block checks key on it like on a question id. It has no kind, ability or answer.
    """
    return {"id": READ_ONLY_QUESTION + content_id, "target_id": content_id, "content_id": content_id,
            "ability": None, "kind": None, "read_only": True}


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


def _probe_era(checkpoint):
    """The probe-first spacing applies: every lesson or ability started under the probe-first flow
    (profile evidence probe_v1), whatever its route — probe, failed probe then taught, taught directly
    because nothing could be probed, or an ordinary first check. Records of earlier releases (evidence
    self_report, or no profile) and the card batch keep the earlier spacing."""
    if isinstance(checkpoint, str):
        checkpoint = _load(checkpoint) or {}
    profile = (checkpoint or {}).get("initial_familiarity") or {}
    return profile.get("evidence") == PROBE_EVIDENCE


def _retrieval_delay(checkpoint):
    return PROBE_RETRIEVAL_DELAY_SECONDS if _probe_era(checkpoint) else RETRIEVAL_DELAY_SECONDS


def _independent_retrieval(question):
    free = question.get("input_mode", question.get("response")) in {"text", "typing", "free_text"}
    if free and "practice" in question:
        # practice_metadata(question) is not None and question_scaffold(question) == "retrieval", read once and
        # without copying the metadata (a typed question's scaffold is its metadata's).
        from .learning_practice_policy import practice_metadata_view
        meta = practice_metadata_view(question)
        return meta is not None and meta["scaffold"] == "retrieval"
    return free


def free_retrieval(question):
    """Whether ``question`` (a snapshot or a plan) is a free retrieval: its probe due replaces the due (_ability_due)."""
    return _independent_retrieval(question)


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


def _attached_read(store):
    read = getattr(store, "_learning_read", None)
    return read if read is not None and read.active else None


_DICTIONARY_WORDS = {}  # (lexicon path, word id) -> bool; the dictionary layer is a process constant


def _word_lookup(store):
    """Dictionary-layer existence check through the store's bound content repository."""
    repo = getattr(store, "_material_resources", (None, None))[0]
    if repo is None or not callable(getattr(repo, "get_word", None)):
        return None
    key = str(getattr(repo, "path", id(repo)))

    def exists(word_id):
        cached = _DICTIONARY_WORDS.get((key, word_id))
        if cached is None:
            cached = _DICTIONARY_WORDS[(key, word_id)] = repo.get_word(word_id) is not None
        return cached
    return exists


def availability_view(store, *, ready_ids=None):
    """One read of content availability: catalogue, registry and learned rows.

    The effective catalogue is a process constant; ``store.content_catalogue``
    (a content_availability.Catalogue or a callable returning one) replaces it
    for isolated stores such as synthetic test learners. Without a saved
    registry (content_libraries_v1, written on every App start) nothing is
    orphaned and no catalogue or row is read. No row is written.
    """
    from .content_availability import Catalogue, build_view
    from .content_library_service import availability_catalogue, registry_for_availability
    registry = registry_for_availability(store)
    if registry is None:
        return build_view(Catalogue(), None, ())
    source = getattr(store, "content_catalogue", None)
    if source is None:
        catalogue = availability_catalogue(word_exists=_word_lookup(store))
    else:
        catalogue = source() if callable(source) else source
    db = store._db
    rows = []
    if tables_present(db):
        ids = [row[0] for row in db.execute("SELECT content_id FROM learning_content ORDER BY rowid")]
        outside = [cid for cid in ids if cid not in catalogue.ids and cid.startswith("word:")]
        dictionary = set()
        for start in range(0, len(outside), 400):
            chunk = outside[start:start + 400]
            dictionary.update(row[0] for row in db.execute(
                "SELECT content_id FROM learning_content WHERE content_id IN (%s) "
                "AND json_extract(content_json,'$.dictionary_only')=1" % ",".join("?" * len(chunk)), chunk))
        rows = [{"content_id": cid, "dictionary_only": cid in dictionary} for cid in ids]
    return build_view(catalogue, registry, rows, ready_ids=ready_ids)


class GuidedQuestionUnavailable(LookupError):
    """Diagnostic marker: no guided example of this kind can be built now."""


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

    @contextmanager
    def read_operation(self, *, now=None):
        """Share course read projections inside one synchronous action only.

        Pass the yielded object explicitly as ``projection=`` to current/counts
        and their dispatch helpers. It never crosses await/threads/actions;
        writes keep their own transactions and re-validate current rows.
        """
        read = _LearningRead(self, now)
        # Personal-content readers receive the StudyStore, not this object:
        # attach the operation there for its lifetime (nested ones restore).
        previous = getattr(self.store, "_learning_read", None)
        self.store._learning_read = read
        try:
            yield read
        finally:
            read.active = False
            read._values = {}
            self.store._learning_read = previous

    def availability(self, *, projection=None):
        """AvailabilityView of the attached/explicit read operation, else a live read."""
        read = projection if projection is not None else _attached_read(self.store)
        if read is not None:
            return read.availability()
        from .learning_dependencies import independent_ready_ids
        return availability_view(self.store, ready_ids=lambda: independent_ready_ids(self))

    def require_available(self, content_ids, items=None):
        """Write gate (08 section 8.1): refuse unavailable content before any row changes.

        Re-reads availability (live inside the caller's transaction). ``items``
        maps content id -> snapshot for content without progress yet.
        """
        if isinstance(content_ids, str):
            content_ids = (content_ids,)
        view = self.availability()
        for content_id in content_ids:
            item = (items or {}).get(content_id) or {}
            reason = view.refusal(content_id, {"dictionary_only": item.get("dictionary_only") is True})
            if reason:
                raise ValueError(reason)

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
            # abilities: [] is a lesson that is only read (J6 R1): no unit, no question.
            if family not in ABILITIES or not set(abilities) <= ABILITIES[family]:
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
            # 08 section 7.3: no enrollment or added ability for unavailable content.
            self.require_available([item["id"] for item, _ in normalized], {item["id"]: item for item, _ in normalized})
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

    def admit_word_listening(self, now=None, *, daily_limit=WORD_LISTENING_DAILY_LIMIT, enabled_kinds=None):
        """Give studied words their listening unit in small daily batches.

        Each unit starts with the same deferred teaching as any added ability,
        so nothing lands in the review queue at once and no due date moves.
        A batch-introduced word instead needs its listening gate open (四之二):
        every earlier admitted, enabled ability formally passed, and at least
        one formal pass, so a word nobody answered is never "studied".
        """
        def batch_open(content_id):
            gate = self.batch_ability_gate(content_id, enabled_kinds)
            return gate is not None and bool(gate['passed']) and 'listening' not in gate['closed']
        from .app_clock import localize
        now = _utc(now)
        today = localize(now).date().isoformat()
        with self._db:
            self._transaction()
            row = self._db.execute("SELECT value_json FROM app_setting WHERE key=?",
                                   (WORD_LISTENING_ADMISSION,)).fetchone()
            state = _load(row["value_json"]) if row else {}
            used = state.get("count", 0) if state.get("date") == today else 0
            remaining = daily_limit - used
            admitted = []
            unavailable = self.availability().unavailable_ids
            for content in (_teaching.word_listening_candidates(self._db, remaining, batch_open=batch_open) if remaining > 0 else ()):
                if content["id"] in unavailable:
                    continue
                if _teaching.append(self._db, content, ["listening"], now):
                    admitted.append(content["id"])
            if admitted:
                self._db.execute("""INSERT INTO app_setting VALUES (?,?)
                    ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
                    (WORD_LISTENING_ADMISSION, _dump({"date": today, "count": used + len(admitted)})))
        return admitted

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

    def has_other_instruction_exposure(self, content_id, *, instruction_scope=None, personal_revision=None):
        """Another ability's introduction was shown, not necessarily mastered.

        Reuse only the same material; a changed personal explanation needs its
        own introduction unless another frozen guide binds that exact revision.
        """
        current = self.instruction_row(content_id, instruction_scope)
        if current is None:
            return False
        original = self.instruction_row(content_id)
        state = _teaching.read(self._db, content_id)
        rows = [original, *((state or {}).get('additions', {}).values())]
        return any(row['introduction_id'] != current['introduction_id']
            and (checkpoint := _load(row['checkpoint_json'])).get('instruction_exposed_at_utc')
            and (checkpoint.get('guide_source_item') or {}).get('personal_revision') == personal_revision
            for row in rows)

    def prepare_initial_familiarity(self, content_id, *, expected_introduction_id, instruction_scope=None, ability=None):
        """Separate only untouched abilities, preserving every actual study row."""
        with self._db:
            self._transaction()
            row = self.instruction_row(content_id, instruction_scope)
            if row is None or row['introduction_id'] != expected_introduction_id:
                raise ValueError('The introduction is no longer current')
            if ability is not None and ability not in _teaching.abilities(self._db, row):
                raise ValueError('The ability is outside this introduction')
            separated = _teaching.separate_untouched(self._db, row, first_ability=ability)
            if separated and ability:
                instruction_scope = _teaching.scope_for(self._db, content_id, ability)
        return self.instruction_item(content_id, instruction_scope)

    def set_initial_familiarity(self, content_id, levels, *, expected_introduction_id=None,
                                title_answer_exposed=False, now=None, fair_new=False, instruction_scope=None):
        """Save a first-exposure self-report, never a successful review.

        Each explicitly enrolled ability needs its own choice. Familiar content
        can be tested before teaching; a failed placement test returns to the
        teaching flow without pretending that it was a previously learned card.
        Replaying the original choice is harmless, even after real review began.
        """
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
            self.require_available(content_id)
            abilities = set(_teaching.abilities(self._db, row))
            if set(levels) != abilities:
                raise ValueError("Choose familiarity separately for every enrolled ability")
            checkpoint = _load(row["checkpoint_json"])
            previous = checkpoint.get("initial_familiarity")
            if previous is not None:
                if previous.get("levels") != levels:
                    raise ValueError("Initial familiarity has already been saved")
                return self.instruction_item(content_id, instruction_scope)
            # The same rule as the batch cards (_intro_started): what a check of this lesson left is not a start.
            has_events, has_draft = self._intro_started(content_id, abilities)
            if row["stage"] != "intro" or checkpoint.get("guide_answer") is not None or has_events:
                raise ValueError("This content has already started learning")
            if has_draft:
                raise ValueError("An existing answer or draft must be preserved")
            route = ("full" if "new" in levels.values() else "compact" if "some" in levels.values()
                     or checkpoint.get("instruction_exposed_at_utc")
                     or checkpoint.get("supports_initial_familiarity") is not True else "diagnostic")
            checkpoint["initial_familiarity"] = {"version": 1, "evidence": "self_report",
                "levels": dict(levels), "route": route, "selected_at_utc": now.isoformat(),
                "diagnostic_state": "active" if route == "diagnostic" else "not_requested"}
            checkpoint.pop('familiarity_draft', None)
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

    def save_familiarity_draft(self, content_id, levels, *, expected_introduction_id, instruction_scope=None):
        """Keep explicit partial choices for an already-exposed shared intro."""
        with self._db:
            self._transaction()
            row = self.instruction_row(content_id, instruction_scope)
            if row is None or row['introduction_id'] != expected_introduction_id or row['stage'] != 'intro':
                raise ValueError('The introduction is no longer current')
            checkpoint = _load(row['checkpoint_json'])
            if (checkpoint.get('initial_familiarity') or not isinstance(levels, dict)
                    or not set(levels) < set(_teaching.abilities(self._db, row))
                    or any(v not in FAMILIARITY_LEVELS for v in levels.values())):
                raise ValueError('Invalid partial familiarity choices')
            checkpoint['familiarity_draft'] = dict(levels)
            _teaching.update(self._db, row, checkpoint_json=_dump(checkpoint))
        return self.instruction_item(content_id, instruction_scope)

    # Batch introduction (dormant): intro_batch owns the rules,
    # intro_batch_state the cursor setting; this store owns each transaction.

    def _intro_started(self, content_id, abilities):
        """Any real event, submitted result or started draft in this scope.

        What a check of the lesson left behind is not a start (检查掌握情况 on
        a lesson that is enrolled but not yet begun): the check's own question
        (id ``diagnostic:…``) with an unscheduled observation, or still
        pending with an answer or a draft. Those rows stay exactly as they
        are; counting them refused the first familiarity choice for good, and
        继续学习 kept returning to a page that could not be completed
        (native12). A scheduled review is a start whoever asked the question.
        """
        db = self._db
        from .learning_dependencies import has_started_answer
        if any(db.execute("""SELECT 1 FROM learning_event WHERE unit_id=?
                AND NOT (role<>'primary' AND substr(question_id,1,11)='diagnostic:') LIMIT 1""",
                          (_unit_id(content_id, a),)).fetchone() for a in abilities):
            return True, False
        for pending in db.execute("""SELECT q.draft_json,q.result_json,u.ability FROM learning_question q
                JOIN learning_unit u ON u.unit_id=q.unit_id WHERE u.content_id=? AND q.status='pending'
                AND substr(q.question_id,1,11)<>'diagnostic:'""", (content_id,)):
            if pending['ability'] in abilities and (
                    pending["result_json"] is not None or has_started_answer(_load(pending["draft_json"]))):
                return False, True
        return False, False

    def _intro_batch_view(self, row):
        db = self._db
        abilities = set(_teaching.abilities(db, row))
        has_events, has_draft = self._intro_started(row['content_id'], abilities)
        return {"content_id": row['content_id'], "family": row['family'],
                "domain": _load(row['content_json']).get('domain'),
                "instruction_scope": dict(row).get('instruction_scope'), "stage": row['stage'],
                "checkpoint": _load(row['checkpoint_json']), "has_events": has_events,
                "has_started_draft": has_draft,
                "units": [{"instruction_ready": unit['instruction_ready'], "review_count": unit['review_count'],
                           "scheduler_state": _load(unit['scheduler_state_json']), "due_at_utc": unit['due_at_utc']}
                          for unit in db.execute('SELECT * FROM learning_unit WHERE content_id=?', (row['content_id'],))
                          if unit['ability'] in abilities]}

    def intro_batch_sizes(self):
        from . import intro_batch_state
        return intro_batch_state.read_sizes(self._db)

    def set_intro_batch_sizes(self, sizes):
        db = self._db
        from . import intro_batch, intro_batch_state
        sizes = intro_batch.validate_sizes(sizes)
        with db:
            self._transaction()
            intro_batch_state.write_sizes(db, sizes)
        return sizes

    def intro_batch(self):
        from . import intro_batch_state
        return intro_batch_state.read(self._db)

    def _intro_batch_entry(self, state, content_id):
        entry = next((e for e in (state or {}).get('entries', ()) if e['content_id'] == content_id), None)
        if entry is None:
            raise ValueError('This item is not in the current introduction batch')
        return entry

    def intro_batch_candidates(self, enabled_kinds, head_content_id, *, now=None, fair_new=False):
        """Read-only: the dispatched head plus later eligible heads of its family.

        Later heads come from current(teaching_only=True) probes restricted to
        the remaining eligible items, so dispatch order and every head filter
        (kinds, pauses, prerequisites, material blocks, timing) stay owned by
        current(). Nothing is written; start_intro_batch re-validates.
        """
        from . import intro_batch
        from .assessment_policy import teaching_allowed
        now = _utc(now)

        def eligible(content_id):
            row = self.instruction_row(content_id)
            return row is not None and intro_batch.eligible(self._intro_batch_view(row)), row

        ok, head = eligible(head_content_id)
        if (not ok or not teaching_allowed(self.instruction_item(head_content_id), store=self.store)
                or self.prerequisite_status(head_content_id)["blocked"]):
            return []
        family = head['family']
        chosen = [head_content_id]
        size = self.intro_batch_sizes()[family]
        pool = set()
        unavailable = self.availability().unavailable_ids
        if head_content_id in unavailable:
            return []
        if size > 1:
            for (cid,) in self._db.execute("""SELECT content_id FROM learning_content
                    WHERE family=? AND stage='intro' AND content_id<>? ORDER BY rowid""",
                    (family, head_content_id)).fetchall():
                if cid not in unavailable and eligible(cid)[0]:
                    pool.add(cid)
        while len(chosen) < size and pool:
            probe = self.current(enabled_kinds, now, fair_new=fair_new, teaching_only=True,
                                 teaching_content_ids=frozenset(pool))
            if not probe or probe.get("type") != "instruction":
                break
            content = probe["content"]
            pool.discard(content["id"])
            if content.get("instruction_scope") is None and teaching_allowed(content, store=self.store):
                chosen.append(content["id"])
        return chosen

    def start_intro_batch(self, content_ids, *, family, now=None):
        """Open one batch from already chosen eligible heads; no question or schedule."""
        db = self._db
        from . import intro_batch, intro_batch_state
        from .explicit_learning_start import claim
        now = _utc(now)
        if (not isinstance(content_ids, (list, tuple)) or not content_ids
                or any(not isinstance(cid, str) for cid in content_ids) or len(set(content_ids)) != len(content_ids)):
            raise ValueError('Choose distinct items for the introduction batch')
        if family not in intro_batch.SIZE_RANGES:
            raise ValueError('Batch introduction supports only words and grammar')
        with db:
            self._transaction()
            if intro_batch_state.read(db) is not None:
                raise ValueError('Another introduction batch is still open')
            size = intro_batch_state.read_sizes(db)[family]
            if len(content_ids) > size:
                raise ValueError('The introduction batch is larger than its setting')
            self.require_available(content_ids)
            entries = []
            for cid in content_ids:
                row = self.instruction_row(cid)
                if row is None:
                    raise KeyError(cid)
                if row['family'] != family or not intro_batch.eligible(self._intro_batch_view(row)):
                    raise ValueError('This item has already started its own introduction')
                first = intro_batch.first_ability(family, _teaching.original_abilities(db, cid))
                _teaching.separate_untouched(db, row, first_ability=first)
                row = self.instruction_row(cid)
                checkpoint = _load(row['checkpoint_json'])
                checkpoint[intro_batch.ROUTE_KEY] = intro_batch.ROUTE
                _teaching.update(db, row, checkpoint_json=_dump(checkpoint))
                claim(db, cid, introduction_id=row['introduction_id'], now=now)
                entries.append({'content_id': cid, 'introduction_id': row['introduction_id'], 'ability': first})
            state = {'version': 1, 'batch_id': uuid4().hex, 'family': family, 'created_at_utc': now.isoformat(),
                     'size': size, 'entries': entries, 'phase': 'cards', 'card_index': 0, 'checks': [],
                     'quiz_queue': []}
            intro_batch_state.write(db, state)
        return intro_batch_state.read(db)

    def choose_intro_card(self, content_id, choice, *, expected_introduction_id, now=None, fair_new=False):
        """"Next" or "I already know it": a self-report, never a review."""
        db = self._db
        from . import intro_batch, intro_batch_state
        from .explicit_learning_start import claim
        now = _utc(now)
        if choice not in intro_batch.CHOICES:
            raise ValueError('Unknown introduction card choice')
        with db:
            self._transaction()
            row = self.instruction_row(content_id)
            if row is None:
                raise KeyError(content_id)
            if row['introduction_id'] != expected_introduction_id:
                raise ValueError('The introduction is no longer current')
            self.require_available(content_id)
            abilities = list(_teaching.abilities(db, row))
            levels = {a: 'new' if choice == 'next' else 'familiar' for a in abilities}
            checkpoint = _load(row['checkpoint_json'])
            previous = checkpoint.get('initial_familiarity')
            if previous is not None:
                if previous.get('levels') != levels or previous.get('source') != intro_batch.CARD_SOURCE:
                    raise ValueError('Initial familiarity has already been saved')
                return intro_batch_state.read(db)
            state = intro_batch_state.read(db)
            entry = self._intro_batch_entry(state, content_id)
            if entry['introduction_id'] != expected_introduction_id:
                raise ValueError('The introduction is no longer current')
            if checkpoint.get(intro_batch.ROUTE_KEY) != intro_batch.ROUTE:
                raise ValueError('This item keeps its original introduction route')
            has_events, has_draft = self._intro_started(content_id, set(abilities))
            if row['stage'] != 'intro' or checkpoint.get('guide_answer') is not None or has_events:
                raise ValueError('This content has already started learning')
            if has_draft:
                raise ValueError('An existing answer or draft must be preserved')
            state = intro_batch.card_done(state, content_id, choice)
            route = 'full' if choice == 'next' else 'diagnostic'
            profile = {'version': 1, 'evidence': 'self_report', 'levels': levels, 'route': route,
                       'selected_at_utc': now.isoformat(),
                       'diagnostic_state': 'active' if route == 'diagnostic' else 'not_requested',
                       'source': intro_batch.CARD_SOURCE}
            if route == 'diagnostic':
                # The card itself showed the answer: reuse the exposure gate.
                profile['title_answer_exposed'] = True
            checkpoint['initial_familiarity'] = profile
            checkpoint.pop('familiarity_draft', None)
            _teaching.update(db, row, checkpoint_json=_dump(checkpoint))
            claim(db, content_id, introduction_id=row['introduction_id'], now=now)
            if route == 'diagnostic':
                _teaching.update(db, row, stage='bridge', guided_at_utc=now.isoformat(),
                    retrieval_at_utc=(now + timedelta(seconds=RETRIEVAL_DELAY_SECONDS)).isoformat())
                _teaching.ready_units(db, row)
                if fair_new:
                    from .new_dispatch import completed
                    completed(db, "course")
            intro_batch_state.write(db, state)
        return state

    def start_intro_quiz(self, questions_by_content, *, rng=None):
        """Freeze caller-built guided questions and move quiz items to guide."""
        db = self._db
        from . import intro_batch, intro_batch_state
        if not isinstance(questions_by_content, dict):
            raise ValueError('Batch quiz questions must be grouped by item')
        with db:
            self._transaction()
            state = intro_batch_state.read(db)
            if state is None:
                raise ValueError('No introduction batch is open')
            if state['phase'] == 'quiz':
                return state
            if state['card_index'] != len(state['entries']):
                raise ValueError('Finish every introduction card before the batch quiz')
            if set(questions_by_content) != set(intro_batch.quiz_items(state)):
                raise ValueError('Batch quiz questions do not match its items')
            ids = {}
            for cid in intro_batch.quiz_items(state):
                entry = self._intro_batch_entry(state, cid)
                row = self.instruction_row(cid)
                if row is None or row['introduction_id'] != entry['introduction_id'] or row['stage'] != 'intro':
                    raise ValueError('The introduction is no longer current')
                if self.prerequisite_status(cid)["blocked"]:
                    raise ValueError("请先学习前置内容，或明确选择已有前置知识。")
                item = intro_batch.validate_item_state({'version': 1, 'batch_id': state['batch_id'],
                    'questions': questions_by_content[cid], 'answers': {}, 'correct': False, 'draft': {}},
                    content_id=cid)
                checkpoint = _load(row['checkpoint_json'])
                checkpoint[intro_batch.ITEM_KEY] = item
                # Same transition body as advance_instruction('intro_done').
                checkpoint['last_checkpoint'] = 'intro_done'
                _teaching.update(db, row, stage='guide', checkpoint_json=_dump(checkpoint))
                ids[cid] = [question['id'] for question in item['questions']]
            state = intro_batch.start_quiz(state, ids, rng)
            intro_batch_state.write(db, state)
        return state

    def _intro_quiz_row(self, state, content_id):
        from . import intro_batch
        if state is None or state['phase'] != 'quiz' or content_id not in intro_batch.quiz_items(state):
            raise ValueError('This item has no open batch quiz')
        entry = self._intro_batch_entry(state, content_id)
        row = self.instruction_row(content_id)
        if row is None or row['introduction_id'] != entry['introduction_id'] or row['stage'] != 'guide':
            raise ValueError('The introduction is no longer current')
        checkpoint = _load(row['checkpoint_json'])
        item = intro_batch.validate_item_state(checkpoint.get(intro_batch.ITEM_KEY), content_id=content_id)
        if item['batch_id'] != state['batch_id']:
            raise ValueError('Batch quiz checkpoint belongs to another batch')
        return row, checkpoint, item

    def save_intro_quiz_draft(self, content_id, question_id, draft):
        db = self._db
        from . import intro_batch, intro_batch_state
        if not isinstance(draft, dict):
            raise ValueError('Batch quiz draft must be an object')
        with db:
            self._transaction()
            row, checkpoint, item = self._intro_quiz_row(intro_batch_state.read(db), content_id)
            item['draft'] = {'question_id': question_id, 'state': dict(draft)}
            checkpoint[intro_batch.ITEM_KEY] = intro_batch.validate_item_state(item, content_id=content_id)
            _teaching.update(db, row, checkpoint_json=_dump(checkpoint))
        return checkpoint[intro_batch.ITEM_KEY]

    def answer_intro_quiz(self, content_id, question_id, *, answered_text, correct, now=None):
        """Guided evidence only: no learning_question, event or scheduler write."""
        db = self._db
        from . import intro_batch, intro_batch_state
        now = _utc(now)
        with db:
            self._transaction()
            state = intro_batch_state.read(db)
            row, checkpoint, item = self._intro_quiz_row(state, content_id)
            state, item = intro_batch.record_answer(state, item, content_id, question_id, correct, now,
                                                    answer=answered_text, orientation=self.arrangement_orientation())
            checkpoint[intro_batch.ITEM_KEY] = item
            _teaching.update(db, row, checkpoint_json=_dump(checkpoint))
            intro_batch_state.write(db, state)
        return state, item

    def complete_intro_batch(self, *, now=None, fair_new=False):
        """Move every quiz item to bridge at once, as advance_instruction('guide_correct').

        Design 12 Q2: an item settled without a correct answer (breadth first)
        moves the same way with the 'bridge' checkpoint, as a passed guide does.
        """
        db = self._db
        from . import intro_batch, intro_batch_state
        now = _utc(now)
        with db:
            self._transaction()
            state = intro_batch_state.read(db)
            if state is None:
                raise ValueError('No introduction batch is open')
            rows = {cid: self._intro_quiz_row(state, cid) for cid in intro_batch.quiz_items(state)} \
                if state['phase'] == 'quiz' else {}
            if not intro_batch.finished(state, {cid: value[2] for cid, value in rows.items()}):
                raise ValueError('Every batch item needs one correct quiz answer first')
            for cid, (row, checkpoint, item) in rows.items():
                if self.prerequisite_status(cid)["blocked"]:
                    raise ValueError("请先学习前置内容，或明确选择已有前置知识。")
                checkpoint['last_checkpoint'] = 'guide_correct' if item['correct'] else 'bridge'
                _teaching.update(db, row, stage='bridge', guided_at_utc=now.isoformat(),
                    retrieval_at_utc=(now + timedelta(seconds=RETRIEVAL_DELAY_SECONDS)).isoformat(),
                    checkpoint_json=_dump(checkpoint))
                _teaching.ready_units(db, row)
            if fair_new and rows:
                from .new_dispatch import completed
                completed(db, "course")
            intro_batch_state.clear(db)
        return list(rows)

    def batch_ability_gate(self, content_id, enabled_kinds=None):
        """Read-only 四之二 ability gate of a batch_v1 item; None for every other item.

        'closed' lists abilities (in intro_batch.ability_order) not yet open;
        'closed_kinds' the reading kinds not yet offered (written reading
        before one formal reading choice); 'passed' the abilities with formal
        evidence. Formal is the learning_ready_evidence predicate: primary,
        correct, unassisted and rated above Again. An ability whose unit
        already has any event stays open, so re-enabling an earlier kind never
        hides started work or due reviews. Nothing is written here.
        """
        from . import intro_batch
        from .learning_questions import KIND_ABILITIES, question_kinds
        db = self._db
        row = db.execute('SELECT * FROM learning_content WHERE content_id=?', (content_id,)).fetchone()
        if row is None or _load(row['checkpoint_json']).get(intro_batch.ROUTE_KEY) != intro_batch.ROUTE:
            return None
        units = db.execute('SELECT unit_id,ability,suspended FROM learning_unit WHERE content_id=?',
                           (content_id,)).fetchall()
        # A paused ability is skipped like a disabled one: it cannot be answered.
        admitted = {unit['ability'] for unit in units if not unit['suspended']}
        started = {unit['ability'] for unit in units if db.execute(
            'SELECT 1 FROM learning_event WHERE unit_id=? LIMIT 1', (unit['unit_id'],)).fetchone()}
        try:
            available = question_kinds(self._item(row, []))
        except (ValueError, KeyError, TypeError):
            available = []
        wanted = None if enabled_kinds is None else set(enabled_kinds)
        enabled = [kind for kind in available if wanted is None or kind in wanted]
        evidence = db.execute("""SELECT DISTINCT u.ability,q.kind FROM learning_event e
            JOIN learning_unit u ON u.unit_id=e.unit_id JOIN learning_question q ON q.question_id=e.question_id
            WHERE u.content_id=? AND e.role='primary' AND e.outcome='correct' AND e.assisted=0
            AND e.rating>1""", (content_id,)).fetchall()
        passed = {value[0] for value in evidence}
        enabled_abilities = {KIND_ABILITIES[kind] for kind in enabled if kind in KIND_ABILITIES}
        closed = {ability for ability in intro_batch.ability_order(row['family']) if ability not in started
                  and not intro_batch.ability_open(row['family'], ability, admitted=admitted,
                                                   enabled_abilities=enabled_abilities, formally_correct=passed)}
        reading = [kind for kind in available if KIND_ABILITIES.get(kind) == 'reading']
        closed_kinds = set(reading) - set(intro_batch.reading_kinds_open(
            reading, enabled_kinds=enabled, formally_correct_kinds={value[1] for value in evidence}))
        return {'closed': frozenset(closed), 'closed_kinds': frozenset(closed_kinds), 'passed': frozenset(passed)}

    def _batch_gate_reader(self, enabled_kinds):
        """One dispatch/count pass: is this pending row a not-yet-open ability?

        A gated row is "not yet open": it is neither waiting nor disabled.
        A submitted answer or a started draft is never hidden.
        """
        from . import intro_batch
        from .learning_dependencies import has_started_answer
        marked = {value[0] for value in self._db.execute(
            "SELECT content_id FROM learning_content WHERE json_extract(checkpoint_json,'$.intro_route')=?",
            (intro_batch.ROUTE,))}
        gates = {}

        def closed(row):
            cid = row['content_id']
            if cid not in marked or row['result_json'] is not None or has_started_answer(_load(row['draft_json'])):
                return False
            if cid not in gates:
                gates[cid] = self.batch_ability_gate(cid, enabled_kinds)
            return gates[cid] is not None and row['ability'] in gates[cid]['closed']
        return closed

    def is_batch_route(self, content_id):
        """The item was introduced by batch cards (marker on its original row)."""
        from . import intro_batch
        row = self._db.execute('SELECT checkpoint_json FROM learning_content WHERE content_id=?',
                               (content_id,)).fetchone()
        return row is not None and _load(row['checkpoint_json']).get(intro_batch.ROUTE_KEY) == intro_batch.ROUTE

    def open_batch_ability(self, content_id, *, instruction_scope, expected_introduction_id, enabled_kinds=None,
                           now=None, fair_new=False):
        """First appearance of a later ability of a batch item (四之二 item 4).

        No intro page and no familiarity picker: write the same self-report
        shape a "下一项" card writes (levels new, route full, source card) and
        open the scope for its first formal question. Guards match
        choose_intro_card. The transition body is the "known" path's
        (bridge, guided_at, ready_units), but retrieval_at is now rather than
        +60 s: this step shows no answer-bearing teaching, so there is no new
        exposure to space. retrieval_ready stays honest because nothing was
        exposed here; the sibling exposure cooldown (exposure_times) and the
        说明卡 assisted rule keep guarding any card the learner does open.
        """
        db = self._db
        from . import intro_batch
        from .explicit_learning_start import claim
        now = _utc(now)
        if not instruction_scope:
            raise ValueError('Only a later ability opens without its own introduction')
        with db:
            self._transaction()
            row = self.instruction_row(content_id, instruction_scope)
            if row is None:
                raise KeyError(content_id)
            if row['introduction_id'] != expected_introduction_id:
                raise ValueError('The introduction is no longer current')
            self.require_available(content_id)
            original = db.execute('SELECT checkpoint_json FROM learning_content WHERE content_id=?',
                                  (content_id,)).fetchone()
            if _load(original[0]).get(intro_batch.ROUTE_KEY) != intro_batch.ROUTE:
                raise ValueError('This item keeps its original introduction route')
            levels = {instruction_scope: 'new'}
            checkpoint = _load(row['checkpoint_json'])
            previous = checkpoint.get('initial_familiarity')
            if previous is not None:
                if previous.get('levels') != levels or previous.get('source') != intro_batch.CARD_SOURCE:
                    raise ValueError('Initial familiarity has already been saved')
                return self.instruction_item(content_id, instruction_scope)
            gate = self.batch_ability_gate(content_id, enabled_kinds)
            if gate is not None and instruction_scope in gate['closed']:
                raise ValueError('This ability opens after the earlier one is answered correctly')
            has_events, has_draft = self._intro_started(content_id, {instruction_scope})
            if (row['stage'] != 'intro' or has_events
                    or any(key.startswith('guide_') for key in checkpoint)):
                raise ValueError('This content has already started learning')
            if has_draft:
                raise ValueError('An existing answer or draft must be preserved')
            if self.prerequisite_status(content_id, instruction_scope)['blocked']:
                raise ValueError("请先学习前置内容，或明确选择已有前置知识。")
            checkpoint['initial_familiarity'] = {'version': 1, 'evidence': 'self_report', 'levels': levels,
                'route': 'full', 'selected_at_utc': now.isoformat(), 'diagnostic_state': 'not_requested',
                'source': intro_batch.CARD_SOURCE}
            checkpoint.pop('familiarity_draft', None)
            _teaching.update(db, row, checkpoint_json=_dump(checkpoint))
            claim(db, content_id, introduction_id=row['introduction_id'], now=now)
            _teaching.update(db, row, stage='bridge', guided_at_utc=now.isoformat(), retrieval_at_utc=now.isoformat())
            _teaching.ready_units(db, row)
            if fair_new:
                from .new_dispatch import completed
                completed(db, "course")
        return self.instruction_item(content_id, instruction_scope)

    # Probe first (design 02 section 4, 2026-11-21): probe_rules decides, learning_probe
    # reads and writes inside the transactions opened here.

    def open_new_scope(self, content_id, *, expected_introduction_id, enabled_kinds, instruction_scope=None,
                       now=None, fair_new=False):
        """Start an introduction scope that has no profile yet: a probe, teaching or an ordinary first check.

        The result is learning_probe.open_scope_in_transaction's: route "probe"
        (with the question id), "teach", "check", "wait" (no qualifying probe
        now; nothing written) or "existing" (nothing written).
        """
        from .learning_probe import open_scope_in_transaction
        from .course_planning import known_ids
        from .answer_input import system_japanese_enabled
        now = _utc(now)
        with self._db:
            self._transaction()
            return open_scope_in_transaction(self, content_id, instruction_scope=instruction_scope,
                expected_introduction_id=expected_introduction_id, enabled_kinds=list(enabled_kinds),
                known=known_ids(self.queries), system_japanese=system_japanese_enabled(self.store),
                now=now, fair_new=fair_new)

    def open_probe_question(self, content_id, ability, *, enabled_kinds, known, system_japanese_input):
        """A qualifying probe of a unit whose scope is an OPEN probe, else None (reads only).

        For the queue pass (course_planning.iter_question_plans): after an app
        start ended the queue, the unit is planned again as a probe, not with
        its ordinary plan. learning_probe.open_probe_plan decides.
        """
        from .learning_probe import open_probe_plan
        unit = self._db.execute("SELECT * FROM learning_unit WHERE content_id=? AND ability=?",
                                (content_id, _ability(ability))).fetchone()
        return open_probe_plan(self, content_id, ability, unit, enabled_kinds=enabled_kinds, known=known,
                               system_japanese=system_japanese_input)

    def probe_head(self, question_id, now=None):
        """A started probe question as a head, in the shape current() gives a question, when it can be
        answered at ``now``; None while a queue guard (an exposure wait, a floor) still holds it (read only)."""
        now = _utc(now)
        q = self.get_question(question_id)
        if q is None:
            raise KeyError(question_id)
        joined = self._db.execute("""SELECT u.*,q.draft_json,q.result_json,q.not_before_utc FROM learning_question q
            JOIN learning_unit u ON u.unit_id=q.unit_id WHERE q.question_id=?""", (question_id,)).fetchone()
        due = self.effective_question_due(dict(joined), self.get_question(question_id), now)
        if due and datetime.fromisoformat(due) > now:
            return None
        scope = self.ability_instruction_scope(q["target_id"], q["ability"])
        return {"content": self.instruction_item(q["target_id"], scope), "question": q, "draft": q["draft"],
                "result": q["result"], "type": "question", "stage": "check", "probe": True}

    def decline_probe(self, question_id, now=None):
        """「还不会」 on an open probe: the failed-diagnostic record without an answer, plus probe_declined.

        One transaction: the first answer is frozen as revealed (no rating, no
        FSRS write), the profile records the decline and the event is finalized.
        """
        from .probe_rules import DECLINED_KEY
        now = _utc(now)
        with self._db:
            self._transaction()
            row = self._db.execute("SELECT * FROM learning_question WHERE question_id=?", (question_id,)).fetchone()
            if row is None:
                raise KeyError(question_id)
            unit = self._db.execute("SELECT * FROM learning_unit WHERE unit_id=?", (row["unit_id"],)).fetchone()
            content = self.ability_instruction_row(unit["content_id"], unit["ability"])
            if row["status"] != "pending" or row["result_json"] is not None or not self._initial_diagnostic(content, unit):
                raise ValueError("This question is not an open probe")
            result = self._submit_in_transaction(question_id, "", None, outcome="revealed", now=now)
            if not result.get("diagnostic_needs_teaching"):
                raise ValueError("This question is not an open probe")
            content = self.ability_instruction_row(unit["content_id"], unit["ability"])
            checkpoint = _load(content["checkpoint_json"])
            checkpoint["initial_familiarity"][DECLINED_KEY] = True
            _teaching.update(self._db, content, checkpoint_json=_dump(checkpoint))
            return self._finalize_in_transaction(question_id, None, now=now)

    def probe_result_reduced(self, question_id, *, enabled_kinds):
        """Whether the right answer to ``question_id`` gets the reduced result page (reads only).

        probe_rules.reduced_result: the lesson is still probing and another
        ability of it than the question's own has a probe open or one that can
        be opened now, whether or not the question is itself a probe.
        """
        from .learning_probe import LessonView, next_probe_available
        from .probe_rules import reduced_result
        from .course_planning import known_ids
        from .answer_input import system_japanese_enabled
        unit = self._db.execute("""SELECT u.* FROM learning_question q JOIN learning_unit u ON u.unit_id=q.unit_id
            WHERE q.question_id=?""", (question_id,)).fetchone()
        if unit is None:
            return False
        view = LessonView(self, unit["content_id"])
        if not view.probing:
            return False
        return reduced_result(lesson_probing=True, next_probe_available=next_probe_available(
            self, view, unit["ability"], enabled_kinds=list(enabled_kinds), known=known_ids(self.queries),
            system_japanese=system_japanese_enabled(self.store)))

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
        unavailable = self.availability().unavailable_ids
        candidates = [item for item in saved
                      if item["id"] not in unavailable  # 08 section 7.3: no prerequisite overlay
                      and item["stage"] != "done" and not preserves_existing_route(self, item)
                      and item.get("checkpoint", {}).get("prerequisite_acknowledged") is not True]
        from .personal_graph import dependency_override
        for item in saved:
            revised = dependency_override(self, item)
            if item['id'] in unavailable:
                continue
            if revised and not revised['pending'] and not (item.get('checkpoint', {}).get('prerequisite_acknowledged') is True
                    and item.get('checkpoint', {}).get('prerequisite_version') == revised['version_id']):
                candidates.append(item)
        extra_targets = {cid for cid, value in _teaching.state_map(self._db).items()
            if cid not in unavailable and any(row['stage'] != 'done' and _load(row['checkpoint_json']).get('prerequisite_acknowledged') is not True
                   for row in value['additions'].values())}
        candidates = list({item['id']: item for item in [*candidates, *(row for row in saved if row['id'] in extra_targets)]}.values())
        if not candidates:
            return {"added": [], "target_ids": [], "ordered_ids": [], "unresolved": []}
        if catalogue is None:
            # Views: the plan copies (deepcopy) only the lessons it would enrol.
            from .learning_content import learning_item_views
            catalogue = learning_item_views()
        from types import SimpleNamespace
        projected = [{**row, 'depends_on': effective_dependencies(self,
            {**row, 'checkpoint': {}}, preserved=False)} if row['id'] in extra_targets else row for row in saved]
        view = SimpleNamespace(_db=self._db, store=self.store, queries=self.queries,
                               list_items=lambda: projected, get_item=self.get_item)
        plan = build_dependency_plan([item["id"] for item in candidates], catalogue, view,
                                     allow_unresolved=True)
        view = self.availability()
        unresolved = list(plan["unresolved"])
        enrollable = []
        for item in plan["items"]:
            if view.available(item["id"], {"dictionary_only": item.get("dictionary_only") is True}):
                enrollable.append(item)
            else:
                unresolved.append({"id": item["id"], "reason": "unavailable", "path": [item["id"]]})
        plan = {**plan, "items": enrollable, "unresolved": unresolved}
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
            self.require_available(content_id)
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

    def arrangement_orientation(self):
        """The learner's study orientation (design 12); a missing setting is THOROUGH."""
        from .arrangement.services.preferences import ArrangementPreferences
        return ArrangementPreferences(self.store).orientation()

    def prerequisite_status(self, content_id, instruction_scope=None, *, ready_ids=None):
        from .learning_dependencies import prerequisite_status
        if not instruction_scope:
            return prerequisite_status(self, content_id, ready_ids=ready_ids)
        from .learning_dependencies import TaughtRule, effective_dependencies, independent_ready_ids, _status
        item = self.instruction_item(content_id, instruction_scope)
        dependencies = effective_dependencies(self, item, preserved=False)
        ready_ids = independent_ready_ids(self) if ready_ids is None else ready_ids
        taught = TaughtRule(self)
        entries = [_status(self, cid, ready_ids, taught=taught) for cid in dependencies]
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
        # The decoded record is this call's own value: the overlays update it instead of copying it again.
        result = _load(row["content_json"])
        from .material_review import reviewed_item
        result = reviewed_item(result, owned=True)
        from .kana_rule_evidence import teaching_overlay
        result = teaching_overlay(result)
        from .personal_content_versions import resolve as personal_version
        result = personal_version(self.store, result, owned=True)
        result.update(content_id=row["content_id"], stage=row["stage"], introduction_id=row["introduction_id"],
                      checkpoint=_load(row["checkpoint_json"]), exposure_group=row["exposure_group"],
                      introduced_at_utc=row["introduced_at_utc"], retrieval_at_utc=row["retrieval_at_utc"])
        # J6 R4: units of an ability the lesson no longer lists stay stored (history) but leave this view,
        # so nothing plans, teaches or counts them; with none left the lesson is only read (R1).
        retired = [unit["ability"] for unit in units if unit and retired_ability(row["content_id"], unit["ability"])]
        units = [unit for unit in units if not unit or unit["ability"] not in retired]
        result["units"] = units
        if retired:
            result["retired_abilities"] = retired
        result["read_only"] = not units
        # Keep the authored ability list intact for template and dependency
        # validation. Only actual units are admitted to teaching/assessment.
        result["enrolled_abilities"] = [unit["ability"] for unit in units]
        # A word admitted before listening existed keeps its frozen teaching JSON.
        # Its later listening unit is real, so this read view must include it.
        missing = [ability for ability in result["enrolled_abilities"] if ability not in result.get("abilities", ())]
        if result.get("family") == "word" and missing == ["listening"]:
            result["abilities"] = [*result["abilities"], "listening"]
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

    def withdrawn_pending_ids(self):
        """Unanswered queued questions of a task form the app no longer asks, the soonest answerable first.

        learning_questions.withdrawn_task as one query: the heard-word kind
        with a kana-spelling variant number. Read only; the caller exchanges
        them through LearningReplacementStore, a bounded number per pass,
        so those without a stored earliest time and then the earliest stored
        times come first (the order is a priority, not a schedule).
        """
        from .word_sound_questions import AUDIO_WORD, KANA_VARIANT_BASE
        return [row[0] for row in self._db.execute(
            """SELECT question_id FROM learning_question WHERE status='pending' AND result_json IS NULL
               AND kind=? AND json_extract(snapshot_json,'$.variant')>=?
               ORDER BY not_before_utc IS NOT NULL,not_before_utc,rowid""",
            (AUDIO_WORD, KANA_VARIANT_BASE))]

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
        # No new question snapshot for unavailable content (08 section 7.3).
        unavailable = self.availability().unavailable_ids
        items = [self.get_item(row[0]) for row in rows if row[0] not in unavailable]
        # J6 R4: a lesson whose only unqueued units are of abilities it no longer lists needs nothing.
        return [item for item in items if item["enrolled_abilities"]]

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
            self.require_available(content_id)
            if checkpoint in {"intro_done", "guide", "guide_correct", "bridge", "check"} and self.prerequisite_status(content_id, instruction_scope)["blocked"]:
                raise ValueError("请先学习前置内容，或明确选择已有前置知识。")
            draft = _load(row["checkpoint_json"])
            from .learning_replacement import guide_write_guard
            guide_write_guard(draft, state, checkpoint, orientation=self.arrangement_orientation)
            # A guide verdict is new when this write gives one the record does not hold yet (design 03).
            judged_now = isinstance(state, dict) and "guide_correct" in state and any(
                state.get(key) != draft.get(key) for key in ("guide_correct", "guide_answer", "guide_question"))
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
            from .multi_cloze import validate_checkpoint as validate_cloze_checkpoint
            validate_cloze_checkpoint(content_id, row['revision'], draft)
            from .learning_practice_policy import validate_practice_guide
            validate_practice_guide(content_id, row["revision"], draft, item=_load(row["content_json"]),
                                    judged_now=judged_now)
            if checkpoint in {"intro_done", "guide"} and row["stage"] == "intro":
                _teaching.update(self._db, row, stage='guide')
            elif checkpoint in {"guide_correct", "bridge"} and row["stage"] == "guide":
                _teaching.update(self._db, row, stage='bridge', guided_at_utc=now.isoformat(),
                    retrieval_at_utc=(now+timedelta(seconds=_retrieval_delay(draft))).isoformat())
                _teaching.ready_units(self._db, row)
                if fair_new:
                    from .new_dispatch import completed
                    completed(self._db, "course")
            elif checkpoint == "check" and row["stage"] == "bridge":
                if self.retrieval_ready(row, now):
                    _teaching.update(self._db, row, stage='check')
            draft["last_checkpoint"] = checkpoint
            _teaching.update(self._db, row, checkpoint_json=_dump(draft))
        return self.instruction_item(content_id, instruction_scope)

    def retrieval_ready(self, row, now):
        """Read only: whether a bridge-stage teaching row (``row``) may face its retrieval check at ``now``.

        Public since design 13 S3 (the home's unqueued preview and the records page read it too).
        """
        if row["retrieval_at_utc"] and datetime.fromisoformat(row["retrieval_at_utc"]) <= now:
            return True
        if not row["guided_at_utc"]:
            return False
        if _probe_era(row["checkpoint_json"]):
            # Probe first (2026-11-21): three answers of other items (course or kana).
            return self.other_answers_since(row["content_id"], datetime.fromisoformat(row["guided_at_utc"]),
                                            limit=RETRIEVAL_OTHER_ANSWERS) >= RETRIEVAL_OTHER_ANSWERS
        # An intervening independent target, rather than another tap in this
        # same lesson, provides the alternative interleaving opportunity.
        return bool(self._db.execute("""SELECT 1 FROM learning_event e
            JOIN learning_unit u ON u.unit_id=e.unit_id WHERE u.content_id<>?
            AND e.role='primary' AND e.occurred_at_utc>? LIMIT 1""",
            (row["content_id"], row["guided_at_utc"])).fetchone())

    def probe_spacing_waits(self, content_id, ability, now):
        """Read only: whether this ability's lesson (probe-first flow) is taught and its first formal question
        still waits for the spacing (bridge stage, retrieval not ready yet; design 12 N6)."""
        if not content_id or not ability:
            return False
        row = self.ability_instruction_row(content_id, ability)
        return bool(row is not None and row["stage"] == "bridge" and row["guided_at_utc"]
                    and _probe_era(row["checkpoint_json"]) and not self.retrieval_ready(row, _utc(now)))

    def other_answers_since(self, content_id, since, *, limit=RETRIEVAL_OTHER_ANSWERS):
        """Answers of other items after ``since``, counted up to ``limit`` (read only).

        Course answers of other lessons (rated or not) and kana answers, by the
        instant they were finalized. The spacing of probe_rules reads it.
        """
        since = _utc(since).isoformat()
        course = self._db.execute("""SELECT COUNT(*) FROM (SELECT 1 FROM learning_event e
            JOIN learning_unit u ON u.unit_id=e.unit_id WHERE u.content_id<>?
            AND julianday(COALESCE(json_extract(e.result_json,'$.finalized_at_utc'),e.occurred_at_utc))>julianday(?)
            LIMIT ?)""", (content_id, since, limit)).fetchone()[0]
        if course >= limit:
            return course
        kana = self._db.execute("""SELECT COUNT(*) FROM (SELECT 1 FROM review_event
            WHERE julianday(COALESCE(json_extract(review_log_json,'$.finalized_at_utc'),occurred_at_utc))>julianday(?)
            LIMIT ?)""", (since, limit - course)).fetchone()[0]
        return course + kana

    def append_questions(self, questions, now=None, *, exposure_tables=None):
        """``exposure_tables``: exposure_tables() of the queue pass this append belongs to."""
        with self._db:
            self._transaction()
            return self._append_questions_in_transaction(questions, now=now, exposure_tables=exposure_tables)

    def _append_questions_in_transaction(self, questions, now=None, *, exposure_tables=None):
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
            self.require_available(cid)
            unit = self._db.execute("SELECT * FROM learning_unit WHERE content_id=? AND ability=?", (cid, ability)).fetchone()
            if unit is None:
                raise ValueError("Question must target an explicitly enrolled ability")
            from .retention_session import reservation
            if reservation(self.store, unit["unit_id"]) is not None:
                continue
            if self._db.execute("SELECT 1 FROM learning_question WHERE unit_id=? AND status='pending'", (unit["unit_id"],)).fetchone():
                continue
            qid = _identifier(q.get("id", q.get("question_id", uuid4().hex)))
            from .practice_round import next_question_id
            from .learning_replacement import question_receipt
            # An id exchanged away (its row completed with a replacement receipt) is retired like an
            # archived one: the unit of a replacement that an app start archived gets a question again.
            qid = next_question_id(self.store, qid,
                                   retired=lambda value: question_receipt(self.store, value) is not None)
            if self._db.execute("SELECT 1 FROM learning_question WHERE question_id=?", (qid,)).fetchone():
                continue
            if not isinstance(q.get("prompt"), str) or not isinstance(q.get("answer"), str):
                raise ValueError("Invalid question text")
            if q.get("guided") or q.get("stage") in {"intro", "guide"}:
                raise ValueError("Guided questions cannot enter the independent review queue")
            q.update(id=qid, question_id=qid, target_id=cid, ability=ability)
            from .pitch_practice import validate_snapshot
            validate_snapshot(q)
            from .multi_cloze import validate_snapshot as validate_cloze_snapshot
            validate_cloze_snapshot(q)
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
                 self.question_not_before(unit, q) if exposure_tables is None else
                 self.question_not_before(unit, q, tables=exposure_tables), content["exposure_group"]))
            added.append(qid)
        return added

    def question_not_before(self, unit, question, *, now=None, exposure_times=None, tables=None):
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
            exposure_times=exposure_times, tables=tables), effective_due, cooldown)

    def planned_question_due(self, unit, question, *, now=None, exposure_times=None, tables=None):
        """When a planned (not yet queued) ``question`` of ``unit`` will be served (design 12 N2, N10).

        The question is read as the append would store it (question_not_before
        as its floor) and then exactly as a queued one is read
        (effective_question_due): the stored floor alone keeps the stored due, so
        a recent unit's short interval served after one day (N2) is the time
        here as it is on the wait page once the question is queued.
        """
        floor = self.question_not_before(unit, question, now=now, exposure_times=exposure_times, tables=tables)
        return self.effective_question_due(unit, {**dict(question), "not_before_utc": floor}, now=now,
                                           exposure_times=exposure_times, tables=tables)

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
        return self._with_exposure_tables(result, now)

    def _with_exposure_tables(self, result, now):
        from .tutor_store import exposure_times as tutor_exposure_times
        result.tutor = tutor_exposure_times(self.store, now)
        from .retention_check import exposure_times as retention_exposure_times
        result.retention = retention_exposure_times(self.store, now)
        from .retention_schedule import active_due
        result.confirmation = active_due(self.store, now)
        result.additional_teaching = _teaching.exposure_times(self._db, now)
        from .arrangement.sources.recent import RecentFirsts
        result.first_rated = RecentFirsts(self.store)
        return result

    def _first_rated(self, exposure_times=None, tables=None):
        """The pass's RecentFirsts (first formal ratings, read on demand), or a new one for a single read."""
        firsts = getattr(exposure_times, "first_rated", None) or getattr(tables, "first_rated", None)
        if firsts is None:
            from .arrangement.sources.recent import RecentFirsts
            firsts = RecentFirsts(self.store)
        return firsts

    def serviceable_ability_due(self, unit, question, now=None, *, exposure_times=None, tables=None):
        """_ability_due as the course queue serves it: a recent unit's short interval after one day (design 12 N2).

        The stored due is never changed; rules/recent.serviceable_due decides.
        """
        from .arrangement.sources.recent import serviceable
        return serviceable(unit, _ability_due(unit, question), self._first_rated(exposure_times, tables),
                           _utc(now))[0]

    def exposure_tables(self, now=None):
        """The exposure records every question of one queue pass is checked against, read once.

        The tutor, check, confirmation and added-teaching tables of
        content_exposure_times, without its map of feedback exposures: an
        append (question_not_before with ``tables``) still reads each lesson's
        own feedback exposure itself, exactly as it does without them. Reading
        these tables again for every appended question scanned every saved
        exposure record each time. Valid for one synchronous pass that writes
        no exposure.
        """
        return self._with_exposure_tables(_ExposureTimes(), _utc(now))

    def effective_question_due(self, unit, question, now=None, *, exposure_times=None, tables=None):
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
                tutor = getattr(tables, "tutor", None)  # exposure_tables() of the caller's queue pass
            if tutor is None:
                tutor = tutor_exposure_times(self.store, now)
            exposure = _latest_due(exposure, tutor.get(unit["content_id"]))
            from .retention_check import exposure_times as retention_exposure_times
            retention = getattr(exposure_times, 'retention', None)
            if retention is None:
                retention = getattr(tables, 'retention', None)
            if retention is None:
                retention = retention_exposure_times(self.store, _utc(now))
            exposure = _latest_due(exposure, retention.get((unit['content_id'], unit['ability'])))
            additional = getattr(exposure_times, 'additional_teaching', None)
            if additional is None:
                additional = getattr(tables, 'additional_teaching', None)
            if additional is None:
                additional = _teaching.exposure_times(self._db, _utc(now))
            exposure = _latest_due(exposure, additional.get((unit['content_id'], unit['ability'])))
        scheduled = _ability_due(unit, question)
        floor = question.get("not_before_utc") or unit.get("not_before_utc")
        due = self.scheduled_question_due(unit, question, now, exposure_times=exposure_times, tables=tables)
        if due != scheduled:   # an earlier active confirmation replaced the schedule, and its exact floor
            if floor and datetime.fromisoformat(floor) == datetime.fromisoformat(scheduled): floor = None
            scheduled = due
        return _latest_due(scheduled, floor, exposure)

    def scheduled_question_due(self, unit, question, now=None, *, exposure_times=None, tables=None):
        """The unit's due as the dispatch schedules it, without the independence guards.

        The ability's due with this question (_ability_due: the stored due,
        which carries any bounded auxiliary adjustment, or an earlier
        free-retrieval probe) or an earlier active confirmation
        (retention_schedule.active_due), whichever is earlier, read as the
        queue serves it (design 12 N2: a recent unit with an interval of at
        most three days is due once a day has passed since its last formal
        rating; serviceable_due). The floors
        (not_before) and the feedback, tutor, retention and teaching exposure
        waits are left out: they delay serving a question, they do not make a
        due unit "not due" (design 12 E3). effective_question_due adds them on
        top of this value. ``exposure_times``/``tables``: a queue pass's tables,
        read for their confirmation map and first ratings only.
        """
        return self.serviceable_due(unit, question, now, exposure_times=exposure_times, tables=tables)[0]

    def serviceable_due(self, unit, question, now=None, *, exposure_times=None, tables=None):
        """(scheduled_question_due, whether the unit is due at ``now`` only by the N2 reading).

        The second value is what PracticeCredit reads as an early formal
        rating (StateView.early): the stored due still lies ahead.
        """
        scheduled = _ability_due(unit, question)
        from .retention_schedule import active_due
        confirmation = getattr(exposure_times, 'confirmation', None)
        if confirmation is None: confirmation = getattr(tables, 'confirmation', None)
        if confirmation is None: confirmation = active_due(self.store, _utc(now))
        earlier = confirmation.get(unit.get('unit_id'))
        if earlier and scheduled and datetime.fromisoformat(earlier) < datetime.fromisoformat(scheduled):
            scheduled = earlier
        from .arrangement.sources.recent import serviceable
        return serviceable(unit, scheduled, self._first_rated(exposure_times, tables), _utc(now))

    def record_practice_exposure(self, event_id, question, result, *, now=None):
        """A practice-only answer showed this lesson's answer (design 12 E5, 10 (6), AC17).

        In its own transaction: the lesson's feedback exposure (the stamp a
        submitted answer sets, which effective_question_due turns into the
        existing short wait before any formal question of the lesson) and one
        evidence row "见过" (source course, role exposure, weight 0, never
        eligible). No unit, question, event or due is written.
        """
        now = _utc(now)
        if self._db.execute("SELECT 1 FROM learning_content WHERE content_id=?",
                            (question.get("target_id"),)).fetchone() is None:
            raise ValueError("Unknown learning content")
        from .learning_evidence import LearningEvidenceStore
        with self._db:
            self._transaction()
            self._mark_feedback_exposed(question["target_id"], now)
            LearningEvidenceStore(self.store).record_attempt(event_id, dict(question), result,
                                                             source="course", role="exposure", now=now)

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
        from .practice_archive import archived_question
        archive = archived_question(self.store, question_id)
        if archive:
            result.update(status="archived", round_archive=archive)
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

        Returns None when an unopened guide of this kind has only a retired
        example and no replacement whose background is ready; other invalid
        data still raises.

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
            from .learning_questions import practice_templates, retired_practice_variants
            system_input = system_japanese_enabled(self.store)
            templates = practice_templates(target)
            variants = (templates or {}).get(kind, [])
            is_retired = bool(variants and (kind, variant % len(variants)) in
                              retired_practice_variants(target, templates))
            if is_retired or not available(question, system_japanese=system_input):
                from .learning_practice_policy import practice_candidates
                from .course_planning import known_ids
                for candidate in practice_candidates(target, [kind],
                        known_background_ids=known_ids(self.store.queries),
                        system_japanese=system_input):
                    if candidate.get('relaxed') or candidate.get('top_up'):
                        continue  # A guide never shows unmet material nor the formal top-up (design 11, D4).
                    return build_learning_question(target, kind, guided=True, variant=candidate['variant'])
                if is_retired:
                    # Not bad data: this kind's example was retired and every
                    # replacement still needs unlearned background. The caller
                    # tries its next guide kind or offers direct practice.
                    from .diagnostic_events import record_error
                    record_error("learning_guide", "read", GuidedQuestionUnavailable(kind))
                    return None
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
            # The verdict stored with the answer, never judged again (design 03); an older guide without one
            # is judged as before.
            stored = checkpoint.get("guide_correct")
            right = (stored if type(stored) is bool
                     else grade_learning_answer(question, checkpoint.get("guide_answer", "")).get("status") == "correct")
            if not right:
                # Design 12 Q1 (c): under breadth first a step with a recorded
                # wrong answer may be marked seen; the shape stays the same.
                from .arrangement.rules.gates import guide_may_continue
                if not (checkpoint.get("guide_answer") is not None
                        and guide_may_continue(self.arrangement_orientation(), answered_wrong=True)):
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
            from .multi_cloze import validate_draft as validate_cloze_draft
            validate_cloze_draft(_load(row['snapshot_json']), old)
            self._db.execute("UPDATE learning_question SET draft_json=? WHERE question_id=?", (_dump(old), question_id))
        return self.get_question(question_id)

    def _pending(self, enabled_kinds=None, *, projection=None):
        """Internal compatibility alias; external readers use the public API."""
        yield from self.iter_pending_questions(enabled_kinds, projection=projection)

    def iter_pending_questions(self, enabled_kinds=None, *, projection=None):
        """Full pending rows in insertion order, excluding paused/reserved units.

        Rows of unavailable content (08 section 7.2: orphaned, blocked by a
        missing library dependency, retired) are skipped; they stay pending.
        No questions are generated or decoded. The iterator belongs to this
        connection/thread and must be consumed before closing the store.
        """
        kinds = set(enabled_kinds) if enabled_kinds is not None else None
        unavailable = self.availability(projection=projection).unavailable_ids
        for row in self._db.execute("""SELECT q.*,u.content_id,u.ability,u.suspended,u.instruction_ready,
                u.due_at_utc,u.probe_due_at_utc,u.review_count,u.scheduler_state_json
                FROM learning_question q JOIN learning_unit u ON u.unit_id=q.unit_id
                WHERE q.status='pending' AND NOT EXISTS (SELECT 1 FROM app_setting a
                    WHERE a.key='retention_reservation_v1:' || q.unit_id) ORDER BY q.rowid"""):
            if row["suspended"] or kinds is not None and row["kind"] not in kinds or row["content_id"] in unavailable:
                continue
            if retired_ability(row["content_id"], row["ability"]):
                continue   # J6 R4: an ability the lesson no longer lists is never served
            yield row

    def iter_rated_units(self, enabled_kinds, *, projection=None):
        """Formally rated units extra practice may offer, in unit order, each once (design 12 E10).

        The learner's own exclusions and what can be shown, never time: not suspended, no retention reservation,
        content available, the ability not retired; a unit with a pending question when that question's kind is
        enabled (the row carries it: q_kind, q_snapshot_json, q_not_before_utc, question_id), one without (every
        app start ends the pending course questions; a sealed round queues none) when some enabled kind practises
        its ability (the practice deck plans one on the spot). Read only; the iterator belongs to this connection.
        """
        from .learning_questions import KIND_ABILITIES
        kinds = set(enabled_kinds)
        abilities = {KIND_ABILITIES[kind] for kind in kinds if kind in KIND_ABILITIES}
        unavailable = self.availability(projection=projection).unavailable_ids
        rows = self.queries.rated_unit_rows()
        try:
            for row in rows:
                if row["content_id"] in unavailable or retired_ability(row["content_id"], row["ability"]):
                    continue
                if (row["q_kind"] not in kinds) if row["question_id"] is not None else row["ability"] not in abilities:
                    continue
                yield row
        finally:
            rows.close()

    def dispatch_pending(self, enabled_kinds=None, *, projection=None):
        """Read small sort fields; hydrate only the selected question later.

        Preserve absent-vs-explicit-null input_mode, since the former falls
        back to response. Queue snapshots, drafts and result bytes are never
        changed by this projection; _pending stays the full-row reader.
        Read only; public since design 13 S3 (the home's unqueued preview reads it).
        """
        kinds = set(enabled_kinds) if enabled_kinds is not None else None
        unavailable = self.availability(projection=projection).unavailable_ids
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
            if row['suspended'] or kinds is not None and row['kind'] not in kinds or row['content_id'] in unavailable:
                continue
            if retired_ability(row['content_id'], row['ability']):
                continue   # J6 R4
            yield row

    def current(self, enabled_kinds=None, now=None, *, fair_new=False, teaching_only=False,
                teaching_content_ids=None, include_unassessed=False, prefer_unassessed=False,
                projection=None, skip_teaching_ids=None, scope_content_ids=None,
                only_recent=False) -> LearningCurrent | None:
        """Read a tagged dispatch state without consuming or rebuilding questions.

        Normal mode returns a course head or an explicit blocking/wait state;
        None means no available work. Teaching-only probes return eligible heads
        or None (no eligible teaching head), omitting waiting/disabled states.
        include_unassessed requires teaching_only and unrestricted content IDs;
        prefer_unassessed chooses between its additional/introduction pools.
        fair_new changes dispatch priority, never saved order or due times.
        projection (read_operation) shares unchanged reads within one action;
        without it every read is live, exactly as before.
        skip_teaching_ids: content IDs whose intro/guide head the learner chose
        to pass over for now (process-local UI choice); their reviews stay.
        scope_content_ids: the learner's focus scope (design 12 H3; content
        ids), None for no scope. It adds one component (0 inside, 1 outside)
        after the tier to the key that orders the course lane when a round is
        collected (tiers 3 and up; an opened teaching block counts as inside),
        and between the two new-work pools of an include_unassessed probe the
        pool whose head is inside goes first. The frozen cross-lane priority
        (dispatch_priority) never has it. Once the round is sealed or finished
        the order is frozen, so it is dropped (also from the read_operation
        key, so a worker's seeded result keeps matching), except for the
        include_unassessed pool choice.
        only_recent: only the ordinary due reviews (tier 3) of recent units, in
        the course order (design 12 N4 (ii): the first due review of a recent
        state is served before the share rule, also when a scope or the frozen
        order puts other rows before it); every other row is passed over.
        The learner's review filter of the running "继续学习" (design 12 H8-H10;
        arrangement.services.review_filter.serving_filter, None outside one)
        passes over every row of tier 3 and up whose lesson it does not allow,
        also in a sealed round; tiers 0-2 are served wherever they are.
        """
        from .arrangement.services.review_filter import serving_filter
        serve = serving_filter(self.store)
        skip = frozenset(skip_teaching_ids or ())
        if include_unassessed and (not teaching_only or teaching_content_ids is not None):
            raise ValueError("New-ability service requires an unrestricted teaching probe")
        scope = self._effective_scope(scope_content_ids, include_unassessed)
        if projection is not None:
            now = projection.moment(now)
            key = self.current_key(enabled_kinds, now, fair_new=fair_new, teaching_only=teaching_only,
                teaching_content_ids=teaching_content_ids, include_unassessed=include_unassessed,
                prefer_unassessed=prefer_unassessed, skip_teaching_ids=skip, scope_content_ids=scope,
                only_recent=only_recent, serve_filter=serve)
            return projection.result(key, lambda: self._current(enabled_kinds, now, fair_new=fair_new,
                teaching_only=teaching_only, teaching_content_ids=teaching_content_ids,
                include_unassessed=include_unassessed, prefer_unassessed=prefer_unassessed,
                projection=projection, skip_teaching_ids=skip, focus_scope=scope, only_recent=only_recent,
                serve=serve))
        return self._current(enabled_kinds, now, fair_new=fair_new, teaching_only=teaching_only,
            teaching_content_ids=teaching_content_ids, include_unassessed=include_unassessed,
            prefer_unassessed=prefer_unassessed, skip_teaching_ids=skip, focus_scope=scope,
            only_recent=only_recent, serve=serve)

    def _effective_scope(self, scope_content_ids, include_unassessed):
        """The focus scope where it can still act: None without one, or once the course order is frozen."""
        if scope_content_ids is None:
            return None
        from .practice_round import finished, sealed
        if not include_unassessed and (sealed(self.store) or finished(self.store)):
            return None
        return frozenset(scope_content_ids)

    @staticmethod
    def current_key(enabled_kinds, now, *, fair_new=False, teaching_only=False, teaching_content_ids=None,
                    include_unassessed=False, prefer_unassessed=False, skip_teaching_ids=None,
                    scope_content_ids=None, only_recent=False, serve_filter=None):
        """read_operation key of one current() call (see _LearningRead.seed).

        ``scope_content_ids`` is the effective scope of that call (current() drops it once the order is frozen);
        ``serve_filter`` the review filter it ran under (design 12 H10), None for none.
        """
        key = ("current", None if enabled_kinds is None else tuple(enabled_kinds), _utc(now).isoformat(),
               bool(fair_new), bool(teaching_only),
               None if teaching_content_ids is None else frozenset(teaching_content_ids),
               bool(include_unassessed), bool(prefer_unassessed))
        # Unchanged key without a skip or a scope, so seeded worker results still match.
        key = key + (frozenset(skip_teaching_ids),) if skip_teaching_ids else key
        key = key + ("only-recent",) if only_recent else key
        key = key + (("serve", serve_filter),) if serve_filter is not None else key
        return key + (("scope", frozenset(scope_content_ids)),) if scope_content_ids is not None else key

    @classmethod
    def reader(cls, store):
        """Read-only view on a worker connection; never creates tables or takes a write lock.

        The UI connection already ran ensure_schema; a missing table raises
        instead of being created here.
        """
        names = {row[0] for row in store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (%s)" % ",".join("?" * len(TABLES)),
            TABLES)}
        if names != set(TABLES):
            raise RuntimeError("Learning tables are not initialized on this database")
        view = cls.__new__(cls)
        view.store = store
        return view

    def _dispatch_content_rows(self):
        """Small per-content stage fields read once per dispatch pass."""
        return {content["content_id"]: content for content in self._db.execute(
            """SELECT content_id,stage,checkpoint_json,retrieval_at_utc,guided_at_utc
               FROM learning_content""")}

    def read_only_state(self):
        """read_only_lessons of this learner (J6 R1-R4): {content_id: {position, stage, read}}; read only."""
        return read_only_lessons(self._db)

    def read_only_unread(self, *, projection=None):
        """[(content_id, position)] of enrolled lessons that are only read and not read yet (J6 R1), in order.

        Content of an unavailable library is left out (08 section 7.2), as its questions are.
        """
        unavailable = self.availability(projection=projection).unavailable_ids
        return sorted(((cid, value["position"]) for cid, value in read_only_lessons(self._db).items()
                       if not value["read"] and cid not in unavailable), key=lambda pair: pair[1])

    def _with_read_only_heads(self, rows, projection=None):
        """The pending rows with a stand-in row for each unread read-only lesson at its place in the route.

        A stand-in goes right before the first row of a lesson enrolled after it, so the stable priority
        sort keeps it in route order among the first teachings; without unread read-only lessons the rows
        pass through unchanged.
        """
        unread = self.read_only_unread(projection=projection)
        if not unread:
            return rows
        positions = {row[0]: row[1] for row in self._db.execute("SELECT content_id,rowid FROM learning_content")}
        merged, waiting = [], list(unread)
        for row in rows:
            position = positions.get(row["content_id"], 0)
            while waiting and waiting[0][1] < position:
                merged.append(_read_only_stand_in(*waiting.pop(0)))
            merged.append(row)
        merged.extend(_read_only_stand_in(*pair) for pair in waiting)
        return merged

    def complete_read_only(self, content_id, *, expected_introduction_id, now=None, fair_new=False):
        """「知道了」 on a lesson that is only read (J6 R1): its stage is done, its route section complete.

        Idempotent: a lesson already read changes nothing (False). No unit, question, event or due date is
        written; the exposure stamp is kept if one was saved.
        """
        now = _utc(now)
        with self._db:
            self._transaction()
            row = self._db.execute("SELECT * FROM learning_content WHERE content_id=?", (content_id,)).fetchone()
            if row is None:
                raise KeyError(content_id)
            if row["introduction_id"] != expected_introduction_id:
                raise ValueError("The introduction is no longer current")
            if content_id not in read_only_lessons(self._db):
                raise ValueError("This lesson has abilities to practise")
            if row["stage"] == "done":
                return False
            self.require_available(content_id)
            checkpoint = _load(row["checkpoint_json"]) or {}
            checkpoint.setdefault("instruction_exposed_at_utc", now.isoformat())
            from .explicit_learning_start import claim
            claim(self._db, content_id, introduction_id=row["introduction_id"], now=now)
            self._db.execute("UPDATE learning_content SET stage='done',checkpoint_json=? WHERE content_id=?",
                             (_dump(checkpoint), content_id))
            if fair_new:
                from .new_dispatch import completed
                completed(self._db, "course")
        return True

    def _current(self, enabled_kinds, now, *, fair_new, teaching_only, teaching_content_ids,
                 include_unassessed, prefer_unassessed, projection=None, skip_teaching_ids=frozenset(),
                 focus_scope=None, only_recent=False, serve=None):
        # focus_scope: current()'s effective focus scope (not an instruction scope, which the loop below names "scope").
        # serve: the review filter of the running "继续学习" (design 12 H10), None for none.
        from .learning_dependencies import independent_ready_ids, prerequisite_status, has_started_answer
        from .review_dispatch import review_priority, reviews_today
        now = _utc(now)
        review_day, daily_counts = reviews_today(self.store, now, course=True)
        waiting, blocked, dependencies = [], [], {}
        blocked_material = []
        ready_ids = projection.ready_ids() if projection is not None else independent_ready_ids(self)
        exposure_times = (projection.exposure_times(now) if projection is not None
                          else self.content_exposure_times(now))
        firsts = self._first_rated(exposure_times)
        from .arrangement.rules.recent import REVIEW_TIER, ordered_priority
        def has_text(value):
            return isinstance(value, str) and bool(value.strip())

        ability_states = projection.ability_states() if projection is not None else _teaching.state_map(self._db)
        content_rows = projection.content_rows() if projection is not None else self._dispatch_content_rows()
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
            if _read_only_row(row):
                # J6 R1: new content, ordered with every other first teaching.
                started = bool(_load(content_rows[row["content_id"]]["checkpoint_json"]).get("instruction_exposed_at_utc"))
                return (4, -1 if started else 0) if fair_new else (5, 0)
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
            # Design 12 N2: a recent unit's short interval is served after one day (serviceable_ability_due).
            return review_priority(row, due=self.serviceable_ability_due(row, _load(row["snapshot_json"]), now,
                                                                         exposure_times=exposure_times),
                now=now, day_start=review_day, reviewed_today=daily_counts.get(row["unit_id"], 0))

        def recent_review(row, priority):
            return priority[0] == REVIEW_TIER and firsts.recent(row["unit_id"], now)

        # A newly due step can interrupt untouched new lessons, but never a
        # frozen answer or real saved input. Sorting is read-only and stable;
        # question snapshots and their original insertion order stay intact.
        def service_priority(row):
            priority = dispatch_priority(row)
            from .practice_round import frozen_priority
            frozen_priority(self.store, "cross-course", row['question_id'], priority)
            held = priority == (4, -1)
            # Design 12 N1: inside the due reviews, recent units first (both orientations); the scope stays in front.
            priority = ordered_priority(priority, recent_review(row, priority))
            if focus_scope is not None:
                # Design 12 H3: inside the focus scope first, in this collection key only.
                from .arrangement.rules.focus import scoped_priority, sort_component
                priority = scoped_priority(priority, sort_component(row['content_id'], focus_scope), held=held)
            if not include_unassessed or priority[0] < 4:
                return (0, priority)
            category = unassessed(row)
            return (int(category != prefer_unassessed), priority)

        service_heads = {}
        def chosen_service_head():
            preferred = "additional" if prefer_unassessed else "introduction"
            opened = service_heads.get("introduction", {}).get("new_dispatch_continue")
            selected_pool = "introduction" if opened else preferred if preferred in service_heads else next(iter(service_heads))
            inside = [pool for pool, head in service_heads.items() if head["content"]["id"] in (focus_scope or ())]
            if focus_scope is not None and not opened and len(service_heads) == 2 and len(inside) == 1:
                # Design 12 H2: of the two new-work pools, the head inside the focus scope is taught first.
                selected_pool = inside[0]
            return {**service_heads[selected_pool], "selected_course_pool": selected_pool,
                "service_pool_candidates": {pool: {
                    "content_id": head["content"]["id"], "question_id": head["question"]["id"],
                    "ability": head["question"]["ability"], "kind": head["question"]["kind"]}
                    for pool, head in service_heads.items()}}

        from .practice_round import freeze_course_rows
        from .learning_probe import GateReader
        gate_closed = self._batch_gate_reader(enabled_kinds)
        probe_gate = GateReader(self, now)
        pending_rows = list(self.dispatch_pending(enabled_kinds, projection=projection))
        firsts.prime(row["unit_id"] for row in pending_rows if row["review_count"])
        # J6 R1: unread read-only lessons join as stand-in rows at their route place (never recent work).
        rows = self._with_read_only_heads(pending_rows, projection)
        for row in freeze_course_rows(self.store, rows, service_priority):
            if only_recent and (_read_only_row(row) or row["result_json"] is not None
                                or not recent_review(row, dispatch_priority(row))):
                continue
            if serve is not None and not serve.serves(row["content_id"], dispatch_priority(row)[0], row["ability"]):
                continue   # design 12 H8, H9: left out by the learner's filter from tier 3 on (section 22: not
                           # work started by hand before its first formal rating)
            content_row = content_row_for(row)
            if _read_only_row(row):
                # J6 R1: a lesson that is only read, not read yet: its explanation page with 「知道了」.
                cid = row["content_id"]
                if (cid in skip_teaching_ids or (cid, None) in skip_teaching_ids
                        or teaching_content_ids is not None and cid not in teaching_content_ids
                        or include_unassessed and "introduction" in service_heads):
                    continue
                content = self.instruction_item(cid)
                result = {"content": content, "question": read_only_head_question(cid), "draft": None,
                          "result": None, "dispatch_priority": dispatch_priority(row), "read_only": True}
                if (cid, None) not in dependencies:
                    dependencies[(cid, None)] = self.prerequisite_status(cid, None, ready_ids=ready_ids)
                if dependencies[(cid, None)]["blocked"]:
                    blocked.append({**result, "dependency_status": dependencies[(cid, None)],
                                    "prerequisites": dependencies[(cid, None)]["missing"]})
                    continue
                if fair_new:
                    result["new_dispatch_continue"] = False   # one page; nothing to continue
                head = {**result, "type": "instruction", "stage": content["stage"]}
                if not include_unassessed:
                    return head
                service_heads.setdefault("introduction", head)
                if len(service_heads) == 2:
                    return chosen_service_head()
                continue
            if _paused_practice_guide(content_row, enabled_kinds):
                continue
            if gate_closed(row):
                # 四之二: a later ability of a batch item is not yet open.
                continue
            held = probe_gate(row, content_row)
            if held is not None:
                # Probe first (2026-11-21): an earlier probe is open, teaching comes first, or the spacing waits.
                if held != "hold" and not teaching_only:
                    waiting.append(held)
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
            if row["review_count"] and firsts.recent(row["unit_id"], now):
                # Design 12 N3/N4: a recent state; its due work is served before the share rule, never a candidate.
                result["recent_unit"] = True
                if recent_review(row, result["dispatch_priority"]):
                    result["recent_review"] = True
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
                # A whole lesson (its id) or one of its scopes ((id, scope): no qualifying probe yet).
                if content["id"] in skip_teaching_ids or (content["id"], scope) in skip_teaching_ids:
                    continue
                if serve is not None and serve.learned_only:
                    continue   # design 12 E11: 加练 never serves a teaching block, also of an added ability
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
            if content["stage"] == "bridge" and not self.retrieval_ready(content_row, now):
                waiting.append(content["retrieval_at_utc"])
                continue
            due = self.effective_question_due(row, q, now, exposure_times=exposure_times)
            if due and datetime.fromisoformat(due) > now and q["result"] is None:
                waiting.append(due)
                continue
            if unassessed(row):
                result.update(protected_unassessed_ability=True, service_reason="first_ability_check",
                              new_dispatch_continue=False)
            if q["result"] is None and self._initial_diagnostic(content_row, row):
                result["probe"] = True   # its page shows no aid on the item itself (probe_rules)
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
            return {'type': 'content_blocked', 'question_ids': blocked_material, 'counts': self.counts(enabled_kinds, now, projection=projection)}
        if blocked:
            return {**blocked[0], "type": "prerequisite", "counts": self.counts(enabled_kinds, now, projection=projection),
                    "due_at_utc": min(waiting) if waiting else None}
        if waiting:
            return {"type": "waiting", "due_at_utc": min(waiting), "counts": self.counts(enabled_kinds, now, projection=projection)}
        counts = self.counts(enabled_kinds, now, projection=projection)
        if counts.get('diagnostic'):
            owned = self._db.execute("SELECT json_extract(value_json,'$.episode_id') FROM app_setting WHERE key GLOB 'retention_reservation_v1:*' LIMIT 1").fetchone()
            return {'type': 'diagnostic', 'episode_id': owned[0], 'counts': counts}
        if counts["disabled"] or counts["unavailable_content"]:
            return {"type": "disabled", "counts": counts}
        if counts["library_unavailable"]:
            # 08 section 8.2: only content of a disabled/missing library is left.
            return {"type": "content_blocked", "question_ids": [], "reason": "library_disabled", "counts": counts}
        return None

    def counts(self, enabled_kinds=None, now=None, *, projection=None):
        """Home/dispatch counts; projection shares one action's unchanged reads.

        Under the review filter of a running "继续学习" or home refresh (design 12 H10; serving_filter) the work
        it leaves out from tier 3 on is counted nowhere here: the home shows it apart.
        """
        from .arrangement.services.review_filter import serving_filter
        serve = serving_filter(self.store)
        if projection is not None:
            now = projection.moment(now)
            key = ("counts", None if enabled_kinds is None else tuple(enabled_kinds), now.isoformat())
            key = key + (("serve", serve),) if serve is not None else key
            return projection.result(key, lambda: self._counts(enabled_kinds, now, projection=projection, serve=serve))
        return self._counts(enabled_kinds, now, serve=serve)

    def _filter_tier(self, row, now, *, exposure_times, review_day, daily_counts):
        """The dispatch tier of a pending row as far as the review filter asks (0-2 are always served, H8):
        0 feedback, 1 a started answer, 2 a due short step, else 3 (never fewer than _current's tiers)."""
        from .learning_dependencies import has_started_answer
        from .review_dispatch import review_priority
        if row["result_json"] is not None:
            return 0
        if row["instruction_ready"] and has_started_answer(_load(row["draft_json"])):
            return 1
        if not row["review_count"]:
            return 3
        due = self.serviceable_ability_due(row, _load(row["snapshot_json"]), now, exposure_times=exposure_times)
        return max(2, review_priority(row, due=due, now=now, day_start=review_day,
                                      reviewed_today=daily_counts.get(row["unit_id"], 0))[0])

    def _counts(self, enabled_kinds, now, *, projection=None, serve=None):
        from .learning_dependencies import independent_ready_ids, prerequisite_status
        from functools import lru_cache
        from types import SimpleNamespace
        # Invocation-local read projection: prerequisites use content and SQL
        # evidence, never units or per-ability attempt counts. No cross-call cache: each lesson's record and
        # its lesson for the prerequisites are read once for this call (the visible rows revisit a lesson
        # after others, which a small cache lost).
        @lru_cache(maxsize=None)
        def content_row(content_id):
            return self._db.execute("SELECT * FROM learning_content WHERE content_id=?", (content_id,)).fetchone()
        @lru_cache(maxsize=None)
        def dependency_item(content_id):
            content = content_row(content_id)
            return None if content is None else self._item(content, [])
        # store: personal_graph.dependency_override reads head/hold settings
        # through it (same per-key reads; an attached read operation applies).
        dependencies_view = SimpleNamespace(_db=self._db, store=self.store, queries=self.queries,
                                            get_item=dependency_item)
        now = _utc(now)
        # 08 section 8.2: unavailable content (library disabled/missing,
        # dependency missing, retired) is neither ready, waiting, teaching,
        # disabled nor unavailable_content; it is counted on its own.
        availability = self.availability(projection=projection)
        library_unavailable = availability.unavailable_ids
        read_only = self.read_only_state()
        # J6 R3: a lesson that is only read has no question by design; it is new content, not "unavailable".
        unfinished_without_pending = [row[0] for row in self._db.execute("""SELECT c.content_id FROM learning_content c
                    WHERE c.stage<>'done' AND NOT EXISTS (SELECT 1 FROM learning_question q JOIN learning_unit u
                    ON u.unit_id=q.unit_id WHERE u.content_id=c.content_id AND q.status='pending')""")
                    if row[0] not in read_only]
        counts = {"ready": 0, "waiting": 0, "instruction": 0, "pending": 0, "disabled": 0,
                  "blocked": 0, "blocked_content": 0, "blocked_material": 0, "blocked_input": 0,
                  "content": self._db.execute("SELECT COUNT(*) FROM learning_content").fetchone()[0], "next_due_at_utc": None,
                  "unavailable_content": sum(cid not in library_unavailable for cid in unfinished_without_pending)}
        visible = {row["question_id"]: row for row in self._pending(enabled_kinds, projection=projection)}
        counts["pending"] = self._db.execute("SELECT COUNT(*) FROM learning_question WHERE status='pending'").fetchone()[0]
        reserved = hidden = retired = 0
        for content_id, ability, is_reserved in self._db.execute("""SELECT u.content_id,u.ability,
                EXISTS (SELECT 1 FROM app_setting a WHERE a.key='retention_reservation_v1:' || q.unit_id)
                FROM learning_question q JOIN learning_unit u ON u.unit_id=q.unit_id WHERE q.status='pending'"""):
            if is_reserved:
                reserved += 1
            elif content_id in library_unavailable:
                hidden += 1
            elif retired_ability(content_id, ability):
                retired += 1   # J6 R4: an ability the lesson no longer lists counts nowhere
        counts["pending"] -= retired
        if reserved: counts['diagnostic'] = reserved
        counts["library_unavailable"] = len(library_unavailable)
        counts["orphaned"] = len(availability.orphaned_ids)
        counts["library_unavailable_pending"] = hidden
        counts["disabled"] = counts["pending"] - len(visible) - reserved - hidden
        teaching, blocked_content, dependencies, projected = set(), set(), {}, {}
        ready_ids = projection.ready_ids() if projection is not None else independent_ready_ids(self)
        exposure_times = (projection.exposure_times(now) if projection is not None
                          else self.content_exposure_times(now))
        ability_states = projection.ability_states() if projection is not None else _teaching.state_map(self._db)
        # The first formal ratings of every visible unit in one read (the N2 reading asks for each of them).
        firsts = getattr(exposure_times, "first_rated", None)
        if firsts is not None:
            firsts.prime(row["unit_id"] for row in visible.values())
        from .answer_input import available, system_japanese_enabled
        system_input = system_japanese_enabled(self.store)
        gate_closed = self._batch_gate_reader(enabled_kinds)
        from .learning_probe import GateReader
        probe_gate = GateReader(self, now)
        if serve is not None:
            from .review_dispatch import reviews_today
            review_day, daily_counts = reviews_today(self.store, now, course=True)
        for row in visible.values():
            if gate_closed(row):
                # Not yet open (四之二): neither ready, waiting nor disabled.
                continue
            if serve is not None and not serve.allows(row["content_id"]) and not serve.started_work(
                    row["content_id"], row["ability"]) and self._filter_tier(
                    row, now, exposure_times=exposure_times, review_day=review_day, daily_counts=daily_counts) >= 3:
                continue   # design 12 H10: left out by the learner's filter; the home counts it apart
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
            projected_key = (row['content_id'], scope)
            if projected_key not in projected:
                content = _teaching.project(self._db, content_row(row["content_id"]), scope, state=state)
                # Read by content_question_blocked only (never changed): one decode per lesson and scope.
                projected[projected_key] = (content, {**_load(content['content_json']), 'stage': content['stage'],
                                                      'checkpoint': _load(content['checkpoint_json'])})
            content, teaching_item = projected[projected_key]
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
            held = probe_gate(row, content)
            if held == "hold":
                continue   # not yet open (probe first): neither ready, waiting nor disabled
            if held is not None:
                counts["waiting"] += 1
                if counts["next_due_at_utc"] is None or held < counts["next_due_at_utc"]:
                    counts["next_due_at_utc"] = held
                continue
            if content["stage"] in {"intro", "guide"}:
                teaching.add((content["content_id"], scope))
                continue
            if row['result_json'] is None and not available(question(), system_japanese=system_input):
                counts['blocked_input'] += 1
                continue
            due = self.effective_question_due(row, question(), now, exposure_times=exposure_times)
            if content["stage"] == "bridge" and not self.retrieval_ready(content, now):
                due = max(due or "", content["retrieval_at_utc"])
            if due and datetime.fromisoformat(due) > now and row["result_json"] is None:
                counts["waiting"] += 1
                if counts["next_due_at_utc"] is None or due < counts["next_due_at_utc"]:
                    counts["next_due_at_utc"] = due
            else:
                counts["ready"] += 1
        for cid, _ in self.read_only_unread(projection=projection):
            if serve is not None and not serve.allows(cid) and not serve.started_work(cid):
                continue   # design 12 H10: new content the learner's filter leaves out (not one started by hand)
            if self.prerequisite_status(cid, None, ready_ids=ready_ids)["blocked"]:
                counts["blocked"] += 1
                blocked_content.add(cid)
            else:
                teaching.add((cid, None))   # J6 R3: new content to start until it is read
        counts["instruction"] = len(teaching)
        counts["blocked_content"] = len(blocked_content)
        return counts

    def _stored_kind_disabled(self, kind):
        """The stored course kind switches turn this question kind off (the check submit makes)."""
        enabled = self.store.get_setting("learning_preferences_v1", {})
        from .learning_questions import stored_enabled_kinds
        return isinstance(enabled, dict) and isinstance(enabled.get("enabled"), list) and kind not in stored_enabled_kinds(enabled)

    def independence_wait(self, unit, question, floor, *, now=None):
        """When a rating ahead of the schedule may be given: the independence guard, or None.

        The latest of the question's floor (unless it equals the scheduled
        due: only that exact floor is the schedule's) and the feedback,
        tutor, retention and teaching exposure waits, read as
        question_not_before of the unit with its due removed. Shared by the
        mastery check (retention_session.formal_ready) and the early course
        channel (early_ready, design 12 K13). ``now``: the instant the exposure
        records are read at (None: the current time).
        """
        scheduled = _ability_due(unit, question)
        if floor and scheduled and datetime.fromisoformat(floor) == datetime.fromisoformat(scheduled):
            floor = None
        detached = {**dict(unit), "due_at_utc": None, "probe_due_at_utc": None, "not_before_utc": floor}
        return self.question_not_before(detached, {**question, "not_before_utc": floor}, now=now)

    def early_ready(self, unit, question, floor, now):
        """May this unit be rated early at ``now`` (design 12 K13)?

        PracticeCredit says FORMAL_EARLY (not due, not inside a step, 24 hours
        or more since its last formal rating) and the independence guard
        (independence_wait, ``floor``: the pending question's not_before_utc)
        is not later than ``now``. The other conditions (suspension, kind,
        availability, teaching, prerequisites, reservation) are the caller's:
        submit checks them as for any answer, early_head before showing one.
        """
        from .arrangement.assembly import practice_credit
        from .arrangement.model import Credit
        now = _utc(now)
        if practice_credit(self.store, self).course(unit["content_id"], unit["ability"], now) is not Credit.FORMAL_EARLY:
            return False
        guard = self.independence_wait(unit, question, floor, now=now)
        return not guard or datetime.fromisoformat(guard) <= now

    def answer_ready(self, unit, row, content_id, question_id, now):
        """The conditions of an independent answer of ``unit`` besides its due and its guards: the unit and
        its teaching ``row`` (ability_instruction_row) exist, not suspended, teaching done (ready, not
        intro/guide, a bridge past its retrieval wait), ``question_id`` not handed off, prerequisites not
        blocked. Shared by the mastery check (retention_session.formal_ready) and early_head."""
        from .retention_session import handoff
        return not (unit is None or row is None or unit["suspended"] or not unit["instruction_ready"]
                    or handoff(self.store, question_id) is not None
                    or row["stage"] in {"intro", "guide"}
                    or self.prerequisite_status(content_id, dict(row).get("instruction_scope"))["blocked"]
                    or row["stage"] == "bridge" and not self.retrieval_ready(row, now))

    def early_head(self, content_id, ability, now=None):
        """The unit's own pending question as a head (the shape current() gives a question), when the unit
        may be rated early at ``now`` (design 12 K13); otherwise None.

        Conditions: learned and not suspended; no retention reservation and no
        hand-off; its first pending question unanswered, of an enabled kind,
        not an observation-only replacement, its material not blocked; the
        content available; teaching done (ready, not intro/guide, a bridge past
        its retrieval wait); prerequisites not blocked; early_ready. Read-only.
        Submit it with early=True.
        """
        now = _utc(now)
        unit = self.get_unit(content_id, ability)
        if unit is None or unit["suspended"] or not unit["review_count"] or not unit["instruction_ready"]:
            return None
        from .retention_session import reservation
        question_id = self.queries.first_pending_question_id(unit["unit_id"])
        if question_id is None or reservation(self.store, unit["unit_id"]) is not None:
            return None
        from .learning_replacement import observation_only
        q = self.get_question(question_id)
        if (q is None or q["status"] != "pending" or q["result"] is not None or self._stored_kind_disabled(q["kind"])
                or observation_only(self.store, question_id) or content_id in self.availability().unavailable_ids):
            return None
        row = self.ability_instruction_row(content_id, unit["ability"])
        if not self.answer_ready(unit, row, content_id, question_id, now):
            return None
        content = self.instruction_item(content_id, dict(row).get("instruction_scope"))
        if self.content_question_blocked(content, q) or not self.early_ready(unit, q, q["not_before_utc"], now):
            return None
        return {"content": content, "question": q, "draft": q["draft"], "result": q["result"],
                "type": "question", "stage": "check"}

    def content_question_blocked(self, item, question):
        from .personal_content_versions import question_blocked
        from .material_validity import blocked_question
        checkpoint = item.get('checkpoint', {})
        # Frozen batch-quiz questions block like the single frozen guide.
        guides = [checkpoint.get('guide_question'),
                  *(checkpoint.get('intro_batch') or {}).get('questions', ())] if item.get('stage') == 'guide' else ()
        return (question_blocked(self.store, item, question) or blocked_question(self.store, item, question)
                or any(guide and (question_blocked(self.store, item, guide) or blocked_question(self.store, item, guide))
                       for guide in guides))

    def submit(self, question_id, answered_text="", correct=None, *, assisted=False,
               outcome=None, now=None, duration_ms=0, presentation=None, early=False):
        """Freeze the first answer. ``early``: the learner asked to be rated before the unit is due
        (extra practice, design 12 K13); let through only while early_ready holds at ``now``."""
        with self._db:
            self._transaction()
            return self._submit_in_transaction(question_id, answered_text, correct, assisted=assisted, outcome=outcome, now=now, duration_ms=duration_ms, presentation=presentation, early=early)

    def _submit_in_transaction(self, question_id, answered_text="", correct=None, *, assisted=False,
               outcome=None, now=None, duration_ms=0, presentation=None, diagnostic_context=None, early=False):
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
        self.require_available(unit["content_id"])
        content = self.ability_instruction_row(unit['content_id'], unit['ability'])
        scope = dict(content).get('instruction_scope')
        if not diagnostic and self.prerequisite_status(unit["content_id"], scope)["blocked"]:
            raise ValueError("请先学习前置内容，或明确选择已有前置知识。")
        if not diagnostic and (not unit["instruction_ready"] or content["stage"] in {"intro", "guide"} or content["stage"] == "bridge" and not self.retrieval_ready(content, now)):
            raise ValueError("Complete teaching and interleaving before independent assessment")
        snapshot = _load(row["snapshot_json"])
        if self._stored_kind_disabled(row["kind"]):
            raise ValueError("This question type is currently disabled")
        due = self.effective_question_due({**dict(unit), **dict(row)}, snapshot, now)
        # The early permit lets through an ordinary first answer only: an observation-only replacement or a
        # hand-off that appeared after early_head is refused as not due, as before extra practice.
        if unit["suspended"] or not diagnostic and due and datetime.fromisoformat(due) > now and not (
                early and not transferred
                and self.early_ready({**dict(unit), **dict(row)}, snapshot, row["not_before_utc"], now)):
            raise ValueError("This ability is not currently due")
        draft = _load(row["draft_json"])
        assisted = assisted or any(draft.get(k) for k in ("assisted", "answer_revealed", "hint_used"))
        outcome = outcome or ("correct" if correct is True else "incorrect" if correct is False else "unresolved")
        spelling_diagnosis = {}
        from .multi_cloze import is_cloze_question, grade_answer as grade_cloze_answer
        if is_cloze_question(snapshot) and outcome not in {'revealed', 'cancelled', 'device_failure'}:
            judgement = grade_cloze_answer(snapshot, answered_text)
            if judgement['status'] in {'empty', 'invalid'}:
                raise ValueError(judgement['message'])
            outcome, correct = judgement['status'], judgement['correct']
            spelling_diagnosis = {key: judgement[key] for key in (
                'slot_results', 'primary_correct', 'primary_scorable', 'overall_correct', 'background_errors')
                if key in judgement}
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
        if outcome == "unresolved" and not assisted and answered_text:
            # Rederived from the frozen snapshot, never taken from the page.
            from .learning_questions import grade_learning_answer
            from .assessment_policy import UNRESOLVED_REASON_MESSAGES
            reason = grade_learning_answer(snapshot, answered_text).get("unresolved_reason")
            if reason in UNRESOLVED_REASON_MESSAGES:
                result["unresolved_reason"] = reason
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
        self.require_available(unit["content_id"])  # 08 section 7.3: no grading while unavailable
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
        # One read: the prior attempt count and the unit's latest event (F5-B retry rule).
        prior = self._db.execute("""SELECT COUNT(*),
            (SELECT role FROM learning_event WHERE unit_id=?1 ORDER BY rowid DESC LIMIT 1) AS role,
            (SELECT outcome FROM learning_event WHERE unit_id=?1 ORDER BY rowid DESC LIMIT 1) AS outcome
            FROM learning_event WHERE unit_id=?1""", (unit["unit_id"],)).fetchone()
        result["attempt_count"] = prior[0] + 1
        if not scheduled and event_outcome in {"unresolved", "device_failure", "invalid"}:
            # Feedback has exposed a model answer. A new variant may be
            # queued, but cannot immediately turn copying into a successful
            # independent review. This is a queue delay, not an FSRS update.
            previous = prior if prior[0] else None
            result["retry_not_before_utc"] = _retry_not_before(self.store.zone, previous, event_outcome, answer, now)
        if event_outcome == "unresolved" and answer.get("unresolved_reason"):
            result["unresolved_reason"] = answer["unresolved_reason"]
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

    def queued_questions(self, content_id):
        """(ability, question snapshot) of the lesson's pending questions, one read; no write.

        For the planner's one-sentence-per-lesson order (course_planning.iter_question_plans): a question
        waiting in the queue for another ability has its sentence already taken.
        """
        return [(ability, _load(snapshot)) for ability, snapshot in self.queries.pending_question_snapshots(content_id)]

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
        # Everything learning_practice_policy._fingerprint reads: a heard task is told apart
        # by its audio text, a generated sound task by its options.
        fields = ("target_id", "revision", "ability", "kind", "variant", "input_mode",
                  "prompt", "task", "answer", "practice", "audio_text", "choices")
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


def _retry_not_before(zone, previous, event_outcome, answer, now):
    """Queue floor only; due_at_utc and the FSRS card are untouched.

    `previous` is the unit's latest event row before this one (or None).
    """
    if event_outcome != "unresolved" or answer.get("diagnostic_needs_teaching"):
        # Device/input failures and a placement routed back to teaching
        # keep the short feedback-exposure pause.
        return (now + timedelta(seconds=FEEDBACK_COOLDOWN_SECONDS)).isoformat()
    until = now + timedelta(seconds=UNRESOLVED_RETRY_SECONDS)
    if previous is not None and previous["role"] == "unresolved" and previous["outcome"] == "unresolved":
        # A second consecutive unresolved waits for the store's next local day.
        midnight = datetime.combine(now.astimezone(zone).date() + timedelta(days=1), datetime.min.time(), zone)
        until = max(until, _utc(midnight))
    return until.isoformat()


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
        if 'familiarity_draft' in checkpoint:
            draft = checkpoint['familiarity_draft']
            if (not isinstance(draft, dict) or not set(draft) < set(_teaching.abilities(db, row))
                    or any(value not in FAMILIARITY_LEVELS for value in draft.values())
                    or checkpoint.get('initial_familiarity') or row['stage'] != 'intro'):
                raise ValueError('Invalid partial familiarity choices')
        from .pitch_practice import validate_checkpoint
        validate_checkpoint(row["content_id"], row["revision"], checkpoint)
        from .multi_cloze import validate_checkpoint as validate_cloze_checkpoint
        validate_cloze_checkpoint(row['content_id'], row['revision'], checkpoint)
        from .learning_practice_policy import validate_practice_guide
        validate_practice_guide(row["content_id"], row["revision"], checkpoint, item=item,
                                validate_result=False)
        from . import intro_batch
        if intro_batch.ITEM_KEY in checkpoint:
            intro_batch.validate_item_state(checkpoint[intro_batch.ITEM_KEY], content_id=row["content_id"])
        if intro_batch.ROUTE_KEY in checkpoint:
            intro_batch.validate_route(checkpoint[intro_batch.ROUTE_KEY])
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
                    or profile.get("evidence") not in PROFILE_EVIDENCE or not isinstance(profile.get("levels"), dict)
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
            if DECLINED_KEY in profile and (profile[DECLINED_KEY] is not True
                    or profile["diagnostic_state"] != "needs_teaching" or profile["evidence"] != PROBE_EVIDENCE):
                raise ValueError("Invalid declined probe")
            if "source" in profile and (profile["evidence"] != "self_report" or profile["source"] != intro_batch.CARD_SOURCE
                    or set(profile["levels"].values()) - {"new", "familiar"}
                    or len(set(profile["levels"].values())) != 1):
                raise ValueError("Invalid introduction card self-report")
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
        if result is not None and "unresolved_reason" in result:
            from .assessment_policy import UNRESOLVED_REASON_MESSAGES
            if result["unresolved_reason"] not in UNRESOLVED_REASON_MESSAGES or result.get("outcome") != "unresolved":
                raise ValueError("Invalid unresolved reason")
        from .pitch_practice import validate_snapshot, validate_draft
        validate_snapshot(q)
        validate_draft(q, draft)
        from .multi_cloze import validate_snapshot as validate_cloze_snapshot, validate_draft as validate_cloze_draft
        validate_cloze_snapshot(q)
        validate_cloze_draft(q, draft)
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
            # Legacy records hold exactly +60 s; unresolved ones now hold
            # +4 h or the next local midnight. Accept the bounded window.
            occurred = datetime.fromisoformat(row["occurred_at_utc"])
            delay = datetime.fromisoformat(result["retry_not_before_utc"]) - occurred
            if (row["role"] != "unresolved" or row["outcome"] not in {"unresolved", "device_failure", "invalid"}
                    or not timedelta(seconds=FEEDBACK_COOLDOWN_SECONDS) <= delay
                    <= timedelta(seconds=RETRY_COOLDOWN_MAX_SECONDS)):
                raise ValueError("Invalid answer-feedback cooldown")
        if "unresolved_reason" in result:
            if (row["role"] != "unresolved" or row["outcome"] != "unresolved"
                    or result["unresolved_reason"] != (result.get("answer") or {}).get("unresolved_reason")):
                raise ValueError("Invalid unresolved reason")
        if row["rating"] is not None:
            Card.from_dict(_load(row["state_after_json"]))
            if (row["assisted"] or row["outcome"] in {"incorrect", "revealed"}) and row["rating"] != 1:
                raise ValueError("Assisted answer cannot be a successful independent review")
        elif row["state_before_json"] != row["state_after_json"]:
            raise ValueError("Unresolved evidence cannot alter the schedule")
    from .intro_batch_state import validate_database as validate_intro_batch
    validate_intro_batch(db)
    from .content_library_rules import REGISTRY_SETTING, validate_registry
    for (raw,) in db.execute("SELECT value_json FROM app_setting WHERE key=?", (REGISTRY_SETTING,)):
        # Shape only (08 §7.1): library files need not exist on this device.
        validate_registry(_load(raw))
    from .learning_replacement import validate_database as validate_replacements
    retired = validate_replacements(db)
    from .practice_archive import validate_database as validate_round_archives
    retired = set(retired) | validate_round_archives(db)
    if any(row[0] not in retired for row in db.execute("""SELECT q.question_id FROM learning_question q
            LEFT JOIN learning_event e ON q.question_id=e.question_id
            WHERE q.status='completed' AND e.event_id IS NULL""")):
        raise ValueError("Completed question lacks finalization or an exact replacement receipt")
