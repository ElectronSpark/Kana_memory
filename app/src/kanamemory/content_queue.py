"""Retire only unsubmitted tasks whose content basis changed.

Receipts keep full question/draft/ownership snapshots. Retirement is neither
answering nor completing a task; disabled kinds and input settings never retire
anything. Current queues can continue without a fabricated cancelled grade.
"""
from copy import deepcopy
import json

from .material_workflow import digest, dump

PREFIX = 'content_queue_v1:'


def prepare(db, rows, binding_ids):
    grouped = {}
    for hit in rows:
        if hit['kind'] != 'question' or hit['binding_id'] not in binding_ids: continue
        locator = hit['locator']
        if not locator.startswith(('ai_tutor_v1/', 'ai_practice_v1/', 'retention_check_v1:', 'retention_check_v2:')) or locator.endswith(':stored_original'): continue
        grouped.setdefault(locator, []).append(deepcopy(hit))
    result = []
    for locator, hits in sorted(grouped.items()):
        key, *path = locator.split('/')
        raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        if not raw: continue
        value = json.loads(raw[0]); entry = value
        for part in path: entry = entry[int(part)] if isinstance(entry, list) else entry[part]
        if entry.get('result') is not None or entry.get('completed') or entry.get('status') == 'completed' or entry.get('content_retirement'):
            continue
        result.append({'key': key, 'entry': deepcopy(entry), 'hits': sorted(hits, key=lambda h: h['id'])})
    from .session_content import prepare as prepare_sessions
    return result + prepare_sessions(db, rows, binding_ids)


def apply_in_transaction(store, content_event, plan, group, candidates, inventory, *, withdrawal=False):
    from .content_history import _verdict
    db = store._db
    if not db.in_transaction: raise RuntimeError('Queue retirement requires content transaction')
    states = {}; changed = set()
    for c in candidates:
        key = c['key']
        ordinary = key.startswith('session_question:')
        if ordinary:
            entry = c['entry']
        else:
            if key not in states: states[key] = store.get_setting(key)
            state = states[key]; entry = next((r for r in state['entries'] if r['id'] == c['entry']['id']), None)
        if entry != c['entry']: raise ValueError('待处理题目或草稿已变化，请重新准备修订。')
        q = entry['question']
        verdict, reasons = _verdict(store, {'question': q, 'target_id': q.get('target_id') or q.get('content_id'), 'hits': c['hits']},
            {o['binding_id']: o for o in group['operations']}, withdrawal=withdrawal, inventory=inventory)
        if verdict == 'valid': continue
        changed.add(key)
        receipt = {'version': 1, 'content_event_id': content_event['id'], 'generation': content_event['generation'],
            'created_at': content_event['created_at'], 'plan_id': plan['id'], 'group_id': group['id'],
            'key': key, 'entry': deepcopy(entry), 'verdict': verdict, 'reasons': reasons, 'hits': c['hits']}
        receipt['id'] = digest(receipt)
        db.execute('INSERT INTO app_setting VALUES (?,?)', (PREFIX + receipt['id'], dump(receipt)))
        if ordinary:
            from .session_content import retire_in_transaction
            retire_in_transaction(store, c, receipt)
            continue
        entry['content_retirement'] = receipt['id']
        if key in {'ai_tutor_v1', 'ai_practice_v1'}:
            entry['status'] = 'retired'
        elif key.startswith('retention_check_v2:'):
            from .retention_session import RESERVATION_PREFIX, reservation, unit_id, handoff_key
            uid = unit_id(q); owned = reservation(store, uid)
            expected = {'version': 1, 'unit_id': uid, 'episode_id': state['id'],
                        'entry_id': entry['id'], 'question_id': entry['course_question_id']}
            if owned is not None and owned != expected: raise ValueError('检查预约已属于其他题目。')
            if entry['course_question_id'] and entry['presented'] and (
                    entry['purpose'] == 'check_only' or entry['credit_revoked'] or entry['exposed']):
                db.execute('INSERT OR IGNORE INTO app_setting VALUES (?,?)', (handoff_key(entry['course_question_id']),
                    dump({'version': 1, 'episode_id': state['id'], 'entry_id': entry['id'], 'question_id': entry['course_question_id']})))
            if owned is not None:
                db.execute('DELETE FROM app_setting WHERE key=?', (RESERVATION_PREFIX + uid,))
    for key, state in states.items():
        if key not in changed: continue
        if key == 'ai_tutor_v1':
            from .tutor_store import validate_tutor_value
            state['revision'] += 1; validate_tutor_value(state)
        elif key == 'ai_practice_v1':
            from .ai_practice_store import validate_ai_practice_value
            validate_ai_practice_value(state)
        elif key.startswith('retention_check_v1:'):
            from .retention_check import validate_episode
            if all(r['completed'] or r.get('content_retirement') for r in state['entries']): state['status'] = 'completed'
            validate_episode(state)
        else:
            from .retention_session import validate_episode
            validate_episode(state)
        db.execute('UPDATE app_setting SET value_json=? WHERE key=?', (dump(state), key))


