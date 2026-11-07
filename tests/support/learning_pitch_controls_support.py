"""Shared learning pitch controls fixtures and assertions; no collected scenarios."""
from kanamemory.learning_ui import LEARNING_PREFS
from support.learning_fixtures import NOW
from support.app_fixtures import app_module
from kanamemory.learning_questions import build_learning_question
from kanamemory.pitch_practice import canonical_answer
from support.learning_fixtures import click
from copy import deepcopy
from support.fake_ui import descendants
from support.learning_fixtures import learning_app
from support.learning_fixtures import legacy_dump
from support.app_fixtures import question_app
from support.app_fixtures import setup_app
from support.fake_ui import text_of
from datetime import timedelta


def synthetic_item(*, learner_morae=False, segmentation=False):
    spec = dict(version=1, word="試験用", reading="きょう", morae=["きょ", "う"],
        accent=1, particle="が", particle_morae=["が"], learner_morae=learner_morae,
        allow_segmentation=segmentation)
    templates = {}
    for kind, mode in (("pronunciation_rule", "pitch_identify"), ("pronunciation_application", "pitch_fill")):
        raw = dict(input_mode=mode, pitch_practice=spec, prompt="きょうが", choices=[],
                   task="試験用の入力", feedback="一つの詞と助詞。",
                   text_roles={"prompt": "study", "answer": "ui"})
        raw["answer"] = canonical_answer(raw)
        templates[kind] = [deepcopy(raw), deepcopy(raw)]
    return dict(id="pronunciation:synthetic_pitch269", family="grammar", domain="pronunciation",
        revision="synthetic-1", title="测试音高", abilities=["understanding", "application"],
        instruction={"rule": "测试规则", "example": "きょう", "translation": "测试",
                     "contrast": "测试区别"}, question_templates=templates, depends_on=[])


def prepare(app, mode="pitch_fill", **options):
    item = synthetic_item(**options)
    kind = "pronunciation_application" if mode == "pitch_fill" else "pronunciation_rule"
    app.store.set_setting(LEARNING_PREFS, {"enabled": [kind]})
    app.learning_store.enroll(item, now=NOW - timedelta(minutes=2))
    app.learning_store.advance_instruction(item["id"], "intro_done", now=NOW-timedelta(minutes=2))
    app.learning_store.advance_instruction(item["id"], "guide_correct", now=NOW-timedelta(minutes=2))
    q = build_learning_question(item, kind)
    app.learning_store.append_questions(q)
    app.show_learning_question({"question": q, "draft": {}})
    return item, q


def diagrams(app):
    return [node for node in descendants(app.body) if getattr(node, "_pitch_diagram", False)]


def button(app, text):
    return next(node for node in descendants(app.body) if node.text == text and node.on_press)
