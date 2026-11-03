"""Versioned, local pitch exercises and reversible drafts; no learner writes.

Authored morae are mandatory. This module never guesses a word's accent, uses
TTS as pitch evidence, or treats an editor button as a separate review. Version
1 covers one lexical word and an optional explicitly separated particle.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from .pitch_accent import pitch_levels
from . import pitch_sequence, pitch_alignment

MODES = frozenset({"pitch_fill", "pitch_identify"})
PREFIX = "pp1:"
_SMALL_JOIN = frozenset("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ")
_SPECIAL = frozenset("っッんンー")
_KANA = re.compile(r"[ぁ-ゖァ-ヶー]+\Z")
_MAX_CHARS = 32
_MAX_ACTIONS = _MAX_CHARS * 2


def is_pitch_question(question):
    return isinstance(question, dict) and question.get("input_mode") in MODES


def _sequence(question):
    return (isinstance(question, dict) and isinstance(question.get("pitch_practice"), dict)
            and question["pitch_practice"].get("version") in (2, 3))


def _sequence_module(question):
    return pitch_alignment if question["pitch_practice"].get("version") == 3 else pitch_sequence


def public_task(question):
    return _sequence_module(question).public_task(question)


def _morae(value, reading, *, optional=False):
    if (not isinstance(reading, str) or len(reading) > _MAX_CHARS
            or not isinstance(value, list) or len(value) > _MAX_CHARS
            or any(not isinstance(m, str) for m in value)):
        raise ValueError("Invalid authored pitch morae")
    if optional and reading == "" and value == []:
        return []
    if not reading or not _KANA.fullmatch(reading) or "".join(value) != reading:
        raise ValueError("Pitch morae must reproduce the exact kana reading")
    for mora in value:
        # The author, not a generic small-kana splitter, specifies each join.
        if (not mora or mora[0] in _SMALL_JOIN or len(mora) > 2
                or len(mora) == 2 and (mora[0] in _SPECIAL or mora[1] not in _SMALL_JOIN)):
            raise ValueError("Invalid authored pitch mora")
    return list(value)


def specification(question):
    """Validate and return a detached v1 stimulus/solution specification."""
    if _sequence(question):
        return _sequence_module(question).specification(question)
    if not is_pitch_question(question):
        raise ValueError("Not a pitch exercise")
    value = question.get("pitch_practice")
    fields = {"version", "word", "reading", "morae", "accent", "particle",
              "particle_morae", "learner_morae", "allow_segmentation"}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value.get("version")) is not int or value["version"] != 1):
        raise ValueError("Unsupported pitch practice specification")
    if not isinstance(value["word"], str) or not value["word"].strip() or len(value["word"]) > 80:
        raise ValueError("A pitch exercise requires a word label")
    _morae(value["morae"], value["reading"])
    _morae(value["particle_morae"], value["particle"], optional=True)
    if len(value["reading"] + value["particle"]) > _MAX_CHARS:
        raise ValueError("Pitch exercise is too long")
    count, accent = len(value["morae"]), value["accent"]
    if type(accent) is not int or not 0 <= accent <= count:
        raise ValueError("Pitch accent must be within the lexical word")
    if any(type(value[key]) is not bool for key in ("learner_morae", "allow_segmentation")):
        raise ValueError("Pitch editor options must be boolean")
    if value["allow_segmentation"] and not value["particle"]:
        raise ValueError("Segmentation requires an authored boundary")
    if question["input_mode"] == "pitch_identify" and not value["particle"] and accent in (0, count):
        # Flat and final-drop contours are identical inside a word. A v1
        # single-answer exercise must supply the following particle.
        raise ValueError("Flat versus final drop requires a following particle")
    return deepcopy(value)


def _levels(spec):
    levels = ["H" if level == "-" else "L" for level in pitch_levels(len(spec["morae"]), spec["accent"])]
    return levels + (["H" if spec["accent"] == 0 else "L"] * len(spec["particle_morae"]))


def _encode(mode, value):
    return PREFIX + json.dumps({"mode": mode, **value}, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)


def canonical_answer(question):
    if _sequence(question):
        return _sequence_module(question).canonical_answer(question)
    spec = specification(question)
    if question["input_mode"] == "pitch_identify":
        return _encode("pitch_identify", {"mora_count": len(spec["morae"]), "accent": spec["accent"]})
    return _encode("pitch_fill", {"morae": spec["morae"] + spec["particle_morae"],
        "levels": _levels(spec), "boundaries": [len(spec["morae"])] if spec["particle"] else []})


def _decode(question, answer):
    if not isinstance(answer, str) or not answer.startswith(PREFIX) or len(answer) > 4096:
        raise ValueError("Invalid pitch response")
    try:
        value = json.loads(answer[len(PREFIX):])
    except (ValueError, RecursionError) as exc:
        raise ValueError("Invalid pitch response") from exc
    mode = question["input_mode"]
    if not isinstance(value, dict) or value.get("mode") != mode:
        raise ValueError("Pitch response mode mismatch")
    if mode == "pitch_identify":
        if set(value) != {"mode", "mora_count", "accent"}:
            raise ValueError("Invalid pitch identification")
        for key, low in (("mora_count", 1), ("accent", 0)):
            number = value[key]
            if number is not None and (type(number) is not int or not low <= number <= _MAX_CHARS):
                raise ValueError("Invalid pitch selection")
    else:
        if set(value) != {"mode", "morae", "levels", "boundaries"}:
            raise ValueError("Invalid pitch composition")
        morae, levels, boundaries = value["morae"], value["levels"], value["boundaries"]
        if (not isinstance(morae, list) or len(morae) > _MAX_CHARS
                or any(not isinstance(m, str) or not m or len(m) > 2 or not _KANA.fullmatch(m) for m in morae)
                or not isinstance(levels, list) or len(levels) != len(morae)
                or any(level not in ("H", "L") for level in levels)
                or not isinstance(boundaries, list)
                or any(type(i) is not int or not 0 < i < len(morae) + 1 for i in boundaries)
                or boundaries != sorted(set(boundaries))):
            raise ValueError("Invalid pitch composition")
        spec = specification(question)
        if not (spec["reading"] + spec["particle"]).startswith("".join(morae)):
            raise ValueError("Pitch response changes the supplied reading")
    return value


def validate_snapshot(question):
    """Old question modes are untouched; reject malformed new snapshots."""
    if not is_pitch_question(question):
        return
    if _sequence(question):
        return _sequence_module(question).validate_snapshot(question)
    specification(question)
    expected = canonical_answer(question)
    if (question.get("answer") != expected or question.get("accepted_answers", [expected]) != [expected]
            or question.get("choices", []) or question.get("options", [])
            or question.get("domain") != "pronunciation"
            or question.get("family") != "grammar"
            or question.get("kind") not in {"pronunciation_rule", "pronunciation_application"}
            or question.get("ability") != ("understanding" if question.get("kind") == "pronunciation_rule" else "application")
            or question.get("audio_text") or question.get("optional_audio_text") or question.get("requires_audio")
            or question.get("prompt") != question["pitch_practice"]["reading"] + question["pitch_practice"]["particle"]):
        raise ValueError("Pitch snapshot disagrees with its authored stimulus")


def initial_draft(question):
    if _sequence(question):
        return _sequence_module(question).initial_draft(question)
    specification(question)
    state = {"version": 1, "actions": []} if question["input_mode"] == "pitch_fill" else {
        "version": 1, "mora_count": None, "accent": None}
    return {"pitch_state": state, "answer": ""}


def _replay(spec, actions):
    if not isinstance(actions, list) or len(actions) > _MAX_ACTIONS:
        raise ValueError("Invalid pitch editor actions")
    text = spec["reading"] + spec["particle"]
    authored = spec["morae"] + spec["particle_morae"]
    starts, joins, cursor = {}, set(), 0
    for mora in authored:
        starts[cursor] = mora
        joins.update(range(cursor + 1, cursor + len(mora)))
        cursor += len(mora)
    cursor, pieces, levels, boundaries = 0, [], [], []
    for action in actions:
        if action in ("H", "L"):
            if cursor >= len(text):
                raise ValueError("Pitch reading is already complete")
            piece = text[cursor] if spec["learner_morae"] else starts.get(cursor)
            if not piece:
                raise ValueError("Invalid pitch cursor")
            pieces.append(piece); levels.append(action); cursor += len(piece)
            if not spec["allow_segmentation"] and spec["particle"] and cursor == len(spec["reading"]):
                boundaries.append(len(pieces))
        elif action == "merge":
            if (not spec["learner_morae"] or cursor not in joins or not pieces
                    or len(pieces[-1]) != 1 or len(pieces) in boundaries):
                raise ValueError("This character cannot join the preceding mora")
            pieces[-1] += text[cursor]; cursor += 1
            if not spec["allow_segmentation"] and spec["particle"] and cursor == len(spec["reading"]):
                boundaries.append(len(pieces))
        elif action == "split":
            if (not spec["allow_segmentation"] or not 0 < cursor < len(text)
                    or not pieces or len(pieces) in boundaries):
                raise ValueError("A word boundary cannot be placed here")
            boundaries.append(len(pieces))
        else:
            raise ValueError("Unknown pitch editor action")
    can_merge = bool(spec["learner_morae"] and cursor in joins and pieces
                     and len(pieces[-1]) == 1 and len(pieces) not in boundaries)
    can_split = bool(spec["allow_segmentation"] and 0 < cursor < len(text)
                     and pieces and len(pieces) not in boundaries)
    return {"morae": pieces, "levels": levels, "boundaries": boundaries,
            "cursor": cursor, "complete": cursor == len(text), "can_merge": can_merge,
            "can_split": can_split, "next_character": text[cursor:cursor + 1]}


def _state(question, draft):
    spec = specification(question)
    if not isinstance(draft, dict):
        raise ValueError("Invalid pitch draft")
    state = draft.get("pitch_state", initial_draft(question)["pitch_state"])
    if not isinstance(state, dict) or type(state.get("version")) is not int or state["version"] != 1:
        raise ValueError("Unsupported pitch draft")
    if question["input_mode"] == "pitch_fill":
        if set(state) != {"version", "actions"}:
            raise ValueError("Invalid pitch composition draft")
        value = _replay(spec, state["actions"])
        answer = (_encode("pitch_fill", {k: value[k] for k in ("morae", "levels", "boundaries")})
                  if state["actions"] else "")
    else:
        if set(state) != {"version", "mora_count", "accent"}:
            raise ValueError("Invalid pitch identification draft")
        answer = _encode("pitch_identify", {k: state[k] for k in ("mora_count", "accent")})
        value = _decode(question, answer)
        if state["mora_count"] is None and state["accent"] is None:
            answer = ""
        value["complete"] = state["mora_count"] is not None and state["accent"] is not None
    return spec, deepcopy(state), value, answer


def validate_draft(question, draft):
    if not is_pitch_question(question):
        return
    if _sequence(question):
        return _sequence_module(question).validate_draft(question, draft)
    _, _, _, expected = _state(question, draft)
    if draft.get("answer", "") != expected:
        raise ValueError("Pitch draft answer does not match its reversible actions")


def validate_checkpoint(content_id, revision, checkpoint):
    """Validate only the new guide extension; old guide records stay valid."""
    question = checkpoint.get("guide_question")
    if not is_pitch_question(question):
        if "guide_draft" in checkpoint and isinstance(checkpoint["guide_draft"], dict) and "pitch_state" in checkpoint["guide_draft"]:
            raise ValueError("Pitch guide draft lacks its frozen question")
        return
    if (question.get("target_id") != content_id or question.get("revision") != revision
            or question.get("guided") is not True or question.get("evidence_mode") != "exposure_only"):
        raise ValueError("Pitch guide targets a different lesson")
    validate_snapshot(question)
    validate_draft(question, checkpoint.get("guide_draft", {}))


def _editor_actions(spec, actions):
    """Keep v1 storage valid while making its sole word boundary automatic."""
    if not spec["allow_segmentation"]:
        return list(actions)
    # Old drafts may have a learner-placed boundary anywhere. It is no longer
    # an answer dimension for one word + particle; all pitch/grouping actions
    # remain in their original order. Reinsert a storage-compatible split only
    # at the authored word end, never infer or repair a mora grouping.
    semantic = [action for action in actions if action != "split"]
    automatic = {**spec, "allow_segmentation": False}
    _replay(automatic, semantic)
    output = []
    for index, action in enumerate(semantic):
        output.append(action)
        if _replay(automatic, semantic[:index + 1])["cursor"] == len(spec["reading"]):
            output.append("split")
    return output


def editor_draft(question, draft):
    """Detached current editing view of an ungraded draft; no store writes.

    Legacy snapshots and frozen results retain their original meaning. UI
    callers use this only when opening an editable question/guide, then save
    the returned draft on the next edit or submission through the usual store.
    """
    if _sequence(question):
        return _sequence_module(question).editor_draft(question, draft)
    validate_draft(question, draft)
    spec, state, _, _ = _state(question, draft)
    result = deepcopy(draft)
    if question["input_mode"] == "pitch_fill" and spec["allow_segmentation"]:
        state["actions"] = _editor_actions(spec, state["actions"])
        result["pitch_state"] = state
        result["answer"] = _state(question, result)[3]
    return result


def apply_action(question, draft, action):
    """Replay the original v1 editor protocol, including historical splits.

    New UI uses ``editor_action``. Keeping this raw operation allows exact
    validation/reconstruction of saved questions without migrating their data.
    """
    if _sequence(question):
        return _sequence_module(question).editor_action(question, draft, action)
    validate_draft(question, draft)
    _, state, _, _ = _state(question, draft)
    if question["input_mode"] != "pitch_fill":
        raise ValueError("This exercise does not use pitch composition actions")
    if action == "undo":
        state["actions"] = state["actions"][:-1]
    elif action == "clear":
        state["actions"] = []
    else:
        state["actions"].append(action)
    result = {**deepcopy(draft), "pitch_state": state}
    result["answer"] = _state(question, result)[3]
    return result


def editor_action(question, draft, action):
    """Current editing policy: automatic word boundary, semantic undo only."""
    if _sequence(question):
        return _sequence_module(question).editor_action(question, draft, action)
    draft = editor_draft(question, draft)
    spec, state, _, _ = _state(question, draft)
    if question["input_mode"] != "pitch_fill":
        raise ValueError("This exercise does not use pitch composition actions")
    if action == "split":
        raise ValueError("单词与助词的边界已给定，无需分词。")
    if spec["allow_segmentation"]:
        state["actions"] = [value for value in state["actions"] if value != "split"]
    if action == "undo":
        state["actions"] = state["actions"][:-1]
    elif action == "clear":
        state["actions"] = []
    else:
        state["actions"].append(action)
    state["actions"] = _editor_actions(spec, state["actions"])
    result = {**deepcopy(draft), "pitch_state": state}
    result["answer"] = _state(question, result)[3]
    return result


def set_selection(question, draft, field, value):
    if _sequence(question):
        raise ValueError("Whole-utterance pitch tasks do not select a lexical accent number")
    validate_draft(question, draft)
    _, state, _, _ = _state(question, draft)
    if question["input_mode"] != "pitch_identify" or field not in ("mora_count", "accent"):
        raise ValueError("Unknown pitch selection")
    state[field] = value
    result = {**deepcopy(draft), "pitch_state": state}
    result["answer"] = _state(question, result)[3]
    return result


def _display(spec, morae, levels, *, number=None, boundary=None):
    result = {"morae": list(morae), "levels": list(levels),
              "word_mora_count": boundary if boundary is not None else len(spec["morae"]),
              "label": spec["word"] + ("＋" + spec["particle"] if spec["particle"] else "")}
    if number is not None:
        result["number"] = number
    return result


def display_data(question, answer=None, *, reveal=False):
    """Explicitly public/entered data only; never silently complete a fill."""
    if _sequence(question):
        return _sequence_module(question).display_data(question, answer, reveal=reveal)
    spec = specification(question)
    if reveal:
        return _display(spec, spec["morae"] + spec["particle_morae"], _levels(spec), number=spec["accent"])
    if answer:
        value = _decode(question, answer)
        if question["input_mode"] == "pitch_fill":
            if not spec["learner_morae"]:
                all_morae = spec["morae"] + spec["particle_morae"]
                if value["morae"] == all_morae[:len(value["morae"])]:
                    return _display(spec, all_morae,
                                    value["levels"] + [None] * (len(all_morae) - len(value["levels"])))
            # A learner's incorrect split is displayed as entered, never
            # silently repaired using the hidden authored morae.
            boundary = (value["boundaries"] or [len(value["morae"])])[0]
            result = _display(spec, value["morae"], value["levels"], boundary=boundary)
            result["input_groups"] = True
            return result
    if question["input_mode"] == "pitch_identify":
        return _display(spec, spec["morae"] + spec["particle_morae"], _levels(spec))
    return _display(spec, spec["morae"] + spec["particle_morae"], [None] * (len(spec["morae"]) + len(spec["particle_morae"])))


def state_view(question, draft):
    if _sequence(question):
        return _sequence_module(question).state_view(question, draft)
    draft = editor_draft(question, draft)
    spec, state, value, answer = _state(question, draft)
    result = {**value, "answer": answer, "complete": value["complete"]}
    if question["input_mode"] == "pitch_fill":
        result.update(can_undo=any(action != "split" for action in state["actions"]),
            can_split=False,
            remaining=(spec["reading"] + spec["particle"])[value["cursor"]:],
            display=display_data(question, answer))
        if spec["learner_morae"] and not answer:
            result["display"] = _display(spec, [], [])
        result["next_text"] = result["remaining"]
    else:
        result.update(mora_count=state["mora_count"], accent=state["accent"],
            mora_options=list(range(1, min(_MAX_CHARS, len(spec["reading"]) + 2) + 1)),
            accent_options=list(range(0, min(_MAX_CHARS, len(spec["reading"]) + 2) + 1)),
            display=display_data(question))
    result.update({key: result["display"][key] for key in ("morae", "levels", "word_mora_count")})
    return result


def fixed_view(question, draft=None, *, answer=None, reveal=False):
    """Character-anchored presentation; grouping never moves any kana.

    Character/span offsets are Unicode code-point indexes. ``groups`` affect
    lines/ties only, not glyph placement or wrapping. Fill views expose only
    entered levels; advanced fill views never reveal an unentered small-kana
    join. An explicit answer is shown as entered, including incorrect grouping.
    """
    if _sequence(question):
        return _sequence_module(question).fixed_view(question, draft, answer=answer, reveal=reveal)
    if draft is not None and answer is not None:
        raise ValueError("Choose a pitch draft or a frozen answer")
    spec = specification(question)
    text = spec["reading"] + spec["particle"]
    levels = [None] * len(text)
    groups = []
    cursor = None
    active_span = None
    complete = False
    number = None
    entered_morae, entered_levels = [], []
    if reveal or question["input_mode"] == "pitch_identify":
        if draft is not None:
            validate_draft(question, draft)
            complete = _state(question, draft)[2]["complete"]
        elif answer is not None:
            given = _decode(question, answer)
            complete = (given["mora_count"] is not None and given["accent"] is not None
                        if question["input_mode"] == "pitch_identify" else
                        "".join(given["morae"]) == text)
        entered_morae = spec["morae"] + spec["particle_morae"]
        entered_levels = _levels(spec)
        if reveal:
            number = spec["accent"]
    else:
        if draft is not None:
            current = editor_draft(question, draft)
            _, _, value, _ = _state(question, current)
            entered_morae, entered_levels = value["morae"], value["levels"]
            cursor, complete = value["cursor"], value["complete"]
        elif answer:
            value = _decode(question, answer)
            entered_morae, entered_levels = value["morae"], value["levels"]
            complete = "".join(entered_morae) == text
        else:
            cursor = 0
    position = 0
    for mora, level in zip(entered_morae, entered_levels):
        end = position + len(mora)
        groups.append({"start": position, "end": end})
        levels[position:end] = [level] * len(mora)
        position = end
    authored = spec["morae"] + spec["particle_morae"]
    if (not reveal and question["input_mode"] == "pitch_fill"
            and not spec["learner_morae"] and entered_morae == authored[:len(entered_morae)]):
        # Presegmented questions already teach the grouping. Keep those spans
        # visible before input, without filling any missing pitch level.
        groups, position = [], 0
        for mora in spec["morae"] + spec["particle_morae"]:
            groups.append({"start": position, "end": position + len(mora)})
            position += len(mora)
    if cursor is not None and cursor < len(text):
        end = cursor + 1
        if not spec["learner_morae"]:
            end = next(group["end"] for group in groups if group["start"] == cursor)
        active_span = {"start": cursor, "end": end}
    word_end = len(spec["reading"])
    tokens = [{"id": "word:0", "role": "word", "start": 0, "end": word_end}]
    if spec["particle"]:
        tokens.append({"id": "particle:0", "role": "particle", "start": word_end, "end": len(text)})
    result = {"characters": list(text), "levels": levels, "groups": groups,
              "tokens": tokens, "cursor": cursor, "active_span": active_span,
              "complete": complete,
              "label": spec["word"] + ("＋" + spec["particle"] if spec["particle"] else "")}
    if number is not None:
        result["number"] = number
    return result


def grade_answer(question, answer):
    if _sequence(question):
        return _sequence_module(question).grade_answer(question, answer)
    validate_snapshot(question)
    if answer is None or answer == "":
        return {"status": "empty", "correct": None, "message": "请先作答。", "scorable": False}
    try:
        value = _decode(question, answer)
    except ValueError:
        return {"status": "invalid", "correct": None, "message": "这份音高作答无法读取，请重新作答。", "scorable": False}
    spec = specification(question)
    complete = (value["mora_count"] is not None and value["accent"] is not None
                if question["input_mode"] == "pitch_identify" else
                "".join(value["morae"]) == spec["reading"] + spec["particle"])
    if not complete:
        return {"status": "empty", "correct": None, "message": "请完成这道题再检查。", "scorable": False}
    expected = _decode(question, canonical_answer(question))
    correct = value == expected
    result = {"status": "correct" if correct else "incorrect", "correct": correct,
              "message": question.get("feedback", ""), "scorable": True}
    if not correct:
        fields = ("mora_count", "accent") if question["input_mode"] == "pitch_identify" else ("morae", "levels", "boundaries")
        errors = [key for key in fields if value[key] != expected[key]]
        if "morae" in errors or "mora_count" in errors:
            # Once alignment is wrong, apparent pitch differences are not
            # independent errors. Diagnose the grouping before its contour.
            result["pitch_errors"] = ["morae" if "morae" in errors else "mora_count"]
            result["message"] = "先按拍分组：" + "／".join(spec["morae"]) + f"，共 {len(spec['morae'])} 拍。"
            if spec["particle"]:
                result["message"] += "助词「" + spec["particle"] + "」另列，不计入词的拍数。"
        elif "boundaries" in errors:
            result["pitch_errors"] = ["boundaries"]
            result["message"] = "「" + spec["reading"] + "」是词，后面的「" + spec["particle"] + "」是助词。"
        elif "accent" in errors:
            result["pitch_errors"] = ["accent"]
            result["message"] = ("没有词汇性下降，是 0 型。" if spec["accent"] == 0 else
                                 f"在第 {spec['accent']} 拍后下降，是 {spec['accent']} 型。")
        else:
            result["pitch_errors"] = ["levels"]
            index = next(i for i, (a, b) in enumerate(zip(value["levels"], expected["levels"])) if a != b)
            part = f"第 {index + 1} 拍「{expected['morae'][index]}」" if index < len(spec["morae"]) else "助词「" + spec["particle"] + "」"
            result["message"] = part + ("应标高。" if expected["levels"][index] == "H" else "应标低。")
    return result


def answer_label(question, answer=None):
    if _sequence(question):
        return _sequence_module(question).answer_label(question, answer)
    value = _decode(question, canonical_answer(question) if answer is None else answer)
    if question["input_mode"] == "pitch_identify":
        return f"{value['mora_count'] if value['mora_count'] is not None else '—'} 拍 · {value['accent'] if value['accent'] is not None else '—'} 型"
    parts = []
    for index, (mora, level) in enumerate(zip(value["morae"], value["levels"])):
        if index in value["boundaries"]:
            parts.append("／")
        parts.append(mora + (" 高" if level == "H" else " 低"))
    return " · ".join(parts)
