"""A typed answer is also right when it is a right answer with some kanji words written as their readings.

Owner design (2026-11-18): for 学校へ行きます the learner may type がっこうへいきます, 学校へいきます or
がっこうへ行きます without the content listing every mix. A right answer text comes with reading spans
(start, end, kana) that the app already knows for it (the result page's readings); the typed answer
matches when, normalised, it equals the text with each span written EITHER exactly as it stands OR
exactly as its kana, every span decided on its own. A kanji run without a known reading must be typed
as it stands; a different kanji with the same reading is never accepted, and nothing is read from what
the learner typed.

It applies only where the written form is not what the question tests (applies): the typed grammar
kinds and the word kind that asks for a whole expression (vocab_recall, task family
expression_recall). Readings and written words keep their own checks.

Spans are used only when the content's own kana forms confirm them (agrees); the result page's
readings can be wrong for an inflected word (着いて read き). The same check decides which readings the
result page may show above the answer (shown_readings): a reading is added or replaced only when the
content's kana form leaves exactly one possibility.

Pure: no Toga, store, platform, network or content loading. The judge
(learning_questions.grade_learning_answer) gathers the texts and spans when it judges an answer, never
while the question is open, and compiles each text once into pieces (compile_form) that a typed answer
is walked through in one pass (walk).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
import re

# Typed grammar kinds: grammar application does not test kanji orthography (the same reason the judge
# already accepts a curated kana rendering of grammar answers without reviewed practice).
GRAMMAR_TEXT_KINDS = frozenset({"grammar_cloze", "grammar_transform", "targeted_correction",
                                "controlled_sentence_production"})
# vocab_recall asks for a written word, except in this family: say the whole expression from a cue.
RECALL_KIND, EXPRESSION_RECALL = "vocab_recall", "expression_recall"
# shown_readings: more ways than this to split a kana form over the unread kanji runs is "not certain".
MAX_SPLITS = 32

_KANJI = re.compile(r"[㐀-䶿一-鿿豈-﫿々]")
_KANA = re.compile(r"[ぁ-ゖァ-ヺー]")
_RUNS = re.compile(r"[㐀-䶿一-鿿豈-﫿々]+|[^㐀-䶿一-鿿豈-﫿々]+")

Span = tuple[int, int, str]
Pieces = tuple[tuple[str, ...], ...]


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


def compile_form(text: str, spans: Iterable, normal: Callable[[str], str]) -> Pieces:
    """The text as pieces, each normalised once: a fixed piece is one choice, a span is (surface, kana)."""
    pieces, cursor = [], 0
    for start, end, kana in clean_spans(text, spans):
        if start > cursor:
            pieces.append((normal(text[cursor:start]),))
        pieces.append(tuple(dict.fromkeys((normal(text[start:end]), normal(kana)))))
        cursor = end
    if cursor < len(text):
        pieces.append((normal(text[cursor:]),))
    return tuple(piece for piece in pieces if piece != ("",))


def reads_every_kanji(text: str, spans: Iterable) -> bool:
    """Whether every kanji of text lies in one of its spans."""
    covered = {index for start, end, _ in clean_spans(text, spans) for index in range(start, end)}
    return all(index in covered for index, char in enumerate(text) if _KANJI.match(char))


def walk(value: str, pieces: Pieces) -> bool:
    """Whether value is the pieces' choices joined, in one pass over value (positions reachable so far)."""
    if not isinstance(value, str) or not value:
        return False
    reachable = {0}
    for piece in pieces:
        reachable = {at + len(option) for at in reachable for option in piece
                     if value.startswith(option, at)}
        if not reachable:
            return False
    return len(value) in reachable


def matches(value: str, text: str, spans: Iterable, normal: Callable[[str], str]) -> bool:
    """Whether the normalised typed value is text with each span written exactly as it stands or as its kana."""
    if not isinstance(text, str):
        return False
    return walk(value, compile_form(text, spans, normal))


def _touches_unread(text: str, spans: Sequence[Span]) -> bool:
    """A span directly beside a kanji that no span reads (日本 read, 語 not, in 日本語)."""
    covered = {index for start, end, _ in spans for index in range(start, end)}
    return any(0 <= index < len(text) and index not in covered and _KANJI.match(text[index])
               for start, end, _ in spans for index in (start - 1, end))


