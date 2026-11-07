"""Reviewed personal teaching fields, separate from package and memory identity.

The immutable enrolled revision remains the scheduling identity. A personal
revision identifies a field overlay, never grants a whole question permission.
Old questions and answers are read in place, not rewritten during activation.
"""
from copy import deepcopy
from datetime import datetime
import json
from uuid import uuid4

from . import app_clock
from .feedback_bindings import projection, field_value
from .feedback_impact import token
from .material_workflow import digest, dump
from .retention_ai import _hash

FORMAT = 'kanamemory-personal-content-version'
PREFIX = 'personal_content_version_v1:'
HEAD = 'personal_content_version_head_v1:'
GENERATION = 'personal_content_version_generation_v1'
FIELDS = {'instruction.summary', 'instruction.rule', 'instruction.translation', 'instruction.contrast'}
RESOURCE_FIELDS = {'lesson': FIELDS, 'example': {'translation'}, 'lexical_example': {'chinese_translation'},
                   'reference': {'explanation', 'hint.meaning', 'hint.rule'}}


def resource_fields(kind, version=2):
    from .personal_reading import READING_FIELDS
    if kind == 'question' and version >= 4:
        from .personal_question import RESOURCE_FIELDS as RUBRIC_FIELDS
        return RUBRIC_FIELDS
    from .personal_graph import FIELDS as GRAPH_FIELDS
    return (RESOURCE_FIELDS.get(kind, set()) | (READING_FIELDS.get(kind, set()) if version >= 3 else set())
            | (GRAPH_FIELDS.get(kind, set()) if version >= 5 else set()))


def head_key(identity, kind='lesson'):
    return HEAD + digest([kind, identity])


def validate(value):
    if (not isinstance(value, dict) or set(value) - ({'nonce'} if value.get('version') in {2, 3, 4, 5} else set()) - ({'graph_review'} if value.get('version') == 5 else set()) != {'format', 'version', 'id', 'plan_id', 'plan_sha256',
            'group_id', 'support_id', 'support_sha256', 'generation', 'created_at', 'previous_heads', 'resources'}
            or value['format'] != FORMAT or type(value['version']) is not int or value['version'] not in {1, 2, 3, 4, 5}
            or value['version'] == 5 and not isinstance(value.get('graph_review'), dict)
            or any(not _hash(value[k]) for k in ('id', 'plan_id', 'plan_sha256', 'group_id', 'support_id', 'support_sha256'))
            or value['version'] >= 2 and not _hash(value.get('nonce'))
            or value['id'] != digest([FORMAT, value['plan_id'], value['group_id']] + ([value['nonce']] if value['version'] >= 2 else []))
            or type(value['generation']) is not int or value['generation'] < 1
            or datetime.fromisoformat(value['created_at']).tzinfo is None
            or not isinstance(value['resources'], list) or not 1 <= len(value['resources']) <= 12
            or len(dump(value).encode('utf-8')) > (2 * 1024 * 1024 if value['version'] == 5 else 256_000)):
        raise ValueError('个人内容版本无效。')
    keys = set()
    for r in value['resources']:
        kind = r.get('kind', 'lesson') if isinstance(r, dict) else None
        allowed = resource_fields(kind, value['version'])
        question_fields = {'question_baseline', 'question_bundle', 'question_scope', 'target_id'} if kind == 'question' and value['version'] >= 4 else set()
        graph_fields = {'graph'} if value['version'] == 5 and kind in {'example', 'lexical_example'} else set()
        if (not isinstance(r, dict) or set(r) - {'kind'} - question_fields - graph_fields != {'id', 'base_revision', 'base_sha256', 'base_fields', 'fields'}
                or not question_fields <= set(r)
                or not allowed
                or not isinstance(r['id'], str) or not 0 < len(r['id']) <= 512
                or not isinstance(r['base_revision'], str) or not 0 < len(r['base_revision']) <= 512
                or not _hash(r['base_sha256']) or not isinstance(r['fields'], dict)
                or not r['fields'] or not set(r['fields']) <= allowed
                or not isinstance(r['base_fields'], dict) or set(r['base_fields']) != allowed
                or any(v is not None and not isinstance(v, str) for v in r['base_fields'].values())
                or any(not isinstance(v, str) or not 0 < len(v) <= 4000 for v in r['fields'].values())
                or any(len(v) > (300 if f.startswith('hint.') else 4000 if f == 'link' else 2000) for f, v in r['fields'].items() if kind == 'reference')
                or head_key(r['id'], kind) in keys):
            raise ValueError('个人内容字段或基线无效。')
        from .personal_reading import validate_reading
        for field, text in r['fields'].items(): validate_reading(kind, field, text)
        if value['version'] == 5:
            from .personal_graph import prerequisites, link
            if 'prerequisites' in r['fields']: prerequisites(r['fields']['prerequisites'])
            if 'link' in r['fields']: link(r['fields']['link'])
            if 'graph' in r and not isinstance(r['graph'], dict): raise ValueError('例句关系修订无效。')
        if question_fields:
            from .personal_question import parse, scope
            if (r['target_id'] != r['question_baseline'].get('target_id') or r['question_scope'] != scope(r['question_baseline'])
                    or parse(dump(r['question_bundle']), r['question_baseline']) != r['question_bundle']):
                raise ValueError('个人整题版本的目标或判题合同无效。')
        keys.add(head_key(r['id'], kind))
    if (not isinstance(value['previous_heads'], dict) or set(value['previous_heads']) != keys
            or any(v is not None and not _hash(v) for v in value['previous_heads'].values())):
        raise ValueError('个人内容联动组不完整。')
    return deepcopy(value)


