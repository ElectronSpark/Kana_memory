"""Frozen structural graph/anchor review; no semantic inference or activation."""
from copy import deepcopy
import json
from types import SimpleNamespace

from .feedback_bindings import projection
from .material_workflow import digest, dump
from .retention_ai import _hash

VERSION = 1
MAX_BYTES = 6 * 1024 * 1024


def compact_item(item, origin, *, effective=None):
    from .learning_dependencies import dependencies_of
    return {'origin': origin, 'id': item['id'], 'revision': str(item.get('revision', '')),
        'source_sha256': digest(projection('lesson', item)), 'depends_on': dependencies_of(item),
        'effective_depends_on': list(effective) if effective is not None else dependencies_of(item)}


def capture(inputs, db, tick):
    """Read-only worker snapshot. Current and enrolled routes are never merged."""
    from .learning_dependencies import effective_dependencies
    changes = [{k: deepcopy(c[k]) for k in ('binding', 'new_value')} for c in inputs['changes']]
    target_ids = {c['binding']['target_id'] for c in changes} - {''}
    example_ids = {c['binding']['resource_id'] for c in changes if c['binding']['resource_kind'] == 'example'}
    example_ids |= {c['binding']['example_id'] for c in changes} - {''}
    reference_ids = {c['binding']['resource_id'] for c in changes if c['binding']['resource_kind'] == 'reference'}
    for c in changes:
        if c['binding']['field'] == 'prerequisites':
            from .personal_graph import prerequisites
            target_ids.update(prerequisites(c['new_value']))
        elif c['binding']['field'] == 'link':
            from .personal_graph import link
            target_ids.add(link(c['new_value'])['target_id'])
    lexical = {c['binding']['resource_id'] for c in changes if c['binding']['resource_kind'] == 'lexical_example'}
    current_items = {i['id']: i for i in inputs['items']}
    current_items.update({i['id']: i for i in inputs.get('catalogue_items', [])})
    lexical_links = []
    for change in inputs['changes']:
        for link in change.get('lexical_links', []):
            tick()
            targets = sorted(i['id'] for i in current_items.values() if i.get('word_id') == link['word_id']
                and (not link.get('sense_id') or i.get('sense_id') == link['sense_id']))
            lexical_links.append({'binding_id': change['binding']['id'], 'link': deepcopy(link), 'targets': targets})
            target_ids.update(targets)
    layers = [('visible', inputs.get('pack')), ('retained', inputs.get('custom_pack'))]
    all_examples, all_refs = [], []
    unavailable = [name for name, pack in layers if pack is None and name == 'visible']
    for name, pack in layers:
        for e in (pack or {}).get('examples', []):
            tick()
            all_examples.append({'origin': name, 'snapshot': deepcopy(e), 'sha256': digest(e)})
            if lexical.intersection(e.get('lexical_review_ids', [])) or any(p.get('review_id') in lexical
                    or p.get('original', {}).get('example_id') in lexical for p in e.get('lexical_provenance', [])):
                example_ids.add(e['id'])
        for r in (pack or {}).get('references', []):
            tick(); all_refs.append({'origin': name, 'snapshot': deepcopy(r), 'sha256': digest(r)})
            if r['id'] in reference_ids or r['target_id'] in target_ids: example_ids.add(r['example_id'])
    examples = [e for e in all_examples if e['snapshot']['id'] in example_ids]
    # Current inactive references are inspectable in maintenance, but are absent
    # from teaching indexes. Freeze only an explicitly addressed current root.
    for change in inputs['changes']:
        b, resource = change['binding'], change.get('resource')
        if b['resource_kind'] == 'reference' and b['version'] == 3 and resource is not None:
            if not any(r['origin'] == 'visible' and r['snapshot']['id'] == resource['id'] for r in all_refs):
                raw = deepcopy(resource); raw.pop('link', None)
                all_refs.append({'origin': 'visible', 'snapshot': raw, 'sha256': digest(raw)})
    references = [r for r in all_refs if r['snapshot']['example_id'] in example_ids or r['snapshot']['id'] in reference_ids]
    target_ids |= {r['snapshot']['target_id'] for r in references}
    catalogue = {cid: compact_item(item, 'catalogue') for cid, item in current_items.items()}
    enrolled = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_content'").fetchone():
        for row in db.execute('SELECT content_id,content_json,stage,checkpoint_json FROM learning_content'):
            tick(); item = json.loads(row['content_json']); item.update(stage=row['stage'], checkpoint=json.loads(row['checkpoint_json']))
            from .study_queries import StudyQueries
            effective = effective_dependencies(SimpleNamespace(_db=db, queries=StudyQueries(db)), item)
            enrolled[item['id']] = compact_item(item, 'enrolled', effective=effective)
    # Undirected component over declared prerequisites includes forward chains
    # and reverse dependents. Sentence co-occurrence never becomes a prerequisite.
    neighbors = {}
    for node in [*catalogue.values(), *enrolled.values()]:
        tick()
        for dep in set(node['depends_on'] + node['effective_depends_on']):
            neighbors.setdefault(node['id'], set()).add(dep); neighbors.setdefault(dep, set()).add(node['id'])
    pending, selected = list(target_ids), set()
    while pending:
        tick(); cid = pending.pop()
        if cid in selected: continue
        selected.add(cid); pending.extend(neighbors.get(cid, set()) - selected)
    nodes = [node for collection in (catalogue, enrolled) for cid, node in sorted(collection.items()) if cid in selected]
    usage = db.execute("SELECT value_json FROM app_setting WHERE key='material_usage_v1'").fetchone()
    root_projections = [{'binding_id': c['binding']['id'], 'projection': projection(c['binding']['resource_kind'], c['resource'])
        if c.get('resource') is not None else None} for c in inputs['changes']]
    snapshot = {'version': VERSION, 'changes': changes, 'root_projections': root_projections, 'lexical_links': lexical_links,
        'examples': examples, 'references': references,
        'nodes': nodes, 'root_targets': sorted(target_ids), 'unavailable_layers': unavailable,
        'usage_sha256': digest(json.loads(usage[0]) if usage else None),
        'visible_pack_sha256': digest(inputs.get('pack')), 'retained_pack_sha256': digest(inputs.get('custom_pack'))}
    owners = [{'binding_id': c['binding']['id'], **deepcopy(owner)} for c in inputs['changes'] for owner in c.get('lexical_owners', [])]
    if owners: snapshot['lexical_owners'] = owners
    if len(dump(snapshot).encode('utf-8')) > MAX_BYTES: raise ValueError('Graph review exceeds bounded snapshot budget')
    return snapshot


