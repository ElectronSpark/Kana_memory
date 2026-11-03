"""Read-only evidence and queue probes for an introduced kana's first test."""
from datetime import datetime
from types import SimpleNamespace

from .domain import SKILLS, WORD_SKILLS, SCRIPTS
from .kana_learning_intro import _saved, should_introduce


def teaching_state(store, now, start, end):
    introduced = {}
    for key, row in _saved(store)['introduced'].items():
        if (not isinstance(row,dict) or not row.get('introduced_at_utc')
                or row.get('route') in {'prior_knowledge_check','legacy_exposure_check'}):
            continue  # Familiarity/legacy answer exposure is not teaching.
        fallback=key.rsplit('|',1)
        cid=row.get('content_id') or (fallback[0] if len(fallback)==2 else None)
        script=row.get('script') or (fallback[1] if len(fallback)==2 else None)
        if cid and script in SCRIPTS:
            at=datetime.fromisoformat(row['introduced_at_utc'])
            if at<=now:
                introduced[cid,script]=at
    kinds=sorted(SKILLS-WORD_SKILLS)
    first={}
    for row in store.teaching_queries.kana_first_primary_events(kinds):
        slot=row['content_id'],row['script']
        at=datetime.fromisoformat(row['finalized_at'])
        if at<=now and (slot not in first or at<first[slot]):
            first[slot]=at
    # Cards seeded from self-report and low-weight word evidence are not a
    # primary review event. A genuine Again ends the block just as a Good does.
    started=set(introduced)-set(first)
    attempts=set()
    for row in store.teaching_queries.kana_attempts_by_kind(kinds):
        slot=row['content_id'],row['script']
        if slot in started:
            at=datetime.fromisoformat(row['occurred_at_utc'])
            if max(start,introduced[slot])<=at<end and at<=now:
                attempts.add(row['attempt_id'])
    return {'started':started,
            'service_at':{slot:max(at,first.get(slot,at)) for slot,at in introduced.items()},
            'introduced_today':{slot for slot,at in introduced.items() if start<=at<end},
            'first_primary_today':any(slot in introduced and max(start,introduced[slot])<=at<end and at<=now
                                      for slot,at in first.items()),
            'initial_unscored_today':len(attempts)}


def probe(store, kana_kinds, now, *, slots=None, new_only=False, include_unassessed=False, prefer_unassessed=False):
    if getattr(store,'practice_lane','main')!='main' or not kana_kinds:
        return None
    pending=store.load_session_progress(lane='main',lazy_questions=True)
    if not pending or (not new_only and not slots):
        return None
    preview=store.prepare_next_question(pending['session_id'],now=now,lazy_questions=True,
        fair_new=True,teaching_only=True,dry_run=True,teaching_kinds=kana_kinds,
        teaching_targets=None if new_only else slots,
        teaching_unassessed=include_unassessed, prefer_unassessed=prefer_unassessed)
    if preview is None:
        return None
    index=preview['question_index']
    q=preview['questions'][index]
    intro=should_introduce(SimpleNamespace(store=store),q)
    additional = preview.get('selected_kana_pool') == 'additional'
    return {'type':'introduction' if intro else 'question','question':q,
            'protected_unassessed_direction':additional,
            'selected_kana_pool':preview.get('selected_kana_pool'),
            'kana_service_candidates':preview.get('kana_service_candidates',{}),
            'teaching_targets':{(q.content_id,q.kind,q.script)} if additional else {(q.content_id,q.script)},
            'session_id':preview['session_id'],'question_index':index,'state':preview['state'],
            'pending':preview,'counts':None,'dispatch_priority':(5,0) if intro else (4,0)}


def _direction_firsts(store, now):
    """Indexed first formal event per direction, with exact timestamp ties."""
    import json
    kinds = sorted(SKILLS - WORD_SKILLS)
    first = {}
    # Seek the first REAL event of each mode within a carrier. Its first input
    # may be its twentieth review, and an old sibling's first input still wins.
    for row in store.teaching_queries.first_direction_candidates(kinds, now):
        log = json.loads(row['review_log_json'])
        at = datetime.fromisoformat(log.get('finalized_at_utc') or row['occurred_at_utc'])
        occurred = datetime.fromisoformat(row['occurred_at_utc'])
        key = at, occurred, row['ordinal']
        identity = row['content_id'], row['skill'], row['script']
        if at <= now and occurred <= now and (identity not in first or key < first[identity]):
            first[identity] = key
    return first