def active_version(store, identity, kind='lesson'):
    head = store.get_setting(head_key(identity, kind), None)
    if head is None: return None
    value = store.get_setting(PREFIX + head, None)
    if value is None: raise ValueError('个人内容缺少活动版本。')
    value = validate(value)
    if value['id'] != head or any(store.get_setting(k, None) != head for k in value['previous_heads']):
        raise ValueError('个人内容联动组不一致。')
    return value


def original_source(store, item, *, kind='lesson', owned=False):
    """Recover only our recorded overlay; enrollment must save the base item.

    owned=True means the caller hands over an independent snapshot, so an item
    without an overlay need not be copied again.
    """
    if not item: return deepcopy(item)
    if item.get('personal_question_versions'):
        from .personal_question import validate_frozen_source
        validate_frozen_source(store, item)
    if not item.get('personal_revision'):
        result = item if owned else deepcopy(item)
        for key in ('personal_content_unavailable', 'personal_withdrawal_id', 'personal_reading', 'personal_question_versions', 'personal_prerequisites'): result.pop(key, None)
        return result
    event = store.get_setting(PREFIX + item['personal_revision'], None)
    if event is None: raise ValueError('个人教学版本缺少原基线。')
    event = validate(event)
    row = next((r for r in event['resources'] if r['id'] == item['id'] and r.get('kind', 'lesson') == kind), None)
    if row is None: raise ValueError('个人教学版本不属于这个学习项。')
    result = deepcopy(item)
    for field, text in row['base_fields'].items():
        parts = field.split('.'); parent = result
        for key in parts[:-1]: parent = parent.setdefault(key, {})
        if text is None: parent.pop(parts[-1], None)
        else: parent[parts[-1]] = text
    from .personal_graph import restore_fields
    restore_fields(result, row)
    for key in ('personal_revision', 'personal_base_revision', 'personal_content_unavailable', 'personal_withdrawal_id', 'personal_reading', 'personal_question_versions'):
        result.pop(key, None)
    return result


def teaching_source(item):
    from .material_review import reviewed_item
    from .kana_rule_evidence import teaching_overlay
    return teaching_overlay(reviewed_item(item)) if item else item


def _base_matches(item, row, kind='lesson'):
    value = projection(kind, item)
    if digest(value) == row['base_sha256']: return True
    if kind in {'lesson', 'reference'}:
        from .personal_graph import addressable
        addressed = projection(kind, addressable(kind, item))
        if digest(addressed) == row['base_sha256']: return True
        if kind == 'lesson' and addressed.get('content_id') == item['id']:
            addressed.pop('content_id')
            if digest(addressed) == row['base_sha256']: return True
    # LearningStore adds an alias after projecting the immutable source. It is
    # runtime identity metadata, not a second lesson or a changed source field.
    if kind == 'lesson' and value.get('content_id') == item['id']:
        value.pop('content_id')
        return digest(value) == row['base_sha256']
    return False


