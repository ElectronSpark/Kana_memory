"""Resolve the selected answer to catalog records without changing grading.

Romanization can name several kana. Keep all matching modern spellings so the
caller can explain ambiguity, while placing the actual target first only when
the selected romanization is the question's correct option.
"""
from __future__ import annotations

from collections.abc import Mapping
import unicodedata

from .domain import (Question, _eligible_kana, _romaji_values, _same_kana_shape,
                     normalize_answer, normalize_romaji)


def _kana_spelling(value: str) -> str:
    """Normalize halfwidth/combining kana for lookup, never for scoring."""
    text = value.strip()
    if not text or any(not ("\u3040" <= char <= "\u30ff" or "\uff66" <= char <= "\uff9f")
                       for char in text):
        return ""
    return normalize_answer(unicodedata.normalize("NFKC", text))


def answer_kana_records(question: Question, answer: str, records: list[dict]) -> list[dict]:
    """Return catalog-backed records for the option the user actually chose.

    Reading choices follow the question generator's modern, same-shape pool
    and primary romanizations. Kana choices match their exact script, size and
    spelling, including a wrongly selected homophone; they never use the
    target's spelling as a substitute. Input keys and free writing are outside
    this resolver's scope.
    """
    if not isinstance(answer, str) or question.script not in {"hiragana", "katakana"}:
        return []
    script = question.script
    if question.kind in {"romaji_kana", "kana_handwriting"}:
        spelling = _kana_spelling(answer)
        if not spelling:
            return []
        return [row for row in records if isinstance(row.get(script), str)
                and _kana_spelling(row[script]) == spelling]
    if question.kind != "kana_choice":
        return []

    selected = normalize_romaji(answer)
    if not selected:
        return []
    metadata = question.metadata if isinstance(question.metadata, Mapping) else {}
    target = next((row for row in records if row.get("id") == question.content_id), None)
    if target is None:
        symbol = metadata.get("kana_symbol")
        if not isinstance(symbol, str) or not _kana_spelling(symbol):
            return []
        target = {script: _kana_spelling(symbol)}
    if not target.get(script):
        return []

    matches = []
    for row in records:
        if not _eligible_kana(row, script, True, audio_only=False) or not _same_kana_shape(target, row, script):
            continue
        romanizations = _romaji_values(row)
        if romanizations and normalize_romaji(romanizations[0]) == selected:
            matches.append(row)

    # An in-progress pre-distinction snapshot still displays its original
    # answer "o" for を. Keep that saved question's correct target reachable;
    # newly generated spelling questions use "wo" and never enter this path.
    if (selected == normalize_romaji(question.answer) == "o"
            and target.get(script) in {"を", "ヲ"} and target not in matches):
        matches.append(target)

    config = metadata.get("practice_config")
    categories = config.get("kana_categories") if isinstance(config, Mapping) else None
    scope = {category for category in categories if isinstance(category, str)} if isinstance(categories, (list, tuple)) else set()
    correct_option = selected == normalize_romaji(question.answer)
    # Python's stable sort preserves catalog order after these two priorities.
    matches.sort(key=lambda row: (not (correct_option and row.get("id") == question.content_id),
                                  row.get("category") not in scope))
    return matches
