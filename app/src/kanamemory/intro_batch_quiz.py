"""The batch quiz's question rules (四之二): which guided choice question an item's quiz asks, and its re-ask.

Pure: no Toga, no store, no app object. intro_batch owns the batch's state rules and the store every
write; this module only chooses, for one lesson and the kinds that are on, the frozen question (and a
second one for a re-ask) under the dependency gate of design 11 section 5.
"""
from __future__ import annotations

from .learning_questions import (build_learning_question, question_kinds,
                                 practice_templates, question_requires, retired_practice_variants,
                                 sound_variants,
                                 KIND_ABILITIES, LISTENING_KINDS, READING_CHOICE_KIND,
                                 SOUND_KINDS)

_RECOGNITION_KINDS = {"word": ("vocab_meaning", READING_CHOICE_KIND), "grammar": ("grammar_meaning",)}
# Units with fewer reviews than this are "early" (四之二 item 6).
EARLY_REVIEWS = 2


def background_known(question, known_ids):
    """+1 context: an authored task's background items are all known.

    Questions without author metadata name no background item.
    """
    from .learning_practice_policy import practice_metadata
    meta = practice_metadata(question) if "practice" in question else None
    return meta is None or set(meta["background_ids"]) <= {question.get("target_id"), *known_ids}


def _presentable(question):
    # The batch quiz page presents choices; other operations keep their own guide.
    return question.get("input_mode", "choice") == "choice" and question.get("answer") in question.get("choices", [])


def requirements_met(item, question, known_ids):
    """Dependency gate for one built question (design 11 section 5, D3).

    True when the question's declared requirements (reviewed background ids
    plus its question_dependencies entry) are all known, and for a question
    that declares nothing (a fallback template). Unmet requirements fail.
    """
    requires = question_requires(item, question)
    return requires is None or requires <= {question.get("target_id"), item.get("id"), *known_ids}


def _quiz_variants(item, kind, known_ids=()):
    # The quiz starts from its usual second variant and only then tries the
    # kind's other templates, so a met or fallback variant is found before
    # moving to a less preferred kind. A sound kind offers its known-word
    # variants (options from known words) in the same order.
    try:
        templates = practice_templates(item)
        sound = [number for number, _ in sound_variants(item, kind, known_ids)] if kind in SOUND_KINDS else []
    except (ValueError, KeyError, TypeError):
        templates, sound = None, []  # build_learning_question reports the lesson's own error per kind.
    if sound:
        return (*sound[1:2], *sound[:1], *sound[2:])
    count = len(templates.get(kind) or ()) if isinstance(templates, dict) else 0
    return (1, *(variant for variant in range(max(count, 2)) if variant != 1))


def _retired(item):
    try:
        templates = practice_templates(item)
    except (ValueError, KeyError, TypeError):
        return frozenset()  # build_learning_question reports the lesson's own error per kind.
    return retired_practice_variants(item, templates)


def intro_quiz_question(item, enabled_kinds, *, ability=None, known_background_ids=None):
    """One guided recognition question, else any enabled guided choice question.

    With known_background_ids a question is taken only when requirements_met:
    a question with unmet requirements is never the quiz question, and a
    question declaring nothing is the fallback of its kind. Kind preference
    (recognition first) still decides before the gate; None when no enabled
    kind offers a presentable, requirement-safe question. Retired variants
    (exact retirements and the cognate-class rule, design 11 S2) are skipped
    as in ordinary selection, so an S word's quiz is its one bare task.
    """
    admitted = set(item.get("enrolled_abilities") or [unit["ability"] for unit in item.get("units", [])])
    if ability is not None:
        admitted &= {ability}
    available = [kind for kind in question_kinds(item) if kind in set(enabled_kinds)
                 and KIND_ABILITIES.get(kind) in admitted]
    preferred = [kind for kind in _RECOGNITION_KINDS.get(item.get("family"), ()) if kind in available]
    fallback = [kind for kind in available if kind not in preferred and kind not in LISTENING_KINDS]
    retired = _retired(item) if known_background_ids is not None else frozenset()
    for kind in (*preferred, *fallback):
        for variant in (_quiz_variants(item, kind, known_background_ids) if known_background_ids is not None
                        else (1,)):
            if (kind, variant) in retired:
                continue
            try:
                question = build_learning_question(item, kind, variant=variant, guided=True)
            except (ValueError, KeyError):
                continue
            if not _presentable(question):
                continue
            if known_background_ids is None or requirements_met(item, question, known_background_ids):
                return question
    return None


def intro_quiz_questions(item, enabled_kinds, *, ability=None, known_background_ids=None):
    """The recognition question, plus a second one for a re-ask when possible.

    Like guided_practice_sequence, the second question uses another actual
    context and prefers another kind; it must be an authored choice question
    of the same ability. Without such a question the item keeps only one.
    """
    from .learning_practice_policy import practice_candidates, question_context
    first = intro_quiz_question(item, enabled_kinds, ability=ability, known_background_ids=known_background_ids)
    if first is None:
        return []
    kinds = [kind for kind in question_kinds(item) if kind in set(enabled_kinds)
             and KIND_ABILITIES.get(kind) == KIND_ABILITIES.get(first["kind"]) and kind not in LISTENING_KINDS]
    context = question_context(first)
    candidates = sorted((candidate for candidate in practice_candidates(
                             item, kinds, known_background_ids=tuple(known_background_ids or ()))
                         if candidate["input_mode"] == "choice" and not candidate.get("relaxed")
                         and not candidate.get("top_up")),
                        key=lambda candidate: (candidate["kind"] == first["kind"], candidate["stage"],
                                               candidate["variant"]))
    for candidate in candidates:
        try:
            question = build_learning_question(item, candidate["kind"], variant=candidate["variant"], guided=True)
        except (ValueError, KeyError):
            continue
        if _presentable(question) and question["id"] != first["id"] and question_context(question) != context:
            return [first, question]
    return [first]