def resolve(store, item, *, kind='lesson', owned=False):
    if kind == 'lexical_example' and item and item.get('personal_related_base'):
        item = deepcopy(item['personal_related_base'])
    result = _resolve(store, item, kind=kind, owned=owned)
    if kind == 'lexical_example' and result:
        from .personal_graph import related_lexical_projection
        result = related_lexical_projection(store, item, result)
    return result


def _resolve(store, item, *, kind='lesson', owned=False):
    """Detached teaching projection; raw package/export evidence stays raw."""
    if not item: return item
    event = active_version(store, item['id'], kind)
    from .personal_content_compensation import held
    withdrawal = held(store, item['id'], kind)
    if kind == 'question':
        from .personal_question import project
        if event is None: return deepcopy(item)
        row = next(r for r in event['resources'] if r['id'] == item['id'] and r.get('kind') == 'question')
        result = project(item, row, event)
        if withdrawal: result['personal_content_unavailable'] = True
        return result
    if event is None:
        result = original_source(store, item, kind=kind, owned=owned)
        if withdrawal:
            result.update(personal_content_unavailable=True, personal_withdrawal_id=withdrawal)
            from .personal_content_compensation import holds
            from .personal_reading import READING_FIELDS
            if holds(store).get((kind, item['id']), set()) & READING_FIELDS.get(kind, set()):
                result['personal_reading'] = {'version_id': withdrawal, 'pending': True, 'scope': kind}
        if kind == 'lesson':
            from .personal_question import attach
            result = attach(store, result, owned=True)
        return result
    row = next(r for r in event['resources'] if r['id'] == item['id'] and r.get('kind', 'lesson') == kind)
    result = original_source(store, item, kind=kind)
    if not _base_matches(result, row, kind):
        result['personal_content_unavailable'] = True
        return result
    for field, text in row['fields'].items():
        parts = field.split('.'); parent = result
        for key in parts[:-1]: parent = parent.setdefault(key, {})
        parent[parts[-1]] = text
    result['personal_revision'] = event['id']
    result['personal_base_revision'] = row['base_revision']
    if withdrawal: result.update(personal_content_unavailable=True, personal_withdrawal_id=withdrawal)
    from .personal_reading import project_reading
    project_reading(result, row, event, held=withdrawal is not None)
    if event['version'] >= 5:
        from .personal_graph import project_fields
        project_fields(result, row, event, pending=withdrawal is not None)
    if kind == 'lesson':
        from .personal_question import attach
        result = attach(store, result)
    return result


def revised_fields(store, identity, kind='lesson'):
    event = active_version(store, identity, kind)
    return set(next(r['fields'] for r in event['resources'] if r['id'] == identity and r.get('kind', 'lesson') == kind)) if event else set()


def changed_resources(store):
    """Small active-head index, never scan examples or learner history."""
    if store is None or not getattr(store, '_db', None): return {}
    heads = {k: json.loads(v) for k, v in store._db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (HEAD + '*',))}
    from .personal_content_compensation import holds
    result = holds(store)
    for identity in set(heads.values()) - {None}:
        event = validate(store.get_setting(PREFIX + identity, None))
        if any(heads.get(k) != identity for k in event['previous_heads']): raise ValueError('个人内容活动组不一致。')
        for row in event['resources']:
            result[(row.get('kind', 'lesson'), row['id'])] = set(row['fields'])
    return result


