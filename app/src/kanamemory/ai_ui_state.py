"""Validate saved AI forms and pending previews without activating content."""
from copy import deepcopy
import re

from .ai_questions import validate_ai_question_snapshot

AI_FORM_SETTING = "ai_form_drafts_v1"
AI_PREVIEW_SETTING = "ai_question_preview_v1"
_REQUEST_FIELDS = {"request_id", "request_fingerprint", "request_finished"}
MAX_FORM_DRAFTS = 64


def _text(value, maximum):
    return (isinstance(value, str) and len(value) <= maximum
            and not any(0xD800 <= ord(c) <= 0xDFFF or ord(c) < 32 and c not in "\n\r\t"
                        for c in value))


def _request_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{16,100}", value) is not None


def validate_ai_preview_value(value):
    """A cleared preview is null; an active preview holds frozen local questions."""
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"request_id", "questions"}
            or not _request_id(value["request_id"])
            or not isinstance(value["questions"], list) or not 1 <= len(value["questions"]) <= 20):
        raise ValueError("AI 待确认题目格式无效。")
    identities = set()
    for question in value["questions"]:
        validate_ai_question_snapshot(question)
        if question["id"] in identities:
            raise ValueError("AI 待确认题目重复。")
        identities.add(question["id"])
    return deepcopy(value)


def validate_ai_form_value(value):
    """Draft text may be incomplete or invalid input, but its containers are fixed."""
    if value is None or value == {}:
        return deepcopy(value)
    if (not isinstance(value, dict) or set(value) != {"last", "drafts"}
            or not isinstance(value["drafts"], dict) or not 1 <= len(value["drafts"]) <= MAX_FORM_DRAFTS
            or not isinstance(value["last"], dict)
            or set(value["last"]) not in ({"mode", "target_id", "key"}, {"mode", "target_id", "key", "input_id"})):
        raise ValueError("AI 输入草稿格式无效。")
    for key, draft in value["drafts"].items():
        if not _text(key, 1033) or ":" not in key:
            raise ValueError("AI 输入草稿标识无效。")
        mode, target = key.split(":", 1)
        if (mode not in {"questions", "examples", "analyze"}
                or mode == "examples" and not target.strip()
                or mode != "examples" and target and not (mode == "analyze" and re.fullmatch(r"share/[a-f0-9]{32}", target))
                or len(target) > 1024):
            raise ValueError("AI 输入草稿任务无效。")
        allowed = {"count", "topic", *_REQUEST_FIELDS}
        if mode == "analyze":
            allowed.add("source_text")
        if (not isinstance(draft, dict) or set(draft) - allowed
                or not {"count", "topic"} <= set(draft)
                or mode == "analyze" and "source_text" not in draft
                or any(not _text(draft[name], 4000 if name == "source_text" else 200)
                       for name in ("count", "topic", "source_text") if name in draft)):
            raise ValueError("AI 输入草稿字段无效。")
        request_fields = set(draft) & _REQUEST_FIELDS
        if request_fields and (request_fields != _REQUEST_FIELDS
                or not _request_id(draft["request_id"])
                or not isinstance(draft["request_fingerprint"], str)
                or re.fullmatch(r"[0-9a-f]{64}", draft["request_fingerprint"]) is None
                or type(draft["request_finished"]) is not bool):
            raise ValueError("AI 输入草稿请求记录无效。")
    last = value["last"]
    input_id = last.get("input_id")
    if input_id is not None and (last["mode"] != "analyze" or not isinstance(input_id, str)
                                 or re.fullmatch(r"[a-f0-9]{32}", input_id) is None):
        raise ValueError("分享草稿身份无效。")
    if (not isinstance(last["key"], str) or last["key"] not in value["drafts"]
            or not isinstance(last["mode"], str) or last["mode"] not in {"questions", "examples", "analyze"}
            or last["mode"] == "examples" and (not _text(last["target_id"], 1024) or not last["target_id"].strip())
            or last["mode"] != "examples" and last["target_id"] is not None
            or last["key"] != last["mode"] + ":" + ("share/" + input_id if input_id else last["target_id"] or "")):
        raise ValueError("AI 最近输入草稿不一致。")
    return deepcopy(value)


def reset_ai_form_request_history(value):
    """Keep user text, including legacy drafts, but never reuse a pre-reset request."""
    result = deepcopy(value)
    drafts = result.get("drafts") if isinstance(result, dict) else None
    if isinstance(drafts, dict):
        for draft in drafts.values():
            if isinstance(draft, dict):
                for key in _REQUEST_FIELDS:
                    draft.pop(key, None)
    return result
