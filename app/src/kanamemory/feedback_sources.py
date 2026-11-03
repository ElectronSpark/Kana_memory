"""Versioned, field-scoped source excerpts; retrieval is not factual approval."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import re
from uuid import uuid4

from . import app_clock, retention_ai
from .material_workflow import digest, dump
from .public_excerpt_fetch import allowed_url, EXTRACTOR, MAX_TEXT
from .retention_ai import _hash
from .tutor_store import _text

PREFIX = 'feedback_sources_v1:'
SCOPE = ('form', 'sense', 'period', 'region', 'register')


def validate_document(d):
    if (not isinstance(d, dict) or set(d) != {'id', 'mode', 'url', 'final_url', 'retrieved_at', 'raw_sha256',
            'text', 'text_sha256', 'extractor'} or d['mode'] not in {'retrieved', 'pasted'}
            or d['id'] != digest({k: v for k, v in d.items() if k != 'id'})
            or not isinstance(d['text'], str) or not d['text'].strip() or len(d['text'].encode('utf-8')) > MAX_TEXT
            or re.search(r'sk-(?:proj-)?[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9_.-]{12,}', d['text'])
            or d['text_sha256'] != sha256(d['text'].encode('utf-8')).hexdigest()
            or datetime.fromisoformat(d['retrieved_at']).tzinfo is None):
        raise ValueError('资料正文或版本不一致。')
    if d['mode'] == 'retrieved':
        if (allowed_url(d['url']) != d['url'] or d['final_url'] != d['url'] or not _hash(d['raw_sha256'])
                or d['extractor'] != EXTRACTOR): raise ValueError('网页资料缺少实际获取记录。')
    elif d['url'] or d['final_url'] or d['raw_sha256'] or d['extractor'] != 'user-pasted-text-v1':
        raise ValueError('粘贴资料不能伪装成已获取的网页。')
    return deepcopy(d)


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'parent_id', 'parent_sha256', 'documents', 'excerpts', 'selected', 'attempts'}
            or type(value['version']) is not int or value['version'] != 1
            or not _hash(value['parent_id']) or not _hash(value['parent_sha256'])
            or not isinstance(value['documents'], list) or len(value['documents']) > 6
            or not isinstance(value['excerpts'], list) or len(value['excerpts']) > 12
            or not isinstance(value['attempts'], list) or len(value['attempts']) > 3
            or not isinstance(value['selected'], list) or len(dump(value).encode('utf-8')) > 512 * 1024):
        raise ValueError('资料数量或记录格式超限。')
    documents = {}
    for d in value['documents']:
        validate_document(d)
        if d['id'] in documents: raise ValueError('资料记录重复。')
        documents[d['id']] = d
    seen = set()
    for e in value['excerpts']:
        if (not isinstance(e, dict) or set(e) != {'id', 'document_id', 'binding_id', 'start', 'end', 'quote', 'scope',
                'relationship', 'relationship_reason', 'coordinate_system'}
                or e['id'] != digest({k: v for k, v in e.items() if k != 'id'}) or e['id'] in seen
                or e['document_id'] not in documents or not _hash(e['binding_id'])
                or type(e['start']) is not int or type(e['end']) is not int
                or not 0 <= e['start'] < e['end'] <= len(documents[e['document_id']]['text'])
                or documents[e['document_id']]['text'][e['start']:e['end']] != e['quote']
                or e['coordinate_system'] != 'unicode_codepoint' or not isinstance(e['scope'], dict) or set(e['scope']) != set(SCOPE)
                or e['relationship'] not in {'unknown', 'same_lineage', 'independent_user_asserted'}):
            raise ValueError('摘录位置、所属字段或适用范围无效。')
        _text(e['quote'], 1600)
        for x in e['scope'].values(): _text(x, 300, blank=True)
        _text(e['relationship_reason'], 500, blank=e['relationship'] != 'independent_user_asserted')
        seen.add(e['id'])
    if (any(not isinstance(i, str) or i not in seen for i in value['selected'])
            or len(set(value['selected'])) != len(value['selected'])): raise ValueError('选定摘录无效。')
    attempts = set(); obtained = set()
    for a in value['attempts']:
        if (not isinstance(a, dict) or set(a) != {'id', 'owner', 'url', 'at', 'status', 'document_id'}
                or a['id'] in attempts or not isinstance(a['id'], str) or len(a['id']) != 32
                or not isinstance(a['owner'], str) or len(a['owner']) != 32 or allowed_url(a['url']) != a['url']
                or datetime.fromisoformat(a['at']).tzinfo is None or a['status'] not in {'running', 'completed', 'failed', 'unknown'}
                or (a['status'] == 'completed') != (a['document_id'] is not None)):
            raise ValueError('资料获取记录无效。')
        if a['document_id']:
            d = documents.get(a['document_id'])
            if not d or d['mode'] != 'retrieved' or d['url'] != a['url']: raise ValueError('资料缺少对应获取请求。')
            obtained.add(d['id'])
        attempts.add(a['id'])
    if any(d['mode'] == 'retrieved' and d['id'] not in obtained for d in documents.values()):
        raise ValueError('网页资料缺少获取回执。')
    return deepcopy(value)


def excerpt_evidence(state, excerpt):
    d = next(d for d in state['documents'] if d['id'] == excerpt['document_id'])
    snapshot = {'quote': excerpt['quote'], 'before': d['text'][max(0, excerpt['start'] - 180):excerpt['start']],
        'after': d['text'][excerpt['end']:excerpt['end'] + 180], 'url': d['final_url'], 'mode': d['mode'],
        'text_sha256': d['text_sha256'], 'start': excerpt['start'], 'end': excerpt['end'], 'scope': deepcopy(excerpt['scope'])}
    e = {'kind': 'public_excerpt', 'object_id': excerpt['binding_id'], 'revision': d['text_sha256'],
        'snapshot': snapshot, 'sha256': digest(snapshot), 'source_ids': [d['id'], excerpt['id']],
        'relationship': 'external_context_independence_unproven'}
    e['id'] = digest(e)
    return e


def evidence_for(store, parent_id, binding_id=None):
    raw = store.get_setting(PREFIX + parent_id, None)
    if raw is None: return []
    state = validate(raw)
    return [excerpt_evidence(state, e) for e in state['excerpts']
            if e['id'] in state['selected'] and (binding_id is None or e['binding_id'] == binding_id)]


def validate_public_evidence(e):
    if (not isinstance(e, dict) or set(e) != {'id', 'kind', 'object_id', 'revision', 'snapshot', 'sha256', 'source_ids', 'relationship'}
            or e['kind'] != 'public_excerpt' or not _hash(e['object_id']) or not _hash(e['revision'])
            or e['id'] != digest({k: v for k, v in e.items() if k != 'id'}) or e['sha256'] != digest(e['snapshot'])
            or e['relationship'] != 'external_context_independence_unproven' or not isinstance(e['source_ids'], list)
            or len(e['source_ids']) != 2 or any(not _hash(i) for i in e['source_ids'])):
        raise ValueError('外部摘录依据格式无效。')
    s = e['snapshot']
    if (not isinstance(s, dict) or set(s) != {'quote', 'before', 'after', 'url', 'mode', 'text_sha256', 'start', 'end', 'scope'}
            or s['text_sha256'] != e['revision'] or s['mode'] not in {'retrieved', 'pasted'}
            or type(s['start']) is not int or type(s['end']) is not int or not 0 <= s['start'] < s['end']
            or s['end'] - s['start'] != len(s['quote']) or not isinstance(s['scope'], dict) or set(s['scope']) != set(SCOPE)):
        raise ValueError('外部摘录缺少准确范围。')
    _text(s['quote'], 1600)
    for k in ('before', 'after'): _text(s[k], 180, blank=True)
    for v in s['scope'].values(): _text(v, 300, blank=True)
    if s['mode'] == 'retrieved': allowed_url(s['url'])
    elif s['url']: raise ValueError('粘贴资料不能带获取地址。')


class FeedbackSourceStore:
    def __init__(self, analysis):
        self.analysis, self.store, self.db = analysis, analysis.store, analysis.db

    def get(self, identity):
        value = self.store.get_setting(PREFIX + identity, None)
        if value: return validate(value)
        parent = self.analysis.get(identity)
        if not parent or parent['status'] != 'completed': raise ValueError('请先完成反馈核查。')
        return {'version': 1, 'parent_id': identity, 'parent_sha256': digest(parent), 'documents': [], 'excerpts': [], 'selected': [], 'attempts': []}

    def _write(self, value):
        parent = self.analysis.get(value['parent_id'])
        if not parent or digest(parent) != value['parent_sha256']: raise ValueError('资料所属报告已变化。')
        self.db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
                        (PREFIX + value['parent_id'], dump(validate(value))))

    def recover(self, identity):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE'); state = self.get(identity); changed = False
            for attempt in state['attempts']:
                if attempt['status'] == 'running' and attempt['owner'] != retention_ai.RUN_ID:
                    attempt['status'] = 'unknown'; changed = True
            if changed: self._write(state)
        return state

    def begin_fetch(self, identity, url):
        url = allowed_url(url)
        self.recover(identity)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE'); state = self.get(identity)
            parent = self.analysis.get(identity)
            if not parent or not self.analysis.feedbacks_current(parent['request']):
                raise ValueError('反馈已变化，请先重新核查再获取资料。')
            running = self.db.execute("SELECT 1 FROM app_setting s,json_each(s.value_json,'$.attempts') a "
                "WHERE s.key GLOB 'feedback_sources_v1:*' AND json_extract(a.value,'$.status')='running' "
                "AND json_extract(a.value,'$.owner')=? LIMIT 1", (retention_ai.RUN_ID,)).fetchone()
            if len(state['documents']) >= 6 or len(state['attempts']) >= 3 or running:
                raise ValueError('本报告资料获取次数已用完或已有请求在运行；可保留待核。')
            attempt = {'id': uuid4().hex, 'owner': retention_ai.RUN_ID, 'url': url, 'at': app_clock.utc_now().isoformat(), 'status': 'running', 'document_id': None}
            state['attempts'].append(attempt); self._write(state)
        return attempt['id']

    def finish_fetch(self, identity, attempt_id, document=None):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE'); state = self.get(identity)
            a = next((a for a in state['attempts'] if a['id'] == attempt_id), None)
            if not a: raise ValueError('未知资料获取请求。')
            if a['status'] != 'running': return state
            if a['owner'] != retention_ai.RUN_ID: a['status'] = 'unknown'
            elif document is None: a['status'] = 'failed'
            else:
                d = deepcopy(document); d['id'] = digest(d); validate_document(d)
                if d['url'] != a['url'] or d['mode'] != 'retrieved': raise ValueError('资料获取来源不一致。')
                if not any(r['id'] == d['id'] for r in state['documents']): state['documents'].append(d)
                a.update(status='completed', document_id=d['id'])
            self._write(state)
        return state

    def paste(self, identity, text):
        d = {'mode': 'pasted', 'url': '', 'final_url': '', 'retrieved_at': app_clock.utc_now().isoformat(),
            'raw_sha256': '', 'text': text, 'text_sha256': sha256(text.encode('utf-8')).hexdigest(), 'extractor': 'user-pasted-text-v1'}
        d['id'] = digest(d); validate_document(d)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE'); state = self.get(identity)
            old = next((r for r in state['documents'] if r['mode'] == 'pasted' and r['text'] == text), None)
            if old: return old
            state['documents'].append(d); self._write(state)
        return d

    def add_excerpt(self, identity, document_id, binding_id, quote, scope, *, start=None, relationship='unknown', reason=''):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE'); state = self.get(identity)
            parent = self.analysis.get(identity)
            if binding_id not in {b['id'] for b in parent['request']['bindings']}: raise ValueError('摘录不能关联报告外的字段。')
            d = next((d for d in state['documents'] if d['id'] == document_id), None)
            if not d: raise ValueError('找不到保存的资料。')
            _text(quote, 1600)
            if start is None:
                start = d['text'].find(quote)
                if start < 0 or d['text'].find(quote, start + 1) >= 0: raise ValueError('摘录不存在或重复出现，请指定准确起点。')
            e = {'document_id': document_id, 'binding_id': binding_id, 'start': start, 'end': start + len(quote),
                'quote': quote, 'scope': deepcopy(scope), 'relationship': relationship, 'relationship_reason': reason,
                'coordinate_system': 'unicode_codepoint'}
            e['id'] = digest(e)
            if e['id'] not in {r['id'] for r in state['excerpts']}: state['excerpts'].append(e)
            if e['id'] not in state['selected']: state['selected'].append(e['id'])
            self._write(state)
        return e

    def select(self, identity, selected):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE'); state = self.get(identity)
            state['selected'] = list(selected); self._write(state)


def validate_database(db):
    from .feedback_analysis import PREFIX as ANALYSIS, validate as validate_analysis
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (PREFIX + '*',)):
        state = validate(json.loads(raw))
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (ANALYSIS + state['parent_id'],)).fetchone()
        parent = validate_analysis(json.loads(row[0])) if row else None
        if (key != PREFIX + state['parent_id'] or not parent or digest(parent) != state['parent_sha256']
                or any(e['binding_id'] not in {b['id'] for b in parent['request']['bindings']} for e in state['excerpts'])):
            raise ValueError('核据资料缺少一致的原报告和字段。')
