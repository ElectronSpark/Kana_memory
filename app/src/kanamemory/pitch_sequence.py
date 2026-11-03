"""Reviewed whole-utterance pitch tasks, with separate coordinate systems.

This module never composes lexical accents into sentence prosody. A snapshot is
usable only while its complete reviewed version is registered in the content
pack. Retaining old registry versions keeps frozen queues and backups valid.
"""
from copy import deepcopy
import hashlib
import json
import re

from .content_pack import load_namespace

PREFIX = "pp2:"
_KANA = re.compile(r"[ぁ-ゖァ-ヶー]+\Z")
_SMALL = frozenset("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮ")
_SPECIAL = frozenset("っッんンー")
_PUNCT = frozenset(" 、。，．！？!?：；:;（）()[]「」『』【】〈〉《》…\n")
_FIELDS = {"version", "stimulus_id", "text", "characters", "mora_spans", "tokens",
           "accent_phrases", "wrap_units", "contexts", "segmentations", "reviewed_patterns",
           "selected_pattern_id", "accepted_pattern_ids", "representation", "lexical_accents",
           "learner_morae", "task_mode", "review"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def semantic_fingerprint(spec):
    """Hash all semantic fields; only the digest itself is excluded."""
    value = deepcopy(spec)
    if isinstance(value.get("review"), dict):
        value["review"].pop("content_sha256", None)
    return hashlib.sha256(_json(value).encode("utf8")).hexdigest()


def _text(value, name, maximum=256):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError("Invalid sequence " + name)
    try:
        value.encode("utf8")
    except UnicodeError as error:
        raise ValueError("Invalid sequence Unicode") from error
    return value


def _list(value, maximum, name, *, empty=False):
    if not isinstance(value, list) or not (0 if empty else 1) <= len(value) <= maximum:
        raise ValueError("Invalid sequence " + name)
    return value


def _refs(value):
    _list(value, 16, "source references")
    for item in value:
        _text(item, "source ID", 128)
    if len(set(value)) != len(value):
        raise ValueError("Duplicate sequence source")
    return set(value)


def _spans(values, length, maximum, name, *, extra=(), cover=False):
    _list(values, maximum, name)
    previous = 0
    for value in values:
        if not isinstance(value, dict) or set(value) != {"start", "end", *extra}:
            raise ValueError("Invalid sequence " + name + " fields")
        start, end = value["start"], value["end"]
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= length or start < previous
                or cover and start != previous):
            raise ValueError("Invalid sequence " + name + " coverage")
        previous = end
    if cover and previous != length:
        raise ValueError("Incomplete sequence " + name)


def _token_list(tokens, spec, mora_edges):
    characters = spec["characters"]
    _spans(tokens, len(characters), 64, "tokens", extra=("id", "role", "source_spans"), cover=True)
    ids = set()
    for token in tokens:
        identity = _text(token["id"], "token ID", 128)
        if identity in ids or token["role"] not in {"word", "particle", "auxiliary", "punctuation"}:
            raise ValueError("Invalid sequence token identity/role")
        ids.add(identity)
        chars = characters[token["start"]:token["end"]]
        if token["role"] == "punctuation":
            if any(c not in _PUNCT for c in chars):
                raise ValueError("Punctuation token includes speech")
        elif (any(c in _PUNCT for c in chars) or token["start"] not in mora_edges
              or token["end"] not in mora_edges):
            raise ValueError("Word tokens must align with reviewed morae")
        _spans(token["source_spans"], len(spec["text"]), 8, "source spans")


