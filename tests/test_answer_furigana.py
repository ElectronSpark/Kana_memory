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


GLOSS = {"start": 3, "end": 6, "surface": "図書館", "meaning": "图书馆", "reading": "としょかん", "target_id": "word:other"}


def test_the_answer_form_never_reaches_an_open_question(monkeypatch):
    """A cloze with a reviewed context goes through prompt support and its glosses; the form stays out."""
    context = {"background_glosses": [dict(GLOSS)], "answer_display": "行きました", "answer_reading": "いきました"}
    asked = []
    monkeypatch.setattr(lq, "_reviewed_context", lambda question: asked.append(1) or dict(context))
    question = {"target_id": "grammar:x", "content_id": "grammar:x", "kind": "grammar_cloze",
                "ability": "application", "prompt": "昨日、図書館へ（　）。", "answer": "行きました",
                "practice": {"target_spans": []}, "practice_source": {
                    "schema_version": 1, "item_fingerprint": "0" * 64, "template_sha256": "f" * 64}}
    assert lq.reviewed_practice_context(question) == {"background_glosses": [GLOSS]}
    display = question_support.learning_question_display(question)
    assert asked and display["background_glosses"] == [GLOSS]          # the gloss path really ran
    assert "answer_display" not in str(display) and "answer_reading" not in str(display)
    assert "行きました" not in str(display.get("background_glosses"))
    assert lq.reviewed_answer_form(question) == {"answer_display": "行きました", "answer_reading": "いきました"}
    # empty or non-string values are no display form
    context.update(answer_display=" ", answer_reading=3)
    assert lq.reviewed_answer_form(question) == {}


def test_the_display_is_stripped_and_used_only_for_a_japanese_answer(monkeypatch):
    monkeypatch.setattr(furigana, "annotation_spans", lambda question, text: [])
    form = {"answer_display": "  学校\n", "answer_reading": " がっこう "}
    assert furigana.answer_form(use("がっこう"), form=form) == ("学校", [(0, 2, "がっこう")])
    assert furigana.answer_form(use("学校X"), form=form) == ("学校", [(0, 2, "がっこう")])
    assert furigana.answer_form(use("学校"), form={"answer_display": " \n"}) == ("学校", [])
    # a meaning question keeps its Chinese answer, and nothing is looked up for it
    monkeypatch.setattr(furigana, "lookup_answer_form", lambda question: pytest.fail("not looked up"))
    for ability, kind in (("understanding", "vocab_meaning"), ("listening", "vocab_audio_meaning")):
        meaning = {"target_id": "none", "kind": kind, "ability": ability, "answer": "学校"}
        assert furigana.answer_form(meaning, form={"answer_display": "がっこう"}) == ("学校", [])
        assert furigana.answer_form(meaning) == ("学校", [])


# Without a standard spelling the readings above the answer are checked by the content's own kana forms
# (its all-kana accepted answers and answer_reading): reading_form_answers.shown_readings.

def accepted(answer, *others):
    return dict(use(answer), accepted_answers=[answer, *others])


def test_a_reading_the_content_kana_contradicts_is_replaced_by_the_content_reading(monkeypatch):
    # 着いて read き (from 着る) above the answer; the content lists えきについて
    monkeypatch.setattr(furigana, "annotation_spans", lambda question, text: [(0, 1, "えき"), (2, 3, "き")])
    question = accepted("駅に着いて", "えきについて")
    assert furigana.answer_form(question, form={}) == ("駅に着いて", [(0, 1, "えき"), (2, 3, "つ")])
    # answer_reading counts as the content's kana too; punctuation and katakana fold as the judge folds them
    monkeypatch.setattr(furigana, "annotation_spans", lambda question, text: [(0, 1, "く")])
    assert furigana.answer_form(use("来ない。"), form={"answer_reading": "こない"}) == ("来ない。", [(0, 1, "こ")])
    monkeypatch.setattr(furigana, "annotation_spans", lambda question, text: [(7, 8, "ちい"), (15, 16, "つ")])
    question = accepted("このセーターは小さいから、もう着ない", "このせーたーはちいさいからもうきない")
    assert furigana.answer_form(question, form={})[1] == [(7, 8, "ちい"), (15, 16, "き")]


def test_a_reading_is_added_where_none_was_shown_and_confirmed_readings_stay(monkeypatch):
    monkeypatch.setattr(furigana, "annotation_spans", lambda question, text: [(0, 2, "がっこう")])
    question = accepted("学校へ行きます", "がっこうへいきます")
    assert furigana.answer_form(question, form={}) == ("学校へ行きます", [(0, 2, "がっこう"), (3, 4, "い")])
    # a confirmed split of one kanji run (会社 + 員) is kept as it is, nothing added inside that run
    monkeypatch.setattr(furigana, "annotation_spans", lambda question, text: [(0, 2, "かいしゃ"), (2, 3, "いん")])
    question = accepted("会社員です", "かいしゃいんです")
    assert furigana.answer_form(question, form={}) == ("会社員です", [(0, 2, "かいしゃ"), (2, 3, "いん")])
    assert furigana.answer_cells(question, form={}) == [("会社", "かいしゃ"), ("員", "いん"), ("です", "")]


def test_without_a_kana_form_of_the_answer_the_readings_are_todays(monkeypatch):
    shown = [(0, 1, "き")]  # wrong, but nothing in the content says so
    monkeypatch.setattr(furigana, "annotation_spans", lambda question, text: shown)
    assert furigana.answer_form(accepted("着いて"), form={}) == ("着いて", shown)
    # a kana form of another sentence neither confirms nor contradicts this one
    assert furigana.answer_form(accepted("着いて", "ついた"), form={}) == ("着いて", shown)
    # another answer than the question's own (the entered answer) is never checked
    assert furigana.answer_form(accepted("駅に着いて", "えきについて"), "着いて") == ("着いて", shown)


def test_the_kana_forms_align_with_the_text_or_not_at_all():
    from kanamemory import learning_questions as questions
    from kanamemory import reading_form_answers as rfa
    normal = questions._reading_normal
    assert rfa.align("学校へ行きます。", "がっこうへいきます", normal) == [(0, 2, "がっこう"), (3, 4, "い")]
    assert rfa.align("学校へ行きます", "がっこうにいきます", normal) is None
    assert rfa.align("がっこう", "がっこう", normal) is None  # no kanji: nothing to read
    assert rfa.shown_readings("学校", [(0, 2, "がくこう")], [], normal) == [(0, 2, "がくこう")]
