"""User-attributed, evidence-scoped semantic review, not an assessment grant.

Unlike exact-local-field-v1 this method does not claim algorithmic entailment.
The application verifies the provenance and scope of an explicit human review;
content activation and individual question qualification remain separate gates.
"""
from copy import deepcopy
from datetime import datetime
import json

from . import app_clock
from .content_correction_plan import ContentCorrectionPlanStore
from .feedback_field_review import validate as validate_review
from .feedback_impact import token
from .material_workflow import digest, dump
from .retention_ai import _hash
from .tutor_store import _text

PREFIX = 'feedback_field_support_v1:'
FORMAT = 'kanamemory-feedback-field-support'
METHOD = 'user-reviewed-external-evidence-v1'

CHECKS = {
    'prerequisites': {'direction': '前置指向及实际必要性', 'stage': '适用阶段与已有能力',
        'distinction': '必要前置与同现或推荐顺序的区别', 'scope': '新增和移除关系的具体依据'},
    'link': {'target': '具体目标义项或规则', 'sentence': '对应原句及其版本',
        'span': '片段实际表达的用法', 'hint': '提示与解释的适用范围'},
    'translation': {'meaning': '原句意思与指代', 'polarity_time': '否定、时态与语气',
        'omission': '省略成分和补充内容', 'chinese': '汉语自然且未增删原意'},
    'reading': {'surface': '实际词形与活用', 'sense': '本句义项和语境',
        'variants': '适用的读法变体', 'notation': '假名拼写与发音形的区别', 'reference': '读音参考具体覆盖本词形'},
    'sentence': {'syntax': '句子结构与助词', 'meaning': '语境与完整意思',
        'naturalness': '自然度与语用', 'consistency': '与同组译文和读法一致'},
    'question': {'target': '实际考查的能力', 'sufficiency': '题干足以解题',
        'alternatives': '其他合理答案与允许变体', 'distractors': '选项与干扰项',
        'leakage': '注音、音频和提示未泄题'},
    'explanation': {'scope': '本义项或规则的适用范围', 'prerequisites': '必要前提与例外',
        'context': '示例实际体现的用法', 'consistency': '文字、示例与图解的一致性'},
}


def category(binding):
    field, kind = binding['field'], binding['resource_kind']
    if field in {'prerequisites', 'link'}: return field
    if kind == 'question': return 'question'
    if field.endswith('translation'): return 'translation'
    if field.endswith('reading'): return 'reading'
    if field in {'text', 'instruction.example'}: return 'sentence'
    return 'explanation'


def empty_decision(change):
    return {'binding_id': change['binding']['id'], 'status': 'pending', 'reason': '',
        'checks': {key: '' for key in CHECKS[category(change['binding'])]},
        'evidence': [{'id': eid, 'role': 'unknown', 'reason': ''} for eid in change['evidence_ids']],
        'comparison': {'status': 'unresolved', 'reason': ''}}


