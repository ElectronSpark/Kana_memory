"""Domain-specific, frozen correction plans. No generic patch or activation."""
from copy import deepcopy
from datetime import datetime
import json

from . import app_clock
from .feedback_bindings import current_resource, projection, field_value
from .feedback_field_review import FeedbackFieldReviewStore, validate as validate_review
from .feedback_impact import FeedbackImpactStore, validate as validate_impact, token
from .material_workflow import digest, dump
from .retention_ai import _hash

PREFIX = 'content_correction_plan_v1:'
FORMAT = 'kanamemory-content-correction-plan'
MAX_BYTES = 2 * 1024 * 1024
IDENTITIES = {'same_sense', 'different_sense', 'unresolved'}
LABELS = {'revise_explanation': '修订说明', 'revise_translation': '修订译文',
    'revise_prerequisites': '修订前置关系', 'revise_reference': '修订引用关系',
    'revise_annotation': '修订读法标注', 'revise_rubric': '修订判题依据',
    'replace_example': '新建修正例句', 'propose_identity_migration': '提出义项迁移'}
REQUIREMENTS = {
    'independent_fact_qualification': '核验新内容的逐字段依据',
    'current_impact_and_generation': '采纳前重验内容、成绩与队列版本',
    'inspect_unknown_dependencies': '核对缺少字段来源的题目和记录',
    'reanchor_every_reference': '逐条定位新句的引用片段并核对用法',
    'relink_lexical_examples': '重核词库例句与词条义项的关联',
    'revalidate_rubric_and_attribution': '验证完整判题合同及旧成绩归因',
    'explicit_identity_decision': '确认是否仍为同一义项',
    'dedicated_identity_migration': '使用专门迁移方案，不复制原熟练度',
    'graph_structural_issues': '处理图谱与片段的结构问题',
    'personal_note_only': '只可作为待核个人注释，不替换正式教学',
    'pending_user_decision': '完成整组字段决定',
    'conflicting_group_decisions': '同一版本内仍有互不兼容的字段决定',
    'rubric_answer_outside_choices': '新答案不在旧选项中，须连同选项一起修订',
    'rubric_old_answer_variants': '明确复核旧答案变体，不自动保留或删除',
}


def verb(binding, identity):
    kind, field = binding['resource_kind'], binding['field']
    if field == 'prerequisites': return 'revise_prerequisites'
    if field == 'link': return 'revise_reference'
    if identity == 'different_sense': return 'propose_identity_migration'
    if kind in {'example', 'lexical_example'} and field == 'text': return 'replace_example'
    if kind == 'question' and field in {'answer', 'rubric'}: return 'revise_rubric'
    if field.endswith('reading'): return 'revise_annotation'
    if field in {'translation', 'chinese_translation', 'instruction.translation'}: return 'revise_translation'
    return 'revise_explanation'


def validate_identities(value, changes):
    expected = {c['binding']['id'] for c in changes if c['binding']['resource_kind'] == 'lesson'
                and c['binding']['field'] in {'title', 'meaning'}}
    if not isinstance(value, dict) or set(value) != expected: raise ValueError('义项决定必须精确对应词形或词义字段。')
    for decision in value.values():
        if (not isinstance(decision, dict) or set(decision) != {'choice', 'reason'} or decision['choice'] not in IDENTITIES
                or not isinstance(decision['reason'], str) or len(decision['reason']) > 1000
                or decision['choice'] != 'unresolved' and not decision['reason'].strip()):
            raise ValueError('请说明义项是否不变；不确定时可保留待核。')
    return deepcopy(value)


def closure(report):
    """Exclude scan timestamps and counters, never evidence versions or gaps."""
    graph = report.get('graph_review')
    return {'rows': deepcopy(report['rows']), 'gaps': report['gaps'], 'coverage_names': sorted(report['coverage']),
        'enumeration_complete': report['enumeration_complete'],
        'graph_snapshot_sha256': graph['snapshot_sha256'] if graph else None,
        'graph_result': deepcopy(graph['result']) if graph else None}


