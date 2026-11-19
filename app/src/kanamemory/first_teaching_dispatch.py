"""Protected first-teaching service, never a new-item cap.

Reads only. Existing scores, due times, snapshots and admission choices remain
the authority. The caller commits a selected kana reordering only at display.
"""
from datetime import datetime, timedelta
import json
from typing import NamedTuple

from .domain import _utc, WORD_SKILLS
from .review_dispatch import reviews_today
from .kana_question_policy import question_allowed

# Engineering service ratio, not a memory-model parameter or admission cap.
# A block is its introduction, guide and first independent check together.
REVIEWS_PER_TEACHING_BLOCK = 8
MAX_REVIEWS_PER_TEACHING_BLOCK = 64
# An open block can go on "within the bridge wait" (design 12 B3): the course's
# retrieval delay after a guide (a new kana's wait after its introduction is
# the same minute) plus this margin for the time a dispatch itself takes.
BRIDGE_MARGIN_SECONDS = 30


def bridge_wait() -> timedelta:
    """How far ahead an open teaching block still counts as going on."""
    from .learning_store import RETRIEVAL_DELAY_SECONDS
    return timedelta(seconds=RETRIEVAL_DELAY_SECONDS + BRIDGE_MARGIN_SECONDS)


def teaching_review_threshold(overdue_seconds):
    """Engineering backlog damping; finite credit, never a mastery estimate.

    The already filtered ordinary due head supplies pressure. Do not count
    disabled/suspended cards or infer another daily study-time allowance.
    """
    days = max(0, overdue_seconds) / 86400
    return (MAX_REVIEWS_PER_TEACHING_BLOCK if days >= 7 else 32 if days >= 3
            else 16 if days >= 1 else REVIEWS_PER_TEACHING_BLOCK)


def _priority(head):
    if head and head.get('type') in {'question','feedback','introduction','instruction'}:
        return head.get('dispatch_priority')
    return None


def _in_day(value, start, end, now):
    if not value:
        return False
    at = datetime.fromisoformat(value)
    return start <= at < end and at <= now


class GuideEvidence(NamedTuple):
    diagnostic_anchor: bool   # the guided_at_utc may be a diagnostic title exposure
    exposure: str | None      # instruction_exposed_at_utc of the checkpoint
    guided: bool              # guided_at_utc marks a completed guide


def guide_evidence(row, checkpoint) -> GuideEvidence:
    """Read-only: whether an instruction row's guided_at_utc is a completed guide.

    ``row`` is an instruction row (TeachingQueries.instruction_rows) and
    ``checkpoint`` its decoded checkpoint_json. Shared by the first-teaching
    service and the study clock's course-guide marks.
    """
    profile = checkpoint.get('initial_familiarity') or {}
    # A diagnostic title exposure can use this timestamp without a guide.
    # It is not evidence that a first-teaching block was completed.
    diagnostic_anchor = (profile.get('route') == 'diagnostic'
                         or profile.get('diagnostic_state') == 'needs_teaching')
    exposure = checkpoint.get('instruction_exposed_at_utc')
    guide_checkpoint = checkpoint.get('last_checkpoint') in {'guide_correct','bridge'}
    # A failed diagnostic switches route to full but retains its earlier
    # title-exposure anchor. That anchor still is not a completed guide.
    if checkpoint.get('last_checkpoint') == 'check' and exposure and row['guided_at_utc']:
        guide_checkpoint = datetime.fromisoformat(row['guided_at_utc']) >= datetime.fromisoformat(exposure)
    guided = bool(row['guided_at_utc']) and (not diagnostic_anchor or guide_checkpoint)
    return GuideEvidence(diagnostic_anchor, exposure, guided)


