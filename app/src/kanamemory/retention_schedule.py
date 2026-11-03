"""Optional return-to-study prompt and bounded confirmation queue overlays.

Neither visits nor overlays are memory observations. The ordinary scheduler
alone owns card state; an overlay can only make an authorized opportunity earlier.
"""
from datetime import timedelta
from hashlib import sha256
import json

from . import app_clock

ACTIVITY = 'retention_activity_v1'
PREFIX = 'retention_confirmation_v1:'
POLICY = 'retention_schedule_policy_v1'
DEFAULT = {'gap_days': 30, 'confirm_hours': 24, 'expiry_days': 7}


def stamp(text):
    from .retention_check import _date
    return _date(text)


def policy(store):
    value = store.get_setting(POLICY, DEFAULT)
    validate_policy(value)
    return value


def validate_policy(value):
    if (not isinstance(value, dict) or set(value) != set(DEFAULT)
            or any(type(v) is not int for v in value.values())
            or not 7 <= value['gap_days'] <= 365
            or not 1 <= value['confirm_hours'] <= 168
            or not 1 <= value['expiry_days'] <= 30
            or value['confirm_hours'] >= value['expiry_days'] * 24):
        raise ValueError('Invalid retention scheduling policy')
    return value


def validate_activity(value):
    from .retention_session import _identity
    if (not isinstance(value, dict) or set(value) != {'version', 'last_seen', 'previous_seen', 'cycle_id', 'cycle_started', 'dismissed'}
            or value['version'] != 1 or type(value['version']) is not int or type(value['dismissed']) is not bool):
        raise ValueError('Invalid retention activity')
    stamp(value['last_seen'])
    if value['cycle_id'] is None:
        if value['cycle_started'] is not None or value['previous_seen'] is not None:
            raise ValueError('Invalid retention cycle')
    elif (not _identity(value['cycle_id']) or stamp(value['previous_seen']) >= stamp(value['cycle_started'])
            or stamp(value['cycle_started']) > stamp(value['last_seen'])):
        raise ValueError('Invalid retention cycle')
    return value


def validate_confirmation(value):
    from .retention_session import _identity
    keys = {'version', 'id', 'unit_id', 'anchor_event_id', 'episode_id', 'cycle_id', 'created_at',
            'not_before', 'due', 'expires_at', 'status', 'ended_at', 'consumed_by'}
    if (not isinstance(value, dict) or set(value) != keys or type(value['version']) is not int or value['version'] != 1
            or any(not _identity(value[k]) for k in ('id', 'unit_id', 'episode_id'))
            or value['cycle_id'] is not None and not _identity(value['cycle_id'])
            or not isinstance(value['anchor_event_id'], str) or not value['anchor_event_id']
            or value['status'] not in {'active', 'consumed', 'expired', 'revoked', 'superseded'}):
        raise ValueError('Invalid retention confirmation')
    if not stamp(value['created_at']) < stamp(value['not_before']) <= stamp(value['due']) < stamp(value['expires_at']):
        raise ValueError('Invalid confirmation timing')
    if value['status'] == 'active':
        if value['ended_at'] is not None or value['consumed_by'] is not None:
            raise ValueError('Active confirmation cannot have a terminal receipt')
    elif value['ended_at'] is None or stamp(value['ended_at']) < stamp(value['created_at']):
        raise ValueError('Invalid confirmation ending')
    if value['status'] == 'consumed' and (not isinstance(value['consumed_by'], str) or not value['consumed_by']):
        raise ValueError('Missing confirmation consumption event')
    return value


def _write(store, key, value):
    if not store._db.in_transaction: raise RuntimeError('Caller transaction required')
    store._db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
                      (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))


def records(store, *, active=False, unit_id=None):
    sql = 'SELECT value_json FROM app_setting WHERE key GLOB ?'
    args = [PREFIX + '*']
    if active: sql += " AND json_extract(value_json,'$.status')='active'"
    if unit_id is not None:
        sql += " AND json_extract(value_json,'$.unit_id')=?"; args.append(unit_id)
    return [validate_confirmation(json.loads(row[0])) for row in store._db.execute(sql, args)]


def end(store, row, reason, now, event=None):
    row = {**row, 'status': reason, 'ended_at': max(now, stamp(row['created_at'])).isoformat(), 'consumed_by': event}
    validate_confirmation(row); _write(store, PREFIX + row['id'], row)


