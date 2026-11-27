"""The answer of a result page in one format: its standard spelling with kana above each kanji word.

Owner, 2026-11-18: the answer page shows the answer in its full standard spelling with furigana and nothing
else for the answer (no separate kana line, no inline 漢字（かな）). When the question shows kana (kana
options, a word written in kana so that it does not give the answer away), the content supplies the kanji
spelling as ``answer_display`` (and, for an answer that is not all kana, its reading ``answer_reading``)
beside the template; learning_questions.reviewed_answer_form reads it. It is looked up only here, when a
result is shown, and never stored in the question snapshot.

Pure: no Toga, platform, network or database. The page draws the cells (question_support.furigana_block).
The pitch marks (answer_pitch, pitched_cells) read the learner's two switches and the lesson through what
the caller passes, and the read-only sentence_annotations sidecar, only when a result is shown.
"""
from __future__ import annotations

import re

from . import reading_form_answers
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
    if japanese and own:
        return text, checked_spans(question, text, form)
    if japanese:
        return text, annotation_spans(question, text)
    return text, []


def checked_spans(question, text, form=None):
    """The readings of the question's own answer without a standard spelling, checked by the content's kana.

    The content's kana forms are its all-kana accepted answers and answer_reading. One that is a reading of
    the answer decides (reading_form_answers.shown_readings): readings it confirms stay, a kanji word without
    a reading gets one only where that kana form fixes it (exactly one way to split it), readings it
    contradicts (着いて read き) are replaced where it fixes them and removed otherwise. Without one, the
    readings are today's (result_readings.annotation_spans).
    """
    from .learning_questions import _reading_normal
    spans = annotation_spans(question, text)
    accepted = question.get("accepted_answers")
    references = [x for x in (accepted if isinstance(accepted, list) else []) if reading_form_answers.all_kana(x)]
    reading = (form or {}).get("answer_reading")
    if isinstance(reading, str) and reading.strip():
        references.append(reading.strip())
    if not references:
        return spans
    return reading_form_answers.shown_readings(text, spans, references, _reading_normal)


def answer_cells(question, answer=None, *, form=None, pitch=None):
    """The cells of answer_form; with pitch (answer_pitch), the question's own answer carries its pitch marks."""
    text, spans = answer_form(question, answer, form=form)
    cells = furigana_cells(str(text), spans)
    own = answer is None or answer == question.get("answer")
    return pitched_cells(cells, **pitch) if pitch and own and answers_in_japanese(question) else cells


def answer_pitch(store, learning, question):
    """What may give the answer line its pitch marks, by the learner's switches; None without a store.

    lesson: the question's lesson (learning.get_item); word: the 单词 page's 数字调号 (pitch_display_v1),
    the switch of its reading line; sentence: the example sentences' 日文数字调号 (prompt_display_v1), the
    switch of the sentence annotations' marks. Read when the answer is shown.
    """
    if not callable(getattr(store, "get_setting", None)):
        return None
    from .pitch_display import PITCH_DISPLAY_SETTING, clean_pitch_preferences
    from .question_support import PROMPT_DISPLAY_SETTING, clean_prompt_preferences
    target = question.get("target_id") if isinstance(question, dict) else None
    lesson = (learning.get_item(target) if isinstance(target, str) and callable(getattr(learning, "get_item", None))
              else None)
    return {"lesson": lesson,
            "word": clean_pitch_preferences(store.get_setting(PITCH_DISPLAY_SETTING, {}))["number"],
            "sentence": clean_prompt_preferences(store.get_setting(PROMPT_DISPLAY_SETTING, {}))["number"]}


def pitched_cells(cells, *, lesson=None, word=False, sentence=False):
    """cells with pitch marks as a third element (furigana_ui.cell_segment); unchanged where there is no data.

    An answer that is the lesson's own word (its title, or its reading when written in kana) takes, with
    word, the lesson's reading and accents (pitch_accent.lesson_accents, the 单词 page's mark) as one
    cell, when the cells' kana agree with that reading; never a sentence annotation's. Any other answer,
    with sentence, takes the marks of sentence_annotations only when the sidecar holds exactly that text
    (status precomputed, not the dictionary fallback): a word of it becomes one cell where today's cells
    end at its ends and their kana (a kana cell: itself) are its reading. Anything else keeps today's cells.
    """
    from .pitch_accent import lesson_accents
    cells = [tuple(cell) for cell in cells]
    text = "".join(cell[0] for cell in cells)
    if not text:
        return cells
    reading = lesson.get("reading") if isinstance(lesson, dict) else None
    if isinstance(reading, str) and reading and text in (lesson.get("title"), reading):
        accents = lesson_accents(lesson) if word else ()
        if accents and "".join(k or s for s, k in cells) == reading:
            return [(text, "" if reading == text else reading, accents)]
        return cells
    return _sentence_cells(text, cells) if sentence else cells


