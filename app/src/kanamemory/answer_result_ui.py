"""Shared result typography; grading, revelation and scheduling stay in callers."""


def answer_text(question, answer=None):
    """Display structured answers as ordinary Japanese, never their encoding."""
    from .multi_cloze import is_cloze_question, answer_label
    if is_cloze_question(question):
        return answer_label(question, answer)
    return str(question.get('answer', '') if answer is None else answer)


def shown_answer(question, answer=None):
    """answer_text as a result page shows it: a Japanese answer carries kana on every kanji word.

    Design 11 section 7, rule 9 (result_readings): after the answer nothing is left to give away.
    A Chinese meaning is returned as it is. Display only; nothing stored uses this string.
    """
    return shown_text(question, answer_text(question, answer))


def shown_text(question, text):
    """An answer or an entered answer already in its display form, with kana when the answers are Japanese."""
    from .result_readings import annotate, answers_in_japanese
    return annotate(question, text) if isinstance(text, str) and answers_in_japanese(question) else text


def shown_explanation(question, explanation):
    """The explanation under a result, with kana on the Japanese words the question has readings for."""
    from .result_readings import annotate_explanation
    return annotate_explanation(question, explanation)


def asked_text(question, explanation=""):
    """The Japanese the question was about, with kana, when the result page would not show it; else None.

    A meaning question's result shows a Chinese answer only, and a reading question's shows kana: the
    sentence or the word that was asked then appears here with its readings. Left out when the
    answer or a line of the explanation already is that text.
    """
    from .result_readings import annotate, answers_in_japanese, stimulus
    base = stimulus(question)
    if not base:
        return None
    shown = [str(explanation or "")]
    if answers_in_japanese(question):
        shown.append(answer_text(question))
    if any(base in text for text in shown):
        return None
    return annotate(question, base)


def distinct_explanation(explanation, answer):
    """Remove exact answer-only lines, retaining translations and explanations."""
    return "\n".join(line for line in str(explanation or "").splitlines()
                     if line.strip() != str(answer).strip()).strip()


def answer_furigana_block(app, question, answer=None, *, text_role, size, bold=False, **style):
    """The answer line of a result page: the answer's standard spelling with kana above its kanji words.

    Owner 2026-11-18 (answer_furigana): one format for the answer; the content's display form is looked
    up here, when the result is shown. Drawn by furigana_ui.cells_widget (one label when nothing has kana).
    size and text_role are the caller's: each page keeps the size its answer label had.
    Both imports stay inside: this module's other helpers are pure and are used without Toga.
    """
    from .answer_furigana import answer_cells
    from .furigana_ui import cells_widget
    return cells_widget(app, answer_cells(question, answer), size=size, text_role=text_role, bold=bold, **style)


def entered_answer_row(u, text, *, text_role):
    width = (5 * 12 * 96 / 72 + 4) * u.font_scale()
    return u.row(u.label("我的回答：", size=12, color=u.MUTED, width=width),
                 u.label(text, size=12, color=u.MUTED, flex=1, text_role=text_role))
