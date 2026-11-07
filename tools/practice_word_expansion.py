"""Compile exact, sense-bound word practice without inventing inflections.

Private wording and contrast decisions live in the authoring pack. This module
only exposes supplied reviewed sentence surfaces, literal readings, and pinned
contrast rows. It neither enrols memory units nor edits an existing template.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _normal_reading(value):
    return ''.join(chr(ord(c) - 0x60) if '\u30a1' <= c <= '\u30f6' else c for c in value)


def _span(start, quote):
    return {'start': start, 'end': start + len(quote), 'quote': quote}


def _reading(item, reference):
    """A base reading never licenses assigning it to a conjugated surface."""
    surface = reference['spans'][0]['quote']
    if surface == item['title']:
        return item['reading']
    rule = reference.get('hint', {}).get('rule', '')
    match = re.search(re.escape(surface) + r'[（(]([ぁ-ゖァ-ヺー]+)[）)]', rule)
    return match.group(1) if reference.get('surface_hint') is True and match else None


def _metadata(family, example, spans, backgrounds, mode):
    return {'schema_version': 1, 'task_family': family,
        'context_id': 'context:' + digest(example['text'])[:32],
        'example_id': example['id'],
        'scaffold': {'choice': 'recognition', 'text': 'retrieval', 'reorder': 'cued'}[mode],
        'target_spans': deepcopy(spans), 'background_ids': sorted(backgrounds), 'reviewed': True}


def build_word_expansion(items, pack, existing, *, authoring=None):
    """Return an append-only candidate; the caller retains issued indices.

    All inputs remain untouched. Unknown or edited content does not inherit a
    reviewed decision, and stale private contrast rows fail rather than drift.
    """
    from kanamemory.material_review import item_fingerprint
    from kanamemory.learning_questions import KIND_ABILITIES, validate_practice

    if authoring is None:
        authoring = json.loads((ROOT / 'data/content_authoring/word_practice_0281.json').read_text('utf8'))
    if authoring.get('schema_version') != 1 or authoring.get('review_status') != 'reviewed':
        raise ValueError('Word practice decisions are not reviewed')
    limit = authoring.get('selection_limit', 4)
    if type(limit) is not int or not 3 <= limit <= 6:
        raise ValueError('Word practice needs a bounded set of distinct contexts')
    by_id = {item['id']: item for item in items}
    examples = {e['id']: e for e in pack['examples']}
    refs, example_refs = defaultdict(list), defaultdict(list)
    for ref in pack['references']:
        refs[ref['target_id']].append(ref)
        example_refs[ref['example_id']].append(ref)
    output = {'schema_version': 1, 'revision': authoring['revision'], 'items': {}, 'provenance': []}
    rejected = Counter()

    def compatible(item):
        entry = authoring.get('items', {}).get(item['id'], {})
        return (item.get('family') == 'word' and not item.get('ai_generated')
            and entry.get('item_fingerprint') == item_fingerprint(item))

    def backgrounds(example, target, target_spans):
        ids, glosses = set(), []
        for ref in example_refs[example['id']]:
            item = by_id.get(ref['target_id'])
            if (not item or item['id'] == target['id']
                    or ref.get('example_revision') != example['revision']):
                continue
            ids.add(item['id'])
            if item['family'] != 'word':
                continue
            for anchor in ref.get('spans', []):
                start, end = anchor['start'], anchor['end']
                if (example['text'][start:end] != anchor['quote']
                        or anchor['quote'] != item['title']
                        or any(start < s['end'] and end > s['start'] for s in target_spans)
                        or any(start < g['end'] and end > g['start'] for g in glosses)):
                    continue
                glosses.append({'start': start, 'end': end, 'surface': anchor['quote'],
                    'meaning': item['meaning'], 'reading': item['reading'], 'target_id': item['id']})
        return ids, sorted(glosses, key=lambda g: g['start'])

    def evidence(item, example, ref, **extra):
        return {'method': 'exact_reviewed_word_surface_reuse',
            'example_id': example['id'], 'example_sha256': digest(example),
            'reference_id': ref['id'], 'reference_sha256': digest(ref),
            'source_spans': deepcopy(ref['spans']),
            'authoring_revision': authoring['revision'],
            'binding_sha256': digest(authoring['items'][item['id']]), **extra}

    def add(item, kind, raw, source, glosses=()):
        if KIND_ABILITIES[kind] not in item['abilities']:
            return
        validate_practice(raw, target_id=item['id'])
        if raw['input_mode'] == 'choice' and (len(raw['choices']) < 3
                or len(set(raw['choices'])) != len(raw['choices'])
                or raw['answer'] not in raw['choices']):
            raise ValueError('Word choices must include a unique reviewed answer')
        record = output['items'].setdefault(item['id'], {
            'item_fingerprint': item_fingerprint(item), 'item_revision': item['revision'],
            'templates': {}, 'contexts': {}})
        identity = digest(raw)
        if any(digest(old) == identity for old in record['templates'].get(kind, [])):
            return
        record['templates'].setdefault(kind, []).append(raw)
        record['contexts'][identity] = {'background_glosses': deepcopy(list(glosses))}
        output['provenance'].append({'target_id': item['id'], 'kind': kind,
            'template_sha256': identity, **source})

    for item in items:
        if not compatible(item):
            continue
        cid, form, reading, meaning = item['id'], item['title'], item['reading'], item['meaning']
        base = item.get('question_templates') or {}
        meaning_rows = [q for q in base.get('vocab_meaning', [])
                        if q.get('answer') == meaning and len(q.get('choices', [])) >= 3]
        if not meaning_rows:
            # The four legacy words have authored meanings but synthesised
            # base templates. Their exact disjoint comparison set is private.
            choices = authoring['items'][cid].get('meaning_choices', [])
            if len(choices) >= 3 and meaning in choices:
                meaning_rows = [{'answer': meaning, 'choices': choices}]
        if not meaning_rows:
            rejected['no_reviewed_meaning_choices'] += 1
            continue
        issued = {q['practice']['context_id'] for rows in existing.get('items', {}).get(cid, {}).get('templates', {}).values()
                  for q in rows if q.get('practice')}
        candidates = []
        for ref in refs[cid]:
            example = examples.get(ref['example_id'])
            spans = ref.get('spans', [])
            if (not example or ref.get('example_revision') != example['revision']
                    or len(spans) != 1 or not example.get('translation')):
                continue
            text, anchor = example['text'], spans[0]
            surface = anchor['quote']
            if (text[anchor['start']:anchor['end']] != surface or text.count(surface) != 1
                    or (surface != form and ref.get('surface_hint') is not True)
                    or len(text) > 90 or not text.endswith(('。', '？', '！'))
                    or any(v in example['translation'] for v in ('描述对', '描述门的', '这一用法'))):
                continue
            bg, glosses = backgrounds(example, item, spans)
            if len(bg) > 12:
                continue
            old_context = 'context:' + digest(text)[:32] in issued
            candidates.append((old_context, len(bg), len(text), text, example, ref, bg, glosses))
        selected = []
        seen = set()
        for row in sorted(candidates, key=lambda r: r[:4]):
            if row[3] not in seen:
                selected.append(row); seen.add(row[3])
            if len(selected) == limit:
                break
        for index, (_, _, _, text, example, ref, bg, glosses) in enumerate(selected):
            anchor = ref['spans'][0]; surface = anchor['quote']
            source_choice = meaning_rows[index % len(meaning_rows)]
            source = evidence(item, example, ref)
            q = {'prompt': text, 'task': '标记部分在这里是什么意思？',
                'answer': meaning, 'choices': deepcopy(source_choice['choices']),
                'input_mode': 'choice', 'strict': True, 'safe': True,
                'feedback': example['translation'] + '\n' + ref['explanation'],
                'practice': _metadata('word_in_context', example, [anchor], bg, 'choice')}
            surface_reading = _reading(item, ref)
            if surface_reading:
                q['readings'] = {surface: surface_reading}
            add(item, 'vocab_meaning', q, {**source, 'choice_source_sha256': digest(source_choice)}, glosses)
            gap = text[:anchor['start']] + '（　）' + text[anchor['end']:]
            gap_span = [_span(anchor['start'], '（　）')]
            # Prompt offsets after the removed span must be transformed too.
            delta = len('（　）') - len(surface)
            gap_glosses = [dict(g, start=g['start'] + (delta if g['start'] >= anchor['end'] else 0),
                               end=g['end'] + (delta if g['start'] >= anchor['end'] else 0)) for g in glosses]
            accepted = list(dict.fromkeys([surface, *([surface_reading] if surface_reading else [])]))
            q = {'prompt': gap, 'task': '补全句子：' + example['translation'],
                'answer': surface, 'accepted_answers': accepted, 'choices': [],
                'input_mode': 'text', 'strict': False, 'safe': False,
                'feedback': text + '\n' + ref['explanation'],
                'practice': _metadata('contextual_completion', example, gap_span, bg, 'text')}
            add(item, 'vocab_usage', q, source, gap_glosses)
            if surface_reading and _normal_reading(surface_reading) not in _normal_reading(text):
                q = {'prompt': text, 'task': '标记部分怎么读？',
                    'answer': surface_reading, 'accepted_answers': [surface_reading],
                    'choices': [], 'input_mode': 'text', 'strict': True, 'safe': False,
                    'feedback': surface + '：' + surface_reading + '\n' + example['translation'],
                    'practice': _metadata('contextual_reading', example, [anchor], bg, 'text')}
                add(item, 'vocab_reading', q, source)
            if (surface != form and form not in text
                    and _normal_reading(reading) not in _normal_reading(text)):
                q = {'prompt': text, 'task': '标记部分的基本形是？',
                    'answer': form, 'accepted_answers': list(dict.fromkeys([form, reading])),
                    'choices': [], 'input_mode': 'text', 'strict': False, 'safe': False,
                    'feedback': surface + ' → ' + form + '\n' + ref['explanation'],
                    'practice': _metadata('word_form_recall', example, [anchor], bg, 'text')}
                add(item, 'vocab_recall', q, source)
            if surface == form and re.search('[一-龯々]', form) and _normal_reading(form) != _normal_reading(reading):
                prompt = text[:anchor['start']] + reading + text[anchor['end']:]
                if form in prompt:
                    continue
                alternatives = []
                for candidate in base.get('vocab_usage', []):
                    for choice in candidate.get('choices', []):
                        if choice != form and choice not in alternatives:
                            alternatives.append(choice)
                alternatives = alternatives or authoring['items'][cid].get('orthography_distractors', [])
                if len(alternatives) < 2:
                    continue
                q = {'prompt': prompt, 'task': '选出标记词的写法。', 'answer': form,
                    'choices': [form, *alternatives[:3]], 'input_mode': 'choice',
                    'strict': True, 'safe': False, 'feedback': text + '\n' + example['translation'],
                    'practice': _metadata('contextual_orthography', example,
                        [_span(anchor['start'], reading)], bg, 'choice')}
                add(item, 'vocab_orthography', q, source)

    for decision in authoring.get('minimal_contrasts', []):
        item = by_id.get(decision['target_id'])
        if not item or not compatible(item):
            continue
        example = examples.get(decision['example_id'])
        ref = next((r for r in refs[item['id']] if r['id'] == decision['source_ref_id']), None)
        if (not example or not ref or decision.get('review_status') != 'reviewed'
                or example['revision'] != decision['example_revision']
                or example['text'] != decision['text'] or example['translation'] != decision['translation']
                or digest(ref) != decision.get('reference_sha256')
                or ref.get('example_revision') != example['revision']):
            raise ValueError('A reviewed lexical contrast no longer matches its exact source')
        wrong = decision.get('wrong_translations', [])
        if (len(wrong) != 2 or not all(isinstance(x, str) and x for x in wrong)
                or len(set([example['translation'], *wrong])) != 3):
            raise ValueError('A lexical contrast needs two reviewed non-equivalent translations')
        bg, glosses = backgrounds(example, item, ref['spans'])
        q = {'prompt': example['text'], 'task': '选出整句的意思。',
            'answer': example['translation'], 'choices': [example['translation'], *wrong],
            'input_mode': 'choice', 'strict': True, 'safe': True,
            'feedback': example['translation'] + '\n' + ref['explanation'],
            'practice': _metadata('sentence_interpretation', example, ref['spans'], bg, 'choice')}
        surface_reading = _reading(item, ref) if len(ref['spans']) == 1 else None
        if surface_reading:
            q['readings'] = {ref['spans'][0]['quote']: surface_reading}
        add(item, 'reading_comprehension', q, {**evidence(item, example, ref),
            'method': 'manual_lexical_minimal_contrast', 'decision_sha256': digest(decision)}, glosses)

    counts = Counter()
    per_item = {}
    for cid, record in output['items'].items():
        rows = [q for values in record['templates'].values() for q in values]
        per_item[cid] = {'templates': len(rows),
            'contexts': len({q['practice']['context_id'] for q in rows}),
            'task_families': sorted({q['practice']['task_family'] for q in rows}),
            'input_modes': sorted({q['input_mode'] for q in rows})}
        counts.update({kind: len(values) for kind, values in record['templates'].items()})
    output['coverage'] = {'targets': len(per_item), 'templates': sum(counts.values()),
        'by_kind': dict(sorted(counts.items())), 'per_item': per_item, 'rejected': dict(rejected),
        'claims': 'Finite, exact-source practice for pinned vocabulary lessons; not unrestricted dictionary coverage.'}
    return output