def compile_groups(receipt, impact, resources, identities):
    """One content version cannot be partly revised by independent field clicks."""
    from .semantic_impact import summarize
    changes = receipt['scope']['changes']; known = {c['binding']['id']: c for c in changes}
    decisions = {d['binding_id']: d for d in receipt['decisions']}
    classified = {c['binding_id']: c for c in summarize(impact)}
    by_owner = {(r['kind'], r['id']): r for r in resources}
    links = deepcopy(receipt['scope']['dependencies'])
    for bid, change in known.items():
        b = change['binding']; owner = (b['resource_kind'], b['resource_id'])
        links[bid] = sorted(set(links[bid]) | {i for i, c in known.items()
            if i != bid and (c['binding']['resource_kind'], c['binding']['resource_id']) == owner})
    pending = set(links); groups = []; graph = (impact['report'].get('graph_review') or {}).get('result', {})
    while pending:
        connected = {min(pending)}
        while True:
            extended = connected | {d for i in connected for d in links[i]} | {i for i, ds in links.items() if connected.intersection(ds)}
            if extended == connected: break
            connected = extended
        pending -= connected; operations = []; requirements = set(); levels = {'C0'}
        chosen = {decisions[i]['decision'] for i in connected}
        status = ('rejected' if chosen == {'reject'} else 'personal_note_plan' if chosen <= {'supported_by_user', 'display_note'}
                  and 'display_note' in chosen else 'content_review_plan' if chosen == {'supported_by_user'} else 'pending')
        if status == 'personal_note_plan': requirements.add('personal_note_only')
        elif status == 'pending':
            requirements.add('conflicting_group_decisions' if 'reject' in chosen else 'pending_user_decision')
        if status != 'rejected': requirements.add('current_impact_and_generation')
        if status == 'content_review_plan': requirements.add('independent_fact_qualification')
        if not graph or not graph.get('structure_valid'): requirements.add('graph_structural_issues')
        for bid in sorted(connected):
            c = known[bid]; b = c['binding']; owner = by_owner[(b['resource_kind'], b['resource_id'])]
            identity = identities.get(bid, {}).get('choice'); action = verb(b, identity)
            levels.add('C3' if action == 'propose_identity_migration' else classified[bid]['category'])
            if identity == 'unresolved': requirements.add('explicit_identity_decision')
            if action == 'propose_identity_migration': requirements.add('dedicated_identity_migration')
            if classified[bid]['actions'].get('inspect_dependency'): requirements.add('inspect_unknown_dependencies')
            replacement = None
            related = [r for r in graph.get('references', []) if r['example_id'] == b['resource_id']]
            if action == 'replace_example':
                replacement = 'personal:example:' + digest([b['resource_kind'], b['resource_id'], b['source_sha256'], c['new_value']])
                requirements.add('reanchor_every_reference')
                if any(r['binding_id'] == bid for r in (impact['report'].get('graph_review') or {}).get('snapshot', {}).get('lexical_links', [])):
                    requirements.add('relink_lexical_examples')
                if b['resource_kind'] == 'lexical_example':
                    requirements.add('relink_lexical_examples')
                    frozen = impact['report']['graph_review']['snapshot']
                    origins = {(e['origin'], e['snapshot']['id']) for e in frozen['examples']
                        if b['resource_id'] in e['snapshot'].get('lexical_review_ids', []) or any(
                            p.get('review_id') == b['resource_id'] or p.get('original', {}).get('example_id') == b['resource_id']
                            for p in e['snapshot'].get('lexical_provenance', []))}
                    keys = {r['origin'] + ':' + r['snapshot']['id'] for r in frozen['references']
                            if (r['origin'], r['snapshot']['example_id']) in origins}
                    related = [r for r in graph.get('references', []) if r['key'] in keys]
            if classified[bid]['category'] == 'C2' or action == 'revise_rubric':
                requirements.add('revalidate_rubric_and_attribution')
            if action == 'revise_rubric' and b['field'] != 'rubric':
                q = owner['snapshot']; choices = q.get('choices', q.get('options', []))
                if q.get('input_mode', q.get('response')) == 'choice' and c['new_value'] not in choices:
                    requirements.add('rubric_answer_outside_choices')
                if q.get('accepted_answers') or q.get('alternative_answers'):
                    requirements.add('rubric_old_answer_variants')
            operation = {'verb': action, 'binding_id': bid, 'owner': {'kind': owner['kind'], 'id': owner['id'],
                'revision': b['resource_revision'], 'source_sha256': b['source_sha256'], 'snapshot_sha256': owner['sha256']},
                'field': b['field'], 'before_sha256': digest(b['value']), 'after': c['new_value'],
                'replacement_id': replacement, 'supersedes': b['resource_id'] if replacement else None,
                'reference_checks': sorted(r['key'] for r in related) if replacement else [],
                'lexical_link_checks': sorted(digest(r['link']) for r in (impact['report'].get('graph_review') or {}).get('snapshot', {}).get('lexical_links', [])
                    if r['binding_id'] == bid) if action == 'replace_example' else [],
                'evidence_ids': decisions[bid]['evidence_ids'], 'user_decision': decisions[bid]['decision']}
            operation['id'] = digest(operation); operations.append(operation)
        group = {'binding_ids': sorted(connected), 'operations': operations, 'category': max(levels), 'status': status,
            'requirements': sorted(requirements), 'assessment_eligible': False, 'activation_eligible': False,
            'progress_policy': 'retain_facts_no_automatic_regrade', 'due_policy': 'preserve_formal_due_auxiliary_review_required'}
        group['id'] = digest(group); groups.append(group)
    return groups


