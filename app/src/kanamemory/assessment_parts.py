"""Bounded local part observations, reduced once per target ability.

The contract belongs to the app. A repeated phrase does not create a new
learning opportunity; a disagreement without a declared primary stays unknown.
"""
from copy import deepcopy

MAX_PARTS = 16
OUTCOMES = {'correct', 'incorrect', 'revealed', 'unresolved', 'device_failure', 'invalid', 'cancelled'}


def aggregate(observations):
    if not isinstance(observations, list) or not 1 <= len(observations) <= MAX_PARTS:
        raise ValueError('Invalid assessment part count')
    groups, ids = {}, set()
    for part in observations:
        if not isinstance(part, dict):
            raise ValueError('Invalid assessment part')
        identity, unit, role = part.get('part_id'), part.get('unit'), part.get('role')
        if (not isinstance(identity, str) or not identity or len(identity) > 80 or identity in ids
                or not isinstance(unit, dict) or set(unit) != {'knowledge_id', 'ability', 'script'}
                or any(not isinstance(unit[k], str) or len(unit[k]) > 1024 for k in unit)
                or not unit['knowledge_id'] or not unit['ability']
                or not isinstance(role, str) or role not in {'primary', 'auxiliary', 'component', 'background'}
                or not isinstance(part.get('outcome'), str) or part['outcome'] not in OUTCOMES
                or type(part.get('assisted')) is not bool
                or part.get('correct') is not None and type(part['correct']) is not bool
                or part['outcome'] == 'correct' and part.get('correct') is not True
                or part['outcome'] == 'incorrect' and part.get('correct') is not False):
            raise ValueError('Invalid assessment part observation')
        ids.add(identity)
        key = tuple(unit[k] for k in ('knowledge_id', 'ability', 'script'))
        groups.setdefault(key, []).append(part)
    results = []
    for values in groups.values():
        active = [part for part in values if part['role'] != 'background']
        primaries = [part for part in active if part['role'] == 'primary']
        if len(primaries) > 1:
            raise ValueError('Only one declared primary part per ability')
        # A declared whole-answer judge owns the result even when secondary
        # components differ. Without one, inconsistent/incomplete parts do not
        # establish success or forgetting of the common ability.
        if primaries:
            chosen = primaries[0]
            outcome, correct, policy = chosen['outcome'], chosen.get('correct'), 'declared_primary'
        elif active and all(p['outcome'] == 'correct' for p in active):
            outcome, correct, policy = 'correct', True, 'unanimous'
        elif active and all(p['outcome'] == 'incorrect' for p in active):
            outcome, correct, policy = 'incorrect', False, 'unanimous'
        else:
            outcome, correct, policy = 'unresolved', None, 'unresolved'
        role = 'primary' if primaries else 'auxiliary' if any(p['role'] == 'auxiliary' for p in active) else 'exposure'
        results.append({'unit': deepcopy(values[0]['unit']), 'part_ids': [p['part_id'] for p in values],
                        'role': role, 'outcome': outcome, 'correct': correct,
                        'assisted': any(p['assisted'] for p in active), 'aggregation': policy})
    return results
