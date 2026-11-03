"""One kana character per Roman-fragment choice, retaining word context.

Fragments are pieces of the word's spelling, not isolated pronunciations:
きゃ is k / ya and しゃ is sh / a. Written long marks use a vowel choice,
then compose it as a macron at that exact slot; ordinary おお stays oo.
"""
from __future__ import annotations

import hashlib
import random
import re
import unicodedata

from .romanization import _GEMINATABLE, _MAPPING, _MATCH_LENGTHS, romanize_kana
from .word_spelling import MAX_SPELLING_LENGTH, _catalog, _tokenize


_VOWELS = frozenset("aeiou")
_MACRON = {"a": "ā", "e": "ē", "i": "ī", "o": "ō", "u": "ū"}
_ROMAN = re.compile(r"[a-z']+\Z")
_TRIPLES = {base + "ぃ" + small for base in "てで" for small in "ゃゅょ"}


def _kana(value):
    if not isinstance(value, str):
        raise TypeError("kana must be a string")
    value = unicodedata.normalize("NFC", value).strip()
    if not value or len(value) > MAX_SPELLING_LENGTH:
        raise ValueError("Kana fragment plan requires a nonempty, bounded word")
    if any(not ("ぁ" <= char <= "ゖ" or "ァ" <= char <= "ヺ" or char == "ー") for char in value):
        raise ValueError("Kana fragment plan requires kana characters only")
    if unicodedata.normalize("NFKC", value) != value:
        raise ValueError("Kana fragment plan requires normalized full-width kana")
    return value


def _compose(units, parts):
    result = ""
    for unit, part in zip(units, parts):
        if unit["operation"] == "length" and result and result[-1] == part:
            result = result[:-1] + _MACRON[part]
        else:
            # A repeated mark after an existing macron adds its base vowel,
            # matching the canonical engine. Wrong vowel selections remain
            # visible rather than silently becoming the correct long vowel.
            result += part
    return result


def _tokens(kana):
    folded = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in kana)
    tokens, cursor = [], 0
    while cursor < len(folded):
        char = folded[cursor]
        if char in {"っ", "ん", "ー"}:
            tokens.append(({"っ": "sokuon", "ん": "nasal", "ー": "long"}[char], cursor, cursor + 1, char))
            cursor += 1
            continue
        size = next((size for size in _MATCH_LENGTHS
                     if cursor + size <= len(folded)
                     and folded[cursor:cursor + size] in _MAPPING), None)
        if size is None:
            raise ValueError("Unsupported kana in Roman-fragment plan")
        end = cursor + size
        tokens.append(("kana", cursor, end, _MAPPING[folded[cursor:end]]))
        cursor = end
    return folded, tokens


def _expected_units(kana):
    folded, tokens = _tokens(kana)
    units = []
    for index, (kind, start, end, roman) in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        operation = "append"
        if kind == "nasal":
            pieces = ["n'" if following and following[0] == "kana" and following[3][0] in "aeiouy" else "n"]
        elif kind == "sokuon":
            if not following or following[0] != "kana" or following[3][0] not in _GEMINATABLE:
                raise ValueError("Sokuon needs a supported following consonant")
            pieces = [following[3][0]]
        elif kind == "long":
            if not index or tokens[index - 1][0] not in {"kana", "long"} or not units:
                raise ValueError("Long mark needs a supported preceding vowel")
            vowel = units[-1]["answer"][-1]
            if vowel not in _VOWELS:
                raise ValueError("Long mark needs a supported preceding vowel")
            pieces, operation = [vowel], "length"
        elif end - start == 1:
            pieces = [roman]
        elif end - start == 2:
            tail = _MAPPING.get(folded[end - 1], "")
            # Prefer k / ya, f / yu and k / wa. Contracted sh / a and ch / a,
            # plus foreign t / i and ts / e, retain their actual Roman stem.
            if tail and roman.endswith(tail) and len(roman) > len(tail):
                pieces = [roman[:-len(tail)], tail]
            else:
                pieces = [roman[:-1], roman[-1]]
        elif folded[start:end] in _TRIPLES and len(roman) >= 3:
            pieces = [roman[0], roman[1:-1], roman[-1]]
        else:
            raise ValueError("Unsupported multi-character Roman-fragment mapping")
        if len(pieces) != end - start or any(not piece or not _ROMAN.fullmatch(piece) for piece in pieces):
            raise ValueError("Roman-fragment mapping cannot be assigned to every kana")
        units.extend({"kana": char, "answer": piece, "operation": operation}
                     for char, piece in zip(kana[start:end], pieces))
    if len(units) != len(kana) or _compose(units, [u["answer"] for u in units]) != romanize_kana(kana):
        raise ValueError("Roman-fragment plan disagrees with canonical word spelling")
    return units


_SYLLABLES = frozenset(value for key, value in _MAPPING.items() if len(key) == 1)
_STEMS = frozenset({"n", "n'"} | {unit["answer"] for key in _MAPPING
    for unit in _expected_units(key) if unit["answer"][-1] not in _VOWELS})
_NASALS = frozenset({"n", "n'", "na", "ni", "nu", "ne", "no"})


def _option_pool(unit):
    answer = unit["answer"]
    if unit["operation"] == "length" or answer in _VOWELS:
        pool = _VOWELS
    elif answer in {"n", "n'"}:
        pool = _NASALS
    elif answer[-1] not in _VOWELS:
        pool = _STEMS
    else:
        pool = _SYLLABLES
    return pool | {answer}


