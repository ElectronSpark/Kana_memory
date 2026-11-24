"""Kanji numerals and digits made the same for comparing typed answers (rule T5, design 03).

Pure. For COMPARISON only: what a page shows never changes. Full-width digits already become ASCII
digits in the judge's NFKC normal form; this folds kanji numerals into ASCII digits after it:
- a maximal run of 〇零一二三四五六七八九十百千万 with 十, 百, 千 or 万 that is a well-formed number
  becomes its value (二十三 -> 23, 三百 -> 300, 一万 -> 10000, 十 -> 10); 万 needs a coefficient
  (一万, never 万 alone), every unit appears at most once and in falling order (十二十 is left alone);
- a run without 十百千万 is read digit by digit (二〇二六 -> 2026, 一 -> 1);
- anything else is left alone. No reading is ever inferred: 2さつ is not にさつ.
"""
from __future__ import annotations

import re

DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
UNITS = {"十": 10, "百": 100, "千": 1000}
MAN = "万"
_RUN = re.compile("[〇零一二三四五六七八九十百千万]+")


def _below_man(text: str) -> int | None:
    """The value of a well-formed numeral below 一万 ([d]千[d]百[d]十[d]), or None."""
    if not text:
        return None
    value, last_unit, index = 0, 10000, 0
    while index < len(text):
        char = text[index]
        if char in UNITS:
            coefficient = 1
        elif char in DIGITS and DIGITS[char] > 0:
            coefficient = DIGITS[char]
            index += 1
            if index == len(text):
                return value + coefficient  # the ones digit ends the numeral
            char = text[index]
            if char not in UNITS:
                return None
        else:
            return None
        unit = UNITS[char]
        if unit >= last_unit:
            return None
        value += coefficient * unit
        last_unit = unit
        index += 1
    return value


def numeral_value(run: str) -> int | None:
    """The value of a run with a unit (十百千万) when it is a well-formed number, else None."""
    if MAN in run:
        head, _, tail = run.partition(MAN)
        if MAN in tail:
            return None
        high = _below_man(head)
        if high is None:
            return None
        low = _below_man(tail) if tail else 0
        return None if low is None else high * 10000 + low
    return _below_man(run)


def _fold(match: re.Match) -> str:
    run = match.group()
    if not any(char in UNITS or char == MAN for char in run):
        return "".join(str(DIGITS[char]) for char in run)
    value = numeral_value(run)
    return run if value is None else str(value)


def fold_numbers(text: str) -> str:
    """``text`` with every kanji numeral run folded as the module text says."""
    return _RUN.sub(_fold, text) if isinstance(text, str) else text
