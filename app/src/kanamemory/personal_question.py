"""Exact, reviewed rubric revisions using the personal-content transaction.

The bundle is an editable semantic unit, not a model-supplied permission flag.
It cannot change the target, measured ability, response mode or scheduler.
Historical task snapshots stay immutable; only a newly built task is projected.
"""
from collections import Counter
from copy import deepcopy
import json

from .material_workflow import digest, dump

BUNDLE_FIELDS = ('prompt', 'task', 'answer', 'accepted_answers', 'alternative_answers',
                 'retired_ambiguous_answers', 'choices', 'known_wrong', 'feedback', 'audio_text', 'pitch_practice')
RESOURCE_FIELDS = {'prompt', 'task', 'answer', 'feedback', 'explanation', 'rubric'}
METHOD = 'reviewed-exact-rubric-v1'


def bundle(question):
    return {key: deepcopy(question.get(key, [] if key in {'accepted_answers', 'alternative_answers', 'retired_ambiguous_answers', 'choices', 'known_wrong'}
                                       else None if key == 'pitch_practice' else '')) for key in BUNDLE_FIELDS}


def addressable(question):
    if question is None: return None
    result = deepcopy(question)
    if result.get('personal_rubric', {}).get('owner_id'):
        # A later attempt has its own queue ID; maintenance still addresses
        # the original content owner, so a second revision replaces its head.
        result['id'] = result['personal_rubric']['owner_id']
    result['rubric'] = dump(bundle(question))
    return result


def scope(question):
    from .assessment_policy import contract_values
    value = contract_values(question)
    # The same reviewed template can be shuffled in another learning round.
    # Prompt, ability, answer, support and every other scoring fact stay bound.
    for key in ('choices', 'options'):
        if isinstance(value.get(key), list): value[key] = sorted(value[key])
    value['feedback'] = question.get('feedback')
    value['practice_source'] = question.get('practice_source')
    return digest(value)


def parse(text, original):
    from .learning_questions import KIND_ABILITIES, _normal, _grammar_kana, _reading_normal
    from .pitch_practice import is_pitch_question, validate_snapshot
    try: value = json.loads(text)
    except (ValueError, TypeError): raise ValueError('整题修订应包含完整的答案、变体、选项和解析。') from None
    if not isinstance(value, dict) or set(value) != set(BUNDLE_FIELDS):
        raise ValueError('整题修订字段不完整，不能保留未经复核的旧答案变体。')
    for key in ('prompt', 'task', 'answer', 'feedback', 'audio_text'):
        if not isinstance(value[key], str) or len(value[key]) > 4000:
            raise ValueError('整题修订文本无效。')
    if any(not value[k].strip() for k in ('prompt', 'answer', 'feedback')):
        raise ValueError('题干、标准答案和解析不可为空。')
    normal = (_reading_normal if original.get('kind') == 'vocab_reading' else
              (lambda s: _normal(_grammar_kana(s))) if original.get('family') == 'grammar'
              and original.get('domain') != 'pronunciation' and original.get('input_mode') in {'text', 'reorder'} else _normal)
    for key in ('accepted_answers', 'alternative_answers', 'retired_ambiguous_answers', 'choices', 'known_wrong'):
        entries = value[key]
        if (not isinstance(entries, list) or len(entries) > 64
                or any(not isinstance(s, str) or not s.strip() or len(s) > 2000 for s in entries)):
            raise ValueError('答案变体和选项必须为有限文本列表。')
        if key != 'choices' and len(set(entries)) != len(entries):
            raise ValueError('答案或变体重复。')
    accepted = {normal(s) for s in value['accepted_answers']}
    alternatives = {normal(s) for s in value['alternative_answers']}
    wrong = {normal(s) for s in value['known_wrong']}
    retired = {normal(s) for s in value['retired_ambiguous_answers']}
    if (normal(value['answer']) not in accepted or accepted & (alternatives | wrong | retired) or alternatives & wrong or retired & wrong):
        raise ValueError('标准答案、同目标允许答案、其他语境读法和明确错误必须分开。')
    if original.get('kind') not in KIND_ABILITIES or original.get('ability') != KIND_ABILITIES[original['kind']]:
        raise ValueError('只能修订既有受控题型，不能更换目标能力。')
    mode = original.get('input_mode')
    if mode == 'choice':
        choices = value['choices']
        if (not 2 <= len(choices) <= 32 or len({normal(s) for s in choices}) != len(choices)
                or value['answer'] not in choices or sum(normal(s) in accepted for s in choices) != 1
                or any(normal(s) in alternatives | retired for s in choices)):
            raise ValueError('选择题必须有且只有一个正确选项，其他语境的合理答案不能充当错误选项。')
    elif mode == 'reorder':
        if not _can_order(value['choices'], value['answer'], normal):
            raise ValueError('排序题词块无法组成标准答案。')
    elif mode not in {'text', 'pitch', 'pitch_sequence', 'pitch_contour'} and not is_pitch_question(original):
        raise ValueError('这个作答方式尚无可核对的整题合同。')
    if original.get('requires_audio') and not value['audio_text'].strip():
        raise ValueError('听力题需要与题意一致的朗读文本。')
    if not original.get('requires_audio') and value['audio_text']:
        raise ValueError('非听力题不能通过新增朗读泄露目标读法或填空答案。')
    if original.get('kind') == 'vocab_reading' and normal(value['prompt']) in accepted:
        raise ValueError('读法题的题干不能已经给出允许答案。')
    candidate = deepcopy(original); candidate.update(value); candidate['options'] = list(value['choices'])
    if original.get('practice'):
        from .learning_questions import validate_practice
        # A rubric edit cannot silently move its original measured span or
        # introduce new background dependencies. Reanchoring belongs to R2.
        validate_practice(candidate, target_id=original['target_id'])
    if is_pitch_question(original):
        if value['pitch_practice'] is None: raise ValueError('音高题必须保留完整结构化模型。')
        validate_snapshot(candidate)
    elif value['pitch_practice'] is not None:
        raise ValueError('不能把原题变为另一种结构化音高任务。')
    return value