def _teaching_service(store, now):
    from itertools import chain
    ability_states = store.teaching_queries.ability_states()
    start, end = (datetime.fromisoformat(value) for value in store._day_bounds(now))
    started, guided_today, guided_content = set(), set(), {}
    service_at = {}
    first_primary_today = False
    first_primary = {}
    # Restore/import can change row order; UTC-offset strings are not ordered
    # instants. A real reteaching block starts at its current guide completion,
    # even when another ability had a prior diagnostic score. Both the frozen
    # answer and finalization must be after that guide. SQL selects the earliest
    # later bucket plus boundary candidates; Python resolves sub-ms precision.
    original_events = (e for e in store.teaching_queries.course_first_primary_events()
                       if e['content_id'] not in ability_states)
    for event in chain(original_events, store.teaching_queries.ability_first_events(ability_states)):
        value = event['finalized_at']
        anchor = datetime.fromisoformat(event['guided_at_utc'])
        if (datetime.fromisoformat(value) > now or datetime.fromisoformat(value) < anchor
                or datetime.fromisoformat(event['occurred_at_utc']) < anchor):
            continue
        scope_key = (event['content_id'], dict(event).get('instruction_scope'))
        prior = first_primary.get(scope_key)
        if prior is None or datetime.fromisoformat(value) < datetime.fromisoformat(prior):
            first_primary[scope_key] = value
    for row in store.teaching_queries.instruction_rows(ability_states):
        scope_key = (row['content_id'], dict(row).get('instruction_scope'))
        checkpoint = json.loads(row['checkpoint_json'])
        profile = checkpoint.get('initial_familiarity') or {}
        if row['stage'] in {'intro','guide'} and (row['stage']=='guide'
                or checkpoint.get('instruction_exposed_at_utc') or profile.get('route') in {'full','compact'}):
            started.add(row['content_id'])
        diagnostic_anchor, exposure, guided = guide_evidence(row, checkpoint)
        first_rating = first_primary.get(scope_key)
        if guided:
            guided_content[scope_key] = datetime.fromisoformat(row['guided_at_utc'])
        if guided and row['stage'] in {'bridge','check'} and first_rating is None:
            started.add(row['content_id'])
        if guided and _in_day(row['guided_at_utc'], start, end, now):
            guided_today.add(row['content_id'])
        # Finishing yesterday's real teaching block today has also received
        # today's protected service, even if the frozen answer was older.
        if guided and first_rating and _in_day(first_rating,start,end,now):
            first_primary_today = True
        # This durable action anchor survives days and restarts. Merely opening
        # an already exposed page never advances it. First-primary is the
        # first check after this guide, not every newly tested ability.
        instants = [datetime.fromisoformat(value) for value in (
            exposure if not diagnostic_anchor else None,
            row['guided_at_utc'] if guided else None,
            first_rating if guided else None) if value and datetime.fromisoformat(value) <= now]
        if instants:
            service_at[row['content_id']] = max([*instants, *([service_at[row['content_id']]] if row['content_id'] in service_at else [])])
    # Count finalized, unique initial-check attempts across contents. Changing
    # blocks cannot reset a failed microphone/model's same-day priority budget.
    # Failed diagnostic placements before real teaching are not these checks.
    initial_unscored_today = 0
    for event in store.teaching_queries.unscored_course_events(start, now):
        cid = (event['content_id'], store.teaching_queries.ability_scope(event['content_id'], event['ability']))
        if cid not in guided_content:
            continue
        result = json.loads(event['result_json'])
        at = result.get('finalized_at_utc') or event['occurred_at_utc']
        instant = datetime.fromisoformat(at)
        first_rating = first_primary.get(cid)
        if (_in_day(at,start,end,now) and instant >= guided_content[cid]
                and not result.get('answer',{}).get('initial_diagnostic')
                and (first_rating is None or instant < datetime.fromisoformat(first_rating))):
            initial_unscored_today += 1
    return {'start':start,'end':end,'started':started,'guided_today':guided_today,
        'first_primary_today':first_primary_today,'initial_unscored_today':initial_unscored_today,
        'service_at':service_at}


def due_reviews_served(store, now, since, *, limit=REVIEWS_PER_TEACHING_BLOCK):
    """Actual due-review service across visits, excluding first tests and AI.

    Read finalized primary logs, not queue lengths or lifetime counters. A
    saved answer earns service only when finalized. Early practice, auxiliary
    spelling/tutor evidence and technical failures do not earn extra slots.
    The result is min(qualifying rows, limit), independent of row order. Rows
    are therefore read newest first: with an unbounded anchor (no teaching
    action yet, since=datetime.min) the capped count stops at the latest
    ``limit`` due reviews instead of decoding the whole history from its start.
    """
    count = 0
    for course in (False, True):
        for row in store.teaching_queries.due_review_candidates(course, since, now, newest_first=True):
            if not since < datetime.fromisoformat(row['finalized_at']) <= now:
                continue
            before = json.loads(row['state_before_json'] or '{}')
            due = before.get('due')
            if (before.get('last_review') and due
                    and datetime.fromisoformat(due) <= datetime.fromisoformat(row['occurred_at_utc'])):
                count += 1
                if count >= limit:
                    return count
    return count


