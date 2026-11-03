"""Atomic, explicitly unqualified personal notes; canonical content is read-only.

The journal, owner heads and outbox share progress.sqlite3. Nothing here grants
teaching/assessment permissions, changes a graph, or edits an existing attempt.
"""
import json
from copy import deepcopy
from datetime import datetime
from uuid import uuid4

from . import app_clock
from .feedback_bindings import current_resource, projection
from .feedback_impact import token
from .material_workflow import digest, dump
from .retention_ai import _hash

FORMAT = 'kanamemory-personal-content-note'
PREFIX = 'personal_content_note_v1:'
HEAD = 'personal_content_note_head_v1:'
OUTBOX = 'personal_content_note_outbox_v1:'
GENERATION = 'personal_content_note_generation_v1'


def owner_key(kind, identity):
    return HEAD + digest([kind, identity])


def event_id(action, plan_id, group_id, nonce):
    return digest([FORMAT, action, plan_id, group_id, nonce])


def entries(plan, group):
    return [{'binding_id': o['binding_id'], 'owner': deepcopy(o['owner']),
             'field': o['field'], 'text': o['after'], 'before_sha256': o['before_sha256']}
            for o in group['operations']]


def validate(value):
    keys = {'format', 'version', 'id', 'action', 'plan_id', 'plan_sha256', 'group_id', 'generation',
            'created_at', 'previous_heads', 'entries', 'feedback_refs', 'assessment_eligible', 'teaching_eligible', 'nonce', 'adoption_id'}
    if (not isinstance(value, dict) or set(value) != keys or value['format'] != FORMAT or value['version'] != 1
            or type(value['version']) is not int or value['action'] not in {'adopt', 'withdraw'}
            or any(not _hash(value[k]) for k in ('id', 'plan_id', 'plan_sha256', 'group_id'))
            or not _hash(value['nonce']) or value['id'] != event_id(value['action'], value['plan_id'], value['group_id'], value['nonce'])
            or (value['adoption_id'] is not None if value['action'] == 'adopt' else not _hash(value['adoption_id']))
            or type(value['generation']) is not int or value['generation'] < 1
            or datetime.fromisoformat(value['created_at']).tzinfo is None
            or value['assessment_eligible'] is not False or value['teaching_eligible'] is not False
            or not isinstance(value['entries'], list) or not 1 <= len(value['entries']) <= 12
            or not isinstance(value['feedback_refs'], list) or not 1 <= len(value['feedback_refs']) <= 10
            or not isinstance(value['previous_heads'], dict) or len(dump(value).encode('utf-8')) > 256_000):
        raise ValueError('个人注释回执格式错误。')
    keys = set()
    from .feedback_bindings import FIELDS
    for e in value['entries']:
        if not isinstance(e, dict) or set(e) != {'binding_id', 'owner', 'field', 'text', 'before_sha256'}:
            raise ValueError('个人注释字段错误。')
        o = e['owner']
        if (not isinstance(o, dict) or set(o) != {'kind', 'id', 'revision', 'source_sha256', 'snapshot_sha256'}
                or o['kind'] not in FIELDS or e['field'] not in FIELDS[o['kind']]
                or any(not isinstance(o[k], str) or not 0 < len(o[k]) <= 512 for k in ('id', 'revision'))
                or any(not _hash(v) for v in (e['binding_id'], e['before_sha256'], o['source_sha256'], o['snapshot_sha256']))
                or not isinstance(e['text'], str) or not 0 < len(e['text']) <= 4000):
            raise ValueError('个人注释来源或文字错误。')
        keys.add(owner_key(o['kind'], o['id']))
    if set(value['previous_heads']) != keys or any(v is not None and not _hash(v) for v in value['previous_heads'].values()):
        raise ValueError('个人注释缺少完整的拥有者组。')
    return deepcopy(value)


def notice(event):
    result = {k: deepcopy(event[k]) for k in ('id', 'action', 'plan_id', 'group_id', 'generation', 'created_at', 'nonce', 'adoption_id')}
    result['feedback_refs'] = [{k: f[k] for k in ('id', 'input_revision', 'context_sha256')} for f in event['feedback_refs']]
    return result


def validate_notice(value):
    if (not isinstance(value, dict) or set(value) != {'id', 'action', 'plan_id', 'group_id', 'generation', 'created_at', 'feedback_refs', 'nonce', 'adoption_id'}
            or value['action'] not in {'adopt', 'withdraw'} or type(value['generation']) is not int or value['generation'] < 1
            or any(not _hash(value[k]) for k in ('id', 'plan_id', 'group_id'))
            or not _hash(value['nonce']) or value['id'] != event_id(value['action'], value['plan_id'], value['group_id'], value['nonce'])
            or (value['adoption_id'] is not None if value['action'] == 'adopt' else not _hash(value['adoption_id']))
            or datetime.fromisoformat(value['created_at']).tzinfo is None
            or not isinstance(value['feedback_refs'], list) or not 1 <= len(value['feedback_refs']) <= 10):
        raise ValueError('个人注释通知无效。')
    from .feedback_store import _id
    for f in value['feedback_refs']:
        if (not isinstance(f, dict) or set(f) != {'id', 'input_revision', 'context_sha256'}
                or not _hash(f['input_revision']) or not _hash(f['context_sha256'])): raise ValueError('通知反馈归属无效。')
        _id(f['id'])
    return deepcopy(value)


