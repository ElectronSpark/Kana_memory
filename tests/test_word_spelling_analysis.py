"""Word spelling diagnoses specific kana without inventing memory evidence."""
import json
from pathlib import Path
import sys
import unicodedata

import pytest

from kanamemory.word_spelling import MAX_SPELLING_LENGTH, analyze_spelling, spelling_feedback


def katakana(value):
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in value)


ROWS = [{"id": f"kana-{symbol}", "hiragana": symbol, "katakana": katakana(symbol)}
        for symbol in ["あ", "い", "う", "お", "か", "が", "き", "こ", "つ", "て", "と", "は", "ば", "ぱ", "や", "よ", "ん", "きゃ", "きゅ", "きょ", "てぃ"]]


def analyze(expected, actual, **kwargs):
    return analyze_spelling(expected, actual, "hiragana", ROWS, **kwargs)


def units(result):
    return {unit["symbol"]: unit for unit in result["units"]}


def kinds(result):
    return {error["type"] for error in result["errors"]}


def test_exact_word_awards_once_per_catalog_unit():
    result = analyze("あきゃあ", "あきゃあ")
    assert result["correct"]
    assert set(units(result)) == {"あ", "きゃ"}
    assert all(unit["result"] == "correct" for unit in result["units"])
    assert result["errors"] == []
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result


@pytest.mark.parametrize("expected,actual,error", [
    ("か", "が", "voicing"), ("ば", "ぱ", "voicing"),
    ("つ", "っ", "size"), ("きゃ", "きや", "size"),
    ("てぃ", "てい", "size"), ("か", "カ", "script"),
    ("きょ", "きゃ", "substitution"), ("お", "う", "substitution"),
])
def test_precise_error_types(expected, actual, error):
    result = analyze(expected, actual)
    assert not result["correct"]
    assert kinds(result) == {error}
    if error == "script":
        assert result["units"] == []
        assert result["orthography_units"][0]["error_types"] == ["script"]
        return
    assert units(result)[expected]["result"] == "incorrect"
    assert units(result)[expected]["error_types"] == [error]


def test_split_contraction_never_awards_base_kana():
    result = analyze("きゃと", "きやと")
    assert set(units(result)) == {"きゃ", "と"}
    assert units(result)["きゃ"]["result"] == "incorrect"
    assert units(result)["と"]["result"] == "correct"


def test_missing_small_kana_penalizes_group_and_explains_missing_piece():
    result = analyze("きゃ", "き")
    assert kinds(result) == {"missing"}
    assert units(result)["きゃ"]["result"] == "incorrect"
    assert "漏写「ゃ」" in spelling_feedback(result)[0]


def test_inserted_small_kana_does_not_penalize_correct_base():
    result = analyze("きと", "きゃと")
    assert kinds(result) == {"extra"}
    assert all(unit["result"] == "correct" for unit in result["units"])
    assert "多写「ゃ」" in spelling_feedback(result)[0]


def test_reverse_size_error_only_affects_changed_character():
    result = analyze("きや", "きゃ")
    assert units(result)["き"]["result"] == "correct"
    assert units(result)["や"]["result"] == "incorrect"
    assert units(result)["や"]["error_types"] == ["size"]


def test_long_vowels_remain_separate_kana():
    result = analyze("こう", "こお")
    assert units(result)["こ"]["result"] == "correct"
    assert units(result)["う"]["result"] == "incorrect"


@pytest.mark.parametrize("expected,actual,kind", [("あか", "あ", "missing"), ("あか", "あいか", "extra"), ("あか", "かあ", "order")])
def test_missing_extra_and_adjacent_transposition(expected, actual, kind):
    assert kind in kinds(analyze(expected, actual))


def test_extra_character_does_not_penalize_neighbors_or_invent_evidence():
    result = analyze("あか", "あいか")
    assert set(units(result)) == {"あ", "か"}
    assert all(unit["result"] == "correct" for unit in result["units"])


def test_transposition_penalizes_both_involved_units():
    result = analyze("あかと", "かあと")
    assert units(result)["あ"]["error_types"] == ["order"]
    assert units(result)["か"]["error_types"] == ["order"]
    assert units(result)["と"]["result"] == "correct"


