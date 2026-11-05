"""Explicit replacement of ungraded, blocked tasks; never a review or a reset.

Old queue rows and instruction checkpoints remain immutable evidence. A receipt
terminates only their queue ownership, while the replacement uses current local
teaching and its own answer identity. No scheduler state is written here.
"""
from copy import deepcopy
from datetime import datetime
import json
from uuid import uuid4

from .material_workflow import digest, dump
from .feedback_impact import token

PREFIX = 'learning_replacement_v1:'
GUIDE_FIELDS = {'guide_question', 'guide_draft', 'guide_answer', 'guide_correct',
                'guide_sequence', 'guide_index', 'guide_completed_ids', 'guide_source_item'}


def question_key(question_id):
    return PREFIX + 'question:' + digest(question_id)


def question_receipt(store, question_id):
    return store.get_setting(question_key(question_id), None)


def assert_answerable(store, question_id):
    from .practice_archive import archived_question
    if archived_question(store, question_id):
        raise ValueError('这一轮已结束，答案记录已归档。请开始新一轮练习。')
    if question_receipt(store, question_id):
        raise ValueError('这道旧题已保留在替换记录中，请继续新的题目。')


def original_opportunity(store, question_id):
    seen = set()
    while question_id not in seen:
        seen.add(question_id)
        row = store._db.execute('SELECT snapshot_json FROM learning_question WHERE question_id=?', (question_id,)).fetchone()
        prior = json.loads(row[0]).get('replacement_of') if row else None
        if not prior: return question_id
        receipt = question_receipt(store, prior)
        if not receipt or receipt['question']['id'] != question_id:
            raise ValueError('替代题缺少原机会回执。')
        question_id = prior
    raise ValueError('替代机会出现循环。')


def observation_only(store, question_id):
    row = store._db.execute('SELECT snapshot_json FROM learning_question WHERE question_id=?', (question_id,)).fetchone()
    prior = json.loads(row[0]).get('replacement_of') if row else None
    if not prior: return False
    receipt = question_receipt(store, prior)
    if not receipt or receipt['question']['id'] != question_id: raise ValueError('替代题缺少保存记录。')
    retired = receipt.get('diagnostic_retirement')
    if retired:
        from .content_queue import archived
        old = archived(store, retired)
        if old['credit_revoked'] or old['presented'] and (old['purpose'] == 'check_only' or old['exposed']): return True
    return observation_only(store, prior)


def guide_write_guard(checkpoint, state, transition):
    """A callback from an old page cannot restore its old guide or finish a new one."""
    if not checkpoint.get('guide_replacement_id'):
        return
    if state and any(k in state for k in ('guide_source_item', 'guide_replacement_id')):
        raise ValueError('引导替换记录由学习记录管理。')
    if state and 'guide_question' in state and state['guide_question'] != checkpoint.get('guide_question'):
        raise ValueError('引导已更新，请重新打开当前练习。')
    if transition in {'guide_correct', 'bridge'}:
        from .learning_questions import grade_learning_answer
        q, answer = checkpoint.get('guide_question'), checkpoint.get('guide_answer')
        if not q or answer is None or grade_learning_answer(q, answer).get('status') != 'correct':
            raise ValueError('请先完成当前引导题。')
        if checkpoint.get('guide_sequence') and checkpoint.get('guide_completed_ids') != [q['id'] for q in checkpoint['guide_sequence']]:
            raise ValueError('请先完成当前引导题组。')


def _write(db, key, value):
    if not db.in_transaction:
        raise RuntimeError('Replacement requires a transaction')
    db.execute('INSERT INTO app_setting VALUES (?,?)', (key, dump(value)))


def _source(learning, cid, scope):
    from .personal_content_versions import teaching_source, resolve
    row = learning.instruction_row(cid, scope)
    return resolve(learning.store, teaching_source(json.loads(row['content_json'])))


def _receipt(value):
    value = deepcopy(value)
    value['id'] = digest(value)
    return value