def _validate(spec):
    if (not isinstance(spec, dict) or set(spec) != _FIELDS or type(spec["version"]) is not int
            or spec["version"] != 2 or spec["representation"] != "mora_plateau_hl"
            or spec["task_mode"] not in {"pitch", "pitch_segmentation"}
            or type(spec["learner_morae"]) is not bool):
        raise ValueError("Unsupported reviewed sequence specification")
    _text(spec["stimulus_id"], "stimulus ID", 160)
    _text(spec["text"], "source text", 512)
    chars = _list(spec["characters"], 256, "characters")
    if any(not isinstance(c, str) or len(c) != 1 or not (_KANA.fullmatch(c) or c in _PUNCT) for c in chars):
        raise ValueError("Sequence requires explicit kana and punctuation")
    morae = spec["mora_spans"]
    _spans(morae, len(chars), 128, "morae")
    covered, edges = set(), set()
    for span in morae:
        mora = "".join(chars[span["start"]:span["end"]])
        if (not _KANA.fullmatch(mora) or mora[0] in _SMALL or len(mora) > 2
                or len(mora) == 2 and (mora[0] in _SPECIAL or mora[1] not in _SMALL)):
            raise ValueError("A reviewed sequence span must be one mora")
        covered.update(range(span["start"], span["end"]))
        edges.update((span["start"], span["end"]))
    speech = {i for i, c in enumerate(chars) if c not in _PUNCT}
    if covered != speech:
        raise ValueError("Reviewed morae must cover every spoken character")
    _token_list(spec["tokens"], spec, edges)
    _spans(spec["wrap_units"], len(chars), 64, "public wrapping blocks", cover=True)
    _spans(spec["accent_phrases"], len(chars), 16, "accent phrases", extra=("id",))
    phrase_coverage, phrase_ids = set(), set()
    for phrase in spec["accent_phrases"]:
        identity = _text(phrase["id"], "phrase ID", 128)
        if (identity in phrase_ids or phrase["start"] not in edges or phrase["end"] not in edges):
            raise ValueError("Invalid sequence accent phrase")
        phrase_ids.add(identity)
        phrase_coverage.update(range(phrase["start"], phrase["end"]))
    if phrase_coverage & speech != speech:
        raise ValueError("Accent phrases omit spoken characters")
    contexts, all_refs = {}, set()
    for context in _list(spec["contexts"], 8, "contexts"):
        if not isinstance(context, dict) or set(context) != {"id", "prompt_zh", "source_refs"}:
            raise ValueError("Invalid sequence context")
        identity = _text(context["id"], "context ID", 128)
        if identity in contexts:
            raise ValueError("Duplicate sequence context")
        _text(context["prompt_zh"], "context explanation", 600)
        contexts[identity] = context
        all_refs.update(_refs(context["source_refs"]))
    segmentations = {}
    for segment in _list(spec["segmentations"], 8, "segmentations"):
        if not isinstance(segment, dict) or set(segment) != {"id", "tokens", "explanation_zh"}:
            raise ValueError("Invalid sequence segmentation")
        identity = _text(segment["id"], "segmentation ID", 128)
        if identity in segmentations:
            raise ValueError("Duplicate segmentation identity")
        _text(segment["explanation_zh"], "segmentation explanation", 600)
        _token_list(segment["tokens"], spec, edges)
        segmentations[identity] = segment
    patterns = {}
    for pattern in _list(spec["reviewed_patterns"], 8, "reviewed patterns"):
        if not isinstance(pattern, dict) or set(pattern) != {"id", "context_id", "levels", "segmentation_ids", "source_refs"}:
            raise ValueError("Invalid reviewed sequence pattern")
        identity = _text(pattern["id"], "pattern ID", 128)
        if identity in patterns or pattern["context_id"] not in contexts:
            raise ValueError("Unknown sequence context or duplicate pattern")
        if (not isinstance(pattern["levels"], list) or len(pattern["levels"]) != len(morae)
                or any(level not in ("H", "L") for level in pattern["levels"])):
            raise ValueError("Reviewed pattern must supply every mora level")
        allowed = _refs(pattern["segmentation_ids"])
        if not allowed <= set(segmentations):
            raise ValueError("Unknown approved segmentation")
        all_refs.update(_refs(pattern["source_refs"]))
        patterns[identity] = pattern
    selected = spec["selected_pattern_id"]
    accepted = _refs(spec["accepted_pattern_ids"])
    if selected not in patterns or selected not in accepted or not accepted <= set(patterns):
        raise ValueError("Unknown accepted sequence pattern")
    if any(patterns[key]["context_id"] != patterns[selected]["context_id"] for key in accepted):
        raise ValueError("Different contexts cannot be interchangeable answers")
    canonical_segment = patterns[selected]["segmentation_ids"][0]
    if spec["tokens"] != segmentations[canonical_segment]["tokens"]:
        raise ValueError("Canonical sequence tokens disagree with selected pattern")
    accents = _list(spec["lexical_accents"], 64, "lexical accents", empty=True)
    tokens = {token["id"]: token for token in spec["tokens"]}
    accented = set()
    for accent in accents:
        if not isinstance(accent, dict) or set(accent) != {"token_id", "mora_count", "number", "scope"}:
            raise ValueError("Invalid lexical reference")
        token = tokens.get(accent["token_id"])
        if not token or token["role"] != "word" or accent["scope"] != "dictionary" or token["id"] in accented:
            raise ValueError("Invalid lexical reference scope")
        count = sum(token["start"] <= span["start"] and span["end"] <= token["end"] for span in morae)
        if (type(accent["mora_count"]) is not int or accent["mora_count"] != count
                or type(accent["number"]) is not int or not 0 <= accent["number"] <= count):
            raise ValueError("Invalid lexical reference accent")
        accented.add(token["id"])
    review = spec["review"]
    if (not isinstance(review, dict) or set(review) != {"review_id", "content_sha256", "source_refs", "status"}
            or review["status"] != "approved"):
        raise ValueError("Sequence lacks an approved review")
    _text(review["review_id"], "review ID", 160)
    all_refs.update(_refs(review["source_refs"]))
    if review["content_sha256"] != semantic_fingerprint(spec):
        raise ValueError("Reviewed sequence fingerprint mismatch")
    return all_refs


