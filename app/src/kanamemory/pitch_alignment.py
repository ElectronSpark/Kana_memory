"""Reviewed pitch beats assigned to whole written units, without learner writes.

Source offsets, reading and morae are separate. The public edit view contains
only source text and entered beats; its size never reveals an expected count.
Version 3 does not turn source-unit confirmation into a grammar segmentation
test or infer that a correct pitch response proves a word's reading is known.
"""
from copy import deepcopy
import json

from .content_pack import load_namespace
from . import pitch_sequence as sequence

PREFIX = 'pp3:'
MAX_UNIT_BEATS = 32
MAX_TOTAL_BEATS = 128
MAX_UNITS = 32
_FIELDS = {'version', 'stimulus_id', 'text', 'source_units', 'contexts',
           'reviewed_patterns', 'selected_pattern_id', 'accepted_pattern_ids',
           'representation', 'task_mode', 'review'}
semantic_fingerprint = sequence.semantic_fingerprint


def _validate(spec):
    if (not isinstance(spec, dict) or set(spec) != _FIELDS
            or type(spec['version']) is not int or spec['version'] != 3
            or spec['task_mode'] != 'source_alignment'
            or spec['representation'] != 'mora_plateau_hl'):
        raise ValueError('Unsupported source-pitch specification')
    sequence._text(spec['stimulus_id'], 'stimulus ID', 160)
    text = sequence._text(spec['text'], 'original text', 512)
    units = spec['source_units']
    sequence._spans(units, len(text), MAX_UNITS, 'source units',
                    extra=('id', 'role', 'reading', 'morae'), cover=True)
    ids, total, speech = set(), 0, 0
    for unit in units:
        identity = sequence._text(unit['id'], 'source unit ID', 128)
        if identity in ids or unit['role'] not in {'word', 'particle', 'auxiliary', 'punctuation'}:
            raise ValueError('Invalid source unit identity')
        ids.add(identity)
        surface = text[unit['start']:unit['end']]
        if unit['role'] == 'punctuation':
            if any(c not in sequence._PUNCT for c in surface) or unit['reading'] != '' or unit['morae'] != []:
                raise ValueError('Punctuation cannot carry spoken beats')
            continue
        if any(c in sequence._PUNCT for c in surface):
            raise ValueError('Source word mixes punctuation and speech')
        reading = sequence._text(unit['reading'], 'reviewed reading', 64)
        morae = sequence._list(unit['morae'], MAX_UNIT_BEATS, 'reviewed unit morae')
        if not sequence._KANA.fullmatch(reading):
            raise ValueError('A reviewed reading requires kana')
        for mora in morae:
            if (not isinstance(mora, str) or not sequence._KANA.fullmatch(mora)
                    or len(mora) > 2 or mora[0] in sequence._SMALL
                    or len(mora) == 2 and (mora[0] in sequence._SPECIAL or mora[1] not in sequence._SMALL)):
                raise ValueError('Invalid reviewed source-unit mora')
        if ''.join(morae) != reading:
            raise ValueError('Source-unit reading and morae disagree')
        total += len(morae)
        speech += 1
    if not speech or total > MAX_TOTAL_BEATS:
        raise ValueError('Unsupported source-pitch length')
    contexts, refs = {}, set()
    for context in sequence._list(spec['contexts'], 8, 'contexts'):
        if not isinstance(context, dict) or set(context) != {'id', 'prompt_zh', 'source_refs'}:
            raise ValueError('Invalid source-pitch context')
        identity = sequence._text(context['id'], 'context ID', 128)
        if identity in contexts:
            raise ValueError('Duplicate source-pitch context')
        sequence._text(context['prompt_zh'], 'context prompt', 600)
        refs.update(sequence._refs(context['source_refs']))
        contexts[identity] = context
    patterns = {}
    for pattern in sequence._list(spec['reviewed_patterns'], 8, 'patterns'):
        if not isinstance(pattern, dict) or set(pattern) != {'id', 'context_id', 'levels', 'source_refs'}:
            raise ValueError('Invalid reviewed source-pitch pattern')
        identity = sequence._text(pattern['id'], 'pattern ID', 128)
        if identity in patterns or pattern['context_id'] not in contexts:
            raise ValueError('Unknown or duplicate source-pitch context')
        if (not isinstance(pattern['levels'], list) or len(pattern['levels']) != total
                or any(level not in ('H', 'L') for level in pattern['levels'])):
            raise ValueError('Reviewed pattern must supply every mora level')
        refs.update(sequence._refs(pattern['source_refs']))
        patterns[identity] = pattern
    selected = spec['selected_pattern_id']
    accepted = sequence._refs(spec['accepted_pattern_ids'])
    if selected not in patterns or selected not in accepted or not accepted <= set(patterns):
        raise ValueError('Unknown accepted source-pitch pattern')
    if any(patterns[key]['context_id'] != patterns[selected]['context_id'] for key in accepted):
        raise ValueError('Different contexts cannot share source-pitch answers')
    review = spec['review']
    if (not isinstance(review, dict) or set(review) != {'review_id', 'content_sha256', 'source_refs', 'status'}
            or review['status'] != 'approved'):
        raise ValueError('Source-pitch specification lacks a review')
    sequence._text(review['review_id'], 'review ID', 160)
    refs.update(sequence._refs(review['source_refs']))
    if review['content_sha256'] != semantic_fingerprint(spec):
        raise ValueError('Source-pitch review fingerprint mismatch')
    return refs


