"""Independent candidate-check receipts; no teaching/enrollment prerequisite."""
from copy import deepcopy
from datetime import datetime
import json
import re

from .material_workflow import digest, dump, MaterialWorkflowStore
from .material_verification import proposal_content

PREFIX = 'material_pretest_v1:'


def validate(value):
    fields = {'version', 'id', 'proposal_id', 'proposal_sha256', 'diagnostic_id', 'target_ids', 'bindings', 'created_at'}
    if (not isinstance(value, dict) or set(value) != fields or type(value['version']) is not int or value['version'] != 1
            or any(not isinstance(value[k], str) or not re.fullmatch('[0-9a-f]{64}', value[k])
                   for k in ('id', 'proposal_sha256', 'diagnostic_id'))
            or not isinstance(value['proposal_id'], str) or len(value['proposal_id']) > 256
            or not isinstance(value['target_ids'], list) or not 1 <= len(value['target_ids']) <= 12
            or any(not isinstance(cid, str) or not 1 <= len(cid) <= 1024 for cid in value['target_ids'])
            or len(set(value['target_ids'])) != len(value['target_ids'])
            or not isinstance(value['bindings'], list) or len(value['bindings']) != len(value['target_ids'])
            or not isinstance(value['created_at'], str) or datetime.fromisoformat(value['created_at']).tzinfo is None):
        raise ValueError('材料先测回执无效。')
    for cid, row in zip(value['target_ids'], value['bindings']):
        if (not isinstance(row, dict) or set(row) != {'target_id', 'candidate_object_id'} or row['target_id'] != cid
                or row['candidate_object_id'] is not None and not isinstance(row['candidate_object_id'], str)):
            raise ValueError('材料候选映射无效。')
    return deepcopy(value)


def get(store, identity):
    rows = receipts(store, identity)
    from .retention_session import PREFIX as EPISODES, LIVE
    for row in rows:
        episode = store.get_setting(EPISODES + row['diagnostic_id'], None)
        if episode and episode['status'] in LIVE:
            return row
    return rows[-1] if rows else None


def receipts(store, identity):
    return [validate(json.loads(raw)) for (raw,) in store._db.execute(
        'SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,\'$.id\')=? ORDER BY rowid',
        (PREFIX + identity + '*', identity))]


def receipt_key(value, *, first=False):
    return PREFIX + value['id'] + ('' if first else ':' + digest([value['diagnostic_id'], value['target_ids']]))


def matches_episode(store, episode):
    rows = [row for row in receipts(store, episode['material_id']) if row['diagnostic_id'] == episode['id']]
    order = {cid: index for index, cid in enumerate(episode['scope_ids'])}
    if any(cid not in order for row in rows for cid in row['target_ids']): return False
    scopes = [cid for row in sorted(rows, key=lambda r: order[r['target_ids'][0]]) for cid in row['target_ids']]
    return scopes == episode['scope_ids'] and len(scopes) == len(set(scopes))


def added_targets(study, identity):
    from .material_eligibility import pretest_targets
    from .material_qualification import candidate_basis
    old = receipts(study.store, identity)
    covered = {cid for row in old for cid in row['target_ids']}
    covered.update(binding['candidate_object_id'] for row in old for binding in row['bindings']
                   if binding['candidate_object_id'])
    state = study.get(identity)
    if state and state.get('diagnostic_id'):
        from .retention_session import PREFIX as EPISODES
        episode = study.store.get_setting(EPISODES + state['diagnostic_id'], None)
        if episode: covered.update(episode['scope_ids'])
    # A candidate becoming a verified canonical identity does not create a
    # second independent opportunity for the same sense.
    for basis in candidate_basis(study.store, identity, repo=study.repo, examples=study.examples):
        aliases = {basis['item']['id'], basis['source']['object_id'], basis['item'].get('candidate_object_id')}
        if aliases & covered: covered.update(aliases)
    return [item for item in pretest_targets(study.store, identity, repo=study.repo, examples=study.examples)
            if item['id'] not in covered and item.get('candidate_object_id', item['id']) not in covered]


