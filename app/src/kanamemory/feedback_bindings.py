"""Exact owner/field addresses alongside immutable page snapshots.

An address is a candidate for review, not write permission or semantic evidence.
Display order and widget paths never identify a writable content owner.
"""
from copy import deepcopy
import json

from .material_workflow import digest, dump
from .tutor_store import _text
from .retention_ai import _hash

FIELDS = {
    'lesson': ('title', 'reading', 'meaning', 'instruction.summary', 'instruction.rule',
               'instruction.example', 'instruction.translation', 'instruction.contrast', 'prerequisites'),
    'example': ('text', 'translation', 'reading'),
    'lexical_example': ('text', 'chinese_translation', 'reading'),
    'reference': ('explanation', 'hint.meaning', 'hint.reading', 'hint.rule', 'link'),
    'question': ('prompt', 'task', 'answer', 'feedback', 'explanation', 'rubric'),
}
IDENTITY_FIELDS = ('id', 'revision', 'target_id', 'example_id', 'example_revision', 'family',
                   'kind', 'ability', 'script', 'content_id', 'content_revision', 'input_mode', 'personal_revision', 'personal_base_revision')
SEMANTIC_FIELDS = ('choices', 'options', 'accepted_answers', 'alternative_answers', 'spans', 'depends_on', 'question_templates')


def field_value(resource, field):
    value = resource
    for part in field.split('.'):
        if not isinstance(value, dict): return None
        value = value.get(part)
    return value


def projection(kind, resource):
    return {**{k: resource[k] for k in (*IDENTITY_FIELDS, *SEMANTIC_FIELDS) if k in resource},
            # Legacy snapshots lack the new virtual bundle: retain their
            # original byte-for-byte projection rather than appending null.
            'fields': {k: field_value(resource, k) for k in FIELDS[kind]
                       if k not in {'rubric', 'prerequisites', 'link'} or k in resource}}


def bindings_for(kind, resource, origin, *, version=None):
    if not isinstance(resource, dict) or not isinstance(resource.get('id'), str) or not resource['id']:
        return []
    if version is None and kind in {'lesson', 'reference'}:
        # Existing prose keeps its original owner hash. Only the new structured
        # field needs v3; this avoids migrating unrelated historical feedback.
        return (bindings_for(kind, resource, origin, version=1)
                + [b for b in bindings_for(kind, resource, origin, version=3) if b['field'] in {'prerequisites', 'link'}])
    version = version or (3 if kind in {'lesson', 'reference'} else 2 if kind == 'question' else 1)
    if version == 3:
        from .personal_graph import addressable
        resource = addressable(kind, resource)
    elif kind in {'lesson', 'reference'}:
        resource = deepcopy(resource)
        for field in ('prerequisites', 'link'): resource.pop(field, None)
    if kind == 'question' and version >= 2:
        from .personal_question import addressable
        resource = addressable(resource)
    elif kind == 'question' and 'rubric' in resource:
        resource = deepcopy(resource); resource.pop('rubric')
    source = projection(kind, resource); source_hash = digest(source)
    result = []
    for field, value in source['fields'].items():
        if not isinstance(value, str) or not value.strip() or len(value) > 4000: continue
        binding = {'version': version, 'resource_kind': kind, 'resource_id': resource['id'],
            'resource_revision': str(resource.get('revision') or resource.get('example_revision') or source_hash),
            'source_sha256': source_hash, 'field': field, 'value': value,
            'coordinate_system': 'unicode_codepoint', 'start': 0, 'end': len(value),
            'exact_quote': value, 'prefix': '', 'suffix': '', 'origin_sha256': origin,
            'target_id': resource.get('target_id') or (resource['id'] if kind == 'lesson' else ''),
            'example_id': resource.get('example_id', '')}
        binding['id'] = digest(binding)
        result.append(binding)
    return result


