"""Conservative, bounded spelling evidence from a whole-word kana answer.

This module does not grade or schedule cards.  Only catalog-backed, unambiguous
evidence is returned; presentation still explains untracked spelling marks.
"""
from __future__ import annotations

from dataclasses import dataclass
import unicodedata


MAX_SPELLING_LENGTH = 128
_SMALL = "ぁぃぅぇぉゃゅょゎゕゖァィゥェォャュョヮヵヶっッ"
_LARGE = "あいうえおやゆよわかけアイウエオヤユヨワカケつツ"
_SIZE_MAP = str.maketrans(_SMALL, _LARGE)
_JOIN_SMALL = set(_SMALL) - {"っ", "ッ"}
_MARKS = {"\u3099", "\u309a"}
_ERROR_ORDER = ("script", "homophone", "voicing", "size", "substitution", "missing", "extra", "order")
_ORTHOGRAPHY_ERRORS = frozenset({"script", "homophone"})


def reading_evidence_unit(unit: dict) -> bool:
    """Do not infer a sound failure from an orthography-only diagnostic.

    New analyses keep these in ``orthography_units``. This guard also protects
    callers holding older/custom analysis objects instead of a fresh analysis.
    """
    kinds = unit.get("error_types", ())
    return not (unit.get("result") == "incorrect" and kinds
                and set(kinds) <= _ORTHOGRAPHY_ERRORS)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _hira(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def _unvoiced(text: str) -> str:
    return _nfc("".join(c for c in unicodedata.normalize("NFD", text) if c not in _MARKS))


def _shape(text: str) -> str:
    return _unvoiced(_hira(text)).translate(_SIZE_MAP)


def _differences(expected: str, actual: str) -> tuple[str, ...]:
    if expected == actual:
        return ()
    from .kana_orthography import same_kana_sound
    if same_kana_sound(expected, actual, fold_script=True):
        kinds = ["script"] if _hira(expected) == _hira(actual) else ["homophone"]
        if kinds == ["homophone"] and any(("ァ" <= a <= "ヶ") != ("ァ" <= b <= "ヶ")
                                          for a, b in zip(expected, actual)):
            kinds.insert(0, "script")
        return tuple(kinds)
    if _shape(expected) != _shape(actual):
        return ("substitution",)
    kinds = []
    if any(("ァ" <= a <= "ヶ") != ("ァ" <= b <= "ヶ")
           for a, b in zip(expected, actual)):
        kinds.append("script")
    left, right = _hira(expected), _hira(actual)
    if unicodedata.normalize("NFD", left).count("\u3099") != unicodedata.normalize("NFD", right).count("\u3099") or \
       unicodedata.normalize("NFD", left).count("\u309a") != unicodedata.normalize("NFD", right).count("\u309a"):
        kinds.append("voicing")
    elif _unvoiced(left) != left and left.translate(_SIZE_MAP) != right.translate(_SIZE_MAP):
        # Distinguish moved marks too, rather than only comparing mark counts.
        kinds.append("voicing")
    if _unvoiced(left) != _unvoiced(right):
        kinds.append("size")
    return tuple(kinds) or ("substitution",)


@dataclass(frozen=True)
class _Token:
    text: str
    content_id: str | None


def _catalog(kana_rows: list[dict], script: str):
    if not isinstance(kana_rows, list) or any(not isinstance(row, dict) for row in kana_rows):
        raise TypeError("kana_rows must be a list of dictionaries")
    expected_ids: dict[str, set[str]] = {}
    symbols = set()
    for row in kana_rows:
        content_id = row.get("id", row.get("content_id"))
        for key in ("hiragana", "katakana"):
            symbol = row.get(key)
            if not isinstance(symbol, str) or not 0 < len(symbol) <= 8:
                continue
            symbol = _nfc(symbol)
            symbols.add(symbol)
            if key == script and isinstance(content_id, str) and content_id:
                expected_ids.setdefault(symbol, set()).add(content_id)
    return symbols, {symbol: next(iter(ids)) for symbol, ids in expected_ids.items() if len(ids) == 1}


def _tokenize(text: str, symbols: set[str], ids: dict[str, str]) -> list[_Token]:
    tokens = []
    cursor = 0
    while cursor < len(text):
        symbol = next((text[cursor:cursor + size] for size in range(min(8, len(text) - cursor), 0, -1)
                       if text[cursor:cursor + size] in symbols), text[cursor])
        # Unknown contractions must not accidentally reward their base kana.
        if len(symbol) == 1 and cursor + 1 < len(text) and text[cursor + 1] in _JOIN_SMALL and \
                ("ぁ" <= symbol <= "ゖ" or "ァ" <= symbol <= "ヶ"):
            symbol += text[cursor + 1]
        tokens.append(_Token(symbol, ids.get(symbol)))
        cursor += len(symbol)
    return tokens


@dataclass(frozen=True)
class _Edge:
    di: int
    dj: int
    cost: int
    # A status for every consumed expected token. Empty tuple means correct.
    kinds: tuple[tuple[str, ...], ...]
    label: str = ""


def _edges(left: list[_Token], right: list[_Token], i: int, j: int):
    if i < len(left) and j < len(right):
        expected, actual = left[i].text, right[j].text
        if actual.startswith(expected) and actual != expected and set(actual[len(expected):]) <= _JOIN_SMALL:
            # A superfluous small kana must not make its correct base wrong.
            yield _Edge(1, 1, 1, ((),), "extra")
        elif expected.startswith(actual) and actual != expected and set(expected[len(actual):]) <= _JOIN_SMALL:
            yield _Edge(1, 1, 1, (("missing",),), "missing")
        else:
            kinds = _differences(expected, actual)
            # Align equivalent sounds directly before inventing a deletion,
            # insertion or transposition around repeated homophones. The
            # spelling error remains in kinds even though its sound cost is 0.
            sound_cost = int(bool(kinds) and not set(kinds) <= _ORTHOGRAPHY_ERRORS)
            yield _Edge(1, 1, sound_cost, (kinds,))
        if j + 1 < len(right):
            joined = right[j].text + right[j + 1].text
            kinds = _differences(left[i].text, joined)
            if kinds and "substitution" not in kinds:
                yield _Edge(1, 2, 1, (kinds,))
        if i + 1 < len(left):
            joined = left[i].text + left[i + 1].text
            kinds = _differences(joined, right[j].text)
            if kinds and "substitution" not in kinds:
                boundary = len(left[i].text)
                yield _Edge(2, 1, 1, (
                    _differences(left[i].text, right[j].text[:boundary]),
                    _differences(left[i + 1].text, right[j].text[boundary:]),
                ))
        if i + 1 < len(left) and j + 1 < len(right) and left[i].text != left[i + 1].text and \
                left[i].text == right[j + 1].text and left[i + 1].text == right[j].text:
            yield _Edge(2, 2, 1, (("order",), ("order",)), "order")
    if i < len(left):
        yield _Edge(1, 0, 1, (("missing",),), "missing")
    if j < len(right):
        yield _Edge(0, 1, 1, (), "extra")


def _message(kind: str, expected: str, actual: str, position: int) -> str:
    if kind == "homophone":
        return f"「{actual}」与「{expected}」读音相同，这个词应写「{expected}」。"
    if kind == "missing":
        if actual and expected.startswith(actual):
            return f"「{actual}」后漏写「{expected[len(actual):]}」，应为「{expected}」。"
        return f"漏写「{expected}」，在第 {position + 1} 个假名处补上。"
    if kind == "extra":
        if expected and actual.startswith(expected):
            actual = actual[len(expected):]
        return f"多写「{actual}」，删去这一处。"
    if kind == "order":
        return f"顺序写反了：「{actual}」应为「{expected}」。"
    detail = {"voicing": "检查浊点或半浊点", "size": "注意小假名与普通假名的大小", "script": "使用题目要求的平假名或片假名"}.get(kind)
    suffix = f"；{detail}" if detail else ""
    return f"「{actual}」应为「{expected}」{suffix}。"


def analyze_spelling(expected: str, actual: str, script: str, kana_rows: list[dict], *,
                     assisted: bool = False, accepted_answers=()) -> dict:
    """Return JSON-safe evidence, without changing size, voicing, or script.

    Positions are zero-based expected *unit* positions. Repeated catalog IDs
    contribute once and any definite error takes priority. If optimal edit
    paths disagree whether an ID was wrong, that ID supplies no evidence.
    ``accepted_answers`` must already be reviewed whole-word spellings. A
    noncanonical accepted spelling supplies no target-glyph memory evidence.
    Same-sound or script-only mistakes are retained as orthography diagnostics,
    never as failures in the ``units`` consumed by reading-review scheduling.
    """
    if script not in {"hiragana", "katakana"}:
        raise ValueError("script must be hiragana or katakana")
    if not isinstance(expected, str) or not isinstance(actual, str):
        raise TypeError("expected and actual must be strings")
    if not isinstance(accepted_answers, (list, tuple)) or any(not isinstance(x, str) for x in accepted_answers):
        raise TypeError("accepted_answers must be a list or tuple of reviewed spellings")
    # Match the practice grader: trim only the answer boundary, never internal
    # whitespace, kana size, voicing, script, or long-vowel spelling.
    expected, actual = _nfc(expected).strip(), _nfc(actual).strip()
    result = {"version": 1, "expected": expected, "actual": actual, "script": script,
              "assisted": bool(assisted), "correct": expected == actual, "units": [], "errors": [],
              "ambiguous": False, "evidence_limited": False,
              "diagnostic_version": 1, "error_category": None, "orthography_units": []}
    if expected and not actual:
        if not assisted:
            result["error_category"] = "unresolved"
            result["errors"] = [{"type": "empty", "expected": expected, "actual": "", "position": 0,
                                 "message": "尚未输入假名；先尝试拼写，本次不调整单个假名的记忆记录。"}]
        return result
    if max(len(expected), len(actual)) > MAX_SPELLING_LENGTH:
        result["evidence_limited"] = True
        result["error_category"] = "unresolved"
        result["errors"] = [{"type": "length", "expected": "", "actual": "", "position": 0,
                             "message": "输入过长，请缩短后重试；本次不调整单个假名的记忆记录。"}]
        return result
    if actual and actual != expected and actual in {_nfc(x).strip() for x in accepted_answers}:
        result.update(correct=True, accepted_alternative=True, evidence_limited=True)
        return result
    symbols, ids = _catalog(kana_rows, script)
    left, right = _tokenize(expected, symbols, ids), _tokenize(actual, symbols, {})
    n, m = len(left), len(right)
    # Suffix distances plus an optimal-edge DAG avoid enumerating exponentially
    # many equivalent paths for repeated syllables.
    distance = [[n + m + 1] * (m + 1) for _ in range(n + 1)]
    distance[n][m] = 0
    for i in range(n, -1, -1):
        for j in range(m, -1, -1):
            if i != n or j != m:
                distance[i][j] = min(edge.cost + distance[i + edge.di][j + edge.dj]
                                     for edge in _edges(left, right, i, j))
    id_order = list(dict.fromkeys(token.content_id for token in left if token.content_id))
    id_bit = {content_id: 1 << index for index, content_id in enumerate(id_order)}
    # For each node: any-error IDs on EVERY/ANY path, sound-error IDs on
    # EVERY/ANY path, and capped paths. An ambiguous spelling alignment must
    # not turn a possible same-sound mistake into a definite reading failure.
    states = {(0, 0): (0, 0, 0, 0, 1)}
    error_types = {content_id: set() for content_id in id_order}
    for i in range(n + 1):
        for j in range(m + 1):
            if (i, j) not in states:
                continue
            must, may, sound_must, sound_may, paths = states[i, j]
            for edge in _edges(left, right, i, j):
                dest = (i + edge.di, j + edge.dj)
                if edge.cost + distance[dest[0]][dest[1]] != distance[i][j]:
                    continue
                failed = sound_failed = 0
                for offset, kinds in enumerate(edge.kinds):
                    content_id = left[i + offset].content_id
                    if content_id and kinds:
                        failed |= id_bit[content_id]
                        error_types[content_id].update(kinds)
                        if not set(kinds) <= _ORTHOGRAPHY_ERRORS:
                            sound_failed |= id_bit[content_id]
                next_state = (must | failed, may | failed,
                              sound_must | sound_failed, sound_may | sound_failed, paths)
                if dest in states:
                    previous = states[dest]
                    next_state = (previous[0] & next_state[0], previous[1] | next_state[1],
                                  previous[2] & next_state[2], previous[3] | next_state[3],
                                  min(2, previous[4] + paths))
                states[dest] = next_state
    must, may, sound_must, sound_may, paths = states[n, m]
    result["ambiguous"] = paths > 1
    if not assisted:
        for content_id in id_order:
            bit = id_bit[content_id]
            if may & bit and not must & bit:
                continue
            if sound_may & bit and not sound_must & bit:
                continue
            symbol = next(token.text for token in left if token.content_id == content_id)
            destination = "orthography_units" if must & bit and not sound_may & bit else "units"
            result[destination].append({"content_id": content_id, "symbol": symbol,
                                       "result": "incorrect" if must & bit else "correct",
                                       "error_types": [kind for kind in _ERROR_ORDER if kind in error_types[content_id]]})
    # A deterministic minimum-edit explanation; ambiguous evidence above is
    # deliberately more conservative than this single displayed alignment.
    i = j = 0
    while i < n or j < m:
        candidates = [edge for edge in _edges(left, right, i, j)
                      if edge.cost + distance[i + edge.di][j + edge.dj] == distance[i][j]]
        edge = min(candidates, key=lambda e: (e.cost, e.label in {"missing", "extra"}, -(e.di + e.dj)))
        expected_part = "".join(token.text for token in left[i:i + edge.di])
        actual_part = "".join(token.text for token in right[j:j + edge.dj])
        kinds = {kind for token_kinds in edge.kinds for kind in token_kinds}
        if edge.label == "extra":
            kinds.add("extra")
        for kind in _ERROR_ORDER:
            if kind in kinds:
                result["errors"].append({"type": kind, "expected": expected_part, "actual": actual_part,
                                         "position": i,
                                         "category": "spelling" if kind in _ORTHOGRAPHY_ERRORS else "reading",
                                         "message": _message(kind, expected_part, actual_part, i)})
        i, j = i + edge.di, j + edge.dj
    categories = {error["category"] for error in result["errors"]}
    result["error_category"] = "mixed" if len(categories) > 1 else next(iter(categories), None)
    return result


def spelling_feedback(analysis: dict) -> list[str]:
    """Short UI-ready Chinese corrections, with uncertainty stated explicitly."""
    lines = list(dict.fromkeys(error["message"] for error in analysis.get("errors", []) if error.get("message")))
    if analysis.get("ambiguous"):
        lines.append("有多种可能的对应位置；这里只对能确定的假名调整记忆记录。")
    if analysis.get("assisted"):
        lines.append("已使用提示，本次不计入单个假名的掌握证据。")
    return lines
