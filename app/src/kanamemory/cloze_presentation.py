"""Answer-free display projections over frozen gaps and public choices."""
from copy import deepcopy
from os.path import commonprefix
import re

from .multi_cloze import STATE_KEY, initial_draft, specification, validate_draft

_WORD_START = re.compile(r'[\u3400-\u9fffァ-ヶ]')


def choice_display(choices):
    """Factor only complete public context; always retain the original values.

    Do not mechanically strip verb stems or suffixes. A long shared phrase may
    end at a particle before a new written word. The separate noun-linking
    pattern preserves its whole shared clause and noun around the gap.
    """
    prefix = suffix = ''
    labels = list(choices)
    if choices and max(map(len, choices)) >= 10 and not any(
            any(c in value for c in '\n/／') for value in choices):
        shared = commonprefix(choices)
        tail = commonprefix([value[len(shared):][::-1] for value in choices])[::-1]
        middle = [value[len(shared):len(value) - len(tail) if tail else None] for value in choices]
        if (len(shared) >= 4 and tail and _WORD_START.match(tail)
                and set(middle) == {'', 'の', 'な'}):
            prefix, suffix, labels = shared, tail, middle
        else:
            for cut in range(len(shared), 3, -1):
                if (shared[cut - 1] in 'をはがにへでとのも'
                        and all(len(value) - cut >= 3 and _WORD_START.match(value[cut:]) for value in choices)):
                    prefix, labels = shared[:cut], [value[cut:] for value in choices]
                    break
    return {'prefix': prefix, 'suffix': suffix,
            'choices': [{'label': label or '∅', 'fragment': label, 'value': value}
                        for label, value in zip(labels, choices)]}


def draft_prompt_plan(question, draft, base_plan):
    """Replace exact ordered anchors using selections only; never an answer key.

    Approved background ruby is retained only on complete unchanged spans.
    Inserted answers are not submitted for new annotation or speech lookup.
    """
    spec = specification(question)
    validate_draft(question, draft)
    state = draft.get(STATE_KEY, initial_draft(question)[STATE_KEY])
    original = question['prompt']
    if base_plan['prompt'] != original:
        raise ValueError('Gap display must bind the original public prompt')
    source, offset = [], 0
    for segment in base_plan['segments']:
        end = offset + len(segment['surface'])
        source.append((offset, end, segment))
        offset = end
    pieces, spans, length = [], [], 0

    def add(text, annotation=None, *, selected=False):
        nonlocal length
        if not text:
            return
        segment = {'surface': text, 'reading': None, 'accent_number': None}
        if annotation:
            segment.update({k: deepcopy(v) for k, v in annotation.items() if k not in {'start', 'end', 'surface'}})
        segment.update(start=length, end=length + len(text))
        if selected:
            spans.append({'start': length, 'end': length + len(text), 'quote': text})
        pieces.append(segment)
        length += len(text)

    def unchanged(start, end):
        for first, last, segment in source:
            a, b = max(start, first), min(end, last)
            if a < b:
                add(original[a:b], segment if (a, b) == (first, last) else None)

    position = 0
    for slot in spec['slots']:
        unchanged(position, slot['start'])
        if slot['id'] in state['values']:
            add(state['values'][slot['id']], selected=True)
        else:
            display = choice_display(slot['choices'])
            add(display['prefix'])
            add(slot['quote'])
            add(display['suffix'])
        position = slot['end']
    unchanged(position, len(original))
    plan = {**deepcopy(base_plan), 'prompt': ''.join(part['surface'] for part in pieces),
            'segments': pieces, 'audio_text': '', 'selected_spans': spans}
    plan['furigana_visible'] = any(s.get('reading') for s in pieces)
    plan['accent_visible'] = any(s.get('accent_number') is not None or s.get('accent_phrases') for s in pieces)
    return plan
