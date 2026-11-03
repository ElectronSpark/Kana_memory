"""Bounded feedback groups and durable queues; no content activation authority."""
from copy import deepcopy
from datetime import datetime
import json

from . import app_clock
from .material_workflow import digest, dump
from .retention_ai import _hash

PREFIX = 'feedback_batch_v1:'


def members(request):
    return request.get('group_members', [request])


def address(binding):
    return (binding['resource_kind'], binding['resource_id'], binding['field'], binding['source_sha256'])


def merge(requests):
    """Retain original per-page bindings; use one representative per exact field."""
    from .feedback_analysis import validate_request
    if not isinstance(requests, list) or not 1 <= len(requests) <= 10:
        raise ValueError('每组最多10条反馈。')
    if any(not isinstance(r, dict) or 'group_members' in r for r in requests):
        raise ValueError('不能嵌套反馈组。')
    requests = sorted((validate_request(r) for r in requests), key=lambda r: r['feedback']['id'])
    ids = [r['feedback']['id'] for r in requests]
    if len(set(ids)) != len(ids): raise ValueError('反馈组包含重复成员。')
    if len(requests) == 1: return deepcopy(requests[0])
    bindings, sources, versions, contexts, fields = {}, {}, {}, {}, {}
    for request in requests:
        for b, s in zip(request['bindings'], request['sources']):
            # Prose v1 and graph v3 intentionally hash different projections.
            # Compare hashes only within one projection, and compare selected
            # field values across projections; adoption still checks full owners.
            owner = (b['resource_kind'], b['resource_id'], b['version'])
            if owner in versions and versions[owner] != b['source_sha256']:
                raise ValueError('同一内容涉及不同旧版本，请先逐条核对，不能覆盖合并。')
            versions[owner] = b['source_sha256']
            field = (b['resource_kind'], b['resource_id'], b['field'])
            if field in fields and fields[field] != b['value']:
                raise ValueError('同一内容涉及不同旧版本，请先逐条核对，不能覆盖合并。')
            fields[field] = b['value']
            key = address(b)
            if key not in bindings: bindings[key], sources[key] = deepcopy(b), deepcopy(s)
            if 'contexts' in request:
                context = request['contexts'][b['id']]
                if key in contexts and contexts[key] != context:
                    raise ValueError('同一内容的日文语境已变化，请重新核查。')
                contexts[key] = deepcopy(context)
    keys = sorted(bindings)
    value = {'version': 1, 'feedback': deepcopy(requests[0]['feedback']),
        'bindings': [bindings[k] for k in keys], 'sources': [sources[k] for k in keys],
        'binding_origin': 'captured' if all(r['binding_origin'] == 'captured' for r in requests) else 'legacy_candidates_confirmed',
        'permission': 'feedback_text_and_selected_content_v1', 'group_members': requests}
    if contexts:
        value['contexts'] = {bindings[k]['id']: contexts.get(k,{}) for k in keys}
    value['id'] = digest(value)
    # The caller's ordinary request validator enforces the same field/text caps.
    return value


def overlap_groups(requests):
    """A feedback is indivisible. Shared owners/sentences join transitively."""
    parents = list(range(len(requests))); owners = {}
    def root(i):
        while parents[i] != i: i = parents[i]
        return i
    for i, request in enumerate(requests):
        keys = set()
        for b in request['bindings']:
            keys.add((b['resource_kind'], b['resource_id']))
            if b['example_id']: keys.add(('example', b['example_id']))
        for key in keys:
            if key in owners: parents[root(i)] = root(owners[key])
            owners[key] = i
    groups = {}
    for i, request in enumerate(requests): groups.setdefault(root(i), []).append(request)
    return sorted((merge(g) for g in groups.values()), key=lambda r: r['id'])


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'id', 'group_ids', 'feedback_ids', 'created_at', 'updated_at', 'last_used_seq',
            'paused', 'archive_after', 'archive_tokens'} or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['group_ids'], list) or not 1 <= len(value['group_ids']) <= 10
            or not all(_hash(i) for i in value['group_ids']) or value['group_ids'] != sorted(set(value['group_ids']))
            or value['id'] != digest(['feedback-batch-v1', value['group_ids']])
            or not isinstance(value['feedback_ids'], list) or not 1 <= len(value['feedback_ids']) <= 10
            or value['feedback_ids'] != sorted(set(value['feedback_ids']))
            or type(value['paused']) is not bool or type(value['archive_after']) is not bool
            or type(value['last_used_seq']) is not int or value['last_used_seq'] < 1
            or not isinstance(value['archive_tokens'], dict) or set(value['archive_tokens']) - set(value['feedback_ids'])):
        raise ValueError('批量反馈任务无效。')
    from .feedback_store import _id
    for identity in value['feedback_ids']: _id(identity)
    if any(not isinstance(value[k], str) or datetime.fromisoformat(value[k]).tzinfo is None for k in ('created_at', 'updated_at')):
        raise ValueError('批次缺少时间。')
    for stamp in value['archive_tokens'].values():
        if not isinstance(stamp, str) or datetime.fromisoformat(stamp).tzinfo is None: raise ValueError('归档版本无效。')
    if value['archive_after'] and set(value['archive_tokens']) != set(value['feedback_ids']):
        raise ValueError('归档范围不完整。')
    return deepcopy(value)


