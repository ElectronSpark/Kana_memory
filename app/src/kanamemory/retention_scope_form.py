"""Persist editable diagnostic scope separately from frozen model requests."""
from copy import deepcopy
import json
import re
from uuid import uuid4

from .material_workflow import dump
from .retention_session import BUDGETS

PREFIX = 'retention_scope_form_v1:'
GROUPS = {'单词', '语法', '读音规则'}
EDITABLE = {'selected', 'message', 'purpose', 'budget', 'expanded', 'offsets', 'exposed', 'ai_plan', 'scope_plan'}
SEMANTIC = ('restriction', 'intent', 'selected', 'message', 'purpose', 'budget')


def validate(value):
    from .retention_ai import _text, _hash
    if (not isinstance(value, dict) or set(value) != {'version', 'id', 'revision', 'restriction', 'intent', 'selected',
            'message', 'purpose', 'budget', 'expanded', 'offsets', 'exposed', 'ai_plan', 'scope_plan', 'episode_id'}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['id'], str) or not re.fullmatch('[0-9a-f]{32}', value['id'])
            or type(value['revision']) is not int or value['revision'] < 0
            or value['intent'] not in {'active', 'reentry'} or value['purpose'] not in {'check_only', 'check_and_adjust'}
            or value['budget'] not in BUDGETS or not isinstance(value['message'], str) or len(value['message']) > 1000
            or not isinstance(value['selected'], list) or len(value['selected']) > 12
            or any(not _text(cid) for cid in value['selected']) or len(set(value['selected'])) != len(value['selected'])
            or value['restriction'] is not None and (not isinstance(value['restriction'], list) or len(value['restriction']) > 2000
                or any(not _text(cid) for cid in value['restriction']) or len(set(value['restriction'])) != len(value['restriction'])
                or not set(value['selected']) <= set(value['restriction']))
            or not isinstance(value['expanded'], list) or any(g not in GROUPS for g in value['expanded'])
            or len(set(value['expanded'])) != len(value['expanded']) or not isinstance(value['offsets'], dict)
            or any(k not in GROUPS or type(v) is not int or not 0 <= v <= 100000 for k, v in value['offsets'].items())
            or not isinstance(value['exposed'], list) or len(value['exposed']) > 4096
            or any(not isinstance(r, list) or len(r) != 2 or any(not _text(v) for v in r) for r in value['exposed'])
            or any(value[k] is not None and not _hash(value[k]) for k in ('ai_plan', 'scope_plan', 'episode_id'))
            or len(dump(value).encode()) > 256000):
        raise ValueError('检查范围草稿无效。')
    return deepcopy(value)


def semantic(value):
    return {k: deepcopy(value[k]) for k in SEMANTIC}


def request_id(value):
    return value['id'] + ':' + str(value['revision'])


class ScopeFormStore:
    def __init__(self, store):
        self.store, self.db = store, store._db

    def get(self, identity):
        raw = self.store.get_setting(PREFIX + identity, None)
        return validate(raw) if raw is not None else None

    def recent(self, limit=4):
        return [validate(json.loads(row[0])) for row in self.db.execute(
            "SELECT value_json FROM app_setting WHERE key GLOB ? AND json_type(value_json,'$.episode_id')='null' ORDER BY rowid DESC LIMIT ?",
            (PREFIX + '*', max(1, min(limit, 8))))]

    def _write(self, value):
        self.db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
                        (PREFIX + value['id'], dump(validate(value))))

    def create(self, *, restriction=None, intent='active', selected=(), message=''):
        value = {'version': 1, 'id': uuid4().hex, 'revision': 0, 'restriction': restriction,
                 'intent': intent, 'selected': sorted(selected), 'message': message, 'purpose': 'check_only', 'budget': 'quick',
                 'expanded': [], 'offsets': {}, 'exposed': [], 'ai_plan': None, 'scope_plan': None, 'episode_id': None}
        with self.db:
            self.db.execute('BEGIN IMMEDIATE'); self._write(value)
        return value

    def edit_in_transaction(self, identity, changes, *, expected_revision=None):
        if not self.db.in_transaction or not isinstance(changes, dict) or set(changes) - EDITABLE:
            raise ValueError('Invalid scope edit transaction')
        value = self.get(identity)
        if not value or value['episode_id'] or expected_revision is not None and value['revision'] != expected_revision:
            raise ValueError('范围选择已变化，请重新打开。')
        updated = {**value, **deepcopy(changes)}
        updated['selected'] = sorted(updated['selected'])
        if semantic(updated) != semantic(value):
            updated.update(revision=value['revision'] + 1, ai_plan=None, scope_plan=None)
        self._write(updated)
        return updated

    def edit(self, identity, changes):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            return self.edit_in_transaction(identity, changes)

    def started(self, identity, episode):
        if not self.db.in_transaction:
            raise RuntimeError('Scope start must share the episode transaction')
        value = self.get(identity)
        if value is None or value['episode_id'] not in (None, episode['id']):
            raise ValueError('范围草稿已用于另一轮检查。')
        if set(value['selected']) != set(episode['scope_ids']) or value['purpose'] != episode['purpose'] or value['budget'] != episode['budget']:
            raise ValueError('范围或用途已变化。')
        value['episode_id'] = episode['id']; self._write(value)


def validate_database(db):
    from .retention_session import PREFIX as EPISODES
    for key, raw in db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB 'retention_scope_form_*'"):
        value = validate(json.loads(raw))
        if key != PREFIX + value['id']:
            raise ValueError('检查范围草稿标识无效。')
        for field, prefix in (('ai_plan', 'retention_ai_v1:'), ('scope_plan', 'retention_scope_ai_v1:')):
            if value[field] is None: continue
            row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (prefix + value[field],)).fetchone()
            job = json.loads(row[0]) if row else None
            if not job or job['id'] != value[field]:
                raise ValueError('检查范围草稿缺少对应建议。')
            request = job['request']
            if field == 'scope_plan':
                if request['form_id'] != value['id']:
                    raise ValueError('范围建议属于另一份草稿。')
            elif ('session' in request or set(request['scope_ids']) != set(value['selected'])
                    or request['purpose'] != value['purpose'] or request['budget'] != value['budget']):
                raise ValueError('选题建议与检查范围不一致。')
        if value['episode_id']:
            row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (EPISODES + value['episode_id'],)).fetchone()
            episode = json.loads(row[0]) if row else None
            if (not episode or set(episode['scope_ids']) != set(value['selected']) or episode['budget'] != value['budget']
                    or episode['intent'] != value['intent']):
                raise ValueError('检查范围草稿缺少对应会话。')
