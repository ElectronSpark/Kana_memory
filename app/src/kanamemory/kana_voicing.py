"""Browse ordinary voiced kana beside their unmarked spelling relatives.

This is a display relation, not a content migration: every kana ID, source,
pronunciation and practice identity remains intact. Foreign combinations and
historical spellings are deliberately outside these elementary families.

The mark-to-letter relationship follows the Japanese Foundation's kana chart:
https://www.jpf.go.jp/j/urawa/j_rsorcs/methods/dl/moji_U2P1_text.pdf
Pronunciation labels always come from the supplied content, never from a
blanket replacement of romanization (which would get shi/ji and chi/ji wrong).
"""
from __future__ import annotations

from dataclasses import dataclass
from unicodedata import normalize

from .kana_table import KanaRow, KanaSection


_BASE_CATEGORIES = frozenset({"basic", "dakuten", "handakuten"})
_FAMILY_CATEGORIES = _BASE_CATEGORIES | {"yoon"}
_VOICED_TO_BASE = dict(zip("がぎぐげござじずぜぞだぢづでどばびぶべぼ", "かきくけこさしすせそたちつてとはひふへほ"))
_HALF_VOICED_TO_BASE = dict(zip("ぱぴぷぺぽ", "はひふへほ"))
_MARKED_TO_BASE = _VOICED_TO_BASE | _HALF_VOICED_TO_BASE
_BASIC_ROW = {"g": "k", "z": "s", "d": "t", "b": "h", "p": "h"}
_YOON_ROW = {"gy": "ky", "じ\nj": "sh", "ぢ\nj": "ch", "by": "hy", "py": "hy"}


@dataclass(frozen=True)
class VoicingExplanation:
    base_id: str
    kana_id: str
    label: str
    hiragana: str
    katakana: str
    romaji: str
    text: str


def _hiragana(form: str) -> str:
    return "".join(chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char
                   for char in normalize("NFC", form))


def base_kana_form(form: str) -> str:
    """Remove a standard voicing mark, preserving the input's script.

    Only single ordinary kana and contracted ゃ/ゅ/ょ forms qualify. This must
    not turn ヴ into ウ, ディ into ティ, or a complete word into another word.
    """
    form = normalize("NFC", form)
    canonical = _hiragana(form)
    if not canonical or len(canonical) > 2 or (len(canonical) == 2 and canonical[1] not in "ゃゅょ"):
        return form
    # A contracted spelling is only conventional with an i-row first letter.
    if len(canonical) == 2 and canonical[0] not in "ぎじぢびぴ":
        return form
    base = _MARKED_TO_BASE.get(canonical[0])
    if base is None:
        return form
    if "ァ" <= form[0] <= "ヶ":
        base = chr(ord(base) + 0x60)
    return base + form[1:]


def _family_key(item: dict) -> tuple[str, str] | None:
    category = item.get("category")
    if category not in _FAMILY_CATEGORIES or item.get("tier") == "historical":
        return None
    form = _hiragana(item.get("hiragana") or item.get("katakana") or "")
    if not form or (category in _BASE_CATEGORIES and len(form) != 1):
        return None
    if category == "yoon" and (len(form) != 2 or form[1] not in "ゃゅょ" or form[0] not in "きしちにひみりぎじぢびぴ"):
        return None
    return ("yoon" if category == "yoon" else "basic", base_kana_form(form))


def _rank(item: dict) -> int:
    form = _hiragana(item.get("hiragana") or item.get("katakana") or "")
    return 2 if form and form[0] in _HALF_VOICED_TO_BASE else 1 if form and form[0] in _VOICED_TO_BASE else 0


def voicing_family(item: dict, inventory: list[dict] | tuple[dict, ...]) -> tuple[dict, ...]:
    """Existing members in basic/voiced/half-voiced order, without mutation.

    A non-family record remains a singleton. Missing inventory records are
    never synthesized, so this also works with a deliberately filtered table.
    """
    key = _family_key(item)
    if key is None:
        return (item,)
    members = [candidate for candidate in inventory if _family_key(candidate) == key]
    if not any(candidate.get("id") == item.get("id") for candidate in members):
        members.append(item)
    return tuple(sorted(members, key=_rank))


def voicing_explanations(item: dict, inventory: list[dict] | tuple[dict, ...]) -> tuple[VoicingExplanation, ...]:
    """Explain only observed voiced relatives, using their recorded readings.

    Both a base detail and a voiced deep link can use these same small cards.
    A UI can suppress a self-link by comparing ``kana_id`` with its current ID.
    """
    members = voicing_family(item, inventory)
    base = next((member for member in members if _rank(member) == 0), None)
    if base is None or len(members) < 2:
        return ()
    result = []
    for member in members:
        rank = _rank(member)
        if not rank:
            continue
        hiragana, katakana = member.get("hiragana") or "", member.get("katakana") or ""
        romanization = member.get("romanization") or {}
        romaji = member.get("romaji") or (romanization.get("display") if isinstance(romanization, dict) else "") or ""
        label, mark = ("半浊音", "゜") if rank == 2 else ("浊音", "゛")
        base_form = base.get("hiragana") or base.get("katakana") or ""
        target_form = hiragana or katakana
        position = "首字" if len(_hiragana(base_form)) == 2 else ""
        reading = f"，读作 {romaji}" if romaji else "；读音尚待整理"
        text = f"在 {base_form} 的{position}右上方加{label}符号「{mark}」，写成 {target_form}{reading}。"
        result.append(VoicingExplanation(str(base["id"]), str(member["id"]), label,
                                        hiragana, katakana, romaji, text))
    return tuple(result)


def _merge_rows(sections: list[KanaSection], mapping: dict[str, str]) -> tuple[KanaRow, ...]:
    rows: dict[str, list[list[dict]]] = {}
    for section in sections:
        for row in section.rows:
            label = mapping.get(row.label, row.label)
            cells = rows.setdefault(label, [[] for _ in row.cells])
            for destination, source in zip(cells, row.cells):
                destination.extend(source)
    return tuple(KanaRow(label, tuple(tuple(sorted(cell, key=_rank)) for cell in cells))
                 for label, cells in rows.items())


def merge_voicing_sections(sections: tuple[KanaSection, ...]) -> tuple[KanaSection, ...]:
    """Put related spellings in one table cell, retaining every original item.

    Histories, foreign combinations, small letters and symbols keep their own
    sections. Passing a filtered table does not reintroduce filtered records.
    """
    basic = [section for section in sections if section.key in _BASE_CATEGORIES and not section.historical]
    result, merged = [], False
    for section in sections:
        if section in basic:
            if not merged:
                result.append(KanaSection("basic", "基本假名（含浊音与半浊音）",
                                          _merge_rows(basic, _BASIC_ROW),
                                          tuple(item for group in basic for item in group.extras)))
                merged = True
        elif section.key == "yoon" and not section.historical:
            result.append(KanaSection(section.key, "拗音（含浊音与半浊音）",
                                      _merge_rows([section], _YOON_ROW), section.extras))
        else:
            result.append(section)
    return tuple(result)
