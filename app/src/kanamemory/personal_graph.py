"""Finite, reviewed relationship revisions over immutable teaching resources.

No scheduler writes belong here. A relationship is teaching context, not an
observation that the learner has mastered either end of that relationship.
"""
from copy import deepcopy
import json

from .material_workflow import digest, dump

FIELDS = {'lesson': {'prerequisites'}, 'reference': {'link'},
          'example': {'text'}, 'lexical_example': {'text'}}
LINK_FIELDS = {'active', 'target_id', 'spans', 'hint', 'explanation', 'reading_scope'}


def addressable(kind, resource):
    result = deepcopy(resource)
    if kind == 'lesson':
        result['prerequisites'] = dump(result.get('depends_on', []))
    elif kind == 'reference' and {'target_id', 'spans', 'hint'} <= set(result):
        result['link'] = dump({'active': not result.get('personal_content_unavailable', False),
            'target_id': result['target_id'], 'spans': result['spans'], 'hint': result['hint'],
            'explanation': result.get('explanation', ''), 'reading_scope': result.get('reading_scope')})
    return result


def prerequisites(text):
    try: value = json.loads(text)
    except (ValueError, TypeError) as exc: raise ValueError('前置项须为标识列表。') from exc
    if (not isinstance(value, list) or len(value) > 64
            or any(not isinstance(v, str) or not 0 < len(v) <= 512 or any(c.isspace() for c in v) for v in value)
            or len(set(value)) != len(value)):
        raise ValueError('前置项标识无效或重复。')
    return value


def link(text):
    try: value = json.loads(text)
    except (ValueError, TypeError) as exc: raise ValueError('引用须为完整的关系对象。') from exc
    if (not isinstance(value, dict) or set(value) != LINK_FIELDS or type(value['active']) is not bool
            or not isinstance(value['target_id'], str) or not value['target_id']
            or not isinstance(value['spans'], list) or not isinstance(value['hint'], dict)
            or not isinstance(value['explanation'], str) or value['reading_scope'] not in {None, 'lemma', 'span'}):
        raise ValueError('引用对象的目标、片段和提示不完整。')
    return value


def project_link(reference, value):
    result = deepcopy(reference)
    for key in LINK_FIELDS - {'active', 'reading_scope'}: result[key] = deepcopy(value[key])
    result.pop('reading_scope', None)
    if value['reading_scope'] is not None: result['reading_scope'] = value['reading_scope']
    result['personal_content_unavailable'] = not value['active']
    return result


def validate_reference(reference, example, targets):
    from .example_store import empty_pack, validate_pack
    validate_pack({**empty_pack(), 'examples': [example], 'references': [reference]}, targets)


def check_dependencies(snapshot, edits):
    """Check both current and saved routes, including reverse dependents."""
    for origin in ('catalogue', 'enrolled'):
        nodes = {n['id']: n for n in snapshot['nodes'] if n['origin'] == 'catalogue'}
        if origin == 'enrolled': nodes.update({n['id']: n for n in snapshot['nodes'] if n['origin'] == origin})
        graph = {cid: list(n['effective_depends_on']) for cid, n in nodes.items()}
        graph.update(edits)
        visited, visiting = set(), set()
        def visit(cid):
            if cid in visiting: raise ValueError('前置关系成环：' + cid)
            if cid not in nodes: raise ValueError('找不到前置学习项：' + cid)
            if cid in visited: return
            visiting.add(cid)
            for dep in graph[cid]: visit(dep)
            visiting.remove(cid); visited.add(cid)
        for cid in graph: visit(cid)


def compile_relationships(resources, group, snapshot):
    """Derived graph fields are recompiled on adoption and backup validation."""
    targets = {n['id'] for n in snapshot['nodes']}
    examples = {e['snapshot']['id']: e['snapshot'] for e in snapshot['examples'] if e['origin'] == 'visible'}
    deps = {}
    for row in resources:
        kind = row.get('kind', 'lesson')
        if kind == 'lesson' and 'prerequisites' in row['fields']:
            deps[row['id']] = prerequisites(row['fields']['prerequisites'])
        if kind == 'reference' and 'link' in row['fields']:
            value = link(row['fields']['link'])
            for op in group['operations']:
                if op['owner']['kind'] != 'reference' or op['owner']['id'] != row['id']: continue
                if op['field'].startswith('hint.'):
                    expected = value['hint'].get(op['field'].split('.')[1])
                elif op['field'] == 'explanation': expected = value['explanation']
                else: continue
                if expected != op['after']: raise ValueError('引用对象与同组提示或说明的新稿不一致。')
            base = next(r['snapshot'] for r in snapshot['references']
                        if r['origin'] == 'visible' and r['snapshot']['id'] == row['id'])
            candidate = project_link(base, value)
            validate_reference(candidate, examples[candidate['example_id']], targets)
    if deps: check_dependencies(snapshot, deps)


