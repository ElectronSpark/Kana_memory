"""Compile anchored grammar application tasks without inventing Japanese.

The private authoring table pins reviewed examples, their lesson-specific
references, and whole-word chunk boundaries prepared offline. This compiler
needs neither a morphological analyzer nor learner state. Text alternatives
and alternative word orders are conservative: unattested responses stay
unresolved instead of being declared grammatical errors.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTHORING = ROOT / 'data/content_authoring/grammar_practice_0281.json'
METHOD = 'exact_reviewed_grammar_application_reuse'
GAP = '（　）'


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _anchored(text, span):
    return (isinstance(span, dict) and set(span) == {'start', 'end', 'quote'}
            and type(span['start']) is int and type(span['end']) is int
            and 0 <= span['start'] < span['end'] <= len(text)
            and text[span['start']:span['end']] == span['quote'])


def _validated_row(row, item, example, reference):
    from kanamemory.material_review import item_fingerprint

    if (row.get('item_fingerprint') != item_fingerprint(item)
            or row.get('example_sha256') != _digest(example)
            or row.get('reference_sha256') != _digest(reference)
            or row.get('text') != example['text']
            or row.get('translation') != example['translation']
            or reference['target_id'] != item['id']
            or reference['example_id'] != example['id']
            or reference.get('example_revision') != example['revision']):
        raise ValueError('Grammar application source no longer matches its reviewed binding')
    text, gap = example['text'], row.get('gap_span')
    focus = row.get('source_focus_span')
    if (not _anchored(text, gap) or not _anchored(text, focus)
            or focus not in reference.get('spans', [])
            or not all(_anchored(text, s) for s in reference['spans'])
            or GAP in text or not 1 <= len(gap['quote']) <= 22
            or not reference.get('hint', {}).get('rule')):
        raise ValueError('Grammar completion needs an exact reviewed focus')
    chunks, boundaries = row.get('chunks'), row.get('token_boundaries')
    if (not isinstance(chunks, list) or not 2 <= len(chunks) <= 6
            or any(not isinstance(c, str) or len(c) < 2 for c in chunks)
            or ''.join(chunks) != text or len(set(chunks)) != len(chunks)
            or not isinstance(boundaries, list) or not boundaries
            or any(type(v) is not int for v in boundaries)
            or boundaries != sorted(set(boundaries))
            or boundaries[0] != 0 or boundaries[-1] != len(text)):
        raise ValueError('Reconstruction requires bounded, exact, whole-word chunks')
    # Reviewed references sometimes highlight an inflectional stem. The authored
    # gap must expand only as far as the adjacent contextual token boundaries;
    # asking for a truncated stem is not a useful sentence-completion task.
    end = min(b for b in boundaries if b >= focus['end'])
    suffix = row.get('auxiliary_suffix', [])
    if not isinstance(suffix, list):
        raise ValueError('Completion auxiliaries require explicit contextual spans')
    for token in suffix:
        span = token.get('span') if isinstance(token, dict) else None
        if (not _anchored(text, span) or token.get('part_of_speech') != '助動詞'
                or span['start'] != end or span['end'] not in boundaries):
            raise ValueError('Completion auxiliaries must continue the exact focused word')
        end = span['end']
    if (gap['start'] != max(b for b in boundaries if b <= focus['start'])
            or gap['end'] != end):
        raise ValueError('Completion must use the minimal whole-token reviewed focus')
    position = 0
    for chunk in chunks[:-1]:
        position += len(chunk)
        if (position not in boundaries
                or any(s['start'] < position < s['end'] for s in reference['spans'])):
            raise ValueError('A reconstruction chunk splits a word or the reviewed grammar expression')
    answers = row.get('accepted_answers')
    if (not isinstance(answers, list) or not answers or answers[0] != gap['quote']
            or any(not isinstance(a, str) or not a.strip() for a in answers)
            or len(set(answers)) != len(answers) or len(answers) > 2):
        raise ValueError('Completion answers must be explicitly pinned in authoring')
    if len(answers) == 2:
        evidence = row.get('kana_evidence', {})
        if (evidence.get('method') != 'context_analyzer_agreement'
                or evidence.get('reading') != answers[1]
                or not isinstance(evidence.get('annotation_sha256'), str)
                or len(evidence['annotation_sha256']) != 64):
            raise ValueError('Additional kana acceptance needs exact contextual evidence')
    return chunks, answers


def _background_glosses(prompt, example, gap, references, by_id):
    """Only exact reviewed word-sense anchors outside the answer survive."""
    result = []
    for ref in references:
        word = by_id.get(ref['target_id'])
        if (not word or word.get('family') != 'word'
                or ref.get('example_revision') != example['revision']):
            continue
        for span in ref.get('spans', []):
            if (not _anchored(example['text'], span)
                    or span['quote'] != word.get('title')
                    or span['start'] < gap['end'] and span['end'] > gap['start']):
                continue
            shift = len(GAP) - len(gap['quote']) if span['start'] >= gap['end'] else 0
            start, end = span['start'] + shift, span['end'] + shift
            if any(start < s['end'] and end > s['start'] for s in result):
                continue
            if prompt[start:end] != span['quote']:
                raise ValueError('A background word moved outside its public anchor')
            result.append({'start': start, 'end': end, 'surface': span['quote'],
                           'meaning': word['meaning'], 'reading': word['reading'],
                           'target_id': word['id']})
    return sorted(result, key=lambda row: row['start'])


def build_grammar_expansion(items, pack, existing, *, authoring=None):
    """Return additions only; the owner appends them after all issued variants.

    Unknown/custom target IDs are skipped, never assigned a default lesson.
    Changed known bindings fail closed and require an updated content review.
    """
    from kanamemory.learning_questions import validate_practice
    from kanamemory.material_review import item_fingerprint

    if authoring is None:
        authoring = json.loads(AUTHORING.read_text('utf8'))
    if (authoring.get('schema_version') != 1
            or authoring.get('review_status') != 'anchored_transform_reviewed'
            or not isinstance(authoring.get('rows'), list)):
        raise ValueError('Unsupported grammar application authoring')
    by_id = {item['id']: item for item in items}
    examples = {example['id']: example for example in pack['examples']}
    references = {ref['id']: ref for ref in pack['references']}
    by_example = defaultdict(list)
    for ref in pack['references']:
        by_example[ref['example_id']].append(ref)
    output = {'schema_version': 1, 'revision': authoring['revision'],
              'items': {}, 'provenance': []}
    contexts, statistics = defaultdict(set), Counter()
    for row in authoring['rows']:
        item = by_id.get(row['target_id'])
        if (not item or item.get('family') != 'grammar' or item.get('domain') == 'pronunciation'
                or 'application' not in item.get('abilities', [])):
            continue
        example = examples.get(row['example_id'])
        reference = references.get(row['reference_id'])
        if example is None or reference is None:
            raise ValueError('Reviewed grammar source missing from the supplied catalogue')
        chunks, accepted = _validated_row(row, item, example, reference)
        text, gap = example['text'], row['gap_span']
        if text in contexts[item['id']]:
            raise ValueError('Repeated source sentence is not a new grammar context')
        contexts[item['id']].add(text)
        backgrounds = sorted({ref['target_id'] for ref in by_example[example['id']]
                              if ref['target_id'] in by_id and ref['target_id'] != item['id']})
        if len(backgrounds) > 40:
            raise ValueError('Grammar context exceeds the bounded prerequisite contract')
        stem = text[:gap['start']] + GAP + text[gap['end']:]
        feedback = text + '\n' + example['translation'] + '\n' + reference['hint']['rule']

        def practice(family, scaffold, spans):
            return {'schema_version': 1, 'task_family': family,
                    'context_id': 'context:' + _digest(text)[:32],
                    'example_id': example['id'], 'scaffold': scaffold,
                    'target_spans': spans, 'background_ids': backgrounds, 'reviewed': True}

        completion = {'prompt': stem, 'task': '补全句子。\n' + example['translation'],
                      'answer': accepted[0], 'accepted_answers': accepted,
                      'input_mode': 'text', 'choices': [], 'strict': False, 'safe': False,
                      'feedback': feedback,
                      'practice': practice('contextual_completion', 'retrieval', [{
                          'start': gap['start'], 'end': gap['start'] + len(GAP), 'quote': GAP}])}
        reconstruction = {'prompt': example['translation'], 'task': '排成一句话。',
                          'answer': text, 'accepted_answers': [text],
                          'input_mode': 'reorder', 'choices': chunks,
                          'strict': False, 'safe': False, 'feedback': feedback,
                          'practice': practice('sentence_reconstruction', 'cued', [{
                              'start': 0, 'end': len(example['translation']),
                              'quote': example['translation']}])}
        entry = output['items'].setdefault(item['id'], {
            'item_fingerprint': item_fingerprint(item), 'item_revision': item['revision'],
            'templates': {}, 'contexts': {}})
        for kind, raw in [('grammar_cloze', completion), ('grammar_reorder', reconstruction)]:
            validate_practice(raw, target_id=item['id'])
            entry['templates'].setdefault(kind, []).append(raw)
            fingerprint = _digest(raw)
            entry['contexts'][fingerprint] = {'background_glosses': (
                _background_glosses(stem, example, gap, by_example[example['id']], by_id)
                if kind == 'grammar_cloze' else [])}
            output['provenance'].append({'target_id': item['id'], 'kind': kind,
                'template_sha256': fingerprint, 'method': METHOD,
                'example_id': example['id'], 'example_sha256': _digest(example),
                'reference_id': reference['id'], 'reference_sha256': _digest(reference),
                'source_spans': deepcopy(reference['spans']),
                'authoring_revision': authoring['revision'], 'authoring_sha256': _digest(row),
                'review_basis': 'Exact reviewed sentence/reference; bounded source chunks; unlisted alternatives unresolved.'})
            statistics[kind] += 1
    per_target = {}
    for cid, entry in output['items'].items():
        prior = existing.get('items', {}).get(cid, {}).get('templates', {})
        all_rows = [q for templates in (prior, entry['templates']) for rows in templates.values() for q in rows]
        per_target[cid] = {'new_contexts': len(contexts[cid]),
            'total_contexts': len({q['practice']['context_id'] for q in all_rows if q.get('practice')}),
            'task_families': sorted({q['practice']['task_family'] for q in all_rows if q.get('practice')}),
            'input_modes': sorted({q.get('input_mode', 'choice') for q in all_rows})}
    output['coverage'] = {'targets': len(output['items']), 'templates': sum(statistics.values()),
        'by_kind': dict(statistics), 'per_target': per_target,
        'minimum_new_contexts': min((len(v) for v in contexts.values()), default=0),
        'claims': 'Application in exact reviewed contexts; not generated conjugation contrasts or exhaustive valid-expression grading.'}
    return output