def validate_binding(value):
    fields = {'version', 'id', 'resource_kind', 'resource_id', 'resource_revision', 'source_sha256', 'field',
              'value', 'coordinate_system', 'start', 'end', 'exact_quote', 'prefix', 'suffix', 'origin_sha256', 'target_id', 'example_id'}
    if (not isinstance(value, dict) or set(value) != fields or type(value['version']) is not int or value['version'] not in {1, 2, 3}
            or value['version'] == 2 and value['resource_kind'] != 'question'
            or value['version'] == 3 and value['resource_kind'] not in {'lesson', 'reference'}
            or value['version'] == 1 and value['field'] in {'rubric', 'prerequisites', 'link'}
            or value['resource_kind'] not in FIELDS or value['field'] not in FIELDS[value['resource_kind']]
            or value['id'] != digest({k: v for k, v in value.items() if k != 'id'})
            or value['coordinate_system'] != 'unicode_codepoint' or type(value['start']) is not int or value['start'] != 0
            or not isinstance(value['value'], str)
            or type(value['end']) is not int or value['end'] != len(value['value']) or value['prefix'] or value['suffix']
            or value['exact_quote'] != value['value']):
        raise ValueError('反馈片段地址不正确。')
    _text(value['value'], 4000)
    for key in ('resource_id', 'resource_revision', 'source_sha256', 'origin_sha256'):
        _text(value[key], 512)
    for key in ('target_id', 'example_id'): _text(value[key], 512, blank=True)
    if value['resource_kind'] == 'lesson' and value['target_id'] != value['resource_id']:
        raise ValueError('学习项归属不一致。')
    if not all(_hash(value[k]) for k in ('source_sha256', 'origin_sha256')):
        raise ValueError('反馈片段指纹无效。')
    return deepcopy(value)


def intact(context, path, resource):
    """Do not promote a truncated/redacted snapshot to a whole-field address."""
    complete = context.get('completeness') or {}
    warnings = complete.get('warnings', [])
    redactions = complete.get('redactions', [])
    if complete.get('warning_count', len(warnings)) > len(warnings): return False
    if complete.get('redaction_count', len(redactions)) > len(redactions): return False
    paths = [w.get('path', '') for w in warnings if isinstance(w, dict)] + redactions
    if any(p == path or p.startswith(path + '/') or path.startswith(p + '/') for p in paths if isinstance(p, str)):
        return False
    raw = dump(resource)
    return 'capture_omitted' not in raw and '[redacted]' not in raw


def candidates(context, *, include_legacy=False):
    """Use exact snapshots only; never guess an owner from a page title or quote."""
    found = {}
    def add(kind, resource, origin, path):
        if not intact(context, path, resource): return
        for binding in bindings_for(kind, resource, origin): found.setdefault(binding['id'], binding)
        if include_legacy and kind in {'question', 'lesson', 'reference'}:
            for binding in bindings_for(kind, resource, origin, version=1): found.setdefault(binding['id'], binding)
    content = context.get('content') or {}
    if isinstance(content, dict):
        item = content.get('learning_item')
        if intact(context, 'content/current_learning_item', item):
            add('lesson', item, digest(item), 'content/learning_item')
    practice = context.get('practice') or {}
    if isinstance(practice, dict) and isinstance(practice.get('question'), dict):
        q = deepcopy(practice['question'])
        hidden = (practice.get('maintainer_only') or {}).get('hidden_solution_and_metadata', {})
        from .personal_question import BUNDLE_FIELDS
        q.update({k: v for k, v in hidden.items() if k in (*FIELDS['question'], *SEMANTIC_FIELDS, *BUNDLE_FIELDS)})
        q.setdefault('id', q.get('question_id'))
        add('question', q, digest(practice), 'practice')
    for index, wrapper in enumerate(context.get('fragments', [])):
        if not isinstance(wrapper, dict): continue
        data = wrapper.get('data', wrapper)
        if not isinstance(data, dict): continue
        origin = digest(data)
        path = f'fragments/{index}/data'
        add('lesson', data.get('item_snapshot'), origin, path + '/item_snapshot')
        if data.get('kind') in {'example_text', 'lexical_example'}:
            add('lexical_example' if data['kind'] == 'lexical_example' else 'example', data.get('example_snapshot'), origin, path + '/example_snapshot')
            add('reference', data.get('reference'), origin, path + '/reference')
            for i, reference in enumerate(data.get('linked_references') or []): add('reference', reference, origin, path + f'/linked_references/{i}')
        if isinstance(data.get('question'), dict): add('question', data['question'], origin, path + '/question')
        if isinstance(data.get('entry'), dict): add('question', data['entry'].get('question'), origin, path + '/entry/question')
    return list(found.values())


