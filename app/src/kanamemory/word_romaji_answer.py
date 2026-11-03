"""Grade canonical word-romanization choices and locate conservative evidence.

The target stays a kana spelling: its Roman answer is only another retrieval
direction. Evidence therefore shares the same reading key as forward spelling.
No inverse transliterator guesses which kana an incorrect Roman option meant.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .romanization import _GEMINATABLE, _MAPPING, _MATCH_LENGTHS, romanize_kana
from .word_spelling import MAX_SPELLING_LENGTH, _catalog, _tokenize


MAX_ROMAJI_LENGTH = MAX_SPELLING_LENGTH * 3
_STANDARD = re.compile(r"[a-zāīūēō']+\Z")
_EXPANDED = re.compile(r"[a-z']+\Z")
_LONG_VOWELS = str.maketrans({"ā": "aa", "ī": "ii", "ū": "uu", "ē": "ee", "ō": "oo"})
_ERROR_ORDER = ("substitution", "missing", "extra")


def _nfc(value):
    return unicodedata.normalize("NFC", value).strip()


def _comparison(value):
    # Options are generated in the canonical scheme. Case and full-width
    # Latin presentation do not change a selected answer; internal spaces and
    # apostrophes are never erased, nor are alternative romanization schemes.
    return unicodedata.normalize("NFKC", value).strip().lower()


def check_word_romaji_answer(kana: str, answer: str) -> bool:
    """Accept the word's canonical Roman option, without phonetic guesses."""
    if not isinstance(kana, str) or not isinstance(answer, str):
        raise TypeError("kana and answer must be strings")
    expected = _comparison(romanize_kana(_nfc(kana)))
    return bool(_STANDARD.fullmatch(expected)) and _comparison(answer) == expected


@dataclass(frozen=True)
class _Part:
    kind: str
    value: str
    owner: int | None


def _target_letters(kana, units):
    """Map canonical Roman characters to exact catalog-backed kana units.

    Macrons become two virtual vowels solely for error alignment. A missing
    length can then be ambiguous between the base vowel and the length mark,
    instead of automatically penalizing the base kana. Exact grading still
    distinguishes the canonical macron option from a written double vowel.
    """
    spans, cursor = {}, 0
    for index, unit in enumerate(units):
        spans[(cursor, cursor + len(unit.text))] = index
        cursor += len(unit.text)
    normalized = unicodedata.normalize("NFKC", kana)
    if normalized != kana:
        # Normalizing half-width or historical source text can move indices.
        # It may still be graded, but must not borrow another unit's evidence.
        return None
    folded = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in kana)
    parts, cursor = [], 0
    while cursor < len(folded):
        char = folded[cursor]
        if char in {"っ", "ん", "ー"}:
            end, value = cursor + 1, char
            kind = {"っ": "sokuon", "ん": "nasal", "ー": "long"}[char]
        else:
            size = next((size for size in _MATCH_LENGTHS
                         if cursor + size <= len(folded)
                         and folded[cursor:cursor + size] in _MAPPING), None)
            if size is None:
                return None
            end, kind = cursor + size, "kana"
            value = _MAPPING[folded[cursor:end]]
        parts.append(_Part(kind, value, spans.get((cursor, end))))
        cursor = end
    letters, owners = [], []
    for index, part in enumerate(parts):
        following = parts[index + 1] if index + 1 < len(parts) else None
        if part.kind == "nasal":
            value = "n'" if following and following.kind == "kana" and following.value[0] in "aeiouy" else "n"
        elif part.kind == "sokuon":
            if not following or following.kind != "kana" or following.value[0] not in _GEMINATABLE:
                return None
            value = following.value[0]
        elif part.kind == "long":
            if (not index or parts[index - 1].kind not in {"kana", "long"}
                    or not letters or letters[-1] not in "aeiou"):
                return None
            value = letters[-1]
        else:
            value = part.value
        letters.extend(value)
        owners.extend([part.owner] * len(value))
    expected = _comparison(romanize_kana(kana)).translate(_LONG_VOWELS)
    if "".join(letters) != expected:
        return None
    return expected, owners


