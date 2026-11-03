"""Describe recognizer evidence and keep practice guesses in its chosen script."""
from __future__ import annotations

import unicodedata


def ink_candidate_kind(text: str) -> str:
    """Label the actual characters without rewriting the recognizer's candidate.

    Width/composition normalization is used only to identify the character
    class. The UI still displays and confirms the original recognized string.
    """
    kinds = []
    has_prolongation = False
    for char in unicodedata.normalize("NFKC", text):
        if char.isspace():
            continue
        name = unicodedata.name(char, "")
        if char == "ー":
            has_prolongation = True
            continue
        if name.startswith("HIRAGANA "):
            kind = "平假名"
        elif name.startswith("KATAKANA "):
            kind = "片假名"
        elif name.startswith(("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH")) or char in "々〇":
            kind = "汉字"
        elif name.startswith("LATIN "):
            kind = "拉丁字母"
        elif char.isnumeric():
            kind = "数字"
        elif unicodedata.category(char).startswith("L"):
            kind = "其他文字"
        else:
            kind = "符号"
        if kind not in kinds:
            kinds.append(kind)
    if has_prolongation and not any(kind in kinds for kind in ("平假名", "片假名")):
        kinds.append("符号")
    return "／".join(dict.fromkeys(kinds)) or "其他"


def practice_ink_candidates(candidates, script: str, limit: int = 5) -> list[str]:
    """Normalize width/composition, without transliteration or answer injection.

    The model can return visually similar kanji or Latin punctuation. Practice
    offers only its requested kana script; free writing uses the raw candidates
    instead. Normalization preserves kana size and voicing.
    """
    if script not in {"hiragana", "katakana"}:
        raise ValueError("Unknown handwriting script")
    if type(limit) is not int or limit < 0:
        raise ValueError("Candidate limit must be a nonnegative integer")
    if not limit:
        return []
    first, last = ("ぁ", "ゖ") if script == "hiragana" else ("ァ", "ヺ")
    iteration = "ゝゞ" if script == "hiragana" else "ヽヾ"
    result = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        text = unicodedata.normalize("NFKC", candidate).strip()
        if (not text or text in result or
                not all(first <= char <= last or char in iteration or char == "ー" for char in text)):
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result
