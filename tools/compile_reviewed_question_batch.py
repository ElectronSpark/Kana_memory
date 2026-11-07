"""Publish an exact, independently reviewed question batch into a content pack.

This tool never writes learner databases. A partial or stale semantic ledger is
an error, not permission to ship a smaller batch under a full-coverage claim.
Question JSON is private content; this compiler contains no course sentences.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import argparse
import json
from pathlib import Path

from build_varied_practice import append_history, digest

FORMAT = 'kanamemory-reviewed-question-batch'
VERSION = 1
FIELDS = {'slot_id', 'target_id', 'kind', 'question', 'task_family', 'context_text',
          'target_spans', 'background_ids', 'assessed_ids', 'coverage_scope', 'source_refs'}


def apply_retirements(output, authoring, review, items):
    """Exclude rejected old exercises from new plans without deleting them."""
    from kanamemory.learning_questions import practice_templates
    from kanamemory.material_review import item_fingerprint
    rows = authoring.get('retirements', [])
    verdicts = review.get('retirements', [])
    if not isinstance(rows, list) or not isinstance(verdicts, list):
        raise ValueError('Invalid retirement manifest')
    if len(rows) != len(verdicts):
        raise ValueError('Retirement scope requires an exact independent review')
    approved = {row.get('slot_id'): row for row in verdicts}
    if len(approved) != len(verdicts):
        raise ValueError('Duplicate retirement approval')
    replacements = {row['slot_id']: row for row in authoring['questions']}
    seen = set()
    pools = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'slot_id', 'target_id', 'kind',
                'item_fingerprint', 'template_sha256', 'replacement_slot_id'}):
            raise ValueError('Invalid exact retirement')
        identity, target, kind = row['slot_id'], row['target_id'], row['kind']
        verdict = approved.get(identity, {})
        if (identity in seen or verdict.get('status') != 'retire'
                or verdict.get('candidate_sha256') != digest(row) or not verdict.get('evidence_refs')):
            raise ValueError('Unreviewed or stale retirement')
        seen.add(identity)
        replacement = replacements.get(row['replacement_slot_id'], {})
        if replacement.get('target_id') != target or replacement.get('kind') != kind:
            raise ValueError('A retired core slot needs a reviewed replacement for the same item and kind')
        item = items.get(target)
        if item is None or item_fingerprint(item) != row['item_fingerprint']:
            raise ValueError('Retirement lesson binding is stale')
        if target not in pools:
            pools[target] = practice_templates(item) or {}
        if row['template_sha256'] not in {digest(raw) for raw in pools[target].get(kind, [])}:
            raise ValueError('Retirement does not match an exact issued raw template')
        hashes = output.setdefault('template_retirements', {}).setdefault(target, {}).setdefault(
            row['item_fingerprint'], {}).setdefault(kind, [])
        if row['template_sha256'] not in hashes:
            hashes.append(row['template_sha256'])
    if seen != set(approved):
        raise ValueError('Retirement review identities differ')


def compile_batch(authoring, review, items, previous):
    from kanamemory.learning_questions import KIND_ABILITIES, validate_practice
    from kanamemory.material_review import item_fingerprint
    from kanamemory.multi_cloze import is_cloze_question, validate_snapshot

    if (authoring.get('format') != FORMAT or authoring.get('version') != VERSION
            or not isinstance(authoring.get('questions'), list)
            or type(authoring.get('expected_count')) is not int
            or len(authoring['questions']) != authoring['expected_count']
            or not authoring['questions']):
        raise ValueError('The authored batch is incomplete')
    if (review.get('format') != 'kanamemory-exact-question-review'
            or review.get('version') != VERSION or review.get('reviewer') != 'gpt-6.1-sol'
            or review.get('authoring_sha256') != digest(authoring)
            or not isinstance(review.get('questions'), list)):
        raise ValueError('An exact Sol review manifest is required')
    approved = {}
    for verdict in review['questions']:
        identity = verdict.get('slot_id')
        if not isinstance(identity, str) or identity in approved:
            raise ValueError('Duplicate or invalid review identity')
        if verdict.get('status') != 'pass' or not verdict.get('evidence_refs'):
            raise ValueError('Unresolved semantic review cannot be published')
        approved[identity] = verdict
    by_id = {item['id']: item for item in items}
    output = {'schema_version': 1, 'revision': authoring['revision'], 'items': {}, 'provenance': []}
    seen = set()
    for row in authoring['questions']:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError('Invalid authoring record')
        identity, target = row['slot_id'], row['target_id']
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError('Duplicate question slot')
        seen.add(identity)
        verdict = approved.get(identity)
        if verdict is None or verdict.get('candidate_sha256') != digest(row):
            raise ValueError('Question or dependency bindings changed after review')
        if target not in by_id:
            raise ValueError('Unknown learning item')
        item, kind = by_id[target], row['kind']
        if kind not in KIND_ABILITIES or KIND_ABILITIES[kind] not in item['abilities']:
            raise ValueError('An exercise cannot create a memory ability')
        raw = deepcopy(row['question'])
        # The publisher owns qualification. An input draft cannot grant itself
        # a reviewed source, material qualification or altered learning owner.
        forbidden = {'practice', 'practice_source', 'material_qualification', 'personal_rubric',
                     'target_id', 'content_id', 'ability', 'kind', 'family', 'revision', 'domain'}
        if not isinstance(raw, dict) or forbidden & set(raw):
            raise ValueError('Draft contains publication-owned fields')
        if any(not isinstance(raw.get(k), str) or not raw[k].strip() for k in ('prompt', 'answer', 'feedback')):
            raise ValueError('Incomplete public exercise')
        mode = raw.get('input_mode', 'choice')
        if mode not in {'choice', 'text', 'reorder', 'multi_cloze', 'pitch_identify', 'pitch_fill'}:
            raise ValueError('Unsupported response mode')
        if not isinstance(row['context_text'], str) or not row['context_text'].strip() or not row['source_refs']:
            raise ValueError('Missing reviewed context evidence')
        backgrounds = row['background_ids']
        if not isinstance(backgrounds, list) or not set(backgrounds) <= set(by_id):
            raise ValueError('Unknown background dependencies')
        raw['practice'] = {
            'schema_version': 1, 'task_family': row['task_family'],
            'context_id': 'context:' + digest(row['context_text'])[:32],
            'scaffold': 'recognition' if mode == 'choice' else 'cued' if mode in {'reorder', 'multi_cloze'} else 'retrieval',
            'target_spans': deepcopy(row['target_spans']), 'background_ids': deepcopy(backgrounds),
            'coverage_scope': row['coverage_scope'], 'assessed_ids': deepcopy(row['assessed_ids']),
            'reviewed': True}
        validate_practice(raw, target_id=target)
        if mode == 'choice':
            choices = raw.get('choices', [])
            if not 2 <= len(choices) <= 32 or len(set(choices)) != len(choices) or raw['answer'] not in choices:
                raise ValueError('Invalid choice exercise')
        if mode == 'reorder':
            from kanamemory.personal_question import _can_order
            if not _can_order(raw.get('choices', []), raw['answer'], lambda s: ''.join(s.split())):
                raise ValueError('Word blocks do not construct the approved answer')
        if is_cloze_question(raw):
            validate_snapshot({**raw, 'target_id': target, 'ability': KIND_ABILITIES[kind],
                               'family': item['family'], 'kind': kind})
            tested = {slot['target_id'] for slot in raw['multi_cloze']['slots']}
            if tested != set(row['assessed_ids']):
                raise ValueError('Multi-gap attribution differs from its reviewed coverage')
        fingerprint = digest(raw)
        record = output['items'].setdefault(target, {'item_fingerprint': item_fingerprint(item),
            'item_revision': item['revision'], 'templates': {}, 'contexts': {}})
        templates = record['templates'].setdefault(kind, [])
        if any(digest(existing) == fingerprint for existing in templates):
            raise ValueError('Two quota slots contain the same exercise')
        templates.append(raw)
        record['contexts'][fingerprint] = {'background_glosses': []}
        output['provenance'].append({'target_id': target, 'kind': kind, 'template_sha256': fingerprint,
            'method': 'sol_exact_slot_review_0291', 'slot_id': identity,
            # Full authoring and independent review ledgers are release artifacts.
            # Their exact hashes retain provenance without copying lengthy source
            # bindings into every phone-resident question record.
            'candidate_sha256': digest(row), 'review_sha256': digest(verdict)})
    if seen != set(approved):
        raise ValueError('Review scope differs from the authored batch')
    apply_retirements(output, authoring, review, by_id)
    merged = append_history(output, previous)
    merged['coverage'] = deepcopy((previous or {}).get('coverage', {}))
    merged['coverage']['expansion_0291'] = {'new_slots': len(seen) - len(authoring.get('retirements', [])),
        'authored_slots': len(seen),
        'by_kind': dict(Counter(row['kind'] for row in authoring['questions'])),
        'reviewer': review['reviewer'], 'authoring_sha256': digest(authoring),
        'review_sha256': digest(review), 'retired_core_slots': len(authoring.get('retirements', [])),
        'claims': 'Only these exact reviewed slots; see coverage audit for retained tasks.'}
    return merged


def main():
    from kanamemory.content_pack import decode_value, namespace_document
    from kanamemory.learning_content import learning_items
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('authoring', 'review', 'previous', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    read = lambda path: json.loads(path.read_text('utf8'))
    prior = read(args.previous)
    if prior.get('name') != 'varied_practice':
        raise ValueError('Previous input is not the varied-practice namespace')
    result = compile_batch(read(args.authoring), read(args.review), learning_items(), decode_value(prior['payload']))
    document = namespace_document('varied_practice', result)
    payload_bytes = len(json.dumps(document['payload'], ensure_ascii=False, separators=(',', ':')).encode())
    if payload_bytes > 32 * 1024 * 1024:
        raise ValueError('Expanded namespace exceeds the existing content-pack limit')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', 'utf8')
    print(json.dumps({'payload_bytes': payload_bytes, **result['coverage']['expansion_0291']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