def inspect(snapshot, tick=lambda: None):
    problems, references, overlaps = [], [], []
    def problem(kind, owner): problems.append({'kind': kind, 'owner': owner})
    examples = {}
    for entry in snapshot['examples']:
        tick(); e = entry['snapshot']; key = (entry['origin'], e['id'])
        if key in examples: problem('duplicate_example_identity', entry['origin'] + ':' + e['id'])
        examples[key] = e
    nodes, by_id = {}, {}
    for node in snapshot['nodes']:
        tick(); key = (node['origin'], node['id'])
        if key in nodes: problem('duplicate_target_identity', node['origin'] + ':' + node['id'])
        nodes[key] = node; by_id.setdefault(node['id'], []).append(node)
    for cid in snapshot['root_targets']:
        if cid not in by_id: problem('target_unavailable', cid)
    roots = []
    projections = {r['binding_id']: r['projection'] for r in snapshot['root_projections']}
    for change in snapshot['changes']:
        b = change['binding']; kind = b['resource_kind']; candidates = []
        actual = projections.get(b['id'])
        if kind == 'lesson': candidates = [n['source_sha256'] for n in by_id.get(b['resource_id'], [])]
        elif kind in {'example', 'reference'}:
            collection = snapshot['examples'] if kind == 'example' else snapshot['references']
            candidates = [digest(projection(kind, e['snapshot'])) for e in collection
                          if e['origin'] == 'visible' and e['snapshot']['id'] == b['resource_id']]
        else: candidates = [digest(actual)] if actual is not None else []
        if b['version'] == 3 and candidates and actual is not None:
            from .personal_graph import addressable
            if kind == 'reference':
                candidates = [digest(projection(kind, addressable(kind, e['snapshot']))) for e in snapshot['references']
                    if e['origin'] == 'visible' and e['snapshot']['id'] == b['resource_id']]
            elif kind == 'lesson':
                plain = deepcopy(actual); derived = plain['fields'].pop('prerequisites', None)
                if digest(plain) in candidates and derived == dump(plain.get('depends_on', [])):
                    candidates = [digest(actual)]
        status = ('missing' if actual is None or not candidates else 'version_mismatch'
            if digest(actual) != b['source_sha256'] or b['source_sha256'] not in candidates else 'matched')
        roots.append({'binding_id': b['id'], 'status': status})
        if status != 'matched': problem('root_' + status, b['id'])
    lexical_rows = []
    for link in snapshot['lexical_links']:
        tick(); present = bool(link['targets']) and all(cid in by_id for cid in link['targets'])
        lexical_rows.append({'binding_id': link['binding_id'], 'link_sha256': digest(link['link']), 'targets': link['targets'],
                             'status': 'mapped_to_current_graph' if present else 'outside_current_shared_graph'})
        if not present: problem('lexical_target_outside_graph', link['binding_id'] + ':' + digest(link['link']))
    changed_text = {c['binding']['resource_id']: c['new_value'] for c in snapshot['changes']
                    if c['binding']['resource_kind'] == 'example' and c['binding']['field'] == 'text'}
    lexical_text = {c['binding']['resource_id'] for c in snapshot['changes']
                    if c['binding']['resource_kind'] == 'lexical_example' and c['binding']['field'] == 'text'}
    lexical_examples = {key for key, e in examples.items() if lexical_text.intersection(e.get('lexical_review_ids', []))
        or any(p.get('review_id') in lexical_text or p.get('original', {}).get('example_id') in lexical_text
               for p in e.get('lexical_provenance', []))}
    ref_seen, segments = set(), {}
    for entry in snapshot['references']:
        tick(); r = entry['snapshot']; key = entry['origin'] + ':' + r['id']; issues = []
        if key in ref_seen: issues.append('duplicate_reference_identity')
        ref_seen.add(key)
        e = examples.get((entry['origin'], r['example_id']))
        if e is None: issues.append('example_unavailable_in_layer')
        elif e.get('revision') != r.get('example_revision'): issues.append('example_revision_mismatch')
        if r['target_id'] not in by_id: issues.append('target_unavailable')
        spans = r.get('spans')
        if not isinstance(spans, list) or not 1 <= len(spans) <= 100: issues.append('invalid_spans'); spans = []
        for index, span in enumerate(spans):
            tick(); start, end, quote = (span.get(k) for k in ('start', 'end', 'quote')) if isinstance(span, dict) else (None, None, None)
            if (e is None or type(start) is not int or type(end) is not int or not isinstance(quote, str)
                    or not 0 <= start < end <= len(e['text']) or e['text'][start:end] != quote):
                issues.append('invalid_anchor'); continue
            group = (entry['origin'], r['example_id'], r.get('example_revision'))
            segments.setdefault(group, []).append((start, end, key, index))
        if r.get('reading_scope') not in {None, 'lemma', 'span'}: issues.append('invalid_reading_scope')
        if r.get('reading_scope') and not (r.get('hint') or {}).get('reading'): issues.append('missing_scoped_reading')
        for issue in sorted(set(issues)): problem(issue, key)
        # Exact old coordinates surviving in new text are still not confirmation
        # that the same linguistic relation remains valid.
        requires = r['example_id'] in changed_text or (entry['origin'], r['example_id']) in lexical_examples
        references.append({'key': key, 'target_id': r['target_id'], 'example_id': r['example_id'],
            'original_sha256': entry['sha256'], 'status': 'invalid_structure' if issues else
                'reanchor_and_semantic_review' if requires else 'unchanged_text_anchor',
            'reading_scope': r.get('reading_scope'), 'semantic_verified': False})
    for group, spans in segments.items():
        active = []
        for start, end, key, index in sorted(spans):
            tick(); active = [p for p in active if p[1] > start]
            for left in active:
                tick()
                overlaps.append({'left': [left[2], left[3]], 'right': [key, index], 'status': 'shared_span_allowed'})
            active.append((start, end, key, index))
    routes = []
    catalogue = {cid: n for (origin, cid), n in nodes.items() if origin == 'catalogue'}
    enrolled = {cid: n for (origin, cid), n in nodes.items() if origin == 'enrolled'}
    for name, chosen, field in [('catalogue', catalogue, 'depends_on'),
            ('saved', {**catalogue, **enrolled}, 'depends_on'),
            ('effective', {**catalogue, **enrolled}, 'effective_depends_on')]:
        graph = {cid: list(n[field]) for cid, n in chosen.items()}; colors, cycles, missing = {}, [], []
        for cid, deps in graph.items():
            tick()
            missing.extend([cid, dep] for dep in deps if dep not in graph)
        for root in graph:
            if colors.get(root): continue
            stack = [(root, 0)]; path = []
            while stack:
                tick(); cid, index = stack[-1]
                if index == 0: colors[cid] = 1; path.append(cid)
                deps = graph[cid]
                if index >= len(deps): colors[cid] = 2; stack.pop(); path.pop(); continue
                dep = deps[index]; stack[-1] = (cid, index + 1)
                if dep not in graph: continue
                if colors.get(dep) == 1: cycles.append(path[path.index(dep):] + [dep])
                elif not colors.get(dep): stack.append((dep, 0))
        for cid, dep in missing: problem('missing_prerequisite', name + ':' + cid + '->' + dep)
        for cycle in cycles: problem('prerequisite_cycle', name + ':' + '->'.join(cycle))
        routes.append({'origin': name, 'nodes': len(graph), 'edges': sum(map(len, graph.values())), 'cycles': cycles, 'missing': missing})
    for layer in snapshot['unavailable_layers']: problem('layer_unavailable', layer)
    return {'roots': roots, 'lexical_links': lexical_rows, 'references': references, 'overlaps': overlaps, 'routes': routes, 'problems': problems,
        'structure_valid': not problems, 'semantic_complete': False, 'activation_eligible': False,
        'text_replacements_require_new_identity': sorted(changed_text),
        'lexical_text_identity_review': sorted(lexical_text)}


