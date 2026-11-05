"""Small, bounded tutoring requests. The model has no scheduling tools.

Local policy owns eligibility and candidate snapshots; generated exercises stay
training material. Valid JSON is deliberately not a semantic certification.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re

from .ai_service import AIServiceError, _json, public_item, public_scope
from .ai_questions import AI_CHOICE_KINDS, question_response_schema, validate_question_proposals

DEFAULT_TUTOR_MODEL = "gpt-6-luna"
FORMAT = "kanamemory-tutor-request"
FIELDS = {"format", "version", "request_id", "intent", "scope", "target", "recent",
          "question", "messages", "message", "candidates"}
QUESTION_FIELDS = ("id", "target_id", "revision", "kind", "task", "prompt", "choices",
                   "answer", "accepted_answers", "feedback", "explanation", "input_mode",
                   "ability", "learning_conditions")


def _error():
    return AIServiceError("tutor_format", "这次辅导内容无法安全使用，可以继续本地练习。")


def _text(value, limit, *, empty=True):
    if (not isinstance(value, str) or len(value) > limit or not empty and not value.strip()
            or any(ord(c) < 32 and c not in "\n\t\r" or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise _error()
    return value


def _question(value, *, describe=False):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _error()
    result = {}
    for key in QUESTION_FIELDS:
        if key not in value:
            continue
        raw = value[key]
        if key in {"choices", "accepted_answers", "learning_conditions"}:
            maximum, width = (8, 160) if key == "learning_conditions" else (32, 1600)
            if not isinstance(raw, list) or len(raw) > maximum:
                raise _error()
            result[key] = [_text(v, width) for v in raw]
        else:
            result[key] = _text(str(raw), 2400)
    if describe or "presentation" in value:
        from .ai_question_capabilities import question_semantics
        try:
            result["presentation"] = question_semantics(value)
        except (ValueError, KeyError, TypeError):
            raise _error() from None
    if 'response_parts' in value or value.get('input_mode') == 'multi_cloze':
        from .ai_question_capabilities import public_response_parts
        try:
            result['response_parts'] = public_response_parts(value)
        except (ValueError, KeyError, TypeError):
            raise _error() from None
    if 'prosody_cue' in value:
        from .prosody_cue import validate_cue
        try:
            result['prosody_cue'] = validate_cue(value['prosody_cue'], value.get('prompt', ''))
        except (ValueError, KeyError, TypeError):
            raise _error() from None
    return result


def make_tutor_request(context, intent, request_id, *, message="", messages=()):
    """Allowlist wire fields, excluding dates, scheduler rows and source metadata."""
    scope = public_scope(context.get("scope", {"format": "kanamemory-ai-scope", "version": 1,
                                               "targets": [], "background": []}))
    scope["targets"] = scope["targets"][:12]
    scope["background"] = scope["background"][:12]
    target = context.get("target")
    if target is not None and not isinstance(target, dict):
        raise _error()
    if target and target.get("rule") and not target.get("instruction"):
        target = {**target, "instruction": {"rule": target["rule"]}}
    target = public_item(target) if target else None
    recent = []
    for row in context.get("recent", [])[-8:]:
        recent.append({k: deepcopy(row[k]) for k in
            ("target_id", "kind", "outcome", "assisted", "answered_text", "correct", "coached") if k in row})
    result, question = context.get("result"), context.get("question")
    if isinstance(result, dict) and isinstance(question, dict):
        event = {"target_id": question.get("target_id", ""), "kind": question.get("kind", ""),
            **{key: deepcopy(result[key]) for key in
               ("outcome", "assisted", "answered_text", "correct") if key in result}}
        if (context.get("correction_row_id") and recent
                and all(recent[-1].get(key) == event.get(key) for key in
                        ("target_id", "kind", "outcome", "answered_text"))):
            recent[-1].update(event)
        else:
            recent = [*recent[-7:], event]
    candidates = []
    if intent == "next":
        for row in context.get("candidates", [])[:8]:
            q = _question(row.get("question"), describe=True)
            candidates.append({"candidate_id": row["candidate_id"], "question": q})
    turns = [{"role": row["role"], "text": row["text"]} for row in list(messages)[-6:]]
    request = {"format": FORMAT, "version": 1, "request_id": request_id, "intent": intent,
        "scope": scope, "target": target, "recent": recent,
        "question": _question(context.get("question"), describe=True), "messages": turns,
        "message": message, "candidates": candidates}
    from .ai_question_capabilities import capability_catalog
    enabled = context.get("enabled_kinds", [k for t in scope["targets"] for k in t.get("allowed_kinds", [])])
    request["question_capabilities"] = capability_catalog(enabled, [c["question"] for c in candidates])
    if context.get("learner_context") is not None:
        request["learner_context"] = deepcopy(context["learner_context"])
    # History is optional context. Individual fields can all be legal while
    # their UTF-8 total exceeds the wire budget, especially in Chinese. Keep
    # the current question/message and evidence; remove the oldest turns first.
    while len(_json(request)) > 64 * 1024 and request['messages']:
        request['messages'].pop(0)
    while len(_json(request)) > 64 * 1024 and request['scope']['background']:
        request['scope']['background'].pop()
    return validate_tutor_request(request)


def validate_tutor_request(request):
    if (not isinstance(request, dict) or set(request) - {"learner_context", "question_capabilities"} != FIELDS or request.get("format") != FORMAT
            or type(request.get("version")) is not int or request["version"] != 1
            or request.get("intent") not in {"next", "ask", "explain"}
            or not isinstance(request.get("request_id"), str)
            or re.fullmatch(r"[A-Za-z0-9_-]{16,100}", request["request_id"]) is None):
        raise _error()
    if "question_capabilities" in request:
        from .ai_question_capabilities import validate_catalog
        try:
            validate_catalog(request["question_capabilities"])
        except (ValueError, KeyError, TypeError):
            raise _error() from None
    if "learner_context" in request:
        from .tutor_context import validate_learner_context
        try:
            validate_learner_context(request["learner_context"])
        except (ValueError, KeyError, TypeError):
            raise _error() from None
    if request["scope"] != public_scope(request["scope"]):
        raise _error()
    from .learning_questions import KIND_ABILITIES
    for group in ("targets", "background"):
        for row in request["scope"][group]:
            if (not isinstance(row.get("id"), str) or not row["id"]
                    or not isinstance(row.get("revision"), str) or not row["revision"]
                    or row.get("family") not in {"word", "grammar"}
                    or not isinstance(row.get("abilities"), list)
                    or any(ability not in set(KIND_ABILITIES.values()) for ability in row["abilities"])
                    or not isinstance(row.get("allowed_kinds"), list)
                    or any(kind not in KIND_ABILITIES for kind in row["allowed_kinds"])
                    or group == "targets" and any(KIND_ABILITIES[kind] not in row["abilities"] for kind in row["allowed_kinds"])
                    or type(row.get("familiarity")) not in {int, float}
                    or not math.isfinite(row["familiarity"]) or not 0 <= row["familiarity"] <= 1):
                raise _error()
    _text(request["message"], 2000)
    if request["target"] is not None:
        if not isinstance(request["target"], dict) or not isinstance(request["target"].get("id"), str):
            raise _error()
        if request["target"] != public_item(request["target"]):
            raise _error()
    if request["question"] != _question(request["question"]):
        raise _error()
    for key, maximum in (("recent", 8), ("messages", 6), ("candidates", 8)):
        if not isinstance(request[key], list) or len(request[key]) > maximum:
            raise _error()
    for row in request["messages"]:
        if not isinstance(row, dict) or set(row) != {"role", "text"} or row["role"] not in {"user", "assistant"}:
            raise _error()
        _text(row["text"], 2400)
    for row in request["recent"]:
        if (not isinstance(row, dict) or set(row) - {"target_id", "kind", "outcome", "assisted", "answered_text", "correct", "coached"}
                or "coached" in row and type(row["coached"]) is not bool
                or len(_json(row)) > 4000):
            raise _error()
    identities = set()
    for row in request["candidates"]:
        if (not isinstance(row, dict) or set(row) != {"candidate_id", "question"}
                or not isinstance(row["candidate_id"], str) or not row["candidate_id"]
                or len(row["candidate_id"]) > 256 or row["candidate_id"] in identities
                or row["question"] is None or row["question"] != _question(row["question"])):
            raise _error()
        identities.add(row["candidate_id"])
    if request["intent"] != "next" and request["candidates"]:
        raise _error()
    encoded = _json(request)
    if re.search(rb"sk-(?:proj-)?[A-Za-z0-9_-]{16,}", encoded):
        raise AIServiceError("sensitive_input", "输入包含疑似密钥，请移除后发送。")
    if len(encoded) > 64 * 1024:
        raise AIServiceError("invalid_request", "当前资料超过单次辅导容量，输入已保留；可以缩小提问范围后重试。")
    return deepcopy(request)


def tutor_spec(request):
    request = validate_tutor_request(request)
    proposal = question_response_schema()["properties"]["questions"]["items"]
    schema = {"type": "object", "additionalProperties": False,
        "required": ["action", "candidate_id", "target_id", "text", "question"],
        "properties": {
            "action": {"type": "string", "enum": ["retrieve", "generate", "explain"]},
            "candidate_id": {"type": "string", "maxLength": 256},
            "target_id": {"type": "string", "maxLength": 256},
            "text": {"type": "string", "maxLength": 1600},
            "question": {"anyOf": [proposal, {"type": "null"}]}}}
    if "learner_context" in request:
        schema["required"].append("memory_updates")
        schema["properties"]["memory_updates"] = memory_updates_schema()
    from .ai_prompt_policy import retrieval_candidates, tutor_instructions
    dispatch_only = _dispatch_only(request)
    if dispatch_only:
        # Native queues own assistance and grading. Selecting a route must
        # never become an unrecorded lesson that reveals its pending answer.
        schema["properties"]["action"] = {"type": "string", "enum": ["retrieve"]}
        schema["properties"]["text"] = {"type": "string", "enum": [""]}
        schema["properties"]["question"] = {"type": "null"}
        if "memory_updates" in schema["properties"]:
            schema["properties"]["memory_updates"]["maxItems"] = 0
    elif request["intent"] in {"ask", "explain"}:
        # Match the existing validator; chat cannot issue another exercise.
        schema["properties"]["action"] = {"type": "string", "enum": ["explain"]}
        schema["properties"]["candidate_id"] = {"type": "string", "enum": [""]}
        schema["properties"]["question"] = {"type": "null"}
    if request["intent"] == "next":
        candidates = [row["candidate_id"] for row in retrieval_candidates(request)]
        # A request with no candidates still fails the local dispatch guard;
        # never publish an invalid empty JSON-schema enum.
        schema["properties"]["candidate_id"] = {"type": "string",
            "enum": candidates if dispatch_only and candidates else ["", *candidates]}
    return tutor_instructions(request, dispatch_only=dispatch_only), request, schema


def _native_dispatch(request):
    return request.get("intent") == "next" and any(
        row.get("question", {}).get("input_mode") == "native_route"
        for row in request.get("candidates", []))


def _dispatch_only(request):
    policy = request.get("learner_context", {}).get("policy", {})
    return _native_dispatch(request) or (request.get("intent") == "next"
        and policy.get("mode") == "independent_check" and policy.get("action") == "practice")


def validate_tutor_response(result, request):
    if (not isinstance(result, dict) or set(result) - {"memory_updates"} != {"action", "candidate_id", "target_id", "text", "question"}
            or result.get("action") not in {"retrieve", "generate", "explain"}):
        raise _error()
    if _dispatch_only(request) and (result["action"] != "retrieve" or result.get("memory_updates")):
        raise _error()
    if "memory_updates" in result:
        from .tutor_memory import validate_updates_structure
        from .tutor_context import reference_sets
        try:
            validate_updates_structure(result["memory_updates"])
            refs = reference_sets(request["learner_context"]) if request.get("learner_context") else {
                "allowed_evidence": set(), "allowed_targets": set(), "allowed_claims": set()}
            evidence_targets = {example['event_id']: target['target_id']
                for target in request.get('learner_context', {}).get('evidence', {}).get('targets', [])
                for pattern in target['patterns'] for example in pattern['examples']}
            for update in result["memory_updates"]:
                if (not set(update["target_ids"]) <= refs["allowed_targets"]
                        or not set(update["evidence_ids"]) <= refs["allowed_evidence"]
                        or not set(update["replaces"]) <= refs["allowed_claims"]
                        or update["kind"] in {"preference", "question_focus"}
                        and not any(v.startswith("message:") for v in update["evidence_ids"])
                        or update["kind"] == "observation"
                        and all(v.startswith("message:") for v in update["evidence_ids"])):
                    raise ValueError("Unfounded memory update")
                if update['kind'] == 'observation' and (not update['target_ids']
                        or not set(update['target_ids']) <= {evidence_targets[eid] for eid in update['evidence_ids']
                                                            if eid in evidence_targets}):
                    raise ValueError('Observation assigned to an unevidenced learning item')
        except (ValueError, TypeError, KeyError):
            raise _error() from None
    for key, limit in (("candidate_id", 256), ("target_id", 256), ("text", 1600)):
        _text(result[key], limit)
    if re.search(rb"sk-(?:proj-)?[A-Za-z0-9_-]{16,}", _json(result)):
        raise _error()
    candidates = {row["candidate_id"]: row["question"] for row in request["candidates"]}
    targets = {row["id"] for row in request["scope"]["targets"]}
    if request['intent'] in {'ask', 'explain'}:
        targets.update(row['id'] for row in request.get('learner_context', {}).get('facts', []))
    targets.update(q.get("target_id") for q in candidates.values())
    if request["target"]:
        targets.add(request["target"]["id"])
    if result["target_id"] and result["target_id"] not in targets:
        raise _error()
    if request["intent"] != "next" and result["action"] != "explain":
        raise _error()
    policy = request.get("learner_context", {}).get("policy", {}).get("mode")
    if (request["intent"] == "next" and policy in {"progress", "independent_check"}
            and result['action'] in {'retrieve', 'generate'}
            and request.get('learner_context', {}).get('policy', {}).get('action') == 'practice'
            and result['target_id'] != request['learner_context']['policy'].get('target_id')):
        raise _error()
    if result["action"] == "retrieve":
        q = candidates.get(result["candidate_id"])
        if (q is None or result["question"] is not None or result["text"]
                or result["target_id"] != q.get("target_id")):
            raise _error()
    elif result["action"] == "generate":
        if result["candidate_id"] or result["text"] or not isinstance(result["question"], dict):
            raise _error()
        # Japanese and Chinese share Han characters, so script detection cannot
        # certify language. Reject obvious leaked Chinese UI instructions instead
        # of silently deleting text that might carry a necessary constraint.
        stimulus = result["question"].get("prompt", "")
        if not isinstance(stimulus, str) or re.search(
                r"这里|什么|哪[个项]|请选择|选择|意思是|下列选项|根据[以上下]|正确答案", stimulus):
            raise _error()
        try:
            validated = validate_question_proposals([result["question"]], request["scope"], AI_CHOICE_KINDS)
        except (TypeError, ValueError, KeyError):
            raise _error() from None
        if len(validated) != 1 or validated[0]["target_id"] != result["target_id"]:
            raise _error()
        result = {**result, "question": validated[0]}
    elif result["candidate_id"] or result["question"] is not None or not result["text"].strip():
        raise _error()
    return deepcopy(result)


def memory_updates_schema():
    return {"type": "array", "maxItems": 3, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["operation", "kind", "text", "target_ids", "evidence_ids", "replaces"],
        "properties": {
            "operation": {"type": "string", "enum": ["upsert", "retract"]},
            "kind": {"type": "string", "enum": ["preference", "observation", "question_focus"]},
            "text": {"type": "string", "maxLength": 240},
            **{key: {"type": "array", "maxItems": maximum,
                     "items": {"type": "string", "maxLength": 256}}
               for key, maximum in (("target_ids", 4), ("evidence_ids", 6), ("replaces", 4))}}}}


def generate_tutor_response(provider, request):
    instructions, payload, schema = tutor_spec(request)
    provider.last_usage = {}
    provider.last_validation = {}
    response = provider._request({
        "model": provider.model, "store": False, "instructions": instructions,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": _json(payload).decode("utf-8")}]}],
        "text": {"format": {"type": "json_schema", "name": "kana_tutor_v1", "strict": True, "schema": schema}},
        "reasoning": {"effort": "low"}, "max_output_tokens": 3000,
    }, timeout=45)
    if not isinstance(response, dict):
        raise _error()
    usage = response.get("usage")
    if isinstance(usage, dict):
        provider.last_usage = {k: v for k, v in usage.items()
            if k in {"input_tokens", "output_tokens", "total_tokens"} and type(v) is int and v >= 0}
        cached = usage.get("input_tokens_details", {}).get("cached_tokens") if isinstance(usage.get("input_tokens_details"), dict) else None
        if type(cached) is int and cached >= 0:
            provider.last_usage["cached_input_tokens"] = cached
    if response.get("status") != "completed":
        raise AIServiceError("incomplete", "辅导生成未完成，可继续本地练习或稍后重试。")
    parts = []
    messages = response.get("output", [])
    if not isinstance(messages, list):
        raise _error()
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "message":
            continue
        content = message.get("content", [])
        if not isinstance(content, list):
            raise _error()
        for part in content:
            if not isinstance(part, dict):
                raise _error()
            if part.get("type") == "refusal":
                raise AIServiceError("refusal", "AI 未回答这次问题，可修改问题或继续练习。")
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
    try:
        result = json.loads("".join(parts))
    except (ValueError, TypeError):
        raise _error() from None
    return validate_tutor_response(result, payload)
