"""Shared build/runtime normalization for the offline vocabulary search index."""
from __future__ import annotations

import unicodedata

from .romanization import romanize_kana


def normalize_search(text: str) -> str:
    """Fold width, Latin case and kana script without erasing small kana/voicing."""
    value = unicodedata.normalize("NFKC", text).casefold().strip()
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in value)


def romanization_aliases(reading: str) -> set[str]:
    roman = normalize_search(romanize_kana(reading))
    # Accept both macrons and a keyboard-friendly doubled-vowel spelling for ー.
    doubled = roman.translate(str.maketrans({"ā": "aa", "ī": "ii", "ū": "uu", "ē": "ee", "ō": "oo"}))
    plain = roman.translate(str.maketrans({"ā": "a", "ī": "i", "ū": "u", "ē": "e", "ō": "o"}))
    return {v for v in (roman, doubled, plain) if v}


def word_search_terms(word: dict) -> list[tuple[str, int]]:
    """Term and tie-break priority; never duplicate a word in result pages."""
    terms: dict[str, int] = {}

    def add(text, priority):
        if isinstance(text, str) and (term := normalize_search(text)):
            terms[term] = min(priority, terms.get(term, priority))

    add(word.get("form") or word.get("display_kanji"), 0)
    add(word.get("reading") or word.get("display_reading"), 0)
    for item in word.get("kanji", []):
        add(item.get("text"), 1)
    readings = {item.get("text") for item in word.get("readings", [])}
    readings.add(word.get("reading") or word.get("display_reading"))
    for reading in readings:
        if reading:
            add(reading, 1)
            for alias in romanization_aliases(reading):
                add(alias, 2)
    add(word.get("gloss"), 3)
    add(word.get("chinese_gloss"), 3)
    return sorted(terms.items())
