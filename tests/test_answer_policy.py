"""answer_policy: the answer overlay format, the one effective policy and the kanji-numeral fold (design 03).

Pure rules on made-up questions; no course content.
"""
import pytest

from kanamemory.answer_policy import overlay
from kanamemory.answer_policy.numbers import fold_numbers

SHA = "b" * 64
QUESTION = {"target_id": "grammar:demo:te", "kind": "grammar_transform", "answer": "たべて",
            "accepted_answers": ["たべて", "食べて"], "strict": False, "known_wrong": ["たべって"],
            "feedback": "て形：たべる → たべて"}


@pytest.mark.parametrize("text, folded", [
    ("二十三", "23"), ("三百", "300"), ("一万", "10000"), ("十", "10"), ("百二十", "120"),
    ("千二百三十四", "1234"), ("一万二千", "12000"), ("十万", "100000"),
    ("二〇二六", "2026"), ("一", "1"), ("〇", "0"), ("三匹", "3匹"), ("二十三人です", "23人です"),
])
def test_well_formed_numerals_and_digit_runs_fold_into_ascii_digits(text, folded):
    assert fold_numbers(text) == folded


@pytest.mark.parametrize("text", ["万", "十二十", "百百", "二百十千", "万万", "〇十"])
def test_runs_that_are_not_well_formed_numbers_stay_as_they_are(text):
    assert fold_numbers(text) == text


def test_the_fold_never_infers_a_reading_and_leaves_digits_and_kana_alone():
    assert fold_numbers("2さつ") == "2さつ" and fold_numbers("にさつ") == "にさつ"
    assert fold_numbers("二さつ") == "2さつ" != fold_numbers("にさつ")
    assert fold_numbers("2026年") == "2026年" and fold_numbers(None) is None


def test_a_row_takes_strict_both_ways_and_adds_answers_after_the_templates():
    tightened = overlay.effective(QUESTION, {"strict": True, "accepted": ["食べて下さい"]})
    assert tightened.strict is True and tightened.changed
    assert tightened.accepted == ("たべて", "食べて", "食べて下さい")
    loosened = overlay.effective(dict(QUESTION, strict=True), {"strict": False})
    assert loosened.strict is False and loosened.accepted == ("たべて", "食べて")
    assert overlay.effective(dict(QUESTION, strict=True), None).strict is True


def test_known_wrong_answers_keep_their_feedback_and_the_template_list_first():
    policy = overlay.effective(QUESTION, {"known_wrong": [{"answer": "たべた", "feedback": "这是过去形。"}]})
    assert policy.known_wrong == ("たべって", "たべた")
    assert policy.row_feedback == (("たべた", "这是过去形。"),)
    assert overlay.row_feedback(policy, "たべた", str) == "这是过去形。"
    assert overlay.row_feedback(policy, "たべって", str) == ""


def test_without_a_row_nothing_changes_and_the_view_is_the_question_itself():
    policy = overlay.effective(QUESTION, None)
    assert not policy.changed and policy.fold is False and policy.row is None
    assert overlay.view(QUESTION, policy) is QUESTION
    view = overlay.view(QUESTION, overlay.effective(QUESTION, {"strict": True}))
    assert view is not QUESTION and view["strict"] is True and QUESTION["strict"] is False


def test_the_fold_mark_changes_no_answer():
    policy = overlay.effective(QUESTION, {"fold": True})
    assert policy.fold is True and not policy.changed and overlay.view(QUESTION, policy) is QUESTION


def test_t8_the_lesson_reading_is_accepted_only_for_the_lessons_own_written_form():
    lesson = overlay.LessonForms(written=("学校", "學校"), reading="がっこう")
    recall = {"kind": "vocab_recall", "answer": "学校", "accepted_answers": ["学校"]}
    assert overlay.effective(recall, None, lesson=lesson).accepted == ("学校", "がっこう")
    spelling = dict(recall, answer="學校", accepted_answers=["學校"])
    assert "がっこう" in overlay.effective(spelling, None, lesson=lesson).accepted
    other = dict(recall, answer="学校へ行く", accepted_answers=["学校へ行く"])
    assert overlay.effective(other, None, lesson=lesson).accepted == ("学校へ行く",)
    reading_kind = dict(recall, kind="vocab_reading")
    assert overlay.effective(reading_kind, None, lesson=lesson).accepted == ("学校",)


@pytest.mark.parametrize("row", [
    {}, {"strict": 1}, {"fold": False}, {"accepted": []}, {"accepted": [""]}, {"accepted": ["a", "a"]},
    {"known_wrong": [{"answer": "x"}]}, {"known_wrong": [{"answer": "x", "feedback": " "}]},
    {"known_wrong": [{"answer": "x", "feedback": "f"}, {"answer": "x", "feedback": "g"}]},
    {"accepted": ["x"], "known_wrong": [{"answer": "x", "feedback": "f"}]}, {"hint_policy": "fold"},
])
def test_malformed_rows_are_refused(row):
    with pytest.raises(overlay.OverlayError):
        overlay.validate_row(row)


def test_the_namespace_is_keyed_by_content_id_kind_and_template_sha():
    value = {"schema_version": 1, "items": {"grammar:demo:te": {"grammar_transform": {SHA: {"strict": True}}}}}
    assert overlay.validate_namespace(value) is value and overlay.row_count(value) == 1
    for bad in ({"schema_version": 2, "items": {}}, {"items": {}},
                {"schema_version": 1, "items": {"x": {"Kind": {SHA: {"strict": True}}}}},
                {"schema_version": 1, "items": {"x": {"k": {"abc": {"strict": True}}}}}):
        with pytest.raises(overlay.OverlayError):
            overlay.validate_namespace(bad)
    assert overlay.row_count(None) == 0


def test_katakana_folds_to_hiragana_and_keeps_the_long_vowel_mark():
    assert overlay.fold_katakana("ダメ") == "だめ" and overlay.fold_katakana("コーヒー") == "こーひー"
    assert overlay.fold_katakana("ヴァ") == "ゔぁ"
