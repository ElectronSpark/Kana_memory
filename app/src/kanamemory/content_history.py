"""Append-only content adjudications and their disposable contribution index.

An invalid/ambiguous attribution is not a new grade. Original question, answer,
formal scheduler event and consumed assessment opportunity remain untouched.
The impact scan selects candidates; it does not itself prove an answer wrong.
"""
from copy import deepcopy
from datetime import datetime
import json

from .material_workflow import digest, dump

FORMAT = 'kanamemory-content-history'
PREFIX = 'content_history_v1:'
INDEX = 'content_history_status_v1:'
TABLE_IDS = {'learning_evidence': 'event_id', 'learning_event': 'event_id',
             'review_event': 'event_id', 'word_spelling_attempt': 'attempt_id'}
SETTING_PREFIXES = ('ai_practice_v1', 'ai_tutor_v1', 'retention_check_v', 'retention_dialogue_v',
                    'retention_ai_v', 'material_', 'ability_teaching_v')
MUTABLE_EVIDENCE = {'retracted', 'retraction_reason', 'retracted_at_utc'}


def formal_recheck_targets(db, content_event_id):
    """Only actually affected formal answers suggest an optional new check.

    This does not replay scheduler history or infer a corrected numeric state.
    Unenrolled content and kana are excluded from the word/grammar check entry.
    """
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='learning_content'").fetchone():
        return []
    rows = db.execute("""SELECT DISTINCT json_extract(h.value_json,'$.candidate.target_id')
        FROM app_setting h JOIN learning_content c
          ON c.content_id=json_extract(h.value_json,'$.candidate.target_id')
        WHERE h.key GLOB ? AND json_extract(h.value_json,'$.content_event_id')=?
          AND json_extract(h.value_json,'$.verdict') IN ('invalid','ambiguous')
          AND (json_extract(h.value_json,'$.candidate.locator') LIKE 'learning_event:%'
               OR json_extract(h.value_json,'$.candidate.locator') LIKE 'review_event:%')
          AND c.family IN ('word','grammar')
          AND EXISTS(SELECT 1 FROM learning_unit u WHERE u.content_id=c.content_id AND u.suspended=0)
        ORDER BY 1""", (PREFIX + '*', content_event_id))
    return [row[0] for row in rows]


def index_key(event_id, target_id='', ability=''):
    return INDEX + digest([event_id, target_id, ability])


def contribution_valid(db, event_id, target_id='', ability=''):
    """Point lookups only; a valid later decision never resurrects old evidence."""
    keys = {index_key(event_id, target_id, ability), index_key(event_id, target_id), index_key(event_id)}
    from .material_validity import index_key as material_index
    keys.update({material_index(event_id, target_id, ability), material_index(event_id, target_id)})
    for key in keys:
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        if row and json.loads(row[0]):
            return False
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_evidence'").fetchone():
        row = db.execute('SELECT retracted FROM learning_evidence WHERE event_id=?', (event_id,)).fetchone()
        if row and row[0]:
            return False
    return True


def contribution_batch(db, subjects):
    """One bounded query per word, instead of per character/index variant."""
    subjects = list(dict.fromkeys(subjects))
    result = {subject: True for subject in subjects}
    owners = {}
    for subject in subjects:
        event, target, ability = subject
        from .material_validity import index_key as material_index
        for key in {index_key(event, target, ability), index_key(event, target), index_key(event),
                    material_index(event, target, ability), material_index(event, target)}:
            owners.setdefault(key, set()).add(subject)
    keys = list(owners)
    for start in range(0, len(keys), 400):
        chunk = keys[start:start+400]
        for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key IN (' + ','.join('?' for _ in chunk) + ')', chunk):
            if json.loads(raw):
                for subject in owners[key]: result[subject] = False
    return result


