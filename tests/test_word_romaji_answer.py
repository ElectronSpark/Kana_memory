"""Reverse word choices produce bounded evidence for target kana only."""
import json
from pathlib import Path
import sys
import unicodedata

import pytest

from kanamemory.word_romaji_answer import (
    MAX_ROMAJI_LENGTH, analyze_word_romaji, check_word_romaji_answer,
)
from kanamemory.word_spelling import MAX_SPELLING_LENGTH


def katakana(text):
    return "".join(chr(ord(char) + 0x60) if "ぁ" <= char <= "ゖ" else char for char in text)


ROWS = [{"id": f"kana-{symbol}", "hiragana": symbol, "katakana": katakana(symbol)}
        for symbol in ["あ", "い", "う", "か", "き", "く", "こ", "が", "し", "ひ",
                       "ま", "に", "よ", "ん", "っ", "ちゃ", "きょ", "にゃ", "てぃ"]]


def analyze(expected, actual, script="hiragana", **kwargs):
    return analyze_word_romaji(expected, actual, script, ROWS, **kwargs)


def units(result):
    return {unit["symbol"]: unit for unit in result["units"]}


@pytest.mark.parametrize("kana,answer", [
    ("かき", "kaki"), ("カキ", " KAKI "), ("かき", "ｋａｋｉ"),
    ("しんよう", "shin'you"), ("まっちゃ", "maccha"), ("コーヒー", "kōhī"),
    ("きょう", "kyou"), ("ティー", "tī"), ("にゃ", "nya"),
])
def test_canonical_options_are_accepted(kana, answer):
    assert check_word_romaji_answer(kana, answer)


@pytest.mark.parametrize("kana,answer", [
    ("にゃ", "ni'ya"), ("しんよう", "shinyou"), ("し", "si"),
    ("コーヒー", "koohii"), ("コーヒー", "kohi"), ("こう", "kō"),
    ("かき", "ka ki"), ("かき", "kaka"), ("っ", "xtsu"),
])
def test_other_options_do_not_normalize_away_spelling_errors(kana, answer):
    assert not check_word_romaji_answer(kana, answer)


def test_complete_correct_answer_retains_kana_key_and_nfc_input():
    result = analyze(" コーヒー ", unicodedata.normalize("NFD", " KŌHĪ "), "katakana")
    assert result["correct"]
    assert result["expected"] == "コーヒー"
    assert result["actual"] == "KŌHĪ"
    assert result["answer_direction"] == "romaji"
    assert result["expected_romaji"] == "kōhī"
    assert set(units(result)) == {"コ", "ヒ"}
    assert all(unit["result"] == "correct" for unit in result["units"])
    assert not result["evidence_limited"]
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result


def test_wrong_option_only_changes_target_unit_and_never_invents_others():
    result = analyze("かき", "kaku")
    assert not result["correct"]
    assert set(units(result)) == {"か", "き"}
    assert units(result)["か"]["result"] == "correct"
    assert units(result)["き"]["result"] == "incorrect"
    assert units(result)["き"]["error_types"] == ["substitution"]
    assert all(unit["content_id"] != "kana-く" for unit in result["units"])


def test_last_single_kana_is_mapped_even_after_multi_letter_syllable():
    result = analyze("しかき", "shikaka")
    assert set(units(result)) == {"し", "か", "き"}
    assert units(result)["き"]["result"] == "incorrect"
    assert units(result)["か"]["result"] == "correct"


def test_inside_syllable_insertion_changes_only_that_target():
    result = analyze("かき", "kyaki")
    assert units(result)["か"]["result"] == "incorrect"
    assert units(result)["か"]["error_types"] == ["extra"]
    assert units(result)["き"]["result"] == "correct"


def test_inserted_whole_syllable_does_not_create_its_own_evidence():
    result = analyze("かき", "kakuki")
    assert set(units(result)) <= {"か", "き"}
    assert all(unit["result"] == "correct" for unit in result["units"])


def test_one_wrong_repeated_occurrence_dominates_and_counts_once():
    result = analyze("かか", "kaki")
    assert len(result["units"]) == 1
    assert units(result)["か"]["result"] == "incorrect"


def test_missing_repeated_occurrence_is_still_definite_for_same_id():
    result = analyze("かか", "ka")
    assert result["ambiguous"]
    assert len(result["units"]) == 1
    assert units(result)["か"]["result"] == "incorrect"


def test_missing_sokuon_does_not_guess_between_two_same_consonants():
    result = analyze("まっちゃ", "macha")
    assert result["ambiguous"]
    assert units(result)["ま"]["result"] == "correct"
    assert "っ" not in units(result)
    assert "ちゃ" not in units(result)


def test_missing_apostrophe_targets_nasal_not_neighboring_units():
    result = analyze("しんよう", "shinyou")
    assert units(result)["ん"]["result"] == "incorrect"
    assert all(unit["result"] == "correct" for symbol, unit in units(result).items() if symbol != "ん")


def test_shortened_long_vowels_do_not_arbitrarily_blame_base_kana():
    result = analyze("コーヒー", "kohi", "katakana")
    assert not result["correct"]
    assert result["ambiguous"]
    assert result["units"] == []


def test_different_written_long_vowel_keeps_other_unit_evidence():
    result = analyze("こうき", "kooki")
    assert units(result)["う"]["result"] == "incorrect"
    assert units(result)["こ"]["result"] == "correct"
    assert units(result)["き"]["result"] == "correct"


def test_equivalent_expanded_vowels_never_reward_wrong_canonical_option():
    result = analyze("コー", "koo", "katakana")
    assert not result["correct"]
    assert result["evidence_limited"]
    assert result["units"] == []


@pytest.mark.parametrize("actual", ["kaki", "kaka", ""])
def test_assisted_answers_have_no_memory_evidence(actual):
    result = analyze("かき", actual, assisted=True)
    assert result["assisted"]
    assert result["units"] == []


def test_blank_selection_has_no_memory_evidence():
    result = analyze("かき", "")
    assert not result["correct"]
    assert result["units"] == []
    assert result["errors"][0]["type"] == "empty"


def test_unknown_contraction_does_not_award_its_catalog_base():
    result = analyze("きゃ", "kya")
    assert result["correct"]
    assert result["units"] == []


def test_actual_script_only_is_awarded():
    result = analyze("カキ", "kaki", "katakana")
    assert set(units(result)) == {"カ", "キ"}
    assert analyze("カキ", "kaki", "hiragana")["units"] == []


@pytest.mark.parametrize("expected,actual", [
    ("か" * (MAX_SPELLING_LENGTH + 1), "ka"),
    ("か", "a" * (MAX_ROMAJI_LENGTH + 1)),
    ("か", "ka!?"), ("か", "か"), ("かき", "ka ki"),
])
def test_unreliable_or_oversize_inputs_cannot_adjust_memory(expected, actual):
    result = analyze(expected, actual)
    assert result["evidence_limited"]
    assert result["units"] == []


def test_input_types_and_script_are_checked():
    with pytest.raises(TypeError):
        check_word_romaji_answer("か", None)
    with pytest.raises(TypeError):
        analyze_word_romaji("か", None, "hiragana", ROWS)
    with pytest.raises(ValueError):
        analyze("か", "ka", "latin")
