"""A typed answer is also right when it is a right answer with some kanji words written as their readings.

Owner design (2026-11-18): for 学校へ行きます the learner may type がっこうへいきます, 学校へいきます or
がっこうへ行きます without the content listing every mix. A right answer text comes with reading spans
(start, end, kana) that the app already knows for it (the result page's readings); the typed answer
matches when, after the judge's own normalisation, it equals the text with each span written EITHER as
it stands OR as its kana, every span decided on its own. A kanji run without a known reading must be
typed as it stands; a different kanji with the same reading is never accepted, and nothing is read
from what the learner typed.

It applies only where the written form is not what the question tests (applies): the typed grammar
kinds and the word kind that asks for a whole expression (vocab_recall, task family
expression_recall). Readings and written words keep their own checks.

Pure: no Toga, store, platform, network or content loading. The judge
(learning_questions.grade_learning_answer) gathers the texts and spans and calls this when it judges an
answer, never while the question is open.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from itertools import product
import re

# Typed grammar kinds: grammar application does not test kanji orthography (the same reason the judge
# already accepts a curated kana rendering of grammar answers without reviewed practice).
GRAMMAR_TEXT_KINDS = frozenset({"grammar_cloze", "grammar_transform", "targeted_correction",
                                "controlled_sentence_production"})
# vocab_recall asks for a written word, except in this family: say the whole expression from a cue.
RECALL_KIND, EXPRESSION_RECALL = "vocab_recall", "expression_recall"
# Spans decided one by one: 2**n spellings. Beyond this many, the spans are matched piece by piece.
MAX_SPELLINGS = 4096

_KANJI = re.compile(r"[㐀-䶿一-鿿豈-﫿々]")
_KANA = re.compile(r"[ぁ-ゖァ-ヺー]")

Span = tuple[int, int, str]


def applies(question) -> bool:
    """Whether a typed answer of this question may write kanji words as their readings."""
    if not isinstance(question, Mapping) or question.get("input_mode") != "text":
        return False
    kind = question.get("kind")
    if kind in GRAMMAR_TEXT_KINDS:
        return question.get("family") == "grammar" and question.get("domain") != "pronunciation"
    practice = question.get("practice")
    return (kind == RECALL_KIND and isinstance(practice, Mapping)
            and practice.get("task_family") == EXPRESSION_RECALL)


def all_kana(text) -> bool:
    """A text with kana and no kanji (a kana answer: its standard spelling is read from it)."""
    return isinstance(text, str) and bool(_KANA.search(text)) and not _KANJI.search(text)


def clean_spans(text: str, spans: Iterable) -> list[Span]:
    """The usable spans, left to right: inside the text, not overlapping, with kana other than the surface."""
    valid = sorted((start, end, kana) for start, end, kana in
                   (span for span in spans or () if isinstance(span, (tuple, list)) and len(span) == 3)
                   if type(start) is int and type(end) is int and isinstance(kana, str) and kana
                   and 0 <= start < end <= len(text) and text[start:end] != kana)
    result, cursor = [], 0
    for start, end, kana in valid:
        if start >= cursor:  # an overlapping span is left out
            result.append((start, end, kana))
            cursor = end
    return result


def _pieces(text: str, spans: Sequence[Span]) -> list[tuple[str, ...]]:
    """The text as pieces: a fixed piece is one choice, a span is (surface, kana)."""
    pieces, cursor = [], 0
    for start, end, kana in spans:
        if start > cursor:
            pieces.append((text[cursor:start],))
        pieces.append((text[start:end], kana))
        cursor = end
    if cursor < len(text):
        pieces.append((text[cursor:],))
    return pieces


def spellings(text: str, spans: Iterable) -> Iterable[str]:
    """Every spelling of text with each span as written or as its kana (the text itself first)."""
    for choice in product(*_pieces(text, clean_spans(text, spans))):
        yield "".join(choice)


def _piecewise(value: str, pieces, normal: Callable[[str], str]) -> bool:
    """value is the pieces' choices, each normalised on its own, joined (for a text with very many spans)."""
    reachable = {0}
    for piece in pieces:
        options = {normal(option) for option in piece}
        reachable = {at + len(option) for at in reachable for option in options
                     if value.startswith(option, at)}
        if not reachable:
            return False
    return len(value) in reachable


