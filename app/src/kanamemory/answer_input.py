"""Answer capabilities, independent of a device's installed input methods.

The key inventory depends on the public stimulus, never the hidden answer.
Checking that at least one already-accepted answer is enterable changes neither
the question nor its grader. In particular it is not a kanji-reading guesser.
"""
from __future__ import annotations

import unicodedata

SETTING = "answer_input_preferences_v1"
TEXT_MODES = frozenset({"text", "typing", "free_text"})
BASIC = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
VOICED = "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽゔ"
SMALL = "ぁぃぅぇぉゃゅょっゎゕゖ"
SYMBOLS = "ー、。！？「」『』（）・〜…：；＝＋／0123456789"
EXTRA = "ゐゑゝゞヷヸヹヺヽヾㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ"


def katakana(text):
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in text)


FIXED_KEYS = frozenset(BASIC + VOICED + SMALL + katakana(BASIC + VOICED + SMALL)
                       + SYMBOLS + EXTRA)


def system_japanese_enabled(store):
    value = store.get_setting(SETTING, {})
    return isinstance(value, dict) and value.get("system_japanese") is True


def japanese_text(question):
    if question.get("input_mode", question.get("response", "choice")) not in TEXT_MODES:
        return False
    from .question_support import learning_text_role
    return learning_text_role(question, "input") == "study"


def romaji_reading_input(question):
    """Only free vocabulary reading recall; never a kana selection stimulus."""
    return (question.get("kind") == "vocab_reading" and japanese_text(question)
            and not question.get("choices") and not question.get("options"))


def reading_input_conversion(question, value):
    """Convert the learner's draft, without looking at the expected answer.

    Raw input remains the persisted draft so unfinished consonants survive
    navigation/restart. Preview and submission share the keyboard convention.
    """
    if not romaji_reading_input(question) or not isinstance(value, str):
        return None
    import re
    if not re.search(r"[a-zA-Zａ-ｚＡ-ＺāīūēōĀĪŪĒŌ]", value):
        return None
    if len(value) > 10000:
        return {"text": "", "pending": "输入过长", "complete": False}
    from .romaji_input import convert_romaji
    return convert_romaji(value, convention="keyboard", final=True)


def public_characters(question):
    """All visible Han characters, in public order, not answer-selected hints."""
    prompt_visible = question.get("prompt_visible") is not False and not any(question.get(k) for k in (
        "audio_text", "audio_only", "has_hidden_transcript"))
    text = (str(question.get("prompt") or "") if prompt_visible else "")
    text += str(question.get("task") or "")
    return tuple(dict.fromkeys(c for c in text if "\u3400" <= c <= "\u9fff"
                              or "\uf900" <= c <= "\ufaff" or c in "々〆〇"))[:96]


def without_japanese_ime(question):
    """Whether the existing response can be completed by the actual controls."""
    if not japanese_text(question):
        return True
    from .learning_questions import _normal, _grammar_kana
    keys = {unicodedata.normalize("NFKC", c) for c in FIXED_KEYS}
    keys.update(unicodedata.normalize("NFKC", c) for c in public_characters(question))
    answers = [question.get("answer", ""), *question.get("accepted_answers", [])]
    for answer in answers:
        if not isinstance(answer, str) or not answer.strip():
            continue
        # This is precisely the existing grammar grader's curated equivalence,
        # not transliteration using a dictionary's arbitrary first reading.
        if question.get("family") == "grammar" and question.get("domain") != "pronunciation":
            answer = _grammar_kana(answer)
        normalized = _normal(answer)
        if normalized and all(c in keys or c.isascii() for c in normalized):
            return True
    return False


def available(question, *, system_japanese=False):
    return system_japanese is True or without_japanese_ime(question)
