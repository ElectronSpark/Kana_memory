"""Explicit replacement of a retired, unanswered diagnostic opportunity."""
from copy import deepcopy
from datetime import datetime
import json
from uuid import uuid4

from . import app_clock
from .feedback_impact import token
from .material_workflow import digest, dump

PREFIX = 'diagnostic_replacement_v1:'


def key(episode_id, entry_id): return PREFIX + digest([episode_id, entry_id])


def find_entry(store, episode, entry_id, *, question_id=None):
    """Old guards/handoffs continue to address the archived original entry."""
    entry = next((r for r in episode['entries'] if r['id'] == entry_id), None)
    if entry and (question_id is None or entry['question']['id'] == question_id): return entry
    value = store.get_setting(key(episode['id'], entry_id), None)
    if value:
        old = value['before']
        if question_id is None or old['question']['id'] == question_id: return deepcopy(old)
    return None


def replace(service, identity, entry_id, enabled_kinds, *, now=None):
    from .retention_session import LIVE, unit_id, reservation
    from .retention_check import _proof
    from .learning_replacement import LearningReplacementStore
    from .retention_sources import current_item
    store, db = service.store, service.db
    prior = store.get_setting(key(identity, entry_id), None)
    if prior: return service.get(identity)
    before_token = token(db); episode = service.get(identity)
    if not episode or episode['status'] not in LIVE: raise ValueError('请先恢复这轮检查。')
    old = service._entry(episode, entry_id)
    if old['result'] is not None or not old.get('content_retirement'):
        raise ValueError('只能更换因内容修订而保留的未答题。')
    if reservation(store, unit_id(old['question'])) is not None:
        raise ValueError('这项能力已有其他正在进行的题目，请先完成。')
    from .content_queue import PREFIX as RETIREMENTS
    material_retirement = store.get_setting(RETIREMENTS + old['content_retirement'], {}).get('version') == 2
    stamp = max(now or app_clock.utc_now(), datetime.fromisoformat(episode['created_at_utc']))
    replacer = None; course_plan = None
    if old['course_question_id']:
        replacer = LearningReplacementStore(service.learning)
        course_plan = replacer.prepare_question(old['course_question_id'], now=stamp,
            diagnostic_retirement=old['content_retirement'])
        event = course_plan.get('event') or course_plan.get('saved')
        q = deepcopy(event['question']); source = event['source']
        if q['kind'] not in enabled_kinds: raise ValueError('替代题型当前已关闭。')
    else:
        choices = [q for q in service._candidates(old['question']['target_id'], enabled_kinds,
            material_id=episode.get('material_id')) if q['ability'] == old['question']['ability']
            and (material_retirement or _proof(q) != old['proof'])]
        if not choices: raise ValueError('当前内容或题型下尚无合格替代题，原题与草稿继续保留。')
        q = deepcopy(choices[0])
        source = (service._material_items(episode['material_id']).get(q['target_id']) if episode.get('material_id')
                  else current_item(store, q['target_id'], repo=service.repo))
        if q.get('material_qualification'):
            from .material_qualification import candidate_basis
            source = {'material_candidate_basis': next(b for b in candidate_basis(store, episode['material_id'],
                repo=service.repo, examples=service.examples) if b['item']['id'] == q['target_id'])}
    new_id = 'diagnostic:' + identity + ':r' + uuid4().hex[:16]
    if not old['course_question_id']: q['id'] = new_id
    fresh = {**deepcopy(old), 'id': new_id, 'question': q, 'proof': _proof(q), 'presented': False,
        'draft': {k: True for k in ('hint_used',) if old['draft'].get(k)}, 'result': None, 'completed': False,
        'course_question_id': q['id'] if old['course_question_id'] else None, 'settlement': None,
        'credit_revoked': old['credit_revoked'] or old['presented'] and old['purpose'] == 'check_only',
        'replacement_of': old['id'], 'opportunity_root': old.get('opportunity_root', old['id'])}
    fresh.pop('content_retirement')
    if material_retirement and _proof(q) == old['proof']:
        fresh['draft'] = deepcopy(old['draft'])
    receipt = {'version': 1, 'episode_id': identity, 'created_at': stamp.isoformat(), 'before': deepcopy(old),
        'after': deepcopy(fresh), 'source': source, 'course_replacement_id':
        ((course_plan.get('event') or course_plan.get('saved'))['id'] if course_plan else None)}
    receipt['id'] = digest(receipt)
    if before_token != token(db): raise ValueError('准备替代期间记录已变化，请重试。')

    def activate(_course_event=None):
        current = service.get(identity)
        index = next((i for i, r in enumerate(current['entries']) if r['id'] == entry_id), None)
        if index is None or current['entries'][index] != old or current['status'] not in LIVE:
            raise ValueError('原检查题已变化。')
        if reservation(store, unit_id(q)) is not None: raise ValueError('替代前预约已变化。')
        if old['course_question_id']:
            # A previously issued receipt is not permission to borrow the
            # replacement again: the course may already have consumed it.
            from .retention_session import latest_anchor, handoff
            from .retention_sources import trusted_question
            row = db.execute('SELECT * FROM learning_question WHERE question_id=?', (q['id'],)).fetchone()
            if (event.get('diagnostic_retirement') != old['content_retirement'] or not row
                    or row['status'] != 'pending' or row['result_json'] is not None
                    or row['unit_id'] != unit_id(q)
                    or db.execute('SELECT 1 FROM learning_event WHERE question_id=?', (q['id'],)).fetchone()
                    or latest_anchor(db, unit_id(q)) != old['anchor_event_id']
                    or handoff(store, q['id']) is not None
                    or any(k not in {'assisted', 'hint_used', 'answer_revealed'} for k in json.loads(row['draft_json']))
                    or not trusted_question(store, q, repo=service.repo)
                    or not service.presentable(fresh, enabled_kinds, material_id=episode.get('material_id'))):
                raise ValueError('替代题已在其他入口开始或完成，或内容已变化。请从原入口继续，或结束本轮检查；原记录保留。')
        current['entries'][index] = fresh
        db.execute('INSERT INTO app_setting VALUES (?,?)', (key(identity, entry_id), dump(receipt)))
        service._reserve(current, fresh)
        service._write(current)

    if replacer and 'saved' not in course_plan:
        replacer.commit(course_plan, _after_commit=activate)
    else:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if before_token != token(db): raise ValueError('替代前记录已变化，请重试。')
            activate()
    return service.get(identity)


