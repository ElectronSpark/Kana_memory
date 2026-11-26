"""Which readings a course page draws in its teaching-card quotations (design 02, teaching cards).

A page of the course before or without an answer (first teaching, quick review, lesson detail, guided practice,
looking back at an explanation during a question, the batch introduction) passes these options to
structured_lesson_ui.teaching_block / original_notes, so that card quotations follow the question prompts
(question_readings rule 3): the learner's 日文假名注音 switch and known lessons decide whether a learned word keeps
its reading. A page that shows the result of an answer, and pages outside the course, pass nothing: every
reading is drawn (card_quote_readings.CardReadings without known lessons). No controls are created here.
"""
from __future__ import annotations

from .question_support import PROMPT_DISPLAY_SETTING, clean_prompt_preferences


def course_page(app, known=None):
    """{"furigana": the switch, "known_ids": the learner's known lessons} for one page. ``known``: the set the
    page already read for its prompt; without it course_planning.known_ids is read here, once for the page."""
    furigana = clean_prompt_preferences(app.store.get_setting(PROMPT_DISPLAY_SETTING, {}))["furigana"]
    if known is None:
        from .course_planning import known_ids
        known = known_ids(app.store.queries)
    return {"furigana": bool(furigana), "known_ids": frozenset(known)}