def archived(store, receipt_id):
    value = store.get_setting(PREFIX + receipt_id, None)
    if value is None: raise ValueError('待核题目缺少原始保存记录。')
    return deepcopy(value['entry'])


def retire_material_in_transaction(store, change):
    """Material provenance uses the same saved entry and reservation contract."""
    from .material_validity import verdict
    from .personal_content_versions import GENERATION
    db = store._db
    if not db.in_transaction: raise RuntimeError('Material retirement requires the decision transaction')
    for key, raw in db.execute("SELECT key,value_json FROM app_setting WHERE key IN ('ai_tutor_v1','ai_practice_v1') OR key GLOB 'retention_check_v[12]:*'").fetchall():
        state = json.loads(raw); changed = False
        for entry in state['entries']:
            if (entry.get('result') is not None or entry.get('completed') or entry.get('status') in {'completed', 'retired'}
                    or entry.get('content_retirement')): continue
            decision = verdict(change, entry['question'], material_id=state.get('material_id'))
            if decision is None: continue
            receipt = {'version': 2, 'change_id': change['id'], 'generation': store.get_setting(GENERATION, 0),
                'created_at': change['created_at'], 'key': key, 'entry': deepcopy(entry),
                'material_id': state.get('material_id'), 'verdict': decision}
            receipt['id'] = digest(receipt)
            db.execute('INSERT INTO app_setting VALUES (?,?)', (PREFIX + receipt['id'], dump(receipt)))
            entry['content_retirement'] = receipt['id']; changed = True
            if key in {'ai_tutor_v1', 'ai_practice_v1'}:
                entry['status'] = 'retired'
            elif key.startswith('retention_check_v2:'):
                from .retention_session import RESERVATION_PREFIX, reservation, unit_id, handoff_key
                uid = unit_id(entry['question']); owned = reservation(store, uid)
                expected = {'version': 1, 'unit_id': uid, 'episode_id': state['id'],
                    'entry_id': entry['id'], 'question_id': entry['course_question_id']}
                if owned is not None and owned != expected: raise ValueError('检查预约已属于其他题目。')
                if entry['course_question_id'] and entry['presented'] and (
                        entry['purpose'] == 'check_only' or entry['credit_revoked'] or entry['exposed']):
                    db.execute('INSERT OR IGNORE INTO app_setting VALUES (?,?)', (handoff_key(entry['course_question_id']),
                        dump({'version': 1, 'episode_id': state['id'], 'entry_id': entry['id'], 'question_id': entry['course_question_id']})))
                if owned is not None: db.execute('DELETE FROM app_setting WHERE key=?', (RESERVATION_PREFIX + uid,))
        if not changed: continue
        if key == 'ai_tutor_v1':
            from .tutor_store import validate_tutor_value
            state['revision'] += 1; validate_tutor_value(state)
        elif key == 'ai_practice_v1':
            from .ai_practice_store import validate_ai_practice_value
            validate_ai_practice_value(state)
        elif key.startswith('retention_check_v1:'):
            from .retention_check import validate_episode
            if all(r['completed'] or r.get('content_retirement') for r in state['entries']): state['status'] = 'completed'
            validate_episode(state)
        else:
            from .retention_session import validate_episode
            validate_episode(state)
        db.execute('UPDATE app_setting SET value_json=? WHERE key=?', (dump(state), key))


