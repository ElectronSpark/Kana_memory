"""Small, evidence-linked current notes; never an alternate scheduler.

Model notes are tentative, expire, and can be withdrawn. User corrections are
separate explicit statements. Request retries cannot append the same note twice.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from . import app_clock
from hashlib import sha256
import json
import re

TABLES = ("tutor_memory_meta", "tutor_user_message", "tutor_memory_claim", "tutor_memory_application")
UPDATE_FIELDS = {"operation", "kind", "text", "target_ids", "evidence_ids", "replaces"}
KINDS = {"preference", "observation", "question_focus"}
SECRET = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _now(value=None):
    value = value or app_clock.utc_now()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Memory clock requires a timezone")
    return value.astimezone(timezone.utc)


def _text(value, limit):
    if (not isinstance(value, str) or len(value) > limit or SECRET.search(value)
            or any(ord(c) < 32 and c not in "\n\t\r" or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError("Invalid tutor memory text")
    return value


def validate_updates_structure(updates):
    if not isinstance(updates, list) or len(updates) > 3:
        raise ValueError("Too many tutor memory updates")
    for item in updates:
        if (not isinstance(item, dict) or set(item) != UPDATE_FIELDS
                or item["operation"] not in {"upsert", "retract"} or item["kind"] not in KINDS):
            raise ValueError("Invalid tutor memory update")
        _text(item["text"], 240)
        for key, limit in (("target_ids", 4), ("evidence_ids", 6), ("replaces", 4)):
            if (not isinstance(item[key], list) or len(item[key]) > limit
                    or any(not _text(v, 256).strip() for v in item[key])
                    or len(set(item[key])) != len(item[key])):
                raise ValueError("Invalid tutor memory references")
        if not item["evidence_ids"] or (item["operation"] == "upsert" and not item["text"].strip()):
            raise ValueError("Tutor memory requires evidence")
        if item["operation"] == "retract" and not item["replaces"]:
            raise ValueError("Tutor memory retraction requires a claim")
    return updates


def ensure_schema(db):
    if db.in_transaction:
        raise RuntimeError("Memory initialization requires its own transaction")
    with db:
        db.execute("BEGIN IMMEDIATE")
        for sql in (
            "CREATE TABLE IF NOT EXISTS tutor_memory_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
            """CREATE TABLE IF NOT EXISTS tutor_user_message(
                message_id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,text TEXT NOT NULL,
                target_id TEXT NOT NULL,created_at_utc TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS tutor_memory_claim(
                claim_id TEXT PRIMARY KEY,kind TEXT NOT NULL,text TEXT NOT NULL,
                target_ids_json TEXT NOT NULL,evidence_ids_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('tentative','confirmed','retracted','superseded')),
                source TEXT NOT NULL CHECK(source IN ('model','user')),created_at_utc TEXT NOT NULL,
                expires_at_utc TEXT,withdrawn_at_utc TEXT,withdrawal_reason TEXT NOT NULL DEFAULT '')""",
            """CREATE TABLE IF NOT EXISTS tutor_memory_application(
                request_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,created_at_utc TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS tutor_memory_current ON tutor_memory_claim(status,created_at_utc DESC)",
        ):
            db.execute(sql)
        db.execute("INSERT OR IGNORE INTO tutor_memory_meta VALUES('version','1')")
        if db.execute("SELECT value FROM tutor_memory_meta WHERE key='version'").fetchone()[0] != '1':
            raise ValueError("Unsupported tutor memory version")
        db.execute("INSERT OR IGNORE INTO tutor_memory_meta VALUES('revision','0')")


def _bump(db):
    db.execute("UPDATE tutor_memory_meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")