def _source(db, locator):
    table, _, identity = locator.partition(':')
    if table in TABLE_IDS:
        row = db.execute('SELECT * FROM ' + table + ' WHERE ' + TABLE_IDS[table] + '=?', (identity,)).fetchone()
        if row is None: raise ValueError('历史裁定缺少原始作答。')
        value = dict(row)
        if table == 'learning_evidence':
            value = {k: v for k, v in value.items() if k not in MUTABLE_EVIDENCE}
        return value
    key, *parts = locator.split('/')
    if not key.startswith(SETTING_PREFIXES): raise ValueError('不支持的历史作答位置。')
    row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
    if row is None: raise ValueError('历史会话不存在。')
    value = json.loads(row[0])
    for part in parts:
        value = value[int(part)] if isinstance(value, list) else value[part]
    # Draft/status and the surrounding queue can still change. The submitted
    # result is the immutable source counted by the impact inventory.
    return {'id': value.get('id') or value['question'].get('id') or locator,
            'question': value['question'], 'result': value.get('result')}


def _question(db, locator, source, questions):
    if 'question' in source: return deepcopy(source['question'])
    table = locator.partition(':')[0]
    if table == 'learning_evidence':
        snapshot = json.loads(source['snapshot_json'])
        opportunity = snapshot.get('opportunity', {}).get('id', '')
        if source.get('source') == 'word' and opportunity.startswith('word:'):
            # The tiny kana attribution is not the displayed spelling rubric.
            # Resolve its explicitly linked frozen whole-word opportunity.
            row = db.execute('SELECT * FROM word_spelling_attempt WHERE attempt_id=?', (opportunity[5:],)).fetchone()
            return _question(db, 'word_spelling_attempt:' + opportunity[5:], dict(row), questions) if row else None
        saved = snapshot.get('assessment_question')
        if saved is not None: return deepcopy(saved)
        choices = questions.get(snapshot.get('question_id'), {})
        return deepcopy(next(iter(choices.values()))) if len(choices) == 1 else None
    if table == 'learning_event':
        row = db.execute('SELECT snapshot_json,result_json FROM learning_question WHERE question_id=?',
                         (source['question_id'],)).fetchone()
        if row:
            return _displayed(json.loads(row[0]), json.loads(row[1]) if row[1] else None)
    if table == 'review_event':
        row = db.execute('''SELECT q.snapshot_json FROM session_question q JOIN session_answer a
            USING(session_id,question_index) WHERE a.event_id=?''', (source['event_id'],)).fetchone()
    elif table == 'word_spelling_attempt':
        row = db.execute('SELECT snapshot_json FROM session_question WHERE session_id=? AND question_index=?',
                         (source['session_id'], source['question_index'])).fetchone()
    else: row = None
    return json.loads(row[0]) if row else None


def _displayed(question, result):
    if isinstance(result, dict) and isinstance(result.get('material_view'), dict):
        from .material_review import QUESTION_VIEW_FIELDS
        return {**question, **{k: v for k, v in result['material_view'].items() if k in QUESTION_VIEW_FIELDS}}
    return question


def _aliases(locator, source):
    table, _, identity = locator.partition(':')
    if table == 'learning_evidence':
        snap = json.loads(source['snapshot_json'])
        aliases = {identity}
        opportunity = snap.get('opportunity', {}).get('id')
        if opportunity:
            aliases.add(opportunity)
            if opportunity.startswith('word:'): aliases.add(opportunity[5:])
        return sorted(aliases)
    if table in TABLE_IDS:
        return sorted({identity, *([{'learning_event': 'course:', 'review_event': 'kana:',
            'word_spelling_attempt': 'word:'}[table] + identity])})
    prefix = ('tutor:' if locator.startswith('ai_tutor_v1') else 'ai:' if locator.startswith('ai_practice_v1')
              else 'diagnostic:' if locator.startswith('retention_check_v') else 'stored:')
    return [prefix + source['id']]


