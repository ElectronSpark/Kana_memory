"""Shared ai service fixtures and assertions; no collected scenarios."""
import json


FAKE_KEY = "sk-unit-test-" + "A" * 40


def request(mode="questions"):
    return {"mode": mode, "request_id": "offline_test_request_0001", "count": 2,
            "topic": "学校", "source_text": "これは本です。" if mode == "analyze" else "",
            "scope": {"format": "kanamemory-ai-scope", "version": 1,
                "targets": [{"id": "grammar:copula:polite", "revision": "1", "family": "grammar",
                    "title": "です", "reading": "", "meaning": "礼貌判断", "rule": "名词＋です",
                    "abilities": ["understanding"], "allowed_kinds": ["grammar_meaning"], "familiarity": .3}],
                "background": []},
            "target": {"id": "grammar:copula:polite", "revision": "1", "family": "grammar", "title": "です"},
            "catalog": []}


def completed(value=None, **overrides):
    value = {"questions": []} if value is None else value
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(value, ensure_ascii=False)}]}],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}, **overrides}
