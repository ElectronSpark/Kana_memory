"""Japanese and simplified Chinese forms of one character (cjk_forms, the cognate rule's spelling); pure."""
import pytest

from kanamemory import cjk_forms as cf


@pytest.mark.parametrize("form,spelling", [
    ("時代", "时代"), ("電話", "电话"), ("豪華", "豪华"), ("会場", "会场"), ("学校", "学校"),
    ("予定", "预定"), ("芸術", "艺术"), ("午後", "午后"),   # Japanese forms that are GB2312 characters too
    ("戦う", "战う"),                                       # kana stays as written
    ("人々", "人人"),                                       # 々 repeats the character before it
])
def test_the_chinese_spelling_of_a_written_form(form, spelling):
    assert cf.chinese_spelling(form) == spelling


@pytest.mark.parametrize("form", ["働く", "込む", "畑", "弁当"])
def test_a_japanese_made_or_word_dependent_character_has_no_chinese_spelling(form):
    # 働, 込 and 畑 have no Chinese counterpart; 弁 is 辩, 辨, 瓣 or 办 by the word.
    assert cf.chinese_spelling(form) is None


def test_the_table_lists_only_differing_forms_with_simplified_chinese_values():
    assert cf.JA_TO_ZH and all(ja != zh for ja, zh in cf.JA_TO_ZH.items())
    assert all(len(zh) == 1 and cf.is_simplified_chinese(zh) for zh in cf.JA_TO_ZH.values())
    # A value is never itself mapped again, so chinese_text maps in one step.
    assert not set(cf.JA_TO_ZH.values()) & set(cf.JA_TO_ZH)
    assert not cf.WORD_DEPENDENT & set(cf.JA_TO_ZH)
    assert {"働", "込", "畑"}.isdisjoint(cf.JA_TO_ZH)


def test_chinese_text_maps_listed_kanji_and_keeps_the_rest():
    assert cf.chinese_text("この言葉の（　）を調べました。本课词：查词。") == "この言叶の（　）を调べました。本课词：查词。"


@pytest.mark.parametrize("form,text,shown", [
    ("豪華", "豪华；奢华", True), ("電話", "本课词：给朋友打电话。", True), ("時代", "時代劇", True),
    ("道具", "工具；用具", False), ("戦う", "战斗；作战", False), ("紹介", "介绍", False),
    ("働く", "工作", None),
])
def test_shows_form(form, text, shown):
    assert cf.shows_form(form, text) is shown


def test_ideographs_and_the_encoding_test():
    assert cf.is_ideograph("時") and not cf.is_ideograph("々") and not cf.is_ideograph("か")
    assert cf.is_simplified_chinese("时") and not cf.is_simplified_chinese("時")
