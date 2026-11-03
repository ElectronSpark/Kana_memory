"""Complete kana-table layout without mistaking spelling rows for IPA.

The main table follows the familiar gojuon rows. Irregular romanizations (shi,
chi, tsu, fu, ji, zu, o) remain on each record; they never determine the spelling
row. Symbols and context-dependent small kana do not claim a vowel coordinate.
"""
from __future__ import annotations

from dataclasses import dataclass

from .presentation import CATEGORY_LABELS


VOWELS = ("a", "i", "u", "e", "o")
HISTORICAL_CATEGORIES = frozenset({"historical_kana", "rare_legacy_katakana"})
_BASIC_ROWS = (
    ("∅", "あいうえお"), ("k", "かきくけこ"), ("s", "さしすせそ"),
    ("t", "たちつてと"), ("n", "なにぬねの"), ("h", "はひふへほ"),
    ("m", "まみむめも"), ("y", "や ゆ よ"), ("r", "らりるれろ"),
    ("w", "わ   を"), ("g", "がぎぐげご"), ("z", "ざじずぜぞ"),
    ("d", "だぢづでど"), ("b", "ばびぶべぼ"), ("p", "ぱぴぷぺぽ"),
)
_BASIC_COORDINATES = {
    form: (row, VOWELS[index])
    for row, forms in _BASIC_ROWS for index, form in enumerate(forms) if form != " "
}
_YOON_ROWS = {
    "き": "ky", "し": "sh", "ち": "ch", "に": "ny", "ひ": "hy", "み": "my", "り": "ry",
    "ぎ": "gy", "じ": "じ\nj", "ぢ": "ぢ\nj", "び": "by", "ぴ": "py",
}
_FOREIGN_ROWS = ("sh", "j", "ch", "ts", "t", "d", "f", "y", "w", "kw", "gw", "v", "ty", "dy", "fy", "vy")
_CATEGORY_ORDER = (
    "basic", "dakuten", "handakuten", "yoon", "foreign_combination", "foreign_voiced",
    "foreign_variant", "small", "long_vowel_mark", "voicing_mark", "iteration_mark",
)


@dataclass(frozen=True)
class KanaRow:
    label: str
    # A tuple per vowel keeps any future alternative spellings in the same cell.
    cells: tuple[tuple[dict, ...], ...]


@dataclass(frozen=True)
class KanaSection:
    key: str
    title: str
    rows: tuple[KanaRow, ...]
    extras: tuple[dict, ...]
    historical: bool = False


def _hiragana(item: dict) -> str:
    form = item.get("hiragana") or item.get("katakana") or ""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in form)


def _coordinate(item: dict) -> tuple[str, str] | None:
    category, form = item.get("category"), _hiragana(item)
    if category in {"basic", "dakuten", "handakuten"}:
        return _BASIC_COORDINATES.get(form)
    if category == "yoon" and len(form) == 2:
        row = _YOON_ROWS.get(form[0])
        vowel = {"ゃ": "a", "ゅ": "u", "ょ": "o"}.get(form[1])
        return (row, vowel) if row and vowel else None
    if category in {"foreign_combination", "foreign_voiced", "foreign_variant"}:
        romaji = item.get("romaji") or item.get("romanization", {}).get("display") or ""
        if len(romaji) >= 2 and romaji[-1] in VOWELS and romaji[:-1] in _FOREIGN_ROWS:
            return romaji[:-1], romaji[-1]
    return None


def build_kana_table(items: list[dict]) -> tuple[KanaSection, ...]:
    """Return every input record exactly once, with historical forms last.

    Unknown records remain visible in an unpositioned area rather than being
    dropped or being assigned a guessed phonetic value.
    """
    groups: dict[str, list[dict]] = {}
    for item in items:
        category = item.get("category", "other")
        historical = category in HISTORICAL_CATEGORIES or item.get("tier") == "historical"
        key = "historical_other" if historical and category not in HISTORICAL_CATEGORIES else category
        # Place single ヴ in the same v row as ヴァ/ヴィ/ヴェ/ヴォ.
        if key == "foreign_voiced":
            key = "foreign_combination"
        groups.setdefault(key, []).append(item)
    order = {key: index for index, key in enumerate(_CATEGORY_ORDER)}
    sections = []
    for key in sorted(groups, key=lambda key: (key in HISTORICAL_CATEGORIES or key == "historical_other", order.get(key, 99))):
        entries = groups[key]
        historical = key in HISTORICAL_CATEGORIES or key == "historical_other"
        cells, extras = {}, []
        for item in entries:
            coordinate = None if historical else _coordinate(item)
            if coordinate is None:
                extras.append(item)
            else:
                row, vowel = coordinate
                cells.setdefault(row, {v: [] for v in VOWELS})[vowel].append(item)
        row_order = (tuple(_YOON_ROWS.values()) if key == "yoon" else _FOREIGN_ROWS
                     if key in {"foreign_combination", "foreign_variant"} else tuple(row for row, _ in _BASIC_ROWS))
        rows = tuple(KanaRow(row, tuple(tuple(cells[row][v]) for v in VOWELS))
                     for row in row_order if row in cells)
        title = {"foreign_combination": "外来语组合与ヴ", "small": "小假名（结合词语读音）",
                 "historical_other": "其他历史假名"}.get(key, CATEGORY_LABELS.get(key, key))
        sections.append(KanaSection(key, title, rows, tuple(extras), historical))
    return tuple(sections)
