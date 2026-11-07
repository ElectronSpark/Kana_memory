"""Shared pitch practice fixtures and assertions; no collected scenarios."""
from support.learning_store_fixtures import START
from kanamemory.learning_questions import build_learning_question
from kanamemory import pitch_practice as pitch
from support.learning_store_fixtures import ready
from support.learning_store_fixtures import stores


def template(mode="pitch_fill", *, learner=False, segmentation=False, particle="が",
             morae=None, accent=1, word="今日"):
    morae = morae or ["きょ", "う"]
    raw = {"input_mode": mode, "prompt": "".join(morae) + particle,
        "task": "①型の高低を示す。" if mode == "pitch_fill" else "数拍并选择调号。",
        "feedback": "词后的助词另列；不计入词的拍数。", "choices": [],
        "text_roles": {"prompt": "study", "answer": "ui"}, "safe": False,
        "pitch_practice": {"version": 1, "word": word, "reading": "".join(morae),
            "morae": morae, "accent": accent, "particle": particle,
            "particle_morae": list(particle), "learner_morae": learner,
            "allow_segmentation": segmentation}}
    raw["answer"] = pitch.canonical_answer(raw)
    return raw


def item(raw=None):
    raw = raw or template()
    return {"id": "pronunciation:test_pitch", "family": "grammar", "domain": "pronunciation",
        "revision": "test-v1", "abilities": ["understanding", "application"], "title": "音高规则",
        "meaning": "应用词的拍和调号", "instruction": {"rule": "看词的调号，再为每拍标高低。",
            "example": "きょう", "summary": "一个词", "translation": "今天"}, "depends_on": [],
        "question_templates": {"pronunciation_rule": [template("pitch_identify")],
                               "pronunciation_application": [raw]}}


def question(**kwargs):
    return build_learning_question(item(template(**kwargs)), "pronunciation_application")


def perform(q, actions, draft=None):
    result = pitch.initial_draft(q) if draft is None else draft
    for action in actions:
        result = pitch.apply_action(q, result, action)
    return result