def compile_resources(plan, group, support, previous, *, version=2, graph_snapshot=None, graph_review=None):
    if (group['status'] != 'content_review_plan' or group['category'] not in ({'C1', 'C2'} if version >= 3 else {'C1'})
            or set(group['requirements']) - {'independent_fact_qualification', 'current_impact_and_generation', 'inspect_unknown_dependencies',
                *({'revalidate_rubric_and_attribution'} if version >= 3 else set()),
                *({'rubric_answer_outside_choices', 'rubric_old_answer_variants'} if version >= 4 else set()),
                *({'reanchor_every_reference', 'relink_lexical_examples', 'graph_structural_issues'} if version >= 5 else set())}):
        raise ValueError('这组修改仍需处理判题、关系或义项，不能直接采纳为说明。')
    claims = {c['binding_id']: c for c in support['claims']}
    if set(claims) != set(group['binding_ids']) or any(c['status'] != 'supported' for c in claims.values()):
        raise ValueError('请先完成整组字段核据。')
    resources = {}
    for op in group['operations']:
        owner = op['owner']; claim = claims[op['binding_id']]
        kind = owner['kind']; allowed = resource_fields(kind, version)
        if kind == 'question' and version >= 4:
            identity = owner['id']; key = (kind, identity)
            if key in resources: continue
            snapshot = next(r['snapshot'] for r in plan['manifest']['resources'] if r['kind'] == kind and r['id'] == identity)
            old = previous.get(head_key(identity, kind))
            prior = next((r for r in old['resources'] if r.get('kind') == kind and r['id'] == identity), None) if old else None
            operations = [o for o in group['operations'] if o['owner']['kind'] == kind and o['owner']['id'] == identity]
            from .personal_question import compile_row
            derived = compile_row(snapshot, operations, claims, prior)
            resources[key] = {**(deepcopy(prior) if prior else {'kind': kind, 'id': identity, 'base_revision': owner['revision'],
                'base_sha256': owner['source_sha256'], 'base_fields': {f: field_value(snapshot, f) for f in allowed}, 'fields': {}}), **derived}
            resources[key]['fields'].update({o['field']: o['after'] for o in operations})
            continue
        if (op['field'] not in allowed
                or op['verb'] not in {'revise_translation', 'revise_explanation', *({'revise_annotation'} if version >= 3 else set()),
                    *({'revise_prerequisites', 'revise_reference', 'replace_example'} if version >= 5 else set())}
                or claim['candidate_sha256'] != digest(op['after'])
                or claim['source_sha256'] != owner['source_sha256']
                or claim['allowed_uses'] != (['replacement_review'] if op['verb'] == 'replace_example' else ['field_reference'])):
            raise ValueError('本入口仅采纳已核实的说明、译文和读法；原句、整题答案和关系仍需专门核对。')
        identity = owner['id']; key = (kind, identity); old = previous.get(head_key(identity, kind))
        prior = next((r for r in old['resources'] if r['id'] == identity and r.get('kind', 'lesson') == kind), None) if old else None
        if key not in resources:
            snapshot = next(r['snapshot'] for r in plan['manifest']['resources'] if r['kind'] == kind and r['id'] == identity)
            if version >= 5 and kind in {'lesson', 'reference'}:
                from .personal_graph import addressable
                snapshot = addressable(kind, snapshot)
            resources[key] = (deepcopy(prior) if prior else {'id': identity, 'base_revision': owner['revision'],
                'base_sha256': owner['source_sha256'], 'base_fields': {k: field_value(snapshot, k) for k in allowed}, 'fields': {}})
            if kind != 'lesson': resources[key]['kind'] = kind
            for field in allowed - set(resources[key]['base_fields']):
                resources[key]['base_fields'][field] = field_value(snapshot, field)
        resources[key]['fields'][op['field']] = op['after']
    result = [resources[k] for k in sorted(resources)]
    if version >= 5:
        from .feedback_graph_review import inspect
        problems = inspect(graph_snapshot)['problems']
        if any(p['kind'] != 'lexical_target_outside_graph' for p in problems):
            raise ValueError('图谱仍有源版本、片段或前置结构问题，请先处理。')
        from .personal_graph import compile_relationships, compile_replacements
        compile_relationships(result, group, graph_snapshot)
        compile_replacements(result, plan, group, support, graph_snapshot, graph_review or {})
    return result