def _specification(question):
    if not isinstance(question, dict) or question.get("input_mode") != "pitch_fill":
        raise ValueError("Whole-utterance sequences require pitch composition")
    spec = question.get("pitch_practice")
    refs = _validate(spec)
    registry = load_namespace("pitch_sequences", {})
    if (not isinstance(registry, dict) or registry.get("format") != "kanamemory-pitch-sequences"
            or type(registry.get("version")) is not int or registry["version"] != 1
            or not isinstance(registry.get("sources"), list) or not isinstance(registry.get("records"), list)):
        raise ValueError("No trusted whole-utterance pitch registry is installed")
    sources = set()
    for source in registry["sources"]:
        if not isinstance(source, dict):
            raise ValueError("Invalid trusted pitch source")
        identity = _text(source.get("id"), "registered source ID", 128)
        _text(source.get("title"), "registered source title", 600)
        _text(source.get("url"), "registered source URL", 1024)
        if identity in sources:
            raise ValueError("Duplicate registered pitch source")
        sources.add(identity)
    if not refs <= sources:
        raise ValueError("Whole-utterance pitch references an unknown source")
    matches = [record for record in registry["records"] if isinstance(record, dict)
               and record.get("stimulus_id") == spec["stimulus_id"]
               and isinstance(record.get("review"), dict)
               and record["review"].get("content_sha256") == spec["review"]["content_sha256"]]
    if len(matches) != 1 or matches[0] != spec:
        raise ValueError("Sequence version is not in the trusted reviewed registry")
    return deepcopy(spec)


def specification(question):
    """Reject damaged/imported containers through the normal validation path."""
    try:
        return _specification(question)
    except (TypeError, KeyError, RecursionError, UnicodeError) as error:
        raise ValueError("Invalid reviewed sequence data") from error


def _patterns(spec):
    return {row["id"]: row for row in spec["reviewed_patterns"]}


def _context(spec):
    return _patterns(spec)[spec["selected_pattern_id"]]["context_id"]


def _boundaries(tokens, chars):
    return [token["start"] for token in tokens if token["start"] > 0
            and chars[token["start"]] not in _PUNCT and chars[token["start"] - 1] not in _PUNCT]


def _encode(spec, groups, levels, boundaries):
    return PREFIX + _json({"mode": "pitch_fill", "spec_sha256": spec["review"]["content_sha256"],
                           "groups": groups, "levels": levels, "boundaries": boundaries})


def canonical_answer(question):
    spec = specification(question)
    return _encode(spec, spec["mora_spans"], _patterns(spec)[spec["selected_pattern_id"]]["levels"],
                   _boundaries(spec["tokens"], spec["characters"]))


def context_prompt(question):
    spec = specification(question)
    return next(row["prompt_zh"] for row in spec["contexts"] if row["id"] == _context(spec))


def public_task(question):
    spec = specification(question)
    instruction = "标出各拍高低，并标记词语边界。" if spec["task_mode"] == "pitch_segmentation" else "标出各拍高低。"
    return context_prompt(question) + "\n" + instruction


