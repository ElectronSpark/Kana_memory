"""Compile conservative, anchored practice into an independent content pack.

No API or learner database is used. Reuse is limited to an exact reviewed lemma
occurrence, its pinned lesson meaning/reading and that lesson's existing reviewed
meaning alternatives. Completion accepts the attested answer, but does not mark
unlisted expressions wrong. Grammar transformations require authored task rows.
Private sentences and prompts are inputs, never executable application source.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app/src'))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def span(text, quote):
    if not quote or text.count(quote) != 1:
        raise ValueError('Authored focus must occur exactly once')
    start = text.index(quote)
    return {'start': start, 'end': start + len(quote), 'quote': quote}


def metadata(family, context, scaffold, prompt, focus, backgrounds, example_id=None):
    result = {'schema_version': 1, 'task_family': family,
              'context_id': 'context:' + digest(context)[:32], 'scaffold': scaffold,
              'target_spans': [span(prompt, focus)],
              'background_ids': sorted(set(backgrounds)), 'reviewed': True}
    if example_id:
        result['example_id'] = example_id
    return result


def append_history(current, previous):
    """Retain issued indices and old exact lesson bindings across rebuilds."""
    if previous is None:
        return current
    if previous.get('schema_version') != 1:
        raise ValueError('Unsupported previous varied practice pack')
    current = deepcopy(current)

    def append_templates(old, new):
        merged = deepcopy(old)
        for kind, candidates in new.items():
            issued = merged.setdefault(kind, [])
            seen = {digest(raw) for raw in issued}
            for raw in candidates:
                identity = digest(raw)
                if identity not in seen:
                    issued.append(deepcopy(raw))
                    seen.add(identity)
        return merged

    for cid, old in previous.get('items', {}).items():
        fresh = current['items'].get(cid)
        if fresh is None:
            current['items'][cid] = deepcopy(old)
            continue
        history = deepcopy(old.get('history', {}))
        if old['item_fingerprint'] == fresh['item_fingerprint']:
            fresh['templates'] = append_templates(old['templates'], fresh['templates'])
            fresh['contexts'] = {**fresh.get('contexts', {}), **old.get('contexts', {})}
        else:
            history[old['item_fingerprint']] = {key: deepcopy(value) for key, value in old.items()
                                               if key != 'history'}
            returning = history.pop(fresh['item_fingerprint'], None)
            if returning is not None:
                fresh['templates'] = append_templates(returning['templates'], fresh['templates'])
                fresh['contexts'] = {**fresh.get('contexts', {}), **returning.get('contexts', {})}
        if history:
            fresh['history'] = history
    sources = {(r['target_id'], r['kind'], r['template_sha256']): deepcopy(r)
               for r in previous.get('provenance', [])}
    for row in current['provenance']:
        sources.setdefault((row['target_id'], row['kind'], row['template_sha256']), row)
    current['provenance'] = list(sources.values())
    # Exclusions affect only future selection; keep all old raw templates and
    # carry exact exclusions forward when another content batch is appended.
    retired = deepcopy(previous.get('template_retirements', {}))
    for target, bindings in current.get('template_retirements', {}).items():
        for binding, kinds in bindings.items():
            merged = retired.setdefault(target, {}).setdefault(binding, {})
            for kind, hashes in kinds.items():
                merged[kind] = sorted(set(merged.get(kind, [])) | set(hashes))
    if retired:
        current['template_retirements'] = retired
    return current


def build(*, items=None, pack=None, authoring=None, previous=None, include_expansions=True):
    from kanamemory.learning_content import learning_items
    from kanamemory.example_catalogue import bundled_example_pack
    from kanamemory.material_review import item_fingerprint
    from kanamemory.learning_questions import (KIND_ABILITIES, WORD_KINDS,
                                              GRAMMAR_KINDS, validate_practice)
    items = learning_items() if items is None else deepcopy(items)
    pack = bundled_example_pack(items) if pack is None else deepcopy(pack)
    if authoring is None:
        authoring = json.loads((ROOT / 'data/content_authoring/varied_practice_0280.json').read_text('utf8'))
    if authoring.get('schema_version') != 1 or authoring.get('review_status') != 'reviewed':
        raise ValueError('Practice authoring has not been reviewed')
    by_id = {x['id']: x for x in items}
    examples = {e['id']: e for e in pack['examples']}
    refs, example_refs, backgrounds = defaultdict(list), defaultdict(list), defaultdict(set)
    for ref in pack['references']:
        refs[ref['target_id']].append(ref)
        example_refs[ref['example_id']].append(ref)
        if ref['target_id'] in by_id:
            backgrounds[ref['example_id']].add(ref['target_id'])
    output = {'schema_version': 1, 'revision': authoring['revision'], 'items': {}}
    stats = Counter()
    provenance = []

    def reviewed_backgrounds(example, target_spans, target_id):
        # A linked grammar is not a license to invent a word meaning. Only an
        # exact lemma occurrence with a reviewed word/sense reference supplies
        # a gloss; inflected forms need separate surface-reading authoring.
        result = []
        for reference in example_refs[example['id']]:
            word = by_id.get(reference['target_id'])
            if (not word or word['family'] != 'word' or word['id'] == target_id
                    or reference.get('example_revision') != example['revision']):
                continue
            for anchor in reference.get('spans', []):
                start, end = anchor['start'], anchor['end']
                surface = anchor['quote']
                if (surface != word['title'] or example['text'][start:end] != surface
                        or any(start < s['end'] and end > s['start'] for s in target_spans)
                        or any(start < s['end'] and end > s['start'] for s in result)):
                    continue
                result.append({'start': start, 'end': end, 'surface': surface,
                    'meaning': word['meaning'], 'reading': word['reading'],
                    'target_id': word['id']})
        return sorted(result, key=lambda row: row['start'])

    def add(item, kind, raw, evidence):
        if (kind not in (WORD_KINDS if item['family'] == 'word' else GRAMMAR_KINDS)
                or KIND_ABILITIES[kind] not in item['abilities']):
            raise ValueError('An extension cannot introduce a new memory ability')
        validate_practice(raw, target_id=item['id'])
        if not all(isinstance(raw.get(k), str) and raw[k] for k in ('prompt', 'task', 'answer', 'feedback')):
            raise ValueError('Incomplete practice task')
        if raw.get('input_mode', 'choice') == 'choice':
            choices = raw.get('choices', [])
            if len(choices) < 3 or len(set(choices)) != len(choices) or raw['answer'] not in choices:
                raise ValueError('Reviewed choice task needs at least three distinct options')
        record = output['items'].setdefault(item['id'], {
            'item_fingerprint': item_fingerprint(item), 'item_revision': item['revision'],
            'templates': {}, 'contexts': {}})
        record['templates'].setdefault(kind, []).append(deepcopy(raw))
        glosses = evidence.pop('background_glosses', [])
        for gloss in glosses:
            if (raw['prompt'][gloss['start']:gloss['end']] != gloss['surface']
                    or not isinstance(gloss.get('meaning'), str) or not gloss['meaning'].strip()):
                raise ValueError('Background gloss is not anchored in the public prompt')
        record['contexts'][digest(raw)] = {'background_glosses': deepcopy(glosses)}
        provenance.append({'target_id': item['id'], 'kind': kind,
                           'template_sha256': digest(raw), **evidence})
        stats[kind] += 1

    limit = authoring.get('lexical_context_limit', 4)
    if type(limit) is not int or not 0 <= limit <= 8:
        raise ValueError('Invalid lexical reuse bound')
    for item in sorted(items, key=lambda x: x['id']):
        if item['family'] != 'word' or not item.get('question_templates'):
            continue
        form, reading = item['title'], item['reading']
        old = item['question_templates']
        meaning_rows = [q for q in old.get('vocab_meaning', [])
                        if q.get('answer') == item['meaning'] and len(q.get('choices', [])) >= 3]
        if not meaning_rows:
            continue
        introductory = {e['text'] for e in item.get('examples', [])}
        introductory.add(item.get('instruction', {}).get('example', ''))
        selected, seen = [], set()
        for ref in refs[item['id']]:
            example = examples.get(ref['example_id'])
            spans = ref.get('spans', [])
            if not example or not example.get('translation') or example['text'] in introductory:
                continue
            text = example['text']
            if (len(spans) != 1 or spans[0].get('quote') != form or text.count(form) != 1
                    or text[spans[0]['start']:spans[0]['end']] != form
                    or ref.get('example_revision') != example['revision']
                    or not text.endswith(('。', '？', '！')) or len(text) > 80):
                continue
            bg = sorted(backgrounds[example['id']] - {item['id']})
            if len(bg) > 8 or text in seen:
                continue
            seen.add(text)
            selected.append((len(bg), len(text), text, example, ref, bg))
        for index, (_, _, text, example, ref, bg) in enumerate(sorted(selected, key=lambda x: x[:3])[:limit]):
            eid = example['id']
            source = {'method': 'exact_reviewed_lemma_reuse', 'example_id': eid,
                      'example_sha256': digest(example), 'reference_id': ref['id'],
                      'reference_sha256': digest(ref), 'source_spans': ref['spans']}
            selected_meaning = meaning_rows[index % len(meaning_rows)]
            # The instruction names the narrow lesson sense. Existing reviewed
            # semantic alternatives are reused, not inferred from the sentence.
            meaning = {'prompt': text, 'task': f'句中「{form}」对应本课的哪个意思？',
                       'answer': item['meaning'], 'choices': deepcopy(selected_meaning['choices']),
                       'input_mode': 'choice', 'strict': True, 'safe': True,
                       'readings': {form: reading},
                       'feedback': f'「{form}」在这里表示{item["meaning"]}。\n{example["translation"]}',
                       'practice': metadata('word_in_context', text, 'recognition', text, form, bg, eid)}
            add(item, 'vocab_meaning', meaning, {**source, 'choice_source_sha256': digest(selected_meaning)})
            if ('reading' in item['abilities'] and old.get('vocab_reading')
                    and reading not in text):
                q = {'prompt': text, 'task': f'只写「{form}」在本课中的假名读法。',
                     'answer': reading, 'accepted_answers': [reading],
                     'alternative_answers': item.get('alternative_readings', []),
                     'choices': [], 'input_mode': 'text', 'strict': True, 'safe': False,
                     'feedback': f'「{form}」读作「{reading}」，这里表示{item["meaning"]}。',
                     'practice': metadata('contextual_reading', text, 'retrieval', text, form, bg, eid)}
                add(item, 'vocab_reading', q, source)
            if 'use' in item['abilities']:
                gap = text.replace(form, '（　）', 1)
                q = {'prompt': gap, 'task': f'用本课表示“{item["meaning"]}”的单词补全，可写汉字或假名。',
                     'answer': form, 'accepted_answers': [form, reading], 'choices': [],
                     'input_mode': 'text', 'strict': False, 'safe': False,
                     'feedback': f'{text}\n{example["translation"]}\n这里用「{form}」表示{item["meaning"]}。',
                     'practice': metadata('contextual_completion', text, 'retrieval', gap, '（　）', bg, eid)}
                add(item, 'vocab_usage', q, source)

    grammar_limit = authoring.get('grammar_context_limit', 4)
    if type(grammar_limit) is not int or not 0 <= grammar_limit <= 8:
        raise ValueError('Invalid grammar recognition reuse bound')
    for item in sorted(items, key=lambda x: x['id']):
        if item['family'] != 'grammar' or item.get('domain') == 'pronunciation':
            continue
        from kanamemory.learning_questions import _CONTENT as question_data
        old = item.get('question_templates') or question_data.get('GRAMMAR_QUESTIONS', {}).get(item['id'], {})
        meaning_rows = [q for q in old.get('grammar_meaning', [])
                        if q.get('answer') == item['meaning'] and len(q.get('choices', [])) >= 3]
        if not meaning_rows:
            continue
        old_prompts = {q.get('prompt') for qs in old.values() for q in qs}
        selected, seen = [], set()
        for ref in refs[item['id']]:
            example = examples.get(ref['example_id'])
            spans = ref.get('spans', [])
            if (not example or not example.get('translation') or not ref.get('hint', {}).get('rule')
                    or not 1 <= len(spans) <= 3 or ref.get('example_revision') != example['revision']):
                continue
            text = example['text']
            if (text in old_prompts or text in seen or len(text) > 100
                    or not text.endswith(('。', '？', '！'))
                    or any(text[s['start']:s['end']] != s['quote'] for s in spans)
                    or sum(len(s['quote']) for s in spans) >= len(text)):
                continue
            bg = sorted(backgrounds[example['id']] - {item['id']})
            if len(bg) > 8:
                continue
            seen.add(text)
            selected.append((len(bg), len(text), text, example, ref, bg))
        for index, (_, _, text, example, ref, bg) in enumerate(sorted(selected, key=lambda x: x[:3])[:grammar_limit]):
            source_choice = meaning_rows[index % len(meaning_rows)]
            focus = '／'.join(s['quote'] for s in ref['spans'])
            practice = {'schema_version': 1, 'task_family': 'grammar_in_context',
                        'context_id': 'context:' + digest(text)[:32],
                        'example_id': example['id'], 'scaffold': 'recognition',
                        'target_spans': deepcopy(ref['spans']), 'background_ids': bg, 'reviewed': True}
            q = {'prompt': text, 'task': f'句中「{focus}」对应本课的什么作用？',
                 'answer': item['meaning'], 'choices': deepcopy(source_choice['choices']),
                 'input_mode': 'choice', 'strict': True, 'safe': True,
                 'feedback': f'这里的「{focus}」用于{item["meaning"]}。\n{example["translation"]}\n{ref["hint"]["rule"]}',
                 'practice': practice}
            add(item, 'grammar_meaning', q, {'method': 'exact_reviewed_grammar_function_reuse',
                'example_id': example['id'], 'example_sha256': digest(example),
                'reference_id': ref['id'], 'reference_sha256': digest(ref),
                'source_spans': ref['spans'], 'choice_source_sha256': digest(source_choice),
                'background_glosses': reviewed_backgrounds(example, ref['spans'], item['id'])})

    for index, authored in enumerate(authoring.get('manual_tasks', [])):
        item = by_id[authored['target_id']]
        raw = deepcopy(authored['question'])
        raw['practice'] = metadata(authored['task_family'], authored['context_text'],
            authored['scaffold'], raw['prompt'], authored['focus'], authored.get('background_ids', []),
            authored.get('example_id'))
        if not set(raw['practice']['background_ids']) <= set(by_id):
            raise ValueError('Unknown manual background item')
        add(item, authored['kind'], raw, {'method': 'manual_scenario_review',
            'authoring_revision': authoring['revision'], 'authoring_index': index,
            'authoring_sha256': digest(authored),
            'background_glosses': deepcopy(authored.get('background_glosses', []))})
    output['provenance'] = provenance
    expansion_coverage = {}
    if include_expansions:
        from practice_grammar_expansion import build_grammar_expansion
        from practice_word_expansion import build_word_expansion
        for name, expand in (('grammar', build_grammar_expansion), ('word', build_word_expansion)):
            extension = expand(items, pack, output)
            expansion_coverage[name] = deepcopy(extension.get('coverage', {}))
            # Base and previously issued variants remain exact prefixes. New
            # modes reuse the same existing ability identities and schedules.
            output = append_history(extension, output)
    output = append_history(output, previous)
    active = {cid: record for cid, record in output['items'].items()
              if cid in by_id and record['item_fingerprint'] == item_fingerprint(by_id[cid])}
    stats = Counter({})
    for record in active.values():
        stats.update({kind: len(rows) for kind, rows in record['templates'].items()})
    output['coverage'] = {'targets': len(output['items']), 'by_kind': dict(sorted(stats.items())),
                          'templates': sum(stats.values()),
                          'by_family': dict(Counter(by_id[cid]['family'] for cid in active)),
                          'manual_tasks': len(authoring.get('manual_tasks', [])),
                          'active_targets': len(active),
                          'archived_bindings': sum(len(record.get('history', {})) for record in output['items'].values()),
                          'claims': 'Only the listed targets/tasks; not full dictionary or grammar coverage.'}
    if expansion_coverage:
        output['coverage']['expansion_0281'] = expansion_coverage
    return output


def main():
    from kanamemory.content_pack import namespace_document, decode_value
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT/'data/content_packs/full/teaching/varied_practice.json')
    args = parser.parse_args()
    previous = None
    if args.output.exists():
        document = json.loads(args.output.read_text('utf8'))
        if document.get('name') != 'varied_practice':
            raise ValueError('Refusing to replace an unrelated namespace')
        previous = decode_value(document['payload'])
    result = build(previous=previous)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(namespace_document('varied_practice', result),
                                     ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(json.dumps(result['coverage'], ensure_ascii=False))


if __name__ == '__main__':
    main()
