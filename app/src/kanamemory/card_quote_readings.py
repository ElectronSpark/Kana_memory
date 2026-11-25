"""Readings for the Japanese quoted inside 「」 of teaching-card prose (design 02, teaching cards).

Card text is Chinese prose with Japanese in 「」. Inside a quotation, each kanji part of a word that has a
reading in the data is followed by its reading in full-width parentheses, in the style of the cue labels
(「読（よ）み」, 「意味（いみ）」, 「食（た）べる」). Not where the text already carries a reading right after the word
(「学校（がっこう）」, 「来よう（こよう）」, 「学校」（がっこう）: reading_given), never inside a quotation without kanji, never for a reading the question
shown on the same page hides (question_readings.hides_card_reading, rule 2). Without data a text stays as it is.

Data: the side file card_quote_readings.json (content_library_rules.SIDE_FILES), read through
content_pack.side_file_path (the active core library's copy, else the bundled one):
    {"format": "kanamemory-card-quote-readings", "version": 1,
     "items": {content_id: {field: {quote: [[start, end, reading, source], ...]}}}}
field: the text's place in the card (card_fields); quote: the exact text between 「 and 」; start, end:
code-point offsets into the quote of a kanji part (食 of 食べる, 意味), in order, not overlapping; reading:
hiragana; source: where the reading came from (tools/annotate_readings.py: course, override, homograph,
analyser). A malformed quotation entry leaves that quotation as it is.

Display only: the stored card, its feedback fragment and every learner record keep the original text.
insert_readings is the one pure function that inserts; the card renderer (structured_lesson_ui) calls it.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import json
from pathlib import Path
import re

FORMAT = "kanamemory-card-quote-readings"
VERSION = 1
FILE = "card_quote_readings.json"
QUOTE = re.compile(r"「([^「」]+)」")
_KANJI = re.compile(r"[㐀-䶿一-鿿豈-﫿々〆]")
_READING = re.compile(r"[ぁ-ゖー]{1,40}")
# A reading the text spells itself: （かな） or (かな) right after the word.
_GIVEN_READING = re.compile(r"[（(][ぁ-ゖァ-ヺー・]+[）)]")
_OKURIGANA = re.compile(r"[ぁ-ゖ]*")
_MAX_SPANS = 64


def reading_given(quote, end, after=""):
    """Whether the text spells a reading right after the word whose kanji part ends at ``end`` of ``quote``:
    （かな） right after the kanji or after the word's kana tail (「来よう（こよう）」), or right after the closing 」
    when the word ends the quotation (``after``: the text after 」, 「学校」（がっこう）)."""
    tail = _OKURIGANA.match(quote, end).end()
    if _GIVEN_READING.match(quote, end) or _GIVEN_READING.match(quote, tail):
        return True
    return tail == len(quote) and _GIVEN_READING.match(after) is not None


def card_fields(view, sections=()):
    """(field, text) of every prose text of a lesson view (structured_lessons.lesson_view) and its original
    sections (structured_lessons.original_sections): the keys of the data. focus; blocks.<i>.<key> for text
    and caption; blocks.<i>.<key>.<j> for items, nodes and cells; blocks.<i>.rows.<j>.<k>; notes.<i>.<j>."""
    fields = [("focus", view.get("focus"))]
    for i, block in enumerate(view.get("blocks") or ()):
        if not isinstance(block, Mapping):
            continue
        fields.extend((f"blocks.{i}.{key}", block.get(key)) for key in ("text", "caption"))
        for key in ("items", "nodes", "cells"):
            values = block.get(key)
            fields.extend((f"blocks.{i}.{key}.{j}", value) for j, value in enumerate(values or ())
                          if isinstance(values, (list, tuple)))
        rows = block.get("rows")
        for j, row in enumerate(rows if isinstance(rows, (list, tuple)) else ()):
            fields.extend((f"blocks.{i}.rows.{j}.{k}", value) for k, value in enumerate(row)
                          if isinstance(row, (list, tuple)))
    for i, (_label, values) in enumerate(sections):
        fields.extend((f"notes.{i}.{j}", value) for j, value in enumerate(values))
    return [(field, text) for field, text in fields if isinstance(text, str) and text]


def checked_spans(quote, spans):
    """((start, end, reading), ...) of one quotation's entry, or None when any part breaks the format."""
    if not isinstance(spans, (list, tuple)) or len(spans) > _MAX_SPANS:
        return None
    result, cursor = [], 0
    for span in spans:
        if not isinstance(span, (list, tuple)) or len(span) != 4:
            return None
        start, end, reading, source = span
        if (type(start) is not int or type(end) is not int or not cursor <= start < end <= len(quote)
                or not isinstance(reading, str) or not _READING.fullmatch(reading)
                or not isinstance(source, str) or not source
                or not all(_KANJI.match(char) for char in quote[start:end])):
            return None
        result.append((start, end, reading))
        cursor = end
    return tuple(result)


def insert_readings(text, quotes, hidden=None):
    """``text`` with （reading） after each annotated kanji part inside its 「」 (the module rules).

    quotes: {quote: [[start, end, reading, source], ...]} for this text's field, or None; hidden(surface,
    reading) -> True leaves that word without its reading (a question on the same page hides it). Pure.
    """
    if not isinstance(text, str) or not isinstance(quotes, Mapping) or not quotes or "「" not in text:
        return text
    parts, cursor = [], 0
    for match in QUOTE.finditer(text):
        quote = match.group(1)
        spans = checked_spans(quote, quotes.get(quote)) if _KANJI.search(quote) else None
        if not spans:
            continue
        after = text[match.end():]
        pieces, inner = [], 0
        for start, end, reading in spans:
            surface = quote[start:end]
            if reading_given(quote, end, after) or hidden is not None and hidden(surface, reading):
                continue
            pieces.append(quote[inner:end] + "（" + reading + "）")
            inner = end
        if not pieces:
            continue
        parts.append(text[cursor:match.start(1)] + "".join(pieces) + quote[inner:])
        cursor = match.end(1)
    return "".join(parts) + text[cursor:] if parts else text


@lru_cache(maxsize=1)
def _items(path):
    # Keyed by the path: an installed library's folder names its version and content hash.
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return {}
    if (not isinstance(data, dict) or data.get("format") != FORMAT or data.get("version") != VERSION
            or not isinstance(data.get("items"), dict)):
        return {}
    return data["items"]


def quotes_for(content_id, field):
    """{quote: spans} of one card text, or None (no data, unknown card or field)."""
    from .content_pack import side_file_path
    entry = _items(str(side_file_path(FILE))).get(content_id) if isinstance(content_id, str) else None
    quotes = entry.get(field) if isinstance(entry, dict) else None
    return quotes if isinstance(quotes, dict) else None


class CardReadings:
    """The readings of one card on one page: text(field, text) is the text the page shows."""

    def __init__(self, content_id, question=None):
        self.content_id = content_id
        self.hidden = None
        if question is not None:
            from .question_readings import hides_card_reading
            self.hidden = hides_card_reading(question)

    def text(self, field, text):
        return insert_readings(text, quotes_for(self.content_id, field), self.hidden)
