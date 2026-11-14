"""Grammar production: transform, reorder, correct or compose a sentence.

Pure rules: no Toga, database, platform, network or content loading. This
module owns the kind-specific parts of the four grammar kinds whose answer is
a complete sentence the learner produces (grammar_transform,
grammar_reorder, targeted_correction, controlled_sentence_production): the
reorder cues, which prompts are instructions rather than study text (pieces
to order, a requirement to follow) and the romaji composition style. They are
built from the shared grammar legacy builder (question_kinds/grammar_meaning)
or the generic template path and graded by the generic local grader. Their
stimulus may be deliberately wrong or shuffled, so their spec flags (builtin)
bind an order translation to the canonical answer only.
"""
from __future__ import annotations

from ..question_kind_registry import KindPresentation

TRANSFORM_KIND = "grammar_transform"
REORDER_KIND = "grammar_reorder"
CORRECTION_KIND = "targeted_correction"
PRODUCTION_KIND = "controlled_sentence_production"
KINDS = (TRANSFORM_KIND, REORDER_KIND, CORRECTION_KIND, PRODUCTION_KIND)

# Instruction cue; the same literal is study text in question_presentation.
ORDER = "語順"
# input_mode_policy result: convert only Latin runs of the draft.
ROMAJI_RUNS = "runs"

PRESENTATIONS = {
    REORDER_KIND: KindPresentation(plain_tasks=(
        ("排成一句话。", ORDER), ("依次点击词块，拼出句子。", ORDER), ("按词块排列成完整句子", ORDER)),
        prompt_is_instruction=True),
    PRODUCTION_KIND: KindPresentation(prompt_is_instruction=True),
}


def romaji_input(question):
    """input_mode_policy: a sentence draft may mix kanji; convert Latin runs only."""
    return ROMAJI_RUNS