def specification(question):
    try:
        if not isinstance(question, dict) or question.get('input_mode') != 'pitch_fill':
            raise ValueError('Source-pitch alignment requires pitch composition')
        spec = question.get('pitch_practice')
        refs = _validate(spec)
        registry = load_namespace('pitch_sequences', {})
        if (not isinstance(registry, dict) or registry.get('format') != 'kanamemory-pitch-sequences'
                or type(registry.get('version')) is not int or registry['version'] != 1
                or not isinstance(registry.get('sources'), list) or not isinstance(registry.get('records'), list)):
            raise ValueError('No trusted pitch registry is installed')
        sources = set()
        for source in registry['sources']:
            if not isinstance(source, dict):
                raise ValueError('Invalid registered source')
            identity = sequence._text(source.get('id'), 'registered source ID', 128)
            sequence._text(source.get('title'), 'registered source title', 600)
            sequence._text(source.get('url'), 'registered source URL', 1024)
            if identity in sources:
                raise ValueError('Duplicate registered pitch source')
            sources.add(identity)
        matches = [record for record in registry['records'] if isinstance(record, dict)
                   and record.get('stimulus_id') == spec['stimulus_id']
                   and isinstance(record.get('review'), dict)
                   and record['review'].get('content_sha256') == spec['review']['content_sha256']]
        if not refs <= sources or len(matches) != 1 or matches[0] != spec:
            raise ValueError('Source-pitch version is not in the trusted registry')
        return deepcopy(spec)
    except (TypeError, KeyError, RecursionError, UnicodeError) as error:
        raise ValueError('Invalid reviewed source-pitch data') from error


def _pattern(spec, identity=None):
    return next(row for row in spec['reviewed_patterns'] if row['id'] == (identity or spec['selected_pattern_id']))


def _context(spec):
    return _pattern(spec)['context_id']


def _empty_units(spec):
    return [{'id': u['id'], 'levels': [], 'complete': u['role'] == 'punctuation'} for u in spec['source_units']]


def _reference_units(spec, identity=None):
    values, offset = _empty_units(spec), 0
    levels = _pattern(spec, identity)['levels']
    for unit, value in zip(spec['source_units'], values):
        count = len(unit['morae'])
        value.update(levels=levels[offset:offset + count], complete=True)
        offset += count
    return values


def _encode(spec, units):
    return PREFIX + sequence._json({'mode': 'pitch_fill', 'spec_sha256': spec['review']['content_sha256'], 'units': units})


def canonical_answer(question):
    spec = specification(question)
    return _encode(spec, _reference_units(spec))


def public_task(question):
    spec = specification(question)
    context = next(c['prompt_zh'] for c in spec['contexts'] if c['id'] == _context(spec))
    # Reading is a given condition of this beat-allocation task, not its target.
    # Keep it continuous: no answer-bearing mora slots, boundaries or levels.
    reading = ''.join(spec['text'][unit['start']:unit['end']] if unit['role'] == 'punctuation'
                      else unit['reading'] for unit in spec['source_units'])
    return context + '\n参考读法：' + reading + '\n每词填拍，│确认并到下一词。'


def validate_snapshot(question):
    spec = specification(question)
    expected = canonical_answer(question)
    if (question.get('answer') != expected or question.get('accepted_answers', [expected]) != [expected]
            or question.get('choices', []) or question.get('options', [])
            or question.get('domain') != 'pronunciation' or question.get('family') != 'grammar'
            or question.get('kind') not in ('pronunciation_rule', 'pronunciation_application')
            or question.get('ability') != ('understanding' if question.get('kind') == 'pronunciation_rule' else 'application')
            or question.get('audio_text') or question.get('optional_audio_text') or question.get('requires_audio')
            or question.get('prompt') != spec['text'] or question.get('task') != public_task(question)):
        raise ValueError('Source-pitch snapshot disagrees with its reviewed stimulus')


