"""Independent introductions for abilities added to an existing memory item.

The original content row, its self-report and its units remain their owners.
This sidecar supplies a virtual instruction row only for each new ability.
"""
from datetime import datetime
from hashlib import sha256
import json
from uuid import uuid4

PREFIX = 'ability_teaching_v1:'
FIELDS = {'introduction_id', 'stage', 'checkpoint_json', 'exposure_group',
          'introduced_at_utc', 'guided_at_utc', 'retrieval_at_utc'}


def key(cid):
    return PREFIX + sha256(cid.encode()).hexdigest()


def read(db, cid):
    row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key(cid),)).fetchone()
    return json.loads(row[0]) if row else None


def write(db, state):
    from .learning_store import _dump
    if not db.in_transaction:
        raise RuntimeError('Ability teaching requires the admission transaction')
    db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
               (key(state['content_id']), _dump(state)))


def original_abilities(db, cid):
    state = read(db, cid)
    return state['original_abilities'] if state else [row[0] for row in db.execute(
        'SELECT ability FROM learning_unit WHERE content_id=? ORDER BY rowid', (cid,))]


def state_map(db):
    return {value['content_id']: value for raw, in db.execute(
        "SELECT value_json FROM app_setting WHERE key GLOB 'ability_teaching_v1:*'") if (value := json.loads(raw))}


def exposure_times(db, now):
    from datetime import timedelta
    from .learning_store import FEEDBACK_COOLDOWN_SECONDS
    result = {}
    for cid, state in state_map(db).items():
        admitted = {*state['original_abilities'], *state['additions']}
        for source, row in state['additions'].items():
            value = json.loads(row['checkpoint_json']).get('instruction_exposed_at_utc')
            if not value:
                continue
            until = datetime.fromisoformat(value) + timedelta(seconds=FEEDBACK_COOLDOWN_SECONDS)
            if until <= now:
                continue
            for target in admitted - {source}:
                pair = (cid, target)
                result[pair] = max(result.get(pair, until.isoformat()), until.isoformat())
    return result


def rows(db, states=None, *, teaching_only=False):
    states = state_map(db) if states is None else states
    columns = 'content_id,stage,checkpoint_json,guided_at_utc' if teaching_only else '*'
    for row in db.execute('SELECT ' + columns + ' FROM learning_content'):
        yield row
        state = states.get(row['content_id'])
        for scope, values in (state or {}).get('additions', {}).items():
            yield {**dict(row), **values, 'instruction_scope': scope}


def first_events(db, states=None):
    """First post-guide score per added/original scope, using per-unit seeks."""
    states = state_map(db) if states is None else states
    for cid, state in states.items():
        content = db.execute('SELECT * FROM learning_content WHERE content_id=?', (cid,)).fetchone()
        scopes = [(dict(content), state['original_abilities']), *(
            ({**dict(content), **value, 'instruction_scope': a}, [a]) for a, value in state['additions'].items())]
        for row, admitted in scopes:
            if not row['guided_at_utc']:
                continue
            yield from _scope_first_events(db, row, admitted)


def _scope_first_events(db, row, admitted):
    from .learning_store import _unit_id
    for ability in admitted:
        event = db.execute("""SELECT occurred_at_utc,
            COALESCE(json_extract(result_json,'$.finalized_at_utc'),occurred_at_utc) AS finalized_at
            FROM learning_event WHERE unit_id=? AND role='primary'
            AND julianday(occurred_at_utc)>=julianday(?)
            ORDER BY julianday(COALESCE(json_extract(result_json,'$.finalized_at_utc'),occurred_at_utc)),rowid LIMIT 1""",
            (_unit_id(row['content_id'], ability), row['guided_at_utc'])).fetchone()
        if event:
            yield {**dict(event), 'content_id': row['content_id'], 'guided_at_utc': row['guided_at_utc'],
                   'instruction_scope': dict(row).get('instruction_scope')}


def scope_for(db, cid, ability):
    state = read(db, cid)
    return ability if state and ability in state['additions'] else None


def project(db, row, scope=None, *, state=None):
    if row is None or not scope:
        return row
    state = state if state is not None else read(db, row['content_id'])
    if not state or scope not in state['additions']:
        raise ValueError('This ability has no separate introduction')
    return {**dict(row), **state['additions'][scope], 'instruction_scope': scope}


def update(db, row, **fields):
    if not set(fields) <= FIELDS or not fields:
        raise ValueError('Invalid instruction fields')
    scope = dict(row).get('instruction_scope')
    if scope:
        state = read(db, row['content_id'])
        state['additions'][scope].update(fields)
        write(db, state)
    else:
        db.execute('UPDATE learning_content SET ' + ','.join(name + '=?' for name in fields) + ' WHERE content_id=?',
                   (*fields.values(), row['content_id']))


def abilities(db, row):
    scope = dict(row).get('instruction_scope')
    return [scope] if scope else original_abilities(db, row['content_id'])


