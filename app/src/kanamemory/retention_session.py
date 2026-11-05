"""Bounded diagnostic sessions sharing real course opportunities.

The session owns presentation, permission and active-time accounting. Only the
ordinary course settlement can alter FSRS, under the same database transaction.
Legacy check-only episodes remain frozen in their separate v1 namespace.
"""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json

from . import app_clock
from .assessment_contract import observations, validate_result, for_question
from .assessment_parts import aggregate
from .assessment_policy import assessment_decision
from .retention_check import RetentionCheckStore, _date, _proof, EXPOSURE_PREFIX, validate_exposure

PREFIX = 'retention_check_v2:'
RESERVATION_PREFIX = 'retention_reservation_v1:'
HANDOFF_PREFIX = 'retention_handoff_v1:'
ACTIVE_KEY = 'retention_active_v2'
FORMAT = 'kanamemory-retention-session'
BUDGETS = {'quick': (6, 10, 300000), 'deep': (12, 20, 600000)}
PURPOSES = {'check_only', 'check_and_adjust'}
LIVE = {'active', 'paused'}
TERMINAL = {'completed', 'stopped', 'budget'}


def _dump(value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    if len(data.encode('utf-8')) > 1024 * 1024:
        raise ValueError('检查记录过大。')
    return data


def _identity(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _text(value, limit=1024):
    return isinstance(value, str) and 0 < len(value) <= limit


def unit_id(question):
    from .learning_store import _unit_id
    return _unit_id(question['target_id'], question['ability'])


def validate_reservation(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'unit_id', 'episode_id', 'entry_id', 'question_id'}
            or type(value['version']) is not int or value['version'] != 1
            or not _identity(value['unit_id']) or not _identity(value['episode_id'])
            or not _text(value['entry_id'])
            or value['question_id'] is not None and not _text(value['question_id'])):
        raise ValueError('Invalid diagnostic reservation')
    return value


def handoff_key(question_id):
    return HANDOFF_PREFIX + sha256(question_id.encode()).hexdigest()


def validate_handoff(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'episode_id', 'entry_id', 'question_id'}
            or type(value['version']) is not int or value['version'] != 1
            or not _identity(value['episode_id']) or not _text(value['entry_id'])
            or not _text(value['question_id'])):
        raise ValueError('Invalid diagnostic handoff')
    return value


def handoff(store, question_id):
    value = store.get_setting(handoff_key(question_id), None)
    return validate_handoff(value) if value is not None else None


def enabled_question(store, question, enabled=None):
    current = store.get_setting('learning_preferences_v1', {})
    selected = current.get('enabled') if isinstance(current, dict) else None
    return ((enabled is None or question['kind'] in enabled)
            and (not isinstance(selected, list) or question['kind'] in selected))


