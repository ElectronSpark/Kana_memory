"""End local runtime plans without grading, deleting met questions, or changing memory.

Receipts own only the checkpoint removal / pending-to-completed transition.
Their complete before images remain ordinary long-term learner records. They
cannot authorize a reviewed event, replace its answer, or settle a diagnostic.
A queued course question the learner never met (untouched_course_questions)
carries no information about the learner: ending a round removes it from the
queue without a receipt or a completed copy.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re

PREFIX = 'practice_archive_v1:'
NATIVE_PREFIX = PREFIX + 'native:'
COURSE_PREFIX = PREFIX + 'course:'
SESSION_PREFIX = PREFIX + 'prefix:'
_CHECKPOINT = {'session_id', 'updated_at_utc', 'question_index', 'session_right',
               'assisted_indices_json', 'state_json'}
_QUESTION = {'question_id', 'unit_id', 'kind', 'snapshot_json', 'status', 'draft_json',
             'result_json', 'created_at_utc', 'not_before_utc', 'exposure_group'}
_COMMON = {'version', 'kind', 'archived_at_utc', 'before', 'before_sha256', 'evidence'}
_SESSION_PART = {'version', 'kind', 'archived_at_utc', 'session_id', 'upto', 'counts', 'sha256'}
_PARTS = ('questions', 'answers', 'spellings', 'events')


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False)


def _load(value):
    return json.loads(value, parse_constant=lambda value: (_ for _ in ()).throw(
        ValueError('Non-finite practice archive value')))


def _sha(value):
    return hashlib.sha256(_dump(value).encode('utf-8')).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value) and len(value) <= 1024


def _date(value):
    if not isinstance(value, str):
        raise ValueError('Invalid practice archive time')
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Practice archive time requires a timezone')


def _course_key(question_id):
    if not _text(question_id):
        raise ValueError('Invalid archived question identity')
    return COURSE_PREFIX + hashlib.sha256(question_id.encode('utf-8')).hexdigest()


def _table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _evidence(before, *, native=False):
    if native:
        state = _load(before['state_json'])
        if not isinstance(state, dict):
            raise ValueError('Archived native state must be an object')
        submitted = state.get('feedback_pending') is True or state.get('page') == 'feedback'
        return {'state': state, 'submitted_unrated': submitted, 'rating': None, 'scheduled': False}
    return {'draft': _load(before['draft_json']),
            'result': _load(before['result_json']) if before['result_json'] is not None else None,
            'rating': None, 'scheduled': False}


def _base(value, kind):
    fields = _COMMON | ({'head'} if kind == 'native' else {'after_sha256'})
    if (not isinstance(value, dict) or set(value) != fields
            or type(value.get('version')) is not int or value['version'] != 1
            or value.get('kind') != kind or not isinstance(value.get('before'), dict)
            or value.get('before_sha256') != _sha(value['before'])):
        raise ValueError('Invalid practice archive receipt')
    _date(value['archived_at_utc'])


def _validate_native(db, key, value):
    _base(value, 'native')
    before = value['before']
    if (set(before) != _CHECKPOINT or not _text(before['session_id'])
            or key != NATIVE_PREFIX + before['session_id']
            or type(before['question_index']) is not int or before['question_index'] < 0
            or type(before['session_right']) is not int
            or not 0 <= before['session_right'] <= before['question_index']
            or not isinstance(before['state_json'], str)
            or not isinstance(before['assisted_indices_json'], str)):
        raise ValueError('Invalid archived native checkpoint')
    _date(before['updated_at_utc'])
    sid, index = before['session_id'], before['question_index']
    if (db.execute('SELECT 1 FROM study_session WHERE session_id=?', (sid,)).fetchone() is None
            or db.execute('SELECT 1 FROM session_checkpoint WHERE session_id=?', (sid,)).fetchone()):
        raise ValueError('Archived native plan is missing or still active')
    count = db.execute('SELECT COUNT(*) FROM session_question WHERE session_id=?', (sid,)).fetchone()[0]
    assisted = _load(before['assisted_indices_json'])
    if (index > count or not isinstance(assisted, list)
            or any(type(i) is not int or not 0 <= i < count for i in assisted)
            or assisted != sorted(set(assisted))):
        raise ValueError('Invalid archived native position')
    row = db.execute('SELECT snapshot_json FROM session_question WHERE session_id=? AND question_index=?',
                     (sid, index)).fetchone()
    expected = ({'question_index': index, 'snapshot_json': row[0],
                 'fingerprint': _sha(_load(row[0]))} if row else None)
    if (_dump(value['head']) != _dump(expected)
            or _dump(value['evidence']) != _dump(_evidence(before, native=True))):
        raise ValueError('Archived native answer differs from its frozen source')
    from .progress_backup import validate_session_state
    validate_session_state(value['evidence']['state'], _load(row[0]) if row else None)


def _validate_course(db, key, value):
    _base(value, 'course')
    before = value['before']
    if (set(before) != _QUESTION or key != _course_key(before.get('question_id'))
            or before.get('status') != 'pending' or not _text(before.get('unit_id'))
            or not _text(before.get('kind'))
            or any(not isinstance(before.get(field), str)
                   for field in ('snapshot_json', 'draft_json', 'created_at_utc', 'exposure_group'))
            or before.get('result_json') is not None and not isinstance(before['result_json'], str)):
        raise ValueError('Invalid archived course before image')
    _date(before['created_at_utc'])
    if before['not_before_utc'] is not None:
        _date(before['not_before_utc'])
    row = db.execute('SELECT * FROM learning_question WHERE question_id=?', (before['question_id'],)).fetchone()
    after = {**before, 'status': 'completed'}
    if (row is None or dict(row) != after or value['after_sha256'] != _sha(after)
            or _dump(value['evidence']) != _dump(_evidence(before))
            or db.execute('SELECT 1 FROM learning_event WHERE question_id=?', (before['question_id'],)).fetchone()):
        raise ValueError('Archived course receipt does not own this exact unscored row')
    unit = db.execute('SELECT content_id,ability FROM learning_unit WHERE unit_id=?', (before['unit_id'],)).fetchone()
    question, draft = _load(before['snapshot_json']), _load(before['draft_json'])
    result = _load(before['result_json']) if before['result_json'] is not None else None
    if (unit is None or not isinstance(question, dict) or not isinstance(draft, dict)
            or result is not None and not isinstance(result, dict)
            or question.get('id', question.get('question_id')) != before['question_id']
            or question.get('target_id') != unit['content_id']
            or question.get('ability') != unit['ability'] or question.get('kind') != before['kind']):
        raise ValueError('Archived course row lost its original learning owner')
    return before['question_id']


def _prefix_rows(db, session_id, upto):
    """The frozen prefix [0, upto): questions, answers, word attempts, review events."""
    def ordered(sql):
        return [dict(row) for row in db.execute(sql, (session_id, upto))]
    return {
        'questions': ordered('SELECT question_index,snapshot_json FROM session_question '
                             'WHERE session_id=? AND question_index<? ORDER BY question_index'),
        'answers': ordered('SELECT * FROM session_answer WHERE session_id=? AND question_index<? '
                           'ORDER BY question_index'),
        'spellings': ordered('SELECT * FROM word_spelling_attempt WHERE session_id=? AND question_index<? '
                             'ORDER BY question_index,attempt_id'),
        'events': ordered('SELECT e.* FROM review_event e JOIN session_answer a ON a.event_id=e.event_id '
                          'WHERE a.session_id=? AND a.question_index<? ORDER BY a.question_index,e.event_id'),
    }


def _prefix_digest(rows):
    counts = {part: len(rows[part]) for part in _PARTS}
    digests = {part: hashlib.sha256('\n'.join(_dump(row) for row in rows[part]).encode('utf-8')).hexdigest()
               for part in _PARTS}
    return counts, digests


def _prefix_counts(db, session_id, upto):
    queries = (
        ('questions', 'SELECT COUNT(*) FROM session_question WHERE session_id=? AND question_index<?'),
        ('answers', 'SELECT COUNT(*) FROM session_answer WHERE session_id=? AND question_index<?'),
        ('spellings', 'SELECT COUNT(*) FROM word_spelling_attempt WHERE session_id=? AND question_index<?'),
        ('events', 'SELECT COUNT(*) FROM session_answer a JOIN review_event e ON e.event_id=a.event_id '
                   'WHERE a.session_id=? AND a.question_index<?'))
    return {part: db.execute(sql, (session_id, upto)).fetchone()[0] for part, sql in queries}


def _session_bound(db, session_id):
    """Largest archivable prefix: settled questions below any live checkpoint.

    With a live checkpoint, questions before it are settled (writers only touch
    the current question and the unfinished suffix). Without one, a question is
    settled once answered (formal review or word attempt). Paused word drafts
    are never frozen.
    """
    from .domain import WORD_KIND_PAUSE
    count = db.execute('SELECT COUNT(*) FROM session_question WHERE session_id=?', (session_id,)).fetchone()[0]
    checkpoint = db.execute('SELECT question_index FROM session_checkpoint WHERE session_id=?',
                            (session_id,)).fetchone()
    limit = count if checkpoint is None else min(count, checkpoint[0])
    bound = 0
    for index, raw in db.execute('SELECT question_index,snapshot_json FROM session_question '
                                 'WHERE session_id=? AND question_index<? ORDER BY question_index',
                                 (session_id, limit)).fetchall():
        question = _load(raw)
        if (index != bound or not isinstance(question, dict)
                or WORD_KIND_PAUSE in (question.get('metadata') or {})):
            break
        if checkpoint is None and not db.execute(
                'SELECT 1 FROM session_answer WHERE session_id=? AND question_index=? UNION ALL '
                'SELECT 1 FROM word_spelling_attempt WHERE session_id=? AND question_index=?',
                (session_id, index, session_id, index)).fetchone():
            break
        bound += 1
    return bound


def _validate_prefix(db, key, value, *, deep=True):
    if (not isinstance(value, dict) or set(value) != _SESSION_PART
            or type(value.get('version')) is not int or value['version'] != 1
            or value.get('kind') != 'prefix' or not _text(value.get('session_id'))
            or key != SESSION_PREFIX + value['session_id']
            or type(value.get('upto')) is not int or value['upto'] <= 0
            or not isinstance(value.get('counts'), dict) or set(value['counts']) != set(_PARTS)
            or not isinstance(value.get('sha256'), dict) or set(value['sha256']) != set(_PARTS)):
        raise ValueError('Invalid session prefix archive receipt')
    _date(value['archived_at_utc'])
    sid, upto = value['session_id'], value['upto']
    if db.execute('SELECT 1 FROM study_session WHERE session_id=?', (sid,)).fetchone() is None:
        raise ValueError('Archived session prefix lost its session')
    checkpoint = db.execute('SELECT question_index FROM session_checkpoint WHERE session_id=?', (sid,)).fetchone()
    if checkpoint is not None and checkpoint[0] < upto:
        raise ValueError('Archived session prefix includes the live question')
    if deep:
        counts, digests = _prefix_digest(_prefix_rows(db, sid, upto))
        if digests != value['sha256']:
            raise ValueError('Archived session prefix differs from its frozen rows')
    else:
        counts = _prefix_counts(db, sid, upto)
    if counts != value['counts'] or counts['questions'] != upto:
        raise ValueError('Archived session prefix differs from its frozen rows')


def validate_database(db, *, deep=True) -> set[str]:
    """Validate exact archive exceptions; return only course rows they own.

    deep=False checks session prefix receipts by row counts instead of hashing
    their frozen rows (used inside the local archival transaction); backup and
    restore validation keep the default full check.
    """
    result = set()
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (PREFIX + '*',)):
        value = _load(raw)
        if key.startswith(NATIVE_PREFIX):
            _validate_native(db, key, value)
        elif key.startswith(COURSE_PREFIX) and _table(db, 'learning_question'):
            result.add(_validate_course(db, key, value))
        elif key.startswith(SESSION_PREFIX):
            _validate_prefix(db, key, value, deep=deep)
        else:
            raise ValueError('Unknown practice archive receipt')
    return result


def archived_question(store, question_id):
    """Return its validated long-term receipt, or None; never grant scoring."""
    key = _course_key(question_id)
    row = store._db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
    if row is None:
        return None
    value = _load(row[0])
    _validate_course(store._db, key, value)
    return deepcopy(value)


def archived_session(store, session_id):
    """Read a validated native receipt; absence does not prove a live session."""
    if not _text(session_id) or len(NATIVE_PREFIX + session_id) > 256:
        raise ValueError('Invalid archived session identity')
    key = NATIVE_PREFIX + session_id
    row = store._db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
    if row is None:
        return None
    value = _load(row[0])
    _validate_native(store._db, key, value)
    return deepcopy(value)


def assert_native_answerable(store, session_id):
    """Reject new answers for an archived round, after recorded replay checks."""
    if archived_session(store, session_id) is not None:
        raise ValueError('这轮练习已结束，请开始新一轮练习。')


def archived_attempts(store, *, limit=20) -> list[dict]:
    """Read at most 100 submitted, ungraded observations, newest archive first.

    A checkpoint draft is not an answer. Native checkpoints did not always
    freeze a correctness judgement: keep that unknown instead of grading it
    with today's rules. Already recorded native answers stay in their history.
    """
    if type(limit) is not int or not 0 <= limit <= 100:
        raise ValueError('Archived attempt limit must be between 0 and 100')
    if not limit:
        return []
    db = store._db
    candidates = db.execute("""SELECT key,value_json FROM app_setting
        WHERE key GLOB ? AND (
          (key GLOB ? AND json_extract(value_json,'$.evidence.submitted_unrated')=1
           AND length(trim(json_extract(value_json,'$.evidence.state.answered_text')))>0)
          OR (key GLOB ? AND json_type(value_json,'$.evidence.result')='object'
           AND length(trim(json_extract(value_json,'$.evidence.result.answered_text')))>0
           AND json_extract(value_json,'$.evidence.result.outcome')!='cancelled'))
        ORDER BY json_extract(value_json,'$.archived_at_utc') DESC,key DESC LIMIT ?""",
        (PREFIX + '*', NATIVE_PREFIX + '*', COURSE_PREFIX + '*', limit)).fetchall()
    result = []
    for key, raw in candidates:
        value = _load(raw)
        before = value['before']
        if key.startswith(NATIVE_PREFIX):
            _validate_native(db, key, value)
            sid, index = before['session_id'], before['question_index']
            if (value['head'] is None
                    or db.execute('SELECT 1 FROM session_answer WHERE session_id=? AND question_index=?', (sid, index)).fetchone()
                    or db.execute('SELECT 1 FROM word_spelling_attempt WHERE session_id=? AND question_index=?', (sid, index)).fetchone()):
                continue
            state = value['evidence']['state']
            question = _load(value['head']['snapshot_json'])
            from .domain import session_practice_lane
            lane = session_practice_lane([question])
            correct = state.get('answer_correct') if type(state.get('answer_correct')) is bool else None
            entry = {'lane': lane, 'content_id': question['content_id'],
                     'answered_text': state['answered_text'], 'correct': correct,
                     'outcome': ('correct' if correct else 'incorrect') if correct is not None else 'unrated',
                     'at': state.get('submitted_at_utc') or before['updated_at_utc'],
                     'question': question, 'session_id': sid, 'question_index': index}
        else:
            _validate_course(db, key, value)
            observation = value['evidence']['result']
            question = _load(before['snapshot_json'])
            entry = {'lane': 'course', 'content_id': question['target_id'],
                     'answered_text': observation['answered_text'],
                     'correct': observation.get('correct'), 'outcome': observation.get('outcome'),
                     'at': observation.get('submitted_at_utc') or before['created_at_utc'],
                     'question': question, 'question_id': before['question_id'],
                     'result': deepcopy(observation)}
        entry.update(receipt_key=key, scheduled=False, rating=None)
        result.append(entry)
    return result


def _owned_course(db, row):
    from .retention_session import RESERVATION_PREFIX, handoff_key
    from .retention_sources import guard_key
    keys = (RESERVATION_PREFIX + row['unit_id'], handoff_key(row['question_id']), guard_key(row['question_id']))
    if db.execute('SELECT 1 FROM app_setting WHERE key IN (?,?,?)', keys).fetchone():
        return 'retention'
    question = _load(row['snapshot_json'])
    if (question.get('ai_generated') or question.get('tutor_practice')
            or str(question.get('target_id', '')).startswith(('local:ai:', 'material-candidate:'))):
        return 'ai'
    return None


_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')


def untouched_course_questions(db, rows) -> set[str]:
    """The ids among ``rows`` that the learner never met; the one definition of "untouched".

    ``rows``: pending learning_question rows that no independent protocol owns
    (_owned_course: no retention reservation, handoff or source guard, not AI
    work). Such a row is untouched exactly when all of these hold:
    (1) result_json is NULL: no submitted answer;
    (2) draft_json decodes to {}: no draft at all. The question page stores
        {'presented': True} when it shows a course question, so a question that
        was ever on screen has a draft key;
    (3) its snapshot has no 'replacement_of': it is not the new end of a
        replacement chain, whose receipt names it;
    (4) no learning_event row has its question_id;
    (5) no learning_evidence row's snapshot names it as question_id (only rows
        written at or after the earliest creation among ``rows`` can, so only
        those are read);
    (6) no other stored row names it: no app_setting key outside this module's
        receipts contains the id or the sha256 hex of the id or of its JSON
        text (the hashed keys of handoffs, guards and replacement receipts), and
        neither those app_setting values nor any learning_content checkpoint
        contain the id as a complete JSON string. This module's own receipts
        are left out: each owns only a completed row, never a pending one.
    Anything that fails a test is archived as before. The test is read-only.
    """
    rows = [row for row in rows if row['result_json'] is None and _load(row['draft_json']) == {}
            and 'replacement_of' not in _load(row['snapshot_json'])]
    if not rows:
        return set()
    ids = {row['question_id'] for row in rows}
    used = {row[0] for row in db.execute('SELECT question_id FROM learning_event')}
    if _table(db, 'learning_evidence'):
        # Older rows are filtered by their time column alone (the inner select never reads a snapshot);
        # only the remaining rows' snapshots are parsed. Both columns hold UTC instants; julianday
        # compares them by value (to the millisecond, ties are scanned), and a time it cannot read is
        # still scanned.
        earliest = min((row['created_at_utc'] for row in rows), key=datetime.fromisoformat)
        used.update(row[0] for row in db.execute(
            "SELECT json_extract(snapshot_json,'$.question_id') FROM learning_evidence WHERE rowid IN "
            "(SELECT rowid FROM learning_evidence WHERE NOT julianday(occurred_at_utc) < julianday(?))",
            (earliest,)))
    keys, strings = [], set()
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key NOT GLOB ?', (PREFIX + '*',)):
        keys.append(key)
        strings.update(_JSON_STRING.findall(raw))
    for (raw,) in db.execute('SELECT checkpoint_json FROM learning_content'):
        strings.update(_JSON_STRING.findall(raw))
    keys = '\n'.join(keys)
    result = set()
    for question_id in ids - used:
        names = (question_id, hashlib.sha256(question_id.encode('utf-8')).hexdigest(),
                 hashlib.sha256(_dump(question_id).encode('utf-8')).hexdigest())
        texts = {json.dumps(question_id, ensure_ascii=False), json.dumps(question_id)}
        if not any(name in keys for name in names) and not texts & strings:
            result.add(question_id)
    return result


def _validate_written(db, keys):
    """Validate exactly the receipts this pass wrote, each once, from their stored text."""
    for key in keys:
        value = _load(db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()[0])
        if key.startswith(NATIVE_PREFIX):
            _validate_native(db, key, value)
        else:
            _validate_course(db, key, value)


def archive_previous_rounds(store, *, now=None):
    """One idempotent commit ending local plans, preserving every observation.

    Retention reservations, handoffs, source guards and AI-owned course work
    belong to their independent protocols and remain pending. An untouched
    course question (untouched_course_questions) leaves the queue without a
    trace; every other pending course question is archived. Only the receipts
    this pass writes are validated here: the full check of all receipts runs
    where a record comes in from outside (backup inspection, restore, legacy
    conversion: learning_store.validate_backup_rows).
    """
    from .domain import _utc
    db = store._db
    if db.in_transaction:
        raise RuntimeError('Practice archival owns its transaction')
    stamp = _utc(now).isoformat()
    summary = {'native': 0, 'course': 0, 'skipped_course': 0, 'skipped_owners': {}}
    with db:
        db.execute('BEGIN IMMEDIATE')
        written = []
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
            written.append(key)
            summary['native'] += 1
        if _table(db, 'learning_question'):
            unowned = []
            for row in db.execute("SELECT * FROM learning_question WHERE status='pending'").fetchall():
                owner = _owned_course(db, row)
                if owner:
                    summary['skipped_course'] += 1
                    summary['skipped_owners'][owner] = summary['skipped_owners'].get(owner, 0) + 1
                    continue
                unowned.append(row)
            untouched = untouched_course_questions(db, unowned)
            for row in unowned:
                if row['question_id'] in untouched:
                    db.execute("DELETE FROM learning_question WHERE question_id=? AND status='pending'",
                               (row['question_id'],))
                    continue
                before = dict(row)
                value = {'version': 1, 'kind': 'course', 'archived_at_utc': stamp,
                         'before': before, 'before_sha256': _sha(before),
                         'after_sha256': _sha({**before, 'status': 'completed'}),
                         'evidence': _evidence(before)}
                key = _course_key(row['question_id'])
                db.execute('INSERT INTO app_setting VALUES (?,?)', (key, _dump(value)))
                db.execute("UPDATE learning_question SET status='completed' WHERE question_id=?", (row['question_id'],))
                written.append(key)
                summary['course'] += 1
        _validate_written(db, written)
    return summary


def archive_session_prefix(store, session_id, *, upto=None, now=None):
    """Explicitly freeze a session's settled prefix; never triggered automatically.

    Rows stay in place, so learning IDs (session_id, question_index), answers,
    review events, due times, grading idempotency and the backup record format
    are unchanged. The receipt records the bound plus counts and sha256 of the
    prefix questions/answers/word attempts/review events, so readers can treat
    [0, upto) as immutable history and work on the unfinished suffix only.
    Re-running with the same bound is a no-op; a bound can only grow.
    """
    from .domain import _utc
    db = store._db
    if db.in_transaction:
        raise RuntimeError('Practice archival owns its transaction')
    if not _text(session_id) or len(SESSION_PREFIX + session_id) > 256:
        raise ValueError('Invalid archived session identity')
    key = SESSION_PREFIX + session_id
    with db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM study_session WHERE session_id=?', (session_id,)).fetchone() is None:
            raise ValueError('Unknown session_id')
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        previous = _load(row[0]) if row else None
        if previous is not None:
            _validate_prefix(db, key, previous)
        bound = _session_bound(db, session_id)
        target = bound if upto is None else upto
        if type(target) is not int or not 0 < target <= bound:
            raise ValueError('Only settled questions before the live question can be archived')
        if previous is not None and target <= previous['upto']:
            if target < previous['upto']:
                raise ValueError('An archived session prefix cannot shrink')
            return {'session_id': session_id, 'upto': target, 'changed': False}
        counts, digests = _prefix_digest(_prefix_rows(db, session_id, target))
        value = {'version': 1, 'kind': 'prefix', 'archived_at_utc': _utc(now).isoformat(),
                 'session_id': session_id, 'upto': target, 'counts': counts, 'sha256': digests}
        db.execute('INSERT INTO app_setting VALUES (?,?) '
                   'ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json', (key, _dump(value)))
        _validate_prefix(db, key, value)
    return {'session_id': session_id, 'upto': target, 'changed': True}


def archived_prefix(store, session_id, *, rows=False):
    """Validated prefix receipt (with rows=True also its frozen rows), or None.

    Without rows the check is count-based and cheap, so a reader may skip
    [0, upto). rows=True re-hashes the prefix and returns detached question,
    answer, word attempt and review event rows.
    """
    if not _text(session_id) or len(SESSION_PREFIX + session_id) > 256:
        raise ValueError('Invalid archived session identity')
    key = SESSION_PREFIX + session_id
    db = store._db
    row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
    if row is None:
        return None
    value = _load(row[0])
    _validate_prefix(db, key, value, deep=rows)
    if rows:
        frozen = _prefix_rows(db, session_id, value['upto'])
        frozen['questions'] = [{'question_index': part['question_index'], 'question': _load(part['snapshot_json'])}
                               for part in frozen['questions']]
        value['rows'] = frozen
    return value
