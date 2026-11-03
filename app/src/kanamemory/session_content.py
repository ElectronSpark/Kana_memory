"""Content retirement and explicit replacement in ordinary frozen queues."""
from copy import deepcopy
from datetime import datetime
import json

from .material_workflow import digest, dump
from .feedback_impact import token

RETIRE = 'content_retirement_v1'
REPLACED = 'content_replacement_v1'
PREFIX = 'session_content_replacement_v1:'


def retired(question):
    meta = question.get('metadata', {}) if isinstance(question, dict) else question.metadata
    return bool(meta.get(RETIRE))


def fingerprint(question):
    from .domain import _question_from_snapshot
    raw = question if isinstance(question, dict) else question.to_dict()
    # UI controls project old compound options to single kana. This existing
    # projection must not make a current page look like a stale contract.
    return digest(_question_from_snapshot(raw).to_dict())


def _draft(question, current):
    parked = question.get('metadata', {}).get('word_kind_pause_v1')
    return deepcopy(parked['state'] if parked and parked.get('state') else current)


def _exposed(entry):
    return bool(entry['assisted'] or any(state.get(k)
        for state in (entry['draft'], entry['question'].get('metadata', {}).get('word_kind_pause_v1', {}).get('state', {}))
        for k in ('answer_revealed', 'feedback_pending', 'word_used_conversion')))


def prepare(db, rows, binding_ids):
    grouped = {}
    for hit in rows:
        if (hit['kind'] == 'question' and hit['binding_id'] in binding_ids
                and hit['locator'].startswith('session_question:') and not hit['locator'].endswith(':stored_original')):
            grouped.setdefault(hit['locator'], []).append(hit)
    result = []
    for locator, hits in sorted(grouped.items()):
        sid, ordinal = locator.removeprefix('session_question:').rsplit(':', 1); index = int(ordinal)
        qrow = db.execute('SELECT snapshot_json FROM session_question WHERE session_id=? AND question_index=?', (sid, index)).fetchone()
        checkpoint = db.execute('SELECT * FROM session_checkpoint WHERE session_id=?', (sid,)).fetchone()
        if not qrow or not checkpoint or index < checkpoint['question_index']: continue
        if answered(db, sid, index): continue
        q = json.loads(qrow[0])
        if retired(q): continue
        draft = _draft(q, json.loads(checkpoint['state_json']) if index == checkpoint['question_index'] else {})
        entry = {'id': digest([sid, index, q]), 'question': q, 'result': None, 'draft': draft,
                 'session_id': sid, 'question_index': index,
                 'assisted': index in json.loads(checkpoint['assisted_indices_json'])}
        result.append({'key': 'session_question:' + sid, 'entry': entry, 'hits': deepcopy(hits)})
    return result


def answered(db, sid, index):
    return db.execute('''SELECT 1 FROM session_answer WHERE session_id=? AND question_index=?
        UNION ALL SELECT 1 FROM word_spelling_attempt WHERE session_id=? AND question_index=?''',
        (sid, index, sid, index)).fetchone() is not None


def retire_in_transaction(store, candidate, receipt):
    from . import session_questions
    entry = candidate['entry']; sid, index = entry['session_id'], entry['question_index']
    rows = store._session_snapshot(sid)
    checkpoint = store._db.execute('SELECT * FROM session_checkpoint WHERE session_id=?', (sid,)).fetchone()
    current = json.loads(checkpoint['state_json']) if checkpoint and checkpoint['question_index'] == index else {}
    if rows[index] != entry['question'] or _draft(rows[index], current) != entry['draft'] or answered(store._db, sid, index):
        raise ValueError('原题或草稿已变化，请重新准备内容修订。')
    changed = deepcopy(rows); changed[index].setdefault('metadata', {})[RETIRE] = receipt['id']
    session_questions.replace(store._db, sid, changed)
    from .kana_rule_evidence import retire_probe_question
    retire_probe_question(store, receipt)
    if checkpoint and checkpoint['question_index'] == index:
        # Includes an answer shown before the four rating buttons. The full
        # first-answer state lives in the immutable retirement receipt.
        store._db.execute("UPDATE session_checkpoint SET state_json='{}' WHERE session_id=?", (sid,))


