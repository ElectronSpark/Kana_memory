"""One ordering suggestion per diagnostic; frozen opportunities stay intact."""
import json

from . import app_clock
from .material_workflow import digest
from .retention_session import LIVE, BUDGETS, unit_id, latest_anchor, reservation, _proof


def identity(episode_id):
    return digest(['diagnostic-mid', episode_id])


def observation(row):
    result = row['result']
    eligible = result.get('assessment', {}).get('assessment_eligible') is True
    raw = result.get('observed_judgement', result)
    outcome = raw['outcome'] if eligible and raw['outcome'] in {'correct', 'incorrect', 'revealed'} else 'unresolved'
    return {'id': row['id'], 'target_id': row['question']['target_id'], 'ability': row['question']['ability'],
            'outcome': outcome, 'assisted': result['assisted'], 'phase': row['phase']}


def validate_session_request(request):
    from .retention_ai import _hash, _text
    session = request['session']
    if (not isinstance(session, dict) or set(session) != {'episode_id', 'permission_revision', 'signature', 'observations', 'entries'}
            or not _hash(session['episode_id']) or request['id'] != identity(session['episode_id'])
            or type(session['permission_revision']) is not int or session['permission_revision'] < 1
            or not _hash(session['signature']) or request['evidence']
            or not isinstance(session['entries'], dict)
            or set(session['entries']) != {r['id'] for r in request['candidates']}
            or len({r['target_id'] for r in request['candidates']}) != len(request['candidates'])
            or any(not isinstance(v, dict) or set(v) != {'id', 'phase'} or not _text(v['id'])
                   or v['phase'] not in {'baseline', 'transfer', 'recovery'} for v in session['entries'].values())
            or len({v['id'] for v in session['entries'].values()}) != len(session['entries'])
            or not isinstance(session['observations'], list) or not 1 <= len(session['observations']) <= 24):
        raise ValueError('会话中建议范围无效。')
    seen = set()
    for row in session['observations']:
        if (not isinstance(row, dict) or set(row) != {'id', 'target_id', 'ability', 'outcome', 'assisted', 'phase'}
                or not _text(row['id']) or row['id'] in seen or row['id'] in {v['id'] for v in session['entries'].values()}
                or row['target_id'] not in request['scope_ids'] or not _text(row['ability'])
                or row['outcome'] not in {'correct', 'incorrect', 'revealed', 'unresolved'}
                or type(row['assisted']) is not bool or row['phase'] not in {'baseline', 'transfer', 'recovery'}):
            raise ValueError('会话中作答依据无效。')
        seen.add(row['id'])


def snapshot(service, episode, enabled):
    from .retention_ai import validate_request
    from .retention_sources import current_item, trusted_question
    from .assessment_policy import assessment_decision
    from .learning_dependencies import has_started_answer
    if (not episode or episode['status'] not in LIVE or episode.get('material_id')
            or episode['used_ms'] >= BUDGETS[episode['budget']][2]):
        raise ValueError('本轮已结束或不适用会话建议。')
    observed = [observation(row) for row in episode['entries'] if row['result'] is not None]
    options, entries = [], {}
    for row in episode['entries']:
        if row['presented'] or row['completed'] or row['result'] is not None or row['draft']:
            continue
        q = row['question']; uid = unit_id(q)
        owner = reservation(service.store, uid)
        if (not owner or owner['episode_id'] != episode['id'] or owner['entry_id'] != row['id']
                or owner['question_id'] != row['course_question_id'] or latest_anchor(service.db, uid) != row['anchor_event_id']):
            continue
        if row['course_question_id']:
            pending = service.db.execute('SELECT * FROM learning_question WHERE question_id=?', (row['course_question_id'],)).fetchone()
            if (not pending or pending['status'] != 'pending' or pending['result_json'] is not None
                    or has_started_answer(json.loads(pending['draft_json']))
                    or _proof(json.loads(pending['snapshot_json'])) != row['proof']):
                continue
        unit = service.db.execute('SELECT * FROM learning_unit WHERE unit_id=?', (uid,)).fetchone()
        if (not unit or not service.presentable(row, enabled) or not trusted_question(service.store, q, repo=service.repo)
                or not assessment_decision(current_item(service.store, q['target_id'], repo=service.repo), q, purpose='check_only')['assessment_eligible']):
            continue
        item = service.learning.get_item(q['target_id'])
        cid = digest([q['target_id'], row['proof']])
        entries[cid] = {'id': row['id'], 'phase': row['phase']}
        options.append({'id': cid, 'target_id': q['target_id'], 'revision': q['revision'], 'ability': q['ability'],
            'kind': q['kind'], 'response_mode': q.get('input_mode', 'choice'), 'title': str(item['title'])[:100],
            'prompt': str(q['prompt'])[:160], 'review_count': unit['review_count'], 'proof': row['proof'],
            'anchor': latest_anchor(service.db, uid), 'course_question_id': row['course_question_id']})
    if len(options) < 2 or not observed:
        raise ValueError('继续本地练习即可；本轮尚无可调整的后续题组。')
    return validate_request({'format': 'kanamemory-diagnostic-plan', 'version': 1, 'id': identity(episode['id']),
        'scope_ids': episode['scope_ids'], 'intent': episode['intent'], 'purpose': episode['purpose'],
        'budget': episode['budget'], 'message': '', 'enabled': sorted(set(enabled)), 'candidates': options, 'evidence': [],
        'session': {'episode_id': episode['id'], 'permission_revision': episode['permission_revision'],
                    'signature': digest({k: v for k, v in episode.items() if k not in {'used_ms', 'status'}}),
                    'observations': observed, 'entries': entries}})