def capture_bindings(context):
    rows = candidates(context)
    return {'version': 1, 'bindings': rows[:200], 'complete': len(rows) <= 200,
            'unresolved_policy': 'Snapshots without an explicit owner are not writable addresses.'}


def current_resource(binding, *, learning=None, repo=None, examples=None):
    result = _current_resource(binding, learning=learning, repo=repo, examples=examples)
    if result is not None and binding['version'] == 3:
        from .personal_graph import addressable
        result = addressable(binding['resource_kind'], result)
    if result is not None and binding['resource_kind'] == 'lesson' and 'content_id' not in result:
        # Enrolled lesson screens include this redundant identity; old bindings
        # retain their exact hash. Do not mix scheduler state into the source.
        enrolled = {**result, 'content_id': result['id']}
        if digest(projection('lesson', enrolled)) == binding['source_sha256']:
            result = enrolled
    return result


def _current_resource(binding, *, learning=None, repo=None, examples=None):
    kind, identity = binding['resource_kind'], binding['resource_id']
    if kind == 'lesson':
        from .learning_content import get_learning_item
        item = get_learning_item(identity)
        if item is None and learning is not None:
            from .retention_sources import current_item
            item = current_item(learning.store, identity, repo=repo)
        if item is None and learning is not None and identity.startswith('local:ai:'):
            from .material_reference import trusted_item
            enrolled = learning.get_item(identity)
            if enrolled is not None: item = trusted_item(learning.store, enrolled)
        if item is not None and learning is not None:
            from .personal_content_versions import resolve as personal_version, teaching_source
            item = personal_version(learning.store, teaching_source(item))
        return item
    if kind == 'question':
        question = learning.get_question(identity) if learning is not None else None
        if question is None and learning is not None:
            # A tutor/diagnostic snapshot is an addressable question too; an
            # ambiguous reused identity must not silently select the first row.
            rows = learning.store.queries.saved_question_snapshots(identity)
            snapshots = [json.loads(row[0]) for row in rows]
            if snapshots and len({digest(projection('question', q)) for q in snapshots}) == 1:
                question = snapshots[0]
        if question is not None:
            from .personal_content_versions import resolve as personal_version
            from .personal_question import addressable
            question = personal_version(learning.store, question, kind='question')
            if binding['version'] >= 2: question = addressable(question)
        return question
    if kind == 'example': return examples.get(identity) if examples is not None else None
    if kind == 'lexical_example':
        item = repo.get_example(identity) if repo is not None else None
        if learning is not None:
            from .personal_content_versions import resolve as personal_version
            item = personal_version(learning.store, item, kind=kind)
        return item
    if kind == 'reference' and examples is not None and binding['example_id']:
        if hasattr(examples, 'get_reference'): return examples.get_reference(identity, binding['example_id'])
        return next((r for r in examples.references(binding['example_id']) if r['id'] == identity), None)
    return None


def resolve(binding, **resources):
    validate_binding(binding)
    current = current_resource(binding, **resources)
    if current is None: return {'status': 'unavailable', 'source_sha256': '', 'value': None}
    value = field_value(current, binding['field'])
    fingerprint = digest(projection(binding['resource_kind'], current))
    return {'status': 'current' if fingerprint == binding['source_sha256'] else 'changed',
            'source_sha256': fingerprint, 'value': value if isinstance(value, str) and len(value) <= 4000 else None}
