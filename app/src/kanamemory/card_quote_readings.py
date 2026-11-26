"""Readings for the Japanese quoted inside 「」 of teaching-card prose (design 02, teaching cards).

Card text is Chinese prose with Japanese in 「」. Inside a quotation, each kanji part of a word that has a
reading in the data is followed by its reading in full-width parentheses, in the style of the cue labels
(「読（よ）み」, 「意味（いみ）」, 「食（た）べる」). Not where the text already carries a reading right after the word
(「学校（がっこう）」, 「来よう（こよう）」, 「学校」（がっこう）: reading_given; an optional grammar piece such as
「〜場合（は）」 or 「高かった（です）」 is not a reading), never inside a quotation without kanji, never for a reading the question
shown on the same page hides (question_readings.hides_card_reading, rule 2). Without data a text stays as it is.

Whether a reading is drawn follows the question prompts (question_readings rule 3) and result pages
(result_readings rule 9): a word the learner has NOT learned always keeps its reading; a LEARNED word keeps it
only while the 日文假名注音 switch is on; a page that shows the result of an answer shows every reading. A span
is LEARNED when its course word (the fifth element) is a course word lesson with that written form and reading
(question_readings.course_words, the lookup of rule 8) among the learner's known lessons. CardReadings takes
the page's switch value and known lessons; without known lessons (pages without a learner, result pages)
every reading is drawn.

Data: the side file card_quote_readings.json (content_library_rules.SIDE_FILES), read through
content_pack.side_file_path (the active core library's copy, else the bundled one):
    {"format": "kanamemory-card-quote-readings", "version": 2,
     "items": {content_id: {field: {quote: [[start, end, reading, source, word], ...]}}}}
field: the text's place in the card (card_fields); quote: the exact text between 「 and 」; start, end:
code-point offsets into the quote of a kanji part (食 of 食べる, 意味), in order, not overlapping; reading:
hiragana; source: where the reading came from (tools/annotate_readings.py: course, override, homograph,
analyser); word: [written form, reading] of the course word lesson that teaches the word as it is read here,
or null when the course has no such lesson. A malformed quotation entry (any malformed span) leaves that
quotation as it is. A file of another version, version 1 included (it had no word element; no version-1 data
was ever shipped), is ignored like a malformed file.

Display rule beside it (drop_kana_echo): a parenthesised group that only repeats the kana run right before it
(「こちらこそ（こちらこそ）」) is not drawn; applied after the readings are inserted, never to a stored text.

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
VERSION = 2
FILE = "card_quote_readings.json"
QUOTE = re.compile(r"「([^「」]+)」")
_KANJI = re.compile(r"[㐀-䶿一-鿿豈-﫿々〆]")
_READING = re.compile(r"[ぁ-ゖー]{1,40}")
# A reading the text spells itself: （かな） or (かな) right after the word.
_GIVEN_READING = re.compile(r"[（(][ぁ-ゖァ-ヺー・]+[）)]")
_OKURIGANA = re.compile(r"[ぁ-ゖ]*")
_MAX_SPANS = 64
_WORD_LIMIT = 64
# drop_kana_echo: a run of two or more kana (with ー) and a bracket group that repeats it exactly.
_KANA_ECHO = re.compile(r"(?<![ぁ-ゖァ-ヺー])([ぁ-ゖァ-ヺー]{2,})(?:（\1）|\(\1\))")


def _bracket_kana(group):
    """The kana of a （かな） group, katakana folded to hiragana and ・ dropped."""
    return "".join(chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char
                   for char in group[1:-1] if char != "・")


def _spells_reading(group, tail, reading):
    """Whether the bracket ``group`` after a kanji part and its kana ``tail`` is that word's reading: it ends
    with the span's reading and the tail (「学校（がっこう）」, 「来よう（こよう）」); after a tail, it ends with the tail
    (the author's own reading of the inflected word, 「行った（おこなった）」; not 「高かった（です）」); right after the
    kanji part, anything but an optional grammar piece that differs from the reading (「十分（に）」 is not one,
    「歯（は）」 read は is)."""
    kana = _bracket_kana(group)
    if kana.endswith(reading + tail):
        return True
    if tail:
        return kana.endswith(tail)
    return kana not in _OPTIONAL_PIECES or kana == reading


# Optional grammar pieces an author writes in brackets after a word (「〜場合（は）」): not a reading.
_OPTIONAL_PIECES = frozenset({"は", "が", "を", "に", "へ", "で", "と", "も", "の", "か", "や", "ね", "よ", "だ",
                              "です", "ます"})


def reading_given(quote, end, after="", reading=None):
    """Whether the text spells a reading right after the word whose kanji part ends at ``end`` of ``quote``:
    （かな） right after the kanji or after the word's kana tail (「来よう（こよう）」), or right after the closing 」
    when the word ends the quotation (``after``: the text after 」, 「学校」（がっこう）). With the span's
    ``reading``, a bracket counts only when it spells the reading (_spells_reading); without it, any bracket."""
    tail = _OKURIGANA.match(quote, end).end()
    group = _GIVEN_READING.match(quote, tail)
    if group is None and tail == len(quote):
        group = _GIVEN_READING.match(after)
    if group is None:
        return False
    return reading is None or _spells_reading(group.group(), quote[end:tail], reading)


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


def _checked_word(word):
    """(written form, reading) of a span's course word, None for null, or False when malformed."""
    if word is None:
        return None
    if (isinstance(word, (list, tuple)) and len(word) == 2
            and all(isinstance(value, str) and 0 < len(value) <= _WORD_LIMIT for value in word)):
        return tuple(word)
    return False


def checked_spans(quote, spans):
    """((start, end, reading, word), ...) of one quotation's entry (word: (written form, reading) or None),
    or None when any part breaks the format."""
    if not isinstance(spans, (list, tuple)) or len(spans) > _MAX_SPANS:
        return None
    result, cursor = [], 0
    for span in spans:
        if not isinstance(span, (list, tuple)) or len(span) != 5:
            return None
        start, end, reading, source, word = span
        word = _checked_word(word)
        if (type(start) is not int or type(end) is not int or not cursor <= start < end <= len(quote)
                or not isinstance(reading, str) or not _READING.fullmatch(reading)
                or not isinstance(source, str) or not source or word is False
                or not all(_KANJI.match(char) for char in quote[start:end])):
            return None
        result.append((start, end, reading, word))
        cursor = end
    return tuple(result)


def drop_kana_echo(text):
    """``text`` without each parenthesised group (（） or ()) that only repeats the whole kana run right before
    it: 「こちらこそ（こちらこそ）」 -> 「こちらこそ」, while 「そこここ（ここ）」 and a single kana stay. Display only. Pure."""
    if not isinstance(text, str) or ("（" not in text and "(" not in text):
        return text
    return _KANA_ECHO.sub(r"\1", text)


def insert_readings(text, quotes, hidden=None, *, furigana=True, learned=None):
    """``text`` with （reading） after each annotated kanji part inside its 「」 (the module rules).

    quotes: {quote: [[start, end, reading, source, word], ...]} for this text's field, or None; hidden(surface,
    reading) -> True leaves that word without its reading (a question on the same page hides it); learned(word)
    -> True marks a span's course word (written form, reading) as learned: with ``furigana`` False (the page's
    日文假名注音 switch off) a learned span gets no reading. learned None: no span is learned. Pure.
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
        for start, end, reading, word in spans:
            surface = quote[start:end]
            if reading_given(quote, end, after, reading) or hidden is not None and hidden(surface, reading):
                continue
            if not furigana and word is not None and learned is not None and learned(word):
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
    """The readings of one card on one page: text(field, text) is the text the page shows.

    furigana: the page's 日文假名注音 switch; known_ids: the learner's known lessons (course_planning.known_ids
    as the page read it), or None on a page that draws every reading (no learner, or the result of an answer)."""

    def __init__(self, content_id, question=None, *, furigana=True, known_ids=None):
        self.content_id = content_id
        self.hidden = None
        self.furigana = True if known_ids is None else bool(furigana)
        self.learned = None
        if question is not None:
            from .question_readings import hides_card_reading
            self.hidden = hides_card_reading(question)
        if known_ids is not None:
            from .question_readings import course_words
            known = known_ids if isinstance(known_ids, (set, frozenset)) else frozenset(known_ids)
            self.learned = lambda word: not known.isdisjoint(course_words().get(word, ()))

    def text(self, field, text):
        shown = insert_readings(text, quotes_for(self.content_id, field), self.hidden,
                                furigana=self.furigana, learned=self.learned)
        return drop_kana_echo(shown)
