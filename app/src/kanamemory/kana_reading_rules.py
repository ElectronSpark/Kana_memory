"""Shared reading-rule identities, without learner state or teaching prose.

Hiragana and katakana signs refer to the same rule. Glyph identities remain
available for reference; this mapping never transfers a score or rewrites a
saved question. Ordinary kana and complete combinations are not retired.
"""
from collections.abc import Mapping
from unicodedata import normalize

SMALL_KANA_RULE = "pronunciation:small_kana"
SOKUON_RULE = "pronunciation:sokuon_stop"
LONG_VOWEL_RULE = "pronunciation:long_duration"
NASAL_RULE = "pronunciation:nasal_mora"
READING_RULE_IDS = (SMALL_KANA_RULE, SOKUON_RULE, LONG_VOWEL_RULE, NASAL_RULE)
# The shared introductory views include the prerequisite concept of a mora.
# This set is presentation policy, not another memory/assessment grouping.
TEACHING_RULE_IDS = ("pronunciation:mora", *READING_RULE_IDS)

SMALL_COMBINING_KANA = frozenset("ぁぃぅぇぉゃゅょゎゕゖァィゥェォャュョヮヵヶ")


def rule_for_symbol(symbol):
    if not isinstance(symbol, str):
        return None
    symbol = normalize("NFC", symbol)
    if symbol in SMALL_COMBINING_KANA:
        return SMALL_KANA_RULE
    if symbol in {"っ", "ッ"}:
        return SOKUON_RULE
    if symbol == "ー":
        return LONG_VOWEL_RULE
    if symbol in {"ん", "ン"}:
        return NASAL_RULE
    return None


def rule_for_kana(row):
    """Return the single rule replacing this standalone sign's enrollment.

    Both forms must agree when present. A malformed row cannot accidentally
    retire an ordinary kana or a full combination merely by its category.
    """
    if not isinstance(row, Mapping):
        return None
    forms = [row[key] for key in ("hiragana", "katakana") if row.get(key)]
    rules = {rule_for_symbol(form) for form in forms}
    return next(iter(rules)) if forms and len(rules) == 1 and None not in rules else None


def explicit_reading_rules(reading):
    """Rules directly evidenced by written signs, in canonical stable order.

    This deliberately does not guess vowel sequences such as おう/えい: their
    long-vowel interpretation needs reviewed word-level pronunciation data.
    It neither awards prerequisite completion nor analyzes ambiguous text.
    """
    if not isinstance(reading, str):
        return ()
    found = {rule_for_symbol(char) for char in normalize("NFC", reading)}
    return tuple(rule for rule in READING_RULE_IDS if rule in found)


def reviewed_long_vowel_spans(reading):
    """Return reviewed word-specific long-vowel spans, never an ou/ei guess.

    Offsets are Unicode character positions in the exact original reading.
    ``start:end`` covers the whole long sound; ``extension_start:extension_end`` marks
    its written extra mora. Optional private content can be absent in demo
    builds. Returned records are detached from the installed content pack.
    """
    if not isinstance(reading, str):
        return ()
    from .content_pack import load_namespace
    content = load_namespace("reading_annotations", {})
    if content.get("version") != 1:
        return ()
    records = content.get("long_vowels", {}).get(reading, [])
    valid = []
    for record in records:
        if not isinstance(record, dict):
            continue
        start, end = record.get("start"), record.get("end")
        tail_start, tail_end = record.get("extension_start"), record.get("extension_end")
        if (not all(type(n) is int for n in (start, end, tail_start, tail_end))
                or not 0 <= start < tail_start < tail_end == end <= len(reading)
                or reading[start:end] != record.get("quote")
                or reading[tail_start:tail_end] != record.get("extension_quote")
                or record.get("vowel") not in {"a", "i", "u", "e", "o"}
                or not isinstance(record.get("source_ids"), list)
                or not record["source_ids"]
                or not isinstance(record.get("source_urls"), list)
                or not record["source_urls"]):
            continue
        valid.append(record)
    return tuple(valid)


def word_reading_rules(reading):
    """Explicit signs plus reviewed, exact lexical pronunciation annotations."""
    found = set(explicit_reading_rules(reading))
    if reviewed_long_vowel_spans(reading):
        found.add(LONG_VOWEL_RULE)
    return tuple(rule for rule in READING_RULE_IDS if rule in found)


def dependencies_for_kana(row):
    """Reading concepts required by a complete kana combination.

    Standalone signs map through ``rule_for_kana`` instead; a shared rule
    cannot require its own retired glyph entries. Keep character prerequisites
    (e.g. voiced ぎ before ぎゃ) in the existing script-specific graph.
    """
    if not isinstance(row, Mapping) or rule_for_kana(row):
        return ()
    found = {rule for key in ("hiragana", "katakana")
             if isinstance(row.get(key), str) and len(row[key]) > 1
             for rule in explicit_reading_rules(row[key])}
    return tuple(rule for rule in READING_RULE_IDS if rule in found)
