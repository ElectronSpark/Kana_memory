"""Scoped material doubt receipts; never rewrite formal review history."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
import json

from .material_workflow import digest, dump

CHANGES = 'material_validity_change_v1:'
HISTORY = 'material_validity_history_v1:'
INDEX = 'material_validity_status_v1:'
MUTABLE = {'retracted', 'retraction_reason', 'retracted_at_utc'}


def index_key(event, target='', ability=''):
    return INDEX + digest([event, target, ability])


def _change(store, parent_key, previous):
    from .material_reference import REVIEWS, MaterialReferenceStore
    from .material_senses import PREFIX as SENSES
    from .material_workflow import PREFIX as WORKFLOWS
    parent = store.get_setting(parent_key, None)
    if parent is None: raise ValueError('材料资格变更缺少原决定。')
    if previous:
        prefix = SENSES if parent_key.startswith(SENSES) else REVIEWS
        if (store.get_setting(prefix + previous['id'], None) != previous
                or previous['proposal_id'] != parent['proposal_id']
                or parent_key.startswith(SENSES) and parent.get('previous_id') != previous['id']
                or parent_key.startswith(REVIEWS) and previous['claim']['id'] != parent['claim']['id']):
            raise ValueError('材料资格变更的前一决定不匹配。')
    proposal = MaterialReferenceStore(store).proposal(parent['proposal_id'])
    if parent_key.startswith(REVIEWS) and parent['status'] in {'pending', 'contradicted'}:
        claim = parent['claim']; kind, identity, field = claim['object_kind'], claim['object_id'], claim['facet']
        group = proposal['items'] if kind == 'item' else proposal['example_pack']['examples' if kind == 'example' else 'references']
        source = next(row for row in group if row['id'] == identity)
    elif parent_key.startswith(SENSES) and previous and previous['source'] != parent['source']:
        kind, identity, field = 'sense', parent['candidate_id'], 'identity'
        source = next(row for row in proposal['items'] if row['id'] == identity)
    else:
        raise ValueError('不支持的材料资格撤回。')
    jobs = [json.loads(raw) for (raw,) in store._db.execute('SELECT value_json FROM app_setting WHERE key GLOB ? '
        'AND json_extract(value_json,\'$.proposal_id\')=?', (WORKFLOWS + '*', proposal['id']))]
    targets = [identity]
    if kind in {'item', 'sense'}:
        index = next(i for i, row in enumerate(proposal['items']) if row['id'] == identity)
        targets.extend(job['candidate_ids'][index] for job in jobs)
    canonical = []
    for job in jobs:
        from .material_verification import PREFIX as REPORTS
        report = store.get_setting(REPORTS + job['id'], {})
        canonical.extend(row['canonical_id'] for row in report.get('identities', [])
                         if row['candidate_object_id'] == identity and row.get('canonical_id'))
    if previous and kind == 'sense': canonical.append(previous['source']['object_id'])
    value = {'version': 1, 'parent_key': parent_key, 'previous': deepcopy(previous), 'proposal_id': proposal['id'],
        'proposal_sha256': parent['proposal_sha256'], 'kind': kind, 'object_id': identity, 'field': field,
        'source': deepcopy(source), 'targets': sorted(set(targets)), 'canonical_targets': sorted(set(canonical)),
        'workflow_ids': [job['id'] for job in jobs], 'created_at': parent['at']}
    value['id'] = digest(value)
    return value


def verdict(change, question, *, material_id=None, history=False):
    """Local identity plus exact provenance; independent canonical work stays."""
    if not isinstance(question, dict): return None
    target = question.get('target_id', question.get('content_id', ''))
    binding = question.get('material_qualification') or {}
    material_id = binding.get('workflow_id') or material_id
    own_material = material_id in change['workflow_ids']
    field, source, kind = change['field'], change['source'], change['kind']
    if kind in {'item', 'sense'}:
        direct = target == change['object_id']
        scoped = own_material and (target in change['targets'] or target in change['canonical_targets']
                                   or binding.get('candidate_object_id') == change['object_id'])
        if not direct and not scoped: return None
        if kind == 'sense': return 'ambiguous'
        if not history: return 'ambiguous'
        if binding.get('candidate_object_id') == change['object_id'] and field in binding.get('facets', {}):
            return 'invalid' if binding['facets'][field] == digest(source.get(field, '')) else None
        if not direct:
            # Material-only canonical checks are observations; this receipt
            # cannot invalidate independent canonical course achievements.
            return 'ambiguous' if field in {'title', 'meaning', 'reading', 'question_templates'} else None
        from .question_dependencies import KEY, matching_uses, factory_matches
        roles = set()
        from .feedback_bindings import field_value
        for entry in question.get(KEY, {}).get('entries', []):
            if entry['field'] == field or entry['field'].startswith(field + '.'):
                b = {'resource_kind': 'lesson', 'resource_id': source['id'], 'resource_revision': str(source['revision']),
                     'field': entry['field'], 'value': field_value(source, entry['field'])}
                roles.update(matching_uses(question, b, source))
        if roles and roles <= {'after_answer'}: return None
        if roles & {'rubric', 'stimulus', 'hint', 'optional_hint'}: return 'invalid'
        if field in {'instruction', 'depends_on'}: return None
        if field == 'question_templates' and factory_matches(question, source): return 'invalid'
        return 'ambiguous'
    practice = question.get('practice') or {}
    example_id = source['example_id'] if kind == 'reference' else source['id']
    if example_id not in {practice.get('example_id'), question.get('learning_condition_source')}:
        return None
    if kind == 'reference' and target != source['target_id'] and source['id'] not in practice.get('reference_ids', []):
        return None
    return 'ambiguous'


def _aliases(row):
    result = [row['event_id']]
    if row['event_id'].startswith('course:'): result.append(row['event_id'][7:])
    opportunity = json.loads(row['snapshot_json']).get('opportunity', {}).get('id')
    if opportunity: result.append(opportunity)
    return sorted(set(result))


def apply_in_transaction(store, parent_key, previous):
    db = store._db
    if not db.in_transaction: raise RuntimeError('Material validity requires the decision transaction')
    change = _change(store, parent_key, previous)
    if store.get_setting(CHANGES + change['id'], None): return
    db.execute('INSERT INTO app_setting VALUES (?,?)', (CHANGES + change['id'], dump(change)))
    from .learning_evidence import tables_present, LearningEvidenceStore
    if tables_present(db):
        # This mutation-time scan is not on rendering, handwriting or scoring.
        evidence = LearningEvidenceStore(store)
        scope = sorted(set(change['targets'] + change['canonical_targets']))
        sql = 'SELECT * FROM learning_evidence WHERE retracted=0'
        args = []
        if change['kind'] in {'item', 'sense'}:
            sql += ' AND target_id IN (' + ','.join('?' for _ in scope) + ')'; args = scope
        for raw in db.execute(sql, args):
            row = dict(raw); snap = json.loads(row['snapshot_json']); q = snap.get('assessment_question')
            if q is None:
                saved = db.execute('SELECT snapshot_json FROM learning_question WHERE question_id=?', (snap.get('question_id'),)).fetchone()
                q = json.loads(saved[0]) if saved else None
            episode = snap.get('episode_id', '').partition(':')[2]
            context = store.get_setting('retention_check_v2:' + episode, {}) if episode else {}
            decision = verdict(change, q, material_id=context.get('material_id'), history=True)
            if decision is None and q is None and row['target_id'] == change['object_id'] and change['kind'] == 'item':
                decision = 'ambiguous'
            if decision is None: continue
            immutable = {k: v for k, v in row.items() if k not in MUTABLE}
            receipt = {'version': 1, 'change_id': change['id'], 'event_id': row['event_id'],
                'target_id': row['target_id'], 'ability': row['ability'], 'source_sha256': digest(immutable),
                'question': q, 'material_id': context.get('material_id'), 'verdict': decision, 'aliases': _aliases(row)}
            receipt['id'] = digest(receipt)
            db.execute('INSERT INTO app_setting VALUES (?,?)', (HISTORY + receipt['id'], dump(receipt)))
            for alias in receipt['aliases']:
                for key in {index_key(alias, row['target_id'], row['ability']), index_key(alias, row['target_id'])}:
                    db.execute('INSERT OR IGNORE INTO app_setting VALUES (?,?)', (key, dump(receipt['id'])))
            evidence.retract_in_transaction(row['event_id'], 'material_validity:' + receipt['id'],
                                            now=datetime.fromisoformat(change['created_at']))
    from .content_queue import retire_material_in_transaction
    retire_material_in_transaction(store, change)


def blocked_question(store, item, question):
    from .assessment_policy import _ai_origin
    if not _ai_origin(item, question): return False
    from .material_reference import question_ready
    return not question_ready(store, item, question)


def validate_database(db):
    def setting(key, default=None):
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    store = SimpleNamespace(_db=db, get_setting=setting)
    changes, expected_index = {}, {}
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (CHANGES + '*',)):
        value = json.loads(raw)
        if key != CHANGES + value.get('id', '') or value != _change(store, value['parent_key'], value['previous']):
            raise ValueError('材料失效记录缺少原决定或具体来源。')
        changes[value['id']] = value
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ? ORDER BY rowid', (HISTORY + '*',)):
        r = json.loads(raw); change = changes.get(r.get('change_id'))
        row = db.execute('SELECT * FROM learning_evidence WHERE event_id=?', (r.get('event_id'),)).fetchone()
        if (not change or not row or key != HISTORY + r.get('id', '')
                or r['id'] != digest({k:v for k,v in r.items() if k != 'id'})
                or r['source_sha256'] != digest({k:v for k,v in dict(row).items() if k not in MUTABLE})
                or (r['target_id'], r['ability']) != (row['target_id'], row['ability'])
                or not row['retracted'] or row['retraction_reason'] != 'material_validity:' + r['id']
                or r['aliases'] != _aliases(row)):
            raise ValueError('材料贡献撤回与原作答不一致。')
        snap = json.loads(row['snapshot_json'])
        episode_id = snap.get('episode_id', '').partition(':')[2]
        episode = setting('retention_check_v2:' + episode_id, {}) if episode_id else {}
        if r['material_id'] != episode.get('material_id'):
            raise ValueError('材料贡献撤回不能改变原会话归属。')
        original_question = snap.get('assessment_question')
        if original_question is None:
            saved = db.execute('SELECT snapshot_json FROM learning_question WHERE question_id=?', (snap.get('question_id'),)).fetchone()
            original_question = json.loads(saved[0]) if saved else None
        if original_question != r['question']:
            raise ValueError('材料撤回不能替换原题。')
        decision = verdict(change, original_question, material_id=episode.get('material_id'), history=True)
        if r['question'] is None and row['target_id'] == change['object_id'] and change['kind'] == 'item': decision = 'ambiguous'
        if decision != r['verdict']: raise ValueError('材料贡献撤回范围不匹配。')
        for alias in r['aliases']:
            for k in {index_key(alias, r['target_id'], r['ability']), index_key(alias, r['target_id'])}:
                expected_index.setdefault(k, r['id'])
    actual = {k: json.loads(v) for k,v in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (INDEX + '*',))}
    if actual != expected_index: raise ValueError('材料贡献有效性索引不一致。')
    return changes
