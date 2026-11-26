"""Romaji in the library searches (学习库, 例句库, 词库例句): the learner never needs a Japanese input method.

Each search keeps its own substring match over its fields and also matches the query against the
romaji of readings, with the dictionary search's normalisation and aliases (word_search:
normalize_search, romanization_aliases; no second romaji table): `sensei` finds 先生（せんせい）,
`koohii` and `kohi` find コーヒー. Substring, as for the other fields. A query without a Latin
letter is never compared with romaji, so a kana, kanji or Chinese query finds what it found before.
The aliases are computed once per reading (romaji_of's cache), not once per row per query.
The 学习库's lesson match (LessonQuery) serves its open tab and its counts of the other tabs alike.
Pure: no Toga, no learner database.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import lru_cache

from .word_search import normalize_search, romanization_aliases

READING_FIELDS = ("reading", "accepted_readings", "alternative_readings")


@lru_cache(maxsize=65536)
def romaji_of(reading: str) -> str:
    """The romaji aliases of one reading, one per line (a needle never holds a line break)."""
    return "\n".join(sorted(romanization_aliases(reading))) if reading else ""


def romaji_needle(query) -> str:
    """The query as it is compared with romaji, or "" when it holds no Latin letter."""
    needle = normalize_search(str(query or ""))
    return needle if any("a" <= char <= "z" for char in needle) and "\n" not in needle else ""


def readings_of(row: Mapping | None) -> list[str]:
    """A lesson's or word's reading and its accepted and alternative readings, when the row has them."""
    found = []
    for field in READING_FIELDS:
        value = (row or {}).get(field)
        for text in ([value] if isinstance(value, str) else value if isinstance(value, (list, tuple)) else ()):
            if isinstance(text, str) and text and text not in found:
                found.append(text)
    return found


def romaji_matches(needle: str, readings: Iterable[str]) -> bool:
    """True when ``needle`` (romaji_needle) is part of the romaji of one of ``readings``."""
    return bool(needle) and any(needle in romaji_of(reading) for reading in readings
                                if isinstance(reading, str) and reading)


# The 学习库 tabs that list lessons (假名 has its own page), in the order of the tab buttons.
LIBRARY_TABS = {"word": "单词", "grammar": "语法", "pronunciation": "读音规则"}
LESSON_SEARCH_FIELDS = ("title", "reading", "meaning", "category")


def library_tab_of(item: Mapping) -> str | None:
    """The 学习库 tab that lists a lesson: 读音规则 for the pronunciation domain, else its family's tab."""
    if item.get("domain") == "pronunciation":
        return "pronunciation"
    family = item.get("family")
    return family if family in LIBRARY_TABS and family != "pronunciation" else None


def items_by_tab(items: Iterable[Mapping]) -> dict[str, list]:
    """``items`` split by library_tab_of, each tab in the given order; every tab is present."""
    tabs = {tab: [] for tab in LIBRARY_TABS}
    for item in items:
        tab = library_tab_of(item)
        if tab is not None:
            tabs[tab].append(item)
    return tabs


class LessonQuery:
    """One 学习库 search: a substring of form, reading, Chinese meaning or topic, or of a reading's romaji.

    One rule for the open tab and for the counts of the other tabs (design 02, 学习库).
    An empty query matches every lesson.
    """

    def __init__(self, query):
        self.text = str(query or "")
        self.needle = self.text.strip().casefold()
        self.romaji = romaji_needle(self.text)

    def __bool__(self):
        return bool(self.needle)

    def matches(self, item: Mapping) -> bool:
        if not self.needle:
            return True
        return (self.needle in " ".join(str(item.get(key, "")) for key in LESSON_SEARCH_FIELDS).casefold()
                or romaji_matches(self.romaji, readings_of(item)))
