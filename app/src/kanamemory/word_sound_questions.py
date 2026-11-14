"""Choice tasks about how a word sounds, derived from its lesson fields.

Pure rules: no Toga, database, platform or network. Both tasks are built from
the title, reading and meaning the lesson already owns, so they need no authored
sentence. Options are chosen from public sound similarity; a distractor can never
be another attested reading of the target or a homophone of it, because hearing
the word could not tell those apart.

Known-word variants (design 11 section 5, WP-K2): given the learner's known
ids, the same ranked candidates are filtered to words already known (invented
kana near-misses always qualify) and sliced/rotated exactly as the bank
variants are. Such a variant is identified by an encoded variant number that
names the candidate positions, so (lesson, kind, variant) still rebuilds the
same question without the known set, and a stored snapshot never changes.

Stable bank pool (design 11 section 9, R1): a vocabulary expansion must not
change the bank variants of words that existed before it, because stored
snapshots, practice-history fingerprints and the evidence trust check rebuild
(lesson, kind, variant). Each word belongs to a cohort read from its own
source_ids: the date of its earliest "kanamemory-course-expansion-<YYYYMMDD>"
source, or "" for the original course. Bank variants draw only on words of
the target's cohort or an earlier one, so original words keep their pre-expansion
options and expansion words may use everything before and beside them. The
rule reads lesson fields only, never learner state. Known-word variants still
rank the whole pool; their encoded numbers are unchanged, and one keeps a bank
number only when its template equals that bank template.

Kana-spelling variants (withdrawn 2026-11-12: never selected again, because a
task that plays a word and offers kana spellings tests no mapping between the
written word and its sound; everything below is kept so that stored snapshots,
history fingerprints and evidence checks still rebuild them): the heard-word
task had generated variants that
ask for the kana spelling instead of the written word. Their options are the
answer plus three invented near-miss spellings (the reading-choice filter and
order; two when only two are usable), built from the lesson alone: no pool
and no other word, so the task needs nothing but its target. They are numbered from KANA_VARIANT_BASE, above
every encoded known-word number, and are never part of the bank list (bank
variants are indexed modulo its length, so it must not grow or reorder).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import lru_cache
from hashlib import sha256
import re
import unicodedata

from .kana_orthography import allowed_word_spellings, kana_sound_key, same_kana_sound

AUDIO_WORD = "vocab_audio_word"
READING_CHOICE = "vocab_reading_choice"
AUDIO_PROMPT = "音声を聞いて、単語を選んでください。"
AUDIO_TASK = "聞いて、単語を選ぶ"
AUDIO_KANA_PROMPT = "音声を聞いて、かなを選んでください。"
AUDIO_KANA_TASK = "聞いて、かなを選ぶ"
READING_TASK = "読み方"
OPTION_COUNT = 4
AUDIO_VARIANTS = 2
READING_VARIANTS = 4
KANA_VARIANTS = 2
# Known-word variants are numbered from this base; three 16-bit candidate
# positions follow (below 2**53, so the number survives any JSON reader).
KNOWN_VARIANT_BASE = 1 << 48
_POSITION_RADIX = 1 << 16
# Kana-spelling variants: KANA_VARIANT_BASE + index. Encoded known-word numbers
# end below 2**49 and bank numbers are small, so the ranges never meet.
KANA_VARIANT_BASE = 1 << 52
# Source id of the lessons a vocabulary expansion added; its date names the cohort.
EXPANSION_SOURCE_PREFIX = "kanamemory-course-expansion-"

_VOICED = dict(zip("かきくけこさしすせそたちつてとはひふへほ", "がぎぐげござじずぜぞだぢづでどばびぶべぼ"))
_VOICED.update({value: key for key, value in _VOICED.items()})
_SEMI = dict(zip("はひふへほ", "ぱぴぷぺぽ"))
_SEMI.update({value: key for key, value in _SEMI.items()})
_VOWEL_OF = {}
for vowel, row in zip("あいうえお", ("あかさたなはまやらわがざだばぱゃ", "いきしちにひみりぎじぢびぴ",
                                    "うくすつぬふむゆるぐずづぶぷゅ", "えけせてねへめれげぜでべぺ",
                                    "おこそとのほもよろごぞどぼぽょ")):
    _VOWEL_OF.update(dict.fromkeys(row, vowel))
_LONG_VOWEL = {"あ": "あ", "い": "い", "う": "う", "え": "い", "お": "う"}
_GEMINATE_NEXT = set("かきくけこさしすせそたちつてとぱぴぷぺぽ")
_SMALL_YOON = {"ゃ": "や", "ゅ": "ゆ", "ょ": "よ"}
_NOT_AFTER_LONG = frozenset("ゃゅょっ")
_VOWEL_BEFORE_LONG_MARK = re.compile("[あいうえお]ー")


def normal_reading(text: str) -> str:
    """NFKC hiragana with spacing removed; long-mark katakana keeps its mark."""
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text)))
    return "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in text)


def word_readings(item: Mapping) -> frozenset[str]:
    """Every reading the lesson attests, including those for other senses."""
    values = [item.get("reading"), *(item.get("alternative_readings") or ()),
              *(item.get("accepted_readings") or ())]
    return frozenset(normal_reading(value) for value in values if isinstance(value, str) and value)


def word_cohort(item: Mapping) -> str:
    """The expansion that first added this lesson (its YYYYMMDD), "" for the original course."""
    sources = item.get("source_ids")
    dates = sorted(source[len(EXPANSION_SOURCE_PREFIX):] for source in
                   (sources if isinstance(sources, (list, tuple)) else ())
                   if isinstance(source, str) and source.startswith(EXPANSION_SOURCE_PREFIX))
    return dates[0] if dates else ""


def word_pool(items: Iterable[Mapping]) -> tuple[tuple[str, str, str, frozenset[str], str], ...]:
    """A hashable projection of (id, title, reading, attested readings, cohort)."""
    return tuple((item["id"], item["title"], normal_reading(item["reading"]), word_readings(item),
                  word_cohort(item))
                 for item in items if item.get("title") and item.get("reading"))


def bank_pool(item: Mapping, pool) -> tuple:
    """Pool rows a bank variant of item may use: its cohort and earlier ones.

    Rows without a cohort field (hand-built four-field pools) always qualify.
    """
    pool = pool if isinstance(pool, tuple) else tuple(pool)
    return _cohort_rows(pool, word_cohort(item))


@lru_cache(maxsize=8)
def _cohort_rows(pool, cohort):
    if all(len(row) < 5 or row[4] <= cohort for row in pool):
        return pool
    return tuple(row for row in pool if len(row) < 5 or row[4] <= cohort)


def _rank(identity: str, value: str) -> str:
    return sha256(f"{identity}|{value}".encode()).hexdigest()


def _distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for row, a in enumerate(left, 1):
        current = [row]
        for column, b in enumerate(right, 1):
            current.append(min(previous[column] + 1, current[-1] + 1, previous[column - 1] + (a != b)))
        previous = current
    return previous[-1]


@lru_cache(maxsize=8192)
def _has_han(text: str) -> bool:
    return any("㐀" <= c <= "鿿" for c in text)


def _same_script(left: str, right: str) -> bool:
    return _has_han(left) == _has_han(right)


@lru_cache(maxsize=8192)
def _sound_key(reading: str):
    """kana_sound_key per distinct reading: a ranking compares one reading with the whole pool."""
    return kana_sound_key(reading)


def _sound_rows(item: Mapping, pool) -> tuple[tuple[str, str, str], ...]:
    """Other words by sound similarity, as (title, reading, id); never a homophone."""
    return _ranked_rows(item["id"], item["title"], normal_reading(item["reading"]), word_readings(item),
                        pool if isinstance(pool, tuple) else tuple(pool))


@lru_cache(maxsize=64)
def _ranked_rows(target_id, target_title, target, attested, pool):
    # Bank and known-word variants of both kinds rank the same pool for the
    # same lesson; the bounded cache keeps that one scan per lesson and pool.
    rows = []
    sound = _sound_key(target)
    for identity, title, reading, others, *_ in pool:
        if identity == target_id or title == target_title or others & attested:
            continue
        if sound is not None and sound == _sound_key(reading):  # same_kana_sound(target, reading, fold_script=True)
            continue
        rows.append((_distance(target, reading) + (0 if _same_script(target_title, title) else 1),
                     _rank(target_id, identity), title, reading, identity))
    rows.sort()
    return tuple((title, reading, identity) for _, _, title, reading, identity in rows)


def _sound_near(item: Mapping, pool) -> list[tuple[str, str]]:
    """Other words by sound similarity, as (title, reading); never a homophone."""
    return [(title, reading) for title, reading, _ in _sound_rows(item, pool)]


def _audio_candidates(item: Mapping, pool) -> list[tuple[str, frozenset[str]]]:
    """Distinct distractor titles in rank order, each with the word ids written so."""
    owners: dict[str, set[str]] = {}
    for title, _, identity in _sound_rows(item, pool):
        owners.setdefault(title, set()).add(identity)
    return [(title, frozenset(ids)) for title, ids in owners.items()]


def _audio_template(item: Mapping, distractors) -> dict:
    return {"prompt": AUDIO_PROMPT, "task": AUDIO_TASK, "answer": item["title"],
            "choices": [item["title"], *distractors], "audio_text": item["reading"],
            "feedback": _feedback(item), "safe": False, "strict": True}


def _audio_groups(count: int) -> list[list[int]]:
    """Bank variants as candidate positions: consecutive groups of three."""
    size = OPTION_COUNT - 1
    result = []
    for variant in range(AUDIO_VARIANTS):
        group = list(range(variant * size, min((variant + 1) * size, count)))
        if not group or result and len(group) < size:
            break
        result.append(group)
    return result


def audio_word_templates(item: Mapping, pool) -> list[dict]:
    """Hear the lesson reading, then pick the written word (bank variants, bank_pool)."""
    near = [title for title, _ in _audio_candidates(item, bank_pool(item, pool))]
    if not near or not item.get("meaning"):
        return []
    return [_audio_template(item, [near[index] for index in group]) for group in _audio_groups(len(near))]


def _feedback(item: Mapping) -> str:
    return f"{item['title']}（{item['reading']}）：{item['meaning']}"


def near_miss_readings(reading: str) -> list[str]:
    """Kana-level confusions: voicing, geminate, long vowel and yoon size."""
    text = normal_reading(reading)
    result = []
    for index, char in enumerate(text):
        for table in (_VOICED, _SEMI):
            if char in table:
                result.append(text[:index] + table[char] + text[index + 1:])
        if char == "っ":
            result.append(text[:index] + text[index + 1:])
        vowel = _VOWEL_OF.get(char)
        following = text[index + 1] if index + 1 < len(text) else ""
        if vowel and following in _GEMINATE_NEXT:
            result.append(text[:index + 1] + "っ" + following + text[index + 2:])
        if char in _SMALL_YOON:
            result.append(text[:index] + _SMALL_YOON[char] + text[index + 1:])
        if vowel and following == _LONG_VOWEL[vowel]:
            result.append(text[:index + 1] + text[index + 2:])
        elif vowel and char not in "あいうえお" and following not in _NOT_AFTER_LONG:
            result.append(text[:index + 1] + _LONG_VOWEL[vowel] + text[index + 1:])
    return list(dict.fromkeys(result))


def _usable_reading(item: Mapping):
    """The reading-option filter of one lesson: never one of its readings or the same sound."""
    target = normal_reading(item["reading"])
    attested = word_readings(item)
    forbidden = {*attested, *(normal_reading(s) for s in allowed_word_spellings(item["reading"]))}

    sound = _sound_key(target)

    def usable(value):
        # The last test is ``not same_kana_sound(target, value, fold_script=True)`` (kana_sound_key).
        return (value and value not in forbidden and value[0] not in "っゃゅょゎん"
                and not (sound is not None and sound == _sound_key(value)))
    return usable


def _invented_readings(item: Mapping, usable=None) -> list[str]:
    """Usable near-miss spellings of the lesson reading, in the lesson's own stable order."""
    usable = _usable_reading(item) if usable is None else usable
    return sorted((value for value in near_miss_readings(item["reading"]) if usable(value)),
                  key=lambda value: _rank(item["id"], value))