def manifest(receipt, impact, parent, resources, identities):
    from .feedback_batch import members
    return {'field_review_id': receipt['id'], 'field_review_sha256': digest(receipt),
        'source_id': receipt['scope']['source_id'], 'source_sha256': receipt['scope']['source_sha256'],
        'phase': receipt['scope']['phase'], 'parent_id': parent['id'], 'parent_sha256': digest(parent),
        'feedback_refs': [deepcopy(r['feedback']) for r in members(parent['request'])],
        'resources': resources, 'closure_sha256': digest(closure(impact['report'])), 'identities': identities}


def validate(v):
    if (not isinstance(v, dict) or set(v) != {'format', 'version', 'id', 'created_at', 'sequence', 'manifest', 'manifest_sha256',
            'impact_id', 'impact_sha256', 'groups', 'assessment_eligible', 'activation_eligible'}
            or v['format'] != FORMAT or type(v['version']) is not int or v['version'] != 1
            or any(not _hash(v[k]) for k in ('id', 'manifest_sha256', 'impact_id', 'impact_sha256'))
            or v['manifest_sha256'] != digest(v['manifest']) or v['id'] != digest([FORMAT, 1, v['manifest_sha256']])
            or v['assessment_eligible'] is not False or v['activation_eligible'] is not False
            or type(v['sequence']) is not int or v['sequence'] < 1
            or datetime.fromisoformat(v['created_at']).tzinfo is None or len(dump(v).encode('utf-8')) > MAX_BYTES):
        raise ValueError('内容修订计划格式无效。')
    m = v['manifest']
    if (not isinstance(m, dict) or set(m) != {'field_review_id', 'field_review_sha256', 'source_id', 'source_sha256',
            'phase', 'parent_id', 'parent_sha256', 'feedback_refs', 'resources', 'closure_sha256', 'identities'}
            or m['phase'] not in {'analysis', 'revision'}
            or any(not _hash(m[k]) for k in ('field_review_id', 'field_review_sha256', 'source_id', 'source_sha256',
                'parent_id', 'parent_sha256', 'closure_sha256'))
            or not isinstance(m['resources'], list) or not 1 <= len(m['resources']) <= 12
            or not isinstance(v['groups'], list) or not 1 <= len(v['groups']) <= 12):
        raise ValueError('修订计划缺少完整基线。')
    seen = set()
    for r in m['resources']:
        if (not isinstance(r, dict) or set(r) != {'kind', 'id', 'snapshot', 'sha256'}
                or r['kind'] not in {'lesson', 'example', 'lexical_example', 'reference', 'question'}
                or not isinstance(r['snapshot'], dict) or r['snapshot'].get('id') != r['id']
                or r['sha256'] != digest(r['snapshot']) or (r['kind'], r['id']) in seen):
            raise ValueError('修订基线对象或版本无效。')
        seen.add((r['kind'], r['id']))
    # Full operation validation must resolve the original immutable receipts.
    # Do not offer an import/API path that treats self-hashed operations as authority.
    return deepcopy(v)