def start(study, identity, enabled, *, append=False):
    from .retention_session import DiagnosticStore, LIVE
    from .material_eligibility import pretest_targets
    diagnostics = DiagnosticStore(study.store, study.learning, repo=study.repo, examples=study.examples)
    old = get(study.store, identity) or study.get(identity)
    if old and old.get('diagnostic_id') and not append:
        return diagnostics.get(old['diagnostic_id'])
    proposal, _ = study._material(identity)
    targets = added_targets(study, identity)
    if not targets:
        if old and old.get('diagnostic_id'):
            return diagnostics.get(old['diagnostic_id'])
        raise ValueError('暂没有依据充分的可测内容，可保留材料继续核对。')
    def receipt(db, episode):
        ids = [item['id'] for item in targets]
        if any(row['diagnostic_id'] == episode['id'] and row['target_ids'] == ids
               for row in receipts(study.store, identity)):
            return
        current = {item['id']: item for item in pretest_targets(study.store, identity, repo=study.repo, examples=study.examples)}
        if any(digest(current.get(item['id'])) != digest(item) for item in targets):
            raise ValueError('新核实范围已改变，请重新打开材料。')
        value = {'version': 1, 'id': identity, 'proposal_id': proposal['id'],
                 'proposal_sha256': digest(proposal_content(proposal)), 'diagnostic_id': episode['id'],
                 'target_ids': ids, 'created_at': episode['created_at_utc'],
                 'bindings': [{'target_id': item['id'], 'candidate_object_id': item.get('candidate_object_id')}
                              for item in targets]}
        db.execute('INSERT INTO app_setting VALUES (?,?)',
                   (receipt_key(value, first=not receipts(study.store, identity)), dump(validate(value))))
    request = 'material-pretest:' + identity
    if old: request += ':' + digest([item['id'] for item in targets])
    active = diagnostics.active()
    append_to = (active['id'] if append and active and active['status'] in LIVE and active.get('material_id') == identity
                 and receipts(study.store, identity) else None)
    return diagnostics.start([item['id'] for item in targets], enabled, request,
        purpose='check_only', intent='material', budget='quick' if len(targets) <= 6 else 'deep',
        exposed_units=[(item['id'], ability) for item in targets for ability in item.get('abilities', ['understanding'])],
        _after_start=receipt, material_id=identity, _append_to=append_to)


def validate_database(db):
    from .material_workflow import PREFIX as WORKFLOW_PREFIX, validate_job
    from .ai_materials import SETTING, empty_state, validate_ai_material_state
    from .retention_session import PREFIX as DIAGNOSTIC_PREFIX, validate_episode
    raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (SETTING,)).fetchone()
    proposals = {p['id']: p for p in validate_ai_material_state(json.loads(raw[0]) if raw else empty_state())['proposals']}
    from types import SimpleNamespace
    view = SimpleNamespace(_db=db)
    owners = set()
    for key, raw in db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB 'material_pretest_*'"):
        value = validate(json.loads(raw))
        job_raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (WORKFLOW_PREFIX + value['id'],)).fetchone()
        job = validate_job(json.loads(job_raw[0])) if job_raw else None
        proposal = proposals.get(value['proposal_id'])
        if (key not in {receipt_key(value, first=True), receipt_key(value)} or not job or job['proposal_id'] != value['proposal_id'] or not proposal
                or digest(proposal_content(proposal)) != value['proposal_sha256']):
            raise ValueError('材料先测缺少原始候选版本。')
        if any((value['id'], cid) in owners for cid in value['target_ids']):
            raise ValueError('材料先测重复占用同一内容。')
        owners.update((value['id'], cid) for cid in value['target_ids'])
        mapping = {job['candidate_ids'][index]: item['id'] for index, item in enumerate(proposal['items'])}
        candidates = {item['id']: item for item in proposal['items']}
        for row in value['bindings']:
            if row['target_id'].startswith('material-candidate:'):
                if mapping.get(row['target_id']) != row['candidate_object_id']:
                    raise ValueError('材料先测候选身份不匹配。')
            elif row['candidate_object_id'] is not None:
                raise ValueError('规范学习项不能冒充新候选。')
        raw_episode = db.execute('SELECT value_json FROM app_setting WHERE key=?', (DIAGNOSTIC_PREFIX + value['diagnostic_id'],)).fetchone()
        episode = validate_episode(json.loads(raw_episode[0])) if raw_episode else None
        if not episode or episode.get('material_id') != value['id'] or not matches_episode(view, episode):
            raise ValueError('材料先测回执没有匹配的检查记录。')
        for entry in episode['entries']:
            q = entry['question']
            if q['target_id'] not in value['target_ids']: continue
            if q['target_id'].startswith('material-candidate:'):
                binding = q.get('material_qualification', {})
                if (entry['course_question_id'] is not None or entry['settlement'] is not None
                        or binding.get('workflow_id') != value['id'] or binding.get('candidate_id') != q['target_id']
                        or binding.get('candidate_object_id') != mapping.get(q['target_id'])):
                    raise ValueError('候选检查不得继承正式学习记录。')
                candidate = candidates[binding['candidate_object_id']]
                if any(digest(candidate.get(field, '')) != sha for field, sha in binding['facets'].items()):
                    raise ValueError('候选检查字段与原始材料不一致。')
                from .material_qualification import CORE
                revision = 'material-facets-v1-' + digest([q['target_id'], {field: binding['facets'][field] for field in CORE}])[:24]
                if q['revision'] != revision:
                    raise ValueError('候选检查题目版本不匹配。')