def validate_database(db):
    from .personal_content_versions import PREFIX as ADOPT
    from .personal_content_compensation import PREFIX as WITHDRAW
    from .content_history import _HistoricalCache, _inventory, _verdict
    from .content_correction_plan import PREFIX as PLAN
    from .feedback_impact import PREFIX as IMPACT
    def setting(key):
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None
    receipts = {}
    with _HistoricalCache(db) as history:
        for key, raw in db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB ? ORDER BY json_extract(value_json,'$.generation'),rowid", (PREFIX + '*',)):
            r = json.loads(raw)
            if r.get('version') == 2:
                from .material_validity import CHANGES, verdict
                change = setting(CHANGES + r.get('change_id', ''))
                owner = setting(r.get('key', '')) or {}
                if (set(r) != {'version', 'id', 'change_id', 'generation', 'created_at', 'key', 'entry', 'material_id', 'verdict'}
                        or r['id'] != digest({k:v for k,v in r.items() if k != 'id'}) or key != PREFIX + r['id']
                        or not change or r['created_at'] != change['created_at']
                        or not owner or r['material_id'] != owner.get('material_id')
                        or r['entry'].get('result') is not None or r['entry'].get('completed') or r['entry'].get('content_retirement')
                        or r['verdict'] != verdict(change, r['entry']['question'], material_id=r['material_id'])):
                    raise ValueError('材料待核题目缺少原始资格变更或保存记录。')
                receipts[r['id']] = r
                continue
            if (set(r) != {'version', 'id', 'content_event_id', 'generation', 'created_at', 'plan_id', 'group_id', 'key', 'entry', 'verdict', 'reasons', 'hits'}
                    or r['version'] != 1 or r['id'] != digest({k: v for k, v in r.items() if k != 'id'}) or key != PREFIX + r['id']):
                raise ValueError('待核题目保存回执无效。')
            event = setting(ADOPT + r['content_event_id']) or setting(WITHDRAW + r['content_event_id'])
            plan = setting(PLAN + r['plan_id'])
            if not event or not plan or (event['generation'], event['created_at']) != (r['generation'], r['created_at']):
                raise ValueError('待核题目缺少内容修订。')
            adoption = setting(ADOPT + event['adoption_id']) if 'adoption_id' in event else event
            if (adoption['plan_id'], adoption['group_id']) != (r['plan_id'], r['group_id']):
                raise ValueError('待核题目属于其他内容修订。')
            group = next(g for g in plan['groups'] if g['id'] == r['group_id'])
            entry = r['entry']; q = entry['question']
            if entry['result'] is not None or entry.get('completed') or entry.get('content_retirement'):
                raise ValueError('已作答题目不能当成未答题撤下。')
            inv = _inventory(db, plan, group, setting(IMPACT + plan['impact_id']))
            matches = {h[0] for h in inv.match_question(q)}
            if not r['hits'] or any(h['binding_id'] not in group['binding_ids'] or (
                    h['binding_id'] not in matches and (q.get('target_id') or q.get('content_id')) not in inv.scope[h['binding_id']]) for h in r['hits']):
                raise ValueError('待核题目没有对应内容依赖。')
            verdict, reasons = _verdict(history.at(r['generation']), {'question': q, 'target_id': q.get('target_id') or q.get('content_id'), 'hits': r['hits']},
                {o['binding_id']: o for o in group['operations']}, withdrawal='adoption_id' in event, inventory=inv)
            if verdict == 'valid' or (verdict, reasons) != (r['verdict'], r['reasons']):
                raise ValueError('待核题目与当时内容版本不一致。')
            receipts[r['id']] = r
    for key, raw in db.execute("SELECT key,value_json FROM app_setting WHERE key IN ('ai_tutor_v1','ai_practice_v1') OR key GLOB 'retention_check_v[12]:*'"):
        state = json.loads(raw)
        for entry in state['entries']:
            identity = entry.get('content_retirement')
            if not identity: continue
            receipt = receipts.get(identity)
            restored = {k: v for k, v in entry.items() if k != 'content_retirement'}
            if key in {'ai_tutor_v1', 'ai_practice_v1'}: restored['status'] = 'pending'
            if not receipt or receipt['key'] != key or receipt['entry'] != restored:
                raise ValueError('待核题目原草稿被修改或回执缺失。')
        if key == 'ai_tutor_v1':
            for entry in state['entries']:
                old_id = entry.get('replacement_of')
                if not old_id:
                    if 'opportunity_root' in entry: raise ValueError('辅导替代机会缺少原题。')
                    continue
                originals = [r['entry'] for r in receipts.values() if r['key'] == key and r['entry']['id'] == old_id]
                if len(originals) != 1: raise ValueError('辅导替代缺少唯一保存的原题。')
                old = originals[0]
                if ((entry['question']['target_id'], entry['question']['ability'], entry.get('anchor_event_id'))
                        != (old['question']['target_id'], old['question']['ability'], old.get('anchor_event_id'))
                        or entry.get('opportunity_root') != old.get('opportunity_root', old['id'])
                        or any(old['draft'].get(k) and not entry['draft'].get(k) for k in ('assisted', 'hint_used'))):
                    raise ValueError('辅导替代不能换目标或提升原机会权限。')
    from .session_content import validate_database as validate_sessions
    validate_sessions(db, receipts)