def _ability_service(store, now, service):
    """Actual first ratings of additional abilities; never AI or repeated reviews.

    Seek one first primary event per unit using the existing partial expression
    index. Inspect only these bounded candidates, not every historical answer.
    No new counter is stored, so restart/rollback/import retain the same anchor.
    """
    first_by_unit = {}
    for row in store.teaching_queries.first_course_ability_events():
        result = json.loads(row['result_json'])
        at = datetime.fromisoformat(result.get('finalized_at_utc') or row['occurred_at_utc'])
        key = (at, datetime.fromisoformat(row['occurred_at_utc']), row['event_row'])
        previous = first_by_unit.get(row['unit_id'])
        if previous is None or key < previous[0]:
            first_by_unit[row['unit_id']] = (key, row, result)
    first_by_content = {}
    for key, row, result in first_by_unit.values():
        at = key[0]
        if at > now or datetime.fromisoformat(row['occurred_at_utc']) > now:
            continue
        first_by_content.setdefault(row['content_id'], []).append((key, result))
    # Placement establishes first-primary evidence but earns no ordinary due
    # review credit. Its additional ability check still consumes service.
    additional = [event for events in first_by_content.values()
        for event in sorted(events, key=lambda value: value[0])[1:]
        if event[1].get('review_count') == 1]
    latest = max((event[0][0] for event in additional), default=None)
    continuations = {}
    for row in store.teaching_queries.instruction_rows():
        if row['stage'] == 'done':
            continue
        profile = json.loads(row['checkpoint_json']).get('initial_familiarity', {})
        failed_at = profile.get('diagnostic_failed_at_utc')
        if profile.get('route') != 'full' or profile.get('diagnostic_state') != 'needs_teaching' or not failed_at:
            continue
        at = datetime.fromisoformat(failed_at)
        # A failed second-ability placement starts this existing teaching
        # block. It is not a score and never updates FSRS or earns due credit.
        if at <= now and any(event[0][1] <= at for event in first_by_content.get(row['content_id'], [])):
            continuations[row['content_id']] = at
    ability_states = store.teaching_queries.ability_states()
    eligible_units = {row['unit_id'] for row in store.teaching_queries.unassessed_course_units()
        if ability_states.get(row['content_id'], {}).get('additions', {}).get(row['ability'], {}).get('stage', row['stage']) == 'done'}
    unscored = set()
    for row in store.teaching_queries.unscored_course_events(service['start'], now):
        result = json.loads(row['result_json'])
        at = result.get('finalized_at_utc') or row['occurred_at_utc']
        if (result.get('unit_id') in eligible_units
                and _in_day(at, service['start'], service['end'], now)):
            unscored.add(result['question_id'])
    return {'latest': latest, 'continuations': continuations, 'unscored_today': len(unscored)}


class _BlockAnchors(NamedTuple):
    """Where the latest teaching action left off: the blocks it opened and the pool rotation."""
    latest: datetime | None
    courses: set
    slots: set
    continuing: bool
    prior_course_pool: str | None
    prior_kana_pool: str | None