def _reading_options(item: Mapping, pool) -> tuple[list[str], dict[str, frozenset[str]]]:
    """Ranked reading options: invented near-misses first, then other words' readings.

    The mapping names the word ids behind each attested option; invented
    near-misses are not words and have no entry.
    """
    options, owners = _reading_options_of(item["id"], item["title"], item["reading"], word_readings(item),
                                          pool if isinstance(pool, tuple) else tuple(pool))
    return list(options), dict(owners)


@lru_cache(maxsize=64)
def _reading_options_of(identity, title, reading, attested, pool):
    # One scan of the ranked pool per lesson and pool: the bank variants, the known-word variants and the
    # bank inside the known-word numbering all ask for the same options (they used to scan three times).
    item = {"id": identity, "title": title, "reading": reading, "accepted_readings": sorted(attested)}
    usable = _usable_reading(item)
    invented = _invented_readings(item, usable)
    listed = set(invented)
    owners: dict[str, set[str]] = {}
    for _, other, owner in _sound_rows(item, pool):
        if usable(other) and other not in listed:
            owners.setdefault(other, set()).add(owner)
    options = tuple(dict.fromkeys([*invented, *owners]))
    return options, tuple((other, frozenset(ids)) for other, ids in owners.items())


def _reading_template(item: Mapping, picked) -> dict:
    return {"prompt": item["title"], "task": READING_TASK, "answer": item["reading"],
            "choices": [item["reading"], *picked], "feedback": _feedback(item),
            "safe": False, "strict": True}