def _known_direction_slots(store, now, primary):
    """A same-script real history or an explicit prior-knowledge declaration.

    Full introduction without its first rating stays in the original teaching
    block. A saved title/exposure alone cannot unlock harder directions.
    """
    import json
    slots = {(cid, script) for cid, _, script in primary}
    for row in store.teaching_queries.reviewed_kana_states():
        last = json.loads(row['scheduler_state_json']).get('last_review')
        if last and datetime.fromisoformat(last) <= now:
            slots.add((row['content_id'],row['script']))
    declarations = set()
    for key, row in _saved(store)['introduced'].items():
        if not isinstance(row, dict) or row.get('route') not in {'prior_knowledge_check','legacy_exposure_check'}:
            continue
        value = row.get('selected_at_utc')
        if not value or datetime.fromisoformat(value) > now:
            continue
        fallback = key.rsplit('|', 1)
        slot = row.get('content_id') or fallback[0], row.get('script') or fallback[-1]
        if slot[1] in SCRIPTS:
            declarations.add(slot)
    return slots | declarations, declarations


def unassessed_directions(store, questions, now):
    """Further restrict already-ready queued work using CURRENT recall stage."""
    from .domain import _adaptive_stages, _ADAPTIVE_LEVELS
    first = _direction_firsts(store, now)
    primary = set(first)
    known, _ = _known_direction_slots(store, now, first)
    cards = {(row['content_id'],row['skill'],row['script']):row for row in store.scheduling_cards(aliases=True)}
    stages = {}
    result = set()
    for question in questions:
        identity = question.content_id, question.kind, question.script
        slot = question.content_id, question.script
        card = cards.get(identity)
        if (question.kind not in SKILLS-WORD_SKILLS or slot not in known or identity in primary
                or card and (card['suspended'] or (datetime.fromisoformat(card['due_at_utc']) > now
                    if card.get('ability_unit_id') else card['review_count']))
                or should_introduce(SimpleNamespace(store=store), question)):
            continue
        if question.script not in stages:
            stages[question.script] = _adaptive_stages(store, question.script, now)
        if _ADAPTIVE_LEVELS[question.kind] <= stages[question.script].get(question.content_id, 0):
            result.add(identity)
    return result


def direction_service(store, now, start, end, started=()):
    """First additional formal ratings consume service, never duplicate/word credit."""
    first = _direction_firsts(store, now)
    known, declarations = _known_direction_slots(store, now, first)
    import json
    legacy = set()
    for row in store.teaching_queries.legacy_kana_states_without_events():
        last = json.loads(row['scheduler_state_json']).get('last_review')
        if last and datetime.fromisoformat(last) <= now:
            legacy.add((row['content_id'],row['script']))
    grouped = {}
    for (cid, kind, script), key in first.items():
        grouped.setdefault((cid,script), []).append(key)
    consumed = [key for slot, values in grouped.items()
                for key in sorted(values)[0 if slot in declarations or slot in legacy else 1:]]
    latest = max((key[0] for key in consumed), default=None)
    attempts = set()
    cards = {(row['content_id'],row['skill'],row['script']):row for row in store.scheduling_cards(aliases=True)}
    for row in store.teaching_queries.kana_attempts_between(start, now):
        identity = row['content_id'],row['skill'],row['script']
        slot = row['content_id'],row['script']
        card = cards.get(identity)
        at = datetime.fromisoformat(row['occurred_at_utc'])
        if (row['skill'] in SKILLS-WORD_SKILLS and identity not in first and slot in known
                and slot not in started and (not card or not card['review_count'] or card.get('ability_unit_id'))
                and start <= at < end and at <= now):
            attempts.add(row['attempt_id'])
    return {'latest':latest,'unscored_today':len(attempts)}
