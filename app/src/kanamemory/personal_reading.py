"""Owner-bound reading corrections and narrowly derived question qualification.

A reviewed whole-sentence reading does not prove word boundaries or pitch.
Only the existing deterministic word factory can derive a new scoring contract;
authored templates and unreviewed alternative readings require separate review.
"""
import re

from .material_workflow import digest

READING_FIELDS = {'lesson': {'reading'}, 'example': {'reading'},
                  'lexical_example': {'reading'}, 'reference': {'hint.reading'}}
METHOD = 'verified-reading-local-factory-v1'


def validate_reading(kind, field, value):
    if field not in READING_FIELDS.get(kind, set()): return
    if kind == 'reference': return  # A scoped hint may include an inflection label.
    if (not isinstance(value, str) or not re.search('[ぁ-ゖァ-ヶ]', value)
            or not re.fullmatch(r'[ぁ-ゖァ-ヶー\s。、，,！？!?・「」『』（）()…]+', value)
            or kind == 'lesson' and not re.fullmatch('[ぁ-ゖァ-ヶー]+', value)):
        raise ValueError('读法应使用假名；说明、调号及汉字请放在各自字段。')


def project_reading(result, resource, event, *, held=False):
    kind = resource.get('kind', 'lesson')
    field = next(iter(READING_FIELDS.get(kind, ())), None)
    if field not in resource['fields']: return result
    if resource['fields'][field] == resource['base_fields'].get(field) and not held: return result
    result['personal_reading'] = {'version_id': event['id'], 'pending': bool(held),
                                  'scope': 'reference' if kind == 'reference' else 'word' if kind == 'lesson' else 'sentence'}
    return result


def presentation(resource):
    state = resource.get('personal_reading') or {}
    if not state: return {}
    return {'reading_override': None if state.get('pending') else resource.get('reading', ''),
            'reading_pending': bool(state.get('pending'))}


def spoken_text(resource):
    state = resource.get('personal_reading') or {}
    if state.get('pending'): return ''
    return (resource.get('reading') or '') if state else resource.get('text', '')


def annotation_exclusions(references):
    """A corrected local hint does not certify the package token annotation."""
    return [span for ref in references if ref and ref.get('personal_reading')
            for span in ref.get('spans', [])]


def reading_qualification(store, source, question, event):
    """Return None unless the complete current word contract is locally proven."""
    from .personal_content_versions import PREFIX
    from .assessment_policy import contract_values
    from .question_dependencies import KEY
    from .question_support import prompt_audio_text, prompt_support
    from .answer_input import available, system_japanese_enabled
    state = source.get('personal_reading') or {}
    if not state or state.get('pending'): return None
    if (source.get('family') != 'word' or source.get('question_templates') is not None
            or question.get('practice') or question.get('practice_source')
            or question.get('kind') not in {'vocab_reading', 'vocab_recall', 'vocab_audio_meaning', 'vocab_orthography',
                                            'vocab_audio_word', 'vocab_reading_choice'}
            or source.get('alternative_readings') or source.get('accepted_readings')
            or not available(question, system_japanese=system_japanese_enabled(store))):
        return None
    support = None; cursor = event; seen = set()
    while cursor and cursor['id'] not in seen:
        seen.add(cursor['id'])
        value = store.get_setting('feedback_field_support_v1:' + cursor['support_id'], None)
        claims = (value or {}).get('claims', [])
        if any(c['owner']['resource_kind'] == 'lesson' and c['owner']['resource_id'] == source['id']
               and c['field'] == 'reading' and c['candidate_sha256'] == digest(source['reading'])
               and c['status'] == 'supported' and c['allowed_uses'] == ['field_reference'] for c in claims):
            support = value; break
        from .personal_content_versions import head_key
        previous = cursor['previous_heads'].get(head_key(source['id']))
        cursor = store.get_setting(PREFIX + previous, None) if previous else None
    if not support: return None
    mode = question.get('input_mode'); choices = question.get('choices', [])
    if mode == 'choice' and (len(choices) < 2 or len(set(choices)) != len(choices) or question['answer'] not in choices):
        return None
    kind = question['kind']; reading = source['reading']
    if kind == 'vocab_reading':
        if (question['answer'] != reading or question['accepted_answers'] != [reading]
                or question.get('alternative_answers') or question.get('optional_audio_text')
                or not question.get('guided') and (prompt_audio_text(question) or prompt_support(question)['furigana_visible'])):
            return None
    elif kind == 'vocab_recall':
        if question['answer'] != source['title'] or reading not in question['accepted_answers']: return None
    elif kind == 'vocab_audio_meaning':
        if question['audio_text'] != reading or question['answer'] != source['meaning']: return None
    elif kind == 'vocab_audio_word':
        if question['audio_text'] != reading or question['answer'] != source['title']: return None
    elif kind == 'vocab_reading_choice':
        if question['answer'] != reading or question['prompt'] != source['title']: return None
    elif question['answer'] != source['title'] or reading not in question['prompt']:
        return None
    return {'version': 1, 'method': METHOD, 'personal_revision': event['id'], 'generation': event['generation'],
            'field_support_id': support['id'], 'field_support_sha256': digest(support),
            'knowledge_id': source['id'], 'base_revision': source['revision'], 'ability': question['ability'],
            'reading_sha256': digest(reading), 'kind': kind, 'input_mode': mode,
            'contract_sha256': digest(contract_values(question)), 'dependencies_sha256': digest(question.get(KEY)),
            'factory': 'word-fields-v1', 'variant': question['variant'], 'guided': question['guided']}


def requires_reading_contract(source, question):
    if not source.get('personal_reading'): return False
    if question.get('kind') in {'vocab_reading', 'vocab_recall', 'vocab_audio_meaning', 'vocab_orthography',
                                'vocab_audio_word', 'vocab_reading_choice'}: return True
    return any(e['field'] == 'reading' and any(u['role'] in {'rubric', 'stimulus'} for u in e['uses'])
               for e in question.get('content_dependencies', {}).get('entries', []))
