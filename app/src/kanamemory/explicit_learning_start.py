"""One current teaching block and at most one deferred explicit start.

This setting owns no cards, questions or review times. Dispatch is read-only;
a real familiarity/exposure transaction binds one target. Its first formal
rating consumes the request through existing evidence, including after restore.
An already claimed block cannot swallow a later real add-and-start action.
"""
from copy import deepcopy
from datetime import datetime
import json
from uuid import uuid4

SETTING = 'explicit_learning_start_v1'


def validate(value):
    fields = {'version', 'action_id', 'created_at_utc', 'entries', 'selected_entry_id'}
    if (not isinstance(value, dict) or set(value) not in (fields, fields | {'pending_start'})
            or type(value['version']) is not int or value['version'] not in (1, 2)
            or (value['version'] == 2) != ('pending_start' in value)
            or not isinstance(value['action_id'], str) or not 1 <= len(value['action_id']) <= 160
            or not isinstance(value['entries'], list) or not 1 <= len(value['entries']) <= 20000):
        raise ValueError('Invalid explicit learning start')
    if not isinstance(value['created_at_utc'], str):
        raise ValueError('Invalid explicit start timestamp')
    at = datetime.fromisoformat(value['created_at_utc'])
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('Explicit learning start requires an aware timestamp')
    ids = set()
    for entry in value['entries']:
        if (not isinstance(entry, dict) or set(entry) != {
                'entry_id', 'content_id', 'kind', 'script', 'introduction_id'}
                or any(not isinstance(entry[key], str) or not 1 <= len(entry[key]) <= 1024
                       for key in ('entry_id', 'content_id'))
                or entry['entry_id'] in ids or entry['kind'] not in {'learning', 'kana'}
                or (entry['kind'] == 'learning' and (entry['script'] is not None
                    or not isinstance(entry['introduction_id'], str) or not 1 <= len(entry['introduction_id']) <= 256))
                or (entry['kind'] == 'kana' and (entry['script'] not in {'hiragana', 'katakana'}
                    or entry['introduction_id'] is not None))):
            raise ValueError('Invalid explicit learning target')
        ids.add(entry['entry_id'])
    if value['selected_entry_id'] is not None and value['selected_entry_id'] not in ids:
        raise ValueError('Explicit learning selection is outside its request')
    if value['version'] == 2:
        pending = value['pending_start']
        # A single unclaimed request, never a recursively growing queue or
        # another already-open block. Version 1 backups remain unchanged.
        if not isinstance(pending, dict) or pending.get('version') != 1 or 'pending_start' in pending:
            raise ValueError('Invalid deferred explicit start')
        validate(pending)
        if (value['selected_entry_id'] is None or len(value['entries']) != 1
                or pending['selected_entry_id'] is not None
                or pending['action_id'] == value['action_id']
                or datetime.fromisoformat(pending['created_at_utc']) < at
                or ids & {entry['entry_id'] for entry in pending['entries']}
                or len(value['entries']) + len(pending['entries']) > 20000):
            raise ValueError('Invalid deferred explicit start boundary')
    return deepcopy(value)


def _db(value):
    return getattr(value, '_db', value)


def read(db):
    row = _db(db).execute('SELECT value_json FROM app_setting WHERE key=?', (SETTING,)).fetchone()
    return validate(json.loads(row[0])) if row else None


def _alive(db, entry):
    if entry['kind'] == 'learning':
        from .ability_teaching import read, original_abilities
        row = db.execute('SELECT introduction_id FROM learning_content WHERE content_id=?',
                         (entry['content_id'],)).fetchone()
        if row is None:
            return False
        state = read(db, entry['content_id'])
        scope = next((a for a, value in (state or {}).get('additions', {}).items()
                      if value['introduction_id'] == entry['introduction_id']), None)
        if scope is None and row[0] != entry['introduction_id']:
            return False
        from .learning_store import _unit_id
        return not any(db.execute("SELECT 1 FROM learning_event WHERE unit_id=? AND role='primary' LIMIT 1",
            (_unit_id(entry['content_id'], a),)).fetchone() for a in ([scope] if scope else original_abilities(db, entry['content_id'])))
    path = db.execute("SELECT value_json FROM app_setting WHERE key='study_path_v1'").fetchone()
    from .kana_question_policy import direction_allowed
    if not direction_allowed(entry['content_id'], 'kana_choice', entry['script']):
        return False
    if not path or entry['entry_id'] not in json.loads(path[0]).get('reserved_entries', []):
        return False
    return not db.execute('''SELECT 1 FROM review_event e JOIN study_card c USING(card_id)
        WHERE c.content_id=? AND c.script=?
        AND c.skill NOT IN ('word_kana','word_meaning_kana','word_romaji') LIMIT 1''',
        (entry['content_id'], entry['script'])).fetchone()


