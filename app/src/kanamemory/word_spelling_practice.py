"""Build real, enabled follow-up questions for measured spelling mistakes."""
from dataclasses import replace
from .kana_question_policy import question_allowed
from .word_spelling import reading_evidence_unit

from .domain import (
    HANDWRITING_SKILLS, LISTENING_SKILLS, WORD_SKILLS,
    _BatchQuestionStore, build_questions,
)


def build_spelling_corrections(repository, store, question, analysis, *, kinds,
                              question_index, pending=(), now=None,
                              handwriting_available=False, audio_available=False,
                              kana_categories=None):
    """Preserve the existing queue; one same-script repair per mistaken item.

    These are actual retrievals, not inferred FSRS ratings. Recognition is the
    first scaffold when enabled; advanced-only choices remain respected.
    """
    if question.kind not in WORD_SKILLS or analysis.get("assisted"):
        return []
    enabled = set(kinds)
    order = ("romaji_kana", "kana_choice", "romaji_kana_full", "kana_romaji",
             "romaji_handwriting", "kana_handwriting")
    possible = [kind for kind in order if kind in enabled
                and (kind not in HANDWRITING_SKILLS or handwriting_available)
                and (kind not in LISTENING_SKILLS or audio_available)]
    if not possible:
        return []
    reserved = {(item.content_id, item.script) for item in pending
                if item.kind in enabled and question_allowed(item)}
    result = []
    for unit in analysis.get("units", ()):
        key = (unit["content_id"], question.script)
        if unit["result"] != "incorrect" or key in reserved or not reading_evidence_unit(unit):
            continue
        if not store.is_content_available(unit["content_id"], now):
            continue
        for kind in possible:
            card = store.get_scheduling_card(unit["content_id"], kind, question.script)
            if card and card["suspended"]:
                continue
            candidates = build_questions(repository,
                _BatchQuestionStore(store, selected=[unit["content_id"]]),
                kind=kind, script=question.script, limit=1,
                seed=f"spelling:{question.content_id}:{question_index}:{unit['content_id']}",
                now=now, include_extended=True, kana_categories=kana_categories)
            if not candidates:
                continue
            item = candidates[0]
            config = {**item.metadata["practice_config"],
                      "schedule_mode": question.metadata.get("practice_config", {}).get("schedule_mode", "spaced"),
                      "kinds": list(kinds), "requested_script": question.script}
            result.append(replace(item, metadata={**item.metadata,
                "practice_config": config,
                "spelling_correction_of": question.content_id,
                "spelling_correction_index": question_index}))
            reserved.add(key)
            break
    return result