def _reading_groups(positions: list[int]) -> list[list[int]]:
    """Bank rotation over the given option positions, stopping at a repeated set."""
    size = OPTION_COUNT - 1
    result, seen = [], set()
    for variant in range(READING_VARIANTS):
        group = [positions[(variant * size + step) % len(positions)] for step in range(min(size, len(positions)))]
        key = frozenset(group)
        if key in seen:
            break
        seen.add(key)
        result.append(group)
    return result


def reading_choice_templates(item: Mapping, pool) -> list[dict]:
    """See the word, then pick its reading from sound-alike kana (bank variants, bank_pool)."""
    options, _ = _reading_options(item, bank_pool(item, pool))
    if not options or not item.get("meaning"):
        return []
    return [_reading_template(item, [options[index] for index in group])
            for group in _reading_groups(list(range(len(options))))]


# ---- Kana-spelling variants of the heard-word task

def _kana_script(text) -> str | None:
    """"hiragana" or "katakana" for kana of one script (long mark allowed), else None."""
    if not isinstance(text, str) or not text.strip("ー"):
        return None
    for script, first, last in (("hiragana", "ぁ", "ゖ"), ("katakana", "ァ", "ヶ")):
        if all(first <= char <= last or char == "ー" for char in text):
            return script
    return None


