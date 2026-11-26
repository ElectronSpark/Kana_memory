"""The explanation under a course question's answer, one text for every result page that shows it.

The course result page (learning_ui.show_learning_feedback), the practice deck's result
(arrangement/sources/deck_course: 加练 and 巩固) and the retention check and session results draw
the same visible text for the same question:
- course_explanation: the stored feedback without a line that only repeats the answer
  (answer_result_ui.distinct_explanation), without a gloss that only repeats its headword
  (without_self_gloss) and without a bracket that only repeats the kana before it
  (card_quote_readings.drop_kana_echo). Plain text: pages compare it with the asked sentence.
- shown_course_explanation: that text as drawn, with kana on the Japanese words the question has
  readings for (answer_result_ui.shown_explanation; design 11 section 7, rule 9); "" when nothing
  is left to show or it is only the answer.
Pure display rules: no Toga and no learner database; nothing stored uses these strings.
"""
from __future__ import annotations

import re

_SELF_GLOSS = re.compile(r"(?P<word>[^（）：:]+)（(?P<reading>[^（）]+)）[：:]\s*(?P<gloss>.+?)[。．.]?")


def without_self_gloss(text):
    """'X（读音）：X' whose gloss only repeats the headword -> 'X（读音）'.

    A display rule for the feedback explanation: other lines, and a gloss
    that differs from the headword, are returned unchanged.
    """
    lines = []
    for line in str(text or "").splitlines():
        match = _SELF_GLOSS.fullmatch(line.strip())
        if match and match["gloss"].strip() == match["word"].strip():
            line = f"{match['word'].strip()}（{match['reading']}）"
        lines.append(line)
    return "\n".join(lines)


def course_explanation(question):
    """The plain explanation of a course question's result (see the module text)."""
    from .answer_result_ui import answer_text, distinct_explanation
    from .card_quote_readings import drop_kana_echo
    return drop_kana_echo(without_self_gloss(distinct_explanation(question.get("feedback"), answer_text(question))))


def shown_course_explanation(question, explanation=None):
    """The explanation as a result page draws it; ``explanation``: course_explanation already made by the caller."""
    from .answer_result_ui import shown_explanation
    text = course_explanation(question) if explanation is None else explanation
    if not text or text == str(question.get("answer", "")).strip():
        return ""
    return shown_explanation(question, text)