def validate_database(db):
    from types import SimpleNamespace
    from .retention_session import PREFIX as EPISODES, validate_episode
    from .retention_check import _proof
    from .learning_replacement import question_receipt
    from .content_queue import archived
    from .question_dependencies import factory_matches
    def setting(name, default=None):
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (name,)).fetchone()
        return json.loads(row[0]) if row else default
    store = SimpleNamespace(_db=db, get_setting=setting); seen = set(); replacements = {}; historical = []
    for k, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ? ORDER BY rowid', (PREFIX + '*',)):
        r = json.loads(raw)
        if (set(r) != {'version', 'id', 'episode_id', 'created_at', 'before', 'after', 'source', 'course_replacement_id'}
                or r['version'] != 1 or r['id'] != digest({k:v for k,v in r.items() if k != 'id'})
                or datetime.fromisoformat(r['created_at']).tzinfo is None):
            raise ValueError('检查替代回执格式无效。')
        old, new = r['before'], r['after']; q = new['question']
        episode = setting(EPISODES + r['episode_id'])
        retired = archived(store, old.get('content_retirement', ''))
        from .content_queue import PREFIX as RETIREMENTS
        retirement = setting(RETIREMENTS + old.get('content_retirement', '')) or {}
        carried_draft = (deepcopy(old['draft']) if retirement.get('version') == 2 and _proof(q) == old['proof']
                         else {k:True for k in ('hint_used',) if old['draft'].get(k)})
        expected_old = {**retired, 'content_retirement': old['content_retirement']}
        if (k != key(r['episode_id'], old['id']) or old != expected_old or old['result'] is not None
                or not episode or new['id'] == old['id'] or new['id'] in seen
                or new.get('replacement_of') != old['id']
                or new.get('opportunity_root') != old.get('opportunity_root', old['id'])
                or (q['target_id'], q['ability'], new['phase'], new['purpose'], new['anchor_event_id'])
                    != (old['question']['target_id'], old['question']['ability'], old['phase'], old['purpose'], old['anchor_event_id'])
                or new['proof'] != _proof(q) or new['draft'] != carried_draft
                or new['exposed'] != old['exposed'] or new['presented'] or new['completed'] or new['result'] is not None
                or new['credit_revoked'] != (old['credit_revoked'] or old['presented'] and old['purpose'] == 'check_only')):
            raise ValueError('检查替代丢失原机会、帮助或用途。')
        if old['course_question_id']:
            course = question_receipt(store, old['course_question_id'])
            if (not course or course['id'] != r['course_replacement_id'] or course['question'] != q
                    or course.get('diagnostic_retirement') != old['content_retirement']):
                raise ValueError('检查替代与课程替代不同步。')
        else:
            from .material_qualification import frozen_factory_matches
            if r['course_replacement_id'] is not None or not (factory_matches(q, r['source']) or frozen_factory_matches(store, q, r['source'])):
                raise ValueError('检查替代不属于已核对的题目来源。')
        current = find_entry(store, episode, new['id'], question_id=q['id'])
        immutable = ('id', 'question', 'proof', 'phase', 'course_question_id', 'anchor_event_id', 'replacement_of', 'opportunity_root')
        if not current or any(current.get(f) != new.get(f) for f in immutable):
            raise ValueError('检查替代的当前或后继题目缺失。')
        seen.add(new['id']); validate_episode(episode)
        replacements[(episode['id'], new['id'])] = r
        historical.extend((episode['id'], entry) for entry in (old, new))
    # A marker without a receipt must not be allowed to select someone else's
    # opportunity. Check both current entries and archived predecessors, and
    # derive the root from the chain rather than trusting the saved root text.
    for raw, in db.execute('SELECT value_json FROM app_setting WHERE key GLOB ?', (EPISODES + '*',)):
        episode = validate_episode(json.loads(raw))
        historical.extend((episode['id'], entry) for entry in episode['entries'])
    for episode_id, entry in historical:
        if not entry.get('replacement_of') and not entry.get('opportunity_root'): continue
        cursor = entry; visited = set()
        while cursor.get('replacement_of'):
            identity = (episode_id, cursor['id'])
            receipt = replacements.get(identity)
            if identity in visited or not receipt or receipt['before']['id'] != cursor['replacement_of']:
                raise ValueError('检查替代缺少唯一的完整回执链。')
            visited.add(identity); cursor = receipt['before']
        if cursor.get('opportunity_root') or entry.get('opportunity_root') != cursor['id']:
            raise ValueError('检查替代的根机会与回执链不一致。')
