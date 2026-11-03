"""Connection-local, copy-on-write views of immutable question payloads.

The journal and caches are disposable projections, never learner records. SQL
writes (including maintenance writes) participate in the caller's transaction.
Only committed reads publish a cache, so rollback cannot reuse transient data.
"""
from collections import OrderedDict
from copy import deepcopy
import json


class FrozenRow(dict):
    """Private JSON tree: consumers copy before editing any child."""
    def __init__(self, value, raw=None):
        super().__init__(value)
        self.raw = raw
        self._encoding = None
        self._projection = None
        self._policy = None

    def __deepcopy__(self, memo):
        # Public copies must not inherit cached facts about the original tree.
        return deepcopy(dict(self), memo)

    def encoding(self):
        if self._encoding is None:
            text = json.dumps(self, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            if text == self.raw:
                text = self.raw
            self._encoding = text, len(text.encode('utf8'))
        return self._encoding

    def projected(self, project):
        if self._projection is None or self._projection[0] is not project:
            value = project(self)
            row = FrozenRow(value)
            # Preserve order-sensitive canonical encoding, including defaults
            # and legacy normalization. Python dict equality is insufficient.
            self._projection = project, None if row.encoding()[0] == self.encoding()[0] else row
        return self if self._projection[1] is None else self._projection[1]

    def allowed(self, predicate):
        if self._policy is None or self._policy[0] is not predicate:
            self._policy = predicate, predicate(self)
        return self._policy[1]


def install_journal(db):
    """Record the latest changed ordinal; size is bounded by touched rows."""
    db.executescript('''
        CREATE TEMP TABLE snapshot_changed (
            session_id TEXT NOT NULL, question_index INTEGER NOT NULL,
            revision INTEGER NOT NULL, PRIMARY KEY(session_id,question_index));
        CREATE INDEX snapshot_changed_revision ON snapshot_changed(session_id,revision);
        CREATE TEMP TABLE snapshot_journal_reset (revision INTEGER NOT NULL);
        INSERT INTO snapshot_journal_reset VALUES (0);
    ''')
    # These triggers do not rely on ordering relative to snapshot_revision's
    # triggers: each records its own increment before writing the journal.
    for table, prefix in (('session_question', 'row'), ('study_session', 'session')):
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            refs = ('OLD', 'NEW') if event == 'UPDATE' else (('OLD',) if event == 'DELETE' else ('NEW',))
            statements = ['UPDATE snapshot_revision SET revision=revision+1;']
            for ref in refs:
                ordinal = f'{ref}.question_index' if prefix == 'row' else '-1'
                statements.append(f'''UPDATE snapshot_changed SET revision=(SELECT revision FROM snapshot_revision)
                    WHERE session_id={ref}.session_id AND question_index={ordinal};
                    INSERT INTO snapshot_changed SELECT {ref}.session_id,{ordinal},(SELECT revision FROM snapshot_revision)
                    WHERE NOT EXISTS (SELECT 1 FROM snapshot_changed
                        WHERE session_id={ref}.session_id AND question_index={ordinal});''')
            db.execute(f'''CREATE TEMP TRIGGER snapshot_journal_{prefix}_{event.lower()}
                AFTER {event} ON main.{table} BEGIN {' '.join(statements)} END''')
        # REPLACE may implicitly delete another ordinal through a rowid
        # conflict without firing DELETE triggers. Such exceptional writes
        # force a full refresh, rather than changing recursive_triggers.
        for event in ('INSERT', 'UPDATE'):
            moved = 'NEW.rowid != OLD.rowid AND ' if event == 'UPDATE' else ''
            db.execute(f'''CREATE TEMP TRIGGER snapshot_journal_{prefix}_{event.lower()}_rowid
                BEFORE {event} ON main.{table}
                WHEN {moved}EXISTS (SELECT 1 FROM main.{table} WHERE rowid=NEW.rowid)
                BEGIN UPDATE snapshot_revision SET revision=revision+1;
                    UPDATE snapshot_journal_reset SET revision=(SELECT revision FROM snapshot_revision); END''')


class SnapshotReader:
    """Keep at most two committed sessions; old callers retain their own view."""
    def __init__(self, db):
        self.db = db
        self.cache = OrderedDict()

    def clear(self):
        self.cache.clear()

    def token(self):
        return (self.db.execute('PRAGMA data_version').fetchone()[0],
                self.db.execute('SELECT revision FROM snapshot_revision').fetchone()[0])

    @staticmethod
    def _parse(text):
        value = json.loads(text)
        # The previous reader returned arbitrary JSON as-is; consumers decide
        # which malformed legacy rows they actually need to inspect.
        return FrozenRow(value, text) if isinstance(value, dict) else value

    def read(self, session_id):
        token = self.token()
        cached = self.cache.get(session_id)
        if cached is not None and cached[0] == token:
            return cached[1], cached[2]
        if self.db.execute('SELECT 1 FROM study_session WHERE session_id=?', (session_id,)).fetchone() is None:
            raise ValueError('Unknown session_id')
        changed = None
        reset = self.db.execute('SELECT revision FROM snapshot_journal_reset').fetchone()[0]
        if cached is not None and cached[0][0] == token[0] and reset <= cached[0][1] <= token[1]:
            changed = list(self.db.execute('''SELECT question_index FROM snapshot_changed
                WHERE session_id=? AND revision>? ORDER BY question_index''', (session_id, cached[0][1])))
            if changed and changed[0][0] == -1:
                changed = None
        if changed is None:
            indexed = {row[0]: self._parse(row[1]) for row in self.db.execute(
                'SELECT question_index,snapshot_json FROM session_question WHERE session_id=? ORDER BY question_index',
                (session_id,))}
            items = list(indexed.values())
        elif not changed:
            indexed, items = cached[1], cached[2]
        else:
            indexed = cached[1].copy()
            for row in self.db.execute('''SELECT d.question_index,q.snapshot_json
                FROM snapshot_changed d LEFT JOIN main.session_question q
                ON q.session_id=d.session_id AND q.question_index=d.question_index
                WHERE d.session_id=? AND d.revision>? ORDER BY d.question_index''', (session_id, cached[0][1])):
                index, text = row
                if text is None:
                    indexed.pop(index, None)
                else:
                    old = indexed.get(index)
                    indexed[index] = old if isinstance(old, FrozenRow) and old.raw == text else self._parse(text)
            indexed = dict(sorted(indexed.items()))
            items = list(indexed.values())
        # An external commit between the revision read and incremental query
        # may change rows absent from this connection's journal. Fall back to
        # a single complete SELECT, and do not publish that ambiguous version.
        if self.db.execute('PRAGMA data_version').fetchone()[0] != token[0]:
            indexed = {row[0]: self._parse(row[1]) for row in self.db.execute(
                'SELECT question_index,snapshot_json FROM session_question WHERE session_id=? ORDER BY question_index',
                (session_id,))}
            return indexed, list(indexed.values())
        if not self.db.in_transaction:
            self.cache[session_id] = token, indexed, items
            self.cache.move_to_end(session_id)
            while len(self.cache) > 2:
                self.cache.popitem(last=False)
        return indexed, items