def assert_current(store, sid, index, question, expected_fingerprint=None):
    if retired(question): raise ValueError('这道题的内容已更新，原题和草稿已保存，请更新后继续。')
    if question.get('metadata', {}).get(REPLACED) and expected_fingerprint != fingerprint(question):
        raise ValueError('题目已替换，请重新打开当前练习。')
    if expected_fingerprint is not None and expected_fingerprint != fingerprint(question):
        raise ValueError('当前页面不是保存的这道题，请重新打开。')


def _replacement_source(store, receipt):
    """Bind a corrected lesson to one exact old word sense, never its homonyms."""
    from .content_correction_plan import PREFIX as PLANS
    from .personal_content_versions import resolve, teaching_source
    from .personal_content_compensation import held
    plan = store.get_setting(PLANS + receipt['plan_id']); old = receipt['entry']['question']
    resources = {(r['kind'], r['id']): r['snapshot'] for r in plan['manifest']['resources']}
    group = next(g for g in plan['groups'] if g['id'] == receipt['group_id'])
    source_ids = {op['owner']['id'] for op in group['operations'] if op['owner']['kind'] == 'lesson'}
    matches = []
    for identity in sorted(source_ids):
        base = resources.get(('lesson', identity))
        if (not base or base.get('family') != 'word' or base.get('word_id') != old['content_id']
                or base.get('reading') != old.get('metadata', {}).get('original_reading')
                or old.get('metadata', {}).get('meaning_hint') not in {base.get('meaning'), *base.get('accepted_meanings', [])}):
            continue
        current = resolve(store, teaching_source(base))
        if held(store, identity) or current.get('personal_content_unavailable') or current.get('personal_reading', {}).get('pending'):
            continue
        if current.get('alternative_readings') or len(current.get('accepted_readings', [])) > 1: continue
        if not current.get('personal_revision'): continue
        matches.append({'base': base, 'current': current})
    if len(matches) != 1:
        raise ValueError('这道旧题尚不能唯一对应已核实的读法和词义。原题与草稿保留，可先继续其他题。')
    return matches[0]


def _build(store, old, source, catalogue, metadata):
    from .domain import build_questions, _BatchQuestionStore
    from .word_romaji_practice import _pool, reverse_word_question
    class Repository:
        def metadata(self): return metadata
        def practice_word_candidates(self, **kw):
            return [{'id': old['content_id'], 'reading': source['current']['reading'],
                     'gloss': source['current']['meaning'], 'practice_gloss_zh': source['current']['meaning'],
                     'practice_approved': True}]
        def kana_list(self): return catalogue
        def all_kana_symbols(self, script): return [r[script] for r in catalogue if r.get(script)]
    repo = Repository(); config = old.get('metadata', {}).get('practice_config', {})
    table = old.get('metadata', {}).get('word_table', 'partial')
    seed = old.get('metadata', {}).get('session_seed', 0)
    if old['kind'] == 'word_romaji':
        rows = _pool(repo, old['script'], None, 'romaji')
        if len(rows) != 1: raise ValueError('当前读法暂不能生成这种题型。')
        meta = deepcopy(old['metadata']); meta.pop(RETIRE, None); meta.pop(REPLACED, None)
        meta.pop('romaji_unit_plan', None)
        row = rows[0]
        meta.update(original_reading=row['original'], meaning_hint=row['meaning'], romaji=row['romaji'],
                    answer_tokens=list(row['answer']))
        q = reverse_word_question(row, meta)
    else:
        values = build_questions(repo, _BatchQuestionStore(store, [old['content_id']]),
            kind=old['kind'], script=old['script'], limit=1, seed=seed,
            include_research=True, include_extended=True, word_table=table)
        if len(values) != 1: raise ValueError('当前内容或题型不能生成合格替代题。')
        q = values[0]
    # Preserve independent reasons, queue lane and original scheduling floors.
    for name in ('practice_config', 'word_romaji_drill', 'kana_context_v1', 'kana_rule_probe_v1',
                 'not_before_utc', 'relearning_of_index', 'relearning_count', 'spelling_correction_of',
                 'spelling_correction_index', 'study_path_entry', 'study_path_due_v1', 'word_kind_pause_v1'):
        if name in old.get('metadata', {}): q.metadata[name] = deepcopy(old['metadata'][name])
    q.metadata.pop('word_kind_pause_v1', None)
    # A changed reading invalidates old pitch and component annotations.
    for name in ('pitch_accent', 'accent', 'long_vowel_spans', 'reading_rule_ids'):
        q.metadata.pop(name, None)
    from .domain import word_kana_answer
    from .kana_rule_evidence import probe_matches_word
    probe = q.metadata.get('kana_rule_probe_v1')
    if probe and not probe_matches_word(probe, word_kana_answer(q)):
        q.metadata.pop('kana_rule_probe_v1')
    q.metadata['content_source_v1'] = {'lesson_id': source['current']['id'],
        'revision_id': source['current']['personal_revision'], 'reading': source['current']['reading'],
        'meaning': source['current']['meaning']}
    return json.loads(dump(q.to_dict()))