def _inventory(db, plan, group, impact):
    from .content_impact import Inventory
    resources = {(r['kind'], r['id']): r['snapshot'] for r in plan['manifest']['resources']}
    changes = [{**c, 'resource': resources.get((c['binding']['resource_kind'], c['binding']['resource_id']))}
               for c in impact['changes'] if c['binding']['id'] in group['binding_ids']]
    inventory = Inventory(db, {'changes': changes})
    for row in impact['report']['rows']:
        # Restore resource membership only. An answer's kana attribution is
        # not permission to invalidate that kana's independent review history.
        if (row['kind'] in {'reference', 'teaching'}
                and row['binding_id'] in inventory.scope and row.get('target_id')):
            inventory.scope[row['binding_id']].add(row['target_id'])
    return inventory


def _component_targets(locator, source):
    table = locator.partition(':')[0]
    if table == 'word_spelling_attempt':
        spelling = json.loads(source['evidence_json'])
    elif table == 'review_event':
        spelling = json.loads(source['review_log_json']).get('word_spelling_evidence', {})
    else:
        return set()
    return {unit['content_id'] for unit in spelling.get('units', [])
            if isinstance(unit, dict) and isinstance(unit.get('content_id'), str)}


def _target(db, locator, source, question):
    target = source.get('target_id') or (question or {}).get('target_id') or (question or {}).get('content_id')
    if not target and locator.startswith('review_event:'):
        row = db.execute('SELECT content_id FROM study_card WHERE card_id=?', (source.get('card_id'),)).fetchone()
        target = row[0] if row else None
    return target or ''


def prepare(store, plan, group, *, refresh=True):
    """Gather only scanned candidates, before taking the write transaction."""
    from .feedback_impact import PREFIX as IMPACT
    impact = store.get_setting(IMPACT + plan['impact_id'])
    bindings = set(group['binding_ids']); histories = {}
    rows = impact['report']['rows']
    if refresh:
        # Withdrawal can happen after further answers. Re-enumerate real
        # history outside the adoption transaction, not the old preview alone.
        inventory = _inventory(store._db, plan, group, impact)
        inventory.persisted_questions(); inventory.settings(); inventory.evidence()
        rows = list(inventory.rows.values())
    for row in rows:
        if row['kind'] == 'history' and row['binding_id'] in bindings:
            histories.setdefault(row['locator'], []).append(deepcopy(row))
    raw = {locator: _source(store._db, locator) for locator in histories}
    questions = {}; resolved = {}
    for locator, source in raw.items():
        if locator.startswith('learning_evidence:'): continue
        q = _question(store._db, locator, source, {})
        if q:
            q = _displayed(q, source.get('result'))
            resolved[locator] = q
            questions.setdefault(q.get('id'), {})[digest(q)] = q
    result = []
    for locator, source in raw.items():
        q = resolved.get(locator) or _question(store._db, locator, source, questions)
        target = _target(store._db, locator, source, q)
        ability = source.get('ability') or (q or {}).get('ability') or ''
        candidate = {'locator': locator, 'source_sha256': digest(source), 'source_snapshot': source, 'target_id': target,
            'ability': ability, 'question': q, 'scan': 'current_history' if refresh else 'impact',
            'hits': sorted(histories[locator], key=lambda h: h['id']),
            'aliases': _aliases(locator, source)}
        result.append(candidate)
        for component in sorted(_component_targets(locator, source)):
            result.append({**deepcopy(candidate), 'target_id': component, 'ability': '', 'component_target': component})
    from .content_queue import prepare as prepare_queues
    return {'candidates': sorted(result, key=lambda c: c['locator']),
            'questions': prepare_queues(store._db, rows, bindings)}