class PersonalContentNoteStore:
    def __init__(self, store, analysis=None):
        self.store, self.db, self.analysis = store, store._db, analysis

    def get(self, identity):
        value = self.store.get_setting(PREFIX + identity, None)
        return validate(value) if value is not None else None

    def generation(self):
        value = self.store.get_setting(GENERATION, 0)
        if type(value) is not int or value < 0: raise ValueError('个人注释代次无效。')
        return value

    def latest(self, action, plan_id, group_id):
        row = self.db.execute("SELECT json_extract(value_json,'$.id') FROM app_setting WHERE key GLOB ? "
            "AND json_extract(value_json,'$.action')=? AND json_extract(value_json,'$.plan_id')=? "
            "AND json_extract(value_json,'$.group_id')=? ORDER BY json_extract(value_json,'$.generation') DESC LIMIT 1",
            (PREFIX + '*', action, plan_id, group_id)).fetchone()
        return self.get(row[0]) if row else None

    def active(self, event):
        return event['action'] == 'adopt' and all(self.store.get_setting(k, None) == event['id'] for k in event['previous_heads'])

    def for_resource(self, kind, resource):
        identity = self.store.get_setting(owner_key(kind, resource['id']), None)
        event = self.get(identity) if identity else None
        if not event or not self.active(event): return None
        selected = [e for e in event['entries'] if (e['owner']['kind'], e['owner']['id']) == (kind, resource['id'])]
        if not selected or any(e['owner']['source_sha256'] != digest(projection(kind, resource)) for e in selected):
            return None  # stale annotations must not silently attach to new content
        return {'event': event, 'entries': selected}

    def prepare(self, plan_id, group_id, *, action='adopt'):
        from .content_correction_plan import ContentCorrectionPlanStore
        if self.analysis is None or action not in {'adopt', 'withdraw'}: raise ValueError('无法准备个人注释。')
        before = token(self.db)
        old = self.latest(action, plan_id, group_id)
        if old: return {'existing': old}
        plans = ContentCorrectionPlanStore(self.analysis); plan = plans.get(plan_id)
        group = next((g for g in plan['groups'] if g['id'] == group_id), None) if plan else None
        if not group or group['status'] != 'personal_note_plan': raise ValueError('请先明确选择仅保留为个人注释。')
        rows = entries(plan, group); keys = {owner_key(e['owner']['kind'], e['owner']['id']) for e in rows}
        previous = {k: self.store.get_setting(k, None) for k in sorted(keys)}
        if action == 'adopt':
            self._current(plan)
            # A former atomic group may only be replaced as a whole.
            for identity in set(previous.values()) - {None}:
                prior = self.get(identity)
                if not prior or not self.active(prior) or not set(prior['previous_heads']) <= keys:
                    raise ValueError('原个人注释还有其他联动内容，请先整组撤下。')
        else:
            adopted = self.latest('adopt', plan_id, group_id)
            if not adopted or not self.active(adopted): raise ValueError('此组已有后续修改，不能覆盖后来保存的注释。')
        nonce = digest(uuid4().hex)
        value = validate({'format': FORMAT, 'version': 1, 'id': event_id(action, plan_id, group_id, nonce),
            'nonce': nonce, 'adoption_id': adopted['id'] if action == 'withdraw' else None,
            'action': action, 'plan_id': plan_id, 'plan_sha256': digest(plan), 'group_id': group_id,
            'generation': self.generation() + 1, 'created_at': app_clock.utc_now().isoformat(),
            'previous_heads': previous, 'entries': rows, 'feedback_refs': plan['manifest']['feedback_refs'],
            'assessment_eligible': False, 'teaching_eligible': False})
        if before != token(self.db): raise ValueError('准备期间记录有变化，请重试。')
        prepared = {'event': value, 'token': before}
        guards = getattr(self.store, '_personal_note_guards', {})
        guards[value['id']] = {'sha256': digest(prepared), 'plan': plan}
        self.store._personal_note_guards = dict(list(guards.items())[-8:])
        return prepared

    def _current(self, plan):
        from .content_correction_plan import ContentCorrectionPlanStore
        return ContentCorrectionPlanStore(self.analysis).assert_current(plan)

    def commit(self, prepared):
        if 'existing' in prepared:
            old = self.get(prepared['existing']['id'])
            if old != prepared['existing']: raise ValueError('个人注释回执已变化。')
            return old
        e = validate(prepared['event']); old = self.get(e['id'])
        if old:
            if old != e: raise ValueError('个人注释不能改写旧回执。')
            return old
        guard = getattr(self.store, '_personal_note_guards', {}).get(e['id'])
        if not guard or guard['sha256'] != digest(prepared): raise ValueError('请重新准备，不能直接提交外部修订。')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if token(self.db) != prepared['token'] or self.generation() + 1 != e['generation']:
                raise ValueError('准备后记录已变化，请重试。')
            if e['action'] == 'adopt': self._current(guard['plan'])
            if any(self.store.get_setting(k, None) != v for k, v in e['previous_heads'].items()):
                raise ValueError('个人注释已有后续修改。')
            self._write(PREFIX + e['id'], e)
            for k in e['previous_heads']: self._write(k, e['id'] if e['action'] == 'adopt' else None)
            self._write(OUTBOX + e['id'], {'event_id': e['id'], 'delivered': False})
            self._write(GENERATION, e['generation'])
        return e

    def _write(self, key, value):
        self.db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json', (key, dump(value)))

    def sync(self, feedback, *, limit=20):
        # Pending writes have priority. Also round-robin previously delivered
        # records: a restored progress backup is not proof of destination data.
        destination = getattr(feedback, '_db', feedback)
        if getattr(self.store, '_personal_note_sync_destination', None) is not destination:
            self.store._personal_note_sync_destination = destination
            self.store._personal_note_sync_cursor = ''
        rows = self.db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.delivered')=0 ORDER BY key LIMIT ?", (OUTBOX + '*', limit)).fetchall()
        cursor = getattr(self.store, '_personal_note_sync_cursor', '')
        verified = self.db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.delivered')=1 AND key>? ORDER BY key LIMIT ?", (OUTBOX + '*', cursor, limit - len(rows))).fetchall()
        if len(rows) < limit:
            self.store._personal_note_sync_cursor = verified[-1][0] if verified else ''
        rows += verified; sent = 0
        for key, raw in rows:
            box = json.loads(raw); event = self.get(box['event_id'])
            if not event: raise ValueError('个人注释回执缺失。')
            sent += bool(feedback.receive_personal_note_receipt(notice(event)))
            if not box['delivered']:
                with self.db:
                    self.db.execute('UPDATE app_setting SET value_json=? WHERE key=? AND value_json=?',
                        (dump({'event_id': event['id'], 'delivered': True}), key, raw))
        return sent


