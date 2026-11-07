"""Choice tasks about how a word sounds, derived from its lesson fields.

Pure rules: no Toga, database, platform or network. Both tasks are built from
the title, reading and meaning the lesson already owns, so they need no authored
sentence. Options are chosen from public sound similarity; a distractor can never
be another attested reading of the target or a homophone of it, because hearing
the word could not tell those apart.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from hashlib import sha256
import re
import unicodedata

from .kana_orthography import allowed_word_spellings, same_kana_sound

AUDIO_WORD = "vocab_audio_word"
READING_CHOICE = "vocab_reading_choice"
AUDIO_PROMPT = "音声を聞いて、単語を選んでください。"
AUDIO_TASK = "聞いて、単語を選ぶ"
READING_TASK = "読み方"
OPTION_COUNT = 4
AUDIO_VARIANTS = 2
READING_VARIANTS = 4

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


def normal_reading(text: str) -> str:
    """NFKC hiragana with spacing removed; long-mark katakana keeps its mark."""
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text)))
    return "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in text)


def word_readings(item: Mapping) -> frozenset[str]:
    """Every reading the lesson attests, including those for other senses."""
    values = [item.get("reading"), *(item.get("alternative_readings") or ()),
              *(item.get("accepted_readings") or ())]
    return frozenset(normal_reading(value) for value in values if isinstance(value, str) and value)


def word_pool(items: Iterable[Mapping]) -> tuple[tuple[str, str, str, frozenset[str]], ...]:
    """A hashable projection of (id, title, reading, attested readings)."""
    return tuple((item["id"], item["title"], normal_reading(item["reading"]), word_readings(item))
                 for item in items if item.get("title") and item.get("reading"))


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


def _same_script(left: str, right: str) -> bool:
    def han(text):
        return any("㐀" <= c <= "鿿" for c in text)
    return han(left) == han(right)


def _sound_near(item: Mapping, pool) -> list[tuple[str, str]]:
    """Other words by sound similarity, as (title, reading); never a homophone."""
    target = normal_reading(item["reading"])
    attested = word_readings(item)
    rows = []
    for identity, title, reading, others in pool:
        if identity == item["id"] or title == item["title"] or others & attested:
            continue
        if same_kana_sound(target, reading, fold_script=True):
            continue
        rows.append((_distance(target, reading) + (0 if _same_script(item["title"], title) else 1),
                     _rank(item["id"], identity), title, reading))
    rows.sort()
    return [(title, reading) for _, _, title, reading in rows]


def audio_word_templates(item: Mapping, pool) -> list[dict]:
    """Hear the lesson reading, then pick the written word."""
    near = list(dict.fromkeys(title for title, _ in _sound_near(item, pool)))
    size = OPTION_COUNT - 1
    if not near or not item.get("meaning"):
        return []
    result = []
    for variant in range(AUDIO_VARIANTS):
        distractors = near[variant * size:(variant + 1) * size]
        if not distractors or result and len(distractors) < size:
            break
        result.append({"prompt": AUDIO_PROMPT, "task": AUDIO_TASK, "answer": item["title"],
                       "choices": [item["title"], *distractors], "audio_text": item["reading"],
                       "feedback": _feedback(item), "safe": False, "strict": True})
    return result


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


def reading_choice_templates(item: Mapping, pool) -> list[dict]:
    """See the word, then pick its reading from sound-alike kana."""
    target = normal_reading(item["reading"])
    attested = word_readings(item)
    forbidden = {*attested, *(normal_reading(s) for s in allowed_word_spellings(item["reading"]))}

    def usable(value):
        return (value and value not in forbidden and value[0] not in "っゃゅょゎん"
                and not same_kana_sound(target, value, fold_script=True))

    invented = sorted((value for value in near_miss_readings(target) if usable(value)),
                      key=lambda value: _rank(item["id"], value))
    attested_near = [reading for _, reading in _sound_near(item, pool) if usable(reading)]
    options = list(dict.fromkeys([*invented, *attested_near]))
    size = OPTION_COUNT - 1
    if not options or not item.get("meaning"):
        return []
    result, seen = [], set()
    for variant in range(READING_VARIANTS):
        picked = [options[(variant * size + step) % len(options)] for step in range(min(size, len(options)))]
        key = frozenset(picked)
        if key in seen:
            break
        seen.add(key)
        result.append({"prompt": item["title"], "task": READING_TASK, "answer": item["reading"],
                       "choices": [item["reading"], *picked], "feedback": _feedback(item),
                       "safe": False, "strict": True})
    return result