def test_sokuon_and_long_mark_errors_visible_without_fake_ids():
    missing = analyze("きゃっと", "きゃと")
    long = analyze_spelling("カー", "カ", "katakana", ROWS)
    assert any(error["expected"] == "っ" and error["type"] == "missing" for error in missing["errors"])
    assert any(error["expected"] == "ー" and error["type"] == "missing" for error in long["errors"])
    assert set(units(missing)) == {"きゃ", "と"}
    assert set(units(long)) == {"カ"}


def test_explicit_catalog_marks_are_supported():
    rows = ROWS + [{"id": "small-tsu", "hiragana": "っ", "katakana": "ッ"}]
    result = analyze_spelling("かっと", "かと", "hiragana", rows)
    assert units(result)["っ"]["content_id"] == "small-tsu"
    assert units(result)["っ"]["result"] == "incorrect"


def test_repeated_symbol_error_wins_and_is_counted_once():
    result = analyze("あかあ", "あか")
    assert units(result)["あ"]["result"] == "incorrect"
    assert len(result["units"]) == 2


def test_ambiguous_repeated_occurrence_is_still_certain_error_for_same_id():
    result = analyze("ああ", "あ")
    assert result["ambiguous"]
    assert units(result)["あ"]["result"] == "incorrect"
    assert units(result)["あ"]["error_types"] == ["missing"]


def test_ambiguous_different_ids_are_not_arbitrarily_penalized():
    # Two substitutions, or missing あ / matching い / extra う.
    result = analyze("あい", "いう")
    assert result["ambiguous"]
    assert units(result)["あ"]["result"] == "incorrect"
    assert "い" not in units(result)
    assert any("多种可能" in line for line in spelling_feedback(result))


def test_assisted_answer_has_feedback_but_no_evidence():
    result = analyze("か", "が", assisted=True)
    assert result["units"] == []
    assert kinds(result) == {"voicing"}
    assert any("提示" in line for line in spelling_feedback(result))


def test_revealed_empty_answer_does_not_tell_user_to_enter_an_answer():
    result = analyze("か", "", assisted=True)
    assert result["units"] == []
    assert result["errors"] == []
    assert len(spelling_feedback(result)) == 1
    assert "提示" in spelling_feedback(result)[0]


def test_normalization_matches_grader_without_erasing_internal_space_or_halfwidth():
    assert analyze("が", unicodedata.normalize("NFD", "が"))["correct"]
    assert analyze("か", " か \n")["correct"]
    assert not analyze("かか", "か か")["correct"]
    assert not analyze_spelling("カ", "ｶ", "katakana", ROWS)["correct"]


def test_mixed_size_voicing_and_script_are_all_diagnosed():
    result = analyze("きゃ", "ギヤ")
    assert kinds(result) == {"script", "voicing", "size"}
    assert units(result)["きゃ"]["result"] == "incorrect"


def test_ambiguous_catalog_symbol_does_not_get_arbitrary_id():
    rows = ROWS + [{"id": "other-a", "hiragana": "あ", "katakana": "ア"}]
    assert analyze_spelling("あ", "あ", "hiragana", rows)["units"] == []


def test_unknown_contraction_does_not_award_its_base():
    assert analyze("かゃ", "かゃ")["units"] == []


def test_length_limit_prevents_large_edit_matrix():
    result = analyze("あ" * (MAX_SPELLING_LENGTH + 1), "あ")
    assert result["evidence_limited"]
    assert result["units"] == []
    assert kinds(result) == {"length"}


def test_many_repeated_kana_use_bounded_dag_not_path_enumeration():
    result = analyze("あ" * 80, "あ" * 40)
    assert result["ambiguous"]
    assert len(result["units"]) == 1
    assert units(result)["あ"]["result"] == "incorrect"


def test_empty_expected_or_actual():
    assert analyze("", "")["correct"]
    assert kinds(analyze("", "あ")) == {"extra"}
    assert kinds(analyze("あ", "")) == {"empty"}
    assert analyze("あ", "")["units"] == []
    assert analyze("あ", " \n ")["units"] == []


def test_script_validation():
    with pytest.raises(ValueError):
        analyze_spelling("あ", "あ", "romaji", ROWS)
    with pytest.raises(TypeError):
        analyze_spelling("あ", None, "hiragana", ROWS)
    with pytest.raises(TypeError):
        analyze_spelling("あ", "あ", "hiragana", ["bad-row"])
    with pytest.raises(TypeError):
        analyze_spelling("あ", "あ", "hiragana", None)
