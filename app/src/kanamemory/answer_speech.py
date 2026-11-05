"""Read-only answer speech projection, separate from labels and grading.

Only callers that already know the field's language may pass ordinary Japanese
text. Pronunciation labels have a weaker contract: they can contain Chinese,
IPA, counts or diagrams despite their Japanese typeface. Those labels need a
Japanese answer or an explicit Japanese stimulus; we never read feedback or
guess an utterance from a lesson's unrelated example.
"""
from __future__ import annotations

import re
from unicodedata import normalize

from .multi_cloze import answer_label, is_cloze_question, specification

_KANA = r"ぁ-ゖァ-ヺーゝゞヽヾ"
_HAN = r"\u3400-\u4dbf\u4e00-\u9fff々〆"
_PUNCTUATION = "。、，,.！？!?「」『』・… 　"
_GAP = re.compile(r"（\s*）|\(\s*\)|_{2,}|＿{2,}|（[①-⑥1-6]）|［[1-6]］")
_ACCENT = re.compile(r"\[[0-9０-９]+\]|［[0-9０-９]+］|[⓪①-⑳]")
_RUBY_WORD = re.compile(rf"([{_HAN}]+[{_KANA}]*)[（(]([{_KANA}]+)[）)]\Z")
_RUBY_KANJI = re.compile(rf"([{_HAN}]+)[（(]([{_KANA}]+)[）)]")
_PARTICLE_NOTE = re.compile(r"(?<=[はへを])[（(](?:wa|e|o|wo)[）)]")
_JAPANESE = re.compile(rf"[{_KANA}{_HAN}0-9０-９{re.escape(_PUNCTUATION)}]+\Z")
_KANA_UTTERANCE = re.compile(rf"[{_KANA}{re.escape(_PUNCTUATION)}]+\Z")


def clean_utterance(value: str) -> str:
    """Remove known display marks, or stay silent on unresolved notation.

    Retain lexical digits, Japanese long vowels and small kana. This is not a
    language detector and must not be used as a global TTS text filter.
    """
    if not isinstance(value, str) or not value.strip():
        return ""
    value = normalize("NFC", value).strip()
    # A missing answer must not become a plausible but incomplete sentence.
    if _GAP.search(value):
        return ""
    # Spaced middot separates answer metadata, never the lexical Japanese ・.
    value = value.split(" · ", 1)[0].strip()
    value = _ACCENT.sub("", value)
    ruby = _RUBY_WORD.fullmatch(value)
    value = ruby[2] if ruby else _RUBY_KANJI.sub(lambda match: match[2], value)
    value = _PARTICLE_NOTE.sub("", value)
    value = re.sub(r"^[✓✔✗✘○×]\s*", "", value)
    value = re.sub(r"[↑↓↗↘ꜛꜜ‾¯_|｜]", "", value)
    # A final arrow can denote a level pitch. An internal arrow is a diagram
    # or a transformation, not a sentence, and is rejected below.
    value = value.rstrip("→ ")
    value = re.sub(r"(?<![0-9０-９])-+(?![0-9０-９])", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not _JAPANESE.fullmatch(value) or not re.search(rf"[{_KANA}{_HAN}]", value):
        return ""
    return value


def pronunciation_utterance(question: dict, public_text: str) -> str:
    """Use the current question's speech, never its analytical answer label."""
    if is_cloze_question(question):
        # These exercises often place a word above two analytical response
        # slots (IPA and mora count). Only that explicit stimulus can be read.
        prompt = question.get("prompt", "")
        first_line = prompt.split("\n", 1)[0]
        spec = specification(question)
        slots = spec["slots"]
        if public_text and all(slot["start"] > len(first_line) for slot in slots):
            # A first slot can be the actual reading/contraction, followed by
            # an analytical count. Prefer it over an uncorrected stimulus.
            spoken = _pronunciation_answer(spec["accepted_combinations"][0][0], question)
            if spoken and question.get("text_roles", {}).get("answer") == "study":
                return spoken
            return _pronunciation_stimulus(first_line)
        return ""
    answer = _pronunciation_answer(question.get("answer", ""), question)
    # Typeface alone cannot authorize Chinese rule explanations, IPA or counts.
    if answer and question.get("text_roles", {}).get("answer") == "study":
        if public_text and len(_GAP.findall(public_text)) == 1:
            return clean_utterance(_GAP.sub(lambda match: answer, public_text))
        stimulus = _pronunciation_stimulus(public_text)
        # A mora divider is removable only when the unsegmented current word
        # proves its role. Genuine lexical middots remain intact elsewhere.
        if "・" in answer and answer.replace("・", "") == stimulus:
            return stimulus
        return answer
    return _pronunciation_stimulus(public_text)


def _pronunciation_stimulus(value: str) -> str:
    if not value:
        return ""
    return clean_utterance(value.replace("／", "、").replace(" / ", "、"))


def _pronunciation_answer(value: str, question: dict) -> str:
    variants = [clean_utterance(part.strip()) for part in re.split(r"／| / ", value)]
    # Mixed-writing answers are licensed only for the existing word-form
    # conversion curriculum. Elsewhere a kana can occur in Chinese analysis,
    # so neither its presence nor its typeface establishes speech permission.
    conversion = str(question.get("target_id", "")).startswith("pronunciation:contraction_")
    if not all(part and re.search(rf"[{_KANA}]", part) for part in variants):
        return ""
    if not conversion and any(not _KANA_UTTERANCE.fullmatch(part) for part in variants):
        return ""
    # Alternative pitch annotations can reduce to exactly the same utterance.
    return "、".join(dict.fromkeys(variants))


def completed_cloze_utterance(question: dict) -> str:
    """Canonical Japanese completion; no serialized arrays or classifier rows."""
    return clean_utterance(answer_label(question))