def validate_episode(value):
    fields = {'format', 'version', 'id', 'status', 'created_at_utc', 'purpose', 'intent',
              'scope_ids', 'budget', 'used_ms', 'permission_revision', 'entries', 'blocked'}
    if (not isinstance(value, dict) or set(value) not in (fields, fields | {'material_id'}, fields | {'source_guards'}) or value['format'] != FORMAT
            or type(value['version']) is not int or value['version'] != 2 or not _identity(value['id'])
            or value['status'] not in LIVE | TERMINAL or value['purpose'] not in PURPOSES
            or value['intent'] not in {'active', 'reentry', 'material'} or value['budget'] not in BUDGETS
            or type(value['used_ms']) is not int or not 0 <= value['used_ms'] <= BUDGETS[value['budget']][2]
            or type(value['permission_revision']) is not int or value['permission_revision'] < 1):
        raise ValueError('Invalid diagnostic session')
    if 'source_guards' in value and value['source_guards'] is not True:
        raise ValueError('Invalid diagnostic source guard version')
    if 'material_id' in value and (not _identity(value['material_id']) or value['intent'] != 'material'
                                   or value['purpose'] != 'check_only'):
        raise ValueError('Invalid material diagnostic scope')
    _dump(value); _date(value['created_at_utc'])
    scope = value['scope_ids']
    if (not isinstance(scope, list) or not 1 <= len(scope) <= BUDGETS[value['budget']][0]
            or any(not _text(cid) for cid in scope) or len(scope) != len(set(scope))
            or not isinstance(value['entries'], list) or len(value['entries']) > 2 * len(scope)
            or not isinstance(value['blocked'], list) or len(value['blocked']) > len(scope)):
        raise ValueError('Invalid diagnostic scope')
    seen, targets, course_ids, formal_units = set(), {}, set(), set()
    for row in value['entries']:
        required = {'id', 'question', 'proof', 'phase', 'purpose', 'presented', 'exposed', 'draft',
                    'result', 'completed', 'course_question_id', 'anchor_event_id', 'settlement', 'credit_revoked'}
        if not isinstance(row, dict) or set(row) - {'content_retirement', 'replacement_of', 'opportunity_root'} != required:
            raise ValueError('Invalid diagnostic entry')
        if 'content_retirement' in row and (not _identity(row['content_retirement']) or row['result'] is not None or row['completed']):
            raise ValueError('Invalid retired diagnostic entry')
        if ('replacement_of' in row) != ('opportunity_root' in row) or any(
                not _text(row[k]) for k in ('replacement_of', 'opportunity_root') if k in row):
            raise ValueError('Invalid replacement diagnostic opportunity')
        if value.get('material_id') and row['purpose'] != 'check_only':
            raise ValueError('Material pretest cannot authorize scheduling')
        q = row['question']
        if isinstance(q, dict) and (q.get('material_qualification') or str(q.get('target_id', '')).startswith('material-candidate:')):
            from .material_qualification import validate_binding
            validate_binding(q)
        if (not isinstance(q, dict) or any(not _text(q.get(k)) for k in ('id', 'target_id', 'ability', 'kind'))
                or not isinstance(q.get('answer'), str) or q['target_id'] not in scope
                or not _text(row['id']) or not row['id'].startswith('diagnostic:' + value['id'] + ':')
                or row['id'] in seen or row['proof'] != _proof(q)
                or row['phase'] not in {'baseline', 'transfer', 'recovery'} or row['purpose'] not in PURPOSES
                or any(type(row[k]) is not bool for k in ('presented', 'exposed', 'completed', 'credit_revoked'))
                or not isinstance(row['draft'], dict) or set(row['draft']) - {'answer', 'audio_ok', 'hint_used', 'pitch_state', 'used', 'cloze_state'}
                or not isinstance(row['draft'].get('answer', ''), str) or len(row['draft'].get('answer', '')) > 10000
                or any(type(row['draft'][k]) is not bool for k in ('audio_ok', 'hint_used') if k in row['draft'])
                or row['anchor_event_id'] is not None and not _text(row['anchor_event_id'])):
            raise ValueError('Invalid diagnostic question or draft')
        from .pitch_practice import validate_draft
        validate_draft(q, row['draft'])
        from .multi_cloze import validate_snapshot as validate_cloze_snapshot, validate_draft as validate_cloze_draft
        validate_cloze_snapshot(q)
        validate_cloze_draft(q, row['draft'])
        used = row['draft'].get('used')
        if used is not None:
            if (q.get('input_mode') != 'reorder' or not isinstance(used, list) or len(used) != len(set(used))
                    or any(type(i) is not int or not 0 <= i < len(q.get('choices', [])) for i in used)
                    or row['draft'].get('answer') != ''.join(q['choices'][i] for i in used)):
                raise ValueError('Invalid diagnostic word-block draft')
        seen.add(row['id'])
        prior = targets.setdefault(q['target_id'], [])
        if len(prior) >= 2 or (not prior) != (row['phase'] == 'baseline'):
            raise ValueError('A target has one baseline and at most one follow-up')
        if prior and (q['ability'] != prior[0]['question']['ability'] or not row['exposed']):
            raise ValueError('A follow-up must retain its ability and exposure')
        prior.append(row)
        cqid = row['course_question_id']
        if cqid is not None:
            if not _text(cqid) or cqid != q['id'] or cqid in course_ids or row['phase'] != 'baseline':
                raise ValueError('Invalid shared course opportunity')
            course_ids.add(cqid)
        elif q['id'] != row['id']:
            raise ValueError('Invalid local diagnostic question identity')
        result = row['result']
        if result is not None:
            if not row['presented'] or not isinstance(result, dict):
                raise ValueError('An unpresented question cannot have an answer')
            validate_result(q, result)
            from .material_review import fingerprint
            contract = result.get('assessment_contract')
            if (not isinstance(contract, dict) or contract.get('version') != 1
                    or contract.get('sha256') != fingerprint({k: v for k, v in contract.items() if k != 'sha256'})
                    or not isinstance(result.get('observations'), list) or not isinstance(result.get('unit_observations'), list)
                    or result.get('observations') != observations(q, result)):
                raise ValueError('Incomplete diagnostic assessment contract')
            raw = result.get('observed_judgement', result)
            if (raw.get('outcome') == 'correct' and raw.get('correct') is not True
                    or raw.get('outcome') == 'incorrect' and raw.get('correct') is not False
                    or raw.get('outcome') not in {'correct', 'incorrect'} and raw.get('correct') is not None):
                raise ValueError('Contradictory diagnostic judgement')
            if (result.get('assessment', {}).get('purpose') != row['purpose']
                    or result.get('outcome') not in {'correct', 'incorrect', 'revealed', 'unresolved', 'cancelled', 'device_failure', 'invalid'}
                    or result.get('correct') is not None and type(result['correct']) is not bool
                    or type(result.get('assisted')) is not bool
                    or not isinstance(result.get('answered_text'), str) or len(result['answered_text']) > 10000):
                raise ValueError('Invalid diagnostic first answer')
            if cqid is None and (result.get('scheduled') is not False
                    or result['assessment'].get('settlement_channel') != 'observation'):
                raise ValueError('Local observations cannot authorize scheduling')
            if value.get('material_id') and (result.get('scheduled') is not False
                    or result['assessment'].get('settlement_channel') != 'observation'):
                raise ValueError('Material observations cannot authorize scheduling')
            if row['exposed'] and not result.get('assisted'):
                raise ValueError('Diagnostic exposure cannot be erased')
            if _date(result['submitted_at_utc']) < _date(value['created_at_utc']):
                raise ValueError('Diagnostic answer predates its session')
        if row['completed'] and result is None:
            raise ValueError('Completed diagnostic entry has no observation')
        receipt = row['settlement']
        if row['completed'] and cqid is not None and receipt is None:
            raise ValueError('Completed diagnostic entry has no settlement receipt')
        if receipt is not None:
            if (not row['completed'] or cqid is None or not isinstance(receipt, dict)
                    or set(receipt) != {'question_id', 'scheduled', 'rating'} or receipt['question_id'] != cqid
                    or type(receipt['scheduled']) is not bool):
                raise ValueError('Invalid diagnostic settlement receipt')
            if receipt['scheduled']:
                uid = unit_id(q)
                if row['purpose'] != 'check_and_adjust' or row['exposed'] or row['credit_revoked'] or uid in formal_units:
                    raise ValueError('Repeated or unauthorized diagnostic settlement')
                formal_units.add(uid)
    for blocked in value['blocked']:
        if (not isinstance(blocked, dict) or set(blocked) != {'target_id', 'reason', 'question_id'}
                or blocked['target_id'] not in scope or not _text(blocked['reason'])
                or blocked['question_id'] is not None and not _text(blocked['question_id'])):
            raise ValueError('Invalid diagnostic blocked target')
    if sum(r['result'] is not None for r in value['entries']) > BUDGETS[value['budget']][1]:
        raise ValueError('Diagnostic answer budget exceeded')
    if value['status'] == 'completed' and any(not r['completed'] and not r.get('content_retirement') for r in value['entries']):
        raise ValueError('Incomplete diagnostic marked completed')
    return deepcopy(value)


