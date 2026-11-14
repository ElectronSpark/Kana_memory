"""Generated sound kinds: heard word to written word, word to its reading.

Pure rules: no Toga, database, platform, network or content loading. This
module defines who gets the two sound kinds and which generator builds each;
word_sound_questions keeps the option generators and kind names (intro_batch
and tests import them). Templates derive only from a lesson's own title,
reading and meaning, so they need no reviewed binding and no saved snapshot
changes when they are regenerated. Build, input and grading use the generic
choice path; this module adds only their instruction cues (the heard-word task
is shown verbatim as study text, the reading task as its reading cue).

Known-word variants (design 11 section 5, WP-K2): known_variants draws the
options from words in the learner's known set; variant_template rebuilds such
an encoded variant from the lesson and the word pool alone.

Kana-spelling variants ("pick the kana spelling" of the heard word, 0.34.0)
are withdrawn and never offered (2026-11-12, see
learning_questions.sound_variants); variant_template still rebuilds a stored
one from its number.
"""
from __future__ import annotations

from collections.abc import Mapping

from ..question_kind_registry import KindPresentation
from ..word_sound_questions import (AUDIO_KANA_TASK, AUDIO_TASK, AUDIO_WORD, READING_CHOICE, READING_TASK,
                                    audio_word_templates, known_audio_word_variants,
                                    known_reading_choice_variants, known_variant_template,
                                    reading_choice_templates)

KINDS = (AUDIO_WORD, READING_CHOICE)
_GENERATORS = {AUDIO_WORD: audio_word_templates, READING_CHOICE: reading_choice_templates}
_KNOWN_GENERATORS = {AUDIO_WORD: known_audio_word_variants, READING_CHOICE: known_reading_choice_variants}
# Instruction cue; the same literal is study text in question_presentation.
READING = "読み"
PRESENTATIONS = {AUDIO_WORD: KindPresentation(study_tasks=(AUDIO_TASK, AUDIO_KANA_TASK)),
                 READING_CHOICE: KindPresentation(plain_tasks=((READING_TASK, READING),))}


def eligible(item: Mapping, ability: str) -> bool:
    """Sound tasks only derive from a lesson's own fields; never AI or custom text."""
    return not (item.get("family") != "word" or item.get("ai_generated")
                or str(item.get("id", "")).startswith("local:")
                or not item.get("title") or not item.get("reading")
                or ability not in item.get("abilities", ()))


def templates(item: Mapping, spec, env) -> list[dict]:
    """TemplateProvider for both sound kinds; [] when the lesson cannot have them."""
    if not eligible(item, spec.ability):
        return []
    return _GENERATORS[spec.kind](item, env.word_pool())


def known_variants(item: Mapping, spec, env, known_ids) -> list[tuple[int, dict]] | None:
    """(variant, template) pairs whose options come from known words.

    None when the lesson cannot have the kind or fewer than three known
    candidates exist; the caller then keeps the bank variants (templates).
    """
    if not eligible(item, spec.ability):
        return None
    return _KNOWN_GENERATORS[spec.kind](item, env.word_pool(), known_ids)


def variant_template(item: Mapping, spec, env, variant: int) -> dict:
    """The template an encoded (known-word or kana-spelling) variant names; ValueError when unavailable."""
    if not eligible(item, spec.ability):
        raise ValueError("No sound task is available for this word")
    return known_variant_template(spec.kind, item, env.word_pool(), variant)
