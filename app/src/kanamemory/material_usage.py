"""Source-scoped use permission, separate from correctness and learner history."""
from copy import deepcopy
import json

from . import app_clock
from .material_workflow import digest, dump
from .material_verification import proposal_content

SETTING = 'material_usage_v1'
GROUPS = ('items', 'examples', 'references')


def empty():
    return {'version': 1, 'generation': 0, 'permissions': {}, 'managed': {'examples': {}, 'references': {}}}


def snapshot_hash(row, group):
    # Experimental order translations are additive display metadata; they are
    # not an ownership certificate and may be merged by the existing importer.
    fields = ('id', 'revision', 'text', 'translation', 'reading') if group == 'examples' else tuple(row)
    return digest({key: row.get(key) for key in fields})


def managed_snapshot(row):
    return {k: deepcopy(row[k]) for k in ('sha256', 'owners', 'external')} if row is not None else None


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'generation', 'permissions', 'managed'}
            or value['version'] != 1 or type(value['generation']) is not int or value['generation'] < 0
            or not isinstance(value['permissions'], dict) or len(value['permissions']) > 1000
            or not isinstance(value['managed'], dict) or set(value['managed']) != {'examples', 'references'}
            or len(dump(value).encode('utf-8')) > 8 * 1024 * 1024):
        raise ValueError('材料使用许可格式无效。')
    for pid, record in value['permissions'].items():
        if (not isinstance(record, dict) or set(record) != {'proposal_sha256', 'events'}
                or not isinstance(pid, str) or not isinstance(record['events'], list) or len(record['events']) > 1024):
            raise ValueError('材料使用许可记录无效。')
        seen = set()
        for event in record['events']:
            if (not isinstance(event, dict) or set(event) != {'request_id', 'selection', 'enabled', 'reason', 'at'}
                    or not isinstance(event['request_id'], str) or not 1 <= len(event['request_id']) <= 256
                    or event['request_id'] in seen or type(event['enabled']) is not bool
                    or not isinstance(event['reason'], str) or len(event['reason']) > 500
                    or not workflow_date(event['at'])):
                raise ValueError('材料使用许可事件无效。')
            validate_selection(event['selection'])
            seen.add(event['request_id'])
    for group, rows in value['managed'].items():
        if not isinstance(rows, dict) or len(rows) > (20000 if group == 'examples' else 50000):
            raise ValueError('材料来源归属过大。')
        for identity, row in rows.items():
            if (not isinstance(identity, str) or not isinstance(row, dict) or set(row) not in ({'sha256', 'owners', 'external'}, {'sha256', 'owners', 'external', 'history'})
                    or not isinstance(row['sha256'], str) or len(row['sha256']) != 64
                    or not isinstance(row['owners'], list) or len(row['owners']) > 1000
                    or any(not isinstance(pid, str) for pid in row['owners']) or len(set(row['owners'])) != len(row['owners'])
                    or type(row['external']) is not bool):
                raise ValueError('材料来源归属无效。')
            history = row.get('history', [])
            if not isinstance(history, list) or len(history) > 2000: raise ValueError('副本使用历史过大。')
            previous = None
            for index, event in enumerate(history):
                if (not isinstance(event, dict) or set(event) != {'kind', 'request_id', 'before', 'after'}
                        or event['kind'] not in {'choice', 'import', 'publication'}
                        or not isinstance(event['request_id'], str)
                        or index and event['before'] != previous
                        or not isinstance(event['after'], dict) or set(event['after']) != {'sha256', 'owners', 'external'}):
                    raise ValueError('副本使用历史不连续。')
                if event['kind'] != 'choice':
                    before, after = event['before'], event['after']
                    if not before or before['sha256'] != after['sha256'] or not set(before['owners']) <= set(after['owners']):
                        raise ValueError('副本发布历史不能移除来源。')
                    if event['kind'] == 'import' and (after['owners'] != before['owners'] or after['external'] is not True):
                        raise ValueError('独立导入不能替换其他来源。')
                    if event['kind'] == 'publication' and (after['external'] != before['external'] or event['request_id'] not in after['owners']):
                        raise ValueError('材料发布历史不一致。')
                previous = event['after']
            if history and previous != managed_snapshot(row): raise ValueError('副本使用投影脱离历史。')
    return deepcopy(value)