class FeedbackBatchStore:
    def __init__(self, analysis):
        self.analysis, self.store, self.db = analysis, analysis.store, analysis.db

    def get(self, identity):
        value = self.store.get_setting(PREFIX + identity, None)
        return validate(value) if value is not None else None

    def latest(self):
        row = self.db.execute("SELECT value_json FROM app_setting WHERE key GLOB ? ORDER BY json_extract(value_json,'$.last_used_seq') DESC LIMIT 1", (PREFIX + '*',)).fetchone()
        return validate(json.loads(row[0])) if row else None

    def _write(self, value):
        # Logical order survives wall-clock rollback and backup row reordering.
        value['last_used_seq'] = self.db.execute("SELECT COALESCE(MAX(json_extract(value_json,'$.last_used_seq')),0)+1 FROM app_setting WHERE key GLOB ?", (PREFIX + '*',)).fetchone()[0]
        self.db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
                        (PREFIX + value['id'], dump(validate(value))))

    def prepare(self, selections):
        from .feedback_analysis import build_request, validate_request
        if (not isinstance(selections, dict) or not 1 <= len(selections) <= 10):
            raise ValueError('请选择1至10条反馈。')
        requests = []
        for fid, selected in sorted(selections.items()):
            record = self.analysis.feedback.get(fid)
            if not record: raise ValueError('原反馈已不存在。')
            requests.append(build_request(record, selected, self.analysis.resources))
        groups = [validate_request(r) for r in overlap_groups(requests)]
        ids = sorted(r['id'] for r in groups)
        value = {'version': 1, 'id': digest(['feedback-batch-v1', ids]), 'group_ids': ids,
            'feedback_ids': sorted(selections), 'created_at': app_clock.utc_now().isoformat(),
            'paused': True, 'archive_after': False, 'archive_tokens': {}}
        value['updated_at'] = value['created_at']
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old = self.get(value['id'])
            if old: return old
            for request in groups: self.analysis._create_request(request)
            self._write(value)
        return value

    def configure(self, identity, *, paused=None, archive_after=None):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE'); value = self.get(identity)
            if not value: raise ValueError('找不到批量反馈任务。')
            if paused is not None: value['paused'] = paused
            if archive_after is not None:
                if archive_after and not value['archive_after']:
                    records = [self.analysis.feedback.get(i) for i in value['feedback_ids']]
                    if any(r is None for r in records): raise ValueError('反馈已删除，不能设置自动归档。')
                    value['archive_tokens'] = {r['id']: r['updated_at'] for r in records}
                elif not archive_after: value['archive_tokens'] = {}
                value['archive_after'] = archive_after
            value['updated_at'] = app_clock.utc_now().isoformat()
            self._write(value)
        return value

    def jobs(self, identity):
        self.analysis.recover()
        value = self.get(identity)
        if not value: raise ValueError('找不到批量反馈任务。')
        jobs = [self.analysis.get(i) for i in value['group_ids']]
        if any(j is None for j in jobs): raise ValueError('批次缺少已保存的核查组。')
        return jobs

    def archive_completed(self, identity):
        value = self.get(identity)
        if not value or not value['archive_after']: return []
        archived = []
        for job in self.jobs(identity):
            if job['status'] != 'completed': continue
            for member in members(job['request']):
                f = member['feedback']
                if self.analysis.feedback.archive_analyzed(f, value['archive_tokens'].get(f['id'])):
                    archived.append(f['id'])
        return archived


def validate_database(db):
    from .feedback_analysis import PREFIX as ANALYSIS, validate as validate_analysis
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (PREFIX + '*',)):
        value = validate(json.loads(raw)); found, requests = [], []
        if key != PREFIX + value['id']: raise ValueError('反馈批次身份不一致。')
        for identity in value['group_ids']:
            row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (ANALYSIS + identity,)).fetchone()
            job = validate_analysis(json.loads(row[0])) if row else None
            if not job: raise ValueError('反馈批次缺少原始核查组。')
            requests.extend(members(job['request']))
            found.extend(r['feedback']['id'] for r in members(job['request']))
        if sorted(found) != value['feedback_ids']: raise ValueError('批次成员有遗漏或重复。')
        if [r['id'] for r in overlap_groups(requests)] != value['group_ids']:
            raise ValueError('重叠反馈不能拆成独立核查组。')