def project_fields(result, row, event, *, pending=False):
    kind = row.get('kind', 'lesson')
    if kind == 'lesson' and 'prerequisites' in row['fields']:
        result['depends_on'] = prerequisites(row['fields']['prerequisites'])
        result['personal_prerequisites'] = {'version_id': event['id'], 'pending': pending}
        result.pop('prerequisites', None)
    if kind == 'reference' and 'link' in row['fields']:
        result.update(project_link(result, link(row['fields']['link'])))
        result['personal_reading'] = {'version_id': event['id'], 'pending': pending, 'scope': 'reference'}
        if pending: result['personal_content_unavailable'] = True
        result.pop('link', None)
    if kind == 'lexical_example' and row.get('graph'):
        graph = row['graph']
        result['personal_shared_example_id'] = graph['example']['id']
        if row['fields'].get('reading'):
            result['reading'] = row['fields']['reading']
            result['personal_reading'] = {'version_id': event['id'], 'pending': pending, 'scope': 'sentence'}
        result.pop('order_translation', None)
        review = result.get('review')
        if review:
            matched_words = {r['entry_id'] for r in review.get('matched_associations', [])}
            associations = []
            for old in graph.get('source_review', {}).get('associations', []):
                for sense in old.get('sense_ids', []) or ['']:
                    choices = [v for v in graph['lexical_links'] if v['source']['word_id'] == old['entry_id']
                        and v['source'].get('sense_id', '') == sense
                        and v['source'].get('lexical_example_id', result['id']) == result['id']]
                    actions = {v['decision']['action'] for v in choices}
                    active = not pending and actions == {'reanchor'}
                    entry = deepcopy(old)
                    entry.update(sense_ids=[sense] if sense else [], active=active,
                        status='corrected' if active else 'rejected' if actions == {'retire'} and not pending else 'uncertain',
                        spans=deepcopy(choices[0]['decision']['spans']) if active else [])
                    associations.append(entry)
            review.update(text=result['text'], associations=associations,
                matched_associations=[a for a in associations if a['entry_id'] in matched_words],
                chinese_translation={**review.get('chinese_translation', {}), 'text': result.get('chinese_translation', '')})
            review['state'] = 'uncertain' if pending or any(a['status'] == 'uncertain' for a in associations) else 'corrected'
            review['japanese'] = {**review.get('japanese', {}), 'status': 'uncertain' if pending else 'corrected'}
            review['chinese_translation']['status'] = 'uncertain' if pending else 'corrected'
            result['review_state'] = review['state']
            result['matched_sense_ids'] = sorted({s for a in review['matched_associations'] if a['active'] for s in a['sense_ids']})
            result['matched_sense_id'] = next(iter(result['matched_sense_ids']), '')
        if not row['fields'].get('reading'):
            result.pop('personal_reading', None); result.pop('reading', None)
            if pending: result['personal_reading'] = {'version_id': event['id'], 'pending': True, 'scope': kind}


def restore_fields(result, row):
    if 'prerequisites' in row['base_fields']:
        result['depends_on'] = prerequisites(row['base_fields']['prerequisites'])
    if row.get('kind') == 'reference' and row['base_fields'].get('link'):
        result.update(project_link(result, link(row['base_fields']['link'])))
    if row.get('kind') == 'lexical_example' and row.get('graph', {}).get('source_review'):
        # Keep the call site's word filter while restoring immutable provenance.
        words = {a['entry_id'] for a in result.get('review', {}).get('matched_associations', [])}
        result['review'] = deepcopy(row['graph']['source_review'])
        if words: result['review']['matched_associations'] = [a for a in result['review']['associations'] if a['entry_id'] in words]
    for key in ('prerequisites', 'link', 'personal_prerequisites', 'personal_shared_example_id'): result.pop(key, None)