def _can_order(pieces, answer, normal):
    """Bounded exact token composition; equal characters alone are not enough."""
    counts = Counter(normal(s) for s in pieces)
    if '' in counts or not counts or len(pieces) > 64: return False
    target = normal(answer)
    if Counter(''.join(normal(s) for s in pieces)) != Counter(target): return False
    tokens = sorted(counts); initial = tuple(counts[s] for s in tokens); seen = set()
    def visit(offset, remaining):
        key = (offset, remaining)
        if key in seen: return False
        if len(seen) >= 4096: raise ValueError('词块组合过于复杂，请简化后重新核对。')
        seen.add(key)
        if not any(remaining): return offset == len(target)
        for i, token in enumerate(tokens):
            if remaining[i] and target.startswith(token, offset):
                rest = list(remaining); rest[i] -= 1
                if visit(offset + len(token), tuple(rest)): return True
        return False
    return visit(0, initial)


def compile_row(snapshot, operations, claims, prior=None):
    """All editable fields are supported by the existing evidence workflow."""
    current = deepcopy(snapshot)
    for op in operations:
        claim = claims[op['binding_id']]
        if (op['field'] not in RESOURCE_FIELDS or claim['allowed_uses'] != ['rubric_review']
                or claim['candidate_sha256'] != digest(op['after'])
                or claim['source_sha256'] != op['owner']['source_sha256']):
            raise ValueError('整题修订缺少对应版本的完整核据。')
        current[op['field']] = op['after']
    if 'rubric' in {o['field'] for o in operations}:
        # A second answer edit must not contradict the reviewed whole bundle.
        payload = parse(current['rubric'], snapshot)
        if any(o['field'] in payload and payload[o['field']] != o['after'] for o in operations if o['field'] != 'rubric'):
            raise ValueError('整题稿与单独字段修订不一致，请作为同一份稿重新核对。')
    else:
        if snapshot.get('alternative_answers') or snapshot.get('accepted_answers', []) not in ([], [snapshot.get('answer')]):
            raise ValueError('请核对整题判题合同，明确保留或删除每个旧答案变体。')
        payload = bundle(current)
        payload['accepted_answers'] = [current['answer']]
        payload = parse(dump(payload), snapshot)
    baseline = deepcopy(prior['question_baseline'] if prior else snapshot)
    return {'question_baseline': baseline, 'question_bundle': payload,
            'question_scope': scope(baseline), 'target_id': baseline['target_id']}