class PersonalContentVersionStore:
    def __init__(self, analysis):
        from .feedback_field_support import FeedbackFieldSupportStore
        self.analysis, self.store, self.db = analysis, analysis.store, analysis.db
        self.supports = FeedbackFieldSupportStore(analysis)
        self.plans = self.supports.plans

    def get(self, identity):
        value = self.store.get_setting(PREFIX + identity, None)
        return validate(value) if value else None

    def latest(self, plan_id, group_id):
        row = self.db.execute("SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.plan_id')=? AND json_extract(value_json,'$.group_id')=? ORDER BY json_extract(value_json,'$.generation') DESC LIMIT 1",
            (PREFIX + '*', plan_id, group_id)).fetchone()
        return validate(json.loads(row[0])) if row else None

    def active(self, event):
        return all(self.store.get_setting(k, None) == event['id'] for k in event['previous_heads'])

    def formal_recheck_targets(self, identity):
        """Return affected learning IDs without modifying records or due dates."""
        from .content_history import formal_recheck_targets
        return formal_recheck_targets(self.db, identity)

    def prepare(self, plan_id, group_id, *, graph_review=None):
        before = token(self.db); plan = self.plans.get(plan_id)
        if not plan: raise ValueError('找不到修订计划。')
        saved = self.latest(plan_id, group_id)
        if saved: return {'saved': saved}
        self.plans.assert_current(plan)
        group = next((g for g in plan['groups'] if g['id'] == group_id), None)
        support = self.supports.latest(plan_id, group_id)
        if not group or not support: raise ValueError('请先完成整组字段核据。')
        from .personal_content_compensation import hold_requirements
        required, held_groups = hold_requirements(self.store)
        proposed_keys = {head_key(o['owner']['id'], o['owner']['kind']) for o in group['operations']}
        if any(not held_groups.get(k, set()) <= proposed_keys for k in proposed_keys):
            raise ValueError('请整组重新核对撤下的联动内容。')
        for kind, owner in {(o['owner']['kind'], o['owner']['id']) for o in group['operations']}:
            checked = {o['field'] for o in group['operations'] if (o['owner']['kind'], o['owner']['id']) == (kind, owner)}
            if not required.get((kind, owner), set()) <= checked:
                raise ValueError('请先重新核对这组撤下内容的所有字段。')
        keys = {head_key(op['owner']['id'], op['owner']['kind']) for op in group['operations']}
        heads = {k: self.store.get_setting(k, None) for k in keys}
        prior = {k: self.get(v) if v else None for k, v in heads.items()}
        if any(v and not set(v['previous_heads']) <= keys for v in prior.values()):
            raise ValueError('不能只替换之前联动修订的一部分。')
        from .personal_reading import READING_FIELDS
        version = (5 if any(op['verb'] in {'revise_prerequisites', 'revise_reference', 'replace_example'} for op in group['operations'])
                   or any('prerequisites' in r['snapshot'] or 'link' in r['snapshot'] for r in plan['manifest']['resources'])
                   or any(v and v['version'] >= 5 for v in prior.values())
                   else 4 if any(op['owner']['kind'] == 'question' for op in group['operations']) or any(v and v['version'] >= 4 for v in prior.values())
                   else 3 if any(op['field'] in READING_FIELDS.get(op['owner']['kind'], set()) for op in group['operations']) or any(v and v['version'] >= 3 for v in prior.values()) else 2)
        from .feedback_impact import PREFIX as IMPACT
        graph_snapshot = self.store.get_setting(IMPACT + plan['impact_id'])['report']['graph_review']['snapshot']
        resources = compile_resources(plan, group, support, prior, version=version,
            graph_snapshot=graph_snapshot, graph_review=graph_review)
        nonce = digest(uuid4().hex); identity = digest([FORMAT, plan_id, group_id, nonce])
        value = validate({'format': FORMAT, 'version': version, 'nonce': nonce, 'id': identity, 'plan_id': plan_id,
            **({'graph_review': deepcopy(graph_review or {})} if version >= 5 else {}),
            'plan_sha256': digest(plan), 'group_id': group_id, 'support_id': support['id'],
            'support_sha256': digest(support), 'generation': self.store.get_setting(GENERATION, 0) + 1,
            'created_at': app_clock.utc_now().isoformat(), 'previous_heads': heads, 'resources': resources})
        from .content_history import prepare as prepare_history
        history = prepare_history(self.store, plan, group)
        if before != token(self.db): raise ValueError('采纳准备期间记录已变化，请重试。')
        prepared = {'event': value, 'token': before, 'history': history}
        self._prepared = (digest(prepared), plan)
        return prepared

    def commit(self, prepared):
        if 'saved' in prepared:
            saved = self.get(prepared['saved']['id'])
            if saved != prepared['saved']: raise ValueError('已有采纳回执不一致。')
            return saved
        guard = getattr(self, '_prepared', None)
        if not guard or guard[0] != digest(prepared): raise ValueError('请重新准备采纳。')
        event = validate(prepared['event']); saved = self.get(event['id'])
        if saved: return saved
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if token(self.db) != tuple(prepared['token']): raise ValueError('采纳准备后记录已变化，请重试。')
            self.plans.assert_current(guard[1])
            self.db.execute('INSERT INTO app_setting VALUES (?,?)', (PREFIX + event['id'], dump(event)))
            for key in event['previous_heads']:
                self.db.execute('INSERT OR REPLACE INTO app_setting VALUES (?,?)', (key, dump(event['id'])))
                from .personal_content_compensation import hold_key, OUTBOX, write
                write(self.db, hold_key(key), None)
            write(self.db, OUTBOX + event['id'], {'event_id': event['id'], 'delivered': False})
            self.db.execute('INSERT OR REPLACE INTO app_setting VALUES (?,?)', (GENERATION, dump(event['generation'])))
            from .content_history import apply_in_transaction
            group = next(g for g in guard[1]['groups'] if g['id'] == event['group_id'])
            apply_in_transaction(self.store, event, guard[1], group, prepared['history'])
        return event