def matches(value: str, text: str, spans: Iterable, normal: Callable[[str], str]) -> bool:
    """Whether the normalised typed value is text with each span written as it stands or as its kana."""
    if not isinstance(value, str) or not value or not isinstance(text, str):
        return False
    usable = clean_spans(text, spans)
    if 2 ** len(usable) > MAX_SPELLINGS:
        return _piecewise(value, _pieces(text, usable), normal)
    return any(normal(spelling) == value for spelling in spellings(text, usable))


_RUNS = re.compile(r"[㐀-䶿一-鿿豈-﫿々]+|[^㐀-䶿一-鿿豈-﫿々]+")


def align(text: str, kana: str, normal: Callable[[str], str]) -> list[Span] | None:
    """[(start, end, kana)] for every kanji run of text, read from the content's kana form of the whole text.

    Each part between kanji runs must equal the kana form after normalisation (normal: punctuation, spaces
    and script as the judge folds them); None when the kana form is not a reading of this text. The kana
    of a run is taken from the normalised kana form.
    """
    if not isinstance(text, str) or not isinstance(kana, str) or not kana or not _KANJI.search(text):
        return None
    parts = _RUNS.findall(text)
    pattern = "".join("(.+?)" if _KANJI.match(part) else re.escape(normal(part)) for part in parts)
    match = re.fullmatch(pattern, normal(kana))
    if match is None:
        return None
    spans, cursor, group = [], 0, 1
    for part in parts:
        if _KANJI.match(part):
            spans.append((cursor, cursor + len(part), match.group(group)))
            group += 1
        cursor += len(part)
    return spans


def shown_readings(text: str, spans: Iterable, references: Iterable[str],
                   normal: Callable[[str], str]) -> list[Span]:
    """The readings a result page shows above an answer, checked against the content's own kana forms.

    spans: the readings found for the text (the result page's); references: the content's all-kana forms
    of the answer. Readings a kana form confirms stay as they are, and a kanji run none of them covers
    gets the kana form's reading; readings a kana form contradicts give way to that kana form's reading.
    When no kana form is a reading of this text, nothing confirms or contradicts the spans: unchanged.
    """
    given = list(spans or ())
    readings = [(reference, aligned) for reference in references
                if (aligned := align(text, reference, normal)) is not None]
    if not readings:
        return given  # exactly as given
    usable = clean_spans(text, given)
    for reference, aligned in readings:
        if agrees(text, usable, [reference], normal):
            added = [span for span in aligned
                     if not any(start < span[1] and span[0] < end for start, end, _ in given)]
            return sorted(given + added) if added else given
    return readings[0][1]


def agrees(text: str, spans: Iterable, references: Iterable[str], normal: Callable[[str], str]) -> bool:
    """Whether the text read through its spans is one of the content's own kana answers (references).

    A kanji run left without a reading may stand for any kana. The result page's readings are made for
    showing and can miss the reading of an inflected word (着いて read き from 着る, 来ない read く from
    来る); a span set that the content's kana answer contradicts is not used for judging. Without any
    kana answer to compare with nothing is confirmed (False).
    """
    usable = clean_spans(text, spans)
    if not usable:
        return True
    read, cursor = [], 0
    for start, end, kana in usable:
        read += [text[cursor:start], kana]
        cursor = end
    read.append(text[cursor:])
    pattern = "".join("(?:.+?)" if _KANJI.match(part) else re.escape(normal(part))
                      for part in _RUNS.findall("".join(read)))
    return any(re.fullmatch(pattern, normal(reference)) is not None
               for reference in references if isinstance(reference, str) and reference)


def matches_any(value: str, forms: Iterable[tuple[str, Iterable]], normal: Callable[[str], str]) -> bool:
    """matches for any (text, spans) of the right answers."""
    return any(matches(value, text, spans, normal) for text, spans in forms)
