"""Plan an isolated kana demonstration without changing its written identity.

This is intentionally not a text normalizer: words and sentences must retain
their complete reading and context. A symbol with no independent sound gets a
labelled example or no playback, never a guessed sound or a symbol name.

Modern foreign spellings and クヮ / クァ, グヮ / グァ are described in:
https://www.bunka.go.jp/kokugo_nihongo/sisaku/joho/joho/kijun/naikaku/gairai/honbun05.html
For contracted sounds, geminates and long vowels, see:
https://www.bunka.go.jp/kokugo_nihongo/sisaku/joho/joho/kijun/naikaku/gendaikana/honbun_dai1.html
The selected spelling is an input policy, not a guarantee of a TTS engine's
acoustic accuracy. Engine / voice combinations still require listening checks.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from unicodedata import normalize


@dataclass(frozen=True)
class KanaAudioPlan:
    text: str | None
    label: str = ""
    kind: str = "syllable"

    @property
    def playable(self) -> bool:
        return bool(self.text)

    @property
    def isolated(self) -> bool:
        """An example must never become the answer sound of a kana-only test."""
        return self.playable and self.kind in {"syllable", "modern_reading"}


_ORDINARY = frozenset(
    "アイウエオカキクケコサシスセソタチツテトナニヌネノ"
    "ハヒフヘホマミムメモヤユヨラリルレロワヲン"
    "ガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポ"
)
_CONTRACTED = frozenset(first + small for first in "キシチニヒミリギジヂビピ" for small in "ャュョ")
_FOREIGN = frozenset(
    "シェ ジェ チェ ツァ ツェ ツォ ティ ディ ファ フィ フェ フォ デュ "
    "イェ ウィ ウェ ウォ クァ クィ クェ クォ グァ ツィ トゥ ドゥ "
    "ヴァ ヴィ ヴェ ヴォ テュ フュ ヴュ".split()
)
# These are contemporary standard-Japanese phonetic equivalents, not spelling
# replacements. In particular ヲ's pedagogical romanization remains "wo".
_MODERN_SOUND = {"ヲ": "オ", "ヂ": "ジ", "ヅ": "ズ", "ヂャ": "ジャ", "ヂュ": "ジュ", "ヂョ": "ジョ"}
_SMALL_EXAMPLES = {
    "ァ": ("ファ", "组合示例：ファ"),
    "ィ": ("ティ", "组合示例：ティ"),
    "ゥ": ("トゥ", "组合示例：トゥ"),
    "ェ": ("シェ", "组合示例：シェ"),
    "ォ": ("フォ", "组合示例：フォ"),
    "ャ": ("キャ", "组合示例：キャ"),
    "ュ": ("キュ", "组合示例：キュ"),
    "ョ": ("キョ", "组合示例：キョ"),
    "ッ": ("きって", "词内示例：きって"),
    "ヮ": ("クァ", "组合示例：クヮ（クァ）"),
}


def _katakana(form: str) -> str:
    return "".join(chr(ord(char) + 0x60) if "ぁ" <= char <= "ゖ" else char
                   for char in normalize("NFC", form))


def kana_audio_plan(item: dict) -> KanaAudioPlan:
    """Choose audio for one catalog entry, preserving its source and learning ID.

    Accept both repository records (flattened fields plus payload) and a payload
    wrapper. Only explicitly supported categories and complete units are read;
    unknown entries cannot accidentally turn a whole word into an isolated test.
    """
    data = item.get("payload") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            data = {}
    data = {**(data if isinstance(data, dict) else {}), **{key: value for key, value in item.items() if key != "payload"}}
    category = data.get("category")
    form = data.get("katakana") or data.get("hiragana")
    if not isinstance(form, str) or not form:
        return KanaAudioPlan(None, "暂无经核对的读音示范", "unavailable")
    form = _katakana(form)

    if category in {"basic", "dakuten", "handakuten"} and form in _ORDINARY:
        return KanaAudioPlan(_MODERN_SOUND.get(form, form))
    if category == "yoon" and form in _CONTRACTED:
        return KanaAudioPlan(_MODERN_SOUND.get(form, form))
    if category == "foreign_voiced" and form == "ヴ":
        return KanaAudioPlan(form)
    if category == "foreign_combination" and form in _FOREIGN:
        return KanaAudioPlan(form)
    if category == "foreign_variant" and form in {"クヮ", "グヮ"}:
        return KanaAudioPlan({"クヮ": "クァ", "グヮ": "グァ"}[form])
    if category == "small":
        if form in _SMALL_EXAMPLES:
            text, label = _SMALL_EXAMPLES[form]
            return KanaAudioPlan(text, label, "example")
        if form in {"ヵ", "ヶ"}:
            return KanaAudioPlan(None, "需结合具体词语确定读音", "unavailable")
    if category == "long_vowel_mark" and form == "ー":
        return KanaAudioPlan("コーヒー", "词内示例：コーヒー", "example")
    if category == "voicing_mark" and form in {"゛", "゜"}:
        return KanaAudioPlan("ガ", "组合示例：か → が", "example") if form == "゛" else KanaAudioPlan(
            "パ", "组合示例：は → ぱ", "example")
    if category == "iteration_mark" and form in {"ゝ", "ゞ", "ヽ", "ヾ"}:
        return KanaAudioPlan(None, "重复前面的假名，需结合前文朗读", "unavailable")
    if category == "historical_kana" and form in {"ヰ", "ヱ"}:
        text = {"ヰ": "イ", "ヱ": "エ"}[form]
        return KanaAudioPlan(text, "现代读法：" + text, "modern_reading")
    # A legacy glyph alone does not establish a universally applicable modern
    # pronunciation. Keep sourced recordings independent of this TTS planner.
    return KanaAudioPlan(None, "暂无经核对的读音示范", "unavailable")