def validate_database(db):
    from types import SimpleNamespace
    from .content_correction_plan import ContentCorrectionPlanStore
    from .feedback_analysis import PREFIX as ANALYSIS, validate as validate_analysis
    def setting(key, default=None):
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    store = SimpleNamespace(_db=db, get_setting=setting)
    service = PersonalContentNoteStore(store)
    analysis = SimpleNamespace(store=store, db=db, get=lambda key: validate_analysis(setting(ANALYSIS + key)) if setting(ANALYSIS + key) else None)
    plans = ContentCorrectionPlanStore(analysis); heads = {}; generation = 0; expected_outbox = set(); adopted = {}; actions = set()
    rows = db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB ? ORDER BY json_extract(value_json,'$.generation')", (PREFIX + '*',)).fetchall()
    for key, raw in rows:
        e = validate(json.loads(raw)); plan = plans.get(e['plan_id'])
        group = next((g for g in plan['groups'] if g['id'] == e['group_id']), None) if plan else None
        if (key != PREFIX + e['id'] or e['generation'] != generation + 1 or not group
                or group['status'] != 'personal_note_plan' or digest(plan) != e['plan_sha256']
                or entries(plan, group) != e['entries'] or plan['manifest']['feedback_refs'] != e['feedback_refs']
                or any(heads.get(k) != v for k, v in e['previous_heads'].items())):
            raise ValueError('个人注释缺少一致的原计划或完整历史。')
        keys = set(e['previous_heads'])
        action_key = (e['action'], e['plan_id'], e['group_id'])
        if action_key in actions: raise ValueError('同一计划组不能重复采纳或撤下。')
        actions.add(action_key)
        if e['action'] == 'withdraw':
            if (set(e['previous_heads'].values()) != {e['adoption_id']}
                    or adopted.get(e['adoption_id']) != (e['plan_id'], e['group_id'])):
                raise ValueError('撤下的个人注释不是当前版本。')
        else:
            adopted[e['id']] = (e['plan_id'], e['group_id'])
            for previous in set(e['previous_heads'].values()) - {None}:
                if not set(service.get(previous)['previous_heads']) <= keys:
                    raise ValueError('不能部分替换联动个人注释。')
        heads.update({k: e['id'] if e['action'] == 'adopt' else None for k in keys})
        generation = e['generation']; expected_outbox.add(OUTBOX + e['id'])
    actual_heads = {k: json.loads(v) for k, v in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (HEAD + '*',))}
    if actual_heads != heads or service.generation() != generation: raise ValueError('个人注释活动版本不一致。')
    actual_outbox = set()
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (OUTBOX + '*',)):
        box = json.loads(raw); actual_outbox.add(key)
        if (set(box) != {'event_id', 'delivered'} or key != OUTBOX + box['event_id'] or type(box['delivered']) is not bool):
            raise ValueError('个人注释同步回执错误。')
    if actual_outbox != expected_outbox: raise ValueError('个人注释缺少成套同步回执。')
    for key, in db.execute("SELECT key FROM app_setting WHERE key GLOB 'personal_content_note*'"):
        if not (key == GENERATION or key.startswith((PREFIX, HEAD, OUTBOX))): raise ValueError('未知个人注释版本。')
