"""App-authored positive field uses. Missing declarations never prove no use.

These are provenance for impact review, not truth or assessment certificates.
Only new factory questions receive them; historical snapshots are not rebuilt.
"""
from copy import deepcopy

from .feedback_bindings import field_value
from .material_workflow import digest
from .retention_ai import _hash

KEY = 'content_dependencies'
FACTORY = 'learning-fields-v1'
ROLES = {'prompt': 'stimulus', 'task': 'stimulus', 'audio_text': 'stimulus', 'choices': 'stimulus',
         'options': 'stimulus', 'answer': 'rubric', 'accepted_answers': 'rubric', 'alternative_answers': 'rubric',
         'feedback': 'after_answer', 'guide': 'hint', 'support': 'optional_hint', 'spans': 'optional_hint',
         'optional_audio_text': 'optional_hint'}


def attach(question, item, *, templates):
    """Called before display overlays, so replaced output hashes do not match.

Template text has its own practice_source. We do not infer that its answer was
copied from a lesson just because the strings happen to be equal.
"""
    uses = {}
    def add(field, *slots):
        if field_value(item, field) is None: return
        uses.setdefault(field, set()).update(s for s in slots if question.get(s) not in (None, '', [], {}))
    if question.get('guided'):
        add('instruction.rule', 'guide'); add('instruction.example', 'guide')
    if item['family'] == 'word' and not templates:
        kind = question['kind']
        add('meaning', 'feedback')
        if kind in {'vocab_meaning', 'vocab_audio_meaning', 'reading_comprehension', 'listening_comprehension'}:
            add('meaning', 'answer', 'accepted_answers', 'choices', 'options')
        if kind in {'vocab_meaning', 'vocab_reading', 'vocab_zh_ja_contrast'}: add('title', 'prompt')
        if kind == 'vocab_audio_meaning': add('reading', 'audio_text')
        elif kind == 'vocab_reading':
            add('meaning', 'task'); add('reading', 'answer', 'accepted_answers')
            add('alternative_readings', 'alternative_answers'); add('accepted_readings', 'alternative_answers')
        elif kind == 'vocab_recall':
            add('recall', 'prompt'); add('title', 'answer', 'accepted_answers'); add('reading', 'accepted_answers')
        elif kind == 'vocab_orthography':
            add('reading', 'prompt'); add('meaning', 'prompt'); add('title', 'answer', 'accepted_answers', 'choices', 'options')
        elif kind == 'vocab_usage':
            uses.pop('meaning', None); add('cloze', 'prompt', 'task'); add('instruction.rule', 'feedback')
        elif kind == 'vocab_zh_ja_contrast':
            uses.pop('meaning', None); add('instruction.contrast', 'feedback')
        elif kind in {'reading_comprehension', 'listening_comprehension'}:
            uses.get('meaning', set()).discard('feedback')
            add('instruction.translation', 'feedback')
            add('instruction.example', 'audio_text' if kind == 'listening_comprehension' else 'prompt')
            if kind == 'reading_comprehension': add('title', 'task')
        if kind == 'vocab_meaning' and item.get('dictionary_only'): add('accepted_meanings', 'accepted_answers')
        if question.get('guided') and kind == 'vocab_meaning':
            # The factory replaces the ordinary word prompt with the example,
            # or a title/reading/meaning demonstration when it equals the title.
            uses.get('title', set()).discard('prompt')
            if item['instruction']['example'] == item['title']:
                for field in ('title', 'reading', 'meaning'): add(field, 'prompt')
            else: add('instruction.example', 'prompt')
        if question.get('guided') and kind in {'vocab_reading', 'vocab_recall'}:
            uses.get('meaning', set()).discard('task')
            # Choice guide options are produced from the exact answer.
            for field, slots in list(uses.items()):
                if 'answer' in slots: add(field, 'choices', 'options')
        if question.get('guided'):
            for field, slots in list(uses.items()):
                if 'answer' in slots: add(field, 'guide')
    # Only actual target-word support is declared. Other annotation providers
    # and background sources remain untraced rather than borrowing this ID.
    if item['family'] == 'word' and not question.get('background_support_version') and any(s.get('surface') == item['title'] and s.get('reading') == item['reading']
            for s in question.get('support', {}).get('segments', [])):
        add('title', 'support', 'spans'); add('reading', 'support', 'spans')
    if question.get('optional_audio_text') == question.get('prompt'):
        for field, slots in list(uses.items()):
            if 'prompt' in slots: add(field, 'optional_audio_text')
    entries = [{'resource_kind': 'lesson', 'resource_id': item['id'], 'resource_revision': str(item['revision']),
        'field': field, 'value_sha256': digest(field_value(item, field)),
        'uses': [{'slot': slot, 'role': ROLES[slot], 'rendered_sha256': digest(question[slot])} for slot in sorted(slots)]}
        for field, slots in sorted(uses.items()) if slots]
    value = {'version': 1, 'factory': FACTORY, 'coverage': 'positive_uses_only', 'entries': entries}
    value['sha256'] = digest(value)
    question[KEY] = validate(value)
    if item.get('personal_revision'):
        question['personal_content_revision'] = item['personal_revision']
    return question


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'factory', 'coverage', 'entries', 'sha256'}
            or type(value['version']) is not int or value['version'] != 1 or value['factory'] != FACTORY
            or value['coverage'] != 'positive_uses_only' or not isinstance(value['entries'], list) or len(value['entries']) > 24
            or value['sha256'] != digest({k: v for k, v in value.items() if k != 'sha256'})):
        raise ValueError('题目字段用途声明无效。')
    seen = set()
    for e in value['entries']:
        if (not isinstance(e, dict) or set(e) != {'resource_kind', 'resource_id', 'resource_revision', 'field', 'value_sha256', 'uses'}
                or e['resource_kind'] != 'lesson' or any(not isinstance(e[k], str) or not 0 < len(e[k]) <= 1024 for k in ('resource_id', 'resource_revision', 'field'))
                or not _hash(e['value_sha256']) or not isinstance(e['uses'], list) or not 1 <= len(e['uses']) <= len(ROLES)):
            raise ValueError('题目来源字段无效。')
        key = (e['resource_kind'], e['resource_id'], e['resource_revision'], e['field'])
        if key in seen: raise ValueError('题目来源字段重复。')
        seen.add(key); slots = set()
        for use in e['uses']:
            if (not isinstance(use, dict) or set(use) != {'slot', 'role', 'rendered_sha256'} or not isinstance(use['slot'], str)
                    or use['slot'] not in ROLES or use['slot'] in slots or use['role'] != ROLES[use['slot']] or not _hash(use['rendered_sha256'])):
                raise ValueError('题目字段用途或呈现版本不一致。')
            slots.add(use['slot'])
    return deepcopy(value)