def validate_snapshot(question):
    spec = specification(question)
    expected = canonical_answer(question)
    if (question.get("answer") != expected or question.get("accepted_answers", [expected]) != [expected]
            or question.get("choices", []) or question.get("options", [])
            or question.get("domain") != "pronunciation" or question.get("family") != "grammar"
            or question.get("kind") not in ("pronunciation_rule", "pronunciation_application")
            or question.get("ability") != ("understanding" if question.get("kind") == "pronunciation_rule" else "application")
            or question.get("audio_text") or question.get("optional_audio_text") or question.get("requires_audio")
            or question.get("prompt") != "".join(spec["characters"])
            or question.get("task") != public_task(question)):
        raise ValueError("Sequence snapshot disagrees with its reviewed stimulus")


def initial_draft(question):
    spec = specification(question)
    return {"pitch_state": _initial_state(spec), "answer": ""}


def _initial_state(spec):
    return {"version": 2, "stimulus_id": spec["stimulus_id"],
        "spec_sha256": spec["review"]["content_sha256"], "context_id": _context(spec), "actions": []}


def _skip(chars, cursor):
    while cursor < len(chars) and chars[cursor] in _PUNCT:
        cursor += 1
    return cursor


def _replay(spec, actions):
    if not isinstance(actions, list) or len(actions) > 512:
        raise ValueError("Invalid sequence editing actions")
    chars = spec["characters"]
    starts = {span["start"]: span["end"] for span in spec["mora_spans"]}
    joins = {span["start"] + 1 for span in spec["mora_spans"] if span["end"] - span["start"] == 2}
    cursor, groups, levels, boundaries = _skip(chars, 0), [], [], []
    for action in actions:
        if action in ("H", "L"):
            if cursor >= len(chars):
                raise ValueError("Sequence input is already complete")
            end = cursor + 1 if spec["learner_morae"] else starts.get(cursor)
            if end is None:
                raise ValueError("Sequence cursor does not align with a mora")
            groups.append({"start": cursor, "end": end}); levels.append(action)
            cursor = _skip(chars, end)
        elif action == "merge":
            if (not spec["learner_morae"] or cursor not in joins or not groups
                    or groups[-1] != {"start": cursor - 1, "end": cursor}
                    or cursor in boundaries):
                raise ValueError("This sequence character cannot merge with the previous mora")
            groups[-1]["end"] += 1
            cursor = _skip(chars, cursor + 1)
        elif action == "split":
            if (spec["task_mode"] != "pitch_segmentation" or not 0 < cursor < len(chars)
                    or chars[cursor - 1] in _PUNCT or not groups or cursor in boundaries):
                raise ValueError("A word boundary cannot be placed here")
            boundaries.append(cursor)
        else:
            raise ValueError("Unknown sequence editing action")
    if spec["task_mode"] == "pitch":
        boundaries = [b for b in _boundaries(spec["tokens"], chars) if b <= cursor]
    return {"groups": groups, "levels": levels, "boundaries": boundaries, "cursor": cursor,
        "complete": cursor == len(chars),
        "can_merge": bool(spec["learner_morae"] and cursor in joins and groups
            and groups[-1] == {"start": cursor - 1, "end": cursor} and cursor not in boundaries),
        "can_split": bool(spec["task_mode"] == "pitch_segmentation" and 0 < cursor < len(chars)
            and chars[cursor - 1] not in _PUNCT and groups and cursor not in boundaries)}


def _state(question, draft):
    spec = specification(question)
    if not isinstance(draft, dict):
        raise ValueError("Invalid sequence draft")
    state = draft["pitch_state"] if "pitch_state" in draft else _initial_state(spec)
    if (not isinstance(state, dict) or set(state) != {"version", "stimulus_id", "spec_sha256", "context_id", "actions"}
            or type(state["version"]) is not int or state["version"] != 2
            or state["stimulus_id"] != spec["stimulus_id"]
            or state["spec_sha256"] != spec["review"]["content_sha256"] or state["context_id"] != _context(spec)):
        raise ValueError("Sequence draft belongs to a different reviewed stimulus")
    value = _replay(spec, state["actions"])
    answer = _encode(spec, value["groups"], value["levels"], value["boundaries"]) if state["actions"] else ""
    if draft.get("answer", "") != answer:
        raise ValueError("Sequence draft answer disagrees with its actions")
    return spec, deepcopy(state), value, answer