def _initial_state(spec):
    return {'version': 3, 'stimulus_id': spec['stimulus_id'], 'spec_sha256': spec['review']['content_sha256'],
            'context_id': _context(spec), 'actions': []}


def initial_draft(question):
    return {'pitch_state': _initial_state(specification(question)), 'answer': ''}


def _value(spec, units):
    cursor, total = None, 0
    for index, (source, unit) in enumerate(zip(spec['source_units'], units)):
        if source['role'] == 'punctuation':
            continue
        total += len(unit['levels'])
        if cursor is None and not unit['complete']:
            cursor = index
    complete = cursor is None
    return {'units': units, 'cursor': cursor, 'complete': complete, 'can_merge': False,
            'can_input': not complete and total < MAX_TOTAL_BEATS and len(units[cursor]['levels']) < MAX_UNIT_BEATS,
            'can_split': not complete and bool(units[cursor]['levels'])}


def _replay(spec, actions):
    if not isinstance(actions, list) or len(actions) > MAX_TOTAL_BEATS + MAX_UNITS:
        raise ValueError('Invalid source-pitch action count')
    units = _empty_units(spec)
    for action in actions:
        value = _value(spec, units)
        if action in ('H', 'L') and value['can_input']:
            units[value['cursor']]['levels'].append(action)
        elif action == 'split' and value['can_split']:
            units[value['cursor']]['complete'] = True
        else:
            raise ValueError('This source-pitch action is not available')
    return _value(spec, units)


def _state(question, draft):
    spec = specification(question)
    if not isinstance(draft, dict):
        raise ValueError('Invalid source-pitch draft')
    state = draft['pitch_state'] if 'pitch_state' in draft else _initial_state(spec)
    if (not isinstance(state, dict) or set(state) != {'version', 'stimulus_id', 'spec_sha256', 'context_id', 'actions'}
            or type(state['version']) is not int or state['version'] != 3
            or state['stimulus_id'] != spec['stimulus_id'] or state['spec_sha256'] != spec['review']['content_sha256']
            or state['context_id'] != _context(spec)):
        raise ValueError('Source-pitch draft belongs to another stimulus')
    value = _replay(spec, state['actions'])
    answer = _encode(spec, value['units']) if state['actions'] else ''
    if draft.get('answer', '') != answer:
        raise ValueError('Source-pitch draft does not match its actions')
    return spec, deepcopy(state), value, answer


def validate_draft(question, draft):
    _state(question, draft)


def editor_draft(question, draft):
    validate_draft(question, draft)
    return deepcopy(draft)


def editor_action(question, draft, action):
    spec, state, _, _ = _state(question, draft)
    if action == 'undo':
        state['actions'] = state['actions'][:-1]
    elif action == 'clear':
        state['actions'] = []
    else:
        state['actions'].append(action)
    value = _replay(spec, state['actions'])
    answer = _encode(spec, value['units']) if state['actions'] else ''
    return {**deepcopy(draft), 'pitch_state': state, 'answer': answer}


def _decode(spec, answer):
    if not isinstance(answer, str) or not answer.startswith(PREFIX) or len(answer) > 32768:
        raise ValueError('Invalid source-pitch answer')
    try:
        value = json.loads(answer[len(PREFIX):])
        if PREFIX + sequence._json(value) != answer:
            raise ValueError('Noncanonical or duplicate source-pitch answer fields')
    except (ValueError, RecursionError, TypeError) as error:
        raise ValueError('Invalid source-pitch answer') from error
    if (not isinstance(value, dict) or set(value) != {'mode', 'spec_sha256', 'units'}
            or value['mode'] != 'pitch_fill' or value['spec_sha256'] != spec['review']['content_sha256']
            or not isinstance(value['units'], list) or len(value['units']) != len(spec['source_units'])):
        raise ValueError('Source-pitch answer belongs to another stimulus')
    pending, total = False, 0
    for source, unit in zip(spec['source_units'], value['units']):
        if (not isinstance(unit, dict) or set(unit) != {'id', 'levels', 'complete'} or unit['id'] != source['id']
                or type(unit['complete']) is not bool or not isinstance(unit['levels'], list)
                or len(unit['levels']) > MAX_UNIT_BEATS or any(v not in ('H', 'L') for v in unit['levels'])):
            raise ValueError('Invalid source-pitch unit response')
        if source['role'] == 'punctuation':
            if unit['levels'] or not unit['complete']:
                raise ValueError('Punctuation must not carry an answer')
            continue
        if (pending and (unit['complete'] or unit['levels'])) or unit['complete'] and not unit['levels']:
            raise ValueError('Source-pitch answer skips or reorders a unit')
        pending = pending or not unit['complete']
        total += len(unit['levels'])
    if total > MAX_TOTAL_BEATS:
        raise ValueError('Source-pitch answer is too long')
    return _value(spec, value['units'])


