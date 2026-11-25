"""A typed kana answer one or two kana away from an accepted answer, and how it differs (design 03).

Pure: no Toga, learner database, platform, network, clock or content loading. Both sides arrive already
normalised by the judge (typed_answer_normal: spaces and punctuation dropped, romaji already converted
to kana by the input); the distance compares them with katakana folded to hiragana (overlay.fold_katakana),
so the kana of either script count alike, while the lines below name the kana as they were typed.

Distance: insert, delete or substitute one kana each (the long-vowel mark and small kana count as kana).
A near miss is an answer whose NEAREST kana accepted answer is at distance 1 when that answer has at least
3 kana, or at distance 1 or 2 when it has at least 5; never for shorter accepted answers, never at
distance 0 (the same kana in the other script is the judge's script rule), and never for an answer in
``exempt`` (a form the judge already knows for this same lesson; another lesson's word one or two kana
away is a near miss). ``describe`` names each difference of the alignment in one short
Chinese line: 长音, 促音, 拨音, 浊音·半浊音, 拗音, else 少了「x」／多了「x」／「x」应为「y」.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .overlay import fold_katakana

MIN_ONE, MIN_TWO = 3, 5   # the shortest accepted answer for distance 1, and for distance 2
_KANA = re.compile(r"[ぁ-ゖァ-ヺー]+")
_VOWEL_ROWS = {"a": "あかさたなはまやらわがざだばぱぁゃゎ", "i": "いきしちにひみりぎじぢびぴぃ",
               "u": "うくすつぬふむゆるぐずづぶぷぅゅゔ", "e": "えけせてねへめれげぜでべぺぇ",
               "o": "おこそとのほもよろをごぞどぼぽぉょ"}
_VOWEL = {kana: vowel for vowel, row in _VOWEL_ROWS.items() for kana in row}
# The kana that lengthen a syllable with each vowel (おねえさん, せんせい, とうきょう, おおきい).
_LENGTHENING = {"a": "あ", "i": "い", "u": "う", "e": "いえ", "o": "うお"}
_SMALL_YOUON = {"ゃ": "や", "ゅ": "ゆ", "ょ": "よ"}


def is_kana(text) -> bool:
    """``text`` is non-empty and only hiragana, katakana and the long-vowel mark."""
    return isinstance(text, str) and _KANA.fullmatch(text) is not None


def allowed_distance(length: int) -> int:
    """The largest distance that is a near miss of an accepted answer of ``length`` kana (0: none)."""
    return 2 if length >= MIN_TWO else 1 if length >= MIN_ONE else 0


def _table(expected, actual):
    n, m = len(expected), len(actual)
    costs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n, -1, -1):
        for j in range(m, -1, -1):
            if i == n or j == m:
                costs[i][j] = (n - i) + (m - j)
            else:
                costs[i][j] = min((expected[i] != actual[j]) + costs[i + 1][j + 1],
                                  1 + costs[i + 1][j], 1 + costs[i][j + 1])
    return costs


def distance(expected: str, actual: str) -> int:
    """The edit distance of two kana strings, katakana folded (insert, delete, substitute)."""
    return _table(fold_katakana(expected), fold_katakana(actual))[0][0]


def alignment(expected: str, actual: str) -> tuple[tuple[str, str], ...]:
    """A minimum alignment as (expected kana, typed kana) pairs, "" for a missing side; the kana are the
    original ones, the comparison folds katakana. Ties: the same position first, then a missing kana."""
    left, right = fold_katakana(expected), fold_katakana(actual)
    costs = _table(left, right)
    pairs, i, j = [], 0, 0
    n, m = len(left), len(right)
    while i < n or j < m:
        if i < n and j < m and costs[i][j] == (left[i] != right[j]) + costs[i + 1][j + 1]:
            pairs.append((expected[i], actual[j]))
            i, j = i + 1, j + 1
        elif i < n and costs[i][j] == 1 + costs[i + 1][j]:
            pairs.append((expected[i], ""))
            i += 1
        else:
            pairs.append(("", actual[j]))
            j += 1
    return tuple(pairs)


def _base(kana):
    return unicodedata.normalize("NFD", kana)[0]


def _lengthens(kana, before):
    """``kana`` lengthens the syllable ``before`` (the kana in front of it on its own side)."""
    if kana == "ー":
        return True
    vowel = _VOWEL.get(fold_katakana(before or ""))
    return vowel is not None and fold_katakana(kana) in _LENGTHENING[vowel]


def _kind(expected, typed, before):
    """The name of one difference: 长音, 促音, 拨音, 浊音·半浊音, 拗音, or "" (plain)."""
    one, other = fold_katakana(expected), fold_katakana(typed)
    present = [x for x in (one, other) if x]
    if len(present) == 1:
        kana = present[0]
        if kana == "っ":
            return "促音"
        if kana == "ん":
            return "拨音"
        if kana in _SMALL_YOUON:
            return "拗音"
        return "长音" if _lengthens(kana, before) else ""
    if {one, other} == {"っ", "つ"}:
        return "促音"
    if _SMALL_YOUON.get(one) == other or _SMALL_YOUON.get(other) == one:
        return "拗音"
    if _base(one) == _base(other):
        return "浊音·半浊音"
    if _lengthens(one, before) and _lengthens(other, before):
        return "长音"
    return ""


def describe(pairs) -> tuple[str, ...]:
    """One short Chinese line per difference of an ``alignment``, in order (the kana as written)."""
    lines, before = [], ""
    for expected, typed in pairs:
        if fold_katakana(expected) != fold_katakana(typed):
            kind = _kind(expected, typed, before)
            text = (f"少了「{expected}」" if not typed else f"多了「{typed}」" if not expected
                    else f"「{typed}」应为「{expected}」")
            lines.append(f"{kind}：{text}" if kind else text)
        before = expected or typed or before
    return tuple(lines)


@dataclass(frozen=True)
class NearMiss:
    """answer: the typed kana; target: the nearest accepted answer; pairs: their alignment; lines: P3."""
    answer: str
    target: str
    distance: int
    pairs: tuple[tuple[str, str], ...]
    lines: tuple[str, ...]


def find(answer: str, accepted, *, exempt=()) -> NearMiss | None:
    """The near miss of ``answer`` (normalised) to its nearest kana answer of ``accepted``, or None."""
    if not is_kana(answer) or fold_katakana(answer) in {fold_katakana(x) for x in exempt if isinstance(x, str)}:
        return None
    candidates = list(dict.fromkeys(x for x in accepted if is_kana(x)))
    if not candidates:
        return None
    scored = [(distance(target, answer), target) for target in candidates]
    nearest = min(score for score, _ in scored)
    if nearest == 0:
        return None
    for score, target in scored:
        if score == nearest and nearest <= allowed_distance(len(target)):
            pairs = alignment(target, answer)
            return NearMiss(answer, target, nearest, pairs, describe(pairs))
    return None
