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
    assert rfa.compile_form("いきます", [], normal) == (("いきます",),)


def test_a_standard_spelling_read_from_a_kana_answer():
    spans = result_readings.word_spans("学校へ行きます", "がっこうへいきます")
    assert spans == [(0, 2, "がっこう"), (3, 4, "い")]
    for typed in ("学校へ行きます", "学校へいきます", "がっこうへ行きます"):
        assert rfa.matches(normal(typed), "学校へ行きます", spans, normal)


def test_spans_are_cleaned_and_a_long_answer_is_walked_in_one_pass():
    assert rfa.clean_spans("学校へ", [(0, 2, "がっこう"), (1, 2, "x"), (2, 9, "y"), (2, 2, "z"), (0, 1, "")]) == [
        (0, 2, "がっこう")]
    assert rfa.clean_spans("いま", [(0, 2, "いま")]) == []  # a span that is already its reading
    text = "".join("学校へ" for _ in range(30))
    spans = [(3 * i, 3 * i + 2, "がっこう") for i in range(30)]
    pieces = rfa.compile_form(text, spans, normal)
    assert len(pieces) == 60  # 30 spans of two choices, 30 fixed pieces: no spelling is listed
    typed = "がっこうへ" + "学校へ" * 28 + "がっこうへ"
    assert rfa.walk(normal(typed), pieces)
    assert not rfa.walk(normal(typed.replace("学校", "学港", 1)), pieces)
    # a prefix of the answer, or the answer with more after it, is not the answer
    assert not rfa.walk(normal(typed[:-5]), pieces)
    assert not rfa.walk(normal("学校へ" * 29), pieces)
    assert not rfa.walk(normal(typed + "がっこうへ"), pieces)


def test_a_span_is_typed_exactly_as_written_or_exactly_as_its_kana_even_where_the_old_fold_reads_it():
    # the grammar judge folds 本 to ほん for its exact comparison; a reading form never uses that fold
    text, spans = "将来は日本で働きたいです", [(0, 2, "しょうらい"), (3, 5, "にほん"), (6, 7, "はたら")]
    assert rfa.matches(normal("将来はにほんで働きたいです"), text, spans, normal)
    assert not rfa.matches(normal("将来はに本で働きたいです"), text, spans, normal)
    assert not rfa.matches(normal("将来は日ほんで働きたいです"), text, spans, normal)


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
    # an unread run stands for one kana or more, never for nothing
    assert not rfa.agrees("駅に着いて", [(2, 3, "つ")], ["について"], normal)
    assert rfa.agrees("駅に着いて", [(2, 3, "つ")], ["えきについて"], normal)


def test_a_span_that_touches_an_unread_kanji_is_not_confirmed():
    assert not rfa.agrees("日本語です", [(0, 2, "に")], ["にほんごです"], normal)
    assert not rfa.agrees("日本語です", [(0, 2, "にほん")], ["にほんごです"], normal)
    assert rfa.agrees("日本語です", [(0, 2, "にほん"), (2, 3, "ご")], ["にほんごです"], normal)
    assert rfa.agrees("日本語です", [(0, 3, "にほんご")], ["にほんごです"], normal)


def test_a_reading_is_certain_only_when_one_split_fits():
    certain = rfa.certain_readings
    # the confirmed reading is an anchor: 数学 can only be すうがく (not すう before the が of すうがく)
    assert certain("数学が得意です", [(3, 5, "とくい")], "すうがくがとくいです", normal) == [(0, 2, "すうがく")]
    # the kana repeats: 他人 たにん or た, 話 はな or んにはな; neither is certain
    assert certain("他人に話した", [], "たにんにはなした", normal) == []
    assert certain("他人に話した", [(0, 2, "たにん")], "たにんにはなした", normal) == [(3, 4, "はな")]
    # punctuation separates runs: 反面、少し has two runs side by side in the kana; neither is certain
    assert certain("反面、少し", [], "はんめんすこし", normal) == []
    assert certain("反面、少し", [(3, 4, "すこ")], "はんめんすこし", normal) == [(0, 2, "はんめん")]
    # a kana form that is not a reading of the text: None
    assert certain("学校へ行きます", [], "がっこうにいきます", normal) is None
    assert certain("練習し直しました", [(0, 2, "れんしゅう")], "れんしゅうしなおしました", normal) == [(3, 4, "なお")]


def test_added_readings_never_disagree_with_the_given_ones():
    shown = rfa.shown_readings
    # given and confirmed: kept, the rest added only where certain
    assert shown("数学が得意です", [(3, 5, "とくい")], ["すうがくがとくいです"], normal) == [
        (0, 2, "すうがく"), (3, 5, "とくい")]
    assert shown("他人に話した", [(0, 2, "たにん")], ["たにんにはなした"], normal) == [(0, 2, "たにん"), (3, 4, "はな")]
    assert shown("他人に話した", [], ["たにんにはなした"], normal) == []
    # contradicted: replaced where the kana form fixes the reading, else removed
    assert shown("駅に着いて", [(0, 1, "えき"), (2, 3, "き")], ["えきについて"], normal) == [(0, 1, "えき"), (2, 3, "つ")]
    assert shown("他人に話した", [(3, 4, "は")], ["たにんにはなした"], normal) == []
    # no kana form of this text: exactly as given
    given = [(0, 1, "き"), (9, 9, "x")]
    assert shown("着いて", given, ["ついた"], normal) is not given and shown("着いて", given, ["ついた"], normal) == given


READINGS = {"学校へ行きます": "がっこうへいきます", "学校へ行く": "がっこうへいく"}