def analyze_word_romaji(expected: str, actual: str, script: str,
                       kana_rows: list[dict], *, assisted: bool = False) -> dict:
    """Return existing spelling-evidence fields for a Roman-choice answer.

    Only catalog units whose correctness agrees on every minimum-edit path
    contribute. A wrong choice never creates evidence for the different kana
    that might have produced it. Multiple occurrences of one target ID count
    once, and a definite error in any occurrence overrides correct matches.
    """
    if script not in {"hiragana", "katakana"}:
        raise ValueError("script must be hiragana or katakana")
    if not isinstance(expected, str) or not isinstance(actual, str):
        raise TypeError("expected and actual must be strings")
    expected, actual = _nfc(expected), _nfc(actual)
    canonical = romanize_kana(expected)
    result = {"version": 1, "expected": expected, "actual": actual, "script": script,
              "answer_direction": "romaji", "expected_romaji": canonical,
              "assisted": bool(assisted), "correct": check_word_romaji_answer(expected, actual),
              "units": [], "errors": [], "ambiguous": False, "evidence_limited": False}
    if not actual:
        if expected and not assisted:
            result["errors"] = [{"type": "empty", "expected": canonical, "actual": "", "position": 0,
                                 "message": "尚未选择罗马音，本次不调整单个假名的记忆记录。"}]
        return result
    if len(expected) > MAX_SPELLING_LENGTH or len(actual) > MAX_ROMAJI_LENGTH:
        result["evidence_limited"] = True
        return result
    symbols, ids = _catalog(kana_rows, script)
    units = _tokenize(expected, symbols, ids)
    mapped = _target_letters(expected, units)
    right = _comparison(actual).translate(_LONG_VOWELS)
    if mapped is None or not _EXPANDED.fullmatch(right) or len(right) > MAX_ROMAJI_LENGTH:
        result["evidence_limited"] = True
        return result
    left, owners = mapped
    if not left or len(left) > MAX_ROMAJI_LENGTH:
        result["evidence_limited"] = True
        return result
    # Equal virtual sounds with a different canonical option do not establish
    # which written long-vowel unit was recalled. Do not reward a wrong choice.
    if left == right and not result["correct"]:
        result["evidence_limited"] = True
        return result
    mapped_units = {owner for owner in owners if owner is not None}
    id_order = list(dict.fromkeys(unit.content_id for index, unit in enumerate(units)
                                 if unit.content_id and index in mapped_units))
    id_bit = {cid: 1 << index for index, cid in enumerate(id_order)}
    def bit_at(index):
        owner = owners[index]
        return id_bit.get(units[owner].content_id, 0) if owner is not None else 0

    def edges(i, j):
        if i < len(left) and j < len(right):
            wrong = left[i] != right[j]
            yield 1, 1, int(wrong), bit_at(i) if wrong else 0, "substitution"
        if i < len(left):
            yield 1, 0, 1, bit_at(i), "missing"
        if j < len(right):
            # Extra Roman letters inside one syllable can change that kana
            # (ka -> kya). At a unit boundary their ownership is not assumed.
            interior = 0 < i < len(left) and owners[i - 1] is not None and owners[i - 1] == owners[i]
            yield 0, 1, 1, bit_at(i) if interior else 0, "extra"

    n, m = len(left), len(right)
    distance = [[n + m + 1] * (m + 1) for _ in range(n + 1)]
    distance[n][m] = 0
    for i in range(n, -1, -1):
        for j in range(m, -1, -1):
            if i != n or j != m:
                distance[i][j] = min(cost + distance[i + di][j + dj]
                                     for di, dj, cost, _, _ in edges(i, j))
    states = {(0, 0): (0, 0, 1)}
    error_types = {cid: set() for cid in id_order}
    for i in range(n + 1):
        for j in range(m + 1):
            if (i, j) not in states:
                continue
            must, may, paths = states[i, j]
            for di, dj, cost, failed, kind in edges(i, j):
                dest = (i + di, j + dj)
                if cost + distance[dest[0]][dest[1]] != distance[i][j]:
                    continue
                for cid, bit in id_bit.items():
                    if failed & bit:
                        error_types[cid].add(kind)
                next_state = must | failed, may | failed, paths
                if dest in states:
                    previous = states[dest]
                    next_state = previous[0] & next_state[0], previous[1] | next_state[1], min(2, previous[2] + paths)
                states[dest] = next_state
    must, may, paths = states[n, m]
    result["ambiguous"] = paths > 1
    if not assisted:
        for cid in id_order:
            bit = id_bit[cid]
            if may & bit and not must & bit:
                continue
            symbol = next(unit.text for unit in units if unit.content_id == cid)
            result["units"].append({"content_id": cid, "symbol": symbol,
                "result": "incorrect" if must & bit else "correct",
                "error_types": [kind for kind in _ERROR_ORDER if kind in error_types[cid]]})
    if not result["correct"] and not assisted:
        result["errors"] = [{"type": "substitution", "expected": canonical, "actual": actual,
            "position": 0, "message": "所选罗马音与这个单词的假名不一致。"}]
    return result


__all__ = ["check_word_romaji_answer", "analyze_word_romaji"]