def project(question, row, event):
    result = deepcopy(question)
    payload = parse(dump(row['question_bundle']), question)
    result.update(payload); result['options'] = list(payload['choices'])
    # Old token readings, audio hints and conditional-error annotations were
    # reviewed for a different rubric. Never inherit them as verified hints.
    result['optional_audio_text'] = ''
    support = deepcopy(result.get('support') or {})
    support.update(prompt=result['prompt'], segments=[], reading_safe=False, accent_safe=False, audio_safe=False)
    result['support'] = support; result['spans'] = []
    for key in ('content_dependencies', 'assessment', 'assessment_contract', 'rubric',
                'order_translation', 'condition_signature', 'error_attribution',
                'learning_conditions', 'learning_condition_source', 'learning_condition_revision'):
        result.pop(key, None)
    if result.get('guided'):
        introduction = str(question.get('guide') or '').rsplit('\n本题示范：', 1)[0]
        result['guide'] = introduction + '\n本题示范：' + payload['answer']
    result['personal_rubric'] = {'method': METHOD, 'version_id': event['id'],
        'owner_id': row['id'], 'scope': row['question_scope'], 'generation': event['generation']}
    return result


def grade(question, answer, normal):
    """Use the complete reviewed partitions, without legacy alias expansion."""
    from .kana_orthography import same_kana_sound
    accepted = {normal(s) for s in question['accepted_answers']}
    if answer in accepted:
        return {'status': 'correct', 'correct': True, 'scorable': True,
                'accepted_spelling': answer != normal(question['answer']), 'message': question['feedback']}
    if answer in {normal(s) for s in (*question.get('alternative_answers', []), *question.get('retired_ambiguous_answers', []))}:
        return {'status': 'unresolved', 'correct': None, 'scorable': False,
                'message': '这是其他语境的读法或尚有歧义的表达，本题不调整复习间隔。'}
    kana = [s for s in accepted if same_kana_sound(s, s)]
    if question['kind'] in {'vocab_reading', 'vocab_recall', 'vocab_orthography'} and any(
            same_kana_sound(s, answer, fold_script=True) for s in kana):
        reading = question['kind'] == 'vocab_reading'
        return {'status': 'unresolved' if reading else 'incorrect', 'correct': None if reading else False,
                'scorable': not reading, 'error_category': 'spelling', 'error_type': 'orthography',
                'reading_correct': True, 'spelling_correct': False, 'message': '读音对应，但不属于本词已核对的允许写法。'}
    if question.get('strict') or answer in {normal(s) for s in question.get('known_wrong', [])}:
        result = {'status': 'incorrect', 'correct': False, 'scorable': True, 'message': question['feedback']}
        if question['kind'] == 'vocab_reading': result.update(error_category='reading', reading_correct=False, spelling_correct=False)
        return result
    return {'status': 'unresolved', 'correct': None, 'scorable': False,
            'message': '这个表达尚未纳入本题核对范围，本次不调整复习间隔。'}


