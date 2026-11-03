"""Offline, spelling-based kana romanization without lexical pronunciation guesses.

The basic syllables use shi/chi/tsu/fu/ji; the 2025-style doubled consonant
before ch is cc (まっちゃ -> maccha). Loanword combinations have explicit
extended mappings. A written ー adds a vowel length, rendered with a macron;
written おう / おお / えい remain ou / oo / ei. This is not a pitch-accent,
historical-reconstruction or context-sensitive particle pronunciation engine.

NFKC applies only to kana runs, including halfwidth kana and combining dakuten.
Unrecognized text outside those runs is preserved, including fullwidth Latin
letters and punctuation. Context-dependent standalone っ, ー, ヶ and iteration
marks are retained when their sound cannot be determined from the mapping.
Historical ゐ/ゑ use their modern standard reading labels i/e, not a claim about
historical wi/we. は/へ remain ha/he because grammar is not inferred.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
import unicodedata

ROMANIZATION_SCHEME = "kana-spelling-hepburn-extended-v1"
ROMANIZATION_NOTE_ZH = (
    "按假名书写机械生成：shi/chi/tsu/fu/ji；仅长音符ー用ā/ī/ū/ē/ō，"
    "ou/ei/oo按原拼写保留；促音在ch前写cc；拨音在元音或y前写n'。"
    "不推断助词读音、重音或古音。"
)

_ROWS = (
    ("あいうえお", "a i u e o"),
    ("かきくけこ", "ka ki ku ke ko"),
    ("さしすせそ", "sa shi su se so"),
    ("たちつてと", "ta chi tsu te to"),
    ("なにぬねの", "na ni nu ne no"),
    ("はひふへほ", "ha hi fu he ho"),
    ("まみむめも", "ma mi mu me mo"),
    ("やゆよ", "ya yu yo"),
    ("らりるれろ", "ra ri ru re ro"),
    ("わを", "wa o"),
    ("がぎぐげご", "ga gi gu ge go"),
    ("ざじずぜぞ", "za ji zu ze zo"),
    ("だぢづでど", "da ji zu de do"),
    ("ばびぶべぼ", "ba bi bu be bo"),
    ("ぱぴぷぺぽ", "pa pi pu pe po"),
    ("ぁぃぅぇぉゃゅょゎ", "a i u e o ya yu yo wa"),
    ("ゐゑゔ", "i e vu"),
    ("ヷヸヹヺ", "va vi ve vo"),
)
_MAPPING = {kana:roman for kana_row,roman_row in _ROWS for kana,roman in zip(kana_row,roman_row.split())}

for _kana,_stem in (("き","ky"),("し","sh"),("ち","ch"),("に","ny"),
                    ("ひ","hy"),("み","my"),("り","ry"),("ぎ","gy"),
                    ("じ","j"),("ぢ","j"),("び","by"),("ぴ","py")):
    for _small,_vowel in zip("ゃゅょ","auo"):
        _MAPPING[_kana+_small]=_stem+_vowel

# Explicit loanword spellings; not an assertion that every speaker realizes
# foreign v/ti/etc identically. Unsupported combinations fall back to letters.
_MAPPING.update({
    "いぇ":"ye", "うぃ":"wi", "うぇ":"we", "うぉ":"wo",
    "くぁ":"kwa", "くぃ":"kwi", "くぇ":"kwe", "くぉ":"kwo", "くゎ":"kwa",
    "ぐぁ":"gwa", "ぐぃ":"gwi", "ぐぇ":"gwe", "ぐぉ":"gwo", "ぐゎ":"gwa",
    "しぇ":"she", "じぇ":"je", "ちぇ":"che",
    "すぃ":"si", "ずぃ":"zi",
    "つぁ":"tsa", "つぃ":"tsi", "つぇ":"tse", "つぉ":"tso",
    "てぃ":"ti", "でぃ":"di", "とぅ":"tu", "どぅ":"du",
    "てゅ":"tyu", "でゅ":"dyu",
    "ふぁ":"fa", "ふぃ":"fi", "ふぇ":"fe", "ふぉ":"fo",
    "ふゃ":"fya", "ふゅ":"fyu", "ふょ":"fyo",
    "ゔぁ":"va", "ゔぃ":"vi", "ゔぇ":"ve", "ゔぉ":"vo",
    "ゔゃ":"vya", "ゔゅ":"vyu", "ゔょ":"vyo",
    "きぇ":"kye", "ぎぇ":"gye", "にぇ":"nye", "ひぇ":"hye",
    "びぇ":"bye", "ぴぇ":"pye", "みぇ":"mye", "りぇ":"rye",
    "てぃゃ":"tya", "てぃゅ":"tyu", "てぃょ":"tyo",
    "でぃゃ":"dya", "でぃゅ":"dyu", "でぃょ":"dyo",
})
_MATCH_LENGTHS=sorted({len(k) for k in _MAPPING},reverse=True)
_MACRON={"a":"ā","i":"ī","u":"ū","e":"ē","o":"ō"}
_BASE_VOWEL={long:short for short,long in _MACRON.items()}
# Include y for expressive dictionary spellings such as はっやい. Doubling
# is a spelling convention here, not a claim about their phonetic realization.
_GEMINATABLE=frozenset("bcdfghjkpqrstvxzy")
_KANA_RUN=re.compile("[\u3041-\u3096\u3099\u309a\u309d\u309e\u30a1-\u30fa\u30fc-\u30fe\uff66-\uff9f]+")


def kana_learning_romaji_values(row: Mapping) -> tuple[str, ...]:
    """Return spelling labels/aliases for learning an individual kana record.

    を/ヲ use ``wo`` to distinguish the letter from お/オ (``o``). This
    distinction does not change their modern pronunciation, word readings,
    audio assets, or listening-answer fairness. Other kana keep their catalog
    romanization and accepted aliases.
    """
    symbols = {row.get("hiragana"), row.get("katakana")}
    if symbols.intersection({"を", "ヲ"}):
        return ("wo",)
    if symbols.intersection({"お", "オ"}):
        return ("o",)
    romanization = row.get("romanization") or {}
    primary = row.get("romaji") or romanization.get("display", "")
    aliases = romanization.get("aliases", [])
    values = [primary, *(alias.get("text") if isinstance(alias, Mapping) else alias for alias in aliases)]
    return tuple(dict.fromkeys(value.strip() for value in values if isinstance(value, str) and value.strip()))


def kana_learning_romaji(row: Mapping) -> str:
    """Primary label for kana tables, details and spelling questions."""
    if row.get("hiragana") in {"を", "お"} or row.get("katakana") in {"ヲ", "オ"}:
        return kana_learning_romaji_values(row)[0]
    # A keyboard alias such as xtsu is not a standalone pronunciation for っ.
    primary = row.get("romaji") or (row.get("romanization") or {}).get("display", "")
    return primary.strip() if isinstance(primary, str) else ""


def _tokens(text: str) -> list[tuple[str,str]]:
    normalized=_KANA_RUN.sub(lambda m:unicodedata.normalize("NFKC",m.group()),text)
    folded="".join(chr(ord(c)-0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in normalized)
    result=[]
    i=0
    while i<len(folded):
        char=folded[i]
        if char in {"っ","ん","ー"}:
            result.append(({"っ":"sokuon","ん":"nasal","ー":"long"}[char],normalized[i]))
            i+=1
            continue
        for length in _MATCH_LENGTHS:
            roman=_MAPPING.get(folded[i:i+length])
            if roman is not None:
                result.append(("kana",roman))
                i+=length
                break
        else:
            result.append(("literal",normalized[i]))
            i+=1
    return result


def romanize_kana(text: str) -> str:
    """Return a consistent mechanical romanization, preserving unknown text.

    Examples: こうこう -> koukou; モーター -> mōtā; ロマンチック ->
    romanchikku; しんよう -> shin'you. Does not need a repository or network.
    """
    if not isinstance(text,str):
        raise TypeError("romanize_kana expects a string")
    tokens=_tokens(text)
    output=[]
    for i,(kind,value) in enumerate(tokens):
        following=tokens[i+1] if i+1<len(tokens) else None
        if kind=="nasal":
            apostrophe=following is not None and following[0]=="kana" and following[1][0] in "aeiouy"
            output.append("n'" if apostrophe else "n")
        elif kind=="sokuon":
            if following and following[0]=="kana" and following[1][0] in _GEMINATABLE:
                output.append(following[1][0])
            else:
                # A final glottal stop or atypical expressive spelling cannot be
                # assigned a following consonant from spelling alone.
                output.append(value)
        elif kind=="long":
            previous_kind=tokens[i-1][0] if i else None
            last=output[-1][-1] if output and output[-1] else ""
            if previous_kind in {"kana","long"} and last in _MACRON:
                output[-1]=output[-1][:-1]+_MACRON[last]
            elif previous_kind in {"kana","long"} and last in _BASE_VOWEL:
                # Do not silently discard extra length in expressive コーー.
                output[-1]+=_BASE_VOWEL[last]
            else:
                output.append(value)
        else:
            output.append(value)
    return "".join(output)


__all__=["romanize_kana", "kana_learning_romaji", "kana_learning_romaji_values",
         "ROMANIZATION_SCHEME", "ROMANIZATION_NOTE_ZH"]
