"""Explicit ownership choices for old shared copies with incomplete provenance."""
from copy import deepcopy

from . import app_clock
from .material_workflow import digest, dump, _date
from .material_verification import proposal_content
from . import material_usage as usage

SETTING = 'material_source_decisions_v1'


def empty():
    return {'version': 1, 'events': []}


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'events'} or value['version'] != 1
            or not isinstance(value['events'], list) or len(value['events']) > 1000
            or len(dump(value).encode('utf-8')) > 8 * 1024 * 1024):
        raise ValueError('副本来源决定格式无效。')
    seen = set()
    for event in value['events']:
        if (not isinstance(event, dict) or set(event) != {'request_id', 'preview', 'preview_sha256', 'external', 'at'}
                or not isinstance(event['request_id'], str) or not 1 <= len(event['request_id']) <= 256
                or event['request_id'] in seen or event['preview_sha256'] != digest(event['preview'])
                or type(event['external']) is not bool or not _date(event['at'])):
            raise ValueError('副本来源决定记录无效。')
        preview = event['preview']
        if (not isinstance(preview, dict) or set(preview) != {'proposal_id', 'generation', 'usage_sha256', 'rows'}
                or not isinstance(preview['proposal_id'], str) or type(preview['generation']) is not int
                or not isinstance(preview['rows'], list) or not 1 <= len(preview['rows']) <= 200):
            raise ValueError('副本来源预览无效。')
        selected = set()
        for row in preview['rows']:
            if (not isinstance(row, dict) or set(row) != {'group', 'id', 'sha256', 'owners', 'external', 'snapshot'}
                    or row['group'] not in {'examples', 'references'} or not isinstance(row['id'], str)
                    or (row['group'], row['id']) in selected or not isinstance(row['sha256'], str)
                    or not isinstance(row['owners'], list) or not row['owners']
                    or row['external'] is not None and type(row['external']) is not bool):
                raise ValueError('副本来源对象无效。')
            if not isinstance(row['snapshot'], dict) or usage.snapshot_hash(row['snapshot'], row['group']) != row['sha256']:
                raise ValueError('副本来源预览与对象不一致。')
            owner_ids = set()
            for owner in row['owners']:
                if (not isinstance(owner, dict) or set(owner) != {'id', 'sha256'} or not isinstance(owner['id'], str)
                        or owner['id'] in owner_ids or not isinstance(owner['sha256'], str)):
                    raise ValueError('副本来源身份无效。')
                owner_ids.add(owner['id'])
            selected.add((row['group'], row['id']))
        seen.add(event['request_id'])
    return deepcopy(value)


