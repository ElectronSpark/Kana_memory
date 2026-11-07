"""Shared tutor service fixtures and assertions; no collected scenarios."""
from kanamemory.tutor_service import make_tutor_request


def context():
    target = {"id": "grammar:fixture", "revision": "1", "family": "grammar", "title": "礼貌判断",
        "abilities": ["understanding", "application"], "allowed_kinds": ["grammar_meaning", "grammar_cloze"],
        "familiarity": .2, "rule": "名词后用です作礼貌判断。"}
    question = {"id": "local:fixture", "target_id": target["id"], "revision": "1", "kind": "grammar_meaning",
        "prompt": "これは本です。", "choices": ["礼貌地说明类别", "否定某事", "请求行动"],
        "answer": "礼貌地说明类别", "feedback": "名词加です表示礼貌判断。", "secret_timestamp": "never-send"}
    return {"scope": {"format": "kanamemory-ai-scope", "version": 1, "targets": [target], "background": []},
        "target": {**target, "instruction": {"rule": target["rule"]}, "units": [{"scheduler": "private"}]},
        "question": question, "candidates": [{"candidate_id": "fixture", "target_id": target["id"], "question": question}],
        "recent": [{"target_id": target["id"], "outcome": "incorrect", "due": "never-send"}]}


def request(intent="next"):
    return make_tutor_request(context(), intent, "test_request_0123456789", message="为什么这里不能用ます？")


def explain(text="这里用名词作判断，不能把动词的ます直接接到名词后。"):
    return {"action": "explain", "candidate_id": "", "target_id": "grammar:fixture", "text": text, "question": None}