def _verdict(store, candidate, operations, *, withdrawal, inventory):
    if not withdrawal and _unchanged_word_component(store, candidate, operations, inventory=inventory):
        return 'valid', ['same_component_diagnostic_under_corrected_reading']
    reasons = []; unsafe = False; invalid = False
    matches = {h[0]: h for h in inventory.match_question(candidate['question'] or {})}
    for hit in candidate['hits']:
        op = operations[hit['binding_id']]; field = op['field']; kind = op['owner']['kind']
        match = matches.get(hit['binding_id'])
        reason, version = (match[2], match[3]) if match else ('unresolved_provenance', 'unknown')
        roles = set(reason.split('factory_field_uses:', 1)[1].split(',')) if 'factory_field_uses:' in reason else set()
        scoring_change = field == 'answer'
        if withdrawal and kind == 'question' and field == 'rubric':
            from .personal_question import bundle
            q = candidate['question'] or {}
            if (q.get('personal_rubric', {}).get('owner_id') == op['owner']['id']
                    and bundle(q) == json.loads(op['after'])):
                unsafe = True; reasons.append('withdrawn_basis'); continue
        if kind == 'question' and field == 'rubric' and version == 'same':
            from .personal_question import bundle
            before, after = bundle(candidate['question']), json.loads(op['after'])
            changed = {k for k in before if before[k] != after[k]}
            if changed <= {'feedback'}:
                reasons.append('after_answer_or_admission_only'); continue
            scoring_change = bool(changed & {'answer', 'accepted_answers', 'alternative_answers',
                                             'retired_ambiguous_answers', 'known_wrong', 'pitch_practice'})
        # A changed prerequisite describes future admission, never disproves an
        # independently observed past answer. Exact after-answer use is safe.
        if field == 'prerequisites' or roles == {'after_answer'} or kind == 'question' and field in {'feedback', 'explanation'}:
            reasons.append('after_answer_or_admission_only'); continue
        if (not withdrawal and kind == 'reference' and field == 'link'
                and _independent_reference_score(store, candidate, op, inventory.roots[hit['binding_id']])):
            reasons.append('independent_assessment_after_reference_removal'); continue
        if not withdrawal and version == 'same' and (
                'rubric' in roles or kind == 'question' and scoring_change
                and 'exact_question_identity' in reason):
            invalid = True; reasons.append('corrected_scoring_basis'); continue
        # A full unchanged factory contract is positive proof for a potential
        # same-target hit. Identity alone, missing versions and background links
        # are never interpreted as a certain scoring error.
        q = candidate['question']
        if (kind in {'lesson', 'question'} and q and q.get('target_id') == candidate['target_id']
                and (kind == 'question' or op['owner']['id'] == q['target_id'])):
            from .personal_content_versions import question_review
            if not question_review(store, {'id': q['target_id']}, q, for_history=True)['blocked']:
                reasons.append('unchanged_frozen_assessment'); continue
        unsafe = True; reasons.append('withdrawn_basis' if withdrawal else 'unresolved_assessment_dependency')
    return ('invalid' if invalid else 'ambiguous' if unsafe else 'valid'), sorted(set(reasons))


def _independent_reference_score(store, candidate, operation, change):
    """A pure link removal cannot revoke an exact other-target assessment.

    A shared sentence or target ID alone is insufficient. Require the original
    reference, a deletion-only delta, and the unchanged local factory contract.
    Reworded sentences, moved spans, imported/AI tasks and the deleted target
    continue through the ordinary conservative adjudication path.
    """
    q = candidate.get('question') or {}; original = change.get('resource') or {}
    target = candidate.get('target_id')
    if (not target or q.get('target_id') != target or not original.get('target_id')
            or target == original['target_id']): return False
    try:
        before = json.loads(change['binding']['value']); after = json.loads(operation['after'])
        if before.get('active') is not True or after != {**before, 'active': False}: return False
        saved = store._db.execute('SELECT content_json FROM learning_content WHERE content_id=?', (target,)).fetchone()
        if not saved: return False
        from .question_dependencies import factory_matches
        from .personal_content_versions import question_review
        base = json.loads(saved[0])
        return (factory_matches(q, base)
                and not question_review(store, base, q, for_history=True)['blocked'])
    except (KeyError, TypeError, ValueError):
        return False