def rows_for(store, target_id):
    from .personal_content_versions import HEAD, PREFIX, validate, head_key
    from .personal_content_compensation import HOLD, PREFIX as WITHDRAWAL, hold_key
    heads = {k: json.loads(v) for k, v in store._db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (HEAD + '*',))}
    rows, seen = [], set()
    def add(event, pending=False):
        event = validate(event)
        for row in event['resources']:
            if row.get('kind') != 'question' or row['target_id'] != target_id: continue
            key = head_key(row['id'], 'question')
            if not pending and heads.get(key) != event['id']: continue
            marker = (row['id'], event['id'], pending)
            if marker not in seen:
                rows.append({'row': row, 'event': {'id': event['id'], 'generation': event['generation']}, 'pending': pending})
                seen.add(marker)
    for identity in set(heads.values()) - {None}: add(store.get_setting(PREFIX + identity, None))
    for key, raw in store._db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (HOLD + '*',)):
        identity = json.loads(raw)
        if not identity: continue
        withdrawal = store.get_setting(WITHDRAWAL + identity, None)
        event = store.get_setting(PREFIX + withdrawal['adoption_id'], None)
        for row in event['resources']:
            if row.get('kind') == 'question' and row['target_id'] == target_id and key == hold_key(head_key(row['id'], 'question')):
                add(event, pending=True); break
    return rows


def attach(store, item):
    result = deepcopy(item)
    result.pop('personal_question_versions', None)
    if result and store is not None and getattr(store, '_db', None):
        rows = rows_for(store, result.get('id'))
        if rows: result['personal_question_versions'] = rows
    return result


def validate_frozen_source(store, source):
    """A saved source may use a past version, but cannot invent its overlay."""
    from .personal_content_versions import PREFIX, validate
    values = source.get('personal_question_versions', [])
    if not isinstance(values, list) or len(values) > 128:
        raise ValueError('冻结题目来源版本过多或无效。')
    seen = set()
    for value in values:
        if (not isinstance(value, dict) or set(value) != {'row', 'event', 'pending'} or type(value['pending']) is not bool
                or not isinstance(value['event'], dict) or not isinstance(value['row'], dict)):
            raise ValueError('冻结题目缺少个人版本依据。')
        event = validate(store.get_setting(PREFIX + value['event'].get('id', ''), None))
        row = value['row']
        key = (row['id'], event['id'], value['pending'])
        if (value['event'] != {'id': event['id'], 'generation': event['generation']} or row not in event['resources']
                or row.get('kind') != 'question' or row['target_id'] != source.get('id') or key in seen):
            raise ValueError('冻结题目与采纳时核对的完整合同不同。')
        seen.add(key)


def apply(question, item):
    identity = scope(question)
    rows = [v for v in item.get('personal_question_versions', []) if v['row']['question_scope'] == identity]
    if not rows: return question
    if any(v['pending'] for v in rows) or len(rows) != 1:
        result = deepcopy(question); result['assessment_blocked_reason'] = '这道题的判题依据待核。'
        return result
    return project(question, rows[0]['row'], rows[0]['event'])


def review(store, source, question, *, require_input=True):
    """Recompute from active receipts; imported per-question flags grant nothing."""
    from .learning_questions import build_learning_question, rebuild_practice_question
    from .assessment_policy import contract_values
    from .answer_input import available, system_japanese_enabled
    source = attach(store, source)
    rows = source.get('personal_question_versions', [])
    if not rows: return {'blocked': True} if question.get('personal_rubric') else None
    try:
        raw_source = deepcopy(source); raw_source.pop('personal_question_versions', None)
        raw = (rebuild_practice_question(question, raw_source) if question.get('practice_source') else
               build_learning_question(raw_source, question['kind'], variant=question['variant'], guided=question['guided']))
        applicable = [v for v in rows if v['row']['question_scope'] == scope(raw)]
        if not applicable: return {'blocked': True} if question.get('personal_rubric') else None
        if len(applicable) != 1 or applicable[0]['pending']: return {'blocked': True}
        entry = applicable[0]; expected = project(raw, entry['row'], entry['event'])
        valid = (contract_values(expected) == contract_values(question)
                 and all(expected.get(k) == question.get(k) for k in ('personal_rubric', 'feedback', 'guide', 'spans'))
                 and (not require_input or available(question, system_japanese=system_japanese_enabled(store))))
        return {'blocked': not valid, 'rubric_qualification': deepcopy(expected['personal_rubric']) if valid else None}
    except (KeyError, TypeError, ValueError):
        return {'blocked': True}
