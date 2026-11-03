"""Shared, anchored examples adapted from the authored local curriculum.

This catalogue never rewrites saved lessons or question snapshots. Text is an
immutable identity; each lesson reference owns its emphasis and explanation.
Grammar anchors come from the *authored* cloze context, never an arbitrary
particle search across other lessons. Cross-lesson annotations are explicit.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

from .example_expansion import expansion_rows

FORMAT = 'kanamemory-example-pack'
VERSION = 1
SOURCE = 'kanamemory-shared-examples-20261025'


# The original eight foundation lessons predate the paired-example catalogue.
# Each tuple is an original full example, translation, and exact focus quote.
from .content_pack import load_namespace

_CONTENT = load_namespace('example_catalogue', {})

_FOUNDATION = _CONTENT.get('_FOUNDATION', {})


# Explicit pedagogical focus for the authored pronunciation examples. A whole
# word is appropriate where the phenomenon concerns the whole accent/rhythm;
# otherwise only the actual phonological segment is highlighted.
_PRONUNCIATION_FOCUS = _CONTENT.get('_PRONUNCIATION_FOCUS', {})


def example_identity(text):
    """Exact NFC-preserving text identity; spelling changes create a new item."""
    return 'example:' + hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]


def _span(text, start, end):
    return {'start': start, 'end': end, 'quote': text[start:end]}


def _quotes(text, quotes, *, every=False):
    result = []
    for quote in quotes:
        positions = [m.start() for m in re.finditer(re.escape(quote), text)]
        if not positions:
            raise ValueError('Authored example focus is absent: ' + repr((text, quote)))
        for first in positions if every else positions[:1]:
            result.append(_span(text, first, first + len(quote)))
    return _merge_spans(text, result)


def _merge_spans(text, spans):
    bounds = []
    for span in sorted(spans, key=lambda s: (s['start'], s['end'])):
        if bounds and span['start'] < bounds[-1][1]:
            bounds[-1][1] = max(bounds[-1][1], span['end'])
        else:
            bounds.append([span['start'], span['end']])
    return [_span(text, start, end) for start, end in bounds]


def _cloze_focus(text, templates):
    """Align the literal authored surroundings; alternate valid answers work."""
    found = []
    for template in templates:
        prompt = template.get('prompt', template.get('cloze', ''))
        if prompt.count('（　）') != 1:
            continue
        left, right = prompt.split('（　）')
        if not text.startswith(left) or (right and not text.endswith(right)):
            continue
        end = len(text) - len(right) if right else len(text)
        if len(left) < end:
            found.append(_span(text, len(left), end))
    return _merge_spans(text, found)


def _grammar_focus(item, text):
    spans = _cloze_focus(text, item.get('question_templates', {}).get('grammar_cloze', []))
    # Some clozes test the connection before the construction (の before 際に).
    # Add the explicit expression from this lesson's own title when present.
    title_expression = item['title'].split('：')[0].replace('〜', '').replace('～', '')
    for phrase in re.split('[／・、]', title_expression):
        if len(phrase) > 1 and phrase in text:
            spans.extend(_quotes(text, [phrase]))
    if not spans:
        raise ValueError('No anchored grammar focus for ' + item['id'] + ': ' + text)
    return _merge_spans(text, spans)


def _short(value, limit=280):
    value = str(value or '').strip()
    return value if len(value) <= limit else value[:limit - 1] + '…'


def _samples(item):
    identity = item['id']
    if identity in _FOUNDATION:
        return [{'text': text, 'translation': translation,
                 'spans': _quotes(text, [focus])} for text, translation, focus in _FOUNDATION[identity]]
    values = item.get('examples') or item.get('instruction', {}).get('examples') or []
    result = []
    for index, example in enumerate(values):
        text = example.get('text', '')
        if not text:
            continue
        try:
            if example.get('spans'):
                spans = deepcopy(example['spans'])
                if any(type(s.get('start')) is not int or type(s.get('end')) is not int
                       or not 0 <= s['start'] < s['end'] <= len(text)
                       or text[s['start']:s['end']] != s.get('quote') for s in spans):
                    continue
            elif item.get('domain') == 'pronunciation':
                focus = _PRONUNCIATION_FOCUS[identity.split(':', 1)[1]][index]
                # Contrast words はなは / へやへ explicitly teach both occurrences.
                spans = _quotes(text, focus, every=identity in {'pronunciation:particle_wa', 'pronunciation:particle_he'})
            elif item.get('family') == 'word':
                spans = _cloze_focus(text, [{'cloze': example.get('cloze', '')}])
                if not spans:
                    spans = _quotes(text, [item['title']])
            else:
                spans = _grammar_focus(item, text)
        except (KeyError, IndexError, ValueError):
            # Retained/custom snapshots may have unknown courses or different
            # wording. Keep their original lesson usable; never invent an
            # emphasis merely to force the shared catalogue to cover it.
            continue
        result.append({'text': text, 'translation': example.get('translation', ''), 'spans': spans})
    return result


def bundled_example_pack(items, *, apply_review=True):
    """Build an independent v1 pack; no learner state or fuzzy grammar matching.

    Saved dictionary-only courses may have no authored examples. They remain
    addable via the separate user pack instead of receiving invented material.
    """
    items = list(items.values() if isinstance(items, dict) else items)
    if apply_review:
        from .material_review import example_source_item
        items = [example_source_item(item) for item in items]
    by_target = {item['id']: item for item in items}
    examples, references = {}, {}

    def add(item, text, translation, spans, *, rule_hint=None, explanation=None):
        eid = example_identity(text)
        example = examples.setdefault(eid, {'id': eid, 'revision': '1', 'text': text,
            'translation': translation, 'source_ids': [SOURCE]})
        if example['translation'] != translation:
            # Homographic examples have per-reference senses; no global guess.
            example['translation'] = ''
        rid = 'example-ref:' + hashlib.sha256((eid + '\0' + item['id']).encode()).hexdigest()[:32]
        if rid in references:
            return
        focus = '／'.join(span['quote'] for span in spans)
        if item.get('family') == 'word':
            hints = {'meaning': _short(item.get('meaning')), 'reading': _short(item.get('reading')),
                     'rule': _short(rule_hint) if rule_hint else ''}
            detail = explanation or (focus + '：' + item.get('meaning', '') + '。' + translation)
        else:
            meaning = item.get('meaning', '')
            hint = rule_hint or (translation if item.get('domain') == 'pronunciation' else meaning)
            hints = {'meaning': '', 'reading': '', 'rule': _short(hint)}
            detail = explanation or (translation + '\n' + item.get('instruction', {}).get('rule', ''))
        references[rid] = {'id': rid, 'example_id': eid, 'example_revision': '1',
            'target_id': item['id'], 'spans': deepcopy(spans), 'hint': hints,
            'explanation': detail.strip(), 'weight': 1.0}
        if item.get('family') == 'word' and rule_hint:
            references[rid]['surface_hint'] = True

    for item in sorted(items, key=lambda i: i['id']):
        for sample in _samples(item):
            add(item, sample['text'], sample['translation'], sample['spans'])

    # These annotations are individually selected, rather than attaching every
    # は/で/に or homographic word throughout the corpus. One span can legitimately
    # demonstrate both topic grammar and its pronunciation, with distinct hints.
    word_candidates = {}
    for item in items:
        if item.get('family') == 'word':
            word_candidates.setdefault(item['title'], []).append(item)
    # A later imported homograph must not silently redirect an authored sense.
    by_word = {form: values[0] for form, values in word_candidates.items() if len(values) == 1}
    for text, translation, annotations in _SHARED_ANNOTATIONS:
        for target, quotes, hint in annotations:
            item = by_word.get(target[5:]) if target.startswith('form:') else by_target.get(target)
            if item is not None:
                add(item, text, translation, _quotes(text, quotes), rule_hint=hint,
                    explanation=(hint + '\n' + translation) if hint else None)
    # Reviewed background vocabulary helps rank examples by their actual
    # familiar content. Only these exact sentence/form pairs are annotated;
    # there is no corpus-wide substring or homograph inference.
    for line in _REVIEWED_BACKGROUND.strip().splitlines():
        text, forms = line.split('|')
        example = examples.get(example_identity(text))
        if example is None:
            continue  # an old snapshot may no longer contain this sentence
        for form in forms.split():
            item = by_word.get(form)
            if item is not None:
                add(item, text, example['translation'], _quotes(text, [form], every=True))
    # Keep every original example/reference intact. New annotations are exact,
    # sense-pinned rows, and do not change any saved course or question snapshot.
    original_translations = {e['id']: e['translation'] for e in examples.values()}
    for item in sorted(items, key=lambda i: i['id']):
        for text, translation, quotes, hint in expansion_rows(item):
            add(item, text, original_translations.get(example_identity(text), translation),
                _quotes(text, quotes), rule_hint=hint or None,
                explanation=(hint + '\n' + translation).strip() if hint else None)
    pack = {'format': FORMAT, 'version': VERSION, 'status': 'reviewed',
            'examples': sorted(examples.values(), key=lambda e: e['id']),
            'references': sorted(references.values(), key=lambda r: r['id'])}
    if apply_review:
        from .material_review import reviewed_pack
        return reviewed_pack(pack, include_additions=True, known_targets=by_target)
    return pack


_SHARED_ANNOTATIONS = _CONTENT.get('_SHARED_ANNOTATIONS', [])


# Each sentence was checked against the current lesson and the pinned lexical
# sense. For example 車 is used for a car here, never matched inside 電車;
# 先生 is deliberately omitted where the standalone honorific could also mean
# a doctor or another professional. These annotations do not create new text.
_REVIEWED_BACKGROUND = _CONTENT.get('_REVIEWED_BACKGROUND', '')