def _kana_template(item: Mapping, answer: str, distractors) -> dict:
    return {"prompt": AUDIO_KANA_PROMPT, "task": AUDIO_KANA_TASK, "answer": answer,
            "choices": [answer, *distractors], "audio_text": item["reading"],
            "feedback": _feedback(item), "safe": False, "strict": True}


def _vowels_before_long_mark(reading: str) -> int:
    return len(_VOWEL_BEFORE_LONG_MARK.findall(reading))


def kana_word_templates(item: Mapping) -> list[dict]:
    """Hear the lesson reading, then pick its kana spelling among invented near-misses.

    Built from the lesson alone. The answer is the kana spelling as taught: a
    kana title itself, else the reading. The distractors are the reading-choice
    near-misses (same filter and order) written in the answer's script, so no
    other word and no written form appears. A near-miss with more vowel kana
    directly before a long mark than the taught spelling has (たくしいー) is
    skipped here only; the reading-choice options keep it. Variant 0 takes
    the first three; variant 1 the next three (six or more usable) or the last
    three (four or five). With exactly two usable there is one variant with
    three options. [] with fewer than two, without a meaning, or when the
    spelling is not kana of a single script.
    """
    if not item.get("meaning") or not item.get("title") or not item.get("reading"):
        return []
    title, reading = item["title"], item["reading"]
    answer = title if _kana_script(title) and normal_reading(title) == normal_reading(reading) else reading
    script = _kana_script(answer)
    taught = _vowels_before_long_mark(normal_reading(reading))
    usable = [value for value in (_invented_readings(item) if script else [])
              if _vowels_before_long_mark(value) <= taught]
    size = OPTION_COUNT - 1
    if len(usable) < size - 1:
        return []
    groups = [usable[:size]]
    if KANA_VARIANTS > 1 and len(usable) > size:
        groups.append(usable[size:2 * size] if len(usable) >= 2 * size else usable[-size:])
    if script == "katakana":
        # normal_reading folded the near-misses to hiragana; the script must not mark the answer.
        groups = [["".join(chr(ord(char) + 0x60) if "ぁ" <= char <= "ゖ" else char for char in value)
                   for value in group] for group in groups]
    return [_kana_template(item, answer, group) for group in groups]


def is_kana_variant(variant) -> bool:
    return type(variant) is int and variant >= KANA_VARIANT_BASE


