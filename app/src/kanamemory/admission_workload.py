"""Read-only enrollment costs, expressed as content and memory directions.

These are current records, not a forecast of future repetitions or minutes.
Course question families share an ability; legacy kana retain skill × script.
"""
from __future__ import annotations

from datetime import datetime
import json

from .domain import (WORD_SKILLS, LISTENING_SKILLS, HANDWRITING_SKILLS,
                     CONTENT_CONTROLS_SETTING, _BatchQuestionStore, build_questions)
from .learning_content import get_learning_item
from .learning_questions import KIND_ABILITIES, question_kinds


def _entries(overview):
    return {entry["id"]: entry for stage in overview.get("stages", [])
            for unit in stage.get("units", []) for section in unit.get("sections", [])
            for entry in section.get("entries", [])}


def _kana_settings(app, pending):
    from .practice_preferences import enabled_preference_kinds
    kinds = (app._queue_kinds(pending) if pending else
             enabled_preference_kinds(getattr(app, "_practice_preferences", {})))
    return {kind for kind in kinds if kind not in WORD_SKILLS
            and (getattr(app.platform, "tts_ready", False) or kind not in LISTENING_SKILLS)
            and (getattr(app, "model_state", None) == "ready" or kind not in HANDWRITING_SKILLS)}


def admission_snapshot(app, plan, *, overview=None, now=None, read=None):
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
        now, service = _utc(now), app.study_path_service()
        pending = app.store.load_session_progress(lane="main", lazy_questions=True)
    repo = getattr(service, "repo", app.repo)
    entries = _entries(overview if overview is not None else
                       read.overview() if read is not None else service.overview())
    enabled = set(app.learning_preferences()["enabled"])
    kana_kinds = _kana_settings(app, pending)
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
    for (kind, script), content_ids in kana_targets.items():
        candidates = build_questions(repo, _BatchQuestionStore(app.store, sorted(content_ids)),
            kind=kind, script=script, limit=len(content_ids), seed=0, now=now,
            include_extended=True, _targets_only=True)
        kana_directions.update((q.content_id, kind, script) for q in candidates)
    if kana_directions:
        new_directions.update(prospective_identities(app.store, kana_directions).values())
    result["new_direction_count"] = len(new_directions)

    # First teaching is a content-level task, separate from scheduled reviews.
    introduction_ids = {("learning", cid) for cid in app.store.queries.unfinished_course_ids()}
    introduction_ids.update(("kana", entry["content_id"], entry["script"])
                            for entry in entries.values() if entry["kind"] == "kana"
                            and entry.get("state") in {"added", "started"})
    result["pending_intro_count"] = len(introduction_ids)

    _course_reviews(app, enabled, now, result)
    _kana_reviews(app, repo, pending, kana_kinds, now, result)
    return result


def _course_reviews(app, enabled, now, result):
    learning = app.learning_store
    units = {row["unit_id"]: dict(row) for row in app.store.queries.active_review_units()}
    if not units or not enabled:
        return
    dues, pending_ids = {}, set()
    exposures = learning.content_exposure_times(now=now)
    for row in learning.iter_pending_questions():
        pending_ids.add(row["unit_id"])
        if row["kind"] not in enabled or row["unit_id"] not in units:
            continue
        q = json.loads(row["snapshot_json"])
        due = learning.effective_question_due(row, q, now=now, exposure_times=exposures)
        if row["result_json"] is not None:
            due = now.isoformat()  # Ungraded feedback can still be continued.
        if row["unit_id"] not in dues or (due or "") < (dues[row["unit_id"]] or ""):
            dues[row["unit_id"]] = due
    missing_contents = {unit["content_id"] for identity, unit in units.items() if identity not in pending_ids}
    from .course_planning import iter_question_plans
    from .answer_input import system_japanese_enabled
    for cid in missing_contents:
        for unit, q in iter_question_plans(learning.get_item(cid), enabled_kinds=enabled,
                queries=app.store.queries, learning_store=learning,
                system_japanese_input=system_japanese_enabled(app.store)):
            if unit and unit["unit_id"] in units:
                # The append planner also includes per-attempt retry and late
                # feedback delays; use exactly that due for missing snapshots.
                dues[unit["unit_id"]] = learning.question_not_before(unit, q, now=now, exposure_times=exposures)
    for due in dues.values():
        result["waiting_unit_count" if due and datetime.fromisoformat(due) > now else "ready_unit_count"] += 1


def _kana_reviews(app, repo, pending, kinds, now, result):
    if not kinds:
        return
    controls = app.store.get_setting(CONTENT_CONTROLS_SETTING, {})
    from .kana_routing import candidate_cards, current_identities
    cards = [row for row in candidate_cards(app.store, kinds, now)
        if row["skill"] in kinds and not controls.get(row["content_id"], {}).get("suspended")]
    if not cards:
        return
    grouped, due_by_key = {}, {}
    feedback = app.store.feedback_wait_times(((c["content_id"], c["skill"], c["script"]) for c in cards), now)
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
    identities = current_identities(app.store, set(due_by_key) | {(q.content_id, q.kind, q.script) for q in queued})
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
    for (kind, script), ids in grouped.items():
        candidates = build_questions(repo, _BatchQuestionStore(app.store, ids), kind=kind,
            script=script, limit=len(ids), seed=0, now=now, include_extended=True, _targets_only=True)
        for q in candidates:
            key = identities[q.content_id, kind, script]
            due = due_by_key[q.content_id, kind, script]
            by_ability[key] = min(by_ability.get(key, due), due)
    for due in by_ability.values():
        result["waiting_unit_count" if due > now else "ready_unit_count"] += 1


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
