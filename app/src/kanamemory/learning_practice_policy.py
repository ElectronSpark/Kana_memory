"""Bounded, deterministic selection of the next unsaved course question.

This is a teaching policy, not a mastery score or a second scheduler. It reads
only the enabled candidate set and at most 24 completed observations. Existing
questions, drafts and due dates are never inputs that this module can mutate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
import json
import unicodedata

HISTORY_LIMIT = 24
SCAFFOLDS = ("recognition", "cued", "retrieval")
_TEXT_MODES = {"text", "typing", "free_text"}


def practice_metadata(question):
    """Return usable author metadata, never promote a supplied choice to recall."""
    from .learning_questions import validate_practice
    try:
        return validate_practice(question, target_id=question.get("target_id"))
    except (ValueError, TypeError, KeyError):
        return None


def question_scaffold(question):
    """Describe evidence from the actual control, with conservative legacy fallbacks."""
    mode = question.get("input_mode", question.get("response", "choice"))
    if not isinstance(mode, str):
        return "cued"
    meta = practice_metadata(question)
    if mode == "choice":
        return "recognition"
    if mode == "reorder":
        return "cued"
    if mode in _TEXT_MODES:
        if meta:
            return meta["scaffold"]
        # Stored legacy text tasks already own the free-retrieval probe
        # contract. Only new explicit metadata may distinguish cued text;
        # reclassifying old transform kinds would postpone their existing probe.
        return "retrieval"
    # Pitch actions are specific applications of a given model, not proof of
    # independent sentence/word production. Their own grading stays unchanged.
    return "cued"


def _fingerprint(question):
    def normalized(value):
        return "".join(unicodedata.normalize("NFKC", str(value or "")).split())
    fields = (question.get("kind"), question.get("input_mode", "choice"),
              normalized(question.get("prompt")), normalized(question.get("task")),
              normalized(question.get("answer")))
    return sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def _descriptor(question):
    meta = practice_metadata(question)
    fingerprint = _fingerprint(question)
    return {"fingerprint": fingerprint, "stage": SCAFFOLDS.index(question_scaffold(question)),
            "family": meta["task_family"] if meta else question.get("kind", ""),
            "context": meta["context_id"] if meta else "legacy:" + fingerprint}


def _candidate_questions(item, ability, kinds, state):
    from . import learning_questions as questions
    allowed = set(questions.question_kinds(item, ability=ability))
    enabled = list(dict.fromkeys(k for k in kinds if isinstance(k, str) and k in allowed))
    # The provider may append independently reviewed practice templates without
    # rewriting a stored lesson or moving its original variant indices.
    provider = getattr(questions, "practice_templates", None)
    templates = provider(item) if provider else item.get("question_templates")
    known = state.get("known_background_ids", ())
    known = {x for x in known if isinstance(x, str)} if isinstance(known, (list, tuple, set, frozenset)) else set()
    known.add(item["id"])
    seen = set()
    for kind in enabled:
        variants = templates.get(kind, []) if isinstance(templates, dict) else None
        for variant in range(len(variants) if variants is not None else 4):
            if variants is None:
                try:
                    raw = questions.build_learning_question(item, kind, variant=variant)
                except (ValueError, KeyError, TypeError):
                    continue
            else:
                raw = variants[variant]
                if not isinstance(raw, dict):
                    continue
            raw = dict(raw, kind=kind)
            from .answer_input import available
            if not available(dict(raw, family=item.get("family"), domain=item.get("domain")),
                             system_japanese=state.get("system_japanese_input") is True):
                continue
            meta = practice_metadata(raw)
            if "practice" in raw and (meta is None or not set(meta["background_ids"]) <= known):
                continue
            if not isinstance(raw.get("prompt"), str) or not isinstance(raw.get("answer"), str):
                continue
            descriptor = _descriptor(raw)
            if descriptor["fingerprint"] in seen:
                continue
            seen.add(descriptor["fingerprint"])
            yield {**descriptor, "kind": kind, "variant": variant,
                   "input_mode": raw.get("input_mode", "choice"),
                   "reviewed_practice": meta is not None}


def select_practice_plan(item, ability, kinds, state, history):
    """Choose one enabled, background-safe task, or None when none is available.

    History is oldest to newest, as returned by LearningStore.practice_history.
    The caller may add known_background_ids to the unit state. Missing knowledge
    is not inferred from enrollment; unknown extra targets remain excluded.
    """
    state = state or {}
    candidates = list(_candidate_questions(item, ability, kinds, state))
    if not candidates:
        return None
    bank_size = len(candidates)
    observations = []
    for row in list(history or ())[-HISTORY_LIMIT:]:
        if not isinstance(row, dict) or not isinstance(row.get("question"), dict):
            continue
        q = row["question"]
        if (q.get("target_id") != item["id"] or q.get("ability") != ability
                or str(q.get("revision")) != str(item["revision"])):
            continue
        observations.append({**row, **_descriptor(q)})
    assessed = [r for r in observations if r.get("role") == "primary" and r.get("scheduled") is True
                and r.get("outcome") in {"correct", "incorrect", "revealed"}]
    latest = assessed[-1] if assessed else None
    support = bool(latest and (latest.get("assisted") or latest["outcome"] != "correct"))
    successes = 0
    for row in reversed(assessed):
        if row.get("assisted") or row["outcome"] != "correct" or row.get("correct") is not True:
            break
        successes += 1
    desired = max(0, latest["stage"] - 1) if support else min(2, successes)

    # Two independent recognition/cued successes do not certify production.
    # Reserve the next *available* retrieval task; unavailable/disabled modes
    # never create a penalty, a phantom question, or a changed due date.
    recent_successes = [r for r in assessed[-4:]
                        if r["outcome"] == "correct" and r.get("correct") is True and not r.get("assisted")]
    retrieval = [c for c in candidates if c["stage"] == 2 and c["input_mode"] in _TEXT_MODES]
    probe = state.get("probe_due_at_utc")
    due = state.get("due_at_utc")
    try:
        probe_due = bool(probe and due and datetime.fromisoformat(probe) <= datetime.fromisoformat(due))
    except (ValueError, TypeError):
        probe_due = False
    require_retrieval = bool(retrieval and not support and (
        probe_due or len(recent_successes) >= 2 and not any(r["stage"] == 2 for r in recent_successes)))
    if require_retrieval:
        candidates = retrieval
        desired = 2

    families = Counter(r["family"] for r in observations)
    contexts = Counter(r["context"] for r in observations)
    fingerprints = Counter(r["fingerprint"] for r in observations)
    recent_fingerprints = {r["fingerprint"] for r in observations[-3:]}
    recent_contexts = {r["context"] for r in observations[-2:]}
    # Once per four completions prefer an underused task family. Error followup
    # is bounded to its own slot and a changed example, not endless redrilling.
    count = state.get("attempt_count", len(observations))
    count = count if type(count) is int and count >= 0 else len(observations)
    coverage = count % 4 == 3 and not support and not require_retrieval
    targeted = count % 4 == 1
    errors = {r["family"] for r in assessed[-4:] if r["outcome"] == "incorrect" and not r.get("assisted")}

    # Recent evidence is deliberately bounded. Sorting every remaining tie by
    # the lowest variant would therefore recycle the first ~25 contexts forever
    # and starve later appended practice. Rotate only this final tie using the
    # existing completed-attempt count; no new progress state or queue mutation
    # is needed, and restarting a pending attempt cannot advance the rotation.
    # Shift one position after each bank-length cycle as well. Otherwise a
    # family offered only every fourth completion could remain locked to the
    # same modulo-four positions when the bank length is divisible by four.
    # Each family/control has its own ring, so other kinds' contiguous template
    # blocks do not bias its coverage slot back towards its first variant.
    buckets = defaultdict(list)
    for index, candidate in enumerate(candidates):
        buckets[candidate["family"], candidate["stage"], candidate["input_mode"]].append(index)
    rotating_rank = {}
    for indexes in buckets.values():
        rotation = (count + count // bank_size) % len(indexes)
        for position, index in enumerate(indexes):
            rotating_rank[index] = (position - rotation) % len(indexes)

    def key(candidate, index):
        stage_gap = abs(candidate["stage"] - desired)
        recent = candidate["fingerprint"] in recent_fingerprints
        family_count = min(4, families[candidate["family"]])
        # A just-failed unfamiliar task first gets a usable support level.
        # Otherwise prefer a fresh task before repeating the same stimulus.
        first = (stage_gap, recent) if support else (recent, 0 if coverage else stage_gap)
        return (*first, family_count if coverage else 0,
                candidate["context"] in recent_contexts,
                -int(targeted and candidate["family"] in errors),
                family_count, min(4, contexts[candidate["context"]]),
                min(4, fingerprints[candidate["fingerprint"]]),
                stage_gap, rotating_rank[index])

    chosen = min(enumerate(candidates), key=lambda pair: key(pair[1], pair[0]))[1]
    return {name: chosen[name] for name in ("kind", "variant", "input_mode")}


def validate_practice_guide(content_id, revision, checkpoint, *, item=None, validate_result=True):
    """Check the new generic frozen guide extension; leave legacy guides alone."""
    q = checkpoint.get("guide_question")
    sequence = checkpoint.get("guide_sequence")
    source_item = checkpoint.get("guide_source_item")
    if source_item is not None:
        if (not isinstance(source_item, dict) or source_item.get("id") != content_id
                or source_item.get("revision") != revision
                or any(key in source_item for key in ("units", "checkpoint", "stage", "introduction_id",
                                                       "due_at_utc", "retrieval_at_utc", "introduced_at_utc"))):
            raise ValueError("Invalid frozen guide source")
        item = source_item
    if sequence is not None:
        index = checkpoint.get("guide_index")
        completed = checkpoint.get("guide_completed_ids")
        if (not isinstance(sequence, list) or len(sequence) != 2
                or any(not isinstance(value, dict) for value in sequence)
                or type(index) is not int or index not in (0, 1)
                or not isinstance(completed, list)
                or any(not isinstance(value, str) for value in completed)
                or q != sequence[index]):
            raise ValueError("Invalid practice guide sequence")
        ids = [value.get("id") for value in sequence]
        if (any(not isinstance(value, str) for value in ids) or len(set(ids)) != 2
                or completed not in (ids[:index], ids if index == 1 else ids[:index])):
            raise ValueError("Invalid completed practice guides")
        for value in sequence:
            validate_practice_guide(content_id, revision, {"guide_question": value,
                **({"guide_source_item": source_item} if source_item is not None else {})}, item=item)
        sources = [value.get("practice_source", {}).get("item_fingerprint") for value in sequence]
        if sources[0] != sources[1]:
            raise ValueError("Practice guides belong to different lesson sources")
        if sequence[0]["practice"]["context_id"] == sequence[1]["practice"]["context_id"]:
            raise ValueError("Practice guides require different contexts")
    if not isinstance(q, dict) or "practice" not in q:
        if sequence is not None or source_item is not None or isinstance(q, dict) and "practice_source" in q:
            raise ValueError("Practice sequence lacks reviewed guides")
        return
    from .learning_questions import KIND_ABILITIES
    if (practice_metadata(q) is None or q.get("target_id") != content_id
            or q.get("revision") != revision or q.get("guided") is not True
            or q.get("evidence_mode") != "exposure_only"
            or not isinstance(q.get("kind"), str) or q["kind"] not in KIND_ABILITIES
            or q.get("ability") != KIND_ABILITIES[q["kind"]]
            or not isinstance(q.get("prompt"), str) or not isinstance(q.get("answer"), str)
            or not isinstance(q.get("input_mode"), str)
            or q["input_mode"] not in {"choice", "text", "reorder"}):
        raise ValueError("Invalid frozen practice guide")
    if item is not None and sequence is None:
        from .learning_questions import build_learning_question
        variant = q.get("variant")
        if type(variant) is not int or variant < 0:
            raise ValueError("Invalid practice guide variant")
        try:
            if "practice_source" in q:
                from .learning_questions import rebuild_practice_question
                expected = rebuild_practice_question(q, item)
            else:
                expected = build_learning_question(item, q["kind"], variant=variant, guided=True)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("Practice guide is not in the reviewed lesson") from exc
        if q != expected:
            raise ValueError("Practice guide differs from its reviewed template")
    mode = q["input_mode"]
    choices = q.get("choices", [])
    if not isinstance(choices, list) or any(not isinstance(x, str) for x in choices):
        raise ValueError("Invalid practice guide choices")
    if mode == "choice" and q["answer"] not in choices:
        raise ValueError("Practice guide answer is absent")
    draft = checkpoint.get("guide_draft", {})
    if not isinstance(draft, dict) or not isinstance(draft.get("answer", ""), str):
        raise ValueError("Invalid practice guide draft")
    if "demonstration_open" in draft and type(draft["demonstration_open"]) is not bool:
        raise ValueError("Invalid practice guide demonstration state")
    if len(draft.get("answer", "")) > 10000:
        raise ValueError("Practice guide draft is too long")
    if mode == "choice" and draft.get("answer") and draft["answer"] not in choices:
        raise ValueError("Invalid practice guide selection")
    if mode == "reorder":
        used, parts = draft.get("used", []), draft.get("parts", [])
        if (not isinstance(used, list) or not isinstance(parts, list)
                or any(type(i) is not int or i < 0 or i >= len(choices) for i in used)
                or len(set(used)) != len(used) or parts != [choices[i] for i in used]
                or draft.get("answer", "") != "".join(parts)):
            raise ValueError("Invalid practice guide word order")
    if checkpoint.get("guide_answer") is not None:
        if (not isinstance(checkpoint["guide_answer"], str)
                or "answer" in draft and checkpoint["guide_answer"] != draft["answer"]):
            raise ValueError("Practice guide answer and draft differ")
    if "guide_correct" in checkpoint:
        if type(checkpoint["guide_correct"]) is not bool:
            raise ValueError("Invalid practice guide result")
        if validate_result:
            from .learning_questions import grade_learning_answer
            correct = (checkpoint.get("guide_answer") is not None
                       and grade_learning_answer(q, checkpoint["guide_answer"]).get("status") == "correct")
            if checkpoint["guide_correct"] != correct:
                raise ValueError("Practice guide result disagrees with its answer")


def guided_practice_sequence(item, kinds, *, known_background_ids=(), system_japanese=False):
    """Two reviewed examples for one introduction, not two scheduled reviews."""
    from .learning_questions import KIND_ABILITIES, LISTENING_KINDS, build_learning_question
    choices = []
    for ability in item.get("abilities", ()):
        allowed = [kind for kind in kinds if isinstance(kind, str)
                   and KIND_ABILITIES.get(kind) == ability and kind not in LISTENING_KINDS]
        for candidate in _candidate_questions(item, ability, allowed,
                                               {"known_background_ids": known_background_ids,
                                                "system_japanese_input": system_japanese is True}):
            if candidate["reviewed_practice"]:
                choices.append(candidate)
    choices.sort(key=lambda candidate: (candidate["stage"], candidate["variant"]))
    if not choices:
        return []
    first = choices[0]
    remaining = [candidate for candidate in choices if candidate["context"] != first["context"]]
    if not remaining:
        return []
    # Prefer a changed operation with less help, using another actual context.
    second = min(remaining, key=lambda candidate: (
        candidate["stage"] <= first["stage"],
        candidate["family"] == first["family"], candidate["stage"], candidate["variant"]))
    return [build_learning_question(item, candidate["kind"], variant=candidate["variant"], guided=True)
            for candidate in (first, second)]