def reservation(store, uid):
    raw = store.get_setting(RESERVATION_PREFIX + uid, None)
    return validate_reservation(raw) if raw is not None else None


def authorize_course(learning, question_id, context):
    """Read durable ownership; neither a UI flag nor a model can grant credit."""
    row = learning._db.execute('SELECT unit_id FROM learning_question WHERE question_id=?', (question_id,)).fetchone()
    owned = reservation(learning.store, row['unit_id']) if row else None
    if owned is None:
        if context is not None:
            raise ValueError('检查预约已变更，请重新打开原练习。')
        return None
    if context != (owned['episode_id'], owned['entry_id']) or owned['question_id'] != question_id:
        raise ValueError('这道题正在掌握检查中，请从检查入口继续。')
    episode = validate_episode(learning.store.get_setting(PREFIX + owned['episode_id']))
    entry = next((r for r in episode['entries'] if r['id'] == owned['entry_id']), None)
    if entry is None or entry['course_question_id'] != question_id or entry['completed']:
        raise ValueError('检查机会已结束。')
    return episode, entry


def latest_anchor(db, uid):
    row = db.execute("SELECT event_id FROM learning_event WHERE unit_id=? AND role='primary' ORDER BY rowid DESC LIMIT 1", (uid,)).fetchone()
    return row[0] if row else None


def formal_ready(learning, entry, now, *, settling=False):
    """An early check may bypass scheduled due, never teaching/exposure guards."""
    q = entry['question']; uid = unit_id(q)
    unit = learning._db.execute('SELECT * FROM learning_unit WHERE unit_id=?', (uid,)).fetchone()
    content = learning.ability_instruction_row(q['target_id'], q['ability'])
    if (unit is None or content is None or unit['suspended'] or not unit['instruction_ready']
            or entry['credit_revoked'] or not enabled_question(learning.store, q)
            or handoff(learning.store, q['id']) is not None
            or content['stage'] in {'intro', 'guide'} or learning.prerequisite_status(
                q['target_id'], dict(content).get('instruction_scope'))['blocked']
            or content['stage'] == 'bridge' and not learning._retrieval_ready(content, now)
            or latest_anchor(learning._db, uid) != entry['anchor_event_id']):
        return False
    previous = json.loads(unit['scheduler_state_json'] or '{}').get('last_review')
    if previous and _date(previous) > now:
        return False
    if settling:
        return True  # First-answer independence was frozen before feedback.
    from .learning_store import _ability_due
    pending = learning._db.execute('SELECT not_before_utc FROM learning_question WHERE question_id=?', (entry['course_question_id'],)).fetchone()
    floor = pending[0] if pending else None
    scheduled = _ability_due(unit, q)
    # Only the exact old scheduling floor is bypassed. A separate frozen floor
    # (for feedback, explicit pacing, etc.) remains an independence constraint.
    if floor and scheduled and _date(floor) == _date(scheduled):
        floor = None
    detached = {**dict(unit), 'due_at_utc': None, 'probe_due_at_utc': None, 'not_before_utc': floor}
    guard = learning.question_not_before(detached, {**q, 'not_before_utc': floor})
    return not guard or _date(guard) <= now


def expose_in_transaction(store, question, now):
    if not store._db.in_transaction:
        raise RuntimeError('Exposure requires the caller transaction')
    cid = question['target_id']; ability = question['ability']
    key = EXPOSURE_PREFIX + sha256(cid.encode()).hexdigest()
    value = store.get_setting(key, {'version': 1, 'target_id': cid, 'abilities': {}})
    validate_exposure(value)
    previous = value['abilities'].get(ability)
    if previous is None or _date(previous['at']) <= now:
        value['abilities'][ability] = {'at': now.isoformat(), 'source': 'feedback'}
    validate_exposure(value)
    store._db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json', (key, _dump(value)))