def workflow_date(value):
    from .material_workflow import _date
    return _date(value)


def validate_selection(selection):
    if (not isinstance(selection, dict) or set(selection) != {'source', *GROUPS}
            or type(selection['source']) is not bool
            or any(not isinstance(selection[g], list) or len(selection[g]) > 500
                   or any(not isinstance(cid, str) for cid in selection[g])
                   or len(set(selection[g])) != len(selection[g]) for g in GROUPS)
            or not selection['source'] and not any(selection[g] for g in GROUPS)):
        raise ValueError('请选择来源或具体材料。')
    return selection


def read(store):
    return validate(store.get_setting(SETTING, empty()))


def flags(state, proposal):
    result = {'source': True, **{group: set() for group in GROUPS}}
    record = state['permissions'].get(proposal['id'])
    if not record:
        return result
    if record['proposal_sha256'] != digest(proposal_content(proposal)):
        raise ValueError('材料版本已改变，不能套用原使用许可。')
    for event in record['events']:
        selection = event['selection']
        if selection['source']: result['source'] = event['enabled']
        for group in GROUPS:
            if event['enabled']: result[group].difference_update(selection[group])
            else: result[group].update(selection[group])
    return result


def allowed(state, proposal, group=None, identity=None, *, _flags=None):
    current = flags(state, proposal) if _flags is None else _flags
    if not current['source'] or group and identity in current[group]:
        return False
    if group == 'references':
        reference = next((r for r in proposal['example_pack']['references'] if r['id'] == identity), None)
        return bool(reference and reference['example_id'] not in current['examples']
                    and reference['target_id'] not in current['items'])
    return True


def write_in_transaction(db, state):
    if not db.in_transaction: raise RuntimeError('Material usage requires a transaction')
    validate(state)
    db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
        (SETTING, dump(state)))


def register_in_transaction(db, pack, *, proposal=None, previous_pack=None):
    """Record actual publication ownership, before a body/ref becomes shared.

    Pre-existing untracked copies remain independent; do not guess who owned
    old imports from the generic ai-user-reviewed label.
    """
    raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (SETTING,)).fetchone()
    state = validate(json.loads(raw[0])) if raw else empty()
    before = deepcopy(state)
    for group in ('examples', 'references'):
        previous = {row['id']: row for row in (previous_pack or {}).get(group, [])}
        for row in pack.get(group, []):
            sha = snapshot_hash(row, group)
            record = state['managed'][group].setdefault(row['id'],
                {'sha256': sha, 'owners': [], 'external': row['id'] in previous})
            if record['sha256'] != sha:
                raise ValueError('来源归属不能覆盖另一版本。')
            previous = managed_snapshot(record)
            if proposal:
                if proposal['id'] not in record['owners']: record['owners'].append(proposal['id'])
            else: record['external'] = True
            if 'history' in record and previous != managed_snapshot(record):
                record['history'].append({'kind': 'publication' if proposal else 'import',
                    'request_id': proposal['id'] if proposal else 'independent-import',
                    'before': previous, 'after': managed_snapshot(record)})
    if state != before:
        state['generation'] += 1
        write_in_transaction(db, state)


def active_pack(store, pack, *, materials=None, state=None):
    state = read(store) if state is None else state
    if not state['permissions']:
        return deepcopy(pack)
    if materials is None:
        from .ai_materials import AIMaterialStore
        materials = AIMaterialStore(store)._state()
    proposals = {p['id']: p for p in materials['proposals']}
    permissions = {pid: flags(state, p) for pid, p in proposals.items()}
    def usable(row, group):
        record = state['managed'][group].get(row['id'])
        if not record or record['external'] or record['sha256'] != snapshot_hash(row, group):
            return True
        return any(pid in proposals and proposals[pid]['decisions'][group].get(row['id']) == 'kept'
            and allowed(state, proposals[pid], group, row['id'], _flags=permissions[pid]) for pid in record['owners'])
    result = deepcopy(pack)
    result['references'] = [row for row in result['references'] if usable(row, 'references')]
    required = {row['example_id'] for row in result['references']}
    result['examples'] = [row for row in result['examples'] if row['id'] in required or usable(row, 'examples')]
    return result


