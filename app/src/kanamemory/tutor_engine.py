"""Deterministic short-session tutoring around the unchanged long-term schedule.

The language model receives a small legal action space. Local policy owns
eligibility, immutable questions, repetition limits and the exposure guard.
Priorities are transparent heuristics, not a calibrated mastery probability.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from . import app_clock
from hashlib import sha256
import json

from .ai_questions import build_learning_scope, learning_scope_items
from .learning_questions import KIND_ABILITIES, TEXT_KINDS, build_learning_question
from .material_review import reviewed_question
from .learning_dependencies import independent_ready_ids, prerequisite_status
from .learning_harness import (rank_targets, selection_mode, decide_next_step,
                               conditional_patterns, conditional_priority)
from .tutor_store import (COOLDOWN_SECONDS, question_available, question_fingerprint,
                          validate_tutor_value)

MAX_TARGETS = 24
MAX_CANDIDATES = 8


def local_question(item, kind, variant=0):
    """Copy a reviewed local task without turning this drill into an FSRS event."""
    question = reviewed_question(build_learning_question(item, kind, variant=variant))
    question.update(tutor_practice=True, evidence_mode="exposure_only", scheduled=False)
    return question


def _recent(state, store=None):
    rows = []
    for entry in state["entries"][-12:]:
        result = entry["result"]
        if result is None:
            continue
        q = entry["question"]
        if store is not None:
            if not store.queries.contribution_valid('tutor:' + entry['id'], q['target_id'], q.get('ability', '')):
                continue
        rows.append({"target_id": q["target_id"], "kind": q["kind"],
                     "outcome": result["outcome"], "correct": result["correct"],
                     "assisted": result["assisted"], "error_category": result["error_category"],
                     "answered_text": result["answered_text"][:500], "answer": result["answer"][:500],
                     "source": entry["source"], "coached": entry.get("coached", False)})
    return rows


def _evidence_context(learning_store, target_ids, now):
    from .learning_evidence import LearningEvidenceStore
    return LearningEvidenceStore(learning_store.store).context(target_ids, now=now, limit=MAX_TARGETS)


def _course_state(learning_store, enabled):
    """Route back to the actual course without manufacturing a tutor queue."""
    teaching = learning_store.store.queries.first_unfinished_course()
    pending = None
    if enabled:
        pending = learning_store.store.queries.first_pending_course(enabled)
    count = learning_store.store.queries.course_count()
    return {"enrolled_count": count, "instruction_target_id": teaching[0] if teaching else "",
            "pending_target_id": pending[0] if pending else ""}


def _kind_order(target, recent):
    kinds = list(target["allowed_kinds"])
    relevant = [row for row in recent if row["target_id"] == target["id"]]
    last = relevant[-1] if relevant else None
    needs_support = bool(last and (last["outcome"] in {"incorrect", "revealed"} or last["assisted"]))
    prefer_text = not needs_support and (target["familiarity"] >= .35
                    or sum(row["correct"] is True and not row["assisted"] for row in relevant) >= 2)
    count = {kind: sum(row["kind"] == kind for row in relevant) for kind in kinds}
    return sorted(kinds, key=lambda kind: (int((kind in TEXT_KINDS) != prefer_text),
                                          count[kind], kinds.index(kind)))


def _target_candidate(item, target, recent, state, now, patterns, *, system_japanese=False):
    from .learning_conditions import candidate_context
    from . import semantic_errors
    from .tutor_store import question_seen_recently
    best, priority = None, 0.
    ceiling = max((row["concern"] for row in patterns), default=0.)
    for kind in _kind_order(target, recent):
        # Once a fallback exists, unrelated directions cannot improve an exact
        # condition match. Avoid hydrating every template on ordinary turns.
        matching = [row for row in patterns if row["kind"] == kind or
                    row.get('semantic') and row['ability'] == KIND_ABILITIES[kind]]
        if best is not None and not matching:
            continue
        variants = item.get("question_templates", {}).get(kind)
        for variant in range(min(len(variants) if variants else 4, 30)):
            try:
                question = local_question(item, kind, variant)
            except (ValueError, KeyError, TypeError):
                continue
            from .answer_input import available
            if not available(question, system_japanese=system_japanese):
                continue
            if question_seen_recently(question, state, now=now):
                continue
            semantic = [p for p in matching if p.get('semantic')]
            descriptor = semantic_errors.for_question(question) if semantic else None
            score = (semantic_errors.priority(descriptor, semantic) if semantic else
                     conditional_priority(candidate_context(question), matching) if matching else 0.)
            # Prefer a different sentence in the same reviewed error condition.
            # This small tie-breaker stays inside the existing targeted slot.
            if score and descriptor and all(descriptor['source_key'] != p['examples'][-1]['source_key'] for p in semantic if p['examples']):
                score += .000001
            if best is None or score > priority:
                fingerprint = question_fingerprint(question)
                best = {"candidate_id": "local:" + fingerprint[:24], "target_id": target["id"],
                        "kind": kind, "question": question}
                priority = score
            if not patterns or priority > ceiling or priority >= ceiling and not semantic:
                return best, priority
            if not matching:
                break
    return best, priority


def prepare_tutor_context(learning_store, enabled_kinds, state, *, now=None):
    """Return legal candidates without touching old queues, drafts or schedules.

    ``context_fingerprint`` deliberately excludes request state, UI drafts,
    messages and the wall clock. A normal in-flight request can therefore be
    checked again. It includes actual legal candidates and material identities.
    """
    state = validate_tutor_value(state)
    now = now or app_clock.utc_now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("辅导时钟缺少时区。")
    enabled = [kind for kind in KIND_ABILITIES if kind in enabled_kinds]
    items = {item["id"]: item for item in learning_scope_items(learning_store)}
    scope = build_learning_scope(learning_store, enabled, limit=None, now=now,
                                 supported_kinds=tuple(KIND_ABILITIES), _items=items.values())
    # A historical assessment alone must not bypass a currently blocked
    # prerequisite chain, suspension, or a reset to first teaching.
    ready = independent_ready_ids(learning_store)
    targets = [target for target in scope["targets"]
               if not prerequisite_status(learning_store, items[target["id"]], ready_ids=ready)["blocked"]]
    recent = _recent(state, learning_store.store)
    # The evidence query is bounded; the pure ranking policy can still select
    # an ordinary or error-independent target outside this small evidence pool.
    evidence = _evidence_context(learning_store, [row["id"] for row in targets[:MAX_TARGETS]], now)
    targets = rank_targets(targets, recent, evidence, completed_count=state["completed_count"])
    active = next((row for row in state["entries"] if row["status"] == "pending"), None)
    if active:
        targets.sort(key=lambda target: target["id"] != active["question"]["target_id"])
    cooldowns = {cid: datetime.fromisoformat(stamp) + timedelta(seconds=COOLDOWN_SECONDS)
                 for cid, stamp in state["exposures"].items()}
    waits = [until for cid, until in cooldowns.items() if until > now and any(t["id"] == cid for t in targets)]
    scope["targets"] = (targets if active else [target for target in targets
        if cooldowns.get(target["id"], now) <= now])[:MAX_TARGETS]
    scope["background"] = scope["background"][:MAX_TARGETS]
    candidates = []
    if active:
        decision = "resume" if question_available(active["question"], scope, enabled) else "disabled"
    else:
        decision = "empty"
    if active is None:
        targeted = selection_mode(state["completed_count"]) == "targeted"
        priorities = {}
        for target in scope["targets"]:
            item = items[target["id"]]
            patterns = conditional_patterns(target["id"], item["revision"], evidence) if targeted else []
            from .answer_input import system_japanese_enabled
            candidate, priority = _target_candidate(item, target, recent, state, now, patterns,
                system_japanese=system_japanese_enabled(learning_store.store))
            if candidate:
                candidates.append(candidate)
                priorities[target["id"]] = priority
            if len(candidates) >= MAX_CANDIDATES:
                break
        if targeted:
            last = recent[-1]["target_id"] if recent else None
            # Keep target recency protection; exact usable contexts win within
            # this slot. Each target still contributes at most one candidate.
            candidates.sort(key=lambda c: (c["target_id"] == last, -priorities[c["target_id"]]))
        decision = "local" if candidates else "wait" if waits else "empty"
    if selection_mode(state["completed_count"]) == "independent_check":
        # A model may not turn a reserved independent check into another drill
        # of its favorite historical error. The selected local task is fixed.
        candidates = candidates[:1]
    target_id = (active["question"]["target_id"] if active else
                 candidates[0]["target_id"] if candidates else
                 scope["targets"][0]["id"] if scope["targets"] else None)
    target = next((row for row in scope["targets"] if row["id"] == target_id), None)
    result = {"scope": scope, "recent": recent, "candidates": candidates,
              "question": deepcopy(active["question"]) if active else None,
              "target": deepcopy(target), "decision": decision,
              "wait_until": min(waits).isoformat() if waits else None,
              "learning_evidence": evidence}
    result["harness"] = decide_next_step(result, state, _course_state(learning_store, enabled))
    # Raw dates/scheduler state stay local; only stable coarse evidence travels.
    fingerprint = {"scope": scope, "recent": recent,
                   "candidates": [{"candidate_id": c["candidate_id"], "question": c["question"]} for c in candidates],
                   "active": active, "decision": decision, "seen": state["seen"],
                   "harness": result["harness"], "evidence_revision": evidence["revision"]}
    result["context_fingerprint"] = sha256(json.dumps(fingerprint, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return result


def local_next(context):
    candidates = context.get("candidates", [])
    return deepcopy(candidates[0]["question"]) if candidates else None
