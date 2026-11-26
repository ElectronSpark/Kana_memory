"""The home's two counts of work that has no queued question (design 13 S3; formerly study_path_runtime).

course_preview: course abilities whose next question was never saved;
kana_preview: kana reviews without a queued question. Both read without
writing, take their collaborators explicitly and see no app object or Toga. A
running planned round admits none of this work: ready (and the courses'
instruction) read 0, waiting and next_due stay long-term (review 2026-11-08
section 4b).
"""
from __future__ import annotations

import json
from datetime import datetime
from types import MappingProxyType

from ..answer_input import system_japanese_enabled
from ..practice_preferences import enabled_preference_kinds

# kana_preview's ``pending`` when the caller did not read the main checkpoint; None means "known empty".
UNREAD = object()


def course_preview(store, learning_store, *, course_kinds, costs, projection=None, round_limited=True):
    """(the unqueued course counts, the teaching ids for home_counts).

    The gap between finalizing a course and saving its next question: a
    process interruption can leave genuine ability schedules without a
    pending question. Use the continuation planner and append timing rules
    without materializing or saving question snapshots on the home page.
    ``projection``: the caller's LearningStore.read_operation (shared moment,
    prerequisite evidence and exposure times); None reads everything live.
    ``course_kinds()``: the enabled course kinds, read here as before;
    ``costs()``: the add page's AdmissionCosts, whose plan preview the add page
    shares (the next refresh without a write and the add page's existing-work
    line read the plans instead of planning again).

    The counts are {"ready", "waiting", "instruction", "blocked",
    "blocked_content", "blocked_material", "next_due"}. The teaching ids are
    {"queued": {(content_id, instruction_scope)} of visible queued rows in a
    teaching stage, keyed like the teaching set of LearningStore.counts()
    before its filters, "unqueued": {content_id}}. A running round zeroes
    the counts' ready/instruction (it cannot admit them) but keeps the
    unqueued ids: "可开始初学" counts the same content before, during and after
    a round (review 2026-11-08 N2); ``round_limited=False`` (the records page,
    the sealed round's left-out check) ignores the round. "planned":
    {(content_id, ability)} of the abilities without a question that are not
    suspended and get a question planned under the enabled kinds (blocked or
    not, due or not): design 12 UI6 counts a due one of them in "重点之外到期
    N 题" (presenter.home_focus, unqueued_course).
    "due": {(content_id, ability)} of those counted in "ready" (answerable
    now, no question yet): design 12 B5 re-arranges a running round for them.
    """
    from ..domain import _utc
    from ..learning_dependencies import independent_ready_ids, prerequisite_status
    from ..learning_questions import KIND_ABILITIES
    now = projection.moment() if projection is not None else _utc(None)
    enabled = course_kinds()
    result = {"ready": 0, "waiting": 0, "instruction": 0, "blocked": 0,
              "blocked_content": 0, "blocked_material": 0, "next_due": None}
    ids = {"queued": set(), "unqueued": set(), "planned": set(), "due": set()}
    from ..practice_round import planned_round_running
    limited = round_limited and planned_round_running(store)
    if not enabled:
        return result, ids
    # 08 section 8.2: unavailable content (disabled/missing library) is
    # neither previewed nor counted; its rows stay unchanged.
    unavailable = learning_store.availability(projection=projection).unavailable_ids
    visible_rows = [row for row in store.queries.pending_course_headers(enabled)
                    if row['content_id'] not in unavailable]
    visible_content = {row['content_id'] for row in visible_rows}
    known_teaching = set()
    for row in visible_rows:
        current = learning_store.ability_instruction_row(row['content_id'], row['ability'])
        if current['stage'] in {'intro', 'guide'}:
            known_teaching.add(current['introduction_id'])
    # The queued side over the rows counts() reads (no paused/reserved
    # unit, no unavailable content); an initial diagnostic is "ready".
    scopes = {}
    for row in learning_store.dispatch_pending(enabled, projection=projection):
        if row['result_json'] is not None and json.loads(row['result_json']).get('initial_diagnostic'):
            continue
        key = row['content_id'], row['ability']
        if key not in scopes:
            current = learning_store.ability_instruction_row(*key)
            scopes[key] = (current['stage'] in {'intro', 'guide'}, dict(current).get('instruction_scope'))
        if scopes[key][0]:
            ids["queued"].add((row['content_id'], scopes[key][1]))
    blocked_content = set()
    abilities = sorted({KIND_ABILITIES[kind] for kind in enabled})
    missing = [row for row in store.queries.unqueued_course_ids(abilities) if row[0] not in unavailable]
    ready_ids, teaching, future = (projection.ready_ids() if projection is not None
                                   else independent_ready_ids(learning_store)), set(), []
    system_input = system_japanese_enabled(store)
    exposures = (projection.exposure_times(now) if projection is not None
                 else learning_store.content_exposure_times(now=now))
    prerequisites = {}
    from ..intro_batch import ROUTE, ROUTE_KEY
    from ..course_planning import PassFacts, preview_question_plans
    facts = PassFacts(store.queries)   # the known set, the opening and the enrolled ids, read once for the preview
    # The plans of the lessons without a question, for this state of the learner records: the next
    # refresh without a write, and the add page's existing-work line, read them instead of planning again.
    plans = costs().plan_preview()
    from ..arrangement.services.review_filter import serving_filter
    serve = serving_filter(store)
    from ..arrangement.sources.recent import RecentFirsts, serviceable
    firsts = getattr(exposures, "first_rated", None) or RecentFirsts(store)
    served = {KIND_ABILITIES[kind] for kind in enabled}
    from ..learning_probe import GateReader
    probe_gate = GateReader(learning_store, now)

    def prerequisites_block(item, content_row):
        scope = dict(content_row).get('instruction_scope')
        key = item['id'], scope
        if key not in prerequisites:
            prerequisites[key] = (learning_store.prerequisite_status(item['id'], scope,
                ready_ids=ready_ids) if scope else prerequisite_status(learning_store, item,
                ready_ids=ready_ids))['blocked']
        return prerequisites[key]

    def teaching_stage(item, content_row, record):
        """The unit's lesson is in teaching (its counts are the teaching ones); False otherwise."""
        if content_row["stage"] not in {"intro", "guide"}:
            return False
        if content_row['introduction_id'] not in known_teaching:
            record.teaching.add(content_row['introduction_id'])
            record.unqueued.add(item['id'])
        return True

    def planned(item):
        """The lesson's record from its plans (one per unit that gets a question)."""
        record = _LessonRecord()
        # 四之二: a not-yet-open later ability is neither ready nor teaching,
        # matching LearningStore.counts() for pending rows.
        gate = (learning_store.batch_ability_gate(item['id'], enabled)
                if item.get('checkpoint', {}).get(ROUTE_KEY) == ROUTE else None)
        # Already assessed lessons are grandfathered by the dependency
        # contract. Only new lessons need a fresh prerequisite inspection.
        for unit, plan in preview_question_plans(plans, item['id'], lambda item=item: item,
                enabled_kinds=enabled, queries=store.queries, learning_store=learning_store,
                system_japanese_input=system_input, facts=facts,
                unit_states={u['ability']: u for u in item['units']}, unavailable_ids=unavailable):
            if unit["suspended"]:
                continue
            record.planned.add((item['id'], unit['ability']))
            if gate and unit['ability'] in gate['closed']:
                continue
            if serve is not None and not serve.allows(item['id']) and not serve.started_work(item['id'], unit['ability']):
                continue   # design 12 H10: left out by the learner's filter (still "planned" for the apart counts)
            if learning_store.content_question_blocked(item, plan):
                record.blocked_material += 1
                continue
            content_row = learning_store.ability_instruction_row(item['id'], unit['ability'])
            if prerequisites_block(item, content_row):
                record.blocked += 1
                # A pending sibling ability already contributes this content
                # to LearningStore.counts()["blocked_content"].
                if item["id"] not in visible_content:
                    record.blocked_content.add(item["id"])
                continue
            # Probe first (design 12 N6): the gate LearningStore.counts() applies to a queued row, for the unit
            # without one; the spacing is read from the stored answers, so an app start does not open it.
            held = probe_gate.unit(item['id'], unit['ability'], content_row)
            if held == "hold":
                continue   # not yet open: neither ready, waiting nor teaching
            if held is not None:
                record.waiting += 1
                record.future.append(held)
                continue
            if teaching_stage(item, content_row, record):
                continue
            due = learning_store.planned_question_due(unit, plan, now=now, exposure_times=exposures)
            if content_row["stage"] == "bridge" and not learning_store.retrieval_ready(content_row, now):
                due = max(due or "", content_row["retrieval_at_utc"])
            if due and datetime.fromisoformat(due) > now:
                record.waiting += 1
                record.future.append(due)
            else:
                record.ready += 1
                record.due.add((item['id'], unit['ability']))
        return record

    def may_be_free(item, ability):
        """Whether a plan of ``ability`` can be a free retrieval: one of its templates under the enabled kinds
        is (LearningStore.free_retrieval of the plan the planner would make of it); a lesson without a
        template table may be (its questions are built by the legacy builders)."""
        from ..learning_questions import practice_templates
        from ..learning_store import free_retrieval
        table = practice_templates(item)
        if not isinstance(table, dict):
            return True
        for kind in enabled:
            if KIND_ABILITIES.get(kind) != ability:
                continue
            for raw in table.get(kind) or ():
                if not isinstance(raw, dict):
                    return True
                plan = {"target_id": item['id'], "ability": ability, "kind": kind,
                        "input_mode": raw.get("input_mode", "choice")}
                if "practice" in raw:
                    plan.update(prompt=raw.get("prompt", ""), practice=raw["practice"])
                if free_retrieval(plan):
                    return True
        return False

    def live_template(item, ability):
        """Whether the lesson's template table (practice_templates, shared) has, for ``ability``, a template of an
        enabled kind the planner may ask (question_kinds) that is not retired (retired_practice_variants: the
        exact retirements, the cognate rule and the derived twins). The retired set is a fact of the shared
        table, read once (table_fact): the table is keyed by this lesson value and the namespaces the
        retirements come from. A lesson without a table is built by the legacy builders, which retirements do
        not reach: live."""
        from ..learning_questions import practice_templates, question_kinds, retired_practice_variants, table_fact
        table = practice_templates(item)
        if not isinstance(table, dict):
            return True
        retired = table_fact(table, "retired", lambda value: retired_practice_variants(item, value))
        for kind in question_kinds(item, ability=ability, templates=table):
            if kind not in enabled:
                continue
            if any(isinstance(raw, dict) and (kind, variant) not in retired
                   for variant, raw in enumerate(table.get(kind) or ())):
                return True
        return False

    def bounded(item):
        """The lesson's record from its stored columns when none of its units can be due now, else None.

        Design 12 (the plan-free bounds of an unqueued unit): a unit's due without a plan is ``upper``
        (planned_question_due of a question that is no free retrieval: the stored due with its floors), and
        ``lower`` = min(upper, its probe due) bounds every plan's due from below. A lesson whose every unit
        has ``lower`` after now is not planned: after the exclusions that need no plan (suspension, the
        review filter) each such unit is waiting. One that is due now by the queue's N2 reading is planned
        too, so that "planned" is exact for every unit DueReviews counts; so is a lesson of the batch route
        (its gate closes kinds), and one with a unit in teaching or blocked by its prerequisites (those
        counts take a unit only with a question). So is every lesson for which a bounded unit could differ from
        its plan (design 12 N16, two cheap and exact tests): one whose questions its material can block
        (LearningStore.content_question_may_block), and one with an ability that has no live template of an
        enabled kind (live_template: it gets no question at all).
        """
        if not PLAN_FREE_BOUNDS or item.get('checkpoint', {}).get(ROUTE_KEY) == ROUTE:
            return None
        from ..learning_questions import practice_templates
        if learning_store.content_question_may_block(item, practice_templates(item)):
            return None   # a question of it can be blocked by its material: planned, so blocked_material is exact
        record = _LessonRecord()
        pending = store.queries.pending_abilities(item['id'])
        admitted = item.get("enrolled_abilities", item.get("abilities", []))
        for unit in item['units']:
            if (not unit or unit['ability'] not in admitted or unit['ability'] in pending
                    or unit['ability'] not in served or unit["suspended"]):
                continue   # iter_question_plans plans none of these, or the preview skips it (suspended)
            if not live_template(item, unit['ability']):
                return None   # its templates of the enabled kinds are all retired: no question, planned as before
            # The probe due counts only for a plan that is a free retrieval (_ability_due): an ability whose
            # enabled templates offer none is planned at its stored due whatever its probe due says.
            probe = unit['probe_due_at_utc'] if may_be_free(item, unit['ability']) else None
            if not unit['due_at_utc'] or datetime.fromisoformat(unit['due_at_utc']) <= now or (
                    probe and datetime.fromisoformat(probe) <= now):
                return None   # due now as stored, or without a due: planned as before (no bound is read)
            reading = serviceable(unit, unit['due_at_utc'], firsts, now)[0]
            if reading and datetime.fromisoformat(reading) <= now:
                return None
            if serve is not None and not serve.allows(item['id']) and not serve.started_work(item['id'], unit['ability']):
                continue
            content_row = learning_store.ability_instruction_row(item['id'], unit['ability'])
            if content_row["stage"] in {"intro", "guide"} or prerequisites_block(item, content_row):
                return None   # counted as blocked or teaching only with a question: planned as before
            lower = learning_store.planned_question_due(unit, _NO_PLAN, now=now, exposure_times=exposures)
            if lower and probe:
                lower = min((lower, probe), key=datetime.fromisoformat)
            if content_row["stage"] == "bridge" and not learning_store.retrieval_ready(content_row, now):
                lower = max(lower or "", content_row["retrieval_at_utc"])
            if not lower or datetime.fromisoformat(lower) <= now:
                return None
            record.waiting += 1
            record.lowers.append(lower)
        return record

    records, items = {}, {}
    for row in missing:
        item = learning_store.get_item(row[0])
        record = bounded(item)
        records[item['id']], items[item['id']] = (planned(item) if record is None else record), item
    # The next-review moment stays exact: the lesson of the unit with the earliest lower bound is planned,
    # until the earliest planned due is not later than every lower bound left (one plan as a rule).
    left = sorted((datetime.fromisoformat(lower), cid) for cid, record in records.items() for lower in record.lowers)
    found = [datetime.fromisoformat(due) for record in records.values() for due in record.future]
    best = min(found) if found else None
    while left and (best is None or left[0][0] < best):
        cid = left[0][1]
        records[cid] = planned(items[cid])
        left = [entry for entry in left if entry[1] != cid]
        dues = [datetime.fromisoformat(due) for due in records[cid].future]
        best = min([*dues, *([best] if best is not None else [])]) if dues or best is not None else None
    for record in records.values():
        for name in ("ready", "waiting", "blocked", "blocked_material"):
            result[name] += getattr(record, name)
        teaching |= record.teaching
        blocked_content |= record.blocked_content
        future.extend(record.future)
        ids["unqueued"] |= record.unqueued
        ids["planned"] |= record.planned
        ids["due"] |= record.due
    result["instruction"] = len(teaching)
    result["blocked_content"] = len(blocked_content)
    result["next_due"] = min(future) if future else None
    if limited:
        result["ready"] = result["instruction"] = 0
    return result, ids


