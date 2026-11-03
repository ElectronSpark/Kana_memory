"""Versioned presentation-only diagrams, independent of stored learning data."""
from copy import deepcopy
from functools import lru_cache
import math

FORMAT = 'kanamemory-lesson-diagram'
VERSION = 1
KINDS = {'timeline', 'space', 'route', 'flow', 'contrast', 'roles', 'scale',
         'composition', 'beats', 'pitch', 'branch'}
RELATIONS = {'inside', 'outside', 'above', 'below', 'beside', 'between', 'near', 'at', 'deixis'}


def validate_diagram(value):
    def text(v):
        if not isinstance(v, str) or not v.strip() or len(v) > 200:
            raise ValueError('Diagram text must be a short nonempty string')
    def position(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
            raise ValueError('Diagram position outside axis')
    kind = value.get('kind')
    if kind not in KINDS:
        raise ValueError('Unknown diagram kind')
    text(value.get('label'))
    nodes = value.get('nodes')
    if not isinstance(nodes, list) or not 2 <= len(nodes) <= (10 if kind in {'beats', 'pitch'} else 6):
        raise ValueError('Diagram needs a bounded sequence of nodes')
    for node in nodes:
        text(node.get('text'))
        if node.get('text_role', 'ui') not in {'ui', 'study'}:
            raise ValueError('Unknown diagram text role')
        if node.get('detail'):
            text(node['detail'])
        if kind == 'timeline':
            position(node.get('at'))
    if value.get('caption'):
        text(value['caption'])
    if 'axis' in value:
        if not isinstance(value['axis'], list) or len(value['axis']) != 2:
            raise ValueError('Axis requires two endpoint labels')
        for label in value['axis']:
            text(label)
    for edge in value.get('edges', []):
        if any(type(edge.get(k)) is not int or not 0 <= edge[k] < len(nodes) for k in ('from', 'to')) or edge['from'] == edge['to']:
            raise ValueError('Invalid directed diagram relationship')
        if edge.get('text'):
            text(edge['text'])
    if kind == 'roles' and not value.get('edges'):
        raise ValueError('Roles require explicit directions')
    if 'focus' in value and (type(value['focus']) is not int or not 0 <= value['focus'] < len(nodes)):
        raise ValueError('Invalid focused node')
    if kind == 'space' and value.get('relation') not in RELATIONS:
        raise ValueError('Unknown spatial relationship')
    if kind == 'space' and value['relation'] in {'between','deixis'} and len(nodes) != 3:
        raise ValueError('This spatial diagram requires three referents')
    if value.get('action'):
        text(value['action'])
    if kind == 'timeline':
        for span in value.get('spans', []):
            position(span.get('start')); position(span.get('end'))
            if span['start'] >= span['end'] or type(span.get('lane', 0)) is not int or not 0 <= span.get('lane', 0) <= 2:
                raise ValueError('Invalid time interval')
            text(span.get('text'))
        if value.get('reference'):
            position(value['reference'].get('at')); text(value['reference'].get('text'))
    if kind == 'pitch':
        if len(value.get('levels', [])) != len(nodes) or any(type(x) is not int or x not in (0, 1) for x in value['levels']):
            raise ValueError('Pitch levels must match mora positions')
        if 'particle_from' in value and (type(value['particle_from']) is not int or
                not 0 < value['particle_from'] < len(nodes)):
            raise ValueError('Particle boundary outside mora sequence')
        if 'number' in value and (type(value['number']) is not int or
                not 0 <= value['number'] <= value.get('particle_from', len(nodes))):
            raise ValueError('Pitch number must be within the word, excluding particles')
    if any(type(i) is not int or not 0 <= i < len(nodes) for i in value.get('highlight', [])):
        raise ValueError('Highlight outside mora sequence')


@lru_cache(maxsize=1)
def catalogue():
    from . import lesson_diagrams_grammar_a as a, lesson_diagrams_grammar_b as b, lesson_diagrams_words_sounds as w
    diagrams, text_only = {}, {}
    for source in (a, b, w):
        if (diagrams.keys() | text_only.keys()) & (source.DIAGRAMS.keys() | source.TEXT_ONLY.keys()):
            raise ValueError('Duplicate diagram review identity')
        if source.DIAGRAMS.keys() & source.TEXT_ONLY.keys():
            raise ValueError('Contradictory diagram review')
        diagrams.update(source.DIAGRAMS); text_only.update(source.TEXT_ONLY)
    for diagram in diagrams.values():
        validate_diagram(diagram)
    if not all(isinstance(reason, str) and reason.strip() for reason in text_only.values()):
        raise ValueError('Explain why text is preferable')
    return diagrams, text_only


def diagram_for(identity):
    value = catalogue()[0].get(identity)
    return {**deepcopy(value), 'format': FORMAT, 'version': VERSION} if value else None


def accessible_description(diagram):
    """Include relationships, not just disconnected labels, for screen readers."""
    nodes = diagram['nodes']
    parts = [diagram['label']]
    if diagram['kind'] == 'timeline':
        parts.extend(n['text'] for n in sorted(nodes, key=lambda n: n['at']))
        parts.extend(s['text'] for s in diagram.get('spans', []))
        if diagram.get('reference'):
            parts.append('观察时点：' + diagram['reference']['text'])
    else:
        parts.extend(n['text'] for n in nodes)
    parts.extend(n['detail'] for n in nodes if n.get('detail'))
    for edge in diagram.get('edges', []):
        parts.append(nodes[edge['from']]['text'] + ' → ' + nodes[edge['to']]['text'] + '：' + edge.get('text', ''))
    if diagram['kind'] == 'space' and diagram['relation'] == 'deixis':
        parts.append('依次为靠近说话者、靠近听话者、远离双方')
    elif diagram['kind'] == 'space':
        relation = {'inside':'里面', 'outside':'外面', 'above':'上方', 'below':'下方',
                    'beside':'旁边', 'between':'之间', 'near':'附近', 'at':'所在位置'}[diagram['relation']]
        parts.append(nodes[0]['text'] + '在' + '与'.join(n['text'] for n in nodes[1:]) + relation)
    if diagram['kind'] == 'pitch':
        parts.append('音高：' + '、'.join('高' if x else '低' for x in diagram['levels']))
    parts.extend(diagram[k] for k in ('action', 'caption') if diagram.get(k))
    return '。'.join(parts)