def validate_draft(question, draft):
    _state(question, draft)


def editor_draft(question, draft):
    validate_draft(question, draft)
    return deepcopy(draft)


def editor_action(question, draft, action):
    spec, state, _, _ = _state(question, draft)
    if action == "undo":
        state["actions"] = state["actions"][:-1]
    elif action == "clear":
        state["actions"] = []
    else:
        state["actions"].append(action)
    value = _replay(spec, state["actions"])
    answer = _encode(spec, value["groups"], value["levels"], value["boundaries"]) if state["actions"] else ""
    return {**deepcopy(draft), "pitch_state": state, "answer": answer}


def _decode(spec, answer):
    if not isinstance(answer, str) or not answer.startswith(PREFIX) or len(answer) > 32768:
        raise ValueError("Invalid sequence response")
    try:
        value = json.loads(answer[len(PREFIX):])
    except (ValueError, RecursionError) as error:
        raise ValueError("Invalid sequence response") from error
    if (not isinstance(value, dict) or set(value) != {"mode", "spec_sha256", "groups", "levels", "boundaries"}
            or value["mode"] != "pitch_fill" or value["spec_sha256"] != spec["review"]["content_sha256"]):
        raise ValueError("Sequence response belongs to a different stimulus")
    chars, groups = spec["characters"], value["groups"]
    _spans(groups, len(chars), 256, "entered mora groups")
    cursor = _skip(chars, 0)
    for group in groups:
        mora = "".join(chars[group["start"]:group["end"]])
        if group["start"] != cursor or len(mora) > 2 or not _KANA.fullmatch(mora):
            raise ValueError("Sequence response changes the supplied reading")
        cursor = _skip(chars, group["end"])
    if (not isinstance(value["levels"], list) or len(value["levels"]) != len(groups)
            or any(level not in ("H", "L") for level in value["levels"])):
        raise ValueError("Invalid sequence response levels")
    boundaries = value["boundaries"]
    edges = {group["end"] for group in groups}
    if (not isinstance(boundaries, list) or len(boundaries) > 256
            or any(type(b) is not int or not 0 < b < len(chars) or b not in edges
                   or chars[b] in _PUNCT or chars[b - 1] in _PUNCT for b in boundaries)
            or boundaries != sorted(set(boundaries))):
        raise ValueError("Invalid sequence response boundaries")
    if spec["task_mode"] == "pitch" and boundaries != [b for b in _boundaries(spec["tokens"], chars) if b <= cursor]:
        raise ValueError("Given sequence boundaries cannot be edited")
    return {**value, "cursor": cursor, "complete": cursor == len(chars)}


def _entered_tokens(chars, boundaries):
    # These are learner-defined color spans, not guessed grammatical categories.
    cuts = {0, len(chars), *boundaries}
    for index, char in enumerate(chars):
        if char in _PUNCT:
            cuts.update((index, index + 1))
    ordered = sorted(cuts)
    return [{"id": "entered:" + str(i), "start": start, "end": end,
             "role": "punctuation" if all(c in _PUNCT for c in chars[start:end]) else "word"}
            for i, (start, end) in enumerate(zip(ordered, ordered[1:]))]


def fixed_view(question, draft=None, *, answer=None, reveal=False):
    if draft is not None and answer is not None:
        raise ValueError("Choose a draft or a frozen response")
    spec = specification(question)
    chars = spec["characters"]
    cursor, complete, groups, entered_levels, boundaries = _skip(chars, 0), False, [], [], []
    if draft is not None:
        _, _, value, _ = _state(question, draft)
        cursor, complete = value["cursor"], value["complete"]
        groups, entered_levels, boundaries = value["groups"], value["levels"], value["boundaries"]
    elif answer:
        value = _decode(spec, answer)
        cursor, complete = None, value["complete"]
        groups, entered_levels, boundaries = value["groups"], value["levels"], value["boundaries"]
    if reveal:
        groups = spec["mora_spans"]
        entered_levels = _patterns(spec)[spec["selected_pattern_id"]]["levels"]
        boundaries = _boundaries(spec["tokens"], chars)
        cursor = None
    levels = [None] * len(chars)
    for group, level in zip(groups, entered_levels):
        levels[group["start"]:group["end"]] = [level] * (group["end"] - group["start"])
    if not spec["learner_morae"] and (not answer or groups == spec["mora_spans"][:len(groups)]):
        groups = spec["mora_spans"]
    tokens = spec["tokens"] if reveal or spec["task_mode"] == "pitch" else _entered_tokens(chars, boundaries)
    active = None
    if cursor is not None and cursor < len(chars):
        end = cursor + 1 if spec["learner_morae"] else next(span["end"] for span in spec["mora_spans"] if span["start"] == cursor)
        active = {"start": cursor, "end": end}
    return {"characters": list(chars), "levels": levels, "groups": deepcopy(groups),
            "tokens": deepcopy(tokens), "wrap_units": deepcopy(spec["wrap_units"]),
            "cursor": cursor, "active_span": active, "complete": complete, "label": spec["text"]}


