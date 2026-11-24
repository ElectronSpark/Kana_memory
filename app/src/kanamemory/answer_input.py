"""Answer capabilities, independent of a device's installed input methods.

The key inventory depends on the public stimulus, never the hidden answer.
Checking that at least one already-accepted answer is enterable changes neither
the question nor its grader. In particular it is not a kanji-reading guesser.
Both read the effective answer policy (answer_policy: the template's answers
with its overlay row), and a typed question without a kana answer is never
available, whatever the system-keyboard setting (answer_policy.kana_answer, T9).
"""
from __future__ import annotations

import re
import unicodedata

from .question_kinds import REGISTRY

SETTING = "answer_input_preferences_v1"
TEXT_MODES = frozenset({"text", "typing", "free_text"})
BASIC = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
VOICED = "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽゔ"
SMALL = "ぁぃぅぇぉゃゅょっゎゕゖ"
SYMBOLS = "ー、。！？「」『』（）・〜…：；＝＋／0123456789"
EXTRA = "ゐゑゝゞヷヸヹヺヽヾㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ"
# Latin letters that start a romaji composition (ASCII, full-width, macrons).
LATIN_LETTER = re.compile(r"[a-zA-Zａ-ｚＡ-ＺāīūēōĀĪŪĒŌ]")


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


def _romaji_style(question):
    """"reading" (whole draft), "runs" (Latin runs only) or None (no romaji).

    Each kind's style is its registered input_mode_policy (question_kinds/
    vocab_text, grammar_cloze, grammar_production); no policy means no romaji.
    """
    spec = REGISTRY.get(question.get("kind"))
    if spec is not None and spec.input_mode_policy is not None:
        return spec.input_mode_policy(question)
    return None


def romaji_reading_input(question):
    """Free reading/grammar composition; retain the original shared API name.

    Kana selection and pronunciation analysis are separate response contracts.
    Eligibility depends only on the public task, never its expected answer.
    """
    return (_romaji_style(question) is not None and japanese_text(question)
            and not question.get("choices") and not question.get("options"))


def has_latin(value):
    """Whether a draft holds a Latin letter (romaji, an ABC-keyboard word)."""
    return isinstance(value, str) and LATIN_LETTER.search(value) is not None


def reading_input_conversion(question, value):
    """Convert the learner's draft, without looking at the expected answer.

    Raw input remains the persisted draft so unfinished consonants survive
    navigation/restart. Preview and submission share the keyboard convention.
    """
    if not romaji_reading_input(question) or not isinstance(value, str):
        return None
    if not has_latin(value):
        return None
    if len(value) > 10000:
        return {"text": "", "pending": "输入过长", "complete": False}
    from .romaji_input import convert_romaji
    if _romaji_style(question) != "reading":
        # A word or grammar draft may include public kanji, digits or existing kana.
        # Convert only Latin runs; do not guess kanji or rewrite other text.
        output, end = [], 0
        for match in re.finditer(r"[a-zA-Zａ-ｚＡ-ＺāīūēōĀĪŪĒŌ'’＇\-－]+", value):
            if not has_latin(match.group()):
                continue
            output.append(value[end:match.start()])
            result = convert_romaji(match.group(), convention="keyboard", final=True)
            output.append(result["text"])
            if not result["complete"]:
                return {"text": "".join(output),
                        "pending": result["pending"] + value[match.end():], "complete": False}
            end = match.end()
        output.append(value[end:])
        return {"text": "".join(output), "pending": "", "complete": True}
    return convert_romaji(value, convention="keyboard", final=True)


def public_characters(question):
    """All visible Han characters, in public order, not answer-selected hints."""
    prompt_visible = question.get("prompt_visible") is not False and not any(question.get(k) for k in (
        "audio_text", "audio_only", "has_hidden_transcript"))
    text = (str(question.get("prompt") or "") if prompt_visible else "")
    text += str(question.get("task") or "")
    return tuple(dict.fromkeys(c for c in text if "\u3400" <= c <= "\u9fff"
                              or "\uf900" <= c <= "\ufaff" or c in "々〆〇"))[:96]


def without_japanese_ime(question, policy=None):
    """Whether the existing response can be completed by the actual controls.

    ``policy``: the question's effective answer policy (answer_policy.source.policy_for when absent).
    """
    if not japanese_text(question):
        return True
    from .learning_questions import _typed_normal, _grammar_kana
    from .answer_policy.source import policy_for
    accepted = (policy or policy_for(question)).accepted
    keys = {unicodedata.normalize("NFKC", c) for c in FIXED_KEYS}
    keys.update(unicodedata.normalize("NFKC", c) for c in public_characters(question))
    answers = [question.get("answer", ""), *accepted]
    for answer in answers:
        if not isinstance(answer, str) or not answer.strip():
            continue
        # This is precisely the existing grammar grader's curated equivalence,
        # not transliteration using a dictionary's arbitrary first reading.
        if question.get("family") == "grammar" and question.get("domain") != "pronunciation":
            answer = _grammar_kana(answer)
        normalized = _typed_normal(answer)
        if normalized and all(c in keys or c.isascii() for c in normalized):
            return True
    return False


def available(question, *, system_japanese=False, template=None):
    """Whether the question can be served with these input settings.

    A typed Japanese question needs a kana answer (T9, independent of ``system_japanese``); then the
    system keyboard, or the app's keys and the prompt's Han characters, must reach an accepted answer.
    ``template``: the raw template ``question`` was made from at selection (with the lesson id as
    ``target_id``), so that its overlay row is found; a built question is located from its own fields.
    """
    if not japanese_text(question):
        return True
    from .answer_policy import source
    from .answer_policy.kana_answer import has_kana_answer
    if template is not None:
        policy = source.policy_for(question, row_value=source.template_row(
            question.get("target_id"), question.get("kind"), template))
    else:
        policy = source.policy_for(question)
    if not has_kana_answer(question, policy):
        return False
    return system_japanese is True or without_japanese_ime(question, policy)