def ready_units(db, row):
    for ability in abilities(db, row):
        db.execute('UPDATE learning_unit SET instruction_ready=1 WHERE content_id=? AND ability=?',
                   (row['content_id'], ability))


def append(db, item, selected, now):
    from .learning_store import _load, _dump, _unit_id, ALGORITHM_VERSION
    row = db.execute('SELECT * FROM learning_content WHERE content_id=?', (item['id'],)).fetchone()
    original = _load(row['content_json'])
    enrolled = {r[0] for r in db.execute('SELECT ability FROM learning_unit WHERE content_id=?', (item['id'],))}
    missing = [a for a in selected if a not in enrolled]
    if not missing:
        return []
    if (_dump({k: item.get(k) for k in original}) != _dump(original)
            or not set(missing) <= set(original['abilities'])):
        raise ValueError('新能力的教案版本与原记录不同，请先核对内容修订。')
    state = read(db, item['id']) or {'version': 1, 'content_id': item['id'],
        'revision': row['revision'], 'source_sha256': sha256(row['content_json'].encode()).hexdigest(),
        'original_abilities': list(original_abilities(db, item['id'])), 'additions': {}}
    for ability in missing:
        db.execute('INSERT INTO learning_unit (unit_id,content_id,ability,scheduler_version) VALUES (?,?,?,?)',
                   (_unit_id(item['id'], ability), item['id'], ability, ALGORITHM_VERSION))
        state['additions'][ability] = {'introduction_id': uuid4().hex, 'stage': 'intro',
            'checkpoint_json': _dump({'supports_initial_familiarity': True}), 'exposure_group': uuid4().hex,
            'introduced_at_utc': now.isoformat(), 'guided_at_utc': None, 'retrieval_at_utc': None}
    write(db, state)
    return missing


def validate_database(db):
    from .learning_store import STAGES, FAMILIARITY_LEVELS
    for saved_key, raw in db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB 'ability_teaching_*'"):
        state = json.loads(raw)
        if (not isinstance(state, dict) or set(state) != {'version', 'content_id', 'revision',
                'source_sha256', 'original_abilities', 'additions'} or type(state['version']) is not int
                or state['version'] != 1 or not isinstance(state['content_id'], str)
                or saved_key != key(state['content_id']) or not isinstance(state['original_abilities'], list)
                or not state['original_abilities'] or any(not isinstance(a, str) for a in state['original_abilities'])
                or len(set(state['original_abilities'])) != len(state['original_abilities'])
                or not isinstance(state['additions'], dict) or not state['additions']):
            raise ValueError('Invalid additional-ability introduction')
        content = db.execute('SELECT * FROM learning_content WHERE content_id=?', (state['content_id'],)).fetchone()
        if (not content or content['revision'] != state['revision']
                or sha256(content['content_json'].encode()).hexdigest() != state['source_sha256']):
            raise ValueError('Additional ability lost its original teaching snapshot')
        actual = {r[0] for r in db.execute('SELECT ability FROM learning_unit WHERE content_id=?', (state['content_id'],))}
        if (set(state['original_abilities']) & set(state['additions'])
                or set(state['original_abilities']) | set(state['additions']) != actual
                or not actual <= set(json.loads(content['content_json'])['abilities'])):
            raise ValueError('Additional ability scope disagrees with enrolled units')
        for ability, value in state['additions'].items():
            if not isinstance(value, dict) or set(value) != FIELDS or value['stage'] not in STAGES:
                raise ValueError('Invalid additional ability checkpoint')
            for name in ('introduction_id', 'exposure_group'):
                if not isinstance(value[name], str) or len(value[name]) != 32:
                    raise ValueError('Invalid additional ability introduction identity')
            for name in ('introduced_at_utc', 'guided_at_utc', 'retrieval_at_utc'):
                if value[name] is not None and datetime.fromisoformat(value[name]).tzinfo is None:
                    raise ValueError('Invalid additional ability time')
            checkpoint = json.loads(value['checkpoint_json'])
            if not isinstance(checkpoint, dict):
                raise ValueError('Invalid additional ability checkpoint')
            profile = checkpoint.get('initial_familiarity')
            if profile and (set(profile.get('levels', {})) != {ability}
                    or profile['levels'][ability] not in FAMILIARITY_LEVELS):
                raise ValueError('Additional ability inherited another self-report')
            guides = [checkpoint.get('guide_question'), *checkpoint.get('guide_sequence', [])]
            if any(q is not None and (not isinstance(q, dict) or q.get('ability') != ability) for q in guides):
                raise ValueError('Additional ability guide belongs to another ability')
            from .pitch_practice import validate_checkpoint
            from .learning_practice_policy import validate_practice_guide
            validate_checkpoint(state['content_id'], state['revision'], checkpoint)
            validate_practice_guide(state['content_id'], state['revision'], checkpoint,
                                    item=json.loads(content['content_json']), validate_result=False)