class DiagnosticStore:
    def __init__(self, store, learning=None, *, repo=None, examples=None):
        from .learning_store import LearningStore
        self.store, self.db = store, store._db
        self.learning = learning if learning is not None else LearningStore(store)
        self.legacy = RetentionCheckStore(store)
        self.repo, self.examples = repo, examples
        from .material_qualification import bind_resources
        bind_resources(store, repo=repo, examples=examples)

    def get(self, identity):
        raw = self.store.get_setting(PREFIX + identity, None)
        return validate_episode(raw) if raw is not None else None

    def active(self):
        identity = self.store.get_setting(ACTIVE_KEY, None)
        return self.get(identity) if isinstance(identity, str) else None

    def latest_finished(self):
        row = self.db.execute("SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.status') IN ('completed','budget','stopped') ORDER BY json_extract(value_json,'$.created_at_utc') DESC,rowid DESC LIMIT 1", (PREFIX + '*',)).fetchone()
        return validate_episode(json.loads(row[0])) if row else None

    def _write(self, value):
        validate_episode(value)
        current = self.store.get_setting(ACTIVE_KEY, None)
        owns_active = value['status'] in LIVE or any(r['result'] is not None and not r['completed'] for r in value['entries'])
        if owns_active and current not in (None, value['id']):
            raise ValueError('已有另一轮未完成检查。')
        self.db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json', (PREFIX + value['id'], _dump(value)))
        if owns_active or current == value['id']:
            self.db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
                            (ACTIVE_KEY, _dump(value['id'] if owns_active else None)))

    def _reserve(self, episode, entry):
        from .retention_sources import retain_guard
        retain_guard(self.store, episode, entry)
        uid = unit_id(entry['question'])
        old = reservation(self.store, uid)
        if old and old['episode_id'] != episode['id']:
            raise ValueError('这项能力已有未完成检查。')
        value = {'version': 1, 'unit_id': uid, 'episode_id': episode['id'], 'entry_id': entry['id'],
                 'question_id': entry['course_question_id']}
        self.db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json', (RESERVATION_PREFIX + uid, _dump(value)))

    def _release(self, episode, *, keep_results=True):
        for entry in episode['entries']:
            owned = reservation(self.store, unit_id(entry['question']))
            if not owned or owned['episode_id'] != episode['id']:
                continue
            owner = next(r for r in episode['entries'] if r['id'] == owned['entry_id'])
            if keep_results and owner['result'] is not None and not owner['completed']:
                continue
            if (owner['course_question_id'] and owner['result'] is None and owner['presented']
                    and (owner['purpose'] == 'check_only' or owner['credit_revoked'] or owner['exposed'])):
                # Permission and help belong to the opportunity, even after it
                # returns to the ordinary queue. Never turn this into Again.
                self._handoff(episode, owner)
            self.db.execute('DELETE FROM app_setting WHERE key=?', (RESERVATION_PREFIX + owned['unit_id'],))

    def _handoff(self, episode, entry):
        transfer = {'version': 1, 'episode_id': episode['id'], 'entry_id': entry['id'],
                    'question_id': entry['course_question_id']}
        self.db.execute('INSERT OR IGNORE INTO app_setting VALUES (?,?)',
                        (handoff_key(entry['course_question_id']), _dump(transfer)))

    def _exposed(self, q, now):
        # A whole introduction exposes several abilities. Answer feedback is
        # scoped using actual question units, not a target-wide timestamp.
        value = self.store.get_setting(EXPOSURE_PREFIX + sha256(q['target_id'].encode()).hexdigest(), None)
        stamps = []
        if value:
            record = validate_exposure(value)['abilities'].get(q['ability'])
            if record: stamps.append(_date(record['at']))
        from .material_admission import mapped_exposure
        mapped = mapped_exposure(self.store, q['target_id'], q['ability'])
        if mapped: stamps.append(_date(mapped['at']))
        content = self.db.execute('SELECT checkpoint_json FROM learning_content WHERE content_id=?', (q['target_id'],)).fetchone()
        if content:
            stamp = json.loads(content[0]).get('instruction_exposed_at_utc')
            if stamp: stamps.append(_date(stamp))
        for row in self.db.execute('SELECT result_json FROM learning_question WHERE unit_id=? AND result_json IS NOT NULL ORDER BY rowid DESC LIMIT 1', (unit_id(q),)):
            stamps.append(_date(json.loads(row[0])['submitted_at_utc']))
        return any(now - stamp < timedelta(seconds=60) for stamp in stamps)

    def expose_visible(self, abilities, *, now=None):
        """Persist titles before display, including help on already seen drafts."""
        stamp = now or app_clock.utc_now()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for cid, ability in set(abilities):
                q = {'target_id': cid, 'ability': ability}
                expose_in_transaction(self.store, q, stamp)
                owned = reservation(self.store, unit_id(q))
                if owned:
                    value = self.get(owned['episode_id']); entry = self._entry(value, owned['entry_id'])
                    if entry['presented'] and entry['result'] is None:
                        entry['exposed'] = True
                        self._write(value)
                for pending in self.db.execute("SELECT question_id,draft_json FROM learning_question WHERE unit_id=? AND status='pending' AND result_json IS NULL", (unit_id(q),)).fetchall():
                    draft = json.loads(pending['draft_json'])
                    if draft.get('presented') or owned and entry['presented'] and owned['question_id'] == pending['question_id']:
                        draft['assisted'] = True
                        self.db.execute('UPDATE learning_question SET draft_json=? WHERE question_id=?', (_dump(draft), pending['question_id']))

    def _material_items(self, identity):
        from .material_eligibility import pretest_targets
        return {item['id']: item for item in pretest_targets(self.store, identity, repo=self.repo, examples=self.examples)}

    def _candidates(self, cid, enabled, *, material_id=None, canonical_items=None):
        from .retention_sources import current_item, trusted_question
        from .learning_questions import build_learning_question, question_kinds
        from .answer_input import available, system_japanese_enabled
        item = ((canonical_items if canonical_items is not None else self._material_items(material_id)).get(cid)
                if material_id else current_item(self.store, cid, repo=self.repo))
        if item is None: return []
        if material_id and cid.startswith('material-candidate:'):
            from .material_qualification import candidate_questions
            return [q for q in candidate_questions(self.store, material_id, repo=self.repo, examples=self.examples, target_id=cid)
                    if enabled_question(self.store, q, enabled)
                    and available(q, system_japanese=system_japanese_enabled(self.store))
                    and assessment_decision(item, q, purpose='check_only', enrolled=False, store=self.store)['assessment_eligible']]
        candidates = []
        from .learning_practice_policy import practice_candidates
        from .learning_dependencies import independent_ready_ids
        descriptors = list(practice_candidates(item, question_kinds(cid, item=item),
            known_background_ids=independent_ready_ids(self.learning),
            system_japanese=system_japanese_enabled(self.store)))
        for kind in question_kinds(cid, item=item):
            if not enabled_question(self.store, {'kind': kind}, enabled): continue
            for variant in [d['variant'] for d in descriptors if d['kind'] == kind]:
                q = build_learning_question(item, kind, variant=variant)
                if (not material_id and item.get('dictionary_only')
                        and self.learning.get_unit(cid, q['ability']) is None):
                    continue
                from .material_eligibility import exact_question
                trusted = exact_question(item, q) if material_id else trusted_question(self.store, q, repo=self.repo, item=item)
                if (trusted and available(q, system_japanese=system_japanese_enabled(self.store))
                        and assessment_decision(item, q, purpose='check_only', enrolled=False, store=self.store)['assessment_eligible']):
                    candidates.append(q)
        return sorted(candidates, key=lambda q: (q.get('input_mode', 'choice') != 'choice', bool(q.get('audio_text'))))

    def start(self, scope_ids, enabled_kinds, request_id, *, purpose='check_only', budget='quick', intent='active', exposed_units=(), now=None, _after_start=None, material_id=None, preferred_questions=None, _append_to=None):
        from .learning_dependencies import has_started_answer
        if _after_start is not None and not callable(_after_start):
            raise ValueError('Invalid diagnostic transaction hook')
        if material_id is not None and (not _identity(material_id) or intent != 'material' or purpose != 'check_only'):
            raise ValueError('材料先测只记录观察，不调整复习。')
        if preferred_questions is not None and (not isinstance(preferred_questions, dict)
                or set(preferred_questions) - set(scope_ids)
                or any(not _identity(proof) for proof in preferred_questions.values())):
            raise ValueError('检查选题建议无效。')
        if (not _text(request_id, 256) or purpose not in PURPOSES or budget not in BUDGETS
                or not isinstance(scope_ids, list) or not 1 <= len(scope_ids) <= BUDGETS[budget][0]
                or any(not _text(cid) for cid in scope_ids) or len(scope_ids) != len(set(scope_ids))):
            raise ValueError('请选择检查范围、用途及题量。')
        identity = sha256(request_id.encode()).hexdigest(); stamp = now or app_clock.utc_now()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old = self.get(identity)
            if old:
                if old['scope_ids'] != scope_ids or old.get('material_id') != material_id: raise ValueError('同一请求不能替换检查范围。')
                if _after_start is not None: _after_start(self.db, old)
                return old
            active = self.active()
            if _append_to is not None:
                if (not material_id or not active or active['status'] not in LIVE or active['id'] != _append_to
                        or active.get('material_id') != material_id or active['purpose'] != 'check_only'
                        or set(scope_ids) & set(active['scope_ids'])):
                    raise ValueError('材料检查范围已改变，请继续原检查。')
                if len(active['scope_ids']) + len(scope_ids) > BUDGETS['deep'][0]:
                    raise ValueError('本轮检查已达范围上限，请完成后再检查新增内容。')
                episode = deepcopy(active); identity = episode['id']
                episode['scope_ids'].extend(scope_ids)
                episode['budget'] = 'deep'
            else:
                if active or self.legacy.active(): raise ValueError('请先继续未完成的检查。')
                episode = {'format': FORMAT, 'version': 2, 'id': identity, 'status': 'active', 'created_at_utc': stamp.isoformat(),
                           'purpose': purpose, 'intent': intent, 'scope_ids': list(scope_ids), 'budget': budget,
                           'used_ms': 0, 'permission_revision': 1, 'entries': [], 'blocked': []}
            if material_id:
                episode['material_id'] = material_id
            material_items = self._material_items(material_id) if material_id else None
            for cid in scope_ids:
                candidates = self._candidates(cid, enabled_kinds, material_id=material_id, canonical_items=material_items)
                selected = None; blocked = None
                for q in candidates:
                    uid = unit_id(q)
                    unit = self.db.execute('SELECT suspended FROM learning_unit WHERE unit_id=?', (uid,)).fetchone()
                    if unit and unit[0]: continue
                    pending = self.db.execute("SELECT * FROM learning_question WHERE unit_id=? AND status='pending'", (uid,)).fetchone()
                    if pending and (pending['result_json'] is not None or has_started_answer(json.loads(pending['draft_json']))):
                        blocked = {'target_id': cid, 'reason': 'continue_original', 'question_id': pending['question_id']}
                        continue
                    if pending:
                        frozen = json.loads(pending['snapshot_json'])
                        from .answer_input import available, system_japanese_enabled
                        from .retention_sources import current_item as source_item, trusted_question
                        from .material_eligibility import exact_question
                        current_item = material_items.get(cid) if material_id else source_item(self.store, cid, repo=self.repo)
                        trusted = exact_question(current_item, frozen) if material_id else trusted_question(self.store, frozen, repo=self.repo, item=current_item)
                        if (not enabled_question(self.store, frozen, enabled_kinds) or not trusted
                                or not available(frozen, system_japanese=system_japanese_enabled(self.store))
                                or not assessment_decision(current_item, frozen, purpose='check_only', store=self.store)['assessment_eligible']):
                            continue
                        q = frozen
                    if preferred_questions and cid in preferred_questions and _proof(q) != preferred_questions[cid]:
                        continue
                    selected = (q, pending, unit); break
                if selected is None:
                    if preferred_questions and cid in preferred_questions:
                        raise ValueError('建议对应的题目已变化，请重新选择或使用本地安排。')
                    episode['blocked'].append(blocked or {'target_id': cid, 'reason': 'no_eligible_question', 'question_id': None})
                    continue
                q, pending, enrolled = selected
                eid = 'diagnostic:' + identity + ':' + str(len(episode['entries']))
                q = deepcopy(q)
                if enrolled and pending is None:
                    q['id'] = eid
                    added = self.learning._append_questions_in_transaction(q, now=stamp)
                    if not added: raise ValueError('普通题单已变化，请重新开始检查。')
                    pending = self.db.execute('SELECT * FROM learning_question WHERE question_id=?', (eid,)).fetchone()
                    q = json.loads(pending['snapshot_json'])
                if pending is None: q['id'] = eid
                entry = {'id': eid, 'question': q, 'proof': _proof(q), 'phase': 'baseline', 'purpose': purpose,
                         'presented': False, 'exposed': self._exposed(q, stamp) or (cid, q['ability']) in exposed_units,
                         'draft': {}, 'result': None, 'completed': False, 'credit_revoked': False,
                         'course_question_id': pending['question_id'] if pending else None,
                         'anchor_event_id': latest_anchor(self.db, unit_id(q)), 'settlement': None}
                episode['entries'].append(entry); self._reserve(episode, entry)
            if not episode['entries']: episode['status'] = 'completed'
            self._write(episode)
            if _after_start is not None: _after_start(self.db, episode)
            return deepcopy(episode)

    def _mutate(self, identity, operation):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            value = self.get(identity)
            if value is None: raise KeyError(identity)
            result = operation(value)
            self._write(value)
            return deepcopy(result)

    @staticmethod
    def _entry(value, entry_id):
        row = next((r for r in value['entries'] if r['id'] == entry_id), None)
        if row is None: raise KeyError(entry_id)
        return row

    def current(self, identity, enabled_kinds):
        value = self.get(identity)
        if value is None: return None
        frozen = next((r for r in value['entries'] if r['result'] is not None and not r['completed']), None)
        if frozen: return frozen
        if value['status'] not in LIVE: return None
        return next((r for r in value['entries'] if not r['completed'] and self.presentable(r, enabled_kinds, material_id=value.get('material_id'))), None)

    def presentable(self, row, enabled=None, *, material_id=None):
        if row.get('content_retirement'): return False
        from .answer_input import available, system_japanese_enabled
        if material_id:
            from .material_eligibility import material_assessment
            if not material_assessment(self.store, material_id, row['question'], repo=self.repo, examples=self.examples)['assessment_eligible']:
                return False
        else:
            from .retention_sources import trusted_question, current_item
            from .assessment_policy import assessment_decision
            if not trusted_question(self.store, row['question'], repo=self.repo):
                return False
            item = current_item(self.store, row['question']['target_id'], repo=self.repo)
            if not assessment_decision(item, row['question'], purpose='check_only', store=self.store)['assessment_eligible']:
                return False
        unit = self.db.execute('SELECT suspended FROM learning_unit WHERE unit_id=?', (unit_id(row['question']),)).fetchone()
        return (enabled_question(self.store, row['question'], enabled) and not (unit and unit[0])
                and available(row['question'], system_japanese=system_japanese_enabled(self.store)))

    def present(self, identity, entry_id, *, now=None):
        def write(value):
            row = self._entry(value, entry_id)
            if value['status'] not in LIVE: return row
            value['status'] = 'active'
            if row.get('content_retirement'): raise ValueError('此题依据已修订，原题与草稿已保留。')
            if row['result'] is not None: return row
            if not self.presentable(row, material_id=value.get('material_id')): raise ValueError('这项能力、题型或材料依据暂不可用，草稿已保留。')
            row['presented'] = True
            row['exposed'] |= self._exposed(row['question'], now or app_clock.utc_now())
            if row['exposed'] and row['course_question_id']:
                self._write(value)
                self.learning._save_draft_in_transaction(row['course_question_id'], {'assisted': True},
                    diagnostic_context=(identity, entry_id))
            return row
        return self._mutate(identity, write)

    def save_draft(self, identity, entry_id, state):
        def write(value):
            row = self._entry(value, entry_id)
            if row['result'] is not None: return row
            # Terminal navigation callbacks may still hold the former field.
            # The last live draft was flushed before releasing ownership.
            if value['status'] not in LIVE or row.get('content_retirement'): return row
            previous_help = row['draft'].get('hint_used', False)
            played = row['draft'].get('audio_ok', False)
            row['draft'].update(state)
            if previous_help: row['draft']['hint_used'] = True
            if played: row['draft']['audio_ok'] = True
            row['exposed'] |= row['draft'].get('hint_used') is True
            self._write(value)  # Ownership checks below read the same transaction.
            if row['course_question_id']:
                draft = {**row['draft'], **({'assisted': True} if row['exposed'] else {})}
                self.learning._save_draft_in_transaction(row['course_question_id'], draft, diagnostic_context=(identity, entry_id))
            return row
        return self._mutate(identity, write)

    def set_purpose(self, identity, purpose):
        if purpose not in PURPOSES: raise ValueError('Invalid diagnostic purpose')
        def write(value):
            if value.get('material_id') and purpose != 'check_only':
                raise ValueError('材料先测只记录观察，不调整复习。')
            if value['status'] not in LIVE and purpose != 'check_only': return value
            value['purpose'] = purpose; value['permission_revision'] += 1
            if purpose == 'check_only':
                from .retention_schedule import revoke
                revoke(self.store, identity, app_clock.utc_now())
            # Already presented/submitted opportunities never gain permission.
            for row in value['entries']:
                if row['completed'] or row.get('content_retirement'): continue
                if value['status'] in TERMINAL and row['course_question_id'] and row['result'] is None:
                    course = self.db.execute('SELECT status FROM learning_question WHERE question_id=?', (row['course_question_id'],)).fetchone()
                    if course and course[0] == 'completed': continue  # No retroactive rewriting.
                if purpose == 'check_only' and row['presented']:
                    row['credit_revoked'] = True
                if row['result'] is None and (not row['presented'] or purpose == 'check_only'):
                    row['purpose'] = purpose
                if (value['status'] in TERMINAL and row['presented'] and row['course_question_id']
                        and row['result'] is None and purpose == 'check_only'):
                    self._handoff(value, row)
            return value
        return self._mutate(identity, write)

    def consume(self, identity, elapsed_ms, *, pause=False):
        if type(elapsed_ms) is not int or not 0 <= elapsed_ms <= 600000 or type(pause) is not bool:
            raise ValueError('Invalid foreground duration')
        def write(value):
            if value['status'] not in LIVE: return value
            if value['status'] == 'active':
                limit = BUDGETS[value['budget']][2]
                value['used_ms'] = min(limit, value['used_ms'] + elapsed_ms)
                if value['used_ms'] >= limit:
                    value['status'] = 'budget'; self._release(value)
            if pause and value['status'] == 'active': value['status'] = 'paused'
            return value
        return self._mutate(identity, write)

    def stop(self, identity):
        def write(value):
            if value['status'] in LIVE: value['status'] = 'stopped'
            self._release(value)
            return value
        return self._mutate(identity, write)

    def submit(self, identity, entry_id, answer, enabled_kinds, *, revealed=False, now=None):
        from .retention_sources import assessment_item
        from .learning_questions import grade_learning_answer
        from .learning_evidence import LearningEvidenceStore
        from .answer_input import available, system_japanese_enabled
        def write(value):
            row = self._entry(value, entry_id)
            if row['result'] is not None: return row['result']
            if value['status'] != 'active' or value['used_ms'] >= BUDGETS[value['budget']][2]:
                raise ValueError('检查已暂停或达到预算，草稿已保留。')
            if sum(r['result'] is not None for r in value['entries']) >= BUDGETS[value['budget']][1]:
                raise ValueError('本轮已达到题量上限。')
            q = row['question']
            if not self.presentable(row, enabled_kinds, material_id=value.get('material_id')): raise ValueError('这项能力、题型或材料依据暂不可用，原题和草稿已保留。')
            if not available(q, system_japanese=system_japanese_enabled(self.store)):
                raise ValueError('当前输入方式不能完成这道题，草稿已保留。')
            if not isinstance(answer, str) or len(answer) > 10000 or type(revealed) is not bool:
                raise ValueError('Invalid diagnostic answer')
            stamp = max(now or app_clock.utc_now(), _date(value['created_at_utc']))
            row['presented'] = True
            row['exposed'] |= revealed or self._exposed(q, stamp) or row['draft'].get('hint_used') is True
            judge = {'status': 'revealed', 'correct': None} if revealed else grade_learning_answer(q, answer)
            if judge['status'] in {'empty', 'invalid'}: raise ValueError(judge.get('message', '请先作答。'))
            if q.get('audio_text') and not row['draft'].get('audio_ok'):
                judge = {'status': 'device_failure', 'correct': None}
            self._write(value)
            if row['course_question_id']:
                result = self.learning._submit_in_transaction(row['course_question_id'], answer, judge['correct'],
                    assisted=row['exposed'], outcome=judge['status'], now=stamp,
                    diagnostic_context=(identity, entry_id))
            else:
                item = assessment_item(self.store, q)
                decision = assessment_decision(item, q, purpose=row['purpose'], enrolled=False, exposed=row['exposed'], store=self.store)
                if value.get('material_id'):
                    from .material_eligibility import material_assessment
                    decision = material_assessment(self.store, value['material_id'], q,
                        repo=self.repo, examples=self.examples, exposed=row['exposed'])
                qualified = decision['assessment_eligible']
                result = {'question_id': q['id'], 'answered_text': answer, 'correct': judge['correct'] if qualified else None,
                          'outcome': judge['status'] if qualified else 'unresolved', 'assisted': row['exposed'],
                          'scheduled': False, 'qualified': qualified, 'submitted_at_utc': stamp.isoformat(),
                          'assessment': decision, 'observed_judgement': {'outcome': judge['status'], 'correct': judge['correct']},
                          **{key: judge[key] for key in ('slot_results', 'primary_correct', 'primary_scorable',
                              'overall_correct', 'background_errors') if key in judge}}
                result['assessment_contract'] = for_question(q)
                result['observations'] = observations(q, result); result['unit_observations'] = aggregate(result['observations'])
                LearningEvidenceStore(self.store).record_attempt('diagnostic:' + row['id'], q, result,
                    source='diagnostic', role='exposure', trusted=qualified, now=stamp,
                    opportunity_id='diagnostic:' + row.get('opportunity_root', row['id']), episode_id=identity,
                    part_ids=tuple(p['part_id'] for p in result['observations']))
            row['result'] = result
            expose_in_transaction(self.store, q, stamp)
            if sum(r['result'] is not None for r in value['entries']) >= BUDGETS[value['budget']][1]:
                value['status'] = 'budget'; self._release(value)
            return result
        return self._mutate(identity, write)

    def advance(self, identity, entry_id, enabled_kinds, *, rating=None, now=None):
        def write(value):
            row = self._entry(value, entry_id)
            if row['completed']: return value
            if row['result'] is None: raise ValueError('请先作答。')
            stamp = max(now or app_clock.utc_now(), _date(row['result']['submitted_at_utc']))
            if row['course_question_id']:
                settled = self.learning._finalize_in_transaction(row['course_question_id'], rating, now=stamp,
                    diagnostic_context=(identity, entry_id))
                row['settlement'] = {k: settled[k] for k in ('question_id', 'scheduled', 'rating')}
            row['completed'] = True
            q = row['question']
            # All first targets were queued before these optional second slots.
            # Feedback makes a transfer/recovery response permanently assisted.
            if row['phase'] == 'baseline' and value['status'] in LIVE:
                candidates = [c for c in self._candidates(q['target_id'], enabled_kinds, material_id=value.get('material_id'))
                              if c['ability'] == q['ability'] and _proof(c) != row['proof']]
                observed = row['result'].get('observed_judgement', row['result'])
                success = observed.get('correct') is True
                candidates.sort(key=lambda c: (c.get('input_mode', 'choice') == 'choice') if success else (c.get('input_mode', 'choice') != 'choice'))
                if candidates:
                    follow = deepcopy(candidates[0]); eid = 'diagnostic:' + identity + ':' + str(len(value['entries']))
                    follow['id'] = eid
                    next_row = {'id': eid, 'question': follow, 'proof': _proof(follow), 'phase': 'transfer' if success else 'recovery',
                                'purpose': value['purpose'], 'presented': False, 'exposed': True, 'draft': {}, 'result': None,
                                'completed': False, 'credit_revoked': False, 'course_question_id': None, 'anchor_event_id': latest_anchor(self.db, unit_id(q)), 'settlement': None}
                    value['entries'].append(next_row); self._reserve(value, next_row)
                else:
                    self.db.execute('DELETE FROM app_setting WHERE key=?', (RESERVATION_PREFIX + unit_id(q),))
            else:
                self.db.execute('DELETE FROM app_setting WHERE key=?', (RESERVATION_PREFIX + unit_id(q),))
            if all(r['completed'] for r in value['entries']): value['status'] = 'completed'
            if value['status'] in TERMINAL: self._release(value)
            return value
        return self._mutate(identity, write)


