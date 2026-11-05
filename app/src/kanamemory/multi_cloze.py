"""Bounded, versioned multi-gap tasks; no scheduling or inferred graph credit."""
from __future__ import annotations

from copy import deepcopy
import json
import re
import unicodedata

MODE = "multi_cloze"
PREFIX = "cloze1:"
STATE_KEY = "cloze_state"
MAX_SLOTS = 6
_SLOT_FIELDS = {"id", "start", "end", "quote", "choices", "target_id", "ability", "role"}
_BLANK = re.compile(r"(?:（[\s\u3000]*）|（(?:[1-6]|[①-⑥])）|［[1-6]］)")


def _normal(value):
    return unicodedata.normalize("NFKC", value).strip()


def _text(value, limit=256):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= limit
            and not any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value))


def is_cloze_question(question):
    return isinstance(question, dict) and question.get("input_mode") == MODE


def blank_ranges(prompt):
    """Public anchor ranges, shared with the answer-free API descriptor."""
    return [(match.start(), match.end()) for match in _BLANK.finditer(prompt)]


def specification(question):
    """Validate authoring facts; a full snapshot additionally binds its owner."""
    if not is_cloze_question(question):
        raise ValueError("Not a multi-gap question")
    spec = question.get("multi_cloze")
    prompt = question.get("prompt")
    if (not isinstance(spec, dict) or set(spec) != {"version", "slots", "accepted_combinations"}
            or type(spec["version"]) is not int or spec["version"] != 1
            or not isinstance(prompt, str) or not 1 <= len(prompt) <= 8000):
        raise ValueError("Invalid multi-gap specification")
    slots, combinations = spec["slots"], spec["accepted_combinations"]
    if not isinstance(slots, list) or not 2 <= len(slots) <= MAX_SLOTS:
        raise ValueError("Multi-gap tasks require two to six slots")
    ids, end, primaries = set(), 0, set()
    for slot in slots:
        if (not isinstance(slot, dict) or set(slot) != _SLOT_FIELDS
                or not _text(slot["id"], 64) or slot["id"] in ids
                or type(slot["start"]) is not int or type(slot["end"]) is not int
                or not end <= slot["start"] < slot["end"] <= len(prompt)
                or not isinstance(slot["quote"], str) or not _BLANK.fullmatch(slot["quote"])
                or prompt[slot["start"]:slot["end"]] != slot["quote"]
                or not _text(slot["target_id"], 1024) or not _text(slot["ability"], 80)
                or not isinstance(slot["role"], str) or slot["role"] not in {"primary", "background"}):
            raise ValueError("Invalid multi-gap slot or public anchor")
        choices = slot["choices"]
        if (not isinstance(choices, list) or not 2 <= len(choices) <= 12
                or any(not _text(value, 160) for value in choices)
                or len({_normal(value) for value in choices}) != len(choices)):
            raise ValueError("Multi-gap choices must be distinct bounded text")
        ids.add(slot["id"])
        end = slot["end"]
        if slot["role"] == "primary":
            primaries.add((slot["target_id"], slot["ability"]))
    if len(primaries) != 1:
        raise ValueError("Multi-gap tasks need one primary learning ability")
    primary = next(iter(primaries))
    if any(slot["role"] == "background" and slot["target_id"] == primary[0] for slot in slots):
        raise ValueError("The primary item cannot masquerade as background")
    for key, value in zip(("target_id", "ability"), primary):
        if key in question and question[key] != value:
            raise ValueError("Multi-gap primary differs from its question owner")
    if (not isinstance(combinations, list) or not 1 <= len(combinations) <= 32
            or any(not isinstance(row, list) or len(row) != len(slots)
                   or any(not isinstance(value, str) or value not in slots[i]["choices"]
                          for i, value in enumerate(row)) for row in combinations)
            or len({tuple(row) for row in combinations}) != len(combinations)):
        raise ValueError("Invalid reviewed multi-gap answer combinations")
    covered = [(slot["start"], slot["end"]) for slot in slots]
    if blank_ranges(prompt) != covered:
        raise ValueError("Every visible blank must belong to an ordered slot")
    if len(json.dumps(spec, ensure_ascii=False).encode()) > 48000:
        raise ValueError("Multi-gap specification is too large")
    return deepcopy(spec)


def encode_answer(values):
    """Encode a complete ordered response, retaining slot boundaries."""
    if (not isinstance(values, (list, tuple)) or not 2 <= len(values) <= MAX_SLOTS
            or any(not _text(value, 160) for value in values)):
        raise ValueError("Invalid complete multi-gap response")
    return PREFIX + json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def canonical_answer(question):
    return encode_answer(specification(question)["accepted_combinations"][0])