class LearningReplacementStore:
    def __init__(self, learning):
        self.learning, self.store, self.db = learning, learning.store, learning._db

    def _options(self, item, ability=None):
        from .learning_questions import DEFAULT_KINDS, KIND_ABILITIES, question_kinds
        from .answer_input import system_japanese_enabled
        prefs = self.store.get_setting('learning_preferences_v1', {})
        enabled = prefs.get('enabled', DEFAULT_KINDS)
        admitted = [ability] if ability else item['enrolled_abilities']
        kinds = [k for k in question_kinds(item) if k in enabled and KIND_ABILITIES[k] in admitted]
        known = {item['id'], *(r[0] for r in self.db.execute(
            'SELECT DISTINCT content_id FROM learning_unit WHERE instruction_ready=1'))}
        return kinds, {'known_background_ids': known, 'system_japanese_input': system_japanese_enabled(self.store)}

    def _eligible(self, source, q, *, guided=False):
        from .assessment_policy import assessment_decision
        from .answer_input import available, system_japanese_enabled
        decision = assessment_decision(source, q, store=self.store,
            purpose='guided_practice' if guided else 'scheduled_review')
        return decision['assessment_eligible'] and available(q, system_japanese=system_japanese_enabled(self.store))

    def _context(self, cid, scope, *, replacing_unit=None):
        from .assessment_policy import teaching_allowed
        from .retention_session import reservation
        item = self.learning.instruction_item(cid, scope)
        if not item or not teaching_allowed(item, store=self.store):
            raise ValueError('当前内容仍待核，暂时没有合格的替代题。')
        if self.learning.prerequisite_status(cid, scope)['blocked']:
            raise ValueError('请先完成前置学习。')
        if any(reservation(self.store, u['unit_id']) for u in item['units']
               if replacing_unit is None or u['unit_id'] == replacing_unit):
            raise ValueError('请先结束正在进行的掌握检查。')
        return item, _source(self.learning, cid, scope)

    def prepare_question(self, question_id, *, now=None, diagnostic_retirement=None):
        from .learning_store import _utc, _latest_due
        from .learning_questions import build_learning_question
        from .learning_practice_policy import select_practice_plan
        from .personal_content_versions import question_blocked
        from .retention_session import authorize_course, handoff
        saved = question_receipt(self.store, question_id)
        if saved:
            return {'saved': saved}
        before = token(self.db)
        row = self.db.execute('SELECT * FROM learning_question WHERE question_id=?', (question_id,)).fetchone()
        if not row or row['status'] != 'pending' or row['result_json'] is not None:
            raise ValueError('只能替换尚未提交的待核题；已有结果继续保留。')
        authorize_course(self.learning, question_id, None)
        if diagnostic_retirement:
            from .content_queue import archived, PREFIX as QUEUE_RECEIPT
            original = archived(self.store, diagnostic_retirement)
            receipt = self.store.get_setting(QUEUE_RECEIPT + diagnostic_retirement)
            episode = self.store.get_setting(receipt['key'])
            current = next((r for r in episode['entries'] if r['id'] == original['id']), None)
            if (not receipt['key'].startswith('retention_check_v2:') or original['course_question_id'] != question_id
                    or current is None or current.get('content_retirement') != diagnostic_retirement
                    or current['result'] is not None):
                raise ValueError('这道题没有有效的检查撤下回执。')
        if handoff(self.store, question_id) and not diagnostic_retirement:
            raise ValueError('这道题属于掌握检查的原机会，请从原入口继续。')
        unit = self.db.execute('SELECT * FROM learning_unit WHERE unit_id=?', (row['unit_id'],)).fetchone()
        from .ability_teaching import scope_for
        scope = scope_for(self.db, unit['content_id'], unit['ability'])
        item, source = self._context(unit['content_id'], scope, replacing_unit=unit['unit_id'])
        if unit['suspended'] or not (diagnostic_retirement or self.learning.content_question_blocked(item, self.learning.get_question(question_id))):
            raise ValueError('这道题无需替换，或对应能力已暂停。')
        if item['stage'] == 'guide' and item['checkpoint'].get('guide_question'):
            if self.learning.content_question_blocked(item, item['checkpoint']['guide_question']):
                raise ValueError('请先更新引导题。')
        kinds, state = self._options(item, unit['ability'])
        state.update(dict(unit))
        plan = select_practice_plan(source, unit['ability'], kinds, state,
                                    self.learning.practice_history(item['id'], unit['ability']))
        if plan is None:
            raise ValueError('当前题型设置下没有合格的替代题。')
        q = build_learning_question(source, plan['kind'], variant=plan['variant'])
        if not self._eligible(source, q):
            raise ValueError('新题尚未通过评分与输入方式检查。')
        q['id'] = q['question_id'] = 'replacement:' + uuid4().hex
        q['replacement_of'] = question_id
        from .assessment_contract import for_question
        q['assessment_contract'] = for_question(q)
        now = _utc(now)
        value = {'version': 1, 'type': 'question', 'content_id': item['id'], 'scope': scope,
                 'created_at': now.isoformat(), 'before': dict(row), 'source': source, 'question': q,
                 'not_before_utc': _latest_due(row['not_before_utc'], self.learning.question_not_before(unit, q)),
                 'carried_help': {k: True for k in ('assisted', 'hint_used', 'answer_revealed')
                                  if json.loads(row['draft_json']).get(k)}}
        if diagnostic_retirement:
            value['diagnostic_retirement'] = diagnostic_retirement
            if original['exposed'] or original['draft'].get('hint_used'):
                value['carried_help']['assisted'] = True
        return self._prepare(value, before)

    def prepare_guide(self, content_id, scope=None, *, now=None):
        from .learning_store import _utc
        from .learning_questions import build_learning_question, KIND_ABILITIES, LISTENING_KINDS
        from .learning_practice_policy import guided_practice_sequence, _candidate_questions
        from .personal_content_versions import question_blocked
        before = token(self.db)
        item, source = self._context(content_id, scope)
        checkpoint = item['checkpoint']; old = checkpoint.get('guide_question')
        if item['stage'] != 'guide' or not old or not self.learning.content_question_blocked(item, old):
            raise ValueError('当前没有需要更新的引导题。')
        kinds, state = self._options(item, old['ability'])
        levels = checkpoint.get('initial_familiarity', {}).get('levels', {})
        unfamiliar = [k for k in kinds if levels.get(KIND_ABILITIES[k]) in {'new', 'some'}]
        kinds = unfamiliar or kinds
        kinds = [k for k in kinds if k not in LISTENING_KINDS]
        sequence = guided_practice_sequence(source, kinds, known_background_ids=state['known_background_ids'],
                                            system_japanese=state['system_japanese_input'])
        if sequence and not all(self._eligible(source, q, guided=True) for q in sequence):
            sequence = []
        q = sequence[0] if sequence else None
        if q is None:
            for ability in item['enrolled_abilities']:
                for candidate in _candidate_questions(source, ability, kinds, state):
                    built = build_learning_question(source, candidate['kind'], variant=candidate['variant'], guided=True)
                    if self._eligible(source, built, guided=True):
                        q = built; break
                if q is not None: break
        if q is None:
            raise ValueError('当前题型设置下没有合格的新引导题。')
        updated = {k: deepcopy(v) for k, v in checkpoint.items() if k not in GUIDE_FIELDS}
        updated.update(guide_question=q, guide_draft={})
        if sequence:
            updated.update(guide_sequence=sequence, guide_index=0, guide_completed_ids=[])
        if q.get('practice'):
            updated['guide_source_item'] = source
        value = {'version': 1, 'type': 'guide', 'content_id': content_id, 'scope': scope,
                 'created_at': _utc(now).isoformat(), 'before': checkpoint, 'after': updated,
                 'source': source, 'introduction_id': item['introduction_id']}
        return self._prepare(value, before)

    def _prepare(self, value, before):
        if before != token(self.db):
            raise ValueError('准备期间记录已变化，请重新打开。')
        prepared = {'event': _receipt(value), 'token': before}
        self._prepared = digest(prepared)
        return prepared

    def commit(self, prepared, *, _after_commit=None):
        from .learning_store import _dump
        if 'saved' in prepared:
            saved = question_receipt(self.store, prepared['saved']['before']['question_id'])
            if saved != prepared['saved']: raise ValueError('替换记录已变化。')
            return saved
        if getattr(self, '_prepared', None) != digest(prepared):
            raise ValueError('请重新准备替换。')
        event = prepared['event']; key = (question_key(event['before']['question_id']) if event['type'] == 'question'
                                        else PREFIX + 'guide:' + event['id'])
        saved = self.store.get_setting(key, None)
        if saved == event: return saved
        with self.db:
            self.learning._transaction()
            if token(self.db) != tuple(prepared['token']):
                raise ValueError('记录已变化，请重新准备替换。')
            if _source(self.learning, event['content_id'], event['scope']) != event['source']:
                raise ValueError('教学来源已变化，请重新准备替换。')
            if event['type'] == 'question':
                old = event['before']; q = event['question']
                self.db.execute("UPDATE learning_question SET status='completed' WHERE question_id=?", (old['question_id'],))
                added = self.learning._append_questions_in_transaction(q, now=datetime.fromisoformat(event['created_at']))
                if added != [q['id']]: raise ValueError('新题未能入队，原题继续保留。')
                self.db.execute('UPDATE learning_question SET not_before_utc=?,draft_json=? WHERE question_id=?',
                    (event['not_before_utc'], _dump(event['carried_help']), q['id']))
            else:
                from .ability_teaching import update
                after = dict(event['after'], guide_replacement_id=event['id'])
                from .learning_practice_policy import validate_practice_guide
                from .pitch_practice import validate_checkpoint
                validate_practice_guide(event['content_id'], event['source']['revision'], after, item=event['source'])
                validate_checkpoint(event['content_id'], event['source']['revision'], after)
                from .multi_cloze import validate_checkpoint as validate_cloze_checkpoint
                validate_cloze_checkpoint(event['content_id'], event['source']['revision'], after)
                row = self.learning.instruction_row(event['content_id'], event['scope'])
                update(self.db, row, checkpoint_json=_dump(after))
            _write(self.db, key, event)
            if _after_commit is not None: _after_commit(event)
        return deepcopy(event)