def validate_database(db):
    """Cross-check imported receipts against actual opportunities, not claims."""
    from types import SimpleNamespace
    from .diagnostic_replacement import find_entry
    def setting(key, default=None):
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    store = SimpleNamespace(_db=db, get_setting=setting)
    episodes = {}; claimed = set()
    for row in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (PREFIX + '*',)):
        value = validate_episode(json.loads(row['value_json']))
        if row['key'] != PREFIX + value['id']: raise ValueError('Diagnostic key mismatch')
        episodes[value['id']] = value
        if value.get('material_id'):
            from .material_study import PREFIX as STUDY_PREFIX, validate_state as validate_material_state
            raw = db.execute('SELECT value_json FROM app_setting WHERE key=?', (STUDY_PREFIX + value['material_id'],)).fetchone()
            study = validate_material_state(json.loads(raw[0])) if raw else None
            scopes = [r['target_ids'] for r in study.get('scope_history', [])] + [study['target_ids']] if study else []
            matched = study and study['diagnostic_id'] == value['id'] and value['scope_ids'] in scopes
            if not matched:
                from .material_pretest import matches_episode
                if not matches_episode(store, value):
                    raise ValueError('Material diagnostic has no matching study receipt')
        for entry in value['entries']:
            if entry['course_question_id'] is None: continue
            q = db.execute('SELECT * FROM learning_question WHERE question_id=?', (entry['course_question_id'],)).fetchone()
            if q is None or q['unit_id'] != unit_id(entry['question']) or _proof(json.loads(q['snapshot_json'])) != entry['proof']:
                raise ValueError('Shared diagnostic snapshot differs from course opportunity')
            if entry['result'] is not None and json.loads(q['result_json'] or 'null') != entry['result']:
                raise ValueError('Diagnostic first answer differs from shared answer')
            if (value['status'] in TERMINAL and entry['result'] is None and entry['presented']
                    and (entry['purpose'] == 'check_only' or entry['credit_revoked'] or entry['exposed'])
                    and not db.execute('SELECT 1 FROM app_setting WHERE key=?', (handoff_key(entry['course_question_id']),)).fetchone()):
                raise ValueError('Terminated diagnostic is missing its observation handoff')
            if entry['result'] is not None:
                if entry['course_question_id'] in claimed:
                    raise ValueError('Shared answer claimed by multiple diagnostics')
                claimed.add(entry['course_question_id'])
            if entry['settlement'] is not None:
                event = db.execute('SELECT event_id,result_json FROM learning_event WHERE question_id=?', (entry['course_question_id'],)).fetchone()
                if event is None or {k: json.loads(event['result_json'])[k] for k in ('question_id', 'scheduled', 'rating')} != entry['settlement']:
                    raise ValueError('Diagnostic receipt has no matching course event')
                evidence = db.execute('SELECT snapshot_json FROM learning_evidence WHERE event_id=?', ('course:' + event['event_id'],)).fetchone()
                if evidence is None or json.loads(evidence[0]).get('episode_id') != 'course:' + value['id']:
                    raise ValueError('Diagnostic receipt evidence belongs to another episode')
    for saved in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (HANDOFF_PREFIX + '*',)):
        transfer = validate_handoff(json.loads(saved['value_json']))
        episode = episodes.get(transfer['episode_id'])
        entry = find_entry(store, episode, transfer['entry_id'], question_id=transfer['question_id']) if episode else None
        if (saved['key'] != handoff_key(transfer['question_id']) or entry is None
                or episode['status'] not in TERMINAL and not entry.get('content_retirement') or entry['result'] is not None
                or entry['course_question_id'] != transfer['question_id'] or not entry['presented']
                or not (entry['purpose'] == 'check_only' or entry['credit_revoked'] or entry['exposed'])):
            raise ValueError('Dangling diagnostic handoff')
    for row in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (RESERVATION_PREFIX + '*',)):
        owned = validate_reservation(json.loads(row['value_json']))
        episode = episodes.get(owned['episode_id'])
        entry = next((r for r in episode['entries'] if r['id'] == owned['entry_id']), None) if episode else None
        if (row['key'] != RESERVATION_PREFIX + owned['unit_id'] or entry is None or entry['completed'] or entry.get('content_retirement')
                or owned['unit_id'] != unit_id(entry['question']) or owned['question_id'] != entry['course_question_id']
                or episode['status'] in TERMINAL and entry['result'] is None):
            raise ValueError('Dangling diagnostic reservation')
    active = db.execute('SELECT value_json FROM app_setting WHERE key=?', (ACTIVE_KEY,)).fetchone()
    active = json.loads(active[0]) if active else None
    live = [e['id'] for e in episodes.values() if e['status'] in LIVE or any(
        r['result'] is not None and not r['completed'] for r in e['entries'])]
    if live != ([] if active is None else [active]):
        raise ValueError('Diagnostic active session mismatch')
    for episode in episodes.values():
        for entry in episode['entries']:
            if entry['completed'] or entry.get('content_retirement') or episode['status'] in TERMINAL and entry['result'] is None:
                continue
            owned = db.execute('SELECT value_json FROM app_setting WHERE key=?', (RESERVATION_PREFIX + unit_id(entry['question']),)).fetchone()
            if owned is None or json.loads(owned[0])['entry_id'] != entry['id']:
                raise ValueError('Active diagnostic has no reservation')
    if live and db.execute("SELECT 1 FROM app_setting WHERE key GLOB 'retention_check_v1:*' AND json_extract(value_json,'$.status')='active'").fetchone():
        raise ValueError('Legacy and current diagnostics cannot run in parallel')