def _block_anchors(service, ks, ability_service, kana_ability_service) -> _BlockAnchors:
    actions = [(at, 'course', cid) for cid, at in service['service_at'].items()]
    actions.extend((at, 'kana', slot) for slot, at in ks['service_at'].items())
    # Exact timestamp ties use existing stable queue order (e.g. an imported
    # batch), not an invented lexical ordering of content identities.
    actions.extend((at, 'course', cid) for cid, at in ability_service['continuations'].items())
    course_latest = max([*service['service_at'].values(), *ability_service['continuations'].values()], default=None)
    ability_latest = ability_service['latest']
    prior_course_pool = ('additional' if ability_latest is not None and (course_latest is None or ability_latest >= course_latest)
                        else 'introduction' if course_latest is not None else None)
    if ability_latest is not None:
        actions.append((ability_latest, 'course_ability', None))
    kana_latest = max(ks['service_at'].values(), default=None)
    direction_latest = kana_ability_service['latest']
    prior_kana_pool = ('additional' if direction_latest is not None and (kana_latest is None or direction_latest >= kana_latest)
                       else 'introduction' if kana_latest is not None else None)
    if direction_latest is not None:
        actions.append((direction_latest,'kana_ability',None))
    latest = max((value[0] for value in actions), default=None)
    courses = {cid for at, lane, cid in actions if at == latest and lane == 'course'} & service['started']
    slots = {slot for at, lane, slot in actions if at == latest and lane == 'kana'} & ks['started']
    return _BlockAnchors(latest, courses, slots, bool(courses or slots), prior_course_pool, prior_kana_pool)


class _Block(NamedTuple):
    """The block that runs once one is due: its lane and head, the two probes behind them."""
    lane: str | None
    selected: dict | None
    course: dict | None
    kana: dict | None
    waiting: bool = False   # nothing selected: a started block is inside its bridge wait


def _scope_inside(kana, course, scope):
    """(kana head inside, course head inside) the focus scope for choose_lane, or None without a scope."""
    if scope is None:
        return None
    from .arrangement.rules.focus import kana_member
    question = kana['question'] if kana else None
    return (bool(question is not None and kana_member(question.content_id, question.script) in scope),
            bool(course and course.get('content', {}).get('id') in scope))


def _scoped(scope):
    return {} if scope is None else {'scope_content_ids': scope}


def _kana_scoped(scope):
    return {} if scope is None else {'scope': scope}


def _block_head(store, learning, service, ks, anchors, *, earned, enabled_kinds, kana_kinds, now, projection,
                hold_started=False, skip_teaching_ids=frozenset(), scope=None, lessons_only=False) -> _Block:
    """The block that runs once one is due.

    First the blocks already started and not yet rated: those of the latest
    teaching action, or every started one; only when there are none and a
    block was earned, the fresh candidates of the four rotating pools.
    ``hold_started``: a started block that can go on within the bridge wait
    keeps the slot (no fresh candidate; ``waiting``). ``skip_teaching_ids``:
    the learner's process-local passes over a teaching head. ``scope``: the
    learner's focus scope (design 12 H2), None for none: the fresh candidates
    inside it are taught first (in each pool, between the pools and between
    the lanes); a started block goes on as before. ``lessons_only`` (the
    share rule, 2026-11-18): the fresh candidates are new lessons and new
    kana only (the introduction pools); an additional ability's first check
    and a kana direction opened later are review of their lane there. Those
    with their own introduction or guide, and a continuation after a failed
    placement, stay reachable: they are introduction-pool rows or started
    blocks.
    """
    from .first_teaching_kana import probe as kana_probe
    skip = {'skip_teaching_ids': frozenset(skip_teaching_ids)} if skip_teaching_ids else {}
    courses, slots = anchors.courses, anchors.slots
    if not anchors.continuing:
        # Finish a previously interrupted block before opening another one.
        courses, slots = service['started'], ks['started']
    course = learning.current(enabled_kinds, now=now, fair_new=True, teaching_only=True,
        teaching_content_ids=courses, **_shared(projection), **skip) if courses else None
    course_started = bool(course)
    kana = kana_probe(store, kana_kinds, now, slots=slots) if slots else None
    if earned and course is None and kana is None:
        if hold_started and (courses or slots):
            # One block at a time: one waiting for its check (after a restart
            # it is not this round's promoted block) is not passed over.
            later = now + bridge_wait()
            if (courses and learning.current(enabled_kinds, now=later, fair_new=True, teaching_only=True,
                                             teaching_content_ids=courses, **_shared(projection), **skip)
                    or slots and kana_probe(store, kana_kinds, later, slots=slots)):
                return _Block(None, None, None, None, waiting=True)
        # Disabled, deferred or dependency-blocked work must not trap earned
        # service. The ordinary teaching probes still enforce those controls.
        pools = ({} if lessons_only else
                 {'include_unassessed': True, 'prefer_unassessed': anchors.prior_course_pool != 'additional'})
        course = learning.current(enabled_kinds, now=now, fair_new=True, teaching_only=True,
            **pools, **_shared(projection), **skip, **_scoped(scope))
        kana_pools = ({} if lessons_only else
                      {'include_unassessed': True, 'prefer_unassessed': anchors.prior_kana_pool != 'additional'})
        kana = kana_probe(store, kana_kinds, now, new_only=True, **kana_pools, **_kana_scoped(scope))
        # A new candidate is not an already opened block. Keep genuine
        # continuations together, but let the persisted cursor rotate fresh
        # course/kana work after either lane's previous completion.
        course_started = bool(course and course.get('new_dispatch_continue'))
        inside = _scope_inside(kana, course, scope)
        if inside is not None:
            return _chosen(store, kana, course, course_started, inside)
    lane = store.teaching_queries.choose_lane(_priority(kana), _priority(course), course_started=course_started)
    selected = kana if lane == 'kana' else course if lane == 'course' else None
    return _Block(lane, selected, course, kana)


