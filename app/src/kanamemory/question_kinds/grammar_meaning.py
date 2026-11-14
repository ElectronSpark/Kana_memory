"""Grammar meaning: what a lesson's form does in a sentence, answered by choosing.

Pure rules: no Toga, database, platform, network or content loading. This
module owns the kind-specific parts of grammar_meaning (its instruction cues)
and the legacy builder shared by every grammar kind: a grammar lesson without
reviewed templates uses its original authored variants, which the caller lends
through BuildEnv (reviewed templates go through the generic template path).
Grading is the generic exact match. Its spec flags (builtin) let a choice
snapshot enable reviewed reading support and bind an order translation to its
prompt.
"""
from __future__ import annotations

from collections.abc import Mapping

from ..question_kind_registry import KindPresentation

KIND = "grammar_meaning"
KINDS = (KIND,)

# Instruction cue; the same literal is study text in question_presentation.
MEANING = "意味"


def build(item: Mapping, spec, env, variant: int) -> dict:
    """Builder for a grammar lesson without reviewed templates (every grammar kind)."""
    variants = env.legacy_grammar_variants(item, spec.kind)
    if not variants:
        raise ValueError("No reviewed question templates are available")
    return variants[variant % len(variants)]


PRESENTATION = KindPresentation(
    quoted_tasks=(r"句中「(?P<word>[^「」\n]+)」对应本课的什么作用？",), quoted_cue=MEANING,
    plain_tasks=(("这句话中，本课的表达起什么作用？", MEANING), ("这句话中本课形式表达什么？", MEANING)))