def _unchanged_word_component(store, candidate, operations, *, inventory=None):
    """Positive, bounded proof for plain-kana components, never string overlap.

    Re-run the existing all-optimal-path spelling analysis before and after an
    exact reading correction. Combinations, changed cues/variants and missing
    source records remain uncertain; this never awards a newly correct grade.
    """
    source = candidate.get('source_snapshot') or {}; table = candidate.get('locator', '').partition(':')[0]
    if table == 'learning_evidence' and source.get('source') == 'word':
        opportunity = json.loads(source['snapshot_json']).get('opportunity', {}).get('id', '')
        if not opportunity.startswith('word:'): return False
        db = getattr(store, '_content_history_source', store._db)
        row = db.execute('SELECT * FROM word_spelling_attempt WHERE attempt_id=?', (opportunity[5:],)).fetchone()
        if not row: return False
        source = dict(row); table = 'word_spelling_attempt'
    elif not candidate.get('component_target'):
        return False
    if table == 'word_spelling_attempt':
        spelling = json.loads(source['evidence_json'])
    elif table == 'review_event':
        spelling = json.loads(source['review_log_json']).get('word_spelling_evidence', {})
    else: return False
    q = candidate.get('question') or {}; target = candidate['target_id']
    unit = next((u for u in spelling.get('units', []) if u.get('content_id') == target), None)
    old, actual, script = (spelling.get(k) for k in ('expected', 'actual', 'script'))
    if (not unit or q.get('kind') not in {'word_kana', 'word_meaning_kana'}
            or q.get('answer') != old or script not in {'hiragana', 'katakana'}
            or spelling.get('assisted') or spelling.get('used_conversion') or spelling.get('accepted_alternative')):
        return False
    new = old
    for hit in candidate['hits']:
        op = operations[hit['binding_id']]
        # Saved operations bind the old value by hash; the immutable impact
        # root owns its actual value. Do not infer it from today's content.
        previous = op.get('before')
        if inventory is not None:
            previous = inventory.roots.get(hit['binding_id'], {}).get('binding', {}).get('value')
        if not isinstance(previous, str) or op.get('before_sha256', digest(previous)) != digest(previous):
            return False
        if op['field'] == 'answer' and op['owner']['kind'] == 'question' and op['owner']['id'] == q.get('id'):
            before, after = previous, op['after']
        elif op['field'] == 'reading' and (op['owner']['id'] == q.get('content_id') or inventory and any(
                m[0] == hit['binding_id'] and m[2].startswith('exact_word_sense_') for m in inventory.match_question(q))):
            from .domain import to_script
            before, after = to_script(previous, script), to_script(op['after'], script)
        else: return False
        if before != old or new != old and new != after: return False
        new = after
    # Without a frozen full catalogue only plain one-character tokens have an
    # unambiguous segmentation. Small signs and combinations cannot borrow it.
    small = set('ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ')
    texts = (old, actual, new)
    if any(not isinstance(t, str) or len(t) > 64 for t in texts): return False
    chars = set(''.join(texts))
    if any(c in small or not ('ぁ' <= c <= 'ゖ' or 'ァ' <= c <= 'ヶ') for c in chars): return False
    if len(unit.get('symbol', '')) != 1: return False
    from .word_spelling import analyze_spelling
    rows = [{'id': target if c == unit['symbol'] else 'proof:' + str(ord(c)), script: c} for c in chars]
    accepted = q.get('accepted_answers', [])
    before = analyze_spelling(old, actual, script, rows, accepted_answers=accepted)
    after = analyze_spelling(new, actual, script, rows, accepted_answers=accepted)
    def measured(analysis):
        return next((u for u in analysis['units'] if u['content_id'] == target), None)
    fields = ('content_id', 'symbol', 'result', 'error_types')
    original = {k: unit.get(k) for k in fields}
    return measured(before) == original and measured(after) == original