class MaterialSourceStore:
    def __init__(self, store):
        self.store, self.db = store, store._db

    def candidates(self, proposal_id):
        from .ai_materials import AIMaterialStore
        from .example_store import SETTING as EXAMPLES, empty_pack
        materials = AIMaterialStore(self.store)._state()
        proposal = next((p for p in materials['proposals'] if p['id'] == proposal_id), None)
        if not proposal: raise ValueError('原材料不可用。')
        state = usage.read(self.store)
        raw = self.store.get_setting(EXAMPLES, empty_pack())
        result = []
        for group in ('examples', 'references'):
            saved = {r['id']: r for r in raw[group]}
            owners = {}
            for candidate in materials['proposals']:
                for row in candidate['example_pack'][group]:
                    if candidate['decisions'][group][row['id']] == 'kept':
                        owners.setdefault((row['id'], usage.snapshot_hash(row, group)), []).append(candidate)
            for obj in proposal['example_pack'][group]:
                identity, sha = obj['id'], usage.snapshot_hash(obj, group)
                matching = owners.get((identity, sha), [])
                if (proposal['decisions'][group][identity] != 'kept' or identity not in saved
                        or usage.snapshot_hash(saved[identity], group) != sha or not matching):
                    continue
                known = state['managed'][group].get(identity)
                result.append({'group': group, 'id': identity, 'sha256': sha,
                    'snapshot': deepcopy(saved[identity]),
                    'owners': [{'id': p['id'], 'sha256': digest(proposal_content(p))} for p in sorted(matching, key=lambda p: p['id'])],
                    'external': known['external'] if known and known['sha256'] == sha else None})
        return result

    def preview(self, proposal_id, selection):
        if (not isinstance(selection, list) or not 1 <= len(selection) <= 200
                or any(not isinstance(row, dict) or set(row) != {'group', 'id'} for row in selection)):
            raise ValueError('请选择需要处理的副本。')
        wanted = {(row['group'], row['id']) for row in selection}
        rows = [row for row in self.candidates(proposal_id) if (row['group'], row['id']) in wanted]
        if len(wanted) != len(selection) or len(rows) != len(selection):
            raise ValueError('副本版本或来源已变化，请重新选择。')
        state = usage.read(self.store)
        return {'proposal_id': proposal_id, 'generation': state['generation'], 'usage_sha256': digest(state), 'rows': rows}

    def decide(self, expected, *, external, request_id):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            history = validate(self.store.get_setting(SETTING, empty()))
            old = next((e for e in history['events'] if e['request_id'] == request_id), None)
            if old:
                if old['preview'] != expected or old['external'] != external:
                    raise ValueError('同一请求不能替换来源决定。')
                return deepcopy(old)
            selection = [{'group': r['group'], 'id': r['id']} for r in expected['rows']]
            current = self.preview(expected['proposal_id'], selection)
            if current != expected: raise ValueError('来源或使用状态已变化，请重新查看。')
            state = usage.read(self.store)
            for row in current['rows']:
                previous = state['managed'][row['group']].get(row['id'])
                updated = {'sha256': row['sha256'], 'owners': [owner['id'] for owner in row['owners']], 'external': external}
                updated['history'] = deepcopy(previous.get('history', [])) if previous else []
                updated['history'].append({'kind': 'choice', 'request_id': request_id,
                    'before': usage.managed_snapshot(previous), 'after': usage.managed_snapshot(updated)})
                state['managed'][row['group']][row['id']] = updated
            state['generation'] += 1
            event = {'request_id': request_id, 'preview': deepcopy(current), 'preview_sha256': digest(current),
                     'external': external, 'at': app_clock.utc_now().isoformat()}
            history['events'].append(event); validate(history)
            usage.write_in_transaction(self.db, state)
            self.db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
                (SETTING, dump(history)))
        return event


def validate_database(db):
    import json
    from .ai_materials import SETTING as MATERIALS, empty_state, validate_ai_material_state
    from .example_store import SETTING as EXAMPLES, empty_pack, validate_pack
    raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (SETTING,)).fetchone()
    history = validate(json.loads(raw[0])) if raw else empty()
    raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (MATERIALS,)).fetchone()
    proposals = {p['id']: p for p in validate_ai_material_state(json.loads(raw[0]) if raw else empty_state())['proposals']}
    raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (EXAMPLES,)).fetchone()
    pack = validate_pack(json.loads(raw[0]) if raw else empty_pack())
    saved = {group: {row['id']: row for row in pack[group]} for group in ('examples', 'references')}
    raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (usage.SETTING,)).fetchone()
    state = usage.validate(json.loads(raw[0])) if raw else usage.empty()
    receipts = {event['request_id']: event for event in history['events']}
    for event in history['events']:
        for row in event['preview']['rows']:
            current = saved[row['group']].get(row['id'])
            if not current or usage.snapshot_hash(current, row['group']) != row['sha256']:
                raise ValueError('副本来源决定缺少原对象版本。')
            for owner in row['owners']:
                proposal = proposals.get(owner['id'])
                source = next((r for r in proposal['example_pack'][row['group']] if r['id'] == row['id']), None) if proposal else None
                if (not source or digest(proposal_content(proposal)) != owner['sha256']
                        or proposal['decisions'][row['group']][row['id']] != 'kept'
                        or usage.snapshot_hash(source, row['group']) != row['sha256']):
                    raise ValueError('副本来源决定与原保留版本不一致。')
            if event['preview']['proposal_id'] not in {owner['id'] for owner in row['owners']}:
                raise ValueError('副本来源决定未关联所选来源。')
            managed = state['managed'][row['group']].get(row['id'])
            expected = {'sha256': row['sha256'], 'owners': [o['id'] for o in row['owners']], 'external': event['external']}
            if not managed or not any(e['kind'] == 'choice' and e['request_id'] == event['request_id'] and e['after'] == expected
                                      for e in managed.get('history', [])):
                raise ValueError('副本来源决定缺少使用归属。')
    for group, rows in state['managed'].items():
        for identity, managed in rows.items():
            for event in managed.get('history', []):
                if event['kind'] == 'choice':
                    receipt = receipts.get(event['request_id'])
                    if not receipt or not any(r['group'] == group and r['id'] == identity for r in receipt['preview']['rows']):
                        raise ValueError('副本使用投影缺少明确决定。')
