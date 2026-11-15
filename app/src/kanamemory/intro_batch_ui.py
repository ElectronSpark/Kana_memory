"""Batch introduction cards and the batch quiz for new words/grammar (四之二): the entries the app keeps.

Each entry is a thin call into intro_batch_runtime, which asks the service
(course_line.batch: every decision and every write of a batch) and draws the
pages (course_line.ui.batch_pages). What stays here as control code: the
card's body (说明卡), shared by the card page, the quiz's wrong-answer
feedback and the card opened from a formal question; the review hint's label;
the help row of a formal question of a batch item; and the loanword source
labels. Card choices are self-reports and quiz answers are guided evidence:
neither creates a formal question, learning event or scheduler change.
"""
from __future__ import annotations

from . import intro_batch_runtime
from . import loanword_origins
from . import ui_components as u
from .course_line.batch_display import CARD_BUTTON, CARD_PAGE, QUIZ_PAGE, REVIEW_HINT
from .structured_lesson_ui import block_widget, teaching_block

INTRO_BATCH_PAGES = {CARD_PAGE, QUIZ_PAGE}


def origin_labels(item):
    """The loanword source line (and its note) of a word lesson, for pages shown after teaching or an
    answer only: never before or while a question is answered (loanword_origins rule 4)."""
    labels = []
    for line, note in loanword_origins.origin_texts(item):
        labels.append(u.label(line, size=15))
        if note:
            labels.append(u.label(note, size=13, color=u.MUTED))
    return labels


class IntroBatchUIMixin:
    def _intro_batch_known_ids(self, extra=()):
        """Items a batch sentence may use besides the new one: ready items,
        introduced kana (design 11 section 5, D5) and this batch."""
        return intro_batch_runtime.service(self).known_ids(extra)

    def route_intro_batch(self, current, enabled):
        """Whether the batch introduction takes this head: it resumes an open batch whose item the head is,
        or opens a new one at an eligible instruction head (then the batch is written).

        True: the batch owns this head; the caller shows it (course_runtime.perform_course).
        """
        return intro_batch_runtime.service(self).route(current, enabled)

    def show_intro_batch(self, widget=None, **kwargs):
        intro_batch_runtime.show(self)

    def intro_batch_review_due(self):
        """Due review count shown as a hint only; it never blocks a batch."""
        return intro_batch_runtime.service(self).review_due()

    def intro_batch_review_hint(self):
        if not intro_batch_runtime.service(self).batch_open() or not self.intro_batch_review_due():
            return []
        return [u.label(REVIEW_HINT, size=13, color=u.MUTED)]

    def _intro_card_body(self, item, *, origin=False):
        """说明卡 content, shared by the card page and wrong-answer feedback.

        origin=True adds the loanword source under the meaning; the card opened during a formal
        question (learning_hint) leaves it out.
        """
        word = item["family"] == "word"
        children = [u.label(item["title"], size=25, bold=True, color=u.GREEN, text_role="study" if word else "ui")]
        instruction = item.get("instruction", {})
        if word:
            if item.get("reading"):
                children.append(u.row(u.label(self.learning_reading_label(item), size=18, flex=1, text_role="study"),
                    *([] if item.get("personal_reading", {}).get("pending") else
                      [u.speaker_button(lambda w: self.speak_text(item["reading"]))])))
            meaning = item.get("meaning") or instruction.get("summary")
            if meaning:
                children.append(u.label(meaning, size=18))
            if origin:
                children.extend(origin_labels(item))
            children.append(self.example_cards(item, limit=1, known_ids=self._intro_batch_known_ids()))
        else:
            from .structured_lessons import lesson_view
            function = instruction.get("summary") or lesson_view(item).get("focus")
            if function:
                children.append(u.label(function, size=17))
            if instruction.get("rule"):
                children.append(block_widget(u, {"kind": "pattern", "label": "结构", "text": instruction["rule"]}))
            children.append(self.example_cards(item, limit=2, known_ids=self._intro_batch_known_ids()))
            if instruction.get("contrast"):
                children.append(block_widget(u, {"kind": "points", "label": "常见误用",
                                                 "items": [instruction["contrast"]]}, 1))
            explanation = u.col()
            def toggle_explanation(widget):
                if explanation.children:
                    explanation.clear()
                    widget.text = "展开讲解"
                else:
                    explanation.add(teaching_block(u, item, speak=self.speak_text))
                    widget.text = "收起讲解"
            children.extend([u.button("展开讲解", toggle_explanation, compact=True), explanation])
        return children

    def choose_intro_card(self, choice, *, expected):
        intro_batch_runtime.choose_card(self, choice, expected)

    def show_intro_batch_quiz(self):
        intro_batch_runtime.show_quiz(self)

    def open_batch_ability(self, item):
        """A later ability of a batch item: no intro page, no picker (四之二 item 4).

        The store writes the card self-report and opens the scope; the next
        dispatch shows its first formal question with a one-line hint.
        """
        intro_batch_runtime.open_ability(self, item)

    def intro_batch_question_help(self, current):
        """(hint labels, action buttons) for a formal question of a batch item.

        Early questions (fewer than EARLY_REVIEWS reviews) may open the card;
        opening it marks the draft assisted through learning_hint. The first
        question of a later ability also gets one short hint line.
        """
        help_ = intro_batch_runtime.service(self).question_help(current)
        hints = [u.label(help_.hint, size=13, color=u.MUTED)] if help_.hint is not None else []
        actions = ([u.button(CARD_BUTTON, lambda w: self.learning_hint(w, card=True),
                             compact=True, font_size=12, flex=1)] if help_.card else [])
        return hints, actions