def apply_in_transaction(store, event, plan, group, candidates, *, withdrawal=False):
    """Called after changing heads, within the same CAS-protected transaction."""
    db = store._db
    if not db.in_transaction: raise RuntimeError('History adjudication requires the content transaction')
    operations = {o['binding_id']: o for o in group['operations']}
    from .feedback_impact import PREFIX as IMPACT
    inventory = _inventory(db, plan, group, store.get_setting(IMPACT + plan['impact_id']))
    from .learning_evidence import LearningEvidenceStore
    evidence = LearningEvidenceStore(store)
    for candidate in candidates['candidates']:
        if digest(_source(db, candidate['locator'])) != candidate['source_sha256']:
            raise ValueError('准备后原始作答已变化，请重新预览。')
        verdict, reasons = _verdict(store, candidate, operations, withdrawal=withdrawal, inventory=inventory)
        decision = {'format': FORMAT, 'version': 1, 'content_event_id': event['id'],
            'content_event_kind': 'withdrawal' if withdrawal else 'adoption', 'plan_id': plan['id'],
            'group_id': group['id'], 'generation': event['generation'], 'created_at': event['created_at'],
            'candidate': candidate, 'verdict': verdict, 'reasons': reasons}
        decision['id'] = digest(decision)
        key = PREFIX + decision['id']
        old = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        if old:
            if json.loads(old[0]) != decision: raise ValueError('历史裁定不能改写。')
            continue
        db.execute('INSERT INTO app_setting VALUES (?,?)', (key, dump(decision)))
        if verdict == 'valid': continue
        for alias in candidate['aliases']:
            key = index_key(alias, candidate['target_id'], candidate['ability'])
            # Also provide the target-only projection for component consumers
            # that intentionally do not aggregate an independent ability.
            for projected in {key, index_key(alias, candidate['target_id'])}:
                db.execute('INSERT OR IGNORE INTO app_setting VALUES (?,?)', (projected, dump(decision['id'])))
        if candidate['locator'].startswith('learning_evidence:'):
            identity = candidate['locator'].removeprefix('learning_evidence:')
            evidence.retract_in_transaction(identity, 'content_history:' + decision['id'],
                                            now=datetime.fromisoformat(event['created_at']))
    from .content_queue import apply_in_transaction as apply_queues
    apply_queues(store, event, plan, group, candidates['questions'], inventory, withdrawal=withdrawal)


def validate(value):
    fields = {'format', 'version', 'id', 'content_event_id', 'content_event_kind', 'plan_id', 'group_id',
              'generation', 'created_at', 'candidate', 'verdict', 'reasons'}
    if (not isinstance(value, dict) or set(value) != fields or value['format'] != FORMAT or value['version'] != 1
            or value['id'] != digest({k: v for k, v in value.items() if k != 'id'})
            or value['content_event_kind'] not in {'adoption', 'withdrawal'}
            or value['verdict'] not in {'valid', 'invalid', 'ambiguous'}
            or type(value['generation']) is not int or value['generation'] < 1
            or datetime.fromisoformat(value['created_at']).tzinfo is None
            or not isinstance(value['reasons'], list) or not value['reasons']):
        raise ValueError('内容历史裁定格式无效。')
    c = value['candidate']
    fields = {'locator', 'source_sha256', 'source_snapshot', 'target_id', 'ability', 'question', 'hits', 'aliases', 'scan'}
    if (not isinstance(c, dict) or set(c) not in (fields, fields | {'component_target'})
            or not isinstance(c['hits'], list) or not c['hits'] or not isinstance(c['aliases'], list)
            or c['scan'] not in {'impact', 'current_history'}
            or not isinstance(c['source_snapshot'], dict) or digest(c['source_snapshot']) != c['source_sha256']
            or any(not isinstance(c[k], str) for k in ('locator', 'source_sha256', 'target_id', 'ability'))
            or c['question'] is not None and not isinstance(c['question'], dict)):
        raise ValueError('内容历史裁定来源无效。')
    return value