def _request_alive(db, value):
    selected = value['selected_entry_id']
    return any(_alive(db, entry) for entry in value['entries']
               if selected is None or entry['entry_id'] == selected)


def active(store):
    """Return an unconsumed request without writing even a cleanup marker."""
    db = _db(store)
    value = read(db)
    if not value:
        return None
    if _request_alive(db, value):
        return value
    pending = value.get('pending_start')
    return pending if pending and _request_alive(db, pending) else None


def _all_disabled(service, entries):
    """Prove closure by current course settings/controls, not by an empty probe.

    Dependencies, short waits and missing questions are not disabled settings.
    Unknown/kana candidates conservatively keep the existing request; their
    effective session-specific kind policy is not owned by this helper.
    """
    from .learning_questions import DEFAULT_KINDS, KIND_ABILITIES, question_kinds
    raw = service.store.get_setting('learning_preferences_v1', {})
    if not isinstance(raw, dict):
        return False
    kinds_setting = raw.get('enabled', list(DEFAULT_KINDS))
    if (not isinstance(kinds_setting, list)
            or any(not isinstance(kind, str) or kind not in KIND_ABILITIES for kind in kinds_setting)):
        return False
    enabled = set(kinds_setting)
    queries = service.store.queries
    teaching = service.store.teaching_queries
    for entry in entries:
        if not teaching.explicit_entry_alive(entry):
            continue
        if entry['kind'] != 'learning':
            return False
        cid = entry['content_id']
        units = queries.course_suspensions(cid)
        if units and all(units.values()):
            continue
        # Pending snapshots remain usable even if a later content package no
        # longer has their template. Conversely, enrollment precedes building
        # its first question, so include the saved lesson's supported kinds.
        kinds = queries.pending_course_modes(cid)
        item = service.learning.get_item(cid)
        kinds.update((kind, KIND_ABILITIES[kind]) for kind in question_kinds(item))
        if not kinds or any(kind in enabled and not units.get(ability, False) for kind, ability in kinds):
            return False
    return True


def arm(db, entries, *, action_id=None, now, service=None):
    """Called only inside the actual-new-enrollment transaction, once per add.

    Before teaching starts, repeated additions share one opportunity. After a
    block is claimed, retain at most one new request for the next safe boundary.
    Neither a batch of 1000 nor repeated additions can accumulate more slots.
    """
    if not db.in_transaction:
        raise RuntimeError('Start intent must share the enrollment transaction')
    if not entries:
        return False
    value = validate({'version': 1, 'action_id': uuid4().hex if action_id is None else action_id,
                      'created_at_utc': now.isoformat(), 'entries': entries, 'selected_entry_id': None})
    previous = read(db)
    if previous and value['action_id'] in {previous['action_id'],
                                         previous.get('pending_start', {}).get('action_id')}:
        return False
    outstanding = active(db)
    if outstanding:
        def can_replace(request):
            return (service is not None and _all_disabled(service, request['entries'])
                    and not _all_disabled(service, entries))
        pending = outstanding.get('pending_start')
        if pending and _request_alive(db, pending):
            if not can_replace(pending):
                return False
            value = {**outstanding, 'pending_start': value}
        elif can_replace(outstanding):
            pass
        elif outstanding['selected_entry_id'] is None:
            return False
        else:
            value = {**outstanding, 'version': 2, 'pending_start': value}
    validate(value)
    db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
               (SETTING, json.dumps(value, ensure_ascii=False, allow_nan=False)))
    return True