class TutorMemoryStore:
    def __init__(self, store):
        self._db = store._db
        self.last_skipped = 0
        ensure_schema(self._db)

    def record_message(self, request_id, text, target_id='', now=None):
        """Called only on Send, never on typing or opening suggestions."""
        _text(request_id, 100); _text(text, 2000); _text(target_id, 256)
        identity = 'message:' + request_id
        with self._db:
            old = self._db.execute("SELECT * FROM tutor_user_message WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old['text'] != text or old['target_id'] != target_id:
                    raise ValueError("Request message changed")
                return identity
            self._db.execute("INSERT INTO tutor_user_message VALUES(?,?,?,?,?)",
                             (identity, request_id, text, target_id, _now(now).isoformat()))
            _bump(self._db)
        return identity

    def _live(self, row, now):
        if row['status'] not in {'tentative', 'confirmed'}:
            return False
        if row['expires_at_utc'] and datetime.fromisoformat(row['expires_at_utc']) <= now:
            return False
        for eid in json.loads(row['evidence_ids_json']):
            if eid.startswith('message:'):
                exists = self._db.execute("SELECT 1 FROM tutor_user_message WHERE message_id=?", (eid,)).fetchone()
            else:
                from .content_history import contribution_valid
                if not contribution_valid(self._db, eid):
                    return False
                exists = self._db.execute("SELECT 1 FROM learning_evidence WHERE event_id=? AND retracted=0", (eid,)).fetchone()
            if not exists:
                return False
        return True

    def current(self, target_ids=(), now=None, limit=8):
        now = _now(now)
        targets = set(target_ids)
        result = []
        where = "status IN ('tentative','confirmed')"
        args = []
        if targets:
            # Filter before LIMIT: unrelated recent claims cannot hide a user's
            # explicit correction. IDs are JSON-encoded scalars, never SQL.
            where += " AND (target_ids_json='[]' OR " + ' OR '.join('instr(target_ids_json,?)>0' for _ in targets) + ')'
            args = [_json(identity) for identity in sorted(targets)]
        for row in self._db.execute("SELECT * FROM tutor_memory_claim WHERE " + where +
                " ORDER BY (source='user') DESC,created_at_utc DESC,claim_id DESC LIMIT 128", args):
            ids = json.loads(row['target_ids_json'])
            if targets and ids and not targets.intersection(ids) or not self._live(row, now):
                continue
            result.append({'claim_id': row['claim_id'], 'kind': row['kind'], 'text': row['text'],
                'target_ids': ids, 'evidence_ids': json.loads(row['evidence_ids_json']),
                'status': row['status'], 'source': row['source'], 'created_at_utc': row['created_at_utc']})
            if len(result) >= max(1, min(20, limit)):
                break
        return result

    def _superseded_by_user_change(self, update):
        """A delayed optional note cannot undo a newer user correction.

        A model need not list ``replaces`` when it repeats an old preference.
        Compare the actual supporting message/event time with overlapping user
        changes instead. Fresh evidence after that change may establish a new
        note; no backup schema or scheduling state is changed.
        """
        evidence_times = []
        for eid in update['evidence_ids']:
            if eid.startswith('message:'):
                row = self._db.execute('SELECT created_at_utc FROM tutor_user_message WHERE message_id=?', (eid,)).fetchone()
            elif update['kind'] == 'observation':
                row = self._db.execute('SELECT occurred_at_utc FROM learning_evidence WHERE event_id=?', (eid,)).fetchone()
            else:
                continue
            if row:
                evidence_times.append(datetime.fromisoformat(row[0]))
        if not evidence_times:
            return False  # Missing/withdrawn evidence is rejected by _live.
        newest = max(evidence_times)
        targets = set(update['target_ids'])
        for row in self._db.execute("""SELECT target_ids_json,withdrawn_at_utc
                FROM tutor_memory_claim WHERE kind=? AND withdrawal_reason='用户纠正'
                AND withdrawn_at_utc IS NOT NULL""", (update['kind'],)):
            prior_targets = set(json.loads(row['target_ids_json']))
            if (not targets or not prior_targets or targets.intersection(prior_targets)) and (
                    datetime.fromisoformat(row['withdrawn_at_utc']) >= newest):
                return True
        return False

    def apply(self, request_id, updates, *, allowed_evidence, allowed_targets, allowed_claims=(), now=None):
        validate_updates_structure(updates)
        now = _now(now)
        digest = sha256(_json(updates).encode()).hexdigest()
        self.last_skipped = 0
        old = self._db.execute("SELECT payload_hash FROM tutor_memory_application WHERE request_id=?", (request_id,)).fetchone()
        if old:
            if old[0] != digest:
                raise ValueError("Conflicting memory response")
            for update in updates:
                for identity in update['replaces']:
                    row = self._db.execute("SELECT source,withdrawal_reason FROM tutor_memory_claim WHERE claim_id=?", (identity,)).fetchone()
                    if row and (row['source'] == 'user' or row['withdrawal_reason'] == '用户纠正'):
                        self.last_skipped += 1
                        break
            return False
        evidence, targets, claims = set(allowed_evidence), set(allowed_targets), set(allowed_claims)
        for update in updates:
            if (not set(update['evidence_ids']) <= evidence or not set(update['target_ids']) <= targets
                    or not set(update['replaces']) <= claims):
                raise ValueError("Memory proposal references unavailable evidence")
            # A preference must cite a sent user statement, not an error pattern.
            if update['kind'] in {'preference', 'question_focus'} and not any(e.startswith('message:') for e in update['evidence_ids']):
                raise ValueError("Preference requires a user statement")
            if update['kind'] == 'observation' and all(e.startswith('message:') for e in update['evidence_ids']):
                raise ValueError("Observation requires recorded learning evidence")
            if update['kind'] == 'observation':
                observed = {row[0] for eid in update['evidence_ids'] if not eid.startswith('message:')
                            for row in self._db.execute('SELECT target_id FROM learning_evidence WHERE event_id=?', (eid,))}
                if not update['target_ids'] or not set(update['target_ids']) <= observed:
                    raise ValueError('Observation targets must match their recorded evidence')
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            old = self._db.execute("SELECT payload_hash FROM tutor_memory_application WHERE request_id=?", (request_id,)).fetchone()
            if old:
                if old[0] != digest:
                    raise ValueError("Conflicting memory response")
                return False
            for index, update in enumerate(updates):
                if self._superseded_by_user_change(update):
                    self.last_skipped += 1
                    continue
                replaced = [self._db.execute("SELECT * FROM tutor_memory_claim WHERE claim_id=?", (identity,)).fetchone()
                            for identity in update['replaces']]
                if any(row is None or row['source'] == 'user' or not self._live(row, now) for row in replaced):
                    # A reply prepared before a user correction must not erase
                    # its audit or append a contradictory replacement note.
                    self.last_skipped += 1
                    continue
                # Reject references withdrawn since this request was prepared.
                probe = {'status': 'tentative', 'expires_at_utc': None, 'evidence_ids_json': _json(update['evidence_ids'])}
                if not self._live(probe, now):
                    self.last_skipped += 1
                    continue
                for identity in update['replaces']:
                    row = self._db.execute("SELECT * FROM tutor_memory_claim WHERE claim_id=?", (identity,)).fetchone()
                    # Model cannot silently rewrite a user's explicit correction.
                    if row and row['source'] == 'model':
                        self._db.execute("UPDATE tutor_memory_claim SET status=?,withdrawn_at_utc=?,withdrawal_reason=? WHERE claim_id=?",
                            ('retracted' if update['operation'] == 'retract' else 'superseded', now.isoformat(), '模型更新', identity))
                if update['operation'] == 'upsert':
                    identity = 'claim:' + sha256(f'{request_id}:{index}'.encode()).hexdigest()[:32]
                    self._db.execute("INSERT INTO tutor_memory_claim VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (identity, update['kind'], update['text'], _json(update['target_ids']), _json(update['evidence_ids']),
                         'tentative', 'model', now.isoformat(), (now + timedelta(days=30)).isoformat(), None, ''))
            self._db.execute("INSERT INTO tutor_memory_application VALUES(?,?,?)", (request_id, digest, now.isoformat()))
            _bump(self._db)
        return True

    def correct(self, claim_id, text='', now=None):
        """Withdraw or replace one note without touching learning history."""
        _text(text, 240)
        now = _now(now)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute("SELECT * FROM tutor_memory_claim WHERE claim_id=?", (claim_id,)).fetchone()
            if row is None:
                raise KeyError(claim_id)
            if row['status'] not in {'tentative', 'confirmed'}:
                return False
            self._db.execute("UPDATE tutor_memory_claim SET status='retracted',withdrawn_at_utc=?,withdrawal_reason='用户纠正' WHERE claim_id=?",
                             (now.isoformat(), claim_id))
            if text.strip():
                identity = 'claim:' + sha256((claim_id + now.isoformat() + text).encode()).hexdigest()[:32]
                self._db.execute("INSERT INTO tutor_memory_claim VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (identity, row['kind'], text.strip(), row['target_ids_json'], '[]', 'confirmed', 'user', now.isoformat(), None, None, ''))
            _bump(self._db)
        return True


def validate_database(db):
    """Used on a disposable staging database before importing learner data."""
    metadata = dict(db.execute("SELECT key,value FROM tutor_memory_meta"))
    if metadata.get('version') != '1' or not metadata.get('revision', '').isdigit():
        raise ValueError("Invalid tutor memory metadata")
    for row in db.execute("SELECT * FROM tutor_user_message"):
        _text(row['text'], 2000); _text(row['target_id'], 256)
        _text(row['request_id'], 100); _now(datetime.fromisoformat(row['created_at_utc']))
        if row['message_id'] != 'message:' + row['request_id']:
            raise ValueError("Invalid message identity")
    for row in db.execute("SELECT * FROM tutor_memory_claim"):
        _text(row['claim_id'], 256); _text(row['text'], 240)
        _now(datetime.fromisoformat(row['created_at_utc']))
        for field in ('expires_at_utc', 'withdrawn_at_utc'):
            if row[field] is not None:
                _now(datetime.fromisoformat(row[field]))
        for field, maximum in (('target_ids_json', 4), ('evidence_ids_json', 6)):
            values = json.loads(row[field])
            if not isinstance(values, list) or len(values) > maximum or any(not _text(v, 256) for v in values):
                raise ValueError("Invalid memory references")
            if len(set(values)) != len(values):
                raise ValueError("Duplicate memory references")
        for eid in json.loads(row['evidence_ids_json']):
            exists = (db.execute("SELECT 1 FROM tutor_user_message WHERE message_id=?", (eid,)).fetchone()
                      if eid.startswith('message:') else
                      db.execute("SELECT 1 FROM learning_evidence WHERE event_id=?", (eid,)).fetchone())
            if exists is None:
                raise ValueError("Missing memory source evidence")
        if (row['kind'] not in KINDS or row['source'] not in {'model','user'}
                or row['status'] not in {'tentative','confirmed','retracted','superseded'}
                or row['source'] == 'model' and (not json.loads(row['evidence_ids_json']) or row['expires_at_utc'] is None)
                or row['source'] == 'model' and row['status'] == 'confirmed'
                or row['status'] in {'retracted','superseded'} and (row['withdrawn_at_utc'] is None or not row['withdrawal_reason'])
                or row['status'] in {'tentative','confirmed'} and (row['withdrawn_at_utc'] is not None or row['withdrawal_reason'])
                or row['source'] == 'user' and (row['status'] == 'tentative' or row['expires_at_utc'] is not None)
                or row['source'] == 'model' and not timedelta(0) < datetime.fromisoformat(row['expires_at_utc']) - datetime.fromisoformat(row['created_at_utc']) <= timedelta(days=30)):
            raise ValueError("Invalid memory claim")
    for row in db.execute("SELECT * FROM tutor_memory_application"):
        _text(row['request_id'], 100); _now(datetime.fromisoformat(row['created_at_utc']))
        if re.fullmatch(r'[0-9a-f]{64}', row['payload_hash']) is None:
            raise ValueError("Invalid memory receipt")