def create(inputs, db, tick):
    snapshot = capture(inputs, db, tick); result = inspect(snapshot, tick)
    value = {'version': VERSION, 'snapshot': snapshot, 'snapshot_sha256': digest(snapshot), 'result': result}
    if len(dump(value).encode('utf-8')) > MAX_BYTES: raise ValueError('Graph review result exceeds budget')
    return value


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'snapshot', 'snapshot_sha256', 'result'}
            or type(value['version']) is not int or value['version'] != VERSION or not _hash(value['snapshot_sha256'])
            or value['snapshot_sha256'] != digest(value['snapshot']) or len(dump(value).encode('utf-8')) > MAX_BYTES):
        raise ValueError('图谱审查快照无效。')
    s = value['snapshot']
    if (not isinstance(s, dict) or set(s) - {'lexical_owners'} != {'version', 'changes', 'root_projections', 'lexical_links', 'examples', 'references', 'nodes', 'root_targets',
            'unavailable_layers', 'usage_sha256', 'visible_pack_sha256', 'retained_pack_sha256'} or s['version'] != VERSION
            or not all(_hash(s[k]) for k in ('usage_sha256', 'visible_pack_sha256', 'retained_pack_sha256'))):
        raise ValueError('图谱审查缺少原始范围。')
    from .feedback_bindings import validate_binding
    for c in s['changes']: validate_binding(c['binding'])
    bindings = {c['binding']['id'] for c in s['changes']}
    if 'lexical_owners' in s:
        if not isinstance(s['lexical_owners'], list): raise ValueError('词库来源对象无效。')
        for owner in s['lexical_owners']:
            if (not isinstance(owner, dict) or set(owner) != {'binding_id', 'id', 'source_sha256'}
                    or owner['binding_id'] not in bindings or not isinstance(owner['id'], str) or not owner['id']
                    or not _hash(owner['source_sha256'])): raise ValueError('词库来源对象范围无效。')
    if (not isinstance(s['root_projections'], list) or len(s['root_projections']) != len(bindings)
            or {r.get('binding_id') for r in s['root_projections']} != bindings
            or any(set(r) != {'binding_id', 'projection'} or r['projection'] is not None and not isinstance(r['projection'], dict)
                   for r in s['root_projections'])): raise ValueError('图谱审查根实体不完整。')
    if not isinstance(s['lexical_links'], list): raise ValueError('词库原始关联无效。')
    for link in s['lexical_links']:
        if (not isinstance(link, dict) or set(link) != {'binding_id', 'link', 'targets'} or link['binding_id'] not in bindings
                or not isinstance(link['link'], dict) or not isinstance(link['link'].get('word_id'), str)
                or not isinstance(link['targets'], list) or any(not isinstance(i, str) for i in link['targets'])):
            raise ValueError('词库原始关联与图谱范围不一致。')
    for rows in (s['examples'], s['references']):
        for e in rows:
            if (not isinstance(e, dict) or set(e) != {'origin', 'snapshot', 'sha256'} or e['origin'] not in {'visible', 'retained'}
                    or e['sha256'] != digest(e['snapshot'])): raise ValueError('图谱内容层或版本不一致。')
    for n in s['nodes']:
        if (set(n) != {'origin', 'id', 'revision', 'source_sha256', 'depends_on', 'effective_depends_on'}
                or n['origin'] not in {'catalogue', 'enrolled'} or not _hash(n['source_sha256'])
                or any(not isinstance(n[k], str) for k in ('id', 'revision'))
                or any(not isinstance(n[k], list) or any(not isinstance(i, str) for i in n[k]) or len(set(n[k])) != len(n[k])
                       for k in ('depends_on', 'effective_depends_on'))): raise ValueError('前置路线版本无效。')
    count = [0]
    def bounded():
        count[0] += 1
        if count[0] > 200000: raise ValueError('图谱审查验证超出预算。')
    if inspect(s, bounded) != value['result']: raise ValueError('图谱审查结果与冻结结构不一致。')
    return deepcopy(value)
