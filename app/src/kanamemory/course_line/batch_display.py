"""The batch introduction's fixed texts (成批引入: the cards 新项说明 and the quiz 批小测; design 13).

Pure: no Toga, no store, no app object.
"""
from __future__ import annotations

from ..learning_questions import KIND_LABELS, READING_CHOICE_KIND

REVIEW_HINT = "有到期复习，建议先复习"
QUIZ_PAUSED = "未完成题目已保留，重新开启对应题型后可继续。"
CARD_BUTTON = "说明卡"
ABILITY_NAMES = {"understanding": "理解", "reading": "读法", "listening": "听辨", "use": "使用",
                 "application": "应用"}
KIND_CUES = {READING_CHOICE_KIND: "先选读音", "vocab_reading": "写出读法"}


def ability_hint(question):
    """One line for the first formal question of a later ability."""
    ability = ABILITY_NAMES.get(question.get("ability"), "练习")
    return f"新能力：{ability} — {KIND_CUES.get(question.get('kind'), KIND_LABELS.get(question.get('kind'), '练习'))}"
