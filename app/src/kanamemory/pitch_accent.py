"""Read-only Tokyo citation-form pitch annotations from attested word data.

The symbols are an abstract two-level teaching notation, not a claim about
measured F0 or phrase intonation. Type 0 has no lexical fall; type n falls after
mora n. A word-final fall is distinguished from type 0 by its number even when
the word-internal symbols coincide. No accent is inferred for missing data.

References for the notation and mora counting:
https://www.isca-archive.org/slate_2011/short11_slate.pdf (section 2.1)
https://www.coelang.tufs.ac.jp/mt/ja/pmod/practical/03-01-01.php
https://www.spt.metro.tokyo.lg.jp/kyoiku/static/dbook/japanese/pageindices/index183.html
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping


TOKYO_SYSTEM = "Tokyo_pitch_drop_after_mora_zero_unaccented"
_SMALL_JOIN = frozenset("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ")
_SPECIAL = frozenset("っッんンー")
_KANA = re.compile(r"[ぁ-ゖァ-ヶー]+\Z")
_ANNOTATION_PART = re.compile(
    r"\s*(?P<prefix>(?:\([^()]*\)\s*)*)"
    r"(?P<position>[0-9]+)"
    r"\s*(?P<suffix>(?:\([^()]*\)\s*)*)\Z"
)


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def _reading_key(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c
                   for c in _normalized(text))


def morae(reading: str) -> tuple[str, ...]:
    """Split a kana reading into morae while retaining its written script.

    Small vowels/y/w join the preceding ordinary kana. Sokuon, nasal n, and
    long-vowel marks remain separate morae; a written long vowel remains two
    morae as well. Unsupported/non-kana or malformed readings return ().
    These groups describe pitch placement only, never answer keyboard keys.
    """
    if not isinstance(reading, str):
        return ()
    text = _normalized(reading)
    if not text or not _KANA.fullmatch(text):
        return ()
    result: list[str] = []
    for char in text:
        if char in _SMALL_JOIN:
            if not result or result[-1] in _SPECIAL or len(result[-1]) != 1:
                return ()
            result[-1] += char
        else:
            result.append(char)
    return tuple(result)


def pitch_levels(mora_count: int, position: int) -> tuple[str, ...]:
    """Return one '-' (high) or '_' (low) per mora for a valid accent type."""
    if (type(mora_count) is not int or mora_count < 1 or type(position) is not int
            or not 0 <= position <= mora_count):
        raise ValueError("accent position must be an integer from 0 to the mora count")
    if position == 1:
        return ("-",) + ("_",) * (mora_count - 1)
    high_count = mora_count - 1 if position == 0 else position - 1
    return ("_",) + ("-",) * high_count + ("_",) * (mora_count - 1 - high_count)


def _qualifier(text: str) -> str:
    return "; ".join(value.strip() for value in re.findall(r"\(([^()]*)\)", text)
                     if value.strip())


def _positions(item: Mapping) -> tuple[tuple[int, str], ...]:
    """Preserve the dictionary's positional/POS associations, in source order.

    Kanjium uses e.g. '(名)4,0,(副)0': the leading qualifier applies until the
    next one. A suffix qualifier applies just to that alternative. Only full
    simple annotations are parsed, so digits inside notes are never accents.
    Unknown formats remain available in the original evidence, not a guess.
    """
    raw = item.get("raw")
    if raw is None or raw == "":
        positions = item.get("positions") or ()
        if not isinstance(positions, (list, tuple)):
            return ()
        return tuple((n, "") for n in positions if type(n) is int)
    if not isinstance(raw, str):
        return ()
    # Split only outside parentheses; notes may themselves contain commas.
    text = unicodedata.normalize("NFKC", raw)
    parts, start, depth = [], 0, 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
        if depth < 0:
            return ()
    if depth:
        return ()
    parts.append(text[start:])
    result, inherited = [], ""
    for part in parts:
        match = _ANNOTATION_PART.fullmatch(part)
        if match is None:
            return ()
        if match["prefix"]:
            inherited = _qualifier(match["prefix"])
        suffix = _qualifier(match["suffix"])
        qualifier = "; ".join(value for value in (inherited, suffix) if value)
        result.append((int(match["position"]), qualifier))
    return tuple(result)


def _record_data(record: Mapping) -> dict:
    stored = record.get("payload") or {}
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except (TypeError, ValueError):
            stored = {}
    return {**(stored if isinstance(stored, Mapping) else {}),
            **{key: value for key, value in record.items() if key != "payload"}}


def word_pitch_variants(record: Mapping | None, reading: str | None = None) -> tuple[dict, ...]:
    """Return valid sourced alternatives for exactly the displayed reading.

    The caller may pass the reading displayed in a question explicitly. Kana
    script and Unicode width differences can match; small-kana differences,
    long-vowel spellings, and alternate lexical readings cannot. Source forms
    and POS qualifiers are retained rather than merged into an unscoped type.
    This function never mutates the source record or any answer text.
    """
    if not isinstance(record, Mapping):
        return ()
    data = _record_data(record)
    if reading is None:
        reading = data.get("reading") or data.get("display_reading") or data.get("practice_reading")
    if not isinstance(reading, str):
        return ()
    written = _normalized(reading)
    units = morae(written)
    if not units:
        return ()
    japanese = data.get("japanese_enrichment") or {}
    items = data.get("pitch_accent") or (
        japanese.get("pitch_accent") if isinstance(japanese, Mapping) else ()) or ()
    if not isinstance(items, (list, tuple)):
        return ()
    result, seen = [], set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if item.get("system") not in (None, TOKYO_SYSTEM):
            continue
        if item.get("scope") not in (None, "citation_form_not_sentence_sandhi"):
            continue
        source_reading = item.get("reading")
        if not isinstance(source_reading, str) or _reading_key(source_reading) != _reading_key(written):
            continue
        original_reading = item.get("source_reading") or source_reading
        if not isinstance(original_reading, str) or _reading_key(original_reading) != _reading_key(written):
            continue
        raw = item.get("raw")
        if raw is None:
            raw = ", ".join(str(n) for n, _ in _positions(item))
        source_ids = item.get("source_ids") or ()
        if not isinstance(source_ids, (list, tuple)):
            source_ids = ()
        for position, qualifier in _positions(item):
            if not 0 <= position <= len(units):
                continue
            variant = {
                "reading": written, "morae": units, "position": position,
                "levels": pitch_levels(len(units), position), "raw": raw,
                "source_form": str(item.get("source_form") or ""),
                "source_reading": original_reading, "qualifier": qualifier,
                "source_ids": tuple(s for s in source_ids if isinstance(s, str)),
            }
            key = tuple(variant.values())
            if key not in seen:
                seen.add(key)
                result.append(variant)
    return tuple(result)
