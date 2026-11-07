"""Offline API boundary tests. Every credential below is an inert fake string."""
from copy import deepcopy
from io import BytesIO
import json
import traceback
import urllib.error

import pytest

from kanamemory import ai_service
from kanamemory.ai_service import AIServiceError, OpenAIProvider, generation_spec, validate_request

from support.ai_service_support import FAKE_KEY, request, completed


def test_responses_request_strict_schema_no_tools_storage_or_learning_rows(capsys):
    calls = []
    def transport(url, body, **kwargs):
        calls.append((url, body, kwargs))
        return completed()
    provider = OpenAIProvider(FAKE_KEY, transport=transport)
    assert provider.generate(request()) == {"questions": []}
    url, body, kwargs = calls[0]
    assert url == "https://api.openai.com/v1/responses"
    assert body["store"] is False and "tools" not in body and "tool_choice" not in body
    assert body["text"]["format"]["strict"] is True
    assert body["text"]["format"]["schema"]["additionalProperties"] is False
    assert kwargs["token"] == FAKE_KEY
    encoded = json.dumps(body)
    assert FAKE_KEY not in encoded
    payload = json.loads(body["input"][0]["content"][0]["text"])
    for key in ("request_id", "due_at_utc", "scheduler_state", "review_event", "checkpoint_json"):
        assert key not in json.dumps(payload)
    assert provider.last_usage == {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
    assert capsys.readouterr().out == ""


def test_scope_and_public_item_payloads_strip_accidental_private_state():
    source = request()
    source["scope"].update(due_at_utc="private-due", review_event=["private-history"])
    source["scope"]["targets"][0].update(scheduler_state={"private": 1}, checkpoint_json="private-draft")
    source["catalog"] = [{"id": "word:x", "title": "本", "reading": "ほん", "revision": "1",
        "family": "word", "source_urls": ["private-source"], "review_count": 42,
        "instruction": {"rule": "book", "checkpoint": "private-checkpoint"}}]
    _, payload, _ = generation_spec(source)
    encoded = json.dumps(payload)
    assert "private-" not in encoded and "scheduler_state" not in encoded
    assert set(payload["scope"]) == {"format", "version", "targets", "background"}
    assert set(payload["scope"]["targets"][0]) == {"id", "revision", "family", "title", "reading",
        "meaning", "rule", "abilities", "allowed_kinds", "familiarity"}
    assert payload["catalog"][0]["instruction"] == {"rule": "book"}


@pytest.mark.parametrize("mode", ["examples", "analyze"])
def test_content_authoring_keeps_its_own_schema_with_no_practice_scope(mode):
    source = {**request(), "mode": mode, "scope": None,
              "target": {"id": "grammar:x", "family": "grammar", "title": "は", "revision": "1"},
              "source_text": "私は学生です。"}
    instructions, payload, schema = generation_spec(source)
    assert len(payload["question_capabilities"]) == 28
    assert "separate item-template schema" in instructions
    assert "example_pack" in schema["properties"]
    assert "scope" not in payload


@pytest.mark.parametrize("key", [None, "sk-", "sk-short", "plain" + "A"*30,
    "sk-" + "A"*501, "sk-" + "A"*25 + "\nInjected: yes", "sk-" + "A"*25 + "\x00"])
def test_provider_rejects_short_or_control_character_keys_before_transport(key):
    with pytest.raises(AIServiceError) as error:
        OpenAIProvider(key, transport=lambda *_a, **_k: pytest.fail("No request allowed"))
    assert error.value.code == "credentials"
    assert str(key) not in str(error.value)


@pytest.mark.parametrize("change", [
    {"mode": "delete"}, {"request_id": "x"}, {"count": True}, {"count": 0}, {"count": 11},
    {"source_text": "x"*4001}, {"topic": "x"*201}, {"due_at_utc": "tomorrow"},
    {"api_key": FAKE_KEY}, {"topic": FAKE_KEY}, {"catalog": [{}]*101},
    {"scope": []},
])
def test_bad_or_secret_bearing_requests_fail_without_network(change):
    data = request(); data.update(change)
    with pytest.raises(AIServiceError) as error:
        validate_request(data)
    assert error.value.code in {"invalid_request", "sensitive_input"}
    assert FAKE_KEY not in str(error.value)


@pytest.mark.parametrize("body,code", [
    ({"status": "incomplete", "incomplete_details": {"reason": FAKE_KEY}}, "incomplete"),
    (completed(output=[{"type": "message", "content": [{"type": "refusal", "refusal": FAKE_KEY}]}]), "refusal"),
    (completed(output=[{"type": "message", "content": [{"type": "output_text", "text": FAKE_KEY}]}]), "response_format"),
    (completed(value=[]), "response_format"),
])
def test_refusal_incomplete_and_malformed_output_do_not_echo_provider_content(body, code):
    calls = []
    def transport(*args, **kwargs):
        calls.append(1)
        return body
    with pytest.raises(AIServiceError) as error:
        OpenAIProvider(FAKE_KEY, transport=transport).generate(request())
    assert error.value.code == code and len(calls) == 1
    assert FAKE_KEY not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("body", [None, completed(output={"secret": FAKE_KEY}),
    completed(output=[{"type": "message", "content": FAKE_KEY}]),
    completed(output=[{"type": "message", "content": [FAKE_KEY]}]),
    completed(output=[]),
])
def test_malformed_provider_containers_fail_as_redacted_service_errors(body):
    with pytest.raises(AIServiceError) as error:
        OpenAIProvider(FAKE_KEY, transport=lambda *_a, **_k: body).generate(request())
    assert error.value.code == "response_format"
    assert FAKE_KEY not in "".join(traceback.format_exception(error.value))


def test_only_numeric_token_counts_are_retained_from_provider_metadata():
    response = completed(usage={"input_tokens": 10, "output_tokens": True,
                                 "total_tokens": "30", "secret": FAKE_KEY})
    provider = OpenAIProvider(FAKE_KEY, transport=lambda *_a, **_k: response)
    assert provider.generate(request()) == {"questions": []}
    assert provider.last_usage == {"input_tokens": 10}
    response["usage"] = [FAKE_KEY]
    provider.generate(request())
    assert provider.last_usage == {}


@pytest.mark.parametrize("code,label", [(401,"credentials"),(403,"credentials"),(429,"quota"),(409,"busy"),(500,"http_500"),(302,"http_302")])
def test_http_errors_are_redacted_and_not_retried(monkeypatch, code, label):
    calls = []
    class Opener:
        def open(self, req, timeout):
            calls.append(req)
            raise urllib.error.HTTPError(req.full_url, code, FAKE_KEY, {}, BytesIO(FAKE_KEY.encode()))
    monkeypatch.setattr(ai_service.urllib.request, "build_opener", lambda *_: Opener())
    with pytest.raises(AIServiceError) as error:
        ai_service._post("https://api.openai.com/v1/responses", {"hello": "world"}, token=FAKE_KEY)
    assert error.value.code == label and len(calls) == 1
    assert FAKE_KEY not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("failure", [urllib.error.URLError(FAKE_KEY), TimeoutError(FAKE_KEY), OSError(FAKE_KEY)])
def test_transport_failures_are_redacted(monkeypatch, failure):
    class Opener:
        def open(self, *_args, **_kwargs): raise failure
    monkeypatch.setattr(ai_service.urllib.request, "build_opener", lambda *_: Opener())
    with pytest.raises(AIServiceError) as error: ai_service._post("https://api.openai.com/v1/responses", {})
    assert error.value.code == "network"
    assert FAKE_KEY not in "".join(traceback.format_exception(error.value))


@pytest.mark.parametrize("raw,code", [(b"x"*(ai_service.MAX_RESPONSE_BYTES+1),"response_size"),
    (b"not json","response_format"), (b"[]","response_format")], ids=["oversize", "invalid-json", "not-object"])
def test_response_size_and_object_bounds(monkeypatch, raw, code):
    reads = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, size): reads.append(size); return raw
    class Opener:
        def open(self, *_args, **_kwargs): return Response()
    monkeypatch.setattr(ai_service.urllib.request, "build_opener", lambda *_: Opener())
    with pytest.raises(AIServiceError) as error: ai_service._post("https://api.openai.com/v1/responses", {})
    assert error.value.code == code and reads == [ai_service.MAX_RESPONSE_BYTES+1]


def test_authorization_does_not_follow_redirects():
    assert ai_service._NoRedirect().redirect_request(None, None, 302, "moved", {}, "https://foreign.invalid/") is None
