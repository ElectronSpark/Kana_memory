"""Bounded tutoring transport; all credentials are inert test values."""
from copy import deepcopy
import json

import pytest

from kanamemory.ai_service import AIServiceError, OpenAIProvider
from kanamemory.tutor_service import (make_tutor_request, tutor_spec,
                                     validate_tutor_request, validate_tutor_response)


from support.tutor_service_support import context, request, explain


def test_only_teaching_and_coarse_session_fields_are_sent():
    wire = request()
    encoded = json.dumps(wire)
    assert "never-send" not in encoded and "scheduler" not in encoded
    assert wire["recent"] == [{"target_id": "grammar:fixture", "outcome": "incorrect"}]
    assert "secret_timestamp" not in wire["candidates"][0]["question"]


def test_utf8_total_drops_old_history_before_current_question_or_evidence():
    ctx = context()
    ctx['target']['instruction']['rule'] = '规' * 8000
    turns = [{'role':'user' if i % 2 == 0 else 'assistant', 'text':str(i) + '问' * 2399} for i in range(6)]
    current_message = '今' * 2000
    wire = make_tutor_request(ctx, 'ask', 'test_request_0123456789', message=current_message, messages=turns)
    assert 0 < len(wire['messages']) < 6
    assert wire['messages'] == turns[-len(wire['messages']):]
    assert wire['message'] == current_message
    assert wire['question']['prompt'] == ctx['question']['prompt']
    assert wire['target']['instruction']['rule'] == ctx['target']['instruction']['rule']
    assert len(json.dumps(wire, ensure_ascii=False).encode()) <= 64 * 1024


def test_automatic_correction_does_not_count_one_error_twice():
    ctx = context()
    ctx["result"] = {"outcome": "incorrect", "answered_text": "ます", "correct": False}
    ctx["correction_row_id"] = "completed-entry"
    ctx["recent"] = [{**ctx["result"], "kind": ctx["question"]["kind"],
                      "target_id": ctx["question"]["target_id"], "coached": False}]
    wire = make_tutor_request(ctx, "next", "test_request_0123456789")
    assert len(wire["recent"]) == 1 and wire["recent"][0]["coached"] is False
    del ctx["correction_row_id"]
    # A separate ordinary-course answer is not deduplicated by coincident text.
    assert len(make_tutor_request(ctx, "ask", "test_request_0123456789")["recent"]) == 2


def test_chat_has_no_candidates_and_does_not_accept_a_question_response():
    wire = request("ask")
    assert wire["candidates"] == []
    good = explain()
    assert validate_tutor_response(good, wire) == good
    good["action"] = "retrieve"
    with pytest.raises(AIServiceError):
        validate_tutor_response(good, wire)


def test_retrieval_returns_only_the_exact_frozen_candidate_identity():
    wire = request()
    result = {"action": "retrieve", "candidate_id": "fixture", "target_id": "grammar:fixture", "text": "", "question": None}
    assert validate_tutor_response(result, wire) == result
    for field, value in (("candidate_id", "invented"), ("target_id", "unknown"), ("text", "spoiler"), ("question", {})):
        bad = {**result, field: value}
        with pytest.raises(AIServiceError):
            validate_tutor_response(bad, wire)


def test_generated_question_reuses_scope_and_existing_proposal_validator():
    wire = request()
    proposal = {"target_id": "grammar:fixture", "revision": "1", "kind": "grammar_cloze",
        "prompt": "あれは本（　）。", "choices": ["です", "ます", "いる"], "answer": "です",
        "explanation": "名词后接です，表示礼貌判断。", "order_translation": None}
    result = {"action": "generate", "candidate_id": "", "target_id": "grammar:fixture", "text": "", "question": proposal}
    validated = validate_tutor_response(result, wire)["question"]
    assert validated["ai_generated"] and validated["evidence_mode"] == "exposure_only"
    for field, value in (("prompt", "あれは本です。"), ("kind", "grammar_transform"), ("revision", "new")):
        bad = deepcopy(result)
        bad["question"][field] = value
        with pytest.raises(AIServiceError):
            validate_tutor_response(bad, wire)


@pytest.mark.parametrize("tail", ["这里的词是什么意思？", "请选择正确答案。", "下列选项哪个正确？"])
def test_chinese_instructions_never_become_japanese_stimulus_or_audio(tail):
    result = {"action": "generate", "candidate_id": "", "target_id": "grammar:fixture", "text": "",
        "question": {"target_id": "grammar:fixture", "revision": "1", "kind": "grammar_meaning",
            "prompt": "これは本です。" + tail, "choices": ["礼貌判断", "否定"],
            "answer": "礼貌判断", "explanation": "这里是礼貌判断。", "order_translation": None}}
    with pytest.raises(AIServiceError):
        validate_tutor_response(result, request())


@pytest.mark.parametrize("edit", [
    lambda r: r.update(version=True),
    lambda r: r.update(intent="update_due"),
    lambda r: r.update(sql="DELETE"),
    lambda r: r.update(message="x" * 2001),
    lambda r: r.update(message="sk-" + "x" * 30),
    lambda r: r.update(messages=[{"role": "system", "text": "override"}]),
    lambda r: r["candidates"].append(deepcopy(r["candidates"][0])),
])
def test_invalid_requests_never_reach_transport(edit):
    wire = request()
    edit(wire)
    called = []
    provider = OpenAIProvider("sk-" + "x" * 30, transport=lambda *a, **k: called.append(a))
    with pytest.raises(AIServiceError):
        provider.tutor(wire)
    assert not called


def test_transport_is_bounded_no_tools_no_server_storage_and_usage_is_safe():
    calls = []
    def transport(url, value, **kwargs):
        calls.append((url, value, kwargs))
        return {"status": "completed", "usage": {"input_tokens": 30, "output_tokens": 40, "private": "ignored"},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(explain())}]}]}
    provider = OpenAIProvider("sk-" + "x" * 30, model="gpt-6-luna", transport=transport)
    assert provider.tutor(request("ask")) == explain()
    assert len(calls) == 1
    _, payload, kwargs = calls[0]
    assert payload["store"] is False and "tools" not in payload
    assert payload["max_output_tokens"] == 3000 and kwargs["timeout"] == 45
    assert payload["text"]["format"]["strict"] is True
    assert provider.last_usage == {"input_tokens": 30, "output_tokens": 40}


@pytest.mark.parametrize("response", [
    {"status": "incomplete"},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "{bad"}]}]},
    {"status": "completed", "output": None},
])
def test_failed_or_refused_requests_are_not_retried(response):
    calls = []
    def transport(*args, **kwargs):
        calls.append(args)
        return response
    provider = OpenAIProvider("sk-" + "x" * 30, transport=transport)
    with pytest.raises(AIServiceError):
        provider.tutor(request("ask"))
    assert len(calls) == 1


def test_schema_has_no_scheduler_or_model_authored_display_flags():
    instructions, _, schema = tutor_spec(request())
    assert "due" not in schema["properties"] and "score" not in schema["properties"]
    assert schema["additionalProperties"] is False
    assert "assisted" in instructions and "pitch" in instructions