def check_inputs(plan, receipt, impact, parent, source):
    m = plan['manifest']
    validate_review(receipt); validate_impact(impact)
    from .feedback_analysis import validate as validate_analysis
    from .feedback_revision import validate as validate_revision
    scope = receipt['scope']; validate_analysis(parent)
    (validate_analysis if scope['phase'] == 'analysis' else validate_revision)(source)
    if (parent['id'] != scope['parent_id'] or source['id'] != scope['source_id'] or digest(source) != scope['source_sha256']
            or scope['phase'] == 'analysis' and source != parent
            or scope['phase'] == 'revision' and (source['request']['parent_id'] != parent['id']
                or source['request']['parent_sha256'] != digest(parent) or source['request']['review_id'] != scope['review_id']
                or source['request']['review_sha256'] != scope['review_sha256'])
            or impact['feedback_id'] != parent['request']['feedback']['id']
            or impact['feedback_revision'] != parent['request']['feedback']['input_revision']):
        raise ValueError('修订计划父报告或来源链归属不一致。')
    if impact['changed_during_scan'] or not impact['report']['enumeration_complete'] or not impact['report'].get('graph_review'):
        raise ValueError('修订计划不能沿用不完整或已过期的影响检查。')
    changes = receipt['scope']['changes']
    identities = validate_identities(m['identities'], changes)
    if (receipt['scope']['source_id'] != impact['source_id'] or receipt['scope']['phase'] != impact['phase']
            or [{'binding': c['binding'], 'new_value': c['new_value']} for c in changes] != impact['changes']
            or manifest(receipt, impact, parent, m['resources'], identities) != m
            or plan['impact_id'] != impact['id'] or plan['impact_sha256'] != digest(impact)):
        raise ValueError('修订计划与原稿、核据或影响报告不一致。')
    owners = {(r['kind'], r['id']): r for r in m['resources']}
    if set(owners) != {(c['binding']['resource_kind'], c['binding']['resource_id']) for c in changes}:
        raise ValueError('修订基线范围不完整。')
    for c in changes:
        b = c['binding']; source = owners[(b['resource_kind'], b['resource_id'])]['snapshot']
        if digest(projection(b['resource_kind'], source)) != b['source_sha256'] or field_value(source, b['field']) != b['value']:
            raise ValueError('修订计划缺少一致的原内容版本。')
    if compile_groups(receipt, impact, m['resources'], identities) != plan['groups']:
        raise ValueError('修订领域操作或联动组不一致。')
    return plan


