"""Register every built-in kind with the values of the former constant tables.

Registration order is part of the contract: KIND_LABELS/KIND_ABILITIES keep
their historical insertion order and the family/practice tuples are filtered
from it. Migrated families set their hooks from question_kinds/<family>.py
(vocab_sound, vocab_context: template_provider and cues; vocab_choice,
vocab_text, grammar_meaning, grammar_cloze, grammar_production: builder,
grader, presenter, input_mode_policy as each kind needs; pronunciation:
snapshot_finisher); a None hook means the generic path handles it. The
cross-family flags (safe_support, background_support,
order_translation_source, dictionary_lesson, reads_written_form) restate the
former learning_questions sets, pinned by test_question_kind_registry.
"""
from __future__ import annotations

from ..question_kind_registry import QuestionKindSpec, Registry
from . import (grammar_cloze, grammar_meaning, grammar_production, pronunciation, vocab_choice,
               vocab_context, vocab_sound, vocab_text)
from ..word_sound_questions import AUDIO_WORD, READING_CHOICE

CONTEXT_CHOICE = vocab_context.KIND

REGISTRY = Registry()


def _add(kind, label, family, ability, **options):
    REGISTRY.register(QuestionKindSpec(kind=kind, label=label, family=family, ability=ability, **options))


_OPT_IN_LISTENING = dict(default_enabled=False, opt_in_listening=True, needs_audio=True)
# Choice snapshots that may enable reviewed reading/accent/audio support.
_SAFE = dict(safe_support=True)
# Snapshots that may gloss learned background words of their sentence.
_BACKGROUND = dict(background_support=True)


def _choice(kind, **options):
    """question_kinds/vocab_choice: legacy builder and instruction cues."""
    return dict(builder=vocab_choice.build, presenter=vocab_choice.PRESENTATIONS.get(kind), **options)


# Authored word kinds (templates from lessons and reviewed practice packs).
_add("vocab_meaning", "看词辨义", "word", "understanding", dictionary_lesson=True,
     order_translation_source="prompt", **_SAFE, **_choice("vocab_meaning"))
_add("vocab_audio_meaning", "听词辨义", "word", "listening",
     **_choice("vocab_audio_meaning", **_OPT_IN_LISTENING))
_add("vocab_reading", "写出读法", "word", "reading", text_input=True, dictionary_lesson=True,
     reads_written_form=True, order_translation_source="prompt", builder=vocab_text.build,
     grader=vocab_text.READING_GRADER, presenter=vocab_text.READING_PRESENTATION,
     input_mode_policy=vocab_text.reading_input)
_add("vocab_recall", "根据含义想词", "word", "use", text_input=True, builder=vocab_text.build,
     grader=vocab_text.WRITTEN_FORM_GRADER, presenter=vocab_text.RECALL_PRESENTATION,
     input_mode_policy=vocab_text.recall_input)
# Its options are written words: the same written-form check as recall.
_add("vocab_orthography", "选择写法", "word", "use",
     **_choice("vocab_orthography", grader=vocab_text.WRITTEN_FORM_GRADER))
# Its text form (かなで穴埋め) writes the word itself: the same romaji runs as
# recall. Choice snapshots never compose romaji (romaji_reading_input).
_add("vocab_usage", "语境选词", "word", "use", order_translation_source="gap", **_BACKGROUND,
     **_choice("vocab_usage", input_mode_policy=vocab_text.recall_input))
_add("vocab_zh_ja_contrast", "中日词义辨析", "word", "understanding", order_translation_source="prompt",
     **_SAFE, **_choice("vocab_zh_ja_contrast"))
_add("reading_comprehension", "短文理解", "word", "understanding", order_translation_source="prompt",
     **_SAFE, **_choice("reading_comprehension"))
_add("listening_comprehension", "短句听力", "word", "listening",
     **_choice("listening_comprehension", **_OPT_IN_LISTENING))
def _production(kind, **options):
    """question_kinds/grammar_production: a produced sentence is the only source."""
    return dict(builder=grammar_meaning.build, presenter=grammar_production.PRESENTATIONS.get(kind),
                input_mode_policy=grammar_production.romaji_input, order_translation_source="answer",
                **_BACKGROUND, **options)


# Grammar kinds (every legacy grammar lesson builds through grammar_meaning.build).
_add("grammar_meaning", "语法含义", "grammar", "understanding", order_translation_source="prompt",
     builder=grammar_meaning.build, presenter=grammar_meaning.PRESENTATION, **_SAFE)
_add("grammar_cloze", "语法填空", "grammar", "application", order_translation_source="gap",
     builder=grammar_meaning.build, presenter=grammar_cloze.PRESENTATION,
     input_mode_policy=grammar_cloze.romaji_input, **_BACKGROUND)
_add("grammar_transform", "变换词形", "grammar", "application", text_input=True,
     **_production("grammar_transform"))
_add("grammar_reorder", "排列句子", "grammar", "application", **_production("grammar_reorder"))
_add("targeted_correction", "修正一处错误", "grammar", "application", text_input=True,
     **_production("targeted_correction"))
_add("controlled_sentence_production", "按要求造句", "grammar", "application", text_input=True,
     **_production("controlled_sentence_production"))
# Pronunciation kinds (reviewed templates only; question_kinds/pronunciation).
_add("pronunciation_rule", "读音规则辨析", "pronunciation", "understanding",
     snapshot_finisher=pronunciation.finish)
_add("pronunciation_application", "读音规则应用", "pronunciation", "application",
     order_translation_source="prompt", snapshot_finisher=pronunciation.finish)
# Generated sound kinds: on by default; the heard-word task still needs a
# working voice (audio_ready_kinds). Practice and Tutor only.
_add(AUDIO_WORD, "听词选词", "word", "listening", needs_audio=True, practice_only=True,
     added_at_version=1, template_source="sound", template_provider=vocab_sound.templates,
     presenter=vocab_sound.PRESENTATIONS[AUDIO_WORD])
_add(READING_CHOICE, "看词选读音", "word", "reading", practice_only=True, reads_written_form=True,
     added_at_version=1, template_source="sound", template_provider=vocab_sound.templates,
     presenter=vocab_sound.PRESENTATIONS[READING_CHOICE])
# Reviewed Japanese-only context choice from its own content namespace.
_add(CONTEXT_CHOICE, "读句选词", "word", "use", practice_only=True,
     added_at_version=2, template_source=vocab_context.NAMESPACE, order_translation_source="gap",
     template_provider=vocab_context.templates, presenter=vocab_context.PRESENTATION, **_BACKGROUND)
# Experimental kinds: named only, never issued, no ability or label yet.
# Families are descriptive; no derived table includes experimental kinds.
for _kind, _family in (("free_writing_translation", "word"), ("situational_dialogue", "word"),
                       ("pitch_discrimination", "pronunciation"),
                       ("shadowing_read_aloud", "pronunciation"), ("spoken_recall", "word")):
    _add(_kind, "", _family, None, default_enabled=False, template_source="none", experimental=True)

REGISTRY.seal()