def dependency_override(learning, content):
    """Resolve from actual stored version, never trust caller-supplied markers."""
    if learning is None: return None
    store = getattr(learning, 'store', None)
    if store is None:
        from types import SimpleNamespace
        def setting(key, default=None):
            row = learning._db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default
        store = SimpleNamespace(_db=learning._db, get_setting=setting)
    from .personal_content_versions import active_version, resolve
    from .personal_content_compensation import held, holds
    cid = content.get('id', content.get('content_id'))
    event = active_version(store, cid)
    pending = held(store, cid)
    row = next((r for r in (event or {}).get('resources', [])
                if r.get('kind', 'lesson') == 'lesson' and r['id'] == cid), None)
    if row and 'prerequisites' in row['fields']:
        source = resolve(store, content)
        return {'ids': source.get('depends_on', []), 'version_id': event['id'],
                'pending': bool(pending or source.get('personal_content_unavailable'))}
    if pending and 'prerequisites' in holds(store).get(('lesson', cid), set()):
        return {'ids': content.get('depends_on', []), 'version_id': pending, 'pending': True}
    return None


def replacement_inputs(group, snapshot):
    refs = {r['origin'] + ':' + r['snapshot']['id']: r['snapshot'] for r in snapshot['references']}
    result = {}
    for op in group['operations']:
        if op['verb'] != 'replace_example': continue
        result[op['id']] = {'references': {k: refs[k] for k in op['reference_checks']},
            'lexical_links': {digest(r['link']): r['link'] for r in snapshot['lexical_links']
                              if r['binding_id'] == op['binding_id']}}
    return result


def _review_decision(decision, supporting, *, reference=False):
    keys = {'action', 'spans', 'reason', 'evidence_ids'}
    if reference: keys |= {'hint', 'explanation', 'reading_scope'}
    if (not isinstance(decision, dict) or set(decision) != keys
            or decision['action'] not in {'reanchor', 'retire', 'pending'}
            or not isinstance(decision['reason'], str) or not 0 < len(decision['reason'].strip()) <= 1600
            or not isinstance(decision['evidence_ids'], list)
            or any(not isinstance(k, str) for k in decision['evidence_ids'])
            or not set(decision['evidence_ids']) <= supporting):
        raise ValueError('请逐条决定引用或词义关联的去留，并说明依据。')
    if decision['action'] == 'reanchor' and not decision['evidence_ids']:
        raise ValueError('启用新句关联需要明确核对所选资料中的用法依据。')
    if decision['action'] != 'reanchor' and decision['spans']:
        raise ValueError('待核或撤下的关联不能同时带有已启用片段。')


