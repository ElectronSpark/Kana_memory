"""AI references to existing native heads; no copied cards, answers or writes."""
from dataclasses import asdict
from hashlib import sha256
import json

from .ai_question_capabilities import NATIVE


def native_route_candidates(store, allowed_for, *, audio_ready, handwriting_ready, now=None):
    from .practice_availability import summarize_queue_availability
    from .domain import HANDWRITING_SKILLS, LISTENING_SKILLS
    result, enabled = [], set()
    for lane in ("main", "word_romaji"):
        pending = store.load_session_progress(lane=lane, lazy_questions=True)
        if not pending:
            continue
        allowed = set(allowed_for(pending))
        enabled.update(allowed)
        index, questions = pending["question_index"], pending["questions"]
        if index >= len(questions):
            continue
        question = questions[index]
        if (question.kind not in allowed or question.kind not in NATIVE
                or question.kind in HANDWRITING_SKILLS and not handwriting_ready
                or question.kind in LISTENING_SKILLS and not audio_ready):
            continue
        # Keep the absolute head index and state, but inspect only this head.
        # No kind reconciliation/restoration, lane mutation or unseen reordering
        # may happen merely because a network request is being prepared.
        head = {**pending, "questions": questions[:index + 1]}
        if summarize_queue_availability(store, head, now=now).ready_count != 1:
            continue
        snapshot = asdict(question)
        fingerprint = sha256(json.dumps([lane, pending["session_id"], index, snapshot,
            pending["state"], pending["assisted_indices"], sorted(allowed)], ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        result.append({"candidate_id": "native:" + fingerprint, "target_id": question.content_id,
            "kind": question.kind, "native_lane": lane,
            "question": {"id": "native:" + fingerprint, "target_id": question.content_id,
                "revision": fingerprint, "kind": question.kind,
                "task": NATIVE[question.kind], "prompt": question.prompt,
                "has_hidden_transcript": question.kind in LISTENING_SKILLS,
                "tests_reading": True,
                "input_mode": "native_route", "ability": "native",
                "learning_conditions": [], "choices": []}})
    return result, enabled


def attach_native_routes(context, routes, enabled):
    """Attach references without displacing an active tutor question or policy."""
    context["enabled_kinds"] = list(dict.fromkeys([*context.get("enabled_kinds", []), *sorted(enabled)]))
    if context.get("question") or context.get("correction_row_id"):
        routes = []
    if context.get("harness", {}).get("mode") == "independent_check" and context.get("candidates"):
        routes = []
    if routes:
        context["candidates"] = context.get("candidates", [])[:8 - len(routes)] + routes
        if context.get("decision") in {"empty", "wait", "disabled"}:
            context["decision"] = "local"
        if context.get("harness", {}).get("action") in {"instruction", "continue_course", "finish", "wait"}:
            # Native resumption still passes through the main controller's
            # first-teaching protection; it is not an AI mastery assertion.
            context["harness"] = {**context["harness"], "action": "resume_native",
                                   "target_id": routes[0]["target_id"], "source": "native"}
    context["context_fingerprint"] = sha256(json.dumps(
        [context["context_fingerprint"], [r["candidate_id"] for r in routes], sorted(enabled)],
        sort_keys=True).encode()).hexdigest()
    return context


def resume_native_route(app, candidate):
    """Recheck immediately before dispatch; native controller owns all effects."""
    routes, _ = native_route_candidates(app.store, app._queue_kinds,
        audio_ready=bool(app.platform.tts_ready), handwriting_ready=app.model_state == "ready")
    current = next((r for r in routes if r["candidate_id"] == candidate["candidate_id"]), None)
    if current is None:
        return False
    if current["native_lane"] == "word_romaji":
        app.resume_word_drill()
    else:
        app.continue_study_path()
    return True
