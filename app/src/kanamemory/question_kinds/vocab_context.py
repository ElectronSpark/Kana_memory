"""Reviewed Japanese-only context choice (vocab_context_choice).

Pure rules: no Toga, database, platform, network or content loading. Templates
live in their own content namespace (NAMESPACE, equal to the spec's
template_source) and are bound to the exact lesson by item_fingerprint through
the binding the caller lends; this keeps varied_practice and every existing
template position unchanged. Both shapes, single-gap choice (context_choice)
and a shared word bank over several gaps (context_word_bank, multi_cloze), are
built, presented and graded by the generic choice and multi_cloze paths; the
rule that every option word and grammar point is in background_ids (all
learned before the task is issued) is enforced by the generic practice policy.
Its two Chinese-free tasks are shown verbatim as study text (PRESENTATION).
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from ..question_kind_registry import KindPresentation

KIND = "vocab_context_choice"
NAMESPACE = "context_choice"
# The task of one gap, and of several gaps sharing a word bank.
CONTEXT_CHOICE_TASK, CONTEXT_WORD_BANK_TASK = "文脈で選ぶ", "文脈で選ぶ · 語群"
PRESENTATION = KindPresentation(study_tasks=(CONTEXT_CHOICE_TASK, CONTEXT_WORD_BANK_TASK))


def templates(item: Mapping, spec, env) -> list[dict]:
    """TemplateProvider: reviewed tasks bound to this exact lesson, or []."""
    if item.get("family") != "word" or spec.ability not in item.get("abilities", ()):
        return []
    binding = env.practice_binding(item, spec.template_source)
    return deepcopy((binding or {}).get("templates", {}).get(spec.kind, []))