def validate_database(db):
    with _HistoricalCache(db) as history:
        return _validate_database(db, history)


def _validate_database(db, history):
    from .personal_content_versions import PREFIX as ADOPT
    from .personal_content_compensation import PREFIX as WITHDRAW
    from .content_correction_plan import PREFIX as PLAN
    from .feedback_impact import PREFIX as IMPACT
    def setting(key):
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None
    expected = {}; question_index = {}
    decisions = [validate(json.loads(raw)) for key, raw in db.execute(
        "SELECT key,value_json FROM app_setting WHERE key GLOB ? ORDER BY json_extract(value_json,'$.generation'),rowid", (PREFIX + '*',))]
    for decision in decisions:
        c = decision['candidate']
        if c['locator'].startswith('learning_evidence:'): continue
        q = _question(db, c['locator'], c['source_snapshot'], {})
        if q:
            q = _displayed(q, c['source_snapshot'].get('result'))
            question_index.setdefault(q.get('id'), {})[digest(q)] = q
    for d in decisions:
        if setting(PREFIX + d['id']) != d: raise ValueError('历史裁定身份不符。')
        event = setting((ADOPT if d['content_event_kind'] == 'adoption' else WITHDRAW) + d['content_event_id'])
        plan = setting(PLAN + d['plan_id'])
        if not event or not plan or event['generation'] != d['generation'] or event['created_at'] != d['created_at']:
            raise ValueError('历史裁定缺少内容事件。')
        adoption = event if d['content_event_kind'] == 'adoption' else setting(ADOPT + event['adoption_id'])
        if not adoption or (adoption['plan_id'], adoption['group_id']) != (d['plan_id'], d['group_id']):
            raise ValueError('历史裁定不属于此内容修订。')
        group = next((g for g in plan['groups'] if g['id'] == d['group_id']), None)
        if group is None: raise ValueError('历史裁定缺少联动组。')
        c = d['candidate']; impact = setting(IMPACT + plan['impact_id'])
        source = c['source_snapshot']
        q = _question(db, c['locator'], source, question_index)
        if q: q = _displayed(q, source.get('result'))
        target = _target(db, c['locator'], source, q)
        ability = source.get('ability') or (q or {}).get('ability') or ''
        if 'component_target' in c:
            if c['component_target'] not in _component_targets(c['locator'], source):
                raise ValueError('词拼裁定包含原记录没有的假名组件。')
            target, ability = c['component_target'], ''
        if (c['question'], c['target_id'], c['ability']) != (q, target, ability):
            raise ValueError('历史裁定不能更换原始题目、目标或能力。')
        # Bounded tutor UI histories can rotate. The adjudication archives the
        # original result; permanent event-table sources must still be present.
        try: current_source = _source(db, c['locator'])
        except (ValueError, KeyError, IndexError):
            if c['locator'].partition(':')[0] in TABLE_IDS: raise
            current_source = c['source_snapshot']
        if current_source.get('id') != c['source_snapshot'].get('id') and c['locator'].partition(':')[0] not in TABLE_IDS:
            current_source = c['source_snapshot']
        if (digest(current_source) != c['source_sha256'] or impact is None
                or any((c['scan'] == 'impact' and h not in impact['report']['rows'])
                       or h['kind'] != 'history' or h['locator'] != c['locator']
                       or h['binding_id'] not in group['binding_ids'] for h in c['hits'])
                or c['aliases'] != _aliases(c['locator'], c['source_snapshot'])):
            raise ValueError('历史裁定与原始作答或影响预览不一致。')
        inventory = _inventory(db, plan, group, impact)
        matches = {h[0] for h in inventory.match_question(c['question'] or {})}
        for hit in c['hits']:
            scope = inventory.scope[hit['binding_id']]
            if hit['binding_id'] not in matches and c['target_id'] not in scope:
                raise ValueError('裁定没有对应的作答内容关联。')
        verdict, reasons = _verdict(history.at(d['generation']), c,
            {o['binding_id']: o for o in group['operations']},
            withdrawal=d['content_event_kind'] == 'withdrawal', inventory=inventory)
        if (verdict, reasons) != (d['verdict'], d['reasons']):
            raise ValueError('历史裁定与当时的内容版本不一致。')
        if d['verdict'] == 'valid': continue
        for alias in c['aliases']:
            for key in {index_key(alias, c['target_id'], c['ability']), index_key(alias, c['target_id'])}:
                expected.setdefault(key, d['id'])
        if c['locator'].startswith('learning_evidence:'):
            row = db.execute('SELECT retracted FROM learning_evidence WHERE event_id=?', (c['locator'][18:],)).fetchone()
            if row is None or not row[0]: raise ValueError('无效归因仍参与学习投影。')
    actual = {key: json.loads(raw) for key, raw in db.execute(
        'SELECT key,value_json FROM app_setting WHERE key GLOB ?', (INDEX + '*',))}
    if actual != expected: raise ValueError('历史有效性索引与裁定不一致。')


