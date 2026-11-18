"""A typed answer written with its readings is the same answer (reading_form_answers; owner design 2026-11-18).

Pure matcher tests, then the judge per kind with the reading spans given directly (no content lookup).
"""
import pytest

from kanamemory import learning_questions as lq
from kanamemory import reading_form_answers as rfa
from kanamemory import result_readings

normal = lq._normal

GAKKOU = ("学校へ行きます", [(0, 2, "がっこう"), (3, 4, "い")])


@pytest.mark.parametrize("typed, expected", [
    ("学校へ行きます", True),
    ("がっこうへいきます", True),
    ("学校へいきます", True),  # one span as written, the other as its reading
    ("がっこうへ行きます", True),
    ("がっこうへいきます。", True),  # punctuation and spaces as today's normalisation drops them
    (" がっこう へ いきます ", True),
    ("ｶﾞｯｺｳへいきます", False),  # NFKC gives katakana; the reading is hiragana and normal keeps scripts
    ("学港へいきます", False),  # wrong kanji with the right reading
    ("がっ校へいきます", False),  # nothing is read from what was typed: a span is all surface or all kana
    ("学校にいきます", False),
    ("", False),
])
def test_several_spans_each_written_or_read(typed, expected):
    text, spans = GAKKOU
    assert rfa.matches(normal(typed), text, spans, normal) is expected


def test_okurigana_and_a_kanji_run_without_reading():
    # 食べます: the span is the kanji alone, its okurigana stays as written
    assert rfa.matches(normal("たべます"), "食べます", [(0, 1, "た")], normal)
    assert not rfa.matches(normal("食たべます"), "食べます", [(0, 1, "た")], normal)
    # 駅 has no known reading here: it must be typed as it stands
    text, spans = "駅で食べます", [(2, 3, "た")]
    assert rfa.matches(normal("駅でたべます"), text, spans, normal)
    assert not rfa.matches(normal("えきでたべます"), text, spans, normal)


def test_an_all_kana_answer_has_no_other_spelling():
    assert rfa.matches(normal("いきます"), "いきます", [], normal)
    assert not rfa.matches(normal("行きます"), "いきます", [], normal)
    assert list(rfa.spellings("いきます", [])) == ["いきます"]


def test_a_standard_spelling_read_from_a_kana_answer():
    spans = result_readings.word_spans("学校へ行きます", "がっこうへいきます")
    assert spans == [(0, 2, "がっこう"), (3, 4, "い")]
    for typed in ("学校へ行きます", "学校へいきます", "がっこうへ行きます"):
        assert rfa.matches_any(normal(typed), [("がっこうへいきます", []), ("学校へ行きます", spans)], normal)


def test_spans_are_cleaned_and_many_spans_are_matched_piece_by_piece():
    assert rfa.clean_spans("学校へ", [(0, 2, "がっこう"), (1, 2, "x"), (2, 9, "y"), (2, 2, "z"), (0, 1, "")]) == [
        (0, 2, "がっこう")]
    assert rfa.clean_spans("いま", [(0, 2, "いま")]) == []  # a span that is already its reading
    text = "".join("学校へ" for _ in range(14))
    spans = [(3 * i, 3 * i + 2, "がっこう") for i in range(14)]
    assert 2 ** 14 > rfa.MAX_SPELLINGS
    typed = "がっこうへ" + "学校へ" * 12 + "がっこうへ"
    assert rfa.matches(normal(typed), text, spans, normal)
    assert not rfa.matches(normal(typed.replace("学校", "学港", 1)), text, spans, normal)


@pytest.mark.parametrize("question, expected", [
    ({"kind": "grammar_cloze", "family": "grammar", "input_mode": "text"}, True),
    ({"kind": "grammar_transform", "family": "grammar", "input_mode": "text"}, True),
    ({"kind": "targeted_correction", "family": "grammar", "input_mode": "text"}, True),
    ({"kind": "controlled_sentence_production", "family": "grammar", "input_mode": "text"}, True),
    ({"kind": "vocab_recall", "family": "word", "input_mode": "text",
      "practice": {"task_family": "expression_recall"}}, True),
    ({"kind": "vocab_recall", "family": "word", "input_mode": "text",
      "practice": {"task_family": "word_form_recall"}}, False),
    ({"kind": "vocab_recall", "family": "word", "input_mode": "text"}, False),
    ({"kind": "vocab_reading", "family": "word", "input_mode": "text"}, False),
    ({"kind": "vocab_usage", "family": "word", "input_mode": "text"}, False),
    ({"kind": "grammar_reorder", "family": "grammar", "input_mode": "reorder"}, False),
    ({"kind": "grammar_cloze", "family": "grammar", "input_mode": "choice"}, False),
    ({"kind": "grammar_cloze", "family": "grammar", "input_mode": "text", "domain": "pronunciation"}, False),
    (None, False),
])
def test_which_questions_it_applies_to(question, expected):
    assert rfa.applies(question) is expected


def test_spans_are_used_only_when_the_content_kana_answer_agrees():
    text, spans = GAKKOU
    assert rfa.agrees(text, spans, ["がっこうへいきます"], normal)
    assert rfa.agrees(text, spans, ["ガッコウへいきます", "がっこうにいきます"], lq._reading_normal)
    assert not rfa.agrees(text, spans, [], normal)  # nothing to confirm them with
    assert rfa.agrees(text, [], [], normal)  # no spans: nothing to confirm
    # a result-page reading taken from another form of the verb (着る) is contradicted by the kana answer
    assert not rfa.agrees("駅に着いて", [(0, 1, "えき"), (2, 3, "き")], ["えきについて"], normal)
    # a kanji run without a reading stands for any kana; the spans around it must still agree
    assert rfa.agrees("駅に着いて", [(0, 1, "えき")], ["えきについて"], normal)
    assert not rfa.agrees("駅に着いて", [(0, 1, "えい")], ["えきについて"], normal)