def _chosen(store, kana, course, course_started, inside) -> _Block:
    """_block_head's lane choice between two fresh candidates with the focus scope's membership."""
    lane = store.teaching_queries.choose_lane(_priority(kana), _priority(course), course_started=course_started,
                                              inside=inside)
    selected = kana if lane == 'kana' else course if lane == 'course' else None
    return _Block(lane, selected, course, kana)


def _protected_block(block: _Block, anchors, record):
    """Package a block head as the protected head; ``record`` says what made it due."""
    lane, selected, course, kana = block.lane, block.selected, block.course, block.kana
    if selected is None:
        return None
    return lane, {**selected, 'dispatch_priority': (2.5, 0),
        'first_teaching_protected': True, 'new_dispatch_continue': True,
        'teaching_service': {**record,
            'prior_course_pool': anchors.prior_course_pool,
            'course_candidate_pool': course.get('selected_course_pool') if course else None,
            'course_candidates': course.get('service_pool_candidates', {}) if course else {},
            'prior_kana_pool': anchors.prior_kana_pool,
            'kana_candidate_pool':kana.get('selected_kana_pool') if kana else None,
            'kana_candidates':kana.get('kana_service_candidates',{}) if kana else {},
            'selected_lane': lane}}


def _proportional_teaching(store, learning, service, ks, *, enabled_kinds, kana_kinds, now, ordinary_due, ability_service=None, kana_ability_service=None, projection=None, scope=None):
    """Reserve a finite share of already-enrolled work despite an old backlog.

    Neither a calendar-day quota on new work nor a due-date rewrite: after
    8..64 real due answers since the latest teaching action, one teaching
    block may precede ordinary due work. Older overdue work raises the bounded
    threshold; its remaining guide/first check stay
    together. The credit survives midnight even for three-answer visits; a
    past backlog cannot accumulate unlimited prepaid blocks. All ordinary
    idle-time learning remains available. Existing logs and checkpoints
    persist accounting without reserving anything during a read.
    """
    ability_service = ability_service or _ability_service(store, now, service)
    from .first_teaching_kana import direction_service
    kana_ability_service = kana_ability_service or direction_service(store,now,service['start'],service['end'],ks['started'])
    anchors = _block_anchors(service, ks, ability_service, kana_ability_service)
    since = anchors.latest if anchors.latest else datetime.min.replace(tzinfo=now.tzinfo)
    threshold = teaching_review_threshold(now.timestamp() - ordinary_due)
    served = due_reviews_served(store, now, since, limit=threshold)
    earned = served >= threshold
    if not anchors.continuing and not earned:
        return None
    block = _block_head(store, learning, service, ks, anchors, earned=earned, enabled_kinds=enabled_kinds,
                        kana_kinds=kana_kinds, now=now, projection=projection, scope=scope)
    return _protected_block(block, anchors, {'earned': earned, 'threshold': threshold,
        'ordinary_due_timestamp': ordinary_due,
        'due_reviews_served_capped': served, 'service_anchor_utc': since.isoformat()})