def compile_replacements(resources, plan, group, support, snapshot, decisions):
    """Require an explicit disposition for every frozen reference and word link.

    Human review is attributed as such; it is never advertised as automated
    entailment or a grant for grading a newly generated question.
    """
    required = replacement_inputs(group, snapshot)
    if not isinstance(decisions, dict) or set(decisions) != set(required):
        raise ValueError('替换原句前必须逐条处理全部引用和词库关联。')
    claims = {c['binding_id']: c for c in support['claims']}
    targets = {n['id'] for n in snapshot['nodes']}
    originals = {(r['kind'], r['id']): r['snapshot'] for r in plan['manifest']['resources']}
    for op in group['operations']:
        if op['id'] not in required: continue
        decision = decisions[op['id']]; requirements = required[op['id']]
        if (not isinstance(decision, dict) or set(decision) != {'references', 'lexical_links', 'translation_review'}
                or any(not isinstance(decision[k], dict) or set(decision[k]) != set(requirements[k]) for k in requirements)):
            raise ValueError('新句的引用或词库关联决定不完整。')
        kind, owner = op['owner']['kind'], op['owner']['id']
        row = next(r for r in resources if (r.get('kind', 'lesson'), r['id']) == (kind, owner))
        source = originals[(kind, owner)]
        translation = 'chinese_translation' if kind == 'lexical_example' else 'translation'
        supporting = set(claims[op['binding_id']]['supporting_evidence_ids'])
        reviewed_translation = decision['translation_review']
        if (not isinstance(reviewed_translation, dict) or set(reviewed_translation) != {'reason', 'evidence_ids'}
                or not isinstance(reviewed_translation['reason'], str) or not 0 < len(reviewed_translation['reason'].strip()) <= 1600
                or not isinstance(reviewed_translation['evidence_ids'], list) or not reviewed_translation['evidence_ids']
                or any(not isinstance(e, str) for e in reviewed_translation['evidence_ids'])
                or not set(reviewed_translation['evidence_ids']) <= supporting):
            raise ValueError('请核对译文仍准确表达新句；需改译文时先回到字段修订。')
        example = {'id': op['replacement_id'], 'revision': 'personal:' + digest([op['id'], decision]),
            'text': op['after'], 'translation': row['fields'].get(translation, source.get(translation, '')), 'reading': row['fields'].get('reading', ''),
            'source_ids': list(source.get('source_ids', [])),
            'personal_replacement_of': owner, 'personal_evidence_method': 'user-reviewed-external-evidence-v1'}
        related_sources = {r['link']['lexical_example_id']: r['link']['lexical_source_sha256']
            for r in snapshot['lexical_links'] if r['binding_id'] == op['binding_id'] and r['link'].get('lexical_example_id')}
        related_sources.update({r['id']: r['source_sha256'] for r in snapshot.get('lexical_owners', []) if r['binding_id'] == op['binding_id']})
        lexical_ids = sorted(set(source.get('lexical_review_ids', [])) | set(related_sources))
        if kind == 'lexical_example': lexical_ids = sorted(set(lexical_ids) | {owner})
        if lexical_ids: example['lexical_review_ids'] = lexical_ids
        refs, retired, lexical = {}, set(), []
        by_old = {}
        for key, base in requirements['references'].items():
            d = decision['references'][key]; _review_decision(d, supporting, reference=True)
            revised = next((r for r in resources if r.get('kind') == 'reference' and r['id'] == base['id']), None)
            if revised and 'link' in revised['fields']:
                base = project_link(base, link(revised['fields']['link']))
                if base.get('personal_content_unavailable') and d['action'] == 'reanchor':
                    raise ValueError('同组已撤下的引用不能同时迁入新句。')
                if d['action'] == 'reanchor' and (d['hint'] != base['hint'] or d['explanation'] != base.get('explanation', '')
                        or d['reading_scope'] != base.get('reading_scope')):
                    raise ValueError('重新定位决定与同组引用的新提示或说明不一致。')
            if d['action'] == 'reanchor':
                for change in group['operations']:
                    if change['owner']['kind'] != 'reference' or change['owner']['id'] != base['id']: continue
                    field = change['field']
                    expected = d['hint'].get(field.split('.')[1]) if field.startswith('hint.') else d['explanation'] if field == 'explanation' else None
                    if expected is not None and expected != change['after']:
                        raise ValueError('重新定位决定与同组引用的字段修订不一致。')
            retired.add(base['id'])
            # The same retained/visible reference cannot receive two conflicting
            # dispositions, even when its provenance appears in both layers.
            applied = {k: v for k, v in d.items() if k not in {'reason', 'evidence_ids'}}
            if base['id'] in by_old and by_old[base['id']] != applied:
                raise ValueError('同一引用在不同来源层的处理不一致。')
            by_old[base['id']] = applied
            if d['action'] != 'reanchor': continue
            related_links = [digest(l['link']) for l in snapshot['lexical_links']
                if l['binding_id'] == op['binding_id'] and base['target_id'] in l['targets']]
            if any(decision['lexical_links'][k]['action'] != 'reanchor' for k in related_links):
                raise ValueError('共享引用与同一词义的词库关联决定不一致；待核词义不能启用教学引用。')
            ref = {'example_id': example['id'], 'example_revision': example['revision'], 'target_id': base['target_id'],
                'spans': deepcopy(d['spans']), 'hint': deepcopy(d['hint']), 'explanation': d['explanation'],
                'weight': base.get('weight', 1.0)}
            if d['reading_scope'] is not None: ref['reading_scope'] = d['reading_scope']
            from .example_store import reference_id_for
            ref['id'] = reference_id_for(ref)
            validate_reference(ref, example, targets)
            refs[ref['id']] = ref
        for key, base in requirements['lexical_links'].items():
            d = decision['lexical_links'][key]; _review_decision(d, supporting)
            if d['action'] == 'reanchor':
                # Use the same exact span validator; no spelling-based link
                # propagation from the previous sentence is permitted.
                validate_reference({'id': 'reference:lexical-check', 'example_id': example['id'],
                    'example_revision': example['revision'], 'target_id': base['word_id'],
                    'spans': d['spans'], 'hint': {'meaning': d['reason'][:300]}, 'explanation': ''}, example, None)
            lexical.append({'source': deepcopy(base), 'decision': deepcopy(d)})
        same_senses = {}
        for value in lexical:
            key = (value['source'].get('lexical_example_id', owner), value['source']['word_id'], value['source'].get('sense_id', ''))
            applied = {k: value['decision'][k] for k in ('action', 'spans')}
            if key in same_senses and same_senses[key] != applied:
                raise ValueError('同一词义在词典和审校来源中的处理不一致。')
            same_senses[key] = applied
        old_examples = {r['example_id'] for r in requirements['references'].values()}
        if kind == 'example': old_examples.add(owner)
        else:
            old_examples.update(e['snapshot']['id'] for e in snapshot['examples']
                if owner in e['snapshot'].get('lexical_review_ids', []) or any(
                    p.get('review_id') == owner or p.get('original', {}).get('example_id') == owner
                    for p in e['snapshot'].get('lexical_provenance', [])))
        original_review = row.get('graph', {}).get('source_review', source.get('review', {}))
        row['graph'] = {'example': example, 'references': list(refs.values()),
            'retired_example_ids': sorted(old_examples), 'retired_reference_ids': sorted(retired),
            'lexical_links': lexical, 'review_sha256': digest(decision), 'source_review': deepcopy(original_review)}
        row['graph']['related_lexical_sources'] = related_sources


