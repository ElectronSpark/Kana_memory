"""Speech must not change answer display, grading, or Japanese phonemes."""
from copy import deepcopy

import pytest

from kanamemory.answer_speech import clean_utterance, pronunciation_utterance


@pytest.mark.parametrize("label,spoken", [
    ("り｜ん｜ご｜が · _---", "りんごが"),
    ("りんごが [0]", "りんごが"),
    ("たか↓い②", "たかい"),
    ("いく→", "いく"),
    ("わかりました↘", "わかりました"),
    ("わたしは(wa)｜がっこうへ(e)｜いきます。", "わたしはがっこうへいきます。"),
    ("火山（かざん）", "かざん"),
    ("食べる（たべる）", "たべる"),
    ("学校（がっこう）へ行きます。", "がっこうへ行きます。"),
    ("私は学校（がっこう）", "私はがっこう"),
    ("か\u3099っこう", "がっこう"),
    ("パ｜ー｜ティ｜ー", "パーティー"),
    ("きゅ｜う｜り", "きゅうり"),
    ("✓ がっこう", "がっこう"),
    ("本を3冊買いました。", "本を3冊買いました。"),
    ("三百", "三百"),
    ("１人でコーヒーを飲む。", "１人でコーヒーを飲む。"),
    ("ジュース・ケーキ", "ジュース・ケーキ"),
    ("/koː/", ""),
    ("[e] · 2", ""),
    ("_---", ""),
    ("2", ""),
    ("寝る → 一段動詞", ""),
    ("漢字＋漢字", ""),
    ("学校（　）行きます。", ""),
    ("（1）に行きます。", ""),
    ("行く（解释不能朗读）", ""),
])
def test_known_notation_is_removed_without_stripping_lexical_content(label, spoken):
    assert clean_utterance(label) == spoken


@pytest.mark.parametrize("answer,prompt,expected", [
    ("延长 /s/ 的摩擦", "さっそく", "さっそく"),
    ("舌根软腭", "まんが", "まんが"),
    ("词的读法整体记忆", "七夕（たなばた）", "たなばた"),
    ("てがみ", "手紙", "てがみ"),
    ("さんびゃく", "三百", "さんびゃく"),
    ("摩擦延长 · 4", "いっしゅん", "いっしゅん"),
    ("ス・ー・パ・ー", "スーパー", "スーパー"),
    ("シャ・ツ", "シャツ", "シャツ"),
    ("た↓かかった ／ たか↓かった", "たかかった", "たかかった"),
    ("先闭住，再发プ", "コップ", "コップ"),
    ("疑问", "本当？", "本当？"),
    ("は", "これ（　）", "これは"),
    ("不确定", "漢字＋漢字", ""),
    ("双唇", "", ""),
])
def test_pronunciation_uses_current_stimulus_when_the_label_is_analysis(answer, prompt, expected):
    q = {"answer": answer, "prompt": prompt, "text_roles": {"answer": "study"}}
    frozen = deepcopy(q)
    assert pronunciation_utterance(q, prompt) == expected
    assert q == frozen


@pytest.mark.parametrize("answer,prompt", [("食べてしまう", "食べちゃう"), ("読んじゃう", "読んでしまう")])
def test_conversion_answer_is_not_replaced_by_the_opposite_prompt(answer, prompt):
    q = dict(target_id="pronunciation:contraction_chau", answer=answer,
             text_roles={"answer": "study"})
    assert pronunciation_utterance(q, prompt) == answer
