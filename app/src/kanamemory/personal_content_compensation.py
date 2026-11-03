"""Immutable teaching compensation, independent qualification hold and outbox."""
from copy import deepcopy
from datetime import datetime
import json
import sqlite3
from uuid import uuid4

from . import app_clock
from .feedback_impact import token
from .material_workflow import digest, dump
from .retention_ai import _hash

FORMAT = 'kanamemory-personal-content-withdrawal'
PREFIX = 'personal_content_version_withdrawal_v1:'
OUTBOX = 'personal_content_version_outbox_v1:'
HOLD = 'personal_content_version_hold_v1:'


def hold_key(head):
    from .personal_content_versions import HEAD
    return HOLD + head.removeprefix(HEAD)


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'format', 'version', 'id', 'nonce', 'adoption_id',
            'adoption_sha256', 'created_at', 'generation', 'previous_heads', 'restored_heads', 'previous_holds'}
            or value['format'] != FORMAT or type(value['version']) is not int or value['version'] != 1
            or any(not _hash(value[k]) for k in ('id', 'nonce', 'adoption_id', 'adoption_sha256'))
            or value['id'] != digest([FORMAT, value['adoption_id'], value['nonce']])
            or datetime.fromisoformat(value['created_at']).tzinfo is None
            or type(value['generation']) is not int or value['generation'] < 2
            or not isinstance(value['previous_heads'], dict) or not 1 <= len(value['previous_heads']) <= 12
            or not isinstance(value['restored_heads'], dict)
            or set(value['previous_heads']) != set(value['restored_heads'])
            or not isinstance(value['previous_holds'], dict) or set(value['previous_holds']) != set(value['previous_heads'])
            or any(v is not None and not _hash(v) for v in value['previous_holds'].values())
            or any(v != value['adoption_id'] for v in value['previous_heads'].values())
            or any(v is not None and not _hash(v) for v in value['restored_heads'].values())):
        raise ValueError('内容补偿回执无效。')
    from .personal_content_versions import HEAD
    if any(not k.startswith(HEAD) or not _hash(k[len(HEAD):]) for k in value['previous_heads']):
        raise ValueError('内容补偿的拥有者无效。')
    return deepcopy(value)


def write(db, key, value):
    db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json', (key, dump(value)))


def get(store, identity):
    value = store.get_setting(PREFIX + identity, None)
    return validate(value) if value is not None else None


def latest(store, adoption_id):
    row = store._db.execute("SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.adoption_id')=? ORDER BY json_extract(value_json,'$.generation') DESC LIMIT 1",
        (PREFIX + '*', adoption_id)).fetchone()
    return validate(json.loads(row[0])) if row else None


def prepare(versions, adoption_id):
    from .personal_content_versions import GENERATION
    old = latest(versions.store, adoption_id)
    if old: return {'saved': old}
    before = token(versions.db); adopted = versions.get(adoption_id)
    if not adopted or not versions.active(adopted): raise ValueError('此组已有后续修改，不能覆盖后来保存的内容。')
    nonce = digest(uuid4().hex)
    event = validate({'format': FORMAT, 'version': 1, 'id': digest([FORMAT, adoption_id, nonce]), 'nonce': nonce,
        'adoption_id': adoption_id, 'adoption_sha256': digest(adopted), 'created_at': app_clock.utc_now().isoformat(),
        'generation': versions.store.get_setting(GENERATION, 0) + 1,
        'previous_heads': {k: adoption_id for k in adopted['previous_heads']},
        'previous_holds': {k: versions.store.get_setting(hold_key(k), None) for k in adopted['previous_heads']},
        'restored_heads': deepcopy(adopted['previous_heads'])})
    from .content_history import prepare as prepare_history
    plan = versions.plans.get(adopted['plan_id'])
    group = next(g for g in plan['groups'] if g['id'] == adopted['group_id'])
    history = prepare_history(versions.store, plan, group, refresh=True)
    if before != token(versions.db): raise ValueError('准备撤下期间记录已变化。')
    prepared = {'event': event, 'token': before, 'history': history}
    versions._withdraw_prepared = digest(prepared)
    return prepared


