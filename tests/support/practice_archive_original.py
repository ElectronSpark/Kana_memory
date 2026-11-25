"""Pinned copy of practice_archive.archive_previous_rounds as it was before untouched questions were removed.

Reference behaviour only, never called by the app: tests compare what the current pass writes for a
touched question (receipt and completed row) with what this copy writes for the same input. It
reuses the module's unchanged helpers (receipt shape, validation, ownership); only the pass itself is
frozen here, with its full validation before and after the writes.
"""
from kanamemory.practice_archive import (
    NATIVE_PREFIX, _course_key, _dump, _evidence, _load, _owned_course, _sha, _table, validate_database,
)


def archive_previous_rounds(store, *, now=None):
    from kanamemory.domain import _utc
    db = store._db
    if db.in_transaction:
        raise RuntimeError('Practice archival owns its transaction')
    stamp = _utc(now).isoformat()
    summary = {'native': 0, 'course': 0, 'skipped_course': 0, 'skipped_owners': {}}
    with db:
        db.execute('BEGIN IMMEDIATE')
        validate_database(db, deep=False)
        for row in db.execute('SELECT * FROM session_checkpoint').fetchall():
            before = dict(row)
            head = db.execute('SELECT snapshot_json FROM session_question WHERE session_id=? AND question_index=?',
                              (row['session_id'], row['question_index'])).fetchone()
            value = {'version': 1, 'kind': 'native', 'archived_at_utc': stamp,
                     'before': before, 'before_sha256': _sha(before),
                     'head': {'question_index': row['question_index'], 'snapshot_json': head[0],
                              'fingerprint': _sha(_load(head[0]))} if head else None,
                     'evidence': _evidence(before, native=True)}
            key = NATIVE_PREFIX + row['session_id']
            if len(key) > 256:
                raise ValueError('Archived session identity is too long')
            db.execute('INSERT INTO app_setting VALUES (?,?)', (key, _dump(value)))
            db.execute('DELETE FROM session_checkpoint WHERE session_id=?', (row['session_id'],))
            summary['native'] += 1
        if _table(db, 'learning_question'):
            for row in db.execute("SELECT * FROM learning_question WHERE status='pending'").fetchall():
                owner = _owned_course(db, row)
                if owner:
                    summary['skipped_course'] += 1
                    summary['skipped_owners'][owner] = summary['skipped_owners'].get(owner, 0) + 1
                    continue
                before = dict(row)
                value = {'version': 1, 'kind': 'course', 'archived_at_utc': stamp,
                         'before': before, 'before_sha256': _sha(before),
                         'after_sha256': _sha({**before, 'status': 'completed'}),
                         'evidence': _evidence(before)}
                db.execute('INSERT INTO app_setting VALUES (?,?)', (_course_key(row['question_id']), _dump(value)))
                db.execute("UPDATE learning_question SET status='completed' WHERE question_id=?", (row['question_id'],))
                summary['course'] += 1
        validate_database(db, deep=False)
    return summary
