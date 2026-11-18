"""The answer of a result page in one format: the standard spelling with kana above it (owner 2026-11-18).

Pure: the cells, the decision of text and spans, and the content lookup that only a result page makes.
"""
import pytest

from kanamemory import answer_furigana as furigana
from kanamemory import learning_questions as lq
from kanamemory import question_support


def reading(answer, **values):
    """A question whose answers are Japanese (a reading question) without any content lookup."""
    return {"target_id": "none", "kind": "vocab_reading", "ability": "reading", "answer": answer, **values}


def use(answer):
    return {"target_id": "none", "kind": "grammar_cloze", "ability": "use", "answer": answer}


@pytest.mark.parametrize("text, spans, cells", [
    ("学校へ行く", [(0, 2, "がっこう"), (3, 4, "い")], [("学校", "がっこう"), ("へ", ""), ("行", "い"), ("く", "")]),
    ("学校", [(0, 2, "がっこう")], [("学校", "がっこう")]),
    ("です", [], [("です", "")]),
    ("", [], []),
    # out of order, outside the text or empty: left out, the cells still join back to the text
    ("学校へ", [(0, 2, "がっこう"), (1, 2, "x"), (2, 9, "y"), (2, 2, "z")], [("学校", "がっこう"), ("へ", "")]),
])
def test_cells_join_back_to_the_text(text, spans, cells):
    assert furigana.furigana_cells(text, spans) == cells
    assert "".join(surface for surface, _ in cells) == text
    assert furigana.furigana_cells(None, spans) == []


def test_a_kana_answer_with_a_kanji_display_is_read_from_the_answer():
    question = reading("がっこう")
    assert furigana.answer_form(question, form={"answer_display": "学校"}) == ("学校", [(0, 2, "がっこう")])
    assert furigana.answer_cells(question, form={"answer_display": "学校"}) == [("学校", "がっこう")]
    # katakana answer, mixed spelling
    assert furigana.answer_form(reading("タベル"), form={"answer_display": "食べる"}) == ("食べる", [(0, 1, "た")])


def test_a_non_kana_answer_is_read_from_answer_reading_or_not_at_all():
    question = use("学校に行く")
    form = {"answer_display": "学校へ行く", "answer_reading": "がっこうへいく"}
    assert furigana.answer_form(question, form=form) == ("学校へ行く", [(0, 2, "がっこう"), (3, 4, "い")])
    # without answer_reading a display that differs from the answer gets no furigana
    assert furigana.answer_form(question, form={"answer_display": "学校へ行く"}) == ("学校へ行く", [])


def test_mixed_kana_and_kanji(monkeypatch):
    monkeypatch.setattr(furigana, "annotation_spans", lambda question, text: pytest.fail("not asked"))
    form = {"answer_display": "取り消す", "answer_reading": "とりけす"}
    assert furigana.answer_form(use("取消す"), form=form) == ("取り消す", [(0, 1, "と"), (2, 3, "け")])


def test_a_display_equal_to_the_answer_keeps_todays_readings(monkeypatch):
    monkeypatch.setattr(furigana, "annotation_spans", lambda question, text: [(0, 2, "がっこう")])
    assert furigana.answer_form(use("学校"), form={"answer_display": "学校"}) == ("学校", [(0, 2, "がっこう")])
    # with a reading the reading wins
    form = {"answer_display": "学校", "answer_reading": "がくこう"}
    assert furigana.answer_form(use("学校"), form=form) == ("学校", [(0, 2, "がくこう")])


def test_without_a_display_the_answer_has_the_readings_of_the_inline_form(monkeypatch):
    monkeypatch.setattr(furigana, "annotation_spans", lambda question, text: [(0, 2, "がっこう")])
    assert furigana.answer_form(use("学校へ"), form={}) == ("学校へ", [(0, 2, "がっこう")])
    assert furigana.answer_cells(use("学校へ"), form={}) == [("学校", "がっこう"), ("へ", "")]
    # a Chinese meaning is never read
    meaning = {"target_id": "none", "kind": "vocab_meaning", "ability": "understanding", "answer": "学校"}
    assert furigana.answer_form(meaning, form={}) == ("学校", [])


def test_the_display_applies_to_the_questions_own_answer_only(monkeypatch):
    monkeypatch.setattr(furigana, "lookup_answer_form", lambda question: pytest.fail("not looked up"))
    monkeypatch.setattr(furigana, "annotation_spans", lambda question, text: [])
    question = reading("がっこう")
    assert furigana.answer_form(question, "がくこう") == ("がくこう", [])


def test_the_lookup_is_made_when_no_form_is_given(monkeypatch):
    asked = []
    monkeypatch.setattr(furigana, "lookup_answer_form",
                        lambda question: asked.append(question["answer"]) or {"answer_display": "学校"})
    assert furigana.answer_form(reading("がっこう")) == ("学校", [(0, 2, "がっこう")])
    assert asked == ["がっこう"]


def test_a_snapshot_whose_context_is_gone_falls_back_to_the_answer(monkeypatch):
    monkeypatch.setattr(furigana, "annotation_spans", lambda question, text: [])
    question = reading("がっこう", content_id="none", revision=1, practice_source={
        "schema_version": 1, "item_fingerprint": "0" * 64, "template_sha256": "f" * 64})
    assert lq.reviewed_answer_form(question) == {}
    assert furigana.answer_form(question) == ("がっこう", [])
    assert lq.reviewed_answer_form({"practice_source": "broken"}) == {}


def test_the_answer_form_never_reaches_an_open_question(monkeypatch):
    context = {"background_glosses": [], "answer_display": "学校", "answer_reading": "がっこう"}
    monkeypatch.setattr(lq, "_reviewed_context", lambda question: dict(context))
    question = reading("がっこう")
    assert lq.reviewed_practice_context(question) == {"background_glosses": []}
    assert lq.reviewed_answer_form(question) == {"answer_display": "学校", "answer_reading": "がっこう"}
    display = question_support.learning_question_display({**question, "practice_source": {
        "schema_version": 1, "item_fingerprint": "0" * 64, "template_sha256": "f" * 64}, "prompt": "学校"})
    assert "answer_display" not in str(display) and "answer_reading" not in str(display)
    # empty or non-string values are no display form
    context.update(answer_display=" ", answer_reading=3)
    assert lq.reviewed_answer_form(question) == {}