def replace(store, repo, sid, receipt_id, enabled_kinds, *, now=None):
    from . import app_clock, session_questions
    from .content_queue import PREFIX as RECEIPTS
    before_token = token(store._db)
    saved = store.get_setting(PREFIX + receipt_id, None)
    if saved: return saved
    receipt = store.get_setting(RECEIPTS + receipt_id, None)
    if not receipt or receipt['key'] != 'session_question:' + sid: raise ValueError('原题保存记录不存在。')
    rows = store._session_snapshot(sid)
    position = next((i for i, q in enumerate(rows) if q.get('metadata', {}).get(RETIRE) == receipt_id), None)
    if position is None or answered(store._db, sid, position): raise ValueError('原题已经处理。')
    old = receipt['entry']['question']
    if old['kind'] not in enabled_kinds: raise ValueError('原题型已关闭，请先开启相应题型。')
    source = _replacement_source(store, receipt)
    catalogue = repo.kana_list(); metadata = repo.metadata()
    q = _build(store, old, source, catalogue, metadata)
    stamp = now or app_clock.utc_now()
    exposure = _exposed(receipt['entry'])
    # Word examples never become formal word reviews. Exposed replacements
    # remain assisted and cannot adjust any kana interval.
    q['metadata'][REPLACED] = receipt_id
    from .personal_content_versions import GENERATION
    value = {'version': 1, 'retirement_id': receipt_id, 'session_id': sid, 'created_at': stamp.isoformat(),
             'generation': store.get_setting(GENERATION, 0),
             'question': q, 'source': source, 'catalogue': catalogue, 'metadata': metadata, 'assisted': exposure}
    value['id'] = digest(value)
    with store._db:
        store._db.execute('BEGIN IMMEDIATE')
        if token(store._db) != before_token: raise ValueError('题目或内容已变化，请重试。')
        changed = deepcopy(rows); changed[position] = q
        session_questions.replace(store._db, sid, changed)
        store._db.execute('INSERT INTO app_setting VALUES (?,?)', (PREFIX + receipt_id, dump(value)))
        checkpoint = store._db.execute('SELECT * FROM session_checkpoint WHERE session_id=?', (sid,)).fetchone()
        if checkpoint:
            assistance = set(json.loads(checkpoint['assisted_indices_json']))
            if exposure: assistance.add(position)
            store._db.execute("UPDATE session_checkpoint SET state_json=CASE WHEN question_index=? THEN '{}' ELSE state_json END,assisted_indices_json=? WHERE session_id=?",
                (position, dump(sorted(assistance)), sid))
    return value