def kana_audio_word_variants(item: Mapping) -> list[tuple[int, dict]]:
    """(variant, template) pairs of the kana-spelling task, numbered from KANA_VARIANT_BASE."""
    return [(KANA_VARIANT_BASE + index, template) for index, template in enumerate(kana_word_templates(item))]


# ---- Known-word variants (design 11 section 5, WP-K2)

def is_known_variant(variant) -> bool:
    """An encoded variant, rebuilt from the lesson rather than indexed in the bank.

    True for known-word numbers and for kana-spelling numbers (is_kana_variant):
    both go through known_variant_template.
    """
    return type(variant) is int and variant >= KNOWN_VARIANT_BASE


def _encode(group: list[int]) -> int:
    value = 0
    for position in group:
        value = value * _POSITION_RADIX + position
    return KNOWN_VARIANT_BASE + value


def _decode(variant: int) -> list[int]:
    value, group = variant - KNOWN_VARIANT_BASE, []
    for _ in range(OPTION_COUNT - 1):
        value, position = divmod(value, _POSITION_RADIX)
        group.append(position)
    if value:
        raise ValueError("Invalid known-word sound variant")
    return group[::-1]


def _numbered(groups, bank, build) -> list[tuple[int, dict]]:
    """(variant, template) pairs; a template equal to a bank variant keeps its number.

    bank holds the bank templates (drawn from bank_pool); the groups index the
    whole pool, so equal positions alone do not make equal options.
    """
    result = []
    for group in groups:
        template = build(group)
        number = bank.index(template) if template in bank else _encode(group)
        if all(number != existing for existing, _ in result):
            result.append((number, template))
    return result


def known_audio_word_variants(item: Mapping, pool, known_ids) -> list[tuple[int, dict]] | None:
    """Heard-word variants whose distractors are all known words.

    None when fewer than three known candidates exist: the caller keeps the
    bank variants, which draw on the whole pool.
    """
    candidates = _audio_candidates(item, pool)
    if not candidates or not item.get("meaning"):
        return None
    known = frozenset(known_ids)
    size = OPTION_COUNT - 1
    usable = [index for index, (_, ids) in enumerate(candidates) if ids & known and index < _POSITION_RADIX]
    if len(usable) < size:
        return None
    groups = [usable[:size]]
    second = usable[size:2 * size] if len(usable) >= 2 * size else usable[-size:]
    if AUDIO_VARIANTS > 1 and second != groups[0]:
        groups.append(second)
    titles = [title for title, _ in candidates]
    return _numbered(groups, audio_word_templates(item, pool),
                     lambda group: _audio_template(item, [titles[index] for index in group]))


def known_reading_choice_variants(item: Mapping, pool, known_ids) -> list[tuple[int, dict]] | None:
    """Reading-choice variants without readings of words the learner has not met.

    Invented near-misses always qualify; None when fewer than three options
    remain (the caller keeps the bank variants).
    """
    options, owners = _reading_options(item, pool)
    if not options or not item.get("meaning"):
        return None
    known = frozenset(known_ids)
    usable = [index for index, value in enumerate(options)
              if (value not in owners or owners[value] & known) and index < _POSITION_RADIX]
    if len(usable) < OPTION_COUNT - 1:
        return None
    return _numbered(_reading_groups(usable), reading_choice_templates(item, pool),
                     lambda group: _reading_template(item, [options[index] for index in group]))


def known_variant_template(kind: str, item: Mapping, pool, variant: int) -> dict:
    """Rebuild the template an encoded variant names (no known set needed).

    A kana-spelling number rebuilds from the lesson alone (heard-word kind only).
    """
    if is_kana_variant(variant):
        templates = kana_word_templates(item) if kind == AUDIO_WORD else []
        if variant - KANA_VARIANT_BASE >= len(templates):
            raise ValueError("No kana spelling task is available for this word")
        return templates[variant - KANA_VARIANT_BASE]
    group = _decode(variant)
    if len(set(group)) != len(group):
        raise ValueError("Invalid known-word sound variant")
    if kind == AUDIO_WORD:
        values = [title for title, _ in _audio_candidates(item, pool)]
        build = _audio_template
    elif kind == READING_CHOICE:
        values = _reading_options(item, pool)[0]
        build = _reading_template
    else:
        raise ValueError("Not a sound kind")
    if not item.get("meaning") or any(position >= len(values) for position in group):
        raise ValueError("No sound task is available for this word")
    return build(item, [values[position] for position in group])