class _HistoricalContent:
    """Private validation view. Never write/roll back heads in the live DB."""
    def __init__(self, source, generation):
        import sqlite3
        from .personal_content_versions import PREFIX as ADOPT, HEAD, GENERATION
        from .personal_content_compensation import PREFIX as WITHDRAW, HOLD, hold_key
        self._content_history_source = source
        self._db = sqlite3.connect(':memory:'); self._db.row_factory = sqlite3.Row
        self._db.execute('CREATE TABLE app_setting (key TEXT PRIMARY KEY,value_json TEXT)')
        self._db.execute('CREATE TABLE learning_content (content_id TEXT PRIMARY KEY,content_json TEXT)')
        rows = source.execute("SELECT key,value_json FROM app_setting WHERE key GLOB 'personal_content_version*' OR key GLOB 'feedback_*' OR key GLOB 'content_correction_plan_*'")
        for key, raw in rows:
            if key.startswith((HEAD, HOLD)) or key == GENERATION: continue
            self._db.execute('INSERT INTO app_setting VALUES (?,?)', (key, raw))
        if source.execute("SELECT 1 FROM sqlite_master WHERE name='learning_content'").fetchone():
            self._db.executemany('INSERT INTO learning_content VALUES (?,?)',
                source.execute('SELECT content_id,content_json FROM learning_content'))
        events = [json.loads(raw) for raw, in source.execute('SELECT value_json FROM app_setting WHERE key GLOB ? OR key GLOB ?', (ADOPT + '*', WITHDRAW + '*'))]
        for event in sorted(events, key=lambda e: e['generation']):
            if event['generation'] > generation: break
            withdrawn = 'restored_heads' in event
            heads = event['restored_heads'] if withdrawn else {k: event['id'] for k in event['previous_heads']}
            for key, identity in heads.items():
                self._db.execute('INSERT OR REPLACE INTO app_setting VALUES (?,?)', (key, dump(identity)))
                self._db.execute('INSERT OR REPLACE INTO app_setting VALUES (?,?)',
                    (hold_key(key), dump(event['id'] if withdrawn else None)))
            self._db.execute('INSERT OR REPLACE INTO app_setting VALUES (?,?)', (GENERATION, dump(event['generation'])))

    def get_setting(self, key, default=None):
        row = self._db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def __enter__(self): return self
    def __exit__(self, *args): self._db.close()


class _HistoricalCache:
    def __init__(self, db): self.db, self.generation, self.view = db, None, None
    def at(self, generation):
        if generation != self.generation:
            if self.view is not None: self.view._db.close()
            self.view = _HistoricalContent(self.db, generation); self.generation = generation
        return self.view
    def __enter__(self): return self
    def __exit__(self, *args):
        if self.view is not None: self.view._db.close()