def visit(store, *, now=None):
    now = now or app_clock.utc_now(); rules = policy(store)
    with store._db:
        store._db.execute('BEGIN IMMEDIATE')
        value = store.get_setting(ACTIVITY, None)
        if value is None:
            # Existing learners can get an optional prompt on their first visit
            # after upgrade; read timestamps only, never infer new mastery.
            last = None
            if store._db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_event'").fetchone():
                row = store._db.execute('SELECT max(occurred_at_utc) FROM learning_event').fetchone()
                last = row[0] if row else None
            value = {'version': 1, 'last_seen': last or now.isoformat(), 'previous_seen': None,
                     'cycle_id': None, 'cycle_started': None, 'dismissed': False}
        validate_activity(value)
        previous = stamp(value['last_seen'])
        if now - previous >= timedelta(days=rules['gap_days']):
            value.update(previous_seen=previous.isoformat(), cycle_started=now.isoformat(), dismissed=False,
                         cycle_id=sha256((previous.isoformat() + '/' + now.isoformat()).encode()).hexdigest())
        value['last_seen'] = max(now, previous).isoformat()
        _write(store, ACTIVITY, value)
        for row in records(store, active=True):
            if stamp(row['expires_at']) <= now: end(store, row, 'expired', now)
        return value


def dismiss(store):
    value = store.get_setting(ACTIVITY, None)
    if value:
        validate_activity(value)
        store.set_setting(ACTIVITY, {**value, 'dismissed': True})


def after_formal_event(store, unit_id, event_id, now, *, episode_id=None):
    """Consume an existing overlay; optionally add one after authorized checking."""
    previous = records(store, unit_id=unit_id)
    for row in previous:
        if row['status'] == 'active': end(store, row, 'consumed', now, event_id)
    if episode_id is None: return
    activity = store.get_setting(ACTIVITY, None)
    cycle = validate_activity(activity)['cycle_id'] if activity else None
    if cycle and any(row['cycle_id'] == cycle for row in previous): return
    identity = sha256((unit_id + '/' + (cycle or event_id)).encode()).hexdigest()
    if store.get_setting(PREFIX + identity, None) is not None: return
    rules = policy(store); due = now + timedelta(hours=rules['confirm_hours'])
    row = {'version': 1, 'id': identity, 'unit_id': unit_id, 'anchor_event_id': event_id,
           'episode_id': episode_id, 'cycle_id': cycle, 'created_at': now.isoformat(),
           'not_before': due.isoformat(), 'due': due.isoformat(),
           'expires_at': (now + timedelta(days=rules['expiry_days'])).isoformat(),
           'status': 'active', 'ended_at': None, 'consumed_by': None}
    validate_confirmation(row); _write(store, PREFIX + identity, row)


def revoke(store, episode_id, now):
    for row in records(store, active=True):
        if row['episode_id'] == episode_id: end(store, row, 'revoked', now)


def active_due(store, now):
    from .retention_session import latest_anchor
    result = {}
    for row in records(store, active=True):
        if stamp(row['expires_at']) > now and latest_anchor(store._db, row['unit_id']) == row['anchor_event_id']:
            result[row['unit_id']] = max(row['not_before'], row['due'])
    return result


def validate_database(db):
    for saved in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (PREFIX + '*',)):
        row = validate_confirmation(json.loads(saved['value_json']))
        if saved['key'] != PREFIX + row['id']: raise ValueError('Confirmation identity mismatch')
        event = db.execute('SELECT unit_id,role,result_json FROM learning_event WHERE event_id=?', (row['anchor_event_id'],)).fetchone()
        if event is None or event['role'] != 'primary' or event['unit_id'] != row['unit_id']:
            raise ValueError('Confirmation anchor is not a formal event')
        from .retention_session import PREFIX as EPISODES
        episode = db.execute('SELECT value_json FROM app_setting WHERE key=?', (EPISODES + row['episode_id'],)).fetchone()
        if episode is None or not any(e['settlement'] and e['settlement']['scheduled']
                and e['course_question_id'] == json.loads(event['result_json'])['question_id']
                for e in json.loads(episode[0])['entries']):
            raise ValueError('Confirmation lacks an authorized diagnostic receipt')
        if row['status'] == 'consumed':
            receipt = db.execute('SELECT unit_id,role FROM learning_event WHERE event_id=?', (row['consumed_by'],)).fetchone()
            if receipt is None or receipt['unit_id'] != row['unit_id'] or receipt['role'] != 'primary':
                raise ValueError('Invalid confirmation consumption')