def _teaching_state(store, now):
    """Legacy observation shape; dispatch uses the more precise service state."""
    state = _teaching_service(store,now)
    introduced = store.get_setting('kana_introductions_v1', {}).get('introduced', {})
    kana_completed = any(_in_day(row.get('introduced_at_utc'),state['start'],state['end'],now)
                         for row in introduced.values() if isinstance(row,dict))
    return state['start'],state['started'],bool(state['guided_today'] or
        state['first_primary_today'] or kana_completed)


def _older_kana_waits(store, normal, kinds, start, now):
    """A same-content interleave must not hide a ready cross-day due card."""
    if not normal or not normal.get('pending'):
        return False
    pending = normal['pending']
    questions = pending['questions'][pending['question_index']:]
    old = {(r['content_id'],r['skill'],r['script']):r for r in store.scheduling_cards(aliases=True)
           if r['review_count'] > 0 and not r['suspended'] and datetime.fromisoformat(r['due_at_utc']) < start}
    if not old:
        return False
    unavailable = store._unavailable_content_ids(now)
    feedback = store.feedback_wait_times(old, now)
    from .kana_learning_intro import intro_wait_times
    exposures = intro_wait_times(store, questions)
    for q in questions:
        if not question_allowed(q):
            continue
        key = q.content_id,q.kind,q.script
        if key not in old or q.kind not in kinds or q.kind in WORD_SKILLS or q.content_id in unavailable:
            continue
        value = q.metadata.get('not_before_utc')
        due = datetime.fromisoformat(value) if value else now
        if q.metadata.get('kana_intro_v1') is True:
            due = max(due, exposures.get((q.content_id,q.script),now))
        if max(due,feedback.get(key,now)) <= now:
            return True
    return False


def _shared(projection):
    # Old/standalone LearningStore doubles keep their exact call signature.
    return {} if projection is None else {'projection': projection}


class _Services(NamedTuple):
    """The first-teaching service state of one dispatch moment (reads only)."""
    service: dict
    kana: dict
    ability: dict
    kana_ability: dict

    @property
    def stopped(self) -> bool:
        """Two finalized unscored initial checks today stop protected teaching for the day."""
        return (self.service['initial_unscored_today'] + self.kana.get('initial_unscored_today',0)
                + self.ability['unscored_today'] + self.kana_ability['unscored_today']) >= 2


def _services(store, now) -> _Services:
    service = _teaching_service(store,now)
    from .first_teaching_kana import teaching_state as kana_state, direction_service
    ks = kana_state(store,now,service['start'],service['end'])
    ability_service = _ability_service(store, now, service)
    kana_ability_service = direction_service(store,now,service['start'],service['end'],ks['started'])
    return _Services(service, ks, ability_service, kana_ability_service)


def _normal_head(store, normal_kana, normal_course):
    """(lane, head) that ordinary dispatch would serve between the two lane heads."""
    normal_lane = store.teaching_queries.choose_lane(_priority(normal_kana), _priority(normal_course),
        course_started=bool(normal_course and normal_course.get('new_dispatch_continue')))
    return normal_lane, normal_kana if normal_lane=='kana' else normal_course if normal_lane=='course' else None


def _explicit_head(store, learning, intent, services, *, enabled_kinds, kana_kinds, now, projection):
    from .explicit_learning_start import choose as choose_explicit_start
    return choose_explicit_start(store, learning, intent,
        enabled_kinds=enabled_kinds, kana_kinds=kana_kinds, now=now,
        service=services.service, kana_service=services.kana, **_shared(projection))


def explicit_start_head(store, learning, normal_kana, normal_course, *, enabled_kinds, kana_kinds, now,
                        projection=None):
    """The explicit-start part of choose_protected on its own (breadth first): None or a protected (lane, head).

    Same conditions as there, except the two-unscored stop (B8, 2026-11-18):
    a pending explicit start and an ordinary head of priority 3 or more.
    """
    now = _utc(now)
    from .explicit_learning_start import active as explicit_start
    intent = explicit_start(store)
    if intent is None:
        return None
    priority = _priority(_normal_head(store, normal_kana, normal_course)[1])
    if priority is None or priority[0] < 3:
        return None
    services = _services(store, now)
    return _explicit_head(store, learning, intent, services, enabled_kinds=enabled_kinds,
                          kana_kinds=kana_kinds, now=now, projection=projection)