def _sentence_cells(text, cells):
    from .sentence_annotations import sentence_annotation_plan, validated_accent_phrases
    plan = sentence_annotation_plan(text)
    if plan.get("status") != "precomputed" or plan.get("text") != text:
        return cells
    marks = {}
    for segment in plan.get("segments", ()):
        reading, number = segment.get("reading"), segment.get("accent_number")
        phrases = validated_accent_phrases(segment)
        if not isinstance(reading, str) or not reading:
            continue
        if phrases:
            mark = tuple((p["reading"], p["accent_number"]) for p in phrases)
        elif type(number) is int and segment.get("accent_scope") != "phrase_sequence":
            mark = (number,)
        else:
            continue
        marks[segment["start"]] = (segment["end"], reading, mark)
    if not marks:
        return cells
    # Plain cells (no kana) may be cut at a word's ends: they are drawn character by character anyway.
    cuts = {end for end, _, _ in marks.values()} | set(marks)
    split, position = [], 0
    for surface, kana in cells:
        pieces = [surface]
        if not kana:
            inner = sorted(c - position for c in cuts if position < c < position + len(surface))
            pieces = [surface[a:b] for a, b in zip([0, *inner], [*inner, len(surface)])]
        split.extend((piece, kana) for piece in pieces)
        position += len(surface)
    out, index, position, changed = [], 0, 0, False
    while index < len(split):
        entry = marks.get(position)
        if entry is not None:
            end, reading, mark = entry
            stop, reach = index, position + len(split[index][0])
            while reach < end and stop + 1 < len(split):
                stop += 1
                reach += len(split[stop][0])
            run = split[index:stop + 1]
            if reach == end and "".join(k or s for s, k in run) == reading:
                surface = text[position:end]
                out.append((surface, "" if reading == surface else reading, mark))
                index, position, changed = stop + 1, end, True
                continue
        out.append(split[index])
        position += len(split[index][0])
        index += 1
    if not changed:
        return cells
    merged = []
    for cell in out:   # rejoin neighbouring plain cells, as furigana_cells gives them
        if merged and len(cell) == 2 and not cell[1] and len(merged[-1]) == 2 and not merged[-1][1]:
            merged[-1] = (merged[-1][0] + cell[0], "")
        else:
            merged.append(cell)
    return merged


def lookup_answer_form(question):
    """The content's display form of the question's answer; {} when there is none. Result pages only."""
    from .learning_questions import reviewed_answer_form
    return reviewed_answer_form(question)


_CLOSING = "。！？"
_EXACT_ROWS = frozenset({"precomputed", "candidate"})  # an exact row of the sidecar; never the dictionary fallback


def explanation_line_segments(question, line):
    """Reading segments (offsets in ``line``) of a whole explanation line from its exact sentence record, or None.

    For answer_result_ui.shown_explanation, result pages only. The line is trimmed and tried as it is, then
    without one closing 。！？ (or with one when it has none). A line without kana is looked up only when it is
    the question's own Japanese (its prompt or answer): a Chinese line of kanji only is never read. Nothing is
    guessed: without an exact record the line keeps the readings it has.
    """
    from .sentence_annotations import sentence_annotation_plan
    if not isinstance(line, str) or not isinstance(question, dict):
        return None
    text = line.strip()
    own = {str(question.get(key) or "").strip() for key in ("prompt", "answer")}
    if not text or not _KANJI.search(text) or (not _KANA.search(text) and text not in own):
        return None
    lead = len(line) - len(line.lstrip())
    tries = [text, text[:-1]] if text[-1] in _CLOSING else [text, *(text + mark for mark in _CLOSING)]
    for candidate in tries:
        if not candidate:
            continue
        plan = sentence_annotation_plan(candidate)
        if plan.get("text") != candidate or plan.get("status") not in _EXACT_ROWS:
            continue
        segments = [dict(segment, start=segment["start"] + lead, end=segment["end"] + lead)
                    for segment in plan["segments"] if segment.get("reading") and segment["end"] <= len(text)]
        if segments:
            return segments
    return None
