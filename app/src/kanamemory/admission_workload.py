"""Read-only enrollment costs, expressed as content and memory directions.

These are current records, not a forecast of future repetitions or minutes.
Course question families share an ability; legacy kana retain skill × script.

The existing work (lessons waiting for their first teaching, and when each
review direction is next due) does not depend on the selection. Reading it
plans a question for every enrolled lesson that has a direction without a
queued question, which took about a second per hundred such lessons on a
desktop and many times that on a phone, on opening the page, on every tick of
a box and again on submit. It is therefore read once per unchanged learner
database (_existing_work, kept by admission.costs.AdmissionCosts) and kept for
a short time; each call still counts ready and waiting directions against its
own ``now``.

The functions take a WorkloadContext, never the app object (design 13 S2):
the stores, the per-store caches, and callables for what is read again at
each call (the path service, the content repository, the device abilities and
settings).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from types import MappingProxyType
from typing import Any

from .domain import (WORD_SKILLS, LISTENING_SKILLS, HANDWRITING_SKILLS,
                     CONTENT_CONTROLS_SETTING, _BatchQuestionStore, build_questions)
from .learning_content import get_learning_item
from .learning_questions import KIND_ABILITIES, question_kinds


@dataclass(frozen=True)
class WorkloadContext:
    """What the workload line reads, for one learner store; built by admission.assembly.StoreAdmission.

    ``paths``: the current StudyPathService (asked only without a read
    context); ``repository``: the content repository (asked only when the path
    service has none); ``inputs(pending)``: an admission.model.AddInputs for
    the main lane's pending checkpoint, read at every call.
    """

    store: Any
    learning_store: Any
    costs: Any                          # admission.costs.AdmissionCosts of this store
    paths: Callable[[], Any]
    repository: Callable[[], Any]
    inputs: Callable[[Any], Any]


def _entries(overview):
    return {entry["id"]: entry for stage in overview.get("stages", [])
            for unit in stage.get("units", []) for section in unit.get("sections", [])
            for entry in section.get("entries", [])}


def _kana_settings(inputs):
    """The kana kinds of the main lane that count here: no word skill, and only what this device can serve."""
    return {kind for kind in inputs.kana_kinds if kind not in WORD_SKILLS
            and (inputs.speech_ready or kind not in LISTENING_SKILLS)
            and (inputs.handwriting_ready or kind not in HANDWRITING_SKILLS)}


def admission_snapshot(ctx, plan, *, overview=None, now=None, read=None):
    """Describe one exact dependency-expanded selection without creating cards.

    ``new_direction_count`` is the set of supported directions under current
    settings, not the number of questions immediately presented. Existing
    reviewed units are counted once, regardless of how many snapshots remain.
    Disabled directions keep their schedules but are absent from this preview.
    """
    from .domain import _utc
    if read is not None:
        read.check_active()
        now, service, pending = _utc(now) if now is not None else read.now, read.service, read.pending
    else:
        now, service = _utc(now), ctx.paths()
        pending = ctx.store.load_session_progress(lane="main", lazy_questions=True)
    repo = service.repo if hasattr(service, "repo") else ctx.repository()
    entries = _entries(overview if overview is not None else
                       read.overview() if read is not None else service.overview())
    inputs = ctx.inputs(pending)
    enabled = set(inputs.course_kinds)
    kana_kinds = _kana_settings(inputs)
    targets = set(plan["entry_ids"])
    result = dict(new_item_count=len(targets), new_direction_count=0,
                  new_reference_count=0, pending_intro_count=0,
                  ready_unit_count=0, waiting_unit_count=0)
    new_directions, kana_targets = set(), {}
    from .kana_routing import prospective_identities
    kana_directions = set()
    for identity in targets:
        entry = entries[identity]
        cid = entry["content_id"]
        if entry["kind"] == "reference":
            result["new_reference_count"] += 1
        elif entry["kind"] == "kana":
            for kind in kana_kinds:
                kana_targets.setdefault((kind, entry["script"]), set()).add(cid)
        else:
            item = getattr(service, "items", {}).get(cid) or get_learning_item(cid)
            if item:
                new_directions.update((cid, KIND_ABILITIES[kind]) for kind in question_kinds(item)
                                      if kind in enabled and KIND_ABILITIES[kind] in item["abilities"])
    targets = _KanaTargets(repo, ctx.store, now)
    for (kind, script), content_ids in kana_targets.items():
        candidates = targets(sorted(content_ids), kind, script)
        kana_directions.update((q.content_id, kind, script) for q in candidates)
    if kana_directions:
        new_directions.update(prospective_identities(ctx.store, kana_directions).values())
    result["new_direction_count"] = len(new_directions)

    existing = _existing_work(ctx, repo, pending, entries, enabled, kana_kinds, now, read)
    result["pending_intro_count"] = existing["pending_intro_count"]
    _count(existing["dues"], now, result)
    return result


_EXISTING_SECONDS = 120  # how long one reading of the existing work serves an unchanged learner database


def _existing_work(ctx, repo, pending, entries, enabled, kana_kinds, now, read):
    """{'pending_intro_count', 'dues'}: what is already enrolled, independent of the selection.

    ``dues`` holds one entry per existing review direction: the earliest time
    its next question may be asked (None: now). Kept by ctx.costs for
    _EXISTING_SECONDS while the database, the enabled kinds and the
    availability are unchanged; a write of any kind reads it again. The
    saved times are absolute, so a later call inside that window still sorts
    them against its own ``now``.
    """
    # First teaching is a content-level task, separate from scheduled reviews.
    # Content of a disabled/missing library is neither taught nor reviewed
    # (design 08 section 8.2); the same AvailabilityView filters both.
    unavailable = ctx.learning_store.availability().unavailable_ids
    enrolled_kana = frozenset((entry["content_id"], entry["script"]) for entry in entries.values()
                              if entry["kind"] == "kana" and entry.get("state") in {"added", "started"})

    def reading():
        # 待初学 (design 12 N14): lessons whose first teaching has not been shown.
        introduction_ids = {("learning", cid) for cid in ctx.store.queries.untaught_course_ids(
            read.course_rows if read is not None else None) if cid not in unavailable}
        introduction_ids.update(("kana", entry["content_id"], entry["script"])
                                for entry in entries.values() if entry["kind"] == "kana"
                                and entry.get("state") in {"added", "started"})
        # Kept for _EXISTING_SECONDS and counted against a ``now`` up to that much later: a unit whose
        # plan-free lower bound lies beyond that window is waiting at every such ``now`` (_course_dues).
        window = timedelta(seconds=_EXISTING_SECONDS)
        dues = [*_course_dues(ctx, enabled, now, unavailable, window),
                *_kana_dues(ctx, repo, pending, kana_kinds, now)]
        return {"pending_intro_count": len(introduction_ids), "dues": dues}
    return ctx.costs.existing_work((frozenset(enabled), frozenset(kana_kinds), unavailable, enrolled_kana),
                                   now, reading, seconds=_EXISTING_SECONDS)


def _count(dues, now, result):
    for due in dues:
        result["waiting_unit_count" if due is not None and due > now else "ready_unit_count"] += 1


def _course_reviews(ctx, enabled, now, result, unavailable=frozenset()):
    """Count the course review directions into ``result`` (one uncached reading)."""
    _count(_course_dues(ctx, enabled, now, unavailable), now, result)


def _kana_reviews(ctx, repo, pending, kinds, now, result):
    """Count the kana review directions into ``result`` (one uncached reading)."""
    _count(_kana_dues(ctx, repo, pending, kinds, now), now, result)


def _course_dues(ctx, enabled, now, unavailable=frozenset(), window=None):
    """The earliest time (None: now) of every enabled course review direction.

    ``window`` (a timedelta; None: every time is exact): the dues are counted against a ``now`` up to
    ``window`` after this one only, so a lesson without a question that is not planned yet is not planned
    when each of its units that gets a question has a plan-free lower bound later than now + window
    (_bounded_dues): such a unit stands at its bound, waiting at every such ``now`` as its planned time is.
    """
    learning = ctx.learning_store
    units = {row["unit_id"]: dict(row) for row in ctx.store.queries.active_review_units(exclude=unavailable)}
    if not units or not enabled:
        return []
    dues, pending_ids = {}, set()
    exposures = learning.content_exposure_times(now=now)
    for row in learning.iter_pending_questions():
        pending_ids.add(row["unit_id"])
        if row["kind"] not in enabled or row["unit_id"] not in units:
            continue
        q = json.loads(row["snapshot_json"])
        due = learning.effective_question_due(row, q, now=now, exposure_times=exposures)
        if row["result_json"] is not None:
            due = None  # Ungraded feedback can still be continued: ready at any time.
        if row["unit_id"] not in dues or (due or "") < (dues[row["unit_id"]] or ""):
            dues[row["unit_id"]] = due
    missing_contents = {unit["content_id"] for identity, unit in units.items() if identity not in pending_ids}
    from .course_planning import PassFacts, kept_question_plans, preview_question_plans
    from .answer_input import system_japanese_enabled
    plans, system_input = ctx.costs.plan_preview(), system_japanese_enabled(ctx.store)
    facts = PassFacts(ctx.store.queries)   # read once for these lessons
    limit = now + window if window is not None and PLAN_FREE_DUES else None
    for cid in missing_contents:
        # Home has usually planned these lessons a moment ago, from the same records.
        kept = kept_question_plans(plans, cid, enabled_kinds=enabled, system_japanese_input=system_input,
                                   unavailable_ids=unavailable)
        if kept is None and limit is not None:
            bounds = _bounded_dues(ctx, cid, units, enabled, system_input, unavailable, facts, now, exposures, limit)
            if bounds is not None:
                dues.update(bounds)
                continue
        for unit, q in kept if kept is not None else preview_question_plans(
                plans, cid, lambda cid=cid: learning.get_item(cid),
                enabled_kinds=enabled, queries=ctx.store.queries, learning_store=learning,
                system_japanese_input=system_input, unavailable_ids=unavailable, facts=facts):
            if unit and unit["unit_id"] in units:
                # The append planner also includes per-attempt retry and late
                # feedback delays; read as a queued question is (design 12 N10).
                dues[unit["unit_id"]] = learning.planned_question_due(unit, q, now=now, exposure_times=exposures)
    return [datetime.fromisoformat(due) if due else None for due in dues.values()]


# planned_question_due of a unit without a plan: a question that is no free retrieval (_ability_due keeps the due).
_NO_PLAN = MappingProxyType({"input_mode": "choice"})
# _course_dues with a window: the plan-free lower bounds of the lessons not planned yet; False plans every
# lesson (the reference the equivalence test compares with).
PLAN_FREE_DUES = True
# The kana directions' eligibility from one catalogue read (domain.KanaCardPass.targets); False runs one
# target-only build_questions per direction (the reference the equivalence test compares with).
KANA_TARGET_PASS = True


def _bounded_dues(ctx, cid, units, enabled, system_input, unavailable, facts, now, exposures, limit):
    """{unit id: a lower bound of its planned due} of the lesson's units that get a question, when every bound
    is later than ``limit``; None: plan the lesson.

    Which units of the lesson get a question is course_planning.plannable_abilities (no plan is made). The
    planned due of a unit's question depends on the plan only through _ability_due: a plan that is no free
    retrieval is due exactly as _NO_PLAN is; a free retrieval's due is that with min(due, probe due) as its
    schedule, which is never earlier than min(the due of _NO_PLAN, the probe due) (the floors, exposure waits
    and feedback cooldowns are the same or later, and the earlier schedules, an active confirmation and the
    serviceable reading, are a min with the schedule). So the lower bound is min(_NO_PLAN's due, probe due).
    """
    from .course_planning import plannable_abilities
    learning = ctx.learning_store
    item = learning.get_item(cid)
    if item is None:
        return None
    wanted = {unit["ability"] for unit in units.values() if unit["content_id"] == cid}
    states = plannable_abilities(item, enabled_kinds=enabled, queries=ctx.store.queries, learning_store=learning,
                                 system_japanese_input=system_input, abilities=wanted,
                                 unavailable_ids=unavailable, facts=facts)
    if states is None:
        return None
    found = {}
    for ability, (state, kinds) in states.items():
        if state["unit_id"] not in units:
            continue
        lower = learning.planned_question_due(state, _NO_PLAN, now=now, exposure_times=exposures)
        probe = state["probe_due_at_utc"] if _may_be_free(item, ability, kinds) else None
        if lower and probe:
            lower = min((lower, probe), key=datetime.fromisoformat)
        if not lower or datetime.fromisoformat(lower) <= limit:
            return None
        found[state["unit_id"]] = lower
    return found


def _may_be_free(item, ability, kinds):
    """Whether the plan of ``ability`` chosen from ``kinds`` can be a free retrieval (its probe due then counts).

    The plan is built from one of the lesson's templates of those kinds (course_planning.iter_question_plans:
    its input mode, prompt and practice); a generated sound kind takes its templates from sound_variants, a
    non-dict entry from the legacy builders: either may be. False only when no template of those kinds is.
    """
    from .learning_questions import SOUND_KINDS, practice_templates
    from .learning_store import free_retrieval
    table = practice_templates(item)
    if not isinstance(table, dict):
        return True
    for kind in kinds:
        if kind in SOUND_KINDS:
            return True
        for variant, raw in enumerate(table.get(kind) or ()):
            if not isinstance(raw, dict):
                return True
            plan = {"target_id": item["id"], "ability": ability, "kind": kind, "variant": variant,
                    "input_mode": raw.get("input_mode", "choice")}
            if "practice" in raw:
                plan.update(prompt=raw.get("prompt", ""), practice=raw["practice"])
            if free_retrieval(plan):
                return True
    return False


def _kana_dues(ctx, repo, pending, kinds, now):
    """The earliest time of every enabled kana review direction."""
    if not kinds:
        return []
    controls = ctx.store.get_setting(CONTENT_CONTROLS_SETTING, {})
    from .kana_routing import candidate_cards, current_identities
    cards = [row for row in candidate_cards(ctx.store, kinds, now)
        if row["skill"] in kinds and not controls.get(row["content_id"], {}).get("suspended")]
    if not cards:
        return []
    grouped, due_by_key = {}, {}
    feedback = ctx.store.feedback_wait_times(((c["content_id"], c["skill"], c["script"]) for c in cards), now)
    for card in cards:
        cid, kind, script = card["content_id"], card["skill"], card["script"]
        key = (cid, kind, script)
        due = datetime.fromisoformat(card["due_at_utc"])
        due = max(due, feedback.get(key, due))
        buried = controls.get(cid, {}).get("buried_until_utc")
        if buried:
            due = max(due, datetime.fromisoformat(buried))
        due_by_key[key] = due
        grouped.setdefault((kind, script), []).append(cid)
    # Queue-only delays are not FSRS interval changes. Multiple questions for
    # the same legacy direction are still one memory unit in this overview.
    queue_due = {}
    queued = (pending or {}).get("questions", [])[(pending or {}).get("question_index", 0):]
    identities = current_identities(ctx.store, set(due_by_key) | {(q.content_id, q.kind, q.script) for q in queued})
    available_units = {identities[key] for key in due_by_key}
    for q in queued:
        key = identities[q.content_id, q.kind, q.script]
        if q.kind in kinds and key in available_units:
            value = q.metadata.get("not_before_utc")
            due = datetime.fromisoformat(value) if value else now
            queue_due[key] = min(queue_due.get(key, due), due)
    for key in due_by_key:
        due_by_key[key] = max(due_by_key[key], queue_due.get(identities[key], due_by_key[key]))
    by_ability = {}
    targets = _KanaTargets(repo, ctx.store, now)
    for (kind, script), ids in grouped.items():
        for q in targets(ids, kind, script):
            key = identities[q.content_id, kind, script]
            due = due_by_key[q.content_id, kind, script]
            by_ability[key] = min(by_ability.get(key, due), due)
    return list(by_ability.values())


class _KanaTargets:
    """build_questions(repo, _BatchQuestionStore(store, ids), kind=, script=, limit=len(ids), seed=0, now=,
    include_extended=True, _targets_only=True) of the directions of one read-only call: the targets of the
    given states its eligibility keeps (only their content ids are read here). With KANA_TARGET_PASS one
    domain.KanaCardPass reads the catalogue once for all of them."""

    def __init__(self, repo, store, now):
        self._repo, self._store, self._now = repo, store, now
        self._cards = None

    def __call__(self, ids, kind, script):
        if not KANA_TARGET_PASS:
            return build_questions(self._repo, _BatchQuestionStore(self._store, ids), kind=kind, script=script,
                                   limit=len(ids), seed=0, now=self._now, include_extended=True, _targets_only=True)
        if self._cards is None:
            from .domain import KanaCardPass
            self._cards = KanaCardPass(self._repo, self._store, seed=0, now=self._now)
        return self._cards.targets(ids, kind, script)


def admission_text(snapshot):
    """Two quiet lines; no universal quota, guessed study time or forecast."""
    lines = []
    if snapshot["new_item_count"]:
        lines.append(f"当前题型涉及 {snapshot['new_direction_count']} 个复习方向")
    existing = []
    for key, label, unit in (("pending_intro_count", "待初学", "项"),
                             ("ready_unit_count", "到期复习", "个方向"),
                             ("waiting_unit_count", "待到期", "个方向")):
        if snapshot[key]:
            existing.append(f"{label} {snapshot[key]} {unit}")
    if existing:
        lines.append("已有：" + " · ".join(existing))
    return "\n".join(lines)
