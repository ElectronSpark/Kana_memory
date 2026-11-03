"""Offline romaji composition; no dictionary or expected-answer lookup.

Display readings and conventional keyboard spellings have different ti/tu/etc
conventions. The caller exposes that choice instead of guessing from a word's
correct spelling. Particle spelling and homophones remain the learner's choice.
"""
import unicodedata

from .answer_input import katakana

_ROWS = (
    ("a i u e o", "あいうえお"), ("ka ki ku ke ko", "かきくけこ"),
    ("sa shi su se so", "さしすせそ"), ("ta chi tsu te to", "たちつてと"),
    ("na ni nu ne no", "なにぬねの"), ("ha hi fu he ho", "はひふへほ"),
    ("ma mi mu me mo", "まみむめも"), ("ra ri ru re ro", "らりるれろ"),
    ("ya yu yo", "やゆよ"), ("wa wo", "わを"),
    ("ga gi gu ge go", "がぎぐげご"), ("za ji zu ze zo", "ざじずぜぞ"),
    ("da dji dzu de do", "だぢづでど"), ("ba bi bu be bo", "ばびぶべぼ"),
    ("pa pi pu pe po", "ぱぴぷぺぽ"),
)
_COMMON = {key: char for keys, chars in _ROWS for key, char in zip(keys.split(), chars)}
for _stem, _head in (("ky", "き"), ("sh", "し"), ("ch", "ち"), ("ny", "に"),
                     ("hy", "ひ"), ("my", "み"), ("ry", "り"), ("gy", "ぎ"),
                     ("j", "じ"), ("dj", "ぢ"), ("by", "び"), ("py", "ぴ")):
    for _vowel, _small in zip("auo", "ゃゅょ"):
        _COMMON[_stem + _vowel] = _head + _small
for _prefix in ("x", "l"):
    _COMMON.update({_prefix + key: value for key, value in {
        "a": "ぁ", "i": "ぃ", "u": "ぅ", "e": "ぇ", "o": "ぉ",
        "ya": "ゃ", "yu": "ゅ", "yo": "ょ", "wa": "ゎ", "ka": "ゕ", "ke": "ゖ",
        "tsu": "っ", "tu": "っ"}.items()})
_COMMON.update({"vu": "ゔ", "ye": "いぇ", "wi": "うぃ", "we": "うぇ", "who": "うぉ",
    "wha": "うぁ", "whi": "うぃ", "whe": "うぇ", "whu": "う",
    "kwa": "くぁ", "kwi": "くぃ", "kwe": "くぇ", "kwo": "くぉ",
    "gwa": "ぐぁ", "gwi": "ぐぃ", "gwe": "ぐぇ", "gwo": "ぐぉ",
    "tsa": "つぁ", "tsi": "つぃ", "tse": "つぇ", "tso": "つぉ",
    "she": "しぇ", "je": "じぇ", "che": "ちぇ", "thi": "てぃ", "dhi": "でぃ",
    "twu": "とぅ", "dwu": "どぅ", "swi": "すぃ", "zwi": "ずぃ",
    "fa": "ふぁ", "fi": "ふぃ", "fe": "ふぇ", "fo": "ふぉ",
    "va": "ゔぁ", "vi": "ゔぃ", "ve": "ゔぇ", "vo": "ゔぉ"})
for _stem, _head in (("f", "ふ"), ("v", "ゔ")):
    for _vowel, _small in zip("auo", "ゃゅょ"):
        _COMMON[_stem + "y" + _vowel] = _head + _small
for _stem, _head in (("ky", "き"), ("gy", "ぎ"), ("ny", "に"), ("hy", "ひ"),
                     ("my", "み"), ("ry", "り"), ("by", "び"), ("py", "ぴ")):
    _COMMON[_stem + "e"] = _head + "ぇ"
for _stem, _head in (("sy", "し"), ("cy", "ち"), ("jy", "じ")):
    for _vowel, _small in zip("auo", "ゃゅょ"):
        _COMMON[_stem + _vowel] = _head + _small

_READING = {**_COMMON, "ti": "てぃ", "di": "でぃ", "tu": "とぅ", "du": "どぅ",
            "si": "すぃ", "zi": "ずぃ", "wo": "うぉ"}
_KEYBOARD = {**_COMMON, "ti": "ち", "di": "ぢ", "tu": "つ", "du": "づ", "si": "し", "zi": "じ", "hu": "ふ"}
for _vowel, _small in zip("auo", "ゃゅょ"):
    _READING["ty" + _vowel] = "てぃ" + _small
    _READING["dy" + _vowel] = "でぃ" + _small
    _KEYBOARD["ty" + _vowel] = "ち" + _small
    _KEYBOARD["dy" + _vowel] = "ぢ" + _small


def convert_romaji(source, *, script="hiragana", convention="keyboard", final=False):
    if not isinstance(source, str) or len(source) > 10000:
        raise ValueError("罗马音不能超过10000字。")
    if script not in {"hiragana", "katakana"} or convention not in {"keyboard", "reading"}:
        raise ValueError("Unknown romaji input convention")
    text = unicodedata.normalize("NFKC", source).lower().replace("’", "'")
    for marked, plain in zip("āīūēō", "aiueo"):
        text = text.replace(marked, plain + "-")
    table = _KEYBOARD if convention == "keyboard" else _READING
    output, index = [], 0
    while index < len(text):
        rest, char = text[index:], text[index]
        if char.isspace():
            index += 1
            continue
        if char in "-.,!?":
            output.append(dict(zip("-.,!?", "ー。、！？"))[char]); index += 1
            continue
        if "ぁ" <= char <= "ゖ" or "ァ" <= char <= "ヺ" or char in "ー、。！？":
            output.append(chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char)
            index += 1
            continue
        if char == "n":
            if rest.startswith("n'"):
                output.append("ん"); index += 2; continue
            if rest == "n":
                if not final: break
                output.append("ん"); index += 1; continue
            if rest.startswith("nn"):
                output.append("ん")
                index += 1 if len(rest) > 2 and rest[2] in "aiueoy" else 2
                continue
            if rest[1] not in "aiueoy":
                output.append("ん"); index += 1; continue
        if (len(rest) > 1 and char == rest[1] and char in "bcdfghjkpqrstvwxyz"
                or rest.startswith("tch")):
            output.append("っ"); index += 1; continue
        matched = next((rest[:size] for size in (4, 3, 2, 1) if rest[:size] in table), None)
        if matched is None:
            break
        output.append(table[matched]); index += len(matched)
    kana = "".join(output)
    return {"text": katakana(kana) if script == "katakana" else kana,
            "pending": text[index:], "complete": index == len(text)}