def compile_claims(plan, group, receipt, decisions):
    validate_review(receipt)
    if digest(receipt) != plan['manifest']['field_review_sha256'] or receipt['id'] != plan['manifest']['field_review_id']:
        raise ValueError('字段核据与原计划不一致。')
    changes = {c['binding']['id']: c for c in receipt['scope']['changes'] if c['binding']['id'] in group['binding_ids']}
    prior = {r['binding_id']: r for r in receipt['decisions']}
    evidence = {r['evidence']['id']: r for r in receipt['scope']['evidence']}
    if not isinstance(decisions, list) or len(decisions) != len(changes): raise ValueError('请保留这一组全部字段的核据结论。')
    claims, seen = [], set()
    for row in decisions:
        if (not isinstance(row, dict) or set(row) != {'binding_id', 'status', 'reason', 'checks', 'evidence', 'comparison'}
                or row['binding_id'] not in changes or row['binding_id'] in seen
                or row['status'] not in {'pending', 'supported', 'contradicted'}): raise ValueError('字段核据决定无效。')
        bid = row['binding_id']; seen.add(bid); c = changes[bid]; old = prior[bid]; kind = category(c['binding'])
        _text(row['reason'], 1600, blank=row['status'] == 'pending')
        if not isinstance(row['checks'], dict) or set(row['checks']) != set(CHECKS[kind]): raise ValueError('核据问题范围不完整。')
        for value in row['checks'].values(): _text(value, 800, blank=row['status'] != 'supported')
        comparison = row['comparison']
        if not isinstance(comparison, dict) or set(comparison) != {'status', 'reason'} or comparison['status'] not in {'compatible', 'resolved', 'unresolved'}:
            raise ValueError('独立复核的分歧处理无效。')
        _text(comparison['reason'], 1600, blank=row['status'] != 'supported')
        if not isinstance(row['evidence'], list) or len(row['evidence']) != len(c['evidence_ids']): raise ValueError('请逐条处理该字段实际送审的全部摘录。')
        roles = {}
        for e in row['evidence']:
            if (not isinstance(e, dict) or set(e) != {'id', 'role', 'reason'} or e['id'] not in c['evidence_ids']
                    or e['id'] in roles or e['role'] not in {'unknown', 'supports', 'opposes', 'context', 'irrelevant'}):
                raise ValueError('摘录的主张归属或核对结果无效。')
            _text(e['reason'], 800, blank=row['status'] != 'supported'); roles[e['id']] = e['role']
        supporting = sorted(eid for eid, role in roles.items() if role == 'supports')
        opposing = sorted(eid for eid, role in roles.items() if role == 'opposes')
        independent = {e['evidence_id'] for e in old['evidence_reviews'] if e['relationship'] == 'independent_user_asserted'}
        if row['status'] == 'supported':
            if (old['decision'] != 'supported_by_user' or not c['model_reviewed']
                    or not supporting or not independent.intersection(supporting) or not set(supporting) <= set(old['evidence_ids'])
                    or 'unknown' in roles.values() or comparison['status'] == 'unresolved'
                    or opposing and comparison['status'] != 'resolved'):
                raise ValueError('仍有未核来源、分歧或适用范围，不能作为有据修订。')
        use = ('rubric_review' if c['binding']['resource_kind'] == 'question' else
               'replacement_review' if kind == 'sentence' else 'field_reference')
        claims.append({'binding_id': bid, 'owner': {k: c['binding'][k] for k in ('resource_kind', 'resource_id', 'resource_revision')},
            'field': c['binding']['field'], 'candidate_sha256': digest(c['new_value']),
            'source_sha256': c['binding']['source_sha256'], 'method': METHOD, 'reviewer': 'current_user_not_asserted_expert',
            'status': row['status'], 'supporting_evidence_ids': supporting, 'opposing_evidence_ids': opposing,
            'evidence_sha256s': {eid: digest(evidence[eid]) for eid in sorted(roles)},
            'decision_sha256': digest(row), 'prior_decision_sha256': digest(old),
            'allowed_uses': [use] if row['status'] == 'supported' else ['preview'],
            'assessment_eligible': False, 'teaching_eligible': False})
    return sorted(claims, key=lambda c: c['binding_id'])


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'format', 'version', 'id', 'request_id', 'plan_id', 'plan_sha256',
            'group_id', 'field_review_id', 'field_review_sha256', 'generation', 'previous_id', 'created_at', 'decisions', 'claims'}
            or value['format'] != FORMAT or type(value['version']) is not int or value['version'] != 1
            or any(not _hash(value[k]) for k in ('id', 'plan_id', 'plan_sha256', 'group_id', 'field_review_id', 'field_review_sha256'))
            or value['id'] != digest([FORMAT, value['plan_id'], value['group_id'], value['request_id']])
            or type(value['generation']) is not int or value['generation'] < 1
            or value['previous_id'] is not None and not _hash(value['previous_id'])
            or datetime.fromisoformat(value['created_at']).tzinfo is None
            or not isinstance(value['decisions'], list) or not 1 <= len(value['decisions']) <= 12
            or not isinstance(value['claims'], list) or len(value['claims']) != len(value['decisions'])
            or len(dump(value).encode('utf-8')) > 256_000): raise ValueError('字段支持记录无效。')
    _text(value['request_id'], 128)
    return deepcopy(value)