def question_blocked(store, item, question):
    return question_review(store, item, question)['blocked']


def question_review(store, item, question, *, for_history=False):
    """Reconstruct exact factory contracts; unknown old tasks remain pending.

After-answer text may change without invalidating an otherwise identical task.
Hints, alternatives, audio, annotation and scoring fields must still agree.
"""
    clear, blocked = {'blocked': False}, {'blocked': True}
    if store is None or not item: return clear
    from .personal_content_compensation import held
    if held(store, item.get('id', '')): return blocked
    event = active_version(store, item.get('id', ''))
    if event is None and not question.get('personal_rubric'):
        from .personal_question import rows_for
        if not rows_for(store, item.get('id')): return clear
    from .question_dependencies import factory_matches, ROLES
    from .learning_questions import build_learning_question, rebuild_practice_question
    from .assessment_policy import contract_values
    from .material_review import reviewed_question
    try:
        saved = store._db.execute('SELECT content_json FROM learning_content WHERE content_id=?', (item['id'],)).fetchone()
        if saved:
            base = json.loads(saved[0])
        else:
            from .learning_content import get_learning_item
            base = get_learning_item(item['id']) or item
        trusted = teaching_source(base)
        current = resolve(store, trusted)
        if current.get('personal_content_unavailable'): return blocked
        from .personal_question import review as review_rubric
        rubric = review_rubric(store, current, question, require_input=not for_history)
        if rubric is not None: return rubric
        if event is None: return clear
        frozen = {k: v for k, v in question.items() if k not in {'result', 'status'}}
        presented = reviewed_question(frozen)
        if not any(factory_matches(q, source) for q in (frozen, presented) for source in (base, trusted, current)): return blocked
        rebuilt = (rebuild_practice_question(question, current) if question.get('practice_source') else
                   build_learning_question(current, question['kind'], variant=question['variant'], guided=question['guided']))
        # A future revision ID alone is never a certificate for an altered task.
        fields = set(ROLES) - {'feedback'}
        if (contract_values(rebuilt) != contract_values(presented)
                or any(rebuilt.get(k) != presented.get(k) for k in fields)): return blocked
        from .personal_reading import requires_reading_contract, reading_qualification
        if requires_reading_contract(current, rebuilt):
            qualification = reading_qualification(store, current, rebuilt, event)
            return {'blocked': qualification is None, 'reading_qualification': qualification}
        return clear
    except (ValueError, KeyError, TypeError):
        return blocked


