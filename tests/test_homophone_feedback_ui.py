"""Feedback distinguishes spelling-only mistakes without changing queue data."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from support.app_fixtures import question_app, setup_app
from support.fake_ui import text_of, descendants
from support.word_spelling_ui_support import spelling_app
from support.word_spelling_ui_support import start
from support.word_spelling_ui_support import word_question
from support.word_spelling_ui_support import buttons
from kanamemory.answer_feedback import build_answer_feedback


@pytest.mark.parametrize("expected,answer,category,caption", [
    ("かんじ", "かんぢ", "spelling", "写法有误 · 读音相同"),
    ("かんじ", "かんし", "reading", "读音不符"),
    ("じず", "ぢす", "mixed", "写法与读音有误"),
])
def test_word_error_category_is_compact_and_persisted(spelling_app, expected, answer, category, caption):
    app = spelling_app
    question = word_question(expected)
    question.metadata["word_romaji_drill"] = True
    question.metadata["practice_config"]["schedule_mode"] = "reinforce"
    # Old automatically generated alternatives must not continue accepting a
    # nonstandard spelling after upgrade; the snapshot itself stays unchanged.
    question = replace(question, accepted_answers=(answer,) if category == "spelling" else ())
    start(app, question)
    original = deepcopy(app.store.get_session_questions(app.session_id))
    app.check_answer(answer)
    assert not app.answer_correct and caption in text_of(app.body)
    assert "保存并继续" in buttons(app)
    assert "有些困难" not in buttons(app)
    assert not app.store._db.execute("SELECT 1 FROM word_spelling_attempt").fetchone()
    buttons(app)["保存并继续"].on_press(None)
    entry = app.store._db.execute("SELECT correct,evidence_json FROM word_spelling_attempt").fetchone()
    proof = json.loads(entry[1])
    assert entry[0] == 0 and proof["error_category"] == category
    assert app.store.get_session_questions(app.session_id) == original
    app.alert.assert_not_called()


def test_permitted_word_variant_has_one_answer_and_keeps_unfinished_tail(spelling_app):
    app = spelling_app
    question = word_question("せかいじゅう")
    question.metadata["word_romaji_drill"] = True
    question.metadata["practice_config"]["schedule_mode"] = "reinforce"
    tail = word_question("がっこう")
    tail.metadata["word_romaji_drill"] = True
    start(app, question, extra=[tail])
    original = deepcopy(app.store.get_session_questions(app.session_id))
    app.check_answer("せかいぢゅう")
    assert app.answer_correct
    assert "允许的写法" in text_of(app.body)
    # Only the canonical answer is rendered; no duplicate input block.
    assert text_of(app.body).count("せかいじゅう") == 1
    assert "せかいぢゅう" not in text_of(app.body)
    buttons(app)["保存并继续"].on_press(None)
    proof = json.loads(app.store._db.execute("SELECT evidence_json FROM word_spelling_attempt").fetchone()[0])
    assert proof["correct"] and proof["accepted_alternative"]
    assert not proof["units"] and not proof["schedule_adjustments"]
    # The frozen snapshot owns the queue; loading it normalizes the word keyboard.
    assert app.question == original[1] and app.question.answer == tail.answer
    assert app.store.get_session_questions(app.session_id) == original
    app.alert.assert_not_called()


def test_revealing_answer_does_not_show_a_spurious_reading_error(setup_app):
    feedback = build_answer_feedback({"correct": False, "assisted": True, "error_category": "reading"})
    assert "已用提示" in text_of(feedback)
    assert "读音不符" not in text_of(feedback)


def test_ungraded_vocabulary_reading_can_explain_spelling_without_failure_signal(setup_app):
    feedback = build_answer_feedback({"outcome": "unresolved", "correct": None, "error_category": "spelling"})
    assert "写法有误 · 读音相同" in text_of(feedback)
    assert feedback._feedback_fragment["state"] == "ungraded"
    assert "×" not in [node.text for node in descendants(feedback)]
