"""Shared learning practice policy fixtures and assertions; no collected scenarios."""
from kanamemory.learning_practice_policy import select_practice_plan


CID = "grammar:policy-test"


def raw(context, *, mode="choice", scaffold="recognition", family="choose_context", background=()):
    return {"prompt": f"{context}：窓を開けてください。", "task": "この場面では？", "answer": "依頼",
        "accepted_answers": ["依頼", "いらい"] if mode == "text" else ["依頼"],
        "feedback": "ここでは相手に頼んでいます。", "input_mode": mode,
        "choices": ["依頼", "報告"] if mode == "choice" else [],
        "practice": {"schema_version": 1, "task_family": family, "context_id": context,
            "scaffold": scaffold, "target_spans": [{"start": 0, "end": 1, "quote": context[0]}],
            "background_ids": list(background), "reviewed": True}}


def lesson():
    return {"id": CID, "revision": "test-v1", "family": "grammar", "title": "依頼", "abilities": ["application"],
        "instruction": {"rule": "相手に頼む", "example": "窓を開けてください。"},
        "question_templates": {
            "grammar_cloze": [raw("window"), raw("door"), raw("light")],
            "grammar_transform": [raw("shop", mode="text", scaffold="cued", family="transform"),
                                  raw("station", mode="text", scaffold="retrieval", family="produce"),
                                  raw("school", mode="text", scaffold="retrieval", family="produce")],
        }}


def observed(item, kind, variant, *, correct=True, assisted=False, outcome=None):
    q = dict(item["question_templates"][kind][variant], kind=kind, target_id=item["id"],
             revision=item["revision"], ability="application", variant=variant)
    return {"question": q, "correct": correct, "assisted": assisted,
            "outcome": outcome or ("correct" if correct else "incorrect"),
            "scheduled": True, "role": "primary"}


def choose(item=None, history=(), state=None, kinds=None):
    item = item or lesson()
    return select_practice_plan(item, "application", kinds or list(item["question_templates"]),
                                state or {}, history)