def _decode(question, answer):
    spec = specification(question)
    if not isinstance(answer, str) or not answer.startswith(PREFIX) or len(answer) > 8000:
        raise ValueError("Invalid multi-gap response")
    try:
        values = json.loads(answer[len(PREFIX):])
    except (ValueError, RecursionError) as exc:
        raise ValueError("Invalid multi-gap response") from exc
    if (not isinstance(values, list) or len(values) != len(spec["slots"])
            or any(not _text(value, 160) for value in values)):
        raise ValueError("Response does not cover the frozen slots")
    return values


def validate_snapshot(question):
    """Legacy modes remain untouched; new structures cannot borrow old credit."""
    if not is_cloze_question(question):
        if isinstance(question, dict) and question.get("multi_cloze") is not None:
            raise ValueError("Multi-gap data belongs to its explicit response mode")
        return
    spec = specification(question)
    owner = next(slot for slot in spec["slots"] if slot["role"] == "primary")
    expected = canonical_answer(question)
    allowed = (("grammar", "grammar_cloze", "application"), ("word", "vocab_usage", "use"))
    if (str(question.get('target_id', '')).startswith('pronunciation:')
            and question.get('domain', 'pronunciation') == 'pronunciation'):
        allowed += (("grammar", "pronunciation_application", "application"),)
    support = question.get("support", {})
    if (question.get("target_id") != owner["target_id"]
            or (question.get("family"), question.get("kind"), question.get("ability")) not in allowed
            or question.get("answer") != expected
            or question.get("accepted_answers", [expected]) != [expected]
            or question.get("choices", []) or question.get("options", [])
            or question.get("audio_text") or question.get("optional_audio_text")
            or question.get("requires_audio") or not isinstance(support, dict)
            or support.get("audio_safe") is True):
        raise ValueError("Multi-gap snapshot disagrees with its reviewed contract")


def initial_draft(question):
    slots = specification(question)["slots"]
    return {STATE_KEY: {"version": 1, "active_slot": slots[0]["id"], "values": {}}, "answer": ""}


def _state(question, draft):
    spec = specification(question)
    if not isinstance(draft, dict):
        raise ValueError("Invalid multi-gap draft")
    state = draft.get(STATE_KEY)
    if state is None:
        if draft.get("answer"):
            raise ValueError("A response without its multi-gap draft cannot be resumed")
        state = initial_draft(question)[STATE_KEY]
    if (not isinstance(state, dict) or set(state) - {"version", "active_slot", "values", "selection_order"}
            or not {'version', 'active_slot', 'values'} <= set(state)
            or type(state["version"]) is not int or state["version"] != 1
            or not isinstance(state["values"], dict)):
        raise ValueError("Invalid multi-gap draft state")
    slots = {slot["id"]: slot for slot in spec["slots"]}
    if not isinstance(state["active_slot"], str) or state["active_slot"] not in slots or set(state["values"]) - set(slots):
        raise ValueError("Multi-gap draft references another slot")
    for identity, value in state["values"].items():
        if not isinstance(value, str) or value not in slots[identity]["choices"]:
            raise ValueError("Multi-gap draft selection is not offered")
    values = state["values"]
    order = state.get('selection_order')
    if 'selection_order' in state and (not isinstance(order, list) or len(order) > MAX_SLOTS
            or any(not isinstance(key, str) for key in order)
            or len(order) != len(set(order)) or set(order) != set(values)):
        raise ValueError('Invalid multi-gap selection order')
    expected = (encode_answer([values[slot["id"]] for slot in spec["slots"]])
                if len(values) == len(slots) else "")
    if draft.get("answer", "") != expected:
        raise ValueError("Multi-gap draft and serialized response differ")
    return deepcopy(state)


def validate_draft(question, draft):
    if not is_cloze_question(question):
        if isinstance(draft, dict) and STATE_KEY in draft:
            raise ValueError("A multi-gap draft cannot attach to another response mode")
        return
    _state(question, draft)


def validate_checkpoint(content_id, revision, checkpoint):
    """Validate the new frozen guide only, without rewriting older checkpoints."""
    if not isinstance(checkpoint, dict):
        raise ValueError("Invalid multi-gap guide checkpoint")
    question = checkpoint.get("guide_question")
    if not is_cloze_question(question):
        draft = checkpoint.get("guide_draft")
        if isinstance(draft, dict) and STATE_KEY in draft:
            raise ValueError("Multi-gap guide draft lacks its frozen question")
        return
    if (question.get("target_id") != content_id or question.get("revision") != revision
            or question.get("guided") is not True or question.get("evidence_mode") != "exposure_only"):
        raise ValueError("Multi-gap guide targets a different lesson")
    validate_snapshot(question)
    validate_draft(question, checkpoint.get("guide_draft", {}))