def build_romaji_unit_plan(kana, seed=None):
    """Create fixed four-choice controls for every NFC kana character."""
    kana = _kana(kana)
    if seed is None:
        seed = int.from_bytes(hashlib.sha256(kana.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    units = []
    for unit in _expected_units(kana):
        options = [unit["answer"], *rng.sample(sorted(_option_pool(unit) - {unit["answer"]}), 3)]
        rng.shuffle(options)
        units.append({**unit, "options": options})
    return {"version": 1, "units": units}


def validate_romaji_unit_plan(kana, plan):
    """Reject malformed plans, changed semantics or word-sized distractors."""
    kana = _kana(kana)
    if (not isinstance(plan, dict) or set(plan) != {"version", "units"}
            or type(plan["version"]) is not int or plan["version"] != 1
            or not isinstance(plan["units"], list)):
        raise ValueError("Invalid Roman-fragment plan")
    expected = _expected_units(kana)
    if len(plan["units"]) != len(expected):
        raise ValueError("Roman-fragment plan must contain one step per kana")
    for actual, unit in zip(plan["units"], expected):
        if (not isinstance(actual, dict) or set(actual) != {"kana", "answer", "operation", "options"}
                or any(actual[key] != value for key, value in unit.items())):
            raise ValueError("Roman-fragment plan has incorrect kana semantics")
        options = actual["options"]
        if (not isinstance(options, list) or len(options) != 4
                or any(not isinstance(option, str) or option not in _option_pool(unit) for option in options)
                or len(set(options)) != 4 or unit["answer"] not in options):
            raise ValueError("Roman-fragment plan needs four distinct unit choices")


def _parts(plan, parts):
    if not isinstance(parts, (list, tuple)) or any(not isinstance(part, str) for part in parts):
        raise TypeError("Roman fragments must be a list or tuple of strings")
    if len(parts) > len(plan["units"]) or any(part not in unit["options"] for unit, part in zip(plan["units"], parts)):
        raise ValueError("Roman fragments do not match the saved choices")
    return list(parts)


def compose_romaji_parts(plan, parts):
    """Render a selected prefix, applying only explicit written-length slots."""
    if not isinstance(plan, dict) or not isinstance(plan.get("units"), list):
        raise ValueError("Invalid Roman-fragment plan")
    try:
        kana = "".join(unit["kana"] for unit in plan["units"])
    except (KeyError, TypeError):
        raise ValueError("Invalid Roman-fragment plan") from None
    validate_romaji_unit_plan(kana, plan)
    return _compose(plan["units"], _parts(plan, parts))


def check_romaji_parts(kana, parts, plan):
    """Grade exact aligned choices: concatenation cannot hide a wrong slot."""
    validate_romaji_unit_plan(kana, plan)
    return _parts(plan, parts) == [unit["answer"] for unit in plan["units"]]


def analyze_romaji_parts(kana, parts, script, kana_rows, plan, *, assisted=False):
    """Aggregate aligned slots into existing catalog units without new cards.

    き and ゃ are two controls but together supply evidence for the known きゃ
    record. Contextual k must not pretend to test the isolated き card.
    """
    if script not in {"hiragana", "katakana"}:
        raise ValueError("script must be hiragana or katakana")
    kana = _kana(kana)
    validate_romaji_unit_plan(kana, plan)
    parts = _parts(plan, parts)
    correct = parts == [unit["answer"] for unit in plan["units"]]
    result = {"version": 1, "expected": kana, "actual": _compose(plan["units"], parts), "script": script,
              "answer_direction": "romaji", "expected_romaji": romanize_kana(kana),
              "romaji_unit_version": 1, "romaji_parts": parts,
              "assisted": bool(assisted), "correct": correct and not assisted,
              "units": [], "errors": [], "ambiguous": False, "evidence_limited": False}
    if assisted:
        return result
    if not parts:
        result["errors"] = [{"type": "empty", "expected": result["expected_romaji"], "actual": "", "position": 0,
                             "message": "尚未选择罗马音片段，本次不调整单个假名的记忆记录。"}]
        return result
    errors = {}
    for index, unit in enumerate(plan["units"]):
        actual = parts[index] if index < len(parts) else ""
        if actual != unit["answer"]:
            kind = "substitution" if actual else "missing"
            errors[index] = kind
            result["errors"].append({"type": kind, "expected": unit["answer"], "actual": actual,
                "position": index, "message": f"「{unit['kana']}」在这个词中的罗马音片段应为 {unit['answer']}。"})
    symbols, ids = _catalog(kana_rows, script)
    _, sound_tokens = _tokens(kana)
    sound_spans = {(start, end) for _, start, end, _ in sound_tokens}
    aggregated, cursor = {}, 0
    for token in _tokenize(kana, symbols, ids):
        span = cursor, cursor + len(token.text)
        token_errors = {errors[index] for index in range(cursor, cursor + len(token.text)) if index in errors}
        cursor += len(token.text)
        # A dictionary may know てぃ but not the larger てぃゃ contraction.
        # Its partial prefix is not the sound actually tested by these slots.
        if not token.content_id or span not in sound_spans:
            continue
        entry = aggregated.setdefault(token.content_id, {"content_id": token.content_id,
            "symbol": token.text, "result": "correct", "error_types": []})
        if token_errors:
            entry["result"] = "incorrect"
            entry["error_types"] = [kind for kind in ("substitution", "missing")
                                    if kind in token_errors or kind in entry["error_types"]]
    result["units"] = list(aggregated.values())
    return result


__all__ = ["build_romaji_unit_plan", "validate_romaji_unit_plan", "compose_romaji_parts",
           "check_romaji_parts", "analyze_romaji_parts"]