def create(ai, episode_id, enabled):
    with ai.db:
        ai.db.execute('BEGIN IMMEDIATE')
        old = ai.get(identity(episode_id))
        if old:
            return old  # Reopening never buys another call or changes its scope.
        request = snapshot(ai.diagnostics, ai.diagnostics.get(episode_id), enabled)
        value = {'version': 1, 'id': request['id'], 'request': request, 'request_sha256': digest(request),
                 'created_at': app_clock.utc_now().isoformat(), 'status': 'prepared', 'attempt': None,
                 'response': None, 'episode_id': None, 'error': ''}
        ai._write(value)
        return value


def apply(ai, job, enabled):
    request = job['request']; session = request['session']
    def write(episode):
        saved = ai.get(job['id'])
        if saved['status'] != 'completed' or saved['request_sha256'] != job['request_sha256']:
            raise ValueError('检查建议已经改变。')
        if snapshot(ai.diagnostics, episode, enabled) != request:
            raise ValueError('题目、作答或设置已变化，继续使用当前本地安排。')
        by_id = {row['id']: row for row in episode['entries']}
        ordered = [by_id[session['entries'][pick['candidate_id']]['id']] for pick in saved['response']['picks']]
        ordered.sort(key=lambda row: row['phase'] != 'baseline')
        slots = {r['id'] for r in session['entries'].values()}; iterator = iter(ordered)
        episode['entries'] = [next(iterator) if row['id'] in slots else row for row in episode['entries']]
        saved.update(status='applied', episode_id=episode['id'])
        ai._write(saved)  # Shares the episode transaction, including rollback.
        return episode
    return ai.diagnostics._mutate(session['episode_id'], write)


def validate_binding(db, job):
    from .retention_session import PREFIX, validate_episode
    request = job['request']; session = request['session']
    raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (PREFIX + session['episode_id'],)).fetchone()
    episode = validate_episode(json.loads(raw[0])) if raw else None
    if (not episode or episode.get('material_id') or episode['scope_ids'] != request['scope_ids']
            or episode['budget'] != request['budget'] or episode['intent'] != request['intent']
            or episode['permission_revision'] < session['permission_revision']
            or job['episode_id'] is not None and job['episode_id'] != episode['id']):
        raise ValueError('会话中建议缺少对应检查。')
    by_id = {row['id']: row for row in episode['entries']}
    for item in session['observations']:
        row = by_id.get(item['id'])
        if not row or row['result'] is None or observation(row) != item:
            raise ValueError('会话中建议与实际作答不一致。')
    for option in request['candidates']:
        binding = session['entries'][option['id']]; row = by_id.get(binding['id'])
        if (not row or row['proof'] != option['proof'] or row['question']['target_id'] != option['target_id']
                or row['phase'] != binding['phase'] or row['anchor_event_id'] != option['anchor']
                or row['question']['ability'] != option['ability'] or row['course_question_id'] != option['course_question_id']):
            raise ValueError('会话中建议与原题不一致。')