def agrees(text: str, spans: Iterable, references: Iterable[str], normal: Callable[[str], str]) -> bool:
    """Whether the text read through its spans is one of the content's own kana forms (references).

    A kanji run left without a reading may stand for any kana (one or more). The result page's readings
    are made for showing and can miss the reading of an inflected word (着いて read き from 着る, 来ない
    read く from 来る); a span set that the content's kana contradicts is not confirmed, nor is a span
    that touches a kanji run without a reading (日本 read にほん in 日本語). Spans with nothing to compare
    with are not confirmed (False); no spans at all need no confirming (True).
    """
    usable = clean_spans(text, spans)
    if not usable:
        return True
    if _touches_unread(text, usable):
        return False
    read, cursor = [], 0
    for start, end, kana in usable:
        read += [text[cursor:start], kana]
        cursor = end
    read.append(text[cursor:])
    parts = _RUNS.findall("".join(read))
    targets = {normal(reference) for reference in references if isinstance(reference, str) and reference}
    if not any(_KANJI.match(part) for part in parts):
        return normal("".join(parts)) in targets
    pattern = re.compile("".join("(?:.+)" if _KANJI.match(part) else re.escape(normal(part)) for part in parts))
    return any(pattern.fullmatch(target) is not None for target in targets)


def _units(text: str, anchors: Sequence[Span], normal: Callable[[str], str]):
    """The text as tokens: ("kana", normalised text) for kana, anchors and other characters, ("run", start,
    end) for each kanji run no anchor reads. A character the normalisation drops (punctuation, a space)
    ends a run: two runs never join across it."""
    tokens, index, anchor_at = [], 0, {start: (end, kana) for start, end, kana in anchors}
    while index < len(text):
        if index in anchor_at:
            end, kana = anchor_at[index]
            tokens.append(("kana", normal(kana)))
            index = end
        elif _KANJI.match(text[index]):
            end = index
            while end < len(text) and _KANJI.match(text[end]) and end not in anchor_at:
                end += 1
            tokens.append(("run", index, end))
            index = end
        else:
            tokens.append(("kana", normal(text[index])))
            index += 1
    return tokens


def _splits(tokens, target: str, limit: int):
    """Every way (up to limit + 1) to give each run one or more characters of target; [] when none fits."""
    found = []

    def visit(at, position, chosen):
        if len(found) > limit:
            return
        if at == len(tokens):
            if position == len(target):
                found.append(list(chosen))
            return
        token = tokens[at]
        if token[0] == "kana":
            if target.startswith(token[1], position):
                visit(at + 1, position + len(token[1]), chosen)
            return
        following = next((other for other in tokens[at + 1:] if other != ("kana", "")), None)
        for end in range(position + 1, len(target) + 1):
            if following is None and end != len(target):
                continue  # the last run takes the rest
            if following is not None and following[0] == "kana" and not target.startswith(following[1], end):
                continue  # a run ends where the next kana of the text begins
            chosen.append(target[position:end])
            visit(at + 1, end, chosen)
            chosen.pop()
            if len(found) > limit:
                return

    visit(0, 0, [])
    return found


def certain_readings(text: str, anchors: Sequence[Span], reference: str,
                     normal: Callable[[str], str]) -> list[Span] | None:
    """[(start, end, kana)] for the kanji runs between anchors whose reading the kana form fixes.

    The anchors (confirmed readings) stay fixed; every split of the reference over the remaining runs is
    tried, and a run gets a reading only when every split gives it the same one (no split at all: None;
    more than MAX_SPLITS: nothing is certain).
    """
    if not isinstance(reference, str) or not reference:
        return None
    tokens = _units(text, anchors, normal)
    runs = [token for token in tokens if token[0] == "run"]
    found = _splits(tokens, normal(reference), MAX_SPLITS)
    if not found:
        return None
    if len(found) > MAX_SPLITS:
        return []
    return [(run[1], run[2], found[0][index]) for index, run in enumerate(runs)
            if all(split[index] == found[0][index] for split in found)]


def shown_readings(text: str, spans: Iterable, references: Iterable[str],
                   normal: Callable[[str], str]) -> list[Span]:
    """The readings a result page shows above an answer, checked against the content's own kana forms.

    spans: the readings found for the text (the result page's); references: the content's all-kana forms
    of the answer. When a kana form that is a reading of this text confirms the spans, they stay as they
    are, and a kanji run without a reading gets one only when that kana form fixes it. When the kana forms
    that are readings of this text all contradict the spans, the readings shown are only those the first
    of them fixes (a contradicted reading is replaced when certain, else removed). When no kana form is a
    reading of this text, nothing confirms or contradicts the spans: unchanged.
    """
    given = list(spans or ())
    usable = clean_spans(text, given)
    references = [reference for reference in references if isinstance(reference, str) and reference]
    for reference in references:
        if agrees(text, usable, [reference], normal):
            fixed = certain_readings(text, usable, reference, normal)
            if fixed is None:
                continue
            added = [span for span in fixed
                     if not any(start < span[1] and span[0] < end for start, end, _ in usable)]
            return sorted(given + added) if added else given
    for reference in references:
        fixed = certain_readings(text, [], reference, normal)
        if fixed is not None:
            return fixed
    return given