def display_data(question, answer=None, *, reveal=False):
    return fixed_view(question, answer=answer, reveal=reveal)


def state_view(question, draft):
    spec, state, value, answer = _state(question, draft)
    return {**value, "answer": answer, "can_undo": bool(state["actions"]),
        "remaining": "".join(spec["characters"][value["cursor"]:]),
        "next_text": "".join(spec["characters"][value["cursor"]:]),
        "display": fixed_view(question, draft), "task_mode": spec["task_mode"], "version": 2}


def grade_answer(question, answer):
    validate_snapshot(question)
    if answer is None or answer == "":
        return {"status": "empty", "correct": None, "message": "请先作答。", "scorable": False}
    spec = specification(question)
    try:
        value = _decode(spec, answer)
    except ValueError:
        return {"status": "invalid", "correct": None, "message": "这份句子音高作答无法读取，请重新作答。", "scorable": False}
    if not value["complete"]:
        return {"status": "empty", "correct": None, "message": "请完成整句再检查。", "scorable": False}
    if value["groups"] != spec["mora_spans"]:
        group = next((expected for actual, expected in zip(value["groups"], spec["mora_spans"])
                      if actual != expected), spec["mora_spans"][-1])
        text = "".join(spec["characters"][group["start"]:group["end"]])
        return {"status": "incorrect", "correct": False, "scorable": True, "pitch_errors": ["morae"],
                "message": "先检查分拍：「" + text + ("」合为一拍。" if len(text) > 1 else "」单独占一拍。")}
    segments = {row["id"]: row for row in spec["segmentations"]}
    patterns = _patterns(spec)
    permitted = []
    for identity in spec["accepted_pattern_ids"]:
        pattern = patterns[identity]
        for segment_id in pattern["segmentation_ids"]:
            segment = segments[segment_id]
            if spec["task_mode"] == "pitch" or value["boundaries"] == _boundaries(segment["tokens"], spec["characters"]):
                permitted.append(pattern)
                if value["levels"] == pattern["levels"]:
                    return {"status": "correct", "correct": True, "scorable": True,
                            "message": question.get("feedback", ""), "matched_pattern_id": identity,
                            "matched_segmentation_id": segment_id}
    if not permitted:
        chosen = segments[patterns[spec["selected_pattern_id"]]["segmentation_ids"][0]]
        return {"status": "incorrect", "correct": False, "scorable": True, "pitch_errors": ["boundaries"],
                "message": chosen["explanation_zh"]}
    expected = min(permitted, key=lambda pattern: sum(a != b for a, b in zip(value["levels"], pattern["levels"])))
    index = next(i for i, (actual, wanted) in enumerate(zip(value["levels"], expected["levels"])) if actual != wanted)
    span = spec["mora_spans"][index]
    mora = "".join(spec["characters"][span["start"]:span["end"]])
    return {"status": "incorrect", "correct": False, "scorable": True, "pitch_errors": ["levels"],
            "message": f"按本句给定语境，第 {index + 1} 拍「{mora}」应标" + ("高。" if expected["levels"][index] == "H" else "低。")}


def answer_label(question, answer=None):
    spec = specification(question)
    value = _decode(spec, canonical_answer(question) if answer is None else answer)
    parts = []
    for group, level in zip(value["groups"], value["levels"]):
        if group["start"] in value["boundaries"]:
            parts.append("／")
        parts.append("".join(spec["characters"][group["start"]:group["end"]]) + (" 高" if level == "H" else " 低"))
    return " · ".join(parts)
