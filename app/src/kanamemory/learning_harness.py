"""Pure teaching decisions around existing queues and memory records.

This policy has no database, model, clock or UI side effects. Its scores are
bounded selection heuristics, not a second mastery estimate. The persisted
tutor completion count makes coverage slots survive exits and restarts.
"""
from __future__ import annotations

from hashlib import sha256
import math

POLICY = "teaching-harness-v2"
VERSION = 2
MODES = ("progress", "targeted", "progress", "independent_check")


def selection_mode(completed_count):
    if type(completed_count) is not int or completed_count < 0:
        raise ValueError("Invalid completed learning count")
    return MODES[completed_count % len(MODES)]


def _number(value, fallback=0):
    return value if type(value) in {int, float} and math.isfinite(value) else fallback


def _independent(rows):
    return [row for row in rows if row.get("source") == "local"
            and not row.get("assisted") and row.get("outcome") in {"correct", "incorrect"}
            and type(row.get("correct")) is bool]


def _concern(target_id, recent, evidence):
    stored = next((row for row in evidence.get("targets", ())
                   if row.get("target_id") == target_id), None)
    if stored and _number(stored.get("opportunities")) > 0:
        return max(0, min(1, _number(stored.get("concern"))))
    valid = _independent([row for row in recent if row.get("target_id") == target_id])
    errors = sum(row["outcome"] == "incorrect" for row in valid)
    # A small denominator prior avoids treating one failure as a stable trait.
    # Each further independent success decreases this extra error emphasis.
    return errors / (len(valid) + 3)


def rank_targets(targets, recent=(), evidence=None, *, completed_count=0):
    """Keep coverage and error-independent checks alongside targeted practice.

    ``targets`` must already obey current settings and prerequisites. Its input
    order is the existing scheduler's due/familiarity order, used as a stable
    tie break. This function never unlocks or enrolls content.
    """
    mode = selection_mode(completed_count)
    evidence = evidence or {}
    recent = list(recent)[-12:]
    count = {}
    for row in recent:
        cid = row.get("target_id")
        count[cid] = count.get(cid, 0) + 1
    last = recent[-1].get("target_id") if recent else None
    indexed = list(enumerate(targets))
    if mode == "independent_check":
        cycle = completed_count // len(MODES)
        # Stable pseudo-random selection independent of errors and familiarity.
        # Last-target avoidance still protects against immediate repetitions.
        def key(pair):
            _, target = pair
            cid = target["id"]
            lottery = sha256(f"{POLICY}:{cycle}:{cid}".encode()).digest()
            return cid == last, lottery, cid
    elif mode == "targeted":
        def key(pair):
            index, target = pair
            cid = target["id"]
            return cid == last, -_concern(cid, recent, evidence), count.get(cid, 0), index
    else:
        def key(pair):
            index, target = pair
            cid = target["id"]
            return cid == last, count.get(cid, 0), index
    return [target for _, target in sorted(indexed, key=key)]


def decision_trace(*, action, reason, mode, target_id="", candidate_id="",
                   source="none", evidence_revision=0):
    return {"policy": POLICY, "version": VERSION, "action": action,
            "reason": reason, "mode": mode, "target_id": target_id or "",
            "candidate_id": candidate_id or "", "source": source,
            "evidence_revision": evidence_revision}


def conditional_patterns(target_id, revision, evidence):
    """Reuse current counterevidence, without interpreting silence as mastery.

    Mixed/unknown material versions and empty contexts cannot diagnose a
    particular co-occurrence. The existing projection owns success decay,
    recency and retraction; selection does not update any weight itself.
    """
    target = next((row for row in evidence.get("targets", ())
                   if row.get("target_id") == target_id), {})
    semantic = target.get('semantic_patterns', [])
    semantic_abilities = {row['ability'] for row in semantic}
    return [row for row in semantic if row['errors'] > 0 and row['concern'] > 0] + [row for row in target.get("patterns", ())
            if row.get('ability') not in semantic_abilities and row.get("content_revision") == str(revision)
            and row.get("condition_revision")
            and row.get("conditions") and _number(row.get("opportunities")) > 0
            and _number(row.get("errors")) > 0 and _number(row.get("concern")) > 0]


def conditional_priority(candidate, patterns):
    """Exact set, ability and question-direction match, not token overlap.

    These are associations in reviewed examples, not proven causes of error.
    The caller uses this only in the existing targeted slot (one in four).
    """
    if not candidate or candidate.get("version") != 1 or not candidate.get("condition_revision"):
        return 0.
    return max((max(0., min(1., _number(row.get("concern")))) for row in patterns
                if row.get("content_revision") == candidate.get("revision")
                and row.get("condition_revision") == candidate.get("condition_revision")
                and row.get("ability") == candidate.get("ability")
                and row.get("kind") == candidate.get("kind")
                and sorted(row.get("conditions", ())) == candidate.get("conditions")), default=0.)


def decide_next_step(context, state, course_state=None):
    """Describe a next step without moving a queue or submitting an answer.

    The UI executes the decision through the established store APIs. Question
    snapshots are never edited here. Questions to the AI are a separate path.
    """
    course = course_state or {}
    mode = selection_mode(state.get("completed_count", 0))
    revision = context.get("learning_evidence", {}).get("revision", 0)
    if type(revision) is not int or revision < 0:
        revision = 0

    def decision(action, reason, *, target_id="", candidate_id="", source="none"):
        return decision_trace(action=action, reason=reason, mode=mode,
                              target_id=target_id, candidate_id=candidate_id,
                              source=source, evidence_revision=revision)

    active = next((row for row in state.get("entries", ()) if row.get("status") == "pending"), None)
    if active is not None:
        target = active["question"]["target_id"]
        if context.get("decision") == "disabled":
            return decision("wait", "current_settings_or_prerequisites", target_id=target, source="saved")
        if active.get("result") is not None:
            return decision("feedback", "saved_result", target_id=target, source="saved")
        return decision("resume", "saved_question_and_draft", target_id=target, source="saved")

    # Only the latest completed task may trigger one automatic explanation.
    # Device failure and unverified model grading cannot diagnose forgetting.
    latest = next((row for row in reversed(state.get("entries", ()))
                   if row.get("status") == "completed" and row.get("result") is not None), None)
    if latest and not latest.get("coached") and latest.get("source") == "local":
        result = latest["result"]
        if result.get("outcome") in {"incorrect", "revealed"}:
            return decision("explain", "recent_answer_needs_one_explanation",
                            target_id=latest["question"]["target_id"], source="local")

    candidates = context.get("candidates", ())
    if candidates:
        selected = candidates[0]
        return decision("practice", {"progress": "ordinary_coverage",
                        "targeted": "bounded_error_followup",
                        "independent_check": "error_independent_check"}[mode],
                        target_id=selected["target_id"], candidate_id=selected["candidate_id"], source="local")
    if context.get("decision") == "wait":
        return decision("wait", "answer_exposure_cooldown")
    if course.get("pending_target_id"):
        return decision("continue_course", "saved_course_queue",
                        target_id=course["pending_target_id"], source="course")
    if course.get("instruction_target_id"):
        return decision("instruction", "first_learning_before_independent_practice",
                        target_id=course["instruction_target_id"], source="course")
    if not course.get("enrolled_count", 0) and not context.get("scope", {}).get("targets"):
        return decision("instruction", "choose_first_learning_section", source="course")
    return decision("finish", "no_eligible_local_practice")