def active_rows(store):
    from .personal_content_versions import HEAD, PREFIX, validate
    heads = {k: json.loads(v) for k, v in store._db.execute(
        'SELECT key,value_json FROM app_setting WHERE key GLOB ?', (HEAD + '*',))}
    result = []
    for identity in set(heads.values()) - {None}:
        event = validate(store.get_setting(PREFIX + identity))
        if any(heads.get(k) != identity for k in event['previous_heads']): raise ValueError('内容活动组不一致。')
        if event['version'] >= 5:
            result.extend((event, row) for row in event['resources'])
    return result


def stored_example(store, identity):
    """Archived generated sentences remain addressable after further edits."""
    from .personal_content_versions import PREFIX, validate
    for raw, in store._db.execute("SELECT DISTINCT s.value_json FROM app_setting s, "
            "json_each(s.value_json, '$.resources') r WHERE s.key GLOB ? "
            "AND json_extract(r.value, '$.graph.example.id')=? "
            "ORDER BY json_extract(s.value_json, '$.generation')", (PREFIX + '*', identity)):
        event = validate(json.loads(raw))
        for row in event['resources']:
            if row.get('graph', {}).get('example', {}).get('id') == identity: return deepcopy(row['graph']['example'])
    return None


def related_rows(store):
    """Include held sentence owners so reverting cannot reenable old links."""
    from .personal_content_compensation import HOLD, PREFIX as WITHDRAW
    from .personal_content_versions import PREFIX, validate
    rows = [(event, row, False) for event, row in active_rows(store) if row.get('graph', {}).get('related_lexical_sources')]
    for raw, in store._db.execute('SELECT DISTINCT value_json FROM app_setting WHERE key GLOB ?', (HOLD + '*',)):
        identity = json.loads(raw)
        if not identity: continue
        withdrawal = store.get_setting(WITHDRAW + identity)
        adopted = validate(store.get_setting(PREFIX + withdrawal['adoption_id']))
        rows.extend(({**adopted, 'generation': withdrawal['generation']}, row, True)
            for row in adopted['resources'] if row.get('graph', {}).get('related_lexical_sources'))
    return rows


def affected_lexical_ids(store):
    return {identity for _, row, _ in related_rows(store) for identity in row['graph'].get('related_lexical_sources', {})}


