"""Writing practice filters model guesses without substituting the answer."""
import asyncio
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from kanamemory.domain import Question
from kanamemory.ink_candidates import ink_candidate_kind, practice_ink_candidates
from support.handwriting_model_ui_support import model_app
from support.handwriting_model_ui_support import buttons
from support.handwriting_model_ui_support import text_of


@pytest.mark.parametrize("script,expected", [
    ("katakana", ["ニ"]), ("hiragana", ["こ"]),
])
def test_visually_similar_kanji_and_other_script_are_excluded(script, expected):
    assert practice_ink_candidates(["ニ", "こ", "二", "=", "＝"], script) == expected


def test_width_combining_voicing_order_and_deduplication_are_preserved():
    assert practice_ink_candidates(["ｶﾞ", "ガ", "カ\u3099", "ぱ", "ｷｬ", "キヤ", "ー"], "katakana") == ["ガ", "キャ", "キヤ", "ー"]
    assert practice_ink_candidates(["か\u3099", "が", "は\u309a", "ぱ", "きゃ", "きや"], "hiragana") == ["が", "ぱ", "きゃ", "きや"]


def test_only_actual_recognized_script_is_used_without_cross_script_conversion():
    assert practice_ink_candidates(["ニ", "ｺ", "二", "=", "a", None], "hiragana") == []
    assert practice_ink_candidates(["こ", "に", "二", "="], "katakana") == []
    assert practice_ink_candidates(["カa", "かカ", "カ。", "゛", "", "  "], "katakana") == []


def test_limit_applies_after_filtering_and_normalized_deduplication():
    assert practice_ink_candidates(["a", "二", "ｱ", "ア", "イ", "ウ", "エ", "オ", "カ"], "katakana") == ["ア", "イ", "ウ", "エ", "オ"]
    assert practice_ink_candidates(["あ"], "hiragana", limit=0) == []


@pytest.mark.parametrize("script", ["hiragana", "katakana"])
def test_practice_candidates_pass_only_actual_normalized_guess_to_confirmation(model_app, script):
    app = model_app
    app.page, app.free_ink, app.model_state = "ink_question", False, "ready"
    app.question = Question("target", "romaji_handwriting", script, "", "あ" if script == "hiragana" else "ア")
    app.choose_practice_answer = Mock()
    app.platform.recognize.return_value = ["二", "＝", "ﾆ", "ニ", "こ"]
    asyncio.run(app.recognize_ink())
    choices = buttons(app.ink_result_box)
    expected = "こ" if script == "hiragana" else "ニ"
    assert [candidate.text for candidate in choices] == [expected]
    choices[0].on_press(None)
    app.choose_practice_answer.assert_called_once_with(expected, app.question)
    assert expected != app.question.answer
    app.store.record_review.assert_not_called()


def test_no_matching_candidates_requests_rewrite_and_never_grades(model_app):
    app = model_app
    app.page, app.free_ink, app.model_state = "ink_question", False, "ready"
    app.question = Question("target", "kana_handwriting", "hiragana", "", "に")
    app.platform.recognize.return_value = ["ニ", "二", "=", "＝"]
    app.choose_practice_answer = Mock()
    asyncio.run(app.recognize_ink())
    assert not buttons(app.ink_result_box)
    assert "平假名" in app.ink_status_label.text and "请重写" in app.ink_status_label.text
    assert "不影响复习记录" in app.ink_status_label.text
    assert app.recognize_button.enabled and not app._ink_busy
    app.choose_practice_answer.assert_not_called()
    app.check_answer.assert_not_called()
    assert app.store.mock_calls == []


def test_free_writing_retains_unfiltered_recognizer_candidates(model_app):
    app = model_app
    app.model_state = "ready"
    original = ["ニ", "こ", "二", "=", "＝"]
    app.platform.recognize.return_value = original
    asyncio.run(app.recognize_ink())
    assert [candidate.text for candidate in buttons(app.ink_result_box)] == original
    assert app.store.mock_calls == []


@pytest.mark.parametrize("text,kind", [
    ("ニ", "片假名"), ("二", "汉字"), ("=", "符号"), ("＝", "符号"), ("こ", "平假名"),
    ("ｶﾞｰ", "片假名"), ("か\u3099", "平假名"), ("ー", "符号"), ("𠀀", "汉字"),
    ("食べる", "汉字／平假名"), ("こニ", "平假名／片假名"), ("Ａ", "拉丁字母"), ("123", "数字"),
])
def test_free_candidate_kind_distinguishes_lookalikes_without_renaming_text(text, kind):
    assert ink_candidate_kind(text) == kind


def test_free_candidate_labels_are_attached_to_each_real_guess_and_survive_confirmation(model_app):
    app = model_app
    app.model_state = "ready"
    original = ["ニ", "二", "=", "こ", "ｺ"]
    app.platform.recognize.return_value = original
    asyncio.run(app.recognize_ink())
    cells = [cell for row in app.ink_result_box.children[:-1] for cell in row.children]
    assert [(cell.children[0].text, cell.children[1].text) for cell in cells] == [
        ("ニ", "片假名"), ("二", "汉字"), ("=", "符号"), ("こ", "平假名"), ("ｺ", "片假名"),
    ]
    buttons(app.ink_result_box)[-1].on_press(None)
    assert "ｺ" in text_of(app.ink_result_box) and "片假名" in text_of(app.ink_result_box)
    assert "コ" not in text_of(app.ink_result_box)
    app.check_answer.assert_not_called()
    assert app.store.mock_calls == []