def validate_database(db):
    from types import SimpleNamespace
    from .feedback_analysis import PREFIX as ANALYSIS, validate as validate_analysis
    def setting(key, default=None):
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    store = SimpleNamespace(_db=db, get_setting=setting)
    analysis = SimpleNamespace(store=store, db=db, get=lambda key: validate_analysis(setting(ANALYSIS + key)) if setting(ANALYSIS + key) else None)
    from .personal_content_compensation import PREFIX as WITHDRAW, HOLD, OUTBOX, validate as validate_withdrawal, hold_key
    service = PersonalContentVersionStore(analysis); heads = {}; events = {}; generation = 0; hold_heads = {}; all_events = {}; withdrawals = set()
    held_fields, held_groups = {}, {}
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ? OR key GLOB ? ORDER BY json_extract(value_json,\'$.generation\')', (PREFIX + '*', WITHDRAW + '*')):
        if key.startswith(WITHDRAW):
            event = validate_withdrawal(json.loads(raw)); adopted = events.get(event['adoption_id'])
            if (key != WITHDRAW + event['id'] or not adopted or event['generation'] != generation + 1
                    or event['adoption_id'] in withdrawals or digest(adopted) != event['adoption_sha256']
                    or event['restored_heads'] != adopted['previous_heads']
                    or set(event['previous_heads']) != set(adopted['previous_heads'])
                    or any(hold_heads.get(hold_key(k)) != v for k,v in event['previous_holds'].items())
                    or any(heads.get(k) != v for k,v in event['previous_heads'].items())):
                raise ValueError('内容补偿缺少完整的活动版本链。')
            heads.update(event['restored_heads']); withdrawals.add(event['adoption_id'])
            hold_heads.update({hold_key(k): event['id'] for k in event['previous_heads']})
            for r in adopted['resources']:
                hk = head_key(r['id'], r.get('kind','lesson'))
                held_fields.setdefault(hk, set()).update(r['fields'])
                held_groups.setdefault(hk, set()).update(event['previous_heads'])
            all_events[event['id']] = event; generation = event['generation']; continue
        event = validate(json.loads(raw)); plan = service.plans.get(event['plan_id']); support = service.supports.get(event['support_id'])
        group = next((g for g in plan['groups'] if g['id'] == event['group_id']), None) if plan else None
        if (key != PREFIX + event['id'] or not group or not support
                or digest(plan) != event['plan_sha256'] or digest(support) != event['support_sha256']
                or support['plan_id'] != plan['id'] or support['group_id'] != group['id']
                or event['generation'] != generation + 1
                or any(heads.get(k) != v for k, v in event['previous_heads'].items())):
            raise ValueError('个人内容缺少完整采纳与核据链。')
        prior = {k: events.get(v) for k, v in event['previous_heads'].items()}
        from .feedback_impact import PREFIX as IMPACT
        snapshot = setting(IMPACT + plan['impact_id'])['report']['graph_review']['snapshot'] if event['version'] >= 5 else None
        if (any(v and not set(v['previous_heads']) <= set(prior) for v in prior.values())
                or compile_resources(plan, group, support, prior, version=event['version'],
                    graph_snapshot=snapshot, graph_review=event.get('graph_review')) != event['resources']):
            raise ValueError('个人内容与审核字段不一致。')
        for r in event['resources']:
            hk = hold_key(head_key(r['id'], r.get('kind', 'lesson')))
            if hold_heads.get(hk):
                head = head_key(r['id'], r.get('kind','lesson'))
                required = held_fields[head]
                if not held_groups[head] <= set(event['previous_heads']): raise ValueError('不能拆开待核联动组。')
                checked = {o['field'] for o in group['operations'] if (o['owner']['kind'], o['owner']['id']) == (r.get('kind','lesson'), r['id'])}
                if not set(required) <= checked: raise ValueError('撤下字段未重新核对。')
            if event['version'] >= 2:
                hold_heads[hk] = None
                held_fields.pop(head_key(r['id'], r.get('kind','lesson')), None)
                held_groups.pop(head_key(r['id'], r.get('kind','lesson')), None)
        heads.update({k: event['id'] for k in event['previous_heads']}); events[event['id']] = event
        all_events[event['id']] = event
        generation = event['generation']
    actual = {k: json.loads(v) for k, v in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (HEAD + '*',))}
    if heads != actual or setting(GENERATION, 0) != generation: raise ValueError('个人内容活动版本不一致。')
    actual_holds = {k: json.loads(v) for k,v in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (HOLD + '*',))}
    if actual_holds != hold_heads: raise ValueError('内容待核状态与补偿链不一致。')
    boxes = set()
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (OUTBOX + '*',)):
        box = json.loads(raw)
        if (not isinstance(box, dict) or set(box) != {'event_id','delivered'} or box['event_id'] not in all_events
                or key != OUTBOX + box['event_id'] or type(box['delivered']) is not bool): raise ValueError('正式内容同步回执无效。')
        boxes.add(box['event_id'])
    if not {k for k,v in all_events.items() if v['format'] != FORMAT or v['version'] >= 2} <= boxes:
        raise ValueError('正式内容缺少成套同步回执。')
    for key, in db.execute("SELECT key FROM app_setting WHERE key GLOB 'personal_content_version*'"):
        if not (key == GENERATION or key.startswith((PREFIX, HEAD, WITHDRAW, HOLD, OUTBOX))): raise ValueError('未知个人内容版本。')
