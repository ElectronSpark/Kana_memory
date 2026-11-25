"""Reviewed error opportunities, independent of example identity and UI mode.

Raw observations live in the existing evidence ledger. Two disposable indexes
are rebuilt on restore/retraction; neither is another scheduler or backup format.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import math
import threading

from .content_caches import register as register_content_cache
from .material_review import fingerprint

_SHARED = {}
register_content_cache(lambda: _SHARED.clear(), "semantic_errors._SHARED")  # layered namespace
_LOCAL = threading.local()  # set while entries are resolved: the questions built then look up by stored key only


def _shared():
    """(stamp, read-only shared copy of the installed annotations, {(target, kind): equivalence index}).

    load_namespace deep-copies the whole namespace per call, and for_question
    runs per question/keystroke. Reuse one copy until the pack file changes.
    The loader is looked up per call so a replaced loader is honoured.
    """
    from . import content_pack
    loader, path = content_pack.load_namespace, content_pack.pack_path()
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    key = (loader, str(path), stamp)
    entry = _SHARED.get('entry')
    if entry is None or entry[0] != key:
        entry = _SHARED['entry'] = (key, loader('semantic_assessments', {}), {})
    return entry


def _assessments():
    """Read-only shared copy of the installed annotations; never mutate it."""
    return _shared()[1]


def entries():
    """{stored key: entry} of the installed pack (read-only; for reports such as tools/check_landing.py)."""
    return _assessments().get('questions', {})


def question_key(q):
    """The key of a pack entry as stored: the whole question, its task wording included."""
    fields = ('target_id', 'revision', 'ability', 'kind', 'prompt', 'task', 'answer', 'input_mode')
    return fingerprint({**{k: q.get(k) for k in fields},
        'choices': sorted(q.get('choices', [])), 'accepted_answers': sorted(q.get('accepted_answers', []))})


def sentence_key(q):
    """The one task-free equivalence of semantic errors: an entry belongs to a question by its sentence (prompt),
    answer and kind, within its lesson and revision. The task wording plays no part, so a reworded or shortened
    task keeps the entry; for_question still refuses an entry whose wrong answers the question accepts or (as a
    choice question) does not offer.

    Learner rows are already keyed without the task (source_key: lesson, sentence, answer; pattern buckets by
    the entry's conditions), so this key is needed only to reach the pack entry; nothing stored changes.
    """
    return fingerprint({k: q.get(k) for k in ('target_id', 'revision', 'ability', 'kind', 'prompt', 'answer')})


def source_key(target_id, prompt, answer):
    """The key of a recorded sentence in a learner's rows (as stored since 0.28.3)."""
    return fingerprint([target_id, prompt, answer])


def entry_source(entry):
    """(target_id, kind, variant) the entry's source_id names ("course:<target>:<kind>:<variant>"), or None."""
    source = entry.get('source_id') if isinstance(entry, dict) else None
    if not isinstance(source, str) or not source.startswith('course:'):
        return None
    parts = source[len('course:'):].rsplit(':', 2)
    if len(parts) != 3 or not parts[2].isdigit():
        return None
    return parts[0], parts[1], int(parts[2])


def resolve_entry(key, entry):
    """The sentence_key of the question an entry was written for, or None when no template of its lesson gives
    the stored key any more. The template the source_id names is tried first, then the lesson's others of that
    kind (a retired template stays reconstructible, so a reworded task still finds its original)."""
    parts = entry_source(entry)
    if parts is None:
        return None
    target_id, kind, hint = parts
    from .learning_content import get_learning_item
    from .learning_questions import build_learning_question, practice_templates
    item = get_learning_item(target_id)
    if not item:
        return None
    before = getattr(_LOCAL, 'resolving', False)
    _LOCAL.resolving = True
    try:
        try:
            count = len((practice_templates(item) or {}).get(kind, []))
        except (ValueError, KeyError, TypeError):
            return None
        for variant in dict.fromkeys([hint, *range(count)]):
            try:
                q = build_learning_question(item['id'], kind, variant=variant, item=item)
            except (ValueError, KeyError, TypeError, IndexError):
                continue
            if question_key(q) == key:
                return sentence_key(q)
        return None
    finally:
        _LOCAL.resolving = before


def _equivalents(shared, target_id, kind):
    """{sentence_key: entry key} for one lesson and kind, built once per pack; a sentence that two entries
    claim is left out (each keeps its stored key)."""
    index = shared[2]
    found = index.get((target_id, kind))
    if found is None:
        claims = {}
        for key, entry in shared[1].get('questions', {}).items():
            parts = entry_source(entry)
            if parts and parts[:2] == (target_id, kind):
                resolved = resolve_entry(key, entry)
                if resolved is not None:
                    claims.setdefault(resolved, []).append(key)
        found = index[(target_id, kind)] = {k: keys[0] for k, keys in claims.items() if len(keys) == 1}
    return found


def entry_for(q):
    """(stored key, pack entry) of a question: its stored key, else the entry of the same sentence, answer
    and kind (sentence_key). None without one."""
    shared = _shared()
    questions = shared[1].get('questions', {})
    key = question_key(q)
    if key in questions:
        return key, questions[key]
    if getattr(_LOCAL, 'resolving', False) or not isinstance(q.get('target_id'), str):
        return None
    key = _equivalents(shared, q['target_id'], q.get('kind')).get(sentence_key(q))
    return (key, questions[key]) if key else None


def for_question(q):
    """Only a pinned local annotation can declare an observable error."""
    if q.get('ai_generated') or q.get('guided') or q.get('assessment_blocked_reason'):
        return None
    if any(len(str(q.get(k, ''))) > 1000 for k in ('prompt', 'answer')):
        return None  # The raw evidence snapshot must preserve the full source anchor.
    pack = _assessments()
    found = entry_for(q) if pack.get('version') == 1 else None
    if not found:
        return None
    entry = found[1]
    # The shared pack copy is read-only; the descriptor gets its own slice.
    value = {**deepcopy(entry), 'version': 1, 'source_key': source_key(q['target_id'], q['prompt'], q['answer']),
             'target_id': q['target_id'], 'ability': q['ability'], 'content_revision': str(q['revision'])}
    validate(value)
    if any(set(o['wrong_answers']) & set(q.get('accepted_answers', [q['answer']]))
           or q['input_mode'] == 'choice' and not set(o['wrong_answers']) <= set(q['choices'])
           for o in value['opportunities']):
        return None
    return value


def validate(value):
    fields = {'version', 'revision', 'conditions', 'source_id', 'source_key', 'target_id',
              'ability', 'content_revision', 'opportunities'}
    if not isinstance(value, dict) or set(value) != fields or value['version'] != 1:
        raise ValueError('Invalid semantic error descriptor')
    for name in fields - {'version', 'conditions', 'opportunities'}:
        if not isinstance(value[name], str) or not 1 <= len(value[name]) <= 256:
            raise ValueError('Invalid semantic identity')
    conditions = value['conditions']
    if (not isinstance(conditions, list) or not 1 <= len(conditions) <= 8
            or any(not isinstance(s, str) or not 1 <= len(s) <= 120 for s in conditions)
            or sorted(set(conditions)) != conditions):
        raise ValueError('Invalid semantic conditions')
    ops = value['opportunities']
    if not isinstance(ops, list) or not 1 <= len(ops) <= 8:
        raise ValueError('Invalid error opportunities')
    codes, forms = set(), set()
    for op in ops:
        if (not isinstance(op, dict) or set(op) != {'code', 'slot', 'wrong_answers'}
                or op['slot'] != 'answer' or not isinstance(op['code'], str)
                or not 1 <= len(op['code']) <= 120 or op['code'] in codes or op['code'] == 'unknown'
                or not isinstance(op['wrong_answers'], list) or not 1 <= len(op['wrong_answers']) <= 16
                or any(not isinstance(s, str) or not 1 <= len(s) <= 200 for s in op['wrong_answers'])
                or len(set(op['wrong_answers'])) != len(op['wrong_answers'])
                or forms.intersection(op['wrong_answers'])):
            raise ValueError('Ambiguous error opportunity')
        codes.add(op['code']); forms.update(op['wrong_answers'])


def observe(descriptor, result):
    """Unknown failure is not spread over every listed error type."""
    correct = result.get('correct')
    if result.get('assisted') or result.get('revealed') or correct is None:
        return []
    if correct:
        return [{'code': o['code'], 'outcome': 'avoided'} for o in descriptor['opportunities']]
    codes = [o['code'] for o in descriptor['opportunities']
             if result.get('answered_text', '').strip() in o['wrong_answers']]
    return [{'code': codes[0] if len(codes) == 1 else 'unknown',
             'outcome': 'error' if len(codes) == 1 else 'unresolved'}]


def snapshot(q, result):
    descriptor = for_question(q)
    if not descriptor:
        return None
    return {'descriptor': descriptor, 'observations': observe(descriptor, result)}


def pattern_label(target_id, pattern):
    """Presentation metadata never changes a frozen observation or its score."""
    key = fingerprint([target_id, pattern.get('ability'), pattern.get('condition_revision'),
                       pattern.get('conditions'), pattern.get('error_code')])
    value = _assessments().get('labels', {}).get(key)
    if (not isinstance(value, dict) or set(value) != {'title', 'context'}
            or any(not isinstance(value[k], str) or not 1 <= len(value[k]) <= 160 for k in value)):
        return None
    return dict(value)


def feedback_details(question, result):
    """Read-only, post-answer facts; ambiguous answers stay unclassified."""
    descriptor = for_question(question)
    if not descriptor or result.get('outcome') in {'invalid', 'device_failure', 'unresolved'}:
        return []
    details = []
    for observation in observe(descriptor, result):
        if observation['outcome'] != 'error':
            continue
        label = pattern_label(question['target_id'], {
            'ability': descriptor['ability'], 'condition_revision': descriptor['revision'],
            'conditions': descriptor['conditions'], 'error_code': observation['code']})
        if label:
            details.append({**label, 'descriptor': descriptor, 'observation': observation})
    return details


def feedback_nodes(u, question, result):
    """Only called by answer details, keeping rating controls unobstructed."""
    nodes = []
    for detail in feedback_details(question, result):
        node = u.col(u.label(detail['title'], size=15),
                     u.label(detail['context'], size=13, color=u.MUTED))
        node._feedback_fragment = {'kind': 'semantic_error_detail', 'target_id': question['target_id'],
            'question': question, 'result': result, 'detail': detail}
        nodes.append(node)
    return nodes


def validate_snapshot(value, row, raw):
    if not isinstance(value, dict) or set(value) != {'descriptor', 'observations'}:
        raise ValueError('Invalid semantic observation')
    d = value['descriptor']; validate(d)
    if d['target_id'] != row['target_id'] or d['ability'] != row['ability'] or d['content_revision'] != row['content_revision']:
        raise ValueError('Semantic observation target mismatch')
    if d['source_key'] != source_key(row['target_id'], raw['prompt'], raw['answer']) or any(
            raw['answer'] in op['wrong_answers'] for op in d['opportunities']):
        raise ValueError('Semantic source or accepted answer mismatch')
    result = {'correct': row['outcome'] == 'correct' if row['outcome'] in {'correct', 'incorrect'} else None,
              'assisted': bool(row['assisted']), 'answered_text': raw['answered_text']}
    if value['observations'] != observe(d, result) or not raw['trusted']:
        raise ValueError('Semantic observation disagrees with raw answer')


def create_indexes(db):
    db.execute('''CREATE TABLE IF NOT EXISTS semantic_error_pattern (
        bucket TEXT PRIMARY KEY,target_id TEXT NOT NULL,ability TEXT NOT NULL,
        concern REAL NOT NULL,last_at_utc TEXT NOT NULL,payload_json TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS semantic_target ON semantic_error_pattern(target_id,concern DESC)')
    db.execute('''CREATE TABLE IF NOT EXISTS semantic_error_seen (
        bucket TEXT NOT NULL,source_key TEXT NOT NULL,last_at_utc TEXT NOT NULL,
        PRIMARY KEY(bucket,source_key))''')


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def index_event(db, row):
    raw = json.loads(row['snapshot_json']); value = raw.get('semantic_errors')
    if not value:
        return
    d = value['descriptor']; now = datetime.fromisoformat(row['occurred_at_utc'])
    exposure_key = fingerprint([row['target_id'], row['ability'], 'source_exposure'])
    seen = db.execute('SELECT last_at_utc FROM semantic_error_seen WHERE bucket=? AND source_key=?',
                      (exposure_key, d['source_key'])).fetchone()
    independent = seen is None or now - datetime.fromisoformat(seen[0]) >= timedelta(hours=20)
    # All actual exposures, including assisted/unknown answers, restart the
    # source cooldown for every error opportunity, without scoring those errors.
    db.execute('INSERT OR REPLACE INTO semantic_error_seen VALUES (?,?,?)',
               (exposure_key, d['source_key'], row['occurred_at_utc']))
    for observation in value['observations'] if row['eligible'] and not row['retracted'] else ():
        bucket = fingerprint([row['target_id'], row['ability'], d['revision'], d['conditions'], observation['code']])
        old = db.execute('SELECT payload_json FROM semantic_error_pattern WHERE bucket=?', (bucket,)).fetchone()
        state = json.loads(old[0]) if old else {
            'error_code': observation['code'], 'condition_revision': d['revision'], 'conditions': d['conditions'],
            'ability': row['ability'], 'kind': '*', 'content_revision': row['content_revision'],
            'opportunities': 0, 'errors': 0, 'successes': 0, 'uncertain': 0,
            'concern': 0., 'days': [], 'episodes': [], 'contexts': [], 'error_contexts': [], 'examples': [], 'semantic': True,
            'last_independent_at_utc': row['occurred_at_utc']}
        state.setdefault('episodes', [])  # Disposable pre-release caches may lack this coverage field.
        # Repeating a just-seen sentence never supplies fresh counterevidence.
        # This is a conservative engineering cooldown, not a retention estimate.
        if independent:
            outcome = observation['outcome']
            state['opportunities'] += int(outcome != 'unresolved')
            state['errors'] += int(outcome == 'error')
            state['successes'] += int(outcome == 'avoided')
            state['uncertain'] += int(outcome == 'unresolved')
            state['concern'] = state['concern'] * .60 if outcome == 'avoided' else (
                state['concern'] + .25 * (1 - state['concern']) if outcome == 'error' else state['concern'])
            if outcome == 'error':
                state['days'] = sorted(set([*state['days'], now.date().isoformat()]))[-2:]
                if raw.get('episode_id'):
                    state['episodes'] = list(dict.fromkeys([*state['episodes'], raw['episode_id']]))[-2:]
                state['error_contexts'] = list(dict.fromkeys([*state['error_contexts'], d['source_key']]))[-2:]
            state['contexts'] = list(dict.fromkeys([*state['contexts'], d['source_key']]))[-3:]
            state['last_independent_at_utc'] = row['occurred_at_utc']
        example = {'event_id': row['event_id'], 'source_key': d['source_key'], 'condition_source': d['source_id'],
                   'source': row['source'], 'outcome': row['outcome'], 'error_category': observation['code'],
                   'occurred_at_utc': row['occurred_at_utc'], 'independent': independent,
                   'answered_text': raw['answered_text'], 'answer': raw['answer'],
                   'prompt': raw['prompt'][:240], 'assisted': False}
        state['examples'] = [*state['examples'], example][-3:]
        state['last_at_utc'] = row['occurred_at_utc']
        if state['content_revision'] != row['content_revision']:
            state['content_revision'] = ''  # Compatibility is the semantic revision, never a guessed item alias.
        db.execute('INSERT OR REPLACE INTO semantic_error_pattern VALUES (?,?,?,?,?,?)',
                   (bucket, row['target_id'], row['ability'], state['concern'], row['occurred_at_utc'], _dump(state)))


def rebuild(db, target_id=None):
    """Maintenance only, inside restore/retraction transactions; no schedule writes."""
    create_indexes(db)
    if target_id is None:
        db.execute('DELETE FROM semantic_error_seen'); db.execute('DELETE FROM semantic_error_pattern')
    else:
        for row in db.execute('SELECT DISTINCT ability FROM learning_evidence WHERE target_id=?', (target_id,)):
            db.execute('DELETE FROM semantic_error_seen WHERE bucket=?', (fingerprint([target_id, row[0], 'source_exposure']),))
        db.execute('DELETE FROM semantic_error_pattern WHERE target_id=?', (target_id,))
    sql = 'SELECT * FROM learning_evidence WHERE 1=1'
    args = ()
    if target_id is not None:
        sql += ' AND target_id=?'; args = (target_id,)
    for row in db.execute(sql + ' ORDER BY occurred_at_utc,event_id', args):
        index_event(db, row)


def context(db, target_id, now):
    rows = db.execute('''SELECT payload_json FROM semantic_error_pattern WHERE target_id=?
        ORDER BY concern DESC,last_at_utc DESC LIMIT 8''', (target_id,)).fetchall()
    recent = db.execute('''SELECT payload_json FROM semantic_error_pattern WHERE target_id=?
        ORDER BY last_at_utc DESC LIMIT 4''', (target_id,)).fetchall()
    result = []
    for payload in dict.fromkeys(row[0] for row in [*rows, *recent]):
        state = json.loads(payload); age = max(0., (now - datetime.fromisoformat(state['last_independent_at_utc'])).total_seconds() / 86400)
        state['concern'] = round(state['concern'] * math.exp(-age / 90), 6)
        state['status'] = 'stale' if age > 90 else ('recurring' if len(state.get('episodes', [])) >= 2 and len(state['error_contexts']) >= 2 else 'tentative')
        state['coverage_basis'] = 'explicit_episode_ids_and_sources_unknown_sessions_remain_tentative'
        result.append(state)
    return sorted(result, key=lambda x: -x['concern'])[:4]


def priority(descriptor, patterns):
    if not descriptor:
        return 0.
    codes = {o['code'] for o in descriptor['opportunities']}
    return max((p['concern'] for p in patterns if p.get('semantic') and p['error_code'] in codes
                and p['condition_revision'] == descriptor['revision'] and p['conditions'] == descriptor['conditions']
                and p['ability'] == descriptor['ability']), default=0.)
