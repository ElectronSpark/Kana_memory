"""Teaching-card quotation readings on the course pages follow the 日文假名注音 switch (design 02, teaching cards),
and a bracket that only repeats the kana before it is not drawn on a card or a result page's explanation.

Made-up content only: the card readings side file is the test's own, and the course word lookup and the
learner's known lessons are replaced at their source modules.
"""
import json
from datetime import timedelta

import pytest

from kanamemory import card_quote_readings as cards
from kanamemory import content_caches, content_pack, course_planning, question_readings, semantic_errors
from kanamemory import ui_components as component_ui
from kanamemory.answer_policy import source
from kanamemory.learning_questions import build_learning_question
from kanamemory.question_support import PROMPT_DISPLAY_SETTING
from support.app_fixtures import question_app, setup_app  # noqa: F401
from support.fake_ui import descendants, text_of
from support.learning_fixtures import LEARNING_PREFS, NOW, learning_app  # noqa: F401

ITEM = {"id": "word:test:card-page", "revision": "synthetic-v1", "family": "word", "domain": "vocabulary",
        "title": "森生", "reading": "もりせい", "meaning": "（造的词）", "abilities": ["use"], "depends_on": [],
        "instruction": {"summary": "「学校へ行く」表示去学校。", "rule": "合成说明。", "example": "森生に行きます。"}}
SPANS = {"学校へ行く": [[0, 2, "がっこう", "course", ["学校", "がっこう"]], [3, 4, "い", "course", ["行く", "いく"]]]}
BOTH = "「学校（がっこう）へ行（い）く」表示去学校。"
UNLEARNED_ONLY = "「学校へ行（い）く」表示去学校。"


@pytest.fixture
def card_app(learning_app, tmp_path, monkeypatch):  # noqa: F811
    path = tmp_path / cards.FILE
    path.write_text(json.dumps({"format": cards.FORMAT, "version": cards.VERSION,
                                "items": {ITEM["id"]: {"focus": SPANS}}}, ensure_ascii=False), "utf-8")
    real = content_pack.side_file_path
    monkeypatch.setattr(content_pack, "side_file_path", lambda name: path if name == cards.FILE else real(name))
    # 学校 is a course word the learner knows; 行く is one the learner has not learned yet.
    monkeypatch.setattr(question_readings, "course_words", lambda: {
        ("学校", "がっこう"): frozenset({"word:test:gakkou"}), ("行く", "いく"): frozenset({"word:test:iku"})})
    monkeypatch.setattr(course_planning, "known_ids", lambda queries: frozenset({"word:test:gakkou"}))
    cards._items.cache_clear()
    yield learning_app
    cards._items.cache_clear()


def _switch(app, on):
    app.store.set_setting(PROMPT_DISPLAY_SETTING, {"furigana": on, "number": True})


def _texts(app):
    return [str(node.text) for node in descendants(app.body) if isinstance(getattr(node, "text", None), str)]


@pytest.mark.parametrize("on", [False, True])
def test_the_first_teaching_page_follows_the_switch(card_app, on):
    app = card_app
    _switch(app, on)
    app.show_learning_instruction(dict(ITEM))
    assert app.page == "learning_intro"
    assert (BOTH if on else UNLEARNED_ONLY) in _texts(app)


@pytest.mark.parametrize("on", [False, True])
def test_the_lesson_detail_page_follows_the_switch(card_app, on):
    app = card_app
    _switch(app, on)
    app.show_learning_detail(dict(ITEM))
    assert app.page == "learning_detail"
    assert (BOTH if on else UNLEARNED_ONLY) in _texts(app)


@pytest.mark.parametrize("on", [False, True])
def test_a_detail_page_with_a_verdict_shows_every_reading(card_app, monkeypatch, on):
    app = card_app
    _switch(app, on)
    monkeypatch.setattr(semantic_errors, "feedback_nodes", lambda u, question, result: [u.label("答错了")])
    app.show_learning_detail(dict(ITEM), answer_context=({"target_id": ITEM["id"]}, {"correct": False}))
    texts = _texts(app)
    assert "答错了" in texts and BOTH in texts


# ---------------------------------------------------------------- a result page's explanation line (drop_kana_echo)

RECALL = {"prompt": "（造的词）", "task": "写出本课学的单词。", "answer": "森生",
          "accepted_answers": ["森生", "もりせい"], "input_mode": "text", "strict": False,
          "feedback": "ああ（ああ）：那样\n森生（もりせい）"}


def test_a_result_page_explanation_line_drops_a_bracket_that_repeats_its_kana(learning_app, monkeypatch):  # noqa: F811
    app = learning_app
    item = {**ITEM, "question_templates": {"vocab_recall": [RECALL]}}
    monkeypatch.setattr(source, "question_row", lambda question: None)
    content_caches.clear_all()
    try:
        app.store.set_setting(LEARNING_PREFS, {"enabled": ["vocab_recall"]})
        app.learning_store.enroll(item, now=NOW - timedelta(minutes=2))
        app.learning_store.advance_instruction(item["id"], "intro_done", now=NOW - timedelta(minutes=2))
        app.learning_store.advance_instruction(item["id"], "guide_correct", now=NOW - timedelta(minutes=2))
        question = build_learning_question(item, "vocab_recall")
        app.learning_store.append_questions(question)
        app.continue_learning()
        assert app.page == "learning_question"
        app.submit_learning_answer("もりせ")
        text = text_of(app.body)
        assert "ああ：那样" in text and "ああ（ああ）" not in text
        stored = app.learning_store.get_question(question["id"])
        assert "ああ（ああ）：那样" in stored["feedback"]  # the stored text never changes
    finally:
        content_caches.clear_all()