class FeedbackFieldSupportStore:
    def __init__(self, analysis):
        self.analysis, self.store, self.db = analysis, analysis.store, analysis.db
        self.plans = ContentCorrectionPlanStore(analysis)

    def get(self, identity):
        from .feedback_field_review import PREFIX as REVIEW
        value = self.store.get_setting(PREFIX + identity, None)
        if value is None: return None
        value = validate(value); plan = self.plans.get(value['plan_id'])
        receipt = self.store.get_setting(REVIEW + value['field_review_id'], None)
        group = next((g for g in plan['groups'] if g['id'] == value['group_id']), None) if plan else None
        if (not group or not receipt or digest(plan) != value['plan_sha256']
                or value['field_review_id'] != plan['manifest']['field_review_id']
                or digest(receipt) != value['field_review_sha256']
                or compile_claims(plan, group, receipt, value['decisions']) != value['claims']):
            raise ValueError('字段支持缺少完整的原计划或核据链。')
        return value

    def latest(self, plan_id, group_id):
        row = self.db.execute("SELECT json_extract(value_json,'$.id') FROM app_setting WHERE key GLOB ? "
            "AND json_extract(value_json,'$.plan_id')=? AND json_extract(value_json,'$.group_id')=? "
            "ORDER BY json_extract(value_json,'$.generation') DESC LIMIT 1", (PREFIX + '*', plan_id, group_id)).fetchone()
        return self.get(row[0]) if row else None

    def preview(self, plan_id, group_id):
        before = token(self.db); plan = self.plans.get(plan_id)
        group = next((g for g in plan['groups'] if g['id'] == group_id), None) if plan else None
        if not group: raise ValueError('找不到修改组。')
        receipt = self.plans.assert_current(plan); latest = self.latest(plan_id, group_id)
        if before != token(self.db): raise ValueError('核据准备期间记录已变化。')
        return {'plan': plan, 'group': group, 'receipt': receipt, 'previous_id': latest['id'] if latest else None}

    def record(self, expected, decisions, *, request_id):
        _text(request_id, 128)
        plan, group, receipt = expected['plan'], expected['group'], expected['receipt']
        identity = digest([FORMAT, plan['id'], group['id'], request_id]); old = self.get(identity)
        if old:
            if old['decisions'] != decisions: raise ValueError('同一次核据提交不能改写决定。')
            return old
        before = token(self.db); current = self.preview(plan['id'], group['id'])
        if current != expected: raise ValueError('原计划或上一版核据已变化，请重新打开。')
        prior = self.get(expected['previous_id']) if expected['previous_id'] else None
        value = validate({'format': FORMAT, 'version': 1, 'id': identity, 'request_id': request_id,
            'plan_id': plan['id'], 'plan_sha256': digest(plan), 'group_id': group['id'],
            'field_review_id': receipt['id'], 'field_review_sha256': digest(receipt),
            'generation': prior['generation'] + 1 if prior else 1, 'previous_id': expected['previous_id'],
            'created_at': app_clock.utc_now().isoformat(), 'decisions': deepcopy(decisions),
            'claims': compile_claims(plan, group, receipt, decisions)})
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if token(self.db) != before: raise ValueError('核据准备后记录已变化，请重试。')
            self.plans.assert_current(plan)
            self.db.execute('INSERT INTO app_setting VALUES (?,?)', (PREFIX + value['id'], dump(value)))
        return value


def validate_database(db):
    from types import SimpleNamespace
    from .feedback_analysis import PREFIX as ANALYSIS, validate as validate_analysis
    def setting(key, default=None):
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    store = SimpleNamespace(_db=db, get_setting=setting)
    analysis = SimpleNamespace(store=store, db=db, get=lambda key: validate_analysis(setting(ANALYSIS + key)) if setting(ANALYSIS + key) else None)
    service = FeedbackFieldSupportStore(analysis); previous = {}
    rows = db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB ? ORDER BY json_extract(value_json,'$.generation')", (PREFIX + '*',))
    for key, raw in rows:
        value = service.get(validate(json.loads(raw))['id']); scope = (value['plan_id'], value['group_id']); prior = previous.get(scope)
        if (key != PREFIX + value['id'] or value['previous_id'] != (prior['id'] if prior else None)
                or value['generation'] != (prior['generation'] + 1 if prior else 1)):
            raise ValueError('字段支持修订链不完整。')
        previous[scope] = value
