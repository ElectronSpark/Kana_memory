"""Exact-identity retrieval for a small tutor prompt, with current counterevidence.

No vector service or full archive upload is needed for known course identifiers.
Unknown free-text questions remain explicit questions, not invented retrieval.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

FORMAT = 'kanamemory-tutor-context'
FIELDS = {'format', 'version', 'policy', 'facts', 'evidence', 'notes', 'message_evidence'}


def build_learner_context(store, context, *, message_evidence='', message='', now=None):
    from .ai_service import public_item
    from .learning_store import LearningStore
    from .learning_evidence import LearningEvidenceStore
    from .tutor_memory import TutorMemoryStore
    ids = []
    target = context.get('target') or {}
    question = context.get('question') or {}
    from .tutor_retrieval import retrieve_question_material
    matches = retrieve_question_material(store, message, limit=2) if message.strip() else []
    for value in [question.get('target_id'), target.get('id'),
                  *(r['target_id'] for r in matches),
                  *((r.get('question') or {}).get('target_id') for r in context.get('candidates', [])[:4] if not message.strip())]:
        if isinstance(value, str) and value and value not in ids:
            ids.append(value)
    ids = ids[:4]
    facts = []
    learning = LearningStore(store)
    for identity in ids:
        item = learning.get_item(identity)
        if item:
            # public_item deliberately omits content provenance and private data.
            facts.append(public_item(item))
    evidence = LearningEvidenceStore(store).context(ids, now=now, limit=4)
    labels = {}
    for row in evidence['targets']:
        if row.get('semantic_patterns'):
            semantic = row.pop('semantic_patterns')
            abilities = {p['ability'] for p in semantic}
            row['patterns'] = [*semantic, *(p for p in row['patterns'] if p['ability'] not in abilities)]
        row['patterns'] = row['patterns'][:2]
        for pattern in row['patterns']:
            descriptions = []
            for condition in pattern.get('conditions', [])[:8]:
                identity = condition.removeprefix('with:')
                if not condition.startswith('with:'):
                    continue
                if identity not in labels:
                    related = learning.get_item(identity)
                    labels[identity] = str(related.get('title', ''))[:120] if related else ''
                if labels[identity]:
                    descriptions.append({'target_id':identity, 'title':labels[identity]})
            if descriptions:
                pattern['condition_labels'] = descriptions
            for example in pattern['examples']:
                for key in ('answer', 'answered_text', 'prompt'):
                    if key in example:
                        example[key] = example[key][:240]
    policy = {key: deepcopy(context.get('harness', {}).get(key)) for key in
              ('action', 'reason', 'policy', 'mode', 'version', 'source', 'target_id')
              if key in context.get('harness', {})}
    result = {'format': FORMAT, 'version': 1, 'policy': policy, 'facts': facts,
              'evidence': evidence, 'notes': TutorMemoryStore(store).current(ids, now=now),
              'message_evidence': message_evidence}
    if matches:
        result['retrieval'] = [{key: value for key, value in row.items() if key != 'fact'} for row in matches]
    # Recent evidence and opposing successes are not silently truncated to fit.
    # Remove optional narrative first, then optional background facts.
    while len(json.dumps(result, ensure_ascii=False).encode()) > 24000 and result['notes']:
        result['notes'].pop()
    while len(json.dumps(result, ensure_ascii=False).encode()) > 24000 and len(result['facts']) > 1:
        removed = result['facts'].pop()
        if 'retrieval' in result:
            result['retrieval'] = [row for row in result['retrieval'] if row['target_id'] != removed['id']]
    return validate_learner_context(result)


def validate_learner_context(value):
    from .ai_service import public_item
    if (not isinstance(value, dict) or set(value) - {'retrieval'} != FIELDS or value['format'] != FORMAT
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['policy'], dict) or len(value['policy']) > 7
            or not isinstance(value['facts'], list) or len(value['facts']) > 4
            or not isinstance(value['notes'], list) or len(value['notes']) > 8
            or not isinstance(value['message_evidence'], str) or len(value['message_evidence']) > 128
            or value['message_evidence'] and not value['message_evidence'].startswith('message:')):
        raise ValueError('Invalid bounded tutor context')
    if 'retrieval' in value:
        if not isinstance(value['retrieval'], list) or len(value['retrieval']) > 2:
            raise ValueError('Invalid tutor retrieval')
        for row in value['retrieval']:
            if (not isinstance(row, dict) or set(row) != {'target_id','source','match_field','match_text'}
                    or row['source'] != 'saved_learning_content'
                    or row['target_id'] not in {f['id'] for f in value['facts']}
                    or any(not isinstance(v, str) or len(v) > 256 for v in row.values())):
                raise ValueError('Invalid tutor retrieval reference')
    for fact in value['facts']:
        if fact != public_item(fact):
            raise ValueError('Invalid tutor fact')
    ev = value['evidence']
    if (not isinstance(ev, dict) or set(ev) != {'format','version','revision','targets'}
            or ev['format'] != 'kanamemory-learning-evidence' or ev['version'] != 1
            or type(ev['revision']) is not int or ev['revision'] < 0
            or not isinstance(ev['targets'], list) or len(ev['targets']) > 4):
        raise ValueError('Invalid tutor evidence')
    for target in ev['targets']:
        if (not isinstance(target, dict) or not isinstance(target.get('target_id'), str)
                or not isinstance(target.get('patterns'), list) or len(target['patterns']) > 2):
            raise ValueError('Invalid tutor pattern')
        for pattern in target['patterns']:
            if (not isinstance(pattern, dict) or not isinstance(pattern.get('examples'), list)
                    or len(pattern['examples']) > 3):
                raise ValueError('Invalid tutor evidence examples')
    for note in value['notes']:
        if (not isinstance(note, dict) or set(note) != {'claim_id','kind','text','target_ids','evidence_ids','status','source','created_at_utc'}
                or not isinstance(note['text'], str) or len(note['text']) > 240
                or note['status'] not in {'confirmed','tentative'}):
            raise ValueError('Invalid current note')
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise ValueError('Invalid tutor context encoding') from None
    if len(encoded) > 32768 or re.search(rb'sk-(?:proj-)?[A-Za-z0-9_-]{16,}', encoded):
        raise ValueError('Tutor context is too large or sensitive')
    return deepcopy(value)


def reference_sets(context):
    """Only evidence actually sent to the model may support a proposed note."""
    context = validate_learner_context(context)
    evidence = {context['message_evidence']} if context['message_evidence'] else set()
    targets = {f['id'] for f in context['facts']}
    for target in context['evidence']['targets']:
        targets.add(target['target_id'])
        for pattern in target['patterns']:
            evidence.update(e['event_id'] for e in pattern['examples'])
    return {'allowed_evidence': evidence, 'allowed_targets': targets,
            'allowed_claims': {n['claim_id'] for n in context['notes']}}