def related_lexical_projection(store, base, current):
    from .feedback_bindings import projection
    from .personal_content_versions import PREFIX
    own = store.get_setting(PREFIX + current['personal_revision']) if current.get('personal_revision') else None
    candidates = [(event, row, pending) for event, row, pending in related_rows(store)
        if base['id'] in row['graph'].get('related_lexical_sources', {})]
    if not candidates: return current
    event, row, pending = max(candidates, key=lambda r: r[0]['generation'])
    if own and own['generation'] >= event['generation'] and not pending: return current
    result = deepcopy(current)
    if digest(projection('lexical_example', base)) != row['graph']['related_lexical_sources'][base['id']]:
        result['personal_content_unavailable'] = True
        result['personal_reading'] = {'version_id': event['id'], 'pending': True, 'scope': 'sentence'}
        return result
    result['personal_related_base'] = deepcopy(base)
    graph = deepcopy(row['graph']); graph['source_review'] = deepcopy(base.get('review', {}))
    graph['lexical_links'] = [l for l in graph['lexical_links'] if l['source'].get('lexical_example_id') == base['id']]
    result['text'] = base['text'] if pending else graph['example']['text']
    translation = 'chinese_translation' if row.get('kind') == 'lexical_example' else 'translation'
    result['chinese_translation'] = base.get('chinese_translation', '') if pending else row['fields'].get(translation, graph['example']['translation'])
    fields = {'text': result['text']}
    if not pending and row['fields'].get('reading'): fields['reading'] = row['fields']['reading']; result['reading'] = fields['reading']
    project_fields(result, {'kind': 'lexical_example', 'fields': fields, 'graph': graph}, event, pending=pending)
    result['personal_related_revision'] = event['id']
    if pending: result['personal_content_unavailable'] = True
    return result


def project_pack(store, pack):
    """One current inventory feeds all forward/reverse indexes and ranking."""
    from .personal_content_versions import resolve, changed_resources
    from .personal_content_compensation import held, holds
    from .example_store import validate_pack
    examples = {e['id']: deepcopy(e) for e in pack['examples']}
    refs = {r['id']: deepcopy(r) for r in pack['references']}
    retired = {}
    changed = changed_resources(store)
    for event, row in sorted(active_rows(store), key=lambda r: r[0]['generation']):
        graph = row.get('graph')
        if not graph: continue
        pending = held(store, row['id'], row.get('kind', 'lesson'))
        new = deepcopy(graph['example'])
        new['personal_graph_version'] = event['id']
        translation = 'chinese_translation' if row.get('kind') == 'lexical_example' else 'translation'
        new['translation'] = row['fields'].get(translation, new['translation'])
        if row['fields'].get('reading'):
            new['reading'] = row['fields']['reading']
            new['personal_reading'] = {'version_id': event['id'], 'pending': bool(pending), 'scope': 'sentence'}
        for identity in graph['retired_example_ids']: retired[identity] = None if pending else new['id']
        refs = {k: r for k, r in refs.items() if k not in graph['retired_reference_ids']}
        if not pending:
            existing = examples.get(new['id'])
            if existing and existing.get('text') != new['text']: raise ValueError('新例句标识冲突。')
            examples[new['id']] = new
            refs.update({r['id']: deepcopy(r) for r in graph['references']})
    current_refs = []
    for ref in refs.values():
        if ref['example_id'] in retired: continue
        resolved = resolve(store, ref, kind='reference') if ('reference', ref['id']) in changed else ref
        if not resolved.get('personal_content_unavailable'): current_refs.append(resolved)
    # A withdrawn relation/sentence may display its old snapshot, but must not
    # silently regain teaching eligibility through the package's old indexes.
    held_resources = holds(store)
    for (kind, identity), fields in held_resources.items():
        if kind == 'example' and 'text' in fields: retired.setdefault(identity, None)
        if kind == 'lexical_example' and 'text' in fields:
            for e in examples.values():
                if identity in e.get('lexical_review_ids', []): retired.setdefault(e['id'], None)
    current_refs = [r for r in current_refs if r['example_id'] not in retired]
    current_examples = [resolve(store, e, kind='example') if ('example', e['id']) in changed else e
                        for e in examples.values() if e['id'] not in retired]
    return validate_pack({**pack, 'examples': current_examples, 'references': current_refs}), retired