def validate_database(db):
    """Exact retirement exceptions, not a blanket waiver of event consistency."""
    from types import SimpleNamespace
    from .personal_content_versions import original_source, teaching_source, PREFIX as VERSIONS
    from .feedback_bindings import field_value
    from .question_dependencies import factory_matches
    from .learning_practice_policy import validate_practice_guide
    from .pitch_practice import validate_checkpoint
    def setting(key, default=None):
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    store = SimpleNamespace(_db=db, get_setting=setting)
    retired, replacements, guides = set(), set(), {}
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (PREFIX + '*',)):
        e = json.loads(raw)
        common = {'id','version','type','content_id','scope','created_at','before','source'}
        extra = {'question','not_before_utc','carried_help'} if e.get('type') == 'question' else {'after','introduction_id'}
        if (set(e) - {'diagnostic_retirement'} != common | extra or e['version'] != 1 or type(e['version']) is not int
                or 'diagnostic_retirement' in e and e.get('type') != 'question'
                or e['id'] != digest({k:v for k,v in e.items() if k != 'id'})
                or e['type'] not in {'question','guide'} or datetime.fromisoformat(e['created_at']).tzinfo is None):
            raise ValueError('学习替换回执无效。')
        content = db.execute('SELECT content_json FROM learning_content WHERE content_id=?', (e['content_id'],)).fetchone()
        if not content: raise ValueError('替换内容不存在。')
        base = json.loads(content[0]); source = e['source']
        if original_source(store, source) not in (base, teaching_source(base)):
            raise ValueError('替换来源不属于已核对的原学习项。')
        if source.get('personal_revision'):
            adoption = setting(VERSIONS + source['personal_revision'])
            resource = next(r for r in adoption['resources'] if r['id'] == e['content_id'] and r.get('kind', 'lesson') == 'lesson')
            if any(field_value(source, field) != value for field, value in resource['fields'].items()):
                raise ValueError('替换来源与已采纳字段不同。')
        if e['type'] == 'question':
            old = e['before']; q = e['question']; old_id = old['question_id']; new_id = q['id']
            row = db.execute('SELECT * FROM learning_question WHERE question_id=?', (old_id,)).fetchone()
            new = db.execute('SELECT * FROM learning_question WHERE question_id=?', (new_id,)).fetchone()
            unit = db.execute('SELECT * FROM learning_unit WHERE unit_id=?', (old['unit_id'],)).fetchone()
            if (key != question_key(old_id) or old_id == new_id or old_id in retired or new_id in replacements
                    or old['status'] != 'pending' or old['result_json'] is not None or not row or not new or not unit
                    or dict(row) != dict(old, status='completed') or new['unit_id'] != old['unit_id']
                    or unit['content_id'] != e['content_id'] or unit['ability'] != q.get('ability')
                    or q.get('guided') is not False or not factory_matches(q, source)
                    or q.get('replacement_of') != old_id or new['kind'] != q['kind']
                    or new['created_at_utc'] != e['created_at'] or new['not_before_utc'] != e['not_before_utc']
                    or new['exposure_group'] != old['exposure_group']
                    or db.execute('SELECT 1 FROM learning_event WHERE question_id=?', (old_id,)).fetchone()):
                raise ValueError('旧题替换链或保留的草稿不一致。')
            frozen = json.loads(new['snapshot_json'])
            if any(frozen.get(k) != v for k,v in q.items()) or set(frozen) - set(q) - {'assessment','assessment_contract'}:
                raise ValueError('替代题与回执不一致。')
            if old['not_before_utc'] and (not e['not_before_utc'] or datetime.fromisoformat(e['not_before_utc']) < datetime.fromisoformat(old['not_before_utc'])):
                raise ValueError('替换提前了原题的时间下限。')
            help_flags = {k:True for k in ('assisted','hint_used','answer_revealed') if json.loads(old['draft_json']).get(k)}
            if e.get('diagnostic_retirement'):
                from .content_queue import archived
                retired_entry = archived(store, e['diagnostic_retirement'])
                if retired_entry['course_question_id'] != old_id or retired_entry['result'] is not None:
                    raise ValueError('检查替代的原机会不一致。')
                if retired_entry['exposed'] or retired_entry['draft'].get('hint_used'):
                    help_flags['assisted'] = True
            if e['carried_help'] != help_flags or any(json.loads(new['draft_json']).get(k) is not True for k in help_flags):
                raise ValueError('替换丢失了已获得帮助的事实。')
            retired.add(old_id); replacements.add(new_id)
        else:
            if key != PREFIX + 'guide:' + e['id']: raise ValueError('引导替换身份无效。')
            old, after = e['before'], e['after']
            if ({k:v for k,v in old.items() if k not in GUIDE_FIELDS} != {k:v for k,v in after.items() if k not in GUIDE_FIELDS}
                    or not old.get('guide_question') or after.get('guide_draft') != {} or 'guide_answer' in after
                    or 'guide_correct' in after):
                raise ValueError('引导替换修改了原熟悉度或曝光记录。')
            for state in (old, after):
                validate_practice_guide(e['content_id'], source['revision'], state, item=base, validate_result=False)
                validate_checkpoint(e['content_id'], source['revision'], state)
                from .multi_cloze import validate_checkpoint as validate_cloze_checkpoint
                validate_cloze_checkpoint(e['content_id'], source['revision'], state)
            for q in after.get('guide_sequence', [after['guide_question']]):
                if not factory_matches(q, source): raise ValueError('新引导题不属于核对来源。')
            if after['guide_question'].get('practice') and after.get('guide_source_item') != source:
                raise ValueError('新引导冻结的来源不同。')
            guides[e['id']] = e
    # New queue rows are created strictly after their predecessors. Validate
    # topology as well as row identities so a forged import cannot create cycles.
    edges = {e['before']['question_id']: e['question']['id'] for raw, in db.execute(
        'SELECT value_json FROM app_setting WHERE key GLOB ?', (PREFIX+'question:*',)) if (e := json.loads(raw))}
    for start in edges:
        seen = set(); cursor = start
        while cursor in edges:
            if cursor in seen: raise ValueError('学习替换链存在循环。')
            seen.add(cursor); cursor = edges[cursor]
    from .ability_teaching import rows
    heads = set()
    for row in rows(db):
        checkpoint = json.loads(row['checkpoint_json']); head = checkpoint.get('guide_replacement_id')
        seen = set()
        while head:
            e = guides.get(head)
            if (not e or head in seen or e['content_id'] != row['content_id']
                    or e['scope'] != dict(row).get('instruction_scope') or e['introduction_id'] != row['introduction_id']):
                raise ValueError('引导替换缺少同一教学范围的完整链。')
            seen.add(head); heads.add(head)
            allowed = e['after'].get('guide_sequence', [e['after']['guide_question']])
            if checkpoint.get('guide_question') not in allowed:
                raise ValueError('当前引导不是替换后的题目。')
            checkpoint = e['before']; head = checkpoint.get('guide_replacement_id')
    if heads != set(guides): raise ValueError('存在未连接的引导替换回执。')
    return retired