class ShareProbe(NamedTuple):
    """What share_probe found: a protected (lane, head) or None, and why there is none."""
    found: tuple | None
    waiting: bool   # a started block is inside its bridge wait; it becomes the candidate when ready
    stopped: bool = False   # kept for callers; share_probe no longer stops on unscored checks (B8, 2026-11-18)


def share_probe(store, learning, *, enabled_kinds, kana_kinds, now, projection=None,
                skip_teaching_ids=frozenset(), scope=None) -> ShareProbe:
    """The new-content head of the time-share rule (design 12 B3).

    What _proportional_teaching gives once a block is earned, without the
    review count: first the blocks already started and not yet rated, then
    fresh candidates of the four rotating pools. None while a started
    block is inside its bridge wait (``waiting``); since 2026-11-18 not
    stopped by today's unscored initial checks (breadth first only uses it). ``skip_teaching_ids``:
    teaching heads the learner passed over in this run. ``scope``: the
    learner's focus scope, None for none: fresh candidates inside it first
    (_block_head). Reads only.
    """
    now = _utc(now)
    services = _services(store, now)
    # Design 12 B3/B8 (2026-11-18): under breadth first two unscored first checks no longer stop new
    # content for the day (an unjudged typed answer is not a reason to stop teaching); 扎实 keeps the stop.
    anchors = _block_anchors(services.service, services.kana, services.ability, services.kana_ability)
    block = _block_head(store, learning, services.service, services.kana, anchors, earned=True,
                        enabled_kinds=enabled_kinds, kana_kinds=kana_kinds, now=now, projection=projection,
                        hold_started=True, skip_teaching_ids=skip_teaching_ids, scope=scope, lessons_only=True)
    since = anchors.latest if anchors.latest else datetime.min.replace(tzinfo=now.tzinfo)
    found = _protected_block(block, anchors, {'rule': 'time_share', 'service_anchor_utc': since.isoformat()})
    return ShareProbe(found, block.waiting)


def share_candidate(store, learning, *, enabled_kinds, kana_kinds, now, projection=None,
                    skip_teaching_ids=frozenset(), scope=None):
    """share_probe's head alone: None or a protected (lane, head)."""
    return share_probe(store, learning, enabled_kinds=enabled_kinds, kana_kinds=kana_kinds, now=now,
                       projection=projection, skip_teaching_ids=skip_teaching_ids, scope=scope).found


def course_block_continues(learning, question_id, *, enabled_kinds, now, projection=None) -> bool:
    """Whether an open course block's question can be served now or within the bridge wait.

    The ordinary course read at that later moment, so every existing control
    applies (question kinds, prerequisites, pauses, the retrieval delay, the
    queue guard of effective_question_due, retry waits). Meant for a sealed
    round's promoted entry, which is first in the frozen course order.
    """
    head = learning.current(enabled_kinds, now=_utc(now) + bridge_wait(), fair_new=True, **_shared(projection))
    return bool(head and head.get('type') in {'question', 'instruction'}
                and head['question']['id'] == question_id)


def kana_block_continues(store, kana_kinds, key, now) -> bool:
    """Whether an open kana block's queue entry (its practice_round.native_key) can be served
    now or within the bridge wait: the kana teaching probe at that later moment
    (kinds, pauses, prerequisites, the introduction's delay, feedback waits)."""
    from .first_teaching_kana import probe as kana_probe
    from .practice_round import native_key
    found = kana_probe(store, kana_kinds, _utc(now) + bridge_wait(), slots={(key[0], key[2])})
    return bool(found and native_key(found['question']) == tuple(key))