def edit_slot(question, draft, slot_id=None, value=None, *, clear=False):
    """Return a detached proposal; persistence precedes changing the visible UI."""
    spec, state = specification(question), _state(question, draft)
    slots = {slot["id"]: slot for slot in spec["slots"]}
    identity = state["active_slot"] if slot_id is None else slot_id
    if not isinstance(identity, str) or identity not in slots or type(clear) is not bool or clear and value is not None:
        raise ValueError("Invalid multi-gap editing action")
    state["active_slot"] = identity
    order = list(state.get('selection_order', [key for key in slots if key in state['values']]))
    if clear:
        state["values"].pop(identity, None)
        state['selection_order'] = [key for key in order if key != identity]
    elif value is not None:
        if not isinstance(value, str) or value not in slots[identity]["choices"]:
            raise ValueError("This choice does not belong to the selected gap")
        state["values"][identity] = value
        state['selection_order'] = [key for key in order if key != identity] + [identity]
        # Advance to the next unfilled gap, never overwrite another selection.
        identities = list(slots)
        ordered = identities[identities.index(identity) + 1:] + identities[:identities.index(identity)]
        state["active_slot"] = next((key for key in ordered if key not in state["values"]), identity)
    values = state["values"]
    answer = (encode_answer([values[slot["id"]] for slot in spec["slots"]])
              if len(values) == len(slots) else "")
    return {**deepcopy(draft), STATE_KEY: state, "answer": answer}


def undo_selection(question, draft):
    """Remove the most recently chosen gap; legacy drafts use sentence order."""
    spec, state = specification(question), _state(question, draft)
    order = state.get('selection_order', [slot['id'] for slot in spec['slots'] if slot['id'] in state['values']])
    return edit_slot(question, draft, slot_id=order[-1], clear=True) if order else deepcopy(draft)


def grade_answer(question, answer):
    """Grade the primary conservatively; background errors never lower its SRS."""
    spec = specification(question)
    empty = {"status": "empty", "correct": None, "scorable": False,
             "primary_correct": None, "primary_scorable": False, "overall_correct": None,
             "slot_results": [], "background_errors": [], "message": "请填完空位。"}
    if answer in (None, ""):
        return empty
    try:
        values = _decode(question, answer)
    except ValueError:
        return {**empty, "status": "invalid", "message": "作答格式不完整，请检查空位。"}
    combinations = spec["accepted_combinations"]
    accepted = values in combinations
    rows = []
    for index, (slot, value) in enumerate(zip(spec["slots"], values)):
        possible = {row[index] for row in combinations}
        correct = True if value in possible else False if value in slot["choices"] else None
        rows.append({"slot_id": slot["id"], "target_id": slot["target_id"],
                     "ability": slot["ability"], "role": slot["role"], "answered_text": value,
                     "correct": correct, "outcome": "correct" if correct is True else
                     "incorrect" if correct is False else "unresolved"})
    primary = [row for row in rows if row["role"] == "primary"]
    wrong = [row["slot_id"] for row in rows if row["role"] == "background" and row["correct"] is False]
    if accepted:
        status, correct, primary_correct, message = "correct", True, True, question.get("feedback", "")
    elif any(row["correct"] is False for row in primary):
        status, correct, primary_correct, message = "incorrect", False, False, question.get("feedback", "")
    else:
        status, correct = "unresolved", None
        indices = [i for i, slot in enumerate(spec["slots"]) if slot["role"] == "primary"]
        primary_combination = tuple(values[i] for i in indices)
        approved_primary = {tuple(row[i] for i in indices) for row in combinations}
        primary_correct = (True if wrong and primary_combination in approved_primary
                           and all(row["correct"] is not None for row in rows) else None)
        message = ("目标空已填对；其他空仍需核对。" if primary_correct else "这组表达还需核对，本次不调整复习。")
    if not accepted and all(row["correct"] is True for row in rows):
        # Individually attested forms are not evidence that this combination is valid.
        for row in rows:
            row.update(correct=None, outcome="unresolved")
    return {"status": status, "correct": correct, "scorable": correct is not None,
            "primary_correct": primary_correct, "primary_scorable": correct is not None,
            "overall_correct": accepted if all(row["correct"] is not None for row in rows) else None,
            "slot_results": rows, "background_errors": wrong, "message": message}


def answer_label(question, answer=None):
    """Reconstruct readable Japanese; never return encoded or malformed payloads."""
    spec = specification(question)
    if answer is None:
        values = spec["accepted_combinations"][0]
    else:
        try:
            values = _decode(question, answer)
        except ValueError:
            return "未完成"
    pieces, position = [], 0
    for slot, value in zip(spec["slots"], values):
        pieces.extend((question["prompt"][position:slot["start"]], value))
        position = slot["end"]
    pieces.append(question["prompt"][position:])
    return "".join(pieces)
