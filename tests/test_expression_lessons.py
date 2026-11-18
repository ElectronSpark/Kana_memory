"""Common expressions as grammar lessons (id grammar:expression:<slug>, domain "expression"): the catalogue takes
them, the core route places them, the grammar kinds practise them and their titles use the study face.

Fixture lessons only (not course content).
"""
from copy import deepcopy

import pytest

from kanamemory import study_path_catalog as catalog_module
from kanamemory.learning_dependencies import validate_catalog
from kanamemory.learning_questions import build_learning_question, grade_learning_answer, question_kinds
from kanamemory.question_support import lesson_title_role
from kanamemory.study_path_catalog import build_study_path, flatten_sections

COPULA = "grammar:test:copula"
GREETING = "grammar:expression:test_otsukaresama"
REQUEST = "grammar:expression:test_wo_kudasai"


def expression(cid, title, reading, meaning, category, **extra):
    """A lesson with the fields of the content side's contract (CODE_BUDGET_PLAN 7.1, list 6)."""
    lesson = {"id": cid, "content_id": cid, "family": "grammar", "domain": "expression", "title": title,
              "reading": reading, "meaning": meaning, "revision": "fixture-v1", "category": category,
              "register": "polite：对同事、上司", "depends_on": [], "abilities": ["understanding", "application"],
              "instruction": {"summary": meaning, "rule": "下班时对同事说。", "steps": ["对同事、上司说。"],
                              "example": title + "。", "translation": meaning,
                              "examples": [{"text": title + "。", "translation": meaning}] * 3,
                              "prerequisite": "", "contrast": "相近说法写在这里。"},
              "question_templates": {}}
    lesson.update(extra)
    return lesson


def fixture_lessons():
    copula = {"id": COPULA, "family": "grammar", "title": "名词句：です", "category": "基础句法", "level": "进阶",
              "depends_on": []}
    greeting = expression(GREETING, "お疲れさまです", "おつかれさまです", "辛苦了", "问候", depends_on=[COPULA])
    request = expression(REQUEST, "〜をください", "をください", "请给我……", "请求")
    return [copula, greeting, request]


def placement(items):
    sections = flatten_sections(build_study_path([], items))
    return {entry["id"]: (section["stage_id"], section["title"], index)
            for index, section in enumerate(sections) for entry in section["entries"]}


@pytest.fixture
def route_config(monkeypatch):
    monkeypatch.setattr(catalog_module, "GRAMMAR_ROUTE_CONFIG", {"levels": {"入门": 1, "基础": 2, "进阶": 3}})
    monkeypatch.setattr(catalog_module, "GRAMMAR_TOPICS", {})
    monkeypatch.setattr(catalog_module, "SMALL_UNIT_PLANS", {})
    monkeypatch.setattr(catalog_module, "_PATH", {"VOCABULARY_WEAVE": []})
    return catalog_module.GRAMMAR_ROUTE_CONFIG


def test_the_catalogue_check_takes_expression_lessons_with_their_prerequisites():
    order = validate_catalog({item["id"]: item for item in fixture_lessons()})
    assert order.index(COPULA) < order.index(GREETING) and REQUEST in order
    broken = fixture_lessons()
    broken[1]["depends_on"] = ["grammar:test:missing"]
    with pytest.raises(ValueError):
        validate_catalog({item["id"]: item for item in broken})


def test_an_expression_starts_in_the_first_expression_stage_in_a_section_of_its_function(route_config):
    placed = placement(fixture_lessons())
    assert placed[REQUEST][:2] == ("stage:daily", "请求")
    # A prerequisite still lifts it: the copula is a stage-3 lesson here, so the greeting follows it there.
    assert placed[COPULA][0] == placed[GREETING][0] == "stage:connected"
    assert placed[GREETING][1] == "问候" and placed[COPULA][2] < placed[GREETING][2]
    items = fixture_lessons()
    items[0]["level"] = "入门"
    placed = placement(items)
    assert placed[GREETING][0] == placed[REQUEST][0] == "stage:daily"


def test_the_expression_stage_and_a_lesson_level_can_move_it(route_config):
    route_config["expression_stage"] = 2
    assert placement(fixture_lessons())[REQUEST][0] == "stage:conversation"
    route_config["expression_stage"] = "late"  # an invalid value keeps the default
    assert placement(fixture_lessons())[REQUEST][0] == "stage:daily"
    items = fixture_lessons()
    items[2]["level"] = "进阶"
    assert placement(items)[REQUEST][0] == "stage:connected"


def test_other_grammar_keeps_its_route():
    # Only ids under grammar:expression: take the new branch; a grammar family named otherwise is unchanged.
    item = {"id": "grammar:expressionlike:x", "family": "grammar", "title": "x", "category": "基础句法",
            "level": "进阶", "depends_on": []}
    assert catalog_module._grammar_route(item)[0] == 3


def practised(lesson, kind, raw):
    lesson = deepcopy(lesson)
    lesson["question_templates"] = {kind: [raw]}
    return lesson


def test_an_expression_is_practised_with_the_grammar_kinds_and_its_whole_sentence_question_is_strict():
    greeting = fixture_lessons()[1]
    production = {"prompt": "下班时，你对还在加班的同事说什么？", "task": "写出整句表达。", "answer": "お疲れさまです",
                  "accepted_answers": ["お疲れさまです", "おつかれさまです"], "choices": [], "input_mode": "text",
                  "strict": True, "safe": False, "feedback": "お疲れさまです。\n辛苦了。"}
    lesson = practised(greeting, "controlled_sentence_production", production)
    assert question_kinds(GREETING, item=lesson) == ["controlled_sentence_production"]
    question = build_learning_question(GREETING, "controlled_sentence_production", item=lesson, apply_review=False)
    assert (question["family"], question["target_id"], question["strict"]) == ("grammar", GREETING, True)
    assert grade_learning_answer(question, "おつかれさまです")["correct"] is True
    wrong = grade_learning_answer(question, "ごくろうさまです")
    assert (wrong["status"], wrong["scorable"]) == ("incorrect", True)
    meaning = {"prompt": "お疲れさまです。", "task": "这句话是什么意思？", "answer": "辛苦了",
               "choices": ["辛苦了", "初次见面", "我开动了"], "feedback": "辛苦了。", "safe": True}
    lesson = practised(greeting, "grammar_meaning", meaning)
    assert build_learning_question(GREETING, "grammar_meaning", item=lesson, apply_review=False)["answer"] == "辛苦了"


def test_expression_titles_use_the_study_face_and_grammar_titles_the_interface_face():
    copula, greeting, _ = fixture_lessons()
    assert lesson_title_role(greeting) == "study"
    assert lesson_title_role(copula) == "ui"
    assert lesson_title_role({"family": "word", "title": "駅"}) == "study"
    assert lesson_title_role({"family": "grammar", "domain": "pronunciation"}) == "ui"
    assert lesson_title_role(None) == "ui"
