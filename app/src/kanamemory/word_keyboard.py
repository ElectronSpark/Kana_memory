"""Single-letter word keyboards, including compatibility for saved questions.

Keyboard letters are deliberately independent from spelling-analysis units:
the learner presses き then ゃ, while a spelling error can still concern きゃ.
No learner state or stored question snapshot is changed by these helpers.
"""
from __future__ import annotations

import unicodedata


WORD_QUESTION_KINDS = frozenset({"word_kana", "word_meaning_kana"})
_MODERN_HIRAGANA = (
    "あいうえおかきくけこさしすせそたちつてとなにぬねの"
    "はひふへほまみむめもやゆよらりるれろわをん"
    "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽゔ"
)
_SMALL_HIRAGANA = "ぁぃぅぇぉゃゅょっゎゕゖ"


def word_answer_letters(answer: str) -> list[str]:
    """Return the actual taps needed, retaining repetitions and small kana."""
    if not isinstance(answer, str):
        raise TypeError("answer must be a string")
    return list(unicodedata.normalize("NFC", answer).strip())


def _in_script(text: str, script: str) -> str:
    if script == "hiragana":
        return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in text)


def _ordinary_candidate(letter: str, script: str) -> bool:
    if letter == "ー":
        return True
    if script == "hiragana":
        return "ぁ" <= letter <= "ゖ"
    return "ァ" <= letter <= "ヺ" or "ㇰ" <= letter <= "ㇿ"


def keyboard_grid(options):
    """Project the same keys onto five vowel columns without inventing options.

    Voiced rows stay next to their base rows. Missing candidates are blank cells,
    so a partial keyboard never shifts u/o into a/i positions. Unpositioned
    marks remain separate; ん and ー do not acquire a fictional vowel.
    """
    order = (("∅", "あいうえお"), ("k", "かきくけこ"), ("g", "がぎぐげご"),
             ("s", "さしすせそ"), ("z", "ざじずぜぞ"), ("t", "たちつてと"),
             ("d", "だぢづでど"), ("n", "なにぬねの"), ("h", "はひふへほ"),
             ("b", "ばびぶべぼ"), ("p", "ぱぴぷぺぽ"), ("m", "まみむめも"),
             ("y", "や ゆ よ"), ("r", "らりるれろ"), ("w", "わ   を"),
             ("小", "ぁぃぅぇぉ"), ("y 小", "ゃ ゅ ょ"), ("w 小", "ゎ    "),
             ("k 小", "ゕ  ゖ "))
    keys = list(dict.fromkeys(options))
    by_form = {}
    for key in keys:
        by_form.setdefault(_in_script(key, "hiragana"), []).append(key)
    rows, placed = [], set()
    for label, letters in order:
        cells = tuple(tuple(by_form.get(c, ())) if c != " " else () for c in letters)
        if any(cells):
            rows.append((label, cells))
            placed.update(key for cell in cells for key in cell)
    return rows, [key for key in keys if key not in placed]


def normalize_word_keyboard_options(answer: str, options, script: str, *, word_table: str = "partial") -> list[str]:
    """Split old/new candidates into NFC single letters, preserving their order.

    Existing distractors are split rather than resampled. Only a full keyboard
    receives the complete standard letter set. Required punctuation or rare
    spelling marks survive when present in the answer; they are not invented
    as distractors. The saved answer must already use the question's script.
    """
    if script not in {"hiragana", "katakana"}:
        raise ValueError("script must be hiragana or katakana")
    if word_table not in {"partial", "full"}:
        raise ValueError("word_table must be partial or full")
    if not isinstance(options, (list, tuple)) or any(not isinstance(value, str) for value in options):
        raise TypeError("options must be a list or tuple of strings")
    required = word_answer_letters(answer)
    if "".join(required) != _in_script("".join(required), script):
        raise ValueError("answer must already use the question's script")
    required_set = set(required)
    result = []
    seen = set()

    def append(letter):
        if letter not in seen:
            seen.add(letter)
            result.append(letter)

    for option in options:
        for letter in _in_script(unicodedata.normalize("NFC", option).strip(), script):
            if letter in required_set or _ordinary_candidate(letter, script):
                append(letter)
    if word_table == "full":
        for letter in _in_script(_MODERN_HIRAGANA + _SMALL_HIRAGANA, script) + "ー":
            append(letter)
    for letter in required:
        append(letter)
    return result


def normalize_word_question_snapshot(item: dict) -> dict:
    """Project a word snapshot as single-letter controls without rewriting it.

    Independent kana exercises, including romaji_kana_full, pass through
    unchanged. Nested scheduling metadata and the authoritative answer retain
    their original values; only the options and answer_tokens view is updated.
    """
    if not isinstance(item, dict):
        raise TypeError("question snapshot must be a dictionary")
    if item.get("kind") not in WORD_QUESTION_KINDS:
        return item
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        raise TypeError("question metadata must be a dictionary")
    config = metadata.get("practice_config", {})
    config = config if isinstance(config, dict) else {}
    mode = metadata.get("word_table", config.get("word_table", "partial"))
    mode = mode if mode in {"partial", "full"} else "partial"
    answer = item["answer"]
    return {**item,
            "options": normalize_word_keyboard_options(answer, item.get("options", ()), item["script"], word_table=mode),
            "metadata": {**metadata, "answer_tokens": word_answer_letters(answer)}}
