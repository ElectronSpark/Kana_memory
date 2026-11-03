"""Local classification of observed dependencies, never a grade adjudication."""
from .question_dependencies import ROLES

LABELS = {'C0': '展示元数据', 'C1': '解释或呈现', 'C2': '判题依据', 'C3': '身份或知识关系需专门审查'}
ACTIONS = {'inspect_dependency': '用途尚待核对', 'inspect_rubric': '需核对判题依据',
    'inspect_stimulus': '需核对题意与作答条件', 'inspect_exposure': '需核对提示曝光',
    'review_explanation': '已知用途为答后说明', 'retain_snapshot': '保留原快照',
    'reanchor': '需重新定位片段', 'review_relation': '需核对关联', 'refresh_cache': '需刷新派生显示',
    'review_content': '需核对教学内容'}


def roles_for(row, binding):
    if row['state'] == 'not_actual_display' or row['version_match'] != 'same' or row['certainty'] != 'declared_field': return []
    reason = row['reason']
    if 'factory_field_uses:' in reason:
        roles = reason.split('factory_field_uses:', 1)[1].split(',')
        return sorted(set(roles)) if set(roles) <= set(ROLES.values()) else []
    if reason.endswith('exact_question_identity') and binding['resource_kind'] == 'question':
        role = ROLES.get(binding['field'])
        if binding['field'] == 'explanation': role = 'after_answer'
        if binding['field'] == 'rubric': role = 'rubric'
        return [role] if role else []
    return []


def classify(row, binding):
    if row['state'] == 'not_actual_display': return 'retain_snapshot'
    if row['kind'] == 'anchor': return 'reanchor'
    if row['kind'] in {'reference', 'relation'}: return 'review_relation'
    if row['kind'] == 'cache': return 'refresh_cache'
    roles = roles_for(row, binding)
    if 'rubric' in roles: return 'inspect_rubric'
    if 'stimulus' in roles: return 'inspect_stimulus'
    if set(roles).intersection({'hint', 'optional_hint'}): return 'inspect_exposure'
    if roles == ['after_answer']: return 'review_explanation'
    return 'review_content' if row['kind'] in {'resource', 'teaching'} else 'inspect_dependency'


def summarize(saved):
    rows = saved['report']['rows']; result = []
    for change in saved['changes']:
        binding = change['binding']; related = [r for r in rows if r['binding_id'] == binding['id']]
        actions = {}; classified = []
        for row in related:
            action = classify(row, binding)
            actions.setdefault(action, set()).add(row['locator'])
            classified.append({'id': row['id'], 'action': action, 'roles': roles_for(row, binding)})
        identity_review = binding['resource_kind'] == 'lesson' and binding['field'] in {'title', 'meaning'}
        category = ('C2' if 'inspect_rubric' in actions
            or binding['resource_kind'] == 'question' and binding['field'] in {'answer', 'rubric'} else 'C1')
        # All currently editable fields contain instructional content. C0 is
        # reserved for actual metadata operations, never guessed from wording.
        result.append({'binding_id': binding['id'], 'category': category,
            'actions': {k: len(v) for k, v in actions.items()}, 'rows': classified,
            'identity_review_required': identity_review,
            'dependency_scope_complete': False,  # declarations only prove positive uses
            'assessment_eligible': False, 'activation_eligible': False})
    return result