def validate_database(db, receipts):
    from types import SimpleNamespace
    def setting(key, default=None):
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    # Word construction uses no selection history or live scheduler.
    store = SimpleNamespace(_db=db, get_setting=setting, previous_choice_options=lambda *args: {})
    replacements = {}
    for key, raw in db.execute('SELECT key,value_json FROM app_setting WHERE key GLOB ?', (PREFIX + '*',)):
        r = json.loads(raw); receipt = receipts.get(r.get('retirement_id'))
        if (set(r) != {'version', 'id', 'retirement_id', 'session_id', 'created_at', 'generation', 'question', 'source', 'catalogue', 'metadata', 'assisted'}
                or r['version'] != 1 or r['id'] != digest({k:v for k,v in r.items() if k != 'id'})
                or key != PREFIX + r['retirement_id'] or not receipt
                or receipt['key'] != 'session_question:' + r['session_id']
                or datetime.fromisoformat(r['created_at']).tzinfo is None):
            raise ValueError('普通练习替换回执无效。')
        old = receipt['entry']['question']
        base = r['source']['base']; current = r['source']['current']
        from .content_history import _HistoricalCache
        from .personal_content_versions import resolve, teaching_source
        with _HistoricalCache(db) as history:
            at_time = resolve(history.at(r['generation']), teaching_source(base))
        if current != at_time or current.get('personal_content_unavailable'):
            raise ValueError('普通替换题来源不属于当时已采纳的内容版本。')
        from .content_correction_plan import PREFIX as PLANS
        plan = setting(PLANS + receipt['plan_id'])
        if (not any(row['kind'] == 'lesson' and row['snapshot'] == base for row in plan['manifest']['resources'])
                or base.get('word_id') != old['content_id'] or current.get('id') != base.get('id')
                or base.get('reading') != old.get('metadata', {}).get('original_reading')
                or old.get('metadata', {}).get('meaning_hint') not in {base.get('meaning'), *base.get('accepted_meanings', [])}):
            raise ValueError('普通词拼替换改变了原词义范围。')
        q = _build(store, old, r['source'], r['catalogue'], r['metadata']); q['metadata'][REPLACED] = r['retirement_id']
        exposed = _exposed(receipt['entry'])
        if q != r['question'] or r['assisted'] != exposed: raise ValueError('替换题不属于冻结来源，或丢失帮助状态。')
        replacements[r['retirement_id']] = r
    # Every replacement has exactly one live owner or one archived successor.
    # Reordering the unfinished suffix changes ordinals, never its ownership.
    owners = {identity: 0 for identity in replacements}
    retired_owners = {}
    for identity, receipt in receipts.items():
        if not receipt['key'].startswith('session_question:'): continue
        old = receipt['entry']['question']; parent = old.get('metadata', {}).get(REPLACED)
        if not parent: continue
        r = replacements.get(parent); original = deepcopy(old)
        original['metadata'].pop('word_kind_pause_v1', None)
        if (not r or receipt['key'] != 'session_question:' + r['session_id']
                or r['question'] != original or receipt['generation'] <= r['generation']):
            raise ValueError('再次修订的普通题没有唯一的前次替换来源。')
        owners[parent] += 1
    for sid, index, raw in db.execute('SELECT session_id,question_index,snapshot_json FROM session_question'):
        q = json.loads(raw); meta = q.get('metadata', {})
        if meta.get('content_source_v1') and not meta.get(REPLACED):
            raise ValueError('普通题的修订来源缺少替换回执。')
        if meta.get(RETIRE):
            r = receipts.get(meta[RETIRE]); original = deepcopy(q); original['metadata'].pop(RETIRE)
            if (not r or r['key'] != 'session_question:' + sid or r['entry']['question'] != original or answered(db, sid, index)):
                raise ValueError('待更新普通题缺少原题或被重复作答。')
            retired_owners[meta[RETIRE]] = retired_owners.get(meta[RETIRE], 0) + 1
        if meta.get(REPLACED):
            r = replacements.get(meta[REPLACED]); original = deepcopy(q); original['metadata'].pop(RETIRE, None)
            # Settings may park a draft later; this is not a new contract.
            original['metadata'].pop('word_kind_pause_v1', None)
            if not r or r['session_id'] != sid or r['question'] != original:
                raise ValueError('普通替代题缺少相符回执。')
            if not meta.get(RETIRE): owners[meta[REPLACED]] += 1
    if any(count != 1 for count in owners.values()):
        raise ValueError('普通替代题重复出现或丢失后续记录。')
    for identity, receipt in receipts.items():
        if receipt['key'].startswith('session_question:'):
            expected = 0 if identity in replacements else 1
            if retired_owners.get(identity, 0) != expected:
                raise ValueError('普通待更新题重复出现或原队列丢失。')