def candidates(service, entry_ids):
    """Include required, enrolled but unopened parents without adding siblings."""
    from .learning_dependencies import dependencies_of, effective_dependencies
    result, visited = [], set()
    def visit(eid):
        if eid in visited or eid not in service.entries:
            return
        visited.add(eid)
        entry = service.entries[eid]
        cid = entry['content_id']
        source = service.items.get(cid) if entry['kind'] == 'learning' else entry
        deps = effective_dependencies(service.learning, source) if entry['kind'] == 'learning' else dependencies_of(source)
        for dependency in deps:
            visit(service.learning_entries.get(dependency, dependency))
        if entry['kind'] == 'learning':
            row = service.store.queries.course_introduction_header(cid)
            if not row or row['stage'] != 'intro':
                return
            checkpoint = json.loads(row['checkpoint_json'])
            if checkpoint.get('initial_familiarity') or checkpoint.get('instruction_exposed_at_utc'):
                return
            record = {'entry_id': eid, 'content_id': cid, 'kind': 'learning', 'script': None,
                      'introduction_id': row['introduction_id']}
        elif entry['kind'] == 'kana' and not entry.get('practice_retired'):
            from .kana_learning_intro import _saved
            saved = _saved(service.store)
            key = cid+'|'+entry['script']
            if key in saved.get('introduced', {}) or key in saved.get('familiarity', {}):
                return
            record = {'entry_id': eid, 'content_id': cid, 'kind': 'kana', 'script': entry['script'],
                      'introduction_id': None}
        else:
            return
        if service.store.teaching_queries.explicit_entry_alive(record):
            result.append(record)
    for eid in entry_ids:
        visit(eid)
    return result


def claim(db, content_id, *, introduction_id=None, script=None, now):
    """Bind once during a real start action, never by rendering or dispatch."""
    if not db.in_transaction:
        raise RuntimeError('Start claim must share a learner action transaction')
    value = active(db)
    if not value:
        return False
    pending = value.get('pending_start')
    # A real start may also reach the deferred request through a direct user
    # navigation or after the old kind was disabled. Promote only on that real
    # action, never while inspecting/painting a candidate. Old work stays in
    # its own saved teaching/checkpoint records.
    if pending and any(entry['content_id'] == content_id and entry['script'] == script
                       and entry['introduction_id'] == introduction_id for entry in pending['entries']):
        value = pending
    if value['selected_entry_id'] is not None or datetime.fromisoformat(value['created_at_utc']) > now:
        return False
    selected = next((entry for entry in value['entries'] if entry['content_id'] == content_id
                     and entry['introduction_id'] == introduction_id and entry['script'] == script), None)
    if selected is None:
        return False
    value['selected_entry_id'] = selected['entry_id']
    # One action buys one block, not a priority token for every batch member.
    # Drop the unused candidate list once the real start fixes its identity.
    value['entries'] = [selected]
    db.execute('UPDATE app_setting SET value_json=? WHERE key=?',
               (json.dumps(value, ensure_ascii=False, allow_nan=False), SETTING))
    return True


def choose(store, learning, value, *, enabled_kinds, kana_kinds, now, service, kana_service):
    """Read existing eligible heads; never manufacture or reschedule a question."""
    from .first_teaching_kana import probe
    from types import SimpleNamespace
    if datetime.fromisoformat(value['created_at_utc']) > now:
        return None
    selected = value['selected_entry_id']
    entries = [e for e in value['entries'] if (selected is None or e['entry_id'] == selected) and store.teaching_queries.explicit_entry_alive(e)]
    courses = {e['content_id'] for e in entries if e['kind'] == 'learning'}
    slots = {(e['content_id'], e['script']) for e in entries if e['kind'] == 'kana'}

    def heads(course_ids, kana_slots):
        course = learning.current(enabled_kinds, now=now, fair_new=True, teaching_only=True,
                                  teaching_content_ids=course_ids) if course_ids else None
        kana = probe(store, kana_kinds, now, slots=kana_slots) if kana_slots else None
        lane = store.teaching_queries.choose_lane(kana.get('dispatch_priority') if kana else None,
                           course.get('dispatch_priority') if course else None,
                           course_started=bool(course and course.get('new_dispatch_continue')))
        return (lane, kana if lane == 'kana' else course) if lane else None

    # A new click must not split an already opened explanation/guide. Existing
    # blocks do not consume this request, so its new target remains available.
    prior = heads(service['started']-courses, kana_service['started']-slots)
    chosen = prior or heads(courses, slots)
    pending = value.get('pending_start')
    if (chosen is None and pending and store.teaching_queries.explicit_request_alive(pending)
            and _all_disabled(SimpleNamespace(store=store, learning=learning), entries)):
        # Proven settings closure may defer the old block, but a cooldown,
        # missing prerequisite or a temporarily empty probe may not do so.
        return choose(store, learning, pending, enabled_kinds=enabled_kinds,
                      kana_kinds=kana_kinds, now=now, service=service, kana_service=kana_service)
    if chosen is None:
        return None
    lane, head = chosen
    return lane, {**head, 'dispatch_priority': (2.5, 0), 'first_teaching_protected': True,
                  'new_dispatch_continue': True,
                  **({'explicit_learning_start': value['action_id']} if prior is None else {})}