def project_proposal(store, proposal):
    """Detached current-use view for canonical target resolution, never history."""
    state = read(store)
    current = flags(state, proposal)
    if not current['source']: return None
    result = deepcopy(proposal)
    result['items'] = [row for row in result['items'] if allowed(state, proposal, 'items', row['id'], _flags=current)]
    result['example_pack']['examples'] = [row for row in result['example_pack']['examples']
                                        if allowed(state, proposal, 'examples', row['id'], _flags=current)]
    result['example_pack']['references'] = [row for row in result['example_pack']['references']
                                          if allowed(state, proposal, 'references', row['id'], _flags=current)]
    return result


class MaterialUsageStore:
    def __init__(self, store):
        self.store, self.db = store, store._db

    def change(self, proposal_id, selection, *, enabled, request_id, expected_generation, expected_usage_sha256, reason=''):
        from .ai_materials import AIMaterialStore
        validate_selection(selection)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            state = read(self.store)
            proposal = AIMaterialStore(self.store).get(proposal_id)
            if not proposal: raise ValueError('材料已不可用。')
            record = state['permissions'].setdefault(proposal_id,
                {'proposal_sha256': digest(proposal_content(proposal)), 'events': []})
            wanted = {'request_id': request_id, 'selection': deepcopy(selection), 'enabled': enabled, 'reason': reason}
            old = next((event for event in record['events'] if event['request_id'] == request_id), None)
            if old:
                if any(old[k] != v for k, v in wanted.items()): raise ValueError('同一请求不能替换使用决定。')
                return deepcopy(old)
            # Compare against the pre-command state, before adding an empty
            # per-proposal container for this first use decision.
            existing = read(self.store)
            if state['generation'] != expected_generation or digest(existing) != expected_usage_sha256:
                raise ValueError('材料使用状态已变化，请重新查看。')
            for group in GROUPS:
                objects = proposal['items'] if group == 'items' else proposal['example_pack'][group]
                if not set(selection[group]) <= {row['id'] for row in objects}: raise ValueError('选择含有其他批次的材料。')
            event = {**wanted, 'at': app_clock.utc_now().isoformat()}
            record['events'].append(event); state['generation'] += 1
            if selection['source'] and not enabled:
                from . import material_workflow
                job = material_workflow.MaterialWorkflowStore(self.store).for_request(proposal['request_id'])
                if job and job['status'] == 'running':
                    material_workflow._end_attempt(job, 'cancelled', 'source_withdrawn')
                    material_workflow.write_in_transaction(self.db, job)
            write_in_transaction(self.db, state)
        return deepcopy(event)


def validate_database(db):
    from .ai_materials import SETTING as MATERIALS, empty_state, validate_ai_material_state
    from .example_store import SETTING as EXAMPLES, empty_pack, validate_pack
    raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (SETTING,)).fetchone()
    if not raw: return
    state = validate(json.loads(raw[0]))
    raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (MATERIALS,)).fetchone()
    proposals = {p['id']: p for p in validate_ai_material_state(json.loads(raw[0]) if raw else empty_state())['proposals']}
    raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (EXAMPLES,)).fetchone()
    pack = validate_pack(json.loads(raw[0]) if raw else empty_pack())
    for pid, record in state['permissions'].items():
        proposal = proposals.get(pid)
        if not proposal or record['proposal_sha256'] != digest(proposal_content(proposal)):
            raise ValueError('使用许可缺少对应材料版本。')
        for event in record['events']:
            for group in GROUPS:
                objects = proposal['items'] if group == 'items' else proposal['example_pack'][group]
                if not set(event['selection'][group]) <= {row['id'] for row in objects}:
                    raise ValueError('使用许可关联了其他材料。')
    for group, records in state['managed'].items():
        rows = {r['id']: r for r in pack[group]}
        for identity, record in records.items():
            # Bundle-only imports may be pruned from the custom storage. Only
            # owned active snapshots must be present; external receipts are inert.
            if record['owners'] and (identity not in rows or snapshot_hash(rows[identity], group) != record['sha256']):
                raise ValueError('材料来源归属缺少原快照。')
            for pid in record['owners']:
                proposal = proposals.get(pid)
                objects = proposal['example_pack'][group] if proposal else []
                row = next((r for r in objects if r['id'] == identity), None)
                if (not row or proposal['decisions'][group].get(identity) != 'kept'
                        or snapshot_hash(row, group) != record['sha256']):
                    raise ValueError('材料来源归属与原保留决定不一致。')