READINGS = {"学校へ行きます": "がっこうへいきます", "学校へ行く": "がっこうへいく"}


@pytest.fixture
def spans_given(monkeypatch):
    """The result page's readings for exactly these texts; the content's reading of the answer as written."""
    known = {"学校へ行きます": [(0, 2, "がっこう"), (3, 4, "い")], "学校へ行く": [(0, 2, "がっこう"), (3, 4, "い")]}
    monkeypatch.setattr(result_readings, "annotation_spans", lambda question, text: known.get(text, []))
    monkeypatch.setattr(lq, "reviewed_answer_form", lambda question: {
        "answer_display": question["answer"], "answer_reading": READINGS[question["answer"]]}
        if question["answer"] in READINGS else {})
    return known


def question(kind, answer="学校へ行きます", *, strict=False, family="grammar", **extra):
    return {"id": "t", "question_id": "t", "target_id": "none", "kind": kind, "family": family,
            "input_mode": "text", "prompt": "（　）", "answer": answer, "accepted_answers": [answer], "strict": strict,
            "feedback": "ok", **extra}


def status(q, typed):
    return lq.grade_learning_answer(q, typed)["status"]


@pytest.mark.parametrize("kind", sorted(rfa.GRAMMAR_TEXT_KINDS))
@pytest.mark.parametrize("strict", [False, True])
def test_a_typed_grammar_kind_accepts_the_reading_form(spans_given, kind, strict):
    q = question(kind, strict=strict)
    for typed in ("学校へ行きます", "がっこうへいきます", "学校へいきます。", "がっこうへ行きます"):
        assert status(q, typed) == "correct"
    # a plainly wrong answer keeps today's verdict: incorrect when strict, unresolved otherwise
    assert status(q, "学港へいきます") == ("incorrect" if strict else "unresolved")
    assert status(q, "がっこうにいきます") == ("incorrect" if strict else "unresolved")


def test_an_expression_recall_accepts_the_reading_form(spans_given):
    q = question("vocab_recall", "学校へ行く", family="word", practice={"task_family": "expression_recall"})
    assert status(q, "がっこうへいく") == "correct"
    assert status(q, "学校へいく") == "correct"
    plain = question("vocab_recall", "学校へ行く", family="word", practice={"task_family": "word_form_recall"})
    assert status(plain, "学校へいく") == "unresolved"


@pytest.mark.parametrize("kind, family", [("vocab_reading", "word"), ("vocab_usage", "word"),
                                          ("vocab_recall", "word")])
def test_an_excluded_kind_does_not(spans_given, kind, family):
    q = question(kind, family=family)
    assert status(q, "学校へいきます") != "correct"
    assert status(q, "学校へ行きます") == "correct"


def test_accepted_answers_and_known_wrong_work_as_before(spans_given):
    q = question("grammar_transform", accepted_answers=["学校へ行きます", "学校に行きます"],
                 known_wrong=["がっこうへ行きます"])
    assert status(q, "学校に行きます") == "correct"  # a listed answer, no span known for it
    assert status(q, "がっこうにいきます") == "unresolved"  # no readings known for that text
    assert status(q, "がっこうへいきます") == "correct"
    assert status(q, "がっこうへ行きます") == "incorrect"  # a reviewed known-wrong answer is never widened


def test_readings_the_content_does_not_confirm_are_not_used(monkeypatch):
    wrong = {"駅に着いて": [(0, 1, "えき"), (2, 3, "き")], "学校へ行きます": [(0, 2, "がっこう"), (3, 4, "い")]}
    monkeypatch.setattr(result_readings, "annotation_spans", lambda question, text: wrong.get(text, []))
    monkeypatch.setattr(lq, "reviewed_answer_form", lambda question: {})
    q = question("grammar_transform", "駅に着いて", accepted_answers=["駅に着いて", "えきについて"])
    assert status(q, "えきについて") == "correct"  # listed, as before
    assert status(q, "えきにきいて") == "unresolved"  # the contradicted reading is never accepted
    assert status(q, "えきに着いて") == "unresolved"  # its spans are dropped: typed as it stands
    # no kana answer at all: nothing confirms the spans
    assert status(question("grammar_transform"), "がっこうへいきます") == "unresolved"


def test_the_standard_spelling_of_a_kana_answer(monkeypatch):
    monkeypatch.setattr(result_readings, "annotation_spans", lambda question, text: [])
    monkeypatch.setattr(lq, "reviewed_answer_form", lambda question: {"answer_display": " 学校へ行きます "})
    q = question("grammar_cloze", "がっこうへいきます")
    for typed in ("がっこうへいきます", "学校へ行きます", "学校へいきます", "がっこうへ行きます"):
        assert status(q, typed) == "correct"
    assert status(q, "学港へいきます") == "unresolved"
    # with a kanji answer the display is read from answer_reading; without it, typed as it stands
    monkeypatch.setattr(lq, "reviewed_answer_form", lambda question: {
        "answer_display": "学校へ行きます", "answer_reading": "がっこうへいきます"})
    assert status(question("grammar_cloze", "学校へ行きます。"), "学校へいきます") == "correct"
    monkeypatch.setattr(lq, "reviewed_answer_form", lambda question: {"answer_display": "学校へ行きます"})
    assert status(question("grammar_cloze", "学校へ行きます。"), "学校へいきます") == "unresolved"
    assert status(question("grammar_cloze", "学校へ行きます。"), "学校へ行きます") == "correct"
