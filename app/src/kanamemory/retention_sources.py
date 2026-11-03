"""Current local sources for diagnostics, including admitted dictionary senses."""
import json
from hashlib import sha256

PREFIX = 'retention_source_guard_v1:'


def guard_key(question_id):
    return PREFIX + sha256(question_id.encode()).hexdigest()


def guarded(store, question_id):
    return store.get_setting(guard_key(question_id), None) is not None


def retain_guard(store, episode, entry):
    if episode.get('material_id') or not entry['course_question_id']:
        return
    item = current_item(store, entry['question']['target_id'])
    if not item or not item.get('dictionary_only'):
        return
    episode['source_guards'] = True
    value = {'version': 1, 'episode_id': episode['id'], 'entry_id': entry['id'],
             'question_id': entry['course_question_id'], 'proof': entry['proof']}
    old = store.get_setting(guard_key(value['question_id']), None)
    if old:
        if old['proof'] != value['proof']:
            raise ValueError('词典题的原合同已变化。')
        return
    store._db.execute('INSERT INTO app_setting VALUES (?,?)',
                      (guard_key(value['question_id']), json.dumps(value)))


def validate_guard(value):
    from .retention_ai import _hash, _text
    if (not isinstance(value, dict) or set(value) != {'version', 'episode_id', 'entry_id', 'question_id', 'proof'}
            or type(value['version']) is not int or value['version'] != 1 or not _hash(value['episode_id'])
            or not _hash(value['proof']) or not _text(value['entry_id']) or not _text(value['question_id'])):
        raise ValueError('词典检查来源约束无效。')
    return value


def validate_database(db):
    from .retention_session import PREFIX as EPISODES
    from .diagnostic_replacement import find_entry
    from types import SimpleNamespace
    def setting(key, default=None):
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    store = SimpleNamespace(_db=db, get_setting=setting)
    guards = {}
    for key, raw in db.execute("SELECT key,value_json FROM app_setting WHERE key GLOB 'retention_source_guard_*'"):
        value = validate_guard(json.loads(raw))
        record = db.execute('SELECT value_json FROM app_setting WHERE key=?', (EPISODES + value['episode_id'],)).fetchone()
        episode = json.loads(record[0]) if record else {}
        entry = find_entry(store, episode, value['entry_id'], question_id=value['question_id']) if episode else None
        if (key != guard_key(value['question_id']) or not episode.get('source_guards') or not entry
                or entry['course_question_id'] != value['question_id'] or entry['proof'] != value['proof']):
            raise ValueError('词典检查来源约束缺少原题。')
        guards[value['question_id']] = value
    # A guard must survive reservation release, backup and later re-borrowing.
    for raw, in db.execute('SELECT value_json FROM app_setting WHERE key GLOB ?', (EPISODES + '*',)):
        episode = json.loads(raw)
        if not episode.get('source_guards'):
            continue
        for entry in episode['entries']:
            if not entry['course_question_id']:
                continue
            content = db.execute('SELECT content_json FROM learning_content WHERE content_id=?', (entry['question']['target_id'],)).fetchone()
            if content and json.loads(content[0]).get('dictionary_only'):
                guard = guards.get(entry['course_question_id'])
                if not guard or guard['proof'] != entry['proof']:
                    raise ValueError('词典检查缺少持续来源约束。')


def current_item(store, identity, *, repo=None):
    from .learning_content import get_learning_item
    from .personal_content_versions import resolve, teaching_source
    item = get_learning_item(identity)
    if item is not None:
        return resolve(store, teaching_source(item))
    saved = store._db.execute('SELECT content_json FROM learning_content WHERE content_id=?', (identity,)).fetchone()
    if saved is None:
        return None
    saved = json.loads(saved[0])
    if saved.get('dictionary_only') is not True:
        return None
    from .material_verification import current_dictionary_item
    if repo is None:
        repo = getattr(store, '_material_resources', (None, None))[0]
    try:
        item = current_dictionary_item(repo, identity)
    except (ValueError, KeyError, TypeError):
        return None
    # The admitted copy binds identity; it is not an alternative authority.
    # Dependency closure may add local prerequisites without changing a sense.
    if item is None or {k: v for k, v in saved.items() if k != 'depends_on'} != item:
        return None
    return resolve(store, teaching_source(item))


def trusted_question(store, question, *, repo=None, item=None):
    from .learning_evidence import reviewed_question
    from .material_eligibility import exact_question
    item = item if item is not None else current_item(store, question['target_id'], repo=repo)
    if item is None:
        return False
    return exact_question(item, question) if (item.get('dictionary_only') or item.get('personal_revision')
        or item.get('personal_question_versions')) else reviewed_question(question)


def assessment_item(store, question):
    item = current_item(store, question['target_id'])
    if item and item.get('dictionary_only') and not trusted_question(store, question, item=item):
        item = None
    return item or {'id': question['target_id'], 'revision': 'missing'}