def choose_protected(store, learning, normal_kana, normal_course, *, enabled_kinds, kana_kinds, now,
                     projection=None, scope=None):
    """Return None or a protected (lane, head); never reserve or score content.

    New blocks earn bounded, cross-day credit from actual due reviews. The
    latest unfinished guide/exposure continues through its first real rating.
    The original same-day cycling safeguard also permits one block after a
    direction has been rated twice. Two finalized unscored initial checks stop
    protection for that day, without changing memory.
    Feedback, drafts and real short steps always win. Ordinary cross-day due
    yields a bounded share after actual review service; a backlog can no
    longer block every introduction indefinitely. Idle-time new work remains
    unlimited and unchanged.
    projection is the caller's LearningStore.read_operation for this action;
    it only shares unchanged course reads between the probes below.
    scope: the learner's focus scope (design 12 H2), None for none: fresh
    teaching candidates inside it first; the earning rule, the explicit start,
    started blocks and the two-unscored stop are unchanged.
    """
    now = _utc(now)
    normal_lane, normal = _normal_head(store, normal_kana, normal_course)
    priority = _priority(normal)
    from .explicit_learning_start import active as explicit_start
    intent = explicit_start(store)
    if priority is None or priority[0] < 3 or (priority[0] != 3 and intent is None):
        return None
    services = _services(store, now)
    service, ks = services.service, services.kana
    ability_service, kana_ability_service = services.ability, services.kana_ability
    from .first_teaching_kana import probe as kana_probe
    if services.stopped:
        return None
    if intent is not None:
        explicit = _explicit_head(store, learning, intent, services, enabled_kinds=enabled_kinds,
                                  kana_kinds=kana_kinds, now=now, projection=projection)
        if explicit is not None:
            return explicit
    if priority[0] != 3:
        return None
    proportional = _proportional_teaching(store, learning, service, ks,
        enabled_kinds=enabled_kinds, kana_kinds=kana_kinds, now=now, ordinary_due=priority[1],
        ability_service=ability_service, kana_ability_service=kana_ability_service, projection=projection,
        scope=scope)
    if proportional is not None:
        return proportional
    # Keep the original small same-day cycling safeguard for very short visits.
    # It cannot grant a second block after today's first real rating.
    start = service['start']
    if (datetime.fromtimestamp(priority[1],now.tzinfo) < start
            or _older_kana_waits(store,normal_kana,set(kana_kinds),start,now)
            or service['first_primary_today'] or ks['first_primary_today']):
        return None
    # Once a guide or kana exposure was completed today, protect only the
    # delayed first check of such blocks, never another introduction/guide.
    completed_teaching = bool(service['guided_today'] or ks['introduced_today'])
    courses = service['started'] & service['guided_today'] if completed_teaching else service['started']
    slots = ks['started'] & ks['introduced_today'] if completed_teaching else ks['started']
    course = learning.current(enabled_kinds,now=now,fair_new=True,teaching_only=True,
                              teaching_content_ids=courses,**_shared(projection)) if courses else None
    kana = kana_probe(store,kana_kinds,now,slots=slots) if slots else None
    lane = store.teaching_queries.choose_lane(_priority(kana),_priority(course),course_started=bool(course))
    selected = kana if lane=='kana' else course if lane=='course' else None
    if selected is not None:
        return lane,{**selected,'dispatch_priority':(2.5,0),'first_teaching_protected':True,
                     'new_dispatch_continue':True}
    if completed_teaching:
        return None
    if normal_lane=='kana':
        q = normal['question']
        card = store.get_scheduling_card(q.content_id,q.kind,q.script)
        _, counts = reviews_today(store,now)
        identity = card['card_id'] if card else None
    else:
        q = normal['question']
        card = learning.get_unit(q['target_id'],q['ability'])
        _, counts = reviews_today(store,now,course=True)
        identity = card['unit_id'] if card else None
    if card is None or counts.get(identity,0) < 2:
        return None
    course = learning.current(enabled_kinds,now=now,fair_new=True,teaching_only=True,**_shared(projection),
                              **_scoped(scope))
    kana = kana_probe(store,kana_kinds,now,new_only=True,**_kana_scoped(scope))
    inside = _scope_inside(kana, course, scope)
    lane = store.teaching_queries.choose_lane(_priority(kana),_priority(course),
        course_started=bool(course and course.get('new_dispatch_continue')), **({} if inside is None else {'inside': inside}))
    selected = kana if lane=='kana' else course if lane=='course' else None
    return (lane,{**selected,'dispatch_priority':(2.5,0),'first_teaching_protected':True}) if selected else None