def commit(versions, prepared):
    from .personal_content_versions import GENERATION
    if 'saved' in prepared:
        saved = get(versions.store, prepared['saved']['id'])
        if saved != prepared['saved']: raise ValueError('补偿回执已变化。')
        return saved
    event = validate(prepared['event']); saved = get(versions.store, event['id'])
    if saved:
        if saved != event: raise ValueError('补偿回执不能改写。')
        return saved
    if getattr(versions, '_withdraw_prepared', None) != digest(prepared): raise ValueError('请重新准备撤下。')
    with versions.db:
        versions.db.execute('BEGIN IMMEDIATE')
        if token(versions.db) != tuple(prepared['token']): raise ValueError('准备撤下后记录已变化，请重试。')
        adopted = versions.get(event['adoption_id'])
        if (not adopted or not versions.active(adopted) or digest(adopted) != event['adoption_sha256']
                or adopted['previous_heads'] != event['restored_heads']
                or event['generation'] != versions.store.get_setting(GENERATION, 0) + 1):
            raise ValueError('不能覆盖后续内容或拆开联动组。')
        write(versions.db, PREFIX + event['id'], event)
        for key, identity in event['restored_heads'].items():
            write(versions.db, key, identity)
            # Display restoration is not factual requalification.
            write(versions.db, hold_key(key), event['id'])
        write(versions.db, GENERATION, event['generation'])
        write(versions.db, OUTBOX + event['id'], {'event_id': event['id'], 'delivered': False})
        from .content_history import apply_in_transaction
        plan = versions.plans.get(adopted['plan_id'])
        group = next(g for g in plan['groups'] if g['id'] == adopted['group_id'])
        apply_in_transaction(versions.store, event, plan, group, prepared['history'], withdrawal=True)
    return event


def held(store, identity, kind='lesson'):
    from .personal_content_versions import head_key
    return store.get_setting(hold_key(head_key(identity, kind)), None)


def hold_requirements(store):
    result, groups = {}, {}
    if store is None or not getattr(store, '_db', None): return result, groups
    from .personal_content_versions import PREFIX as ADOPT, HEAD, head_key
    for key, raw in store._db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (HOLD + '*',)):
        identity = json.loads(raw)
        if not identity: continue
        head = HEAD + key.removeprefix(HOLD); seen = set()
        while identity:
            if identity in seen: raise ValueError('内容待核补偿链循环。')
            seen.add(identity); event = get(store, identity)
            if event is None or head not in event['previous_heads']: raise ValueError('内容待核标记缺少补偿回执。')
            adopted = store.get_setting(ADOPT + event['adoption_id'], None)
            groups.setdefault(head, set()).update(event['previous_heads'])
            for row in adopted['resources']:
                if head_key(row['id'], row.get('kind', 'lesson')) == head:
                    result.setdefault((row.get('kind', 'lesson'), row['id']), set()).update(row['fields'])
            identity = event['previous_holds'].get(head)
    return result, groups


def holds(store):
    return hold_requirements(store)[0]


def notice(store, event):
    from .personal_content_versions import PREFIX as ADOPT
    from .content_correction_plan import PREFIX as PLAN
    withdrawal = event['format'] == FORMAT
    adopted = store.get_setting(ADOPT + event['adoption_id'], None) if withdrawal else event
    plan = store.get_setting(PLAN + adopted['plan_id'], None)
    result = {'format': 'kanamemory-personal-version-receipt', 'version': 1,
        'id': event['id'], 'event_sha256': digest(event), 'action': 'withdraw' if withdrawal else 'adopt',
        'adoption_id': adopted['id'], 'plan_id': adopted['plan_id'], 'group_id': adopted['group_id'],
        'generation': event['generation'], 'created_at': event['created_at'],
        'restored_heads': deepcopy(event['restored_heads']) if withdrawal else {},
        'feedback_refs': [{k: f[k] for k in ('id', 'input_revision', 'context_sha256')} for f in plan['manifest']['feedback_refs']]}
    return validate_notice(result)


def validate_notice(value):
    if (not isinstance(value, dict) or set(value) != {'format', 'version', 'id', 'event_sha256', 'action',
            'adoption_id', 'plan_id', 'group_id', 'generation', 'created_at', 'restored_heads', 'feedback_refs'}
            or value['format'] != 'kanamemory-personal-version-receipt' or type(value['version']) is not int or value['version'] != 1
            or value['action'] not in {'adopt', 'withdraw'}
            or value['action'] == 'adopt' and value['adoption_id'] != value['id']
            or any(not _hash(value[k]) for k in ('id', 'event_sha256', 'adoption_id', 'plan_id', 'group_id'))
            or type(value['generation']) is not int or value['generation'] < 1
            or datetime.fromisoformat(value['created_at']).tzinfo is None
            or not isinstance(value['restored_heads'], dict)
            or (bool(value['restored_heads']) if value['action'] == 'adopt' else not 1 <= len(value['restored_heads']) <= 12)
            or not isinstance(value['feedback_refs'], list) or not 1 <= len(value['feedback_refs']) <= 10):
        raise ValueError('正式内容同步通知无效。')
    from .feedback_store import _id
    from .personal_content_versions import HEAD
    if any(not k.startswith(HEAD) or not _hash(k[len(HEAD):]) or v is not None and not _hash(v) for k,v in value['restored_heads'].items()):
        raise ValueError('正式内容同步的恢复头无效。')
    for f in value['feedback_refs']:
        if (not isinstance(f, dict) or set(f) != {'id', 'input_revision', 'context_sha256'}
                or not _hash(f['input_revision']) or not _hash(f['context_sha256'])): raise ValueError('正式内容通知反馈归属无效。')
        _id(f['id'])
    return deepcopy(value)