def fixed_view(question, draft=None, *, answer=None, reveal=False):
    if draft is not None and answer is not None:
        raise ValueError('Choose a draft or a frozen response')
    spec = specification(question)
    value = _value(spec, _empty_units(spec))
    if draft is not None:
        _, _, value, _ = _state(question, draft)
    elif answer:
        value = _decode(spec, answer)
    if reveal:
        value = _value(spec, _reference_units(spec))
    units = [{'id': source['id'], 'text': spec['text'][source['start']:source['end']],
              'start': source['start'], 'end': source['end'], 'role': source['role'],
              'levels': list(entered['levels']), 'complete': entered['complete'],
              'active': value['cursor'] == index and not reveal}
             for index, (source, entered) in enumerate(zip(spec['source_units'], value['units']))]
    return {'version': 3, 'surface': {'text': spec['text'], 'units': units},
            'cursor': value['cursor'], 'complete': value['complete'], 'label': spec['text']}


def display_data(question, answer=None, *, reveal=False):
    return fixed_view(question, answer=answer, reveal=reveal)


def state_view(question, draft):
    _, state, value, answer = _state(question, draft)
    return {**value, 'answer': answer, 'can_undo': bool(state['actions']),
            'display': fixed_view(question, draft), 'task_mode': 'source_alignment', 'version': 3}


def grade_answer(question, answer):
    validate_snapshot(question)
    if answer is None or answer == '':
        return {'status': 'empty', 'correct': None, 'scorable': False, 'message': '请先作答。'}
    spec = specification(question)
    try:
        value = _decode(spec, answer)
    except ValueError:
        return {'status': 'invalid', 'correct': None, 'scorable': False, 'message': '这份音拍作答无法读取，请重新作答。'}
    if not value['complete']:
        return {'status': 'empty', 'correct': None, 'scorable': False, 'message': '每词填完后用│确认，最后一词也要确认。'}
    for index, (source, entered) in enumerate(zip(spec['source_units'], value['units'])):
        if len(entered['levels']) != len(source['morae']):
            text = spec['text'][source['start']:source['end']]
            return {'status': 'incorrect', 'correct': False, 'scorable': True, 'pitch_errors': ['morae'],
                    'source_unit_id': source['id'], 'source_unit_index': index,
                    'message': f'先检查「{text}」的拍数：本课读法为 {len(source["morae"])} 拍，你填了 {len(entered["levels"])} 拍。'}
    levels = [v for unit in value['units'] for v in unit['levels']]
    patterns = [_pattern(spec, identity) for identity in spec['accepted_pattern_ids']]
    for pattern in patterns:
        if pattern['levels'] == levels:
            return {'status': 'correct', 'correct': True, 'scorable': True,
                    'matched_pattern_id': pattern['id'], 'message': question.get('feedback', '')}
    expected = min(patterns, key=lambda p: sum(a != b for a, b in zip(levels, p['levels'])))
    error = next(i for i, (actual, wanted) in enumerate(zip(levels, expected['levels'])) if actual != wanted)
    offset = 0
    for index, unit in enumerate(spec['source_units']):
        if error < offset + len(unit['morae']):
            text = spec['text'][unit['start']:unit['end']]
            return {'status': 'incorrect', 'correct': False, 'scorable': True, 'pitch_errors': ['levels'],
                    'source_unit_id': unit['id'], 'source_unit_index': index, 'beat_index': error - offset,
                    'message': f'按本课参考读法，「{text}」第 {error - offset + 1} 拍应标' + ('高。' if expected['levels'][error] == 'H' else '低。')}
        offset += len(unit['morae'])
    raise ValueError('Reviewed pitch response has no matching beat')


def answer_label(question, answer=None):
    spec = specification(question)
    value = _decode(spec, canonical_answer(question) if answer is None else answer)
    return ' ／ '.join(spec['text'][source['start']:source['end']] + '：' + ''.join('▔' if v == 'H' else '▁' for v in unit['levels'])
                      for source, unit in zip(spec['source_units'], value['units']) if source['role'] != 'punctuation')
