"""Reviewed lexical context for exact bundled questions, never a dictionary.

These are meanings of already public non-target words, not translations of
the tested construction. Each small lexicon is anchored to its authored
lesson and complete prompt. It cannot supply hints to unrelated AI/imported
sentences. Readings are supplied separately by the reviewed annotation data.
"""
from __future__ import annotations


from .content_pack import load_namespace

_CONTEXT = load_namespace("question_contexts", {})
_GROUPS = _CONTEXT.get("_GROUPS", ())
_CONTEXT_HINTS = dict(_CONTEXT.get("_CONTEXT_HINTS", ()))


def supplemental_background_senses():
    """Explicitly reviewed local glosses that are not separate memory items."""
    return _CONTEXT.get("supplemental_background_senses", ())


def legacy_display_rules():
    """Exact old snapshot rules; the caller still verifies provenance/safety."""
    return _CONTEXT.get("legacy_display_rules", ())


def reviewed_question_context(target_id, prompt):
    """Return exact anchored lexical records, without changing the question."""
    glosses = []
    for suffix, prompts, words in _GROUPS:
        if target_id != "grammar:" + suffix or prompt not in prompts:
            continue
        # Longest authored spans take precedence (五時十分 before 五時).
        occupied = set()
        for surface, meaning in sorted(words, key=lambda word: -len(word[0])):
            start = prompt.find(surface)
            if start < 0 or any(i in occupied for i in range(start, start + len(surface))):
                continue
            glosses.append({"start": start, "end": start + len(surface), "surface": surface,
                            "meaning": meaning, "target_id": None})
            occupied.update(range(start, start + len(surface)))
        break
    return {"background_glosses": sorted(glosses, key=lambda part: part["start"]),
            "context_hint": _CONTEXT_HINTS.get((target_id, prompt))}