# planned_question_due of a unit without a plan: a question that is no free retrieval (_ability_due keeps the due).
_NO_PLAN = MappingProxyType({"input_mode": "choice"})
# The plan-free bounds of course_preview (design 12, N16); False plans every unit (the reference the
# equivalence test compares with).
PLAN_FREE_BOUNDS = True


class _LessonRecord:
    """What one lesson without a queued question adds to course_preview's counts and ids."""

    def __init__(self):
        self.ready = self.waiting = self.blocked = self.blocked_material = 0
        self.teaching, self.blocked_content, self.unqueued, self.planned, self.due = set(), set(), set(), set(), set()
        self.future, self.lowers = [], []


def kana_preview(store, inputs, *, repository, paths, queue_kinds, pending=UNREAD):
    """Due kana reviews without a queued question, and the next refresh time, without writing.

    A finished queue can leave real review cards but no checkpoint. Count
    their original directions only, using the builder's eligibility rules
    and the same queue policy/service availability as actual resumption.
    Only unfinished directions reserve work; history cannot hide due cards
    or newly unlocked recall directions in a continuously extended queue.
    ``inputs``: HomeInputs (the practice preferences in memory, whose kinds are
    used without a pending queue, and the speech and handwriting device state); ``repository()``, ``paths()``: the
    content repository and the current StudyPathService; ``queue_kinds(pending)``:
    the pending queue's kinds; ``pending``: the main checkpoint as the caller
    read it (UNREAD: read it here; None: known empty).
    Returns {"ready", "waiting", "next_due"}.
    """
    from ..practice_round import planned_round_running
    limited = planned_round_running(store)
    from ..domain import (WORD_SKILLS, LISTENING_SKILLS, HANDWRITING_SKILLS,
                          CONTENT_CONTROLS_SETTING, _BatchQuestionStore,
                          build_questions, _utc)
    now = _utc(None)
    pending = store.load_session_progress(lane="main", lazy_questions=True) if pending is UNREAD else pending
    kinds = queue_kinds(pending) if pending else enabled_preference_kinds(inputs.practice_preferences)
    from ..kana_context_words import preview_context_word
    context_count = int(preview_context_word(repository(), store, kinds,
        audio_available=inputs.speech_ready, now=now))
    kinds = {kind for kind in kinds if kind not in WORD_SKILLS
             and (inputs.speech_ready or kind not in LISTENING_SKILLS)
             and (inputs.handwriting_ready or kind not in HANDWRITING_SKILLS)}
    if not kinds:
        return {"ready": 0 if limited else context_count, "waiting": 0, "next_due": None}
    queued = {(q.content_id, q.kind, q.script) for q in
              (pending["questions"][pending["question_index"]:] if pending else [])
              if q.kind not in WORD_SKILLS}
    grouped, due_by_key = {}, {}
    controls = store.get_setting(CONTENT_CONTROLS_SETTING, {})
    from ..kana_routing import candidate_cards, current_identities, prospective_identities
    cards = candidate_cards(store, kinds, now)
    identities = current_identities(store, queued | {(c['content_id'], c['skill'], c['script']) for c in cards})
    occupied = {identities[key] for key in queued}
    feedback_due = store.feedback_wait_times(
        ((c["content_id"], c["skill"], c["script"]) for c in cards), now)
    from ..arrangement.rules.focus import kana_member
    from ..arrangement.rules.review_filter import allows
    from ..arrangement.services.review_filter import serving_filter
    serve = serving_filter(store)   # design 12 H10: what the learner's filter leaves out is counted apart
    for card in cards:
        cid, kind, script = card["content_id"], card["skill"], card["script"]
        control = controls.get(cid, {})
        if kind not in kinds or identities[cid, kind, script] in occupied or control.get("suspended"):
            continue
        if not allows(serve, kana_member(cid, script)):
            continue
        due = datetime.fromisoformat(card["due_at_utc"])
        due = max(due, feedback_due.get((cid, kind, script), due))
        if control.get("buried_until_utc"):
            due = max(due, datetime.fromisoformat(control["buried_until_utc"]))
        grouped.setdefault((kind, script), []).append(cid)
        due_by_key[cid, kind, script] = due
    advancing = paths().kana_advancement_targets(kinds, now=now, pending=pending)
    advancement_due = store.feedback_wait_times(
        ((entry["content_id"], kind, entry["script"]) for entry, kind, _ in advancing), now)
    for entry, kind, _ in advancing:
        cid, script = entry["content_id"], entry["script"]
        if not allows(serve, kana_member(cid, script)):
            continue
        grouped.setdefault((kind, script), []).append(cid)
        due_by_key[cid, kind, script] = advancement_due.get((cid, kind, script), now)
    # Group by direction to avoid rebuilding the whole eligible catalogue
    # once for every due card. Target-only previews create no choices.
    ready, future, by_ability = context_count, [], {}
    count_identities = prospective_identities(store, due_by_key) if due_by_key else {}
    for (kind, script), content_ids in grouped.items():
        targets = build_questions(repository(), _BatchQuestionStore(store, content_ids),
            kind=kind, script=script, limit=len(content_ids), seed=0, now=now,
            include_extended=True, _targets_only=True)
        for target in targets:
            due = due_by_key[target.content_id, kind, script]
            key = count_identities[target.content_id, kind, script]
            by_ability[key] = min(by_ability.get(key, due), due)
    ready += sum(due <= now for due in by_ability.values())
    future.extend(due for due in by_ability.values() if due > now)
    # Only future instants arm the home timer. Keeping an already-due
    # instant here would cause a refresh loop until the learner answered.
    return {"ready": 0 if limited else ready, "waiting": len(future),
            "next_due": min(future).isoformat() if future else None}