class ContentCorrectionPlanStore:
    def __init__(self, analysis):
        self.analysis, self.store, self.db = analysis, analysis.store, analysis.db
        self.fields = FeedbackFieldReviewStore(analysis)

    def _load(self, prefix, identity, validator):
        raw = self.store.get_setting(prefix + identity, None)
        return validator(raw) if raw is not None else None

    def get(self, identity):
        value = self._load(PREFIX, identity, validate)
        if value is None: return None
        from .feedback_field_review import PREFIX as REVIEW
        from .feedback_impact import PREFIX as IMPACT
        receipt = self._load(REVIEW, value['manifest']['field_review_id'], validate_review)
        impact = self._load(IMPACT, value['impact_id'], validate_impact)
        parent = self.analysis.get(value['manifest']['parent_id'])
        m = value['manifest']
        source = self.analysis.get(m['source_id']) if m['phase'] == 'analysis' else self.fields.revisions.get(m['source_id'])
        if not receipt or not impact or not parent or not source: raise ValueError('修订计划缺少原始审查记录。')
        return check_inputs(value, receipt, impact, parent, source)

    def latest(self, phase, identity):
        row = self.db.execute("SELECT json_extract(value_json,'$.id') FROM app_setting WHERE key GLOB ? "
            "AND json_extract(value_json,'$.manifest.phase')=? AND json_extract(value_json,'$.manifest.source_id')=? "
            "ORDER BY json_extract(value_json,'$.sequence') DESC LIMIT 1", (PREFIX + '*', phase, identity)).fetchone()
        return self.get(row[0]) if row else None

    def prepare(self, phase, identity, impact, *, identities=None):
        """Bind a new plan to a fresh local impact scan."""
        before = token(self.db); expected = self.fields.preview(phase, identity)
        receipt = self.fields.latest(phase, identity)
        if not receipt or receipt['scope'] != expected['scope']: raise ValueError('请先保存当前稿的字段核对决定。')
        impact = validate_impact(impact)
        if (impact['source_id'] != identity or impact['phase'] != phase or impact['source_sha256'] != receipt['scope']['source_sha256']
                or impact['changes'] != [{'binding': c['binding'], 'new_value': c['new_value']} for c in receipt['scope']['changes']]):
            raise ValueError('影响报告不属于当前核对稿。')
        if impact['changed_during_scan'] or not impact['report']['enumeration_complete'] or not impact['report'].get('graph_review'):
            raise ValueError('请先完成当前版本的图谱和影响检查。')
        guard = getattr(self.store, '_feedback_impact_guards', {}).get(impact['id'])
        if not guard or guard['token'] != before:
            raise ValueError('影响报告已不是当前数据库状态，请重新检查。')
        changes = receipt['scope']['changes']
        identities = validate_identities(identities if identities is not None else
            {c['binding']['id']: {'choice': 'unresolved', 'reason': ''} for c in changes
             if c['binding']['resource_kind'] == 'lesson' and c['binding']['field'] in {'title', 'meaning'}}, changes)
        resources = {}
        for c in changes:
            b = c['binding']; current = current_resource(b, **self.analysis.resources)
            if current is None or digest(projection(b['resource_kind'], current)) != b['source_sha256']:
                raise ValueError('原内容已改变，请重新核对。')
            if digest(current) != guard['resources'].get(b['id']): raise ValueError('完整内容基线已变化，请重新检查影响。')
            snapshot = json.loads(dump(current)); owner = (b['resource_kind'], b['resource_id'])
            resources[owner] = {'kind': owner[0], 'id': owner[1], 'snapshot': snapshot, 'sha256': digest(snapshot)}
        parent = self.analysis.get(receipt['scope']['parent_id'])
        if not parent or not self.analysis.feedbacks_current(parent['request']): raise ValueError('原反馈已变化。')
        source = parent if phase == 'analysis' else self.fields.revisions.get(identity)
        resources = [resources[k] for k in sorted(resources)]
        m = manifest(receipt, impact, parent, resources, identities); fingerprint = digest(m)
        value = {'format': FORMAT, 'version': 1, 'id': digest([FORMAT, 1, fingerprint]),
            'created_at': app_clock.utc_now().isoformat(), 'manifest': m, 'manifest_sha256': fingerprint,
            'sequence': self.db.execute("SELECT COALESCE(MAX(json_extract(value_json,'$.sequence')),0)+1 FROM app_setting WHERE key GLOB ?", (PREFIX + '*',)).fetchone()[0],
            'impact_id': impact['id'], 'impact_sha256': digest(impact),
            'groups': compile_groups(receipt, impact, resources, identities), 'assessment_eligible': False, 'activation_eligible': False}
        value = check_inputs(validate(value), receipt, impact, parent, source)
        if token(self.db) != before: raise ValueError('准备计划期间记录有变化，请重试。')
        prepared = {'plan': value, 'token': before, 'field_review_id': receipt['id']}
        guards = getattr(self.store, '_content_plan_guards', {})
        guards[value['id']] = {'sha256': digest(prepared), 'token': before}
        self.store._content_plan_guards = dict(list(guards.items())[-8:])
        return prepared

    def assert_current(self, plan):
        """Recheck finite source preconditions without rerunning the graph scan."""
        m = plan['manifest']; receipt = self.fields.latest(m['phase'], m['source_id'])
        parent = self.analysis.get(m['parent_id'])
        if (not receipt or receipt['id'] != m['field_review_id'] or digest(receipt) != m['field_review_sha256']
                or not parent or digest(parent) != m['parent_sha256'] or not self.analysis.feedbacks_current(parent['request'])):
            raise ValueError('反馈或核对决定已变化，请重新准备计划。')
        # The selected external excerpts and a reference's sentence/target
        # context can change without changing the root owner itself.
        current = self.fields.preview(m['phase'], m['source_id'])
        if current['scope'] != receipt['scope']:
            raise ValueError('原证据或独立复核范围已变化，请重新核对。')
        for r in m['resources']:
            b = next(c['binding'] for c in receipt['scope']['changes']
                if (c['binding']['resource_kind'], c['binding']['resource_id']) == (r['kind'], r['id']))
            current = current_resource(b, **self.analysis.resources)
            if current is None or digest(current) != r['sha256']:
                raise ValueError('原内容版本已变化，请重新准备计划。')
        return receipt

    def save(self, prepared):
        p = prepared['plan']; m = p['manifest']
        validate(p)
        old = self.get(p['id'])
        if old:
            if old['manifest'] != m or old['groups'] != p['groups']: raise ValueError('同一修订计划不能改写内容。')
            return old
        guard = getattr(self.store, '_content_plan_guards', {}).get(p['id'])
        if not guard or digest(prepared) != guard['sha256']:
            raise ValueError('请重新准备修改计划，不能直接提交外部计划。')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if token(self.db) != guard['token']: raise ValueError('准备后内容或学习记录已变化，请重新检查。')
            # Recheck only current parent/decisions/owners under the short lock.
            # The sealed local preparation proves the expensive graph/closure
            # checks ran on this unchanged DB state; imports cannot supply it.
            receipt = self.fields.latest(m['phase'], m['source_id'])
            parent = self.analysis.get(m['parent_id'])
            if (not receipt or receipt['id'] != m['field_review_id'] or digest(receipt) != m['field_review_sha256']
                    or not parent or digest(parent) != m['parent_sha256'] or not self.analysis.feedbacks_current(parent['request'])):
                raise ValueError('修订计划基线已经变化。')
            known = {(r['kind'], r['id']): r for r in m['resources']}
            for change in receipt['scope']['changes']:
                b = change['binding']; current = current_resource(b, **self.analysis.resources)
                if current is None or digest(current) != known[(b['resource_kind'], b['resource_id'])]['sha256']:
                    raise ValueError('原内容版本已经变化。')
            self.db.execute('INSERT INTO app_setting VALUES (?,?)', (PREFIX + p['id'], dump(p)))
        return self.get(p['id'])


def validate_database(db):
    from types import SimpleNamespace
    from .feedback_analysis import PREFIX as ANALYSIS, validate as validate_analysis
    def get_setting(key, default=None):
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    store = SimpleNamespace(_db=db, get_setting=get_setting)
    analysis = SimpleNamespace(store=store, db=db, get=lambda identity: validate_analysis(get_setting(ANALYSIS + identity))
        if get_setting(ANALYSIS + identity) is not None else None)
    plans = ContentCorrectionPlanStore(analysis)
    seen = set()
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (PREFIX + '*',)):
        value = validate(json.loads(raw))
        if key != PREFIX + value['id'] or value['sequence'] in seen: raise ValueError('修订计划身份或顺序错误。')
        seen.add(value['sequence'])
        plans.get(value['id'])