def sync(store, feedback, *, limit=20, identities=None, complete=False):
    """Bounded background retry; exports explicitly sync their entire receipt set."""
    from .personal_content_versions import PREFIX as ADOPT, validate as validate_adoption
    from .content_correction_plan import PREFIX as PLAN
    if not isinstance(getattr(store, '_db', None), sqlite3.Connection): return 0
    params = [ADOPT + '*', PREFIX + '*']; where = '(e.key GLOB ? OR e.key GLOB ?)'
    query = 'SELECT e.value_json FROM app_setting e WHERE ' + where
    order = " ORDER BY json_extract(e.value_json,'$.generation')"
    if complete:
        rows = store._db.execute(query + order, params)
    else:
        limit = max(1, min(100, int(limit)))
        destination = getattr(feedback, '_db', feedback)
        if getattr(store, '_personal_version_sync_destination', None) is not destination:
            store._personal_version_sync_destination = destination; store._personal_version_sync_cursor = 0
        cursor = getattr(store, '_personal_version_sync_cursor', 0)
        delivered = "EXISTS (SELECT 1 FROM app_setting o WHERE o.key=? || json_extract(e.value_json,'$.id') AND json_extract(o.value_json,'$.delivered')=1)"
        pending_rows = store._db.execute(query + ' AND NOT ' + delivered + order + ' LIMIT ?',
            (*params, OUTBOX, limit)).fetchall()
        verified_rows = store._db.execute(query + ' AND ' + delivered + " AND json_extract(e.value_json,'$.generation')>?" + order + ' LIMIT ?',
            (*params, OUTBOX, cursor, limit - len(pending_rows))).fetchall()
        if len(pending_rows) < limit:
            store._personal_version_sync_cursor = json.loads(verified_rows[-1][0])['generation'] if verified_rows else 0
        rows = pending_rows + verified_rows
    records = []
    for raw, in rows:
        value = json.loads(raw); value = validate(value) if value['format'] == FORMAT else validate_adoption(value)
        if identities is not None:
            adopted = store.get_setting(ADOPT + value['adoption_id'], None) if value['format'] == FORMAT else value
            plan = store.get_setting(PLAN + adopted['plan_id'], None)
            if not set(identities).intersection(f['id'] for f in plan['manifest']['feedback_refs']): continue
        box = store.get_setting(OUTBOX + value['id'], {})
        records.append((value, box.get('delivered') is True))
    sent = 0
    for event, delivered in records:
        sent += bool(feedback.receive_personal_version_receipt(notice(store, event)))
        if not delivered:
            with store._db:
                store._db.execute('BEGIN IMMEDIATE')
                key = (PREFIX if event['format'] == FORMAT else ADOPT) + event['id']
                if store.get_setting(key, None) == event:
                    write(store._db, OUTBOX + event['id'], {'event_id': event['id'], 'delivered': True})
    return sent


def export_stamp(store):
    from .personal_content_versions import GENERATION, PREFIX as ADOPT, HEAD
    if not isinstance(getattr(store, '_db', None), sqlite3.Connection): return 0
    # A restored branch can have the same generation but different event IDs.
    rows = store._db.execute("SELECT key,COALESCE(json_extract(value_json,'$.id'),value_json) FROM app_setting WHERE key=? OR key GLOB ? OR key GLOB ? OR key GLOB ? OR key GLOB ? ORDER BY key",
        (GENERATION, ADOPT + '*', PREFIX + '*', HEAD + '*', HOLD + '*'))
    return digest([tuple(r) for r in rows])


def archive_current(store, feedback, receipt, stamp):
    """Exclude a concurrent content writer during the cross-store archive CAS."""
    db = getattr(store, '_db', None)
    if not isinstance(db, sqlite3.Connection): return feedback.archive_exported(receipt)
    with db:
        db.execute('BEGIN IMMEDIATE')
        if export_stamp(store) != stamp: return None
        return feedback.archive_exported(receipt)
