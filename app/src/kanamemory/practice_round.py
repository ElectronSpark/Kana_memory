"""App-instance practice order; never a persisted schedule or eligibility grant.

Startup explicitly enables the root StudyStore. Opening another connection does
not create a round. Callers still check due dates, prerequisites and material
safety when displaying or submitting a frozen entry.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from uuid import uuid4


@dataclass
class AppRunPlan:
    run_id: str = field(default_factory=lambda: uuid4().hex)
    is_sealed: bool = False
    finished: bool = False
    priorities: dict = field(default_factory=dict)
    course_order: tuple | None = None
    native_orders: dict = field(default_factory=dict)
    native_positions: dict = field(default_factory=dict)
    question_ids: dict = field(default_factory=dict)


def enable(store):
    """Opt in once at app startup, without beginning or replacing a round."""
    store._practice_run_enabled = True


def reset(store):
    """Forget runtime plans only after an explicit learner-data reset commits."""
    store._practice_run_plans = {}
    store._practice_run_plan = None
    store._practice_round_question_ids = {}


def restore_connection(previous, reopened, *, imported=False):
    """A backup worker's connection swap is not an app restart."""
    if not getattr(previous, '_practice_run_enabled', False):
        return
    if imported:
        from .practice_archive import archive_previous_rounds
        archive_previous_rounds(reopened)
        reopened._practice_run_plans = {}
    elif hasattr(previous, '_practice_run_plans'):
        reopened._practice_run_plans = previous._practice_run_plans
    elif hasattr(previous, '_practice_run_plan'):
        reopened._practice_run_plans = {
            getattr(previous, '_practice_run_plan_lane', 'main'): previous._practice_run_plan}
    enable(reopened)
    _plan(reopened)


def _lane(store):
    return getattr(store, 'practice_lane', 'main')


def _plan(store, lane=None):
    """Select an existing plan without creating one on a worker connection."""
    current = _lane(store)
    selected = current if lane is None else lane
    plans = getattr(store, '_practice_run_plans', None)
    if plans is not None:
        plan = plans.get(selected)
    else:
        plan = (getattr(store, '_practice_run_plan', None)
                if selected == getattr(store, '_practice_run_plan_lane', 'main') else None)
    if (getattr(store, '_practice_run_enabled', False) and selected == current
            and (plans is not None or hasattr(store, '_practice_run_plan'))):
        store._practice_run_plan = plan
        store._practice_run_plan_lane = current
    return plan


def _begin_lane(store, lane, restart_finished):
    if not getattr(store, '_practice_run_enabled', False):
        return None
    if not hasattr(store, '_practice_run_plans'):
        prior = getattr(store, '_practice_run_plan', None)
        store._practice_run_plans = ({getattr(store, '_practice_run_plan_lane', 'main'): prior}
                                     if isinstance(prior, AppRunPlan) else {})
    plan = _plan(store, lane)
    if plan is None or plan.finished and restart_finished:
        plan = AppRunPlan()
        store._practice_run_plans[lane] = plan
    _plan(store)
    return plan


def begin(store, *, restart_finished=False):
    """Resume this run; only an explicit start after completion resets it."""
    return _begin_lane(store, _lane(store), restart_finished)


def active(store):
    plan = _plan(store)
    return bool(getattr(store, '_practice_run_enabled', False)
                and isinstance(plan, AppRunPlan) and not plan.finished)


def collecting(store):
    return active(store) and not store._practice_run_plan.is_sealed


def planned_round_running(store, *, lane='main'):
    """Home counts must not include work that this active round cannot admit."""
    plan = _plan(store, lane)
    return bool(getattr(store, '_practice_run_enabled', False)
                and isinstance(plan, AppRunPlan) and plan.is_sealed and not plan.finished)


def sealed(store):
    plan = _plan(store)
    return bool(getattr(store, '_practice_run_enabled', False)
                and isinstance(plan, AppRunPlan) and plan.is_sealed)


def seal(store):
    if active(store):
        store._practice_run_plan.is_sealed = True


def mark_finished(store):
    if active(store):
        store._practice_run_plan.finished = True


def finished(store):
    plan = _plan(store)
    return bool(getattr(store, '_practice_run_enabled', False)
                and isinstance(plan, AppRunPlan) and plan.finished)


def restart(store, *, lane=None):
    """An explicit new/extended batch requests one new scheduling pass."""
    selected = _lane(store) if lane is None else lane
    plan = _plan(store, selected)
    if isinstance(plan, AppRunPlan):
        plan.finished = True
    return _begin_lane(store, selected, True)


def frozen_priority(store, lane, stable_id, initial_priority):
    """Remember a semantic entry, not its mutable position in a queue.

    Unknown entries after seal return None, so callers must filter them. In
    legacy/non-round paths the original priority passes through unchanged.
    """
    if finished(store):
        return None
    if not active(store):
        return initial_priority
    key = (lane, stable_id)
    plan = store._practice_run_plan
    if key not in plan.priorities:
        if not collecting(store):
            return None
        plan.priorities[key] = deepcopy(initial_priority)
    return deepcopy(plan.priorities[key])


def _question_id(row):
    # sqlite3.Row and dict both support this protocol.
    keys = row.keys()
    identity = row['question_id'] if 'question_id' in keys else row['id']
    if not isinstance(identity, str) or not identity:
        raise ValueError('Practice order needs a stable question ID')
    return identity


