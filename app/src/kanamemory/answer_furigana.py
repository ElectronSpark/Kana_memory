"""The answer of a result page in one format: its standard spelling with kana above each kanji word.

Owner, 2026-11-18: the answer page shows the answer in its full standard spelling with furigana and nothing
else for the answer (no separate kana line, no inline 漢字（かな）). When the question shows kana (kana
options, a word written in kana so that it does not give the answer away), the content supplies the kanji
spelling as ``answer_display`` (and, for an answer that is not all kana, its reading ``answer_reading``)
beside the template; learning_questions.reviewed_answer_form reads it. It is looked up only here, when a
result is shown, and never stored in the question snapshot.

Pure: no Toga, platform, network or database. The page draws the cells (question_support.furigana_block).
"""
from __future__ import annotations

import re

from .answer_result_ui import answer_text
from .result_readings import annotation_spans, answers_in_japanese, word_spans

_KANJI = re.compile(r"[㐀-䶿一-鿿豈-﫿々]")
_KANA = re.compile(r"[ぁ-ゖァ-ヺー]")


def furigana_cells(text, spans):
    """[(surface, kana or "")] of text: each span's surface with its kana, the text between with "".

    Spans are (start, end, kana), left to right and not overlapping; one that is out of order, out of
    the text or empty is left out, so the cells always join back to the text.
    """
    if not isinstance(text, str):
        return []
    cells, cursor = [], 0
    for start, end, kana in spans or ():
        if not (cursor <= start < end <= len(text)) or not isinstance(kana, str):
            continue
        if start > cursor:
            cells.append((text[cursor:start], ""))
        cells.append((text[start:end], kana))
        cursor = end
    if cursor < len(text):
        cells.append((text[cursor:], ""))
    return cells


def _all_kana(text):
    return isinstance(text, str) and bool(_KANA.search(text)) and not _KANJI.search(text)


def answer_form(question, answer=None, *, form=None):
    """(text, spans) of the answer a result page shows.

    form: {answer_display, answer_reading} from the content (None: look it up). It applies only to the
    question's own answer, and only when the answer is Japanese (answers_in_japanese, the readings' own
    check: a Chinese meaning stays as it is); surrounding white space is dropped. With answer_display
    the text is that spelling, read from the answer when the answer is all kana, else from
    answer_reading; a display that is the answer as written and has neither keeps the readings of
    today. Without it the text is the answer as today, with the readings the inline form had (none for
    a Chinese meaning).
    """
    text = answer_text(question, answer)
    own = answer is None or answer == question.get("answer")
    japanese = isinstance(text, str) and answers_in_japanese(question)
    if own and japanese and form is None:
        form = lookup_answer_form(question)
    display = (form or {}).get("answer_display") if own and japanese else None
    display = display.strip() if isinstance(display, str) else ""
    if display:
        reading = (form or {}).get("answer_reading")
        reading = reading.strip() if isinstance(reading, str) else ""
        if _all_kana(text):
            spans = word_spans(display, text)
        elif reading:
            spans = word_spans(display, reading)
        elif display == text:
            spans = annotation_spans(question, display)
        else:
            spans = []
        return display, spans
    if japanese:
        return text, annotation_spans(question, text)
    return text, []


def answer_cells(question, answer=None, *, form=None):
    """The cells of answer_form."""
    text, spans = answer_form(question, answer, form=form)
    return furigana_cells(str(text), spans)


def lookup_answer_form(question):
    """The content's display form of the question's answer; {} when there is none. Result pages only."""
    from .learning_questions import reviewed_answer_form
    return reviewed_answer_form(question)
