"""Separate modern kana sound equivalence from word-specific spelling.

The permitted word pairs below are the explicitly listed examples in section
2.5(2) of the Agency for Cultural Affairs' Modern Kana Usage. They are whole
spellings, never substitutions applied to arbitrary substrings or compounds.
https://www.bunka.go.jp/kokugo_nihongo/sisaku/joho/joho/kijun/naikaku/gendaikana/honbun_dai2.html
"""
from __future__ import annotations

import unicodedata


ORTHOGRAPHY_POLICY = "modern-kana-word-v1"
ORTHOGRAPHY_SOURCE = "https://www.bunka.go.jp/kokugo_nihongo/sisaku/joho/joho/kijun/naikaku/gendaikana/honbun_dai2.html"

# First spelling is the modern principal spelling. Only the displayed
# ji/zu location varies; long vowels, suffixes and other letters are retained.
_PERMITTED_PAIRS = (
    ("せかいじゅう", "せかいぢゅう"),
    ("いなずま", "いなづま"), ("かたず", "かたづ"), ("きずな", "きづな"),
    ("さかずき", "さかづき"), ("ときわず", "ときわづ"),
    ("ほおずき", "ほおづき"), ("みみずく", "みみづく"),
    ("うなずく", "うなづく"), ("おとずれる", "おとづれる"),
    ("かしずく", "かしづく"), ("つまずく", "つまづく"),
    ("ぬかずく", "ぬかづく"), ("ひざまずく", "ひざまづく"),
    ("あせみずく", "あせみづく"), ("くんずほぐれつ", "くんづほぐれつ"),
    ("さしずめ", "さしづめ"), ("でずっぱり", "でづっぱり"),
    ("なかんずく", "なかんづく"), ("うでずく", "うでづく"),
    ("くろずくめ", "くろづくめ"), ("ひとりずつ", "ひとりづつ"),
    ("ゆうずう", "ゆうづう"),
)
_PAIR_BY_FORM = {form: pair for pair in _PERMITTED_PAIRS for form in pair}
_SOUND_MAP = str.maketrans({"ぢ": "じ", "づ": "ず", "を": "お", "ゐ": "い", "ゑ": "え",
                            "ヂ": "ジ", "ヅ": "ズ", "ヲ": "オ", "ヰ": "イ", "ヱ": "エ"})


def _normal(text):
    return unicodedata.normalize("NFC", text).strip() if isinstance(text, str) else ""


def _hira(text):
    return "".join(chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char for char in text)


def _kata(text):
    return "".join(chr(ord(char) + 0x60) if "ぁ" <= char <= "ゖ" else char for char in text)


def _kana(text, script=None):
    first, last = (("ぁ", "ゖ") if script == "hiragana" else
                   ("ァ", "ヺ") if script == "katakana" else ("ぁ", "ヺ"))
    return bool(text) and all(first <= c <= last and c not in "゗゘゙゚゛゜ゝゞゟ゠・" or c == "ー" for c in text)


def same_kana_sound(expected, actual, script=None, *, fold_script=False):
    """Compare only the well-established modern homophone kana classes.

    This is not a word-spelling validator or a general Japanese phonetic
    transcriber. Small kana, long-vowel spelling and voicing remain distinct.
    Script folding is opt-in for diagnosis, never implicit answer acceptance.
    """
    if script not in {None, "hiragana", "katakana"}:
        raise ValueError("Unknown kana script")
    left, right = _normal(expected), _normal(actual)
    required = None if fold_script else script
    if not _kana(left, required) or not _kana(right, required):
        return False
    if fold_script:
        left, right = _hira(left), _hira(right)
    return left.translate(_SOUND_MAP) == right.translate(_SOUND_MAP)


def allowed_word_spellings(expected, *, metadata=None, accepted_answers=()):
    """Return the saved target plus explicitly permitted whole-word spellings.

    Legacy drills stored a Cartesian product of homophone letters in
    accepted_answers; that list is deliberately not a source of permission.
    Metadata is accepted for a stable caller interface, but a policy label or
    arbitrary source ID does not turn an unreviewed alternate into a norm.
    The saved target is never rewritten, preserving unfinished snapshots.
    """
    canonical = _normal(expected)
    if not canonical:
        return ()
    if not (_kana(canonical, "hiragana") or _kana(canonical, "katakana")):
        return (canonical,)
    pair = _PAIR_BY_FORM.get(_hira(canonical), ())
    alternatives = (_kata(form) if _kana(canonical, "katakana") else form for form in pair)
    return tuple(dict.fromkeys([canonical, *alternatives]))