@pytest.fixture(autouse=True)
def fresh_reading_forms():
    """Each test gives its own readings: the judge's small cache of compiled forms starts empty."""
    lq._READING_FORMS.clear()
    yield
    lq._READING_FORMS.clear()


def spans_of(table):
    return lambda question: lambda text: table.get(text, [])


@pytest.fixture
def spans_given(monkeypatch):
    """The result page's readings for exactly these texts; the content's reading of the answer as written."""
    known = {"学校へ行きます": [(0, 2, "がっこう"), (3, 4, "い")], "学校へ行く": [(0, 2, "がっこう"), (3, 4, "い")]}
    monkeypatch.setattr(result_readings, "annotation_reader", spans_of(known))
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


def test_a_question_whose_answers_changed_never_uses_the_old_forms(spans_given):
    # the same question id after new content was installed in the running app: other accepted answers
    spans_given["学校に行きます"] = [(0, 2, "がっこう"), (3, 4, "い")]
    before = question("grammar_transform", accepted_answers=["学校へ行きます", "学校に行きます", "がっこうにいきます"])
    assert status(before, "がっこうに行きます") == "correct"
    after = question("grammar_transform", accepted_answers=["学校へ行きます"])
    assert status(after, "がっこうに行きます") == "unresolved"  # only the old forms accepted it
    assert status(after, "がっこうへ行きます") == "correct"
    assert len(lq._READING_FORMS) == 2


def test_accepted_answers_and_known_wrong_work_as_before(spans_given):
    q = question("grammar_transform", accepted_answers=["学校へ行きます", "学校に行きます"],
                 known_wrong=["がっこうへ行きます"])
    assert status(q, "学校に行きます") == "correct"  # a listed answer, no span known for it
    assert status(q, "がっこうにいきます") == "unresolved"  # no readings known for that text
    assert status(q, "がっこうへいきます") == "correct"
    assert status(q, "がっこうへ行きます") == "incorrect"  # a reviewed known-wrong answer is never widened


def test_readings_the_content_does_not_confirm_are_not_used(monkeypatch):
    wrong = {"駅に着いて": [(0, 1, "えき"), (2, 3, "き")], "学校へ行きます": [(0, 2, "がっこう"), (3, 4, "い")]}
    monkeypatch.setattr(result_readings, "annotation_reader", spans_of(wrong))
    monkeypatch.setattr(lq, "reviewed_answer_form", lambda question: {})
    q = question("grammar_transform", "駅に着いて", accepted_answers=["駅に着いて", "えきについて"])
    assert status(q, "えきについて") == "correct"  # listed, as before
    assert status(q, "えきにきいて") == "unresolved"  # the contradicted reading is never accepted
    assert status(q, "えきに着いて") == "unresolved"  # its spans are dropped: typed as it stands
    # no kana answer at all: nothing confirms the spans
    assert status(question("grammar_transform"), "がっこうへいきます") == "unresolved"


def test_the_judge_never_applies_the_single_kanji_fold_to_a_reading_form(monkeypatch):
    answer = "将来は日本で働きたいです"
    spans = {answer: [(0, 2, "しょうらい"), (3, 5, "にほん"), (6, 7, "はたら")]}
    monkeypatch.setattr(result_readings, "annotation_reader", spans_of(spans))
    monkeypatch.setattr(lq, "reviewed_answer_form", lambda question: {})
    real_fold = lq._grammar_kana
    monkeypatch.setattr(lq, "_grammar_kana", lambda text: real_fold(text).replace("本", "ほん"))
    q = question("controlled_sentence_production", answer,
                 accepted_answers=[answer, "しょうらいはにほんではたらきたいです"])
    assert status(q, "将来はにほんで働きたいです") == "correct"
    assert status(q, "しょうらいは日本ではたらきたいです") == "correct"
    assert status(q, "将来はに本で働きたいです") == "unresolved"  # half a word
    # the exact comparison keeps the fold as before
    q = question("controlled_sentence_production", "日本です", accepted_answers=["日本です"])
    assert status(q, "日ほんです") == "correct"


def test_the_judge_without_reading_forms_is_the_earlier_judge(spans_given):
    q = question("grammar_transform")
    assert lq.grade_learning_answer(q, "学校へいきます")["status"] == "correct"
    assert lq.grade_learning_answer(q, "学校へいきます", reading_forms=False)["status"] == "unresolved"
    assert lq.grade_learning_answer(q, "学校へ行きます", reading_forms=False)["status"] == "correct"


def test_the_standard_spelling_of_a_kana_answer(monkeypatch):
    monkeypatch.setattr(result_readings, "annotation_reader", spans_of({}))
    monkeypatch.setattr(lq, "reviewed_answer_form", lambda question: {"answer_display": " 学校へ行きます "})
    q = question("grammar_cloze", "がっこうへいきます")
    for typed in ("がっこうへいきます", "学校へ行きます", "学校へいきます", "がっこうへ行きます"):
        assert status(q, typed) == "correct"
    assert status(q, "学港へいきます") == "unresolved"
    # with a kanji answer the display is read from answer_reading; without it, typed as it stands
    monkeypatch.setattr(lq, "reviewed_answer_form", lambda question: {
        "answer_display": "学校へ行きます", "answer_reading": "がっこうへいきます"})
    assert status(question("grammar_cloze", "学校へ行きます。"), "学校へいきます") == "correct"
    lq._READING_FORMS.clear()  # the same question with other content: built again
    monkeypatch.setattr(lq, "reviewed_answer_form", lambda question: {"answer_display": "学校へ行きます"})
    assert status(question("grammar_cloze", "学校へ行きます。"), "学校へいきます") == "unresolved"
    assert status(question("grammar_cloze", "学校へ行きます。"), "学校へ行きます") == "correct"
