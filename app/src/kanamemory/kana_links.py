"""Explicit, catalog-backed links for learning content, never UI discovery.

Only the repository's hiragana/katakana spellings are indexed. Longest matches
win; identical spellings in one record are harmless, but conflicting record
IDs raise ValueError instead of choosing an arbitrary destination. Halfwidth
kana and decomposed voicing marks match canonical spellings without changing
the displayed text. Unknown historical forms and compatibility ideographs
are not expanded into modern kana.

Offsets always count Python Unicode code points, not UTF-16 code units.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
import unicodedata


@dataclass(frozen=True)
class ContentText:
    """An explicitly selected learning-content field eligible for kana links."""

    text: str

    def __post_init__(self):
        if not isinstance(self.text, str):
            raise TypeError("ContentText.text must be a string")


def _kana_base(char: str) -> bool:
    number = ord(char)
    return (
        0x3041 <= number <= 0x3096
        or 0x309D <= number <= 0x309E
        or 0x30A1 <= number <= 0x30FA
        or 0x30FC <= number <= 0x30FE
        or 0xFF66 <= number <= 0xFF9D
    )


def _units(text: str):
    """Yield (normalized, start, end) without splitting a combining sequence.

    A malformed/unsupported voiced cluster stays atomic, so its unvoiced base
    is not silently linked when the actual voiced spelling has no record.
    Detached combining/halfwidth voicing marks stay unknown unless explicitly
    present in the supplied catalog; spacing ゛/゜ match their own records.
    """
    cursor = 0
    while cursor < len(text):
        start = cursor
        cursor += 1
        if text[start:cursor + 1] == "\r\n":
            cursor += 1
        else:
            while cursor < len(text) and (
                unicodedata.category(text[cursor]).startswith("M")
                or text[cursor] in "\uff9e\uff9f"
            ):
                cursor += 1
        raw = text[start:cursor]
        normalized = unicodedata.normalize("NFKC", raw) if _kana_base(raw[0]) else raw
        yield normalized, start, cursor


def _normalized(text: str) -> str:
    return "".join(unit for unit, _, _ in _units(text))


def _width(text: str) -> int:
    # Same per-character width rule as presentation.wrapped().
    return sum(
        0 if unicodedata.combining(char)
        else 2 if unicodedata.east_asian_width(char) in "WF" else 1
        for char in text
    )


class KanaLinkIndex:
    """Construct once from repo.kana_list(); call only for selected content."""

    def __init__(self, records: Iterable[Mapping[str, Any]]):
        self._trie: dict = {}
        for record in records:
            kana_id = record.get("id")
            if not isinstance(kana_id, str) or not kana_id:
                raise ValueError("Each kana record must have a nonempty string id")
            for field in ("hiragana", "katakana"):
                glyph = record.get(field)
                if glyph is None or glyph == "":
                    continue
                if not isinstance(glyph, str):
                    raise TypeError(f"{field} must be a string or None")
                node = self._trie
                for char in _normalized(glyph):
                    node = node.setdefault(char, {})
                existing = node.get(None)
                if existing is not None and existing != kana_id:
                    raise ValueError(
                        f"Conflicting kana spelling {glyph!r}: {existing!r} and {kana_id!r}"
                    )
                node[None] = kana_id

    def spans(self, text: str) -> list[dict]:
        """Return nonoverlapping longest matches, preserving original slices.

        Every result has start/end (end-exclusive), kana_id, and text. No
        aliases are invented from romanization, pronunciation or character
        origin. Each repeated occurrence receives its own span.
        """
        if not isinstance(text, str):
            raise TypeError("KanaLinkIndex.spans expects a string")
        units = list(_units(text))
        result = []
        cursor = 0
        while cursor < len(units):
            node = self._trie
            best = None
            end = cursor
            while end < len(units):
                for char in units[end][0]:
                    node = node.get(char)
                    if node is None:
                        break
                if node is None:
                    break
                # A link may only end at an original cluster boundary.
                if None in node:
                    best = (end + 1, node[None])
                end += 1
            if best is None:
                cursor += 1
                continue
            following, kana_id = best
            start, finish = units[cursor][1], units[following - 1][2]
            result.append({"start": start, "end": finish, "kana_id": kana_id,
                           "text": text[start:finish]})
            cursor = following
        return result

    def render(self, parts: Iterable[str | ContentText], columns: int | None = None
               ) -> tuple[str, list[dict]]:
        """Join UI strings/content fields and optionally wrap the displayed text.

        Ordinary strings are never parsed for links. Adjacent ContentText
        parts are joined before matching; even an empty ordinary string is a
        deliberate boundary. Wrapping does not split an individual kana link,
        even if that link alone exceeds columns. Supplied line breaks and
        original spelling are retained; returned offsets refer to this final
        display string. Do not re-wrap the result in the UI afterwards.
        """
        if columns is not None and (
            not isinstance(columns, int) or isinstance(columns, bool) or columns < 1
        ):
            raise ValueError("columns must be a positive integer or None")

        grouped: list[tuple[str, bool]] = []
        for part in parts:
            if isinstance(part, ContentText):
                if grouped and grouped[-1][1]:
                    grouped[-1] = (grouped[-1][0] + part.text, True)
                else:
                    grouped.append((part.text, True))
            elif isinstance(part, str):
                grouped.append((part, False))
            else:
                raise TypeError("render parts must be strings or ContentText")

        atoms: list[tuple[str, str | None]] = []
        for text, eligible in grouped:
            matches = self.spans(text) if eligible else []
            cursor = 0
            for match in matches:
                gap = text[cursor:match["start"]]
                atoms.extend((gap[start:end], None) for _, start, end in _units(gap))
                atoms.append((match["text"], match["kana_id"]))
                cursor = match["end"]
            gap = text[cursor:]
            atoms.extend((gap[start:end], None) for _, start, end in _units(gap))

        output, result = [], []
        offset, width = 0, 0
        breaks = {"\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"}
        for text, kana_id in atoms:
            if text in breaks:
                output.append(text)
                offset += len(text)
                width = 0
                continue
            size = _width(text)
            if columns is not None and width > 0 and size > 0 and width + size > columns:
                output.append("\n")
                offset += 1
                width = 0
            start = offset
            output.append(text)
            offset += len(text)
            width += size
            if kana_id is not None:
                result.append({"start": start, "end": offset, "kana_id": kana_id, "text": text})
        return "".join(output), result
