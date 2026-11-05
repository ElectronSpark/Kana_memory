"""One observable answer, one declared primary ability, no graph credit.

Contracts are derived by the app from the actual response control. Authored or
generated `assessment_contract` objects are not accepted as scoring authority.
Legacy questions acquire a detached contract without rewriting their snapshots.
"""
from .material_review import fingerprint

VERSION = 1


def _cloze_slots(question):
    from .multi_cloze import is_cloze_question, specification
    return specification(question)['slots'] if is_cloze_question(question) else ()


def _component_fields(question):
    from .pitch_practice import is_pitch_question
    if not is_pitch_question(question):
        return ()
    mode = question.get('input_mode')
    if mode == 'pitch_identify':
        return ('mora_count', 'accent')
    spec = question.get('pitch_practice', {})
    return tuple(field for field, observable in (
        ('morae', spec.get('learner_morae') or spec.get('version') == 3),
        ('boundaries', spec.get('allow_segmentation')), ('levels', True)) if observable)


def primary_unit(question):
    target = question.get("target_id", question.get("content_id"))
    ability = question.get("ability", question.get("dimension"))
    ability = {"meaning": "understanding", "usage": "use"}.get(ability, ability)
    if any(not isinstance(v, str) or not v or len(v) > 1024 for v in (target, ability)):
        raise ValueError("An assessment needs one explicit target ability")
    unit = {"knowledge_id": target, "ability": ability, "script": question.get("script") or ""}
    return unit


def for_question(question):
    mode = question.get("input_mode", question.get("response", "choice"))
    # Unknown controls stay observable, but cannot claim unsupported free recall.
    scaffold = "recognition" if mode == "choice" else "cued_retrieval"
    if mode in {"text", "typing", "free_text"}:
        scaffold = "free_retrieval"
    if question.get("guided"):
        scaffold = "demonstration"
    elif "practice" in question:
        from .learning_practice_policy import practice_metadata, question_scaffold
        if practice_metadata(question) is not None:
            scaffold = {"recognition": "recognition", "supported": "cued_retrieval",
                        "retrieval": "free_retrieval"}.get(question_scaffold(question), "cued_retrieval")
    target = primary_unit(question)
    from .knowledge_graph import question_relations
    relations = question_relations(question)
    from .semantic_errors import for_question as semantic_descriptor
    semantic = semantic_descriptor(question)
    result = {
        "version": VERSION,
        "primary_target": target,
        "response_mode": mode,
        "scaffold": scaffold,
        "roles": [{"knowledge_id": target["knowledge_id"], "ability": target["ability"], "role": "primary"},
                  *[{"knowledge_id": edge["target"], "role": "background"} for edge in relations]],
        "relations": relations,
        "semantic_conditions": semantic['conditions'] if semantic else [],
        "error_opportunities": semantic['opportunities'] if semantic else [],
        "scored_parts": [{"id": "answer", "slot": "answer", "target": target, "role": "primary",
                          "judge": "local_question", "accepted_answers": list(dict.fromkeys(
                              [question.get("answer", ""), *question.get("accepted_answers", [])]))}],
        # These are declared candidates, not proof of actual exposure. Runtime
        # question_support still filters them; submit freezes its visible flags.
        "answer_visibility": {"optional_audio_declared": bool(question.get("optional_audio_text")),
                              "annotations_declared": bool(question.get("support")),
                              "runtime_guard": "question_support.v1",
                              "guided": bool(question.get("guided"))},
        "provenance": {"question_id": question.get("id", question.get("question_id", "")),
                       "content_revision": question.get("revision", ""),
                       "condition_source": question.get("learning_condition_source", ""),
                       "condition_revision": question.get("learning_condition_revision", "")},
    }
    for field in _component_fields(question):
        result['scored_parts'].append({'id': 'pitch:' + field, 'slot': field, 'target': target,
                                      'role': 'component', 'judge': 'local_pitch', 'accepted_answers': []})
    for slot in _cloze_slots(question):
        result['scored_parts'].append({'id': 'cloze:' + slot['id'], 'slot': slot['id'],
            'target': {'knowledge_id': slot['target_id'], 'ability': slot['ability'], 'script': ''},
            'role': 'component' if slot['role'] == 'primary' else 'background',
            'judge': 'local_multi_cloze', 'accepted_answers': []})
    if semantic:
        result['provenance']['semantic_revision'] = semantic['revision']
        result['provenance']['semantic_source'] = semantic['source_id']
    result['aggregation'] = 'declared_primary'
    result["sha256"] = fingerprint(result)
    return result


def observation(question, result):
    """Preserve the answer's local observation separately from its eligibility."""
    raw = result.get("observed_judgement", result)
    return {"part_id": "answer", "unit": primary_unit(question), "role": "primary",
            "answered_text": result.get("answered_text", ""),
            "outcome": raw.get("outcome", "unresolved"), "correct": raw.get("correct"),
            "assisted": bool(result.get("assisted")),
            "presentation": result.get("presentation", {}),
            "attribution": "direct" if raw.get("outcome") in {"correct", "incorrect"} else "unresolved"}


def observations(question, result):
    """Component diagnostics retain facts without guessing later unchecked parts."""
    main = observation(question, result)
    parts = [main]
    for field in _component_fields(question):
        if main['outcome'] == 'correct':
            outcome, correct = 'correct', True
        elif main['outcome'] == 'incorrect' and field in result.get('pitch_errors', ()):
            outcome, correct = 'incorrect', False
        else:
            outcome, correct = 'unresolved', None
        parts.append({**main, 'part_id': 'pitch:' + field, 'role': 'component',
                      'outcome': outcome, 'correct': correct,
                      # The full immutable response is retained in the primary.
                      'answered_text': '', 'response_ref': 'answer',
                      'attribution': 'direct' if correct is not None else 'unresolved'})
    slots = _cloze_slots(question)
    if slots:
        from .multi_cloze import grade_answer
        raw = result.get('observed_judgement', result)
        judged = (grade_answer(question, result.get('answered_text', ''))
                  if raw.get('outcome') in {'correct', 'incorrect', 'unresolved'} else {})
        values = {row['slot_id']: row for row in judged.get('slot_results', [])}
        for slot in slots:
            value = values.get(slot['id'], {})
            correct = value.get('correct')
            parts.append({**main, 'part_id': 'cloze:' + slot['id'],
                'unit': {'knowledge_id': slot['target_id'], 'ability': slot['ability'], 'script': ''},
                'role': 'component' if slot['role'] == 'primary' else 'background',
                'outcome': value.get('outcome', 'unresolved'), 'correct': correct,
                'answered_text': '', 'response_ref': 'answer',
                'attribution': 'direct' if correct is not None else 'unresolved'})
    return parts


def validate_result(question, result):
    if result is None or 'unit_observations' not in result:
        return  # Existing saved observations are not migrated or re-judged.
    from .assessment_parts import aggregate
    expected = observations(question, result)
    if result.get('observations') != expected or result['unit_observations'] != aggregate(expected):
        raise ValueError('Assessment parts disagree with the frozen response')