def freeze_course_rows(store, rows, priority):
    """Capture all course IDs once, then filter without adding or reordering.

    Missing/temporarily blocked rows do not erase their frozen place. This
    function does not decide whether a returned row is currently answerable.
    """
    rows = list(rows)
    if finished(store):
        return []
    if not active(store):
        return sorted(rows, key=priority)
    plan = store._practice_run_plan
    by_id = {}
    for row in rows:
        identity = _question_id(row)
        if identity in by_id:
            raise ValueError('Duplicate question in course order')
        by_id[identity] = row
    if plan.course_order is None:
        if not collecting(store):
            return []
        ranked = []
        for row in rows:
            identity = _question_id(row)
            value = frozen_priority(store, 'course', identity, priority(row))
            if value is not None:
                ranked.append((value, identity))
        # Stable sort retains original insertion order for equal priorities.
        ranked.sort(key=lambda value: value[0])
        plan.course_order = tuple(identity for _, identity in ranked)
    return [by_id[identity] for identity in plan.course_order if identity in by_id]


capture_course_order = freeze_course_rows


def native_key(question):
    """Exact frozen question fields; queue indices and runtime marks are absent."""
    def value(field, default=''):
        return (question.get(field, default) if isinstance(question, dict)
                else getattr(question, field, default))
    return (value('content_id'), value('kind'), value('script'), value('prompt'),
            value('answer'), tuple(value('options', ())), value('audio_text'),
            tuple(value('accepted_answers', ())))


def native_priority(store, sid, question, initial_priority):
    return frozen_priority(store, ('native', sid), native_key(question), initial_priority)


def freeze_native_order(store, sid, ordered_rows):
    """Freeze an already ordered full session without rewriting its indices.

    Capture ready, future, blocked and parked entries together. Subsequent
    Each original occurrence keeps its rank, even when identical questions
    have already been consumed. Call remap_native_order only after committing
    a session reordering; a dry-run projection cannot change this identity map.
    """
    rows = list(ordered_rows)
    if finished(store):
        return []
    if not active(store):
        return rows
    plan = store._practice_run_plan
    if sid not in plan.native_orders:
        if not collecting(store):
            return []
        positions = {}
        for rank, (index, question) in enumerate(rows):
            if type(index) is not int or index < 0 or index in positions:
                raise ValueError('Native order needs distinct nonnegative indices')
            positions[index] = (native_key(question), rank)
        plan.native_orders[sid] = tuple(native_key(question) for _, question in rows)
        plan.native_positions[sid] = positions
        return rows
    positions = plan.native_positions.get(sid, {})
    used, admitted = set(), []
    for row in rows:
        index, question = row
        saved = positions.get(index)
        if index not in used and saved and saved[0] == native_key(question):
            admitted.append((saved[1], row))
            used.add(index)
    admitted.sort(key=lambda entry: entry[0])
    return [row for _, row in admitted]


def remap_native_order(store, sid, remap):
    """Follow a committed old-index -> new-index permutation, without new IDs.

    Unmapped prefix entries stay put. Validate the whole replacement before
    mutating memory, so a failed or conflicting mapping leaves the plan intact.
    This performs no database writes and must not run on prepare dry-runs.
    """
    if not active(store):
        return
    positions = store._practice_run_plan.native_positions.get(sid)
    if positions is None:
        return
    if (not isinstance(remap, dict)
            or any(type(old) is not int or old < 0 or type(new) is not int or new < 0
                   for old, new in remap.items())):
        raise ValueError('Invalid native order remapping')
    updated = {}
    for index, identity in positions.items():
        destination = remap.get(index, index)
        if destination in updated:
            raise ValueError('Native order remapping overlaps another occurrence')
        updated[destination] = identity
    store._practice_run_plan.native_positions[sid] = updated


def promote_initial(store, *, course_id=None, session_id=None, question=None):
    """Protect the initial teaching head before sealing, never add a new head.

    course_id is its frozen question ID. Native teaching probes may return a
    reordered preview index, so match its earliest frozen semantic occurrence.
    Remaining instance ranks stay intact, including identical later questions.
    """
    if not collecting(store):
        return False
    plan = store._practice_run_plan
    promoted = False
    if course_id is not None and course_id in (plan.course_order or ()):
        plan.course_order = (course_id, *(identity for identity in plan.course_order
                                        if identity != course_id))
        plan.priorities[('cross-course', course_id)] = (1, 0)
        promoted = True
    if session_id is not None and question is not None:
        key = native_key(question)
        positions = plan.native_positions.get(session_id, {})
        matching = [(rank, index) for index, (candidate, rank) in positions.items()
                    if candidate == key]
        if matching:
            _, index = min(matching)
            positions[index] = (key, -1)
            plan.priorities[('cross-kana', (session_id, key))] = (1, 0)
            promoted = True
    return promoted


def _archived_question(store, question_id):
    from .practice_archive import archived_question
    return archived_question(store, question_id)


def next_question_id(store, proposed_id):
    """Avoid reusing a retired ID without inventing a learning attempt.

    A repeated proposal stays idempotent within this app round. No question,
    archive receipt, attempt count or scheduler state is written here.
    """
    if not _archived_question(store, proposed_id):
        return proposed_id
    plan = _plan(store, 'main')
    if isinstance(plan, AppRunPlan):
        allocated = plan.question_ids
    else:
        allocated = getattr(store, '_practice_round_question_ids', None)
        if allocated is None:
            allocated = {}
            store._practice_round_question_ids = allocated
    previous = allocated.get(proposed_id)
    if previous is None or _archived_question(store, previous):
        suffix = ':round:' + uuid4().hex
        base = proposed_id if len(proposed_id) + len(suffix) <= 1024 else (
            'practice:' + sha256(proposed_id.encode('utf-8')).hexdigest())
        previous = base + suffix
        allocated[proposed_id] = previous
    return previous