def factory_matches(question, source):
    """Self-supplied hashes are not proof that an old/imported task used a field.

Reconstruct only the exact local factory contract, never a similar question or
the first current template. Unavailable historical sources remain unproven.
"""
    if not isinstance(source, dict) or source.get('ai_generated') or question.get('ai_generated'): return False
    if (question.get('target_id') != source.get('id') or str(question.get('revision')) != str(source.get('revision'))
            or type(question.get('variant')) is not int or type(question.get('guided')) is not bool): return False
    from .learning_questions import build_learning_question, rebuild_practice_question
    from .material_review import reviewed_question
    try:
        rebuilt = (rebuild_practice_question(question, source) if question.get('practice_source') else
                   build_learning_question(source, question['kind'], variant=question['variant'], guided=question['guided'], apply_review=False))
    except (ValueError, KeyError, TypeError): return False
    if question.get('tutor_practice') is True:
        # local_question wraps an unchanged factory task for a non-FSRS lane.
        # Reproduce only that exact wrapper; all stimulus/rubric/hint fields and
        # their source hashes must still match below.
        if (question.get('scheduled') is not False
                or question.get('evidence_mode') != 'exposure_only'
                or question.get('guided') is not False):
            return False
        rebuilt.update(tutor_practice=True, evidence_mode='exposure_only', scheduled=False)
    fields = (*ROLES, 'family', 'ability', 'input_mode', 'strict', 'known_wrong', 'practice', 'practice_source',
              'pitch_practice', 'tests_reading', 'tests_pitch', 'text_roles', 'evidence_mode', 'requires_audio')
    return any(candidate.get(KEY) == question.get(KEY) and all(candidate.get(k) == question.get(k) for k in fields)
               for candidate in (rebuilt, reviewed_question(rebuilt)))


def matching_uses(question, binding, source=None):
    value = question.get(KEY)
    if value is None: return []
    value = validate(value)
    if not any((e['resource_kind'], e['resource_id'], e['resource_revision'], e['field'], e['value_sha256']) == (
            binding['resource_kind'], binding['resource_id'], binding['resource_revision'], binding['field'], digest(binding['value']))
            for e in value['entries']): return []
    if not factory_matches(question, source): return []
    roles = set()
    for e in value['entries']:
        if (e['resource_kind'], e['resource_id'], e['resource_revision'], e['field'], e['value_sha256']) != (
                binding['resource_kind'], binding['resource_id'], binding['resource_revision'], binding['field'], digest(binding['value'])):
            continue
        # A presented correction must not inherit the prior output's uses.
        for use in e['uses']:
            if use['slot'] in question and digest(question[use['slot']]) == use['rendered_sha256']: roles.add(use['role'])
    return sorted(roles)
