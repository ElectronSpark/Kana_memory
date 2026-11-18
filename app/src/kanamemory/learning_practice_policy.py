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
    if mode in {"reorder", "multi_cloze"}:
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
    kind = question.get("kind")
    fields = (kind, question.get("input_mode", "choice"),
              normalized(question.get("prompt")), normalized(question.get("task")),
              normalized(question.get("answer")))
    if kind in {"vocab_audio_word", "vocab_reading_choice"}:
        # These share a stimulus by design; their options are the variation.
        fields = (*fields, sorted(normalized(choice) for choice in question.get("choices", ())))
    elif question.get("audio_text") and kind in _heard_stimulus_kinds():
        # Heard-meaning tasks share prompt and task: what is read aloud is the stimulus.
        # Every other kind, and a question without audio text, keeps its former digest
        # (history matching depends on it).
        fields = (*fields, normalized(question["audio_text"]))
    return sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def _heard_stimulus_kinds():
    """Registry kinds that need audio (LISTENING_KINDS); the heard-word task is told apart by its options."""
    from .learning_questions import LISTENING_KINDS
    return LISTENING_KINDS


def _descriptor(question):
    meta = practice_metadata(question)
    fingerprint = _fingerprint(question)
    return {"fingerprint": fingerprint, "stage": SCAFFOLDS.index(question_scaffold(question)),
            "family": meta["task_family"] if meta else question.get("kind", ""),
            "context": meta["context_id"] if meta else "legacy:" + fingerprint}


def question_context(question):
    """The context a guided/formal question practises (author id, else its content)."""
    return _descriptor(question)["context"]


def not_relaxed(descriptor, template=None):
    """admit filter for paths that never take the relaxed tier (design 11 section 5, D4).

    A check-only trial of new content (retention_check) measures the target
    alone, so an item x ability whose only templates have unmet requirements
    is skipped there, as before R1.
    """
    return not descriptor.get("relaxed")


# Design 11 section 18, Q6: a template is servable with glosses for at most this many unmet stem-only words.
STEM_GLOSS_LIMIT = 2


def review_count(state):
    """The scheduled reviews of the unit being planned (learning_unit.review_count; design 11 section 18, Q5).

    A caller that gives no count, or an unusable one (a bool, None, a negative number), plans as for a unit
    without reviews: such paths (guides, the intro quiz, checks, diagnostics, the tutor) see level 0 only.
    """
    count = state.get("review_count")
    return count if type(count) is int and count >= 0 else 0


def _opens_later(meta, reviews):
    """Q5: the template's validated practice.opens_after_reviews is above the unit's reviews so far."""
    return meta is not None and meta.get("opens_after_reviews", 0) > reviews


def _enrolled_reader(state):
    """The caller's enrolled ids for the stem-gloss tier (Q6), read at most once; None: the tier is off.

    Only a caller that sets state["gloss_allowance"] to True and gives state["enrolled_ids"], a callable
    returning the learner's enrolled content ids, has the tier. The ids are read the first time a template
    is otherwise eligible; content without stem facts never reads them. A reader returning anything but a
    collection of ids counts every word as enrolled (nothing is glossed).
    """
    reader = state.get("enrolled_ids") if state.get("gloss_allowance") is True else None
    if not callable(reader):
        return None
    cell = []

    def enrolled():
        if not cell:
            ids = reader()
            cell.append(frozenset(x for x in ids if isinstance(x, str))
                        if isinstance(ids, (list, tuple, set, frozenset)) else None)
        return cell[0]
    return enrolled


def _stem_gloss(unmet, stem_only, raw, enrolled):
    """Q6: what a template may be served with glossed (sorted word ids and bare oov surfaces), else ().

    Its unmet requirements number 1..STEM_GLOSS_LIMIT and are all glossed stem-only words of its entry
    (TemplateFacts.stem_only), none of them a word the learner has enrolled, and it is not a multi-gap
    question (a filled gap and its stem cannot be told apart). A rule, a kana or an unmarked word among
    them leaves the template to the relaxed pass as before.
    """
    from .learning_questions import OOV_TOKEN_PREFIX
    if (not 1 <= len(unmet) <= STEM_GLOSS_LIMIT or not unmet <= stem_only
            or raw.get("input_mode") == "multi_cloze" or "multi_cloze" in raw):
        return ()
    words = [cid for cid in unmet if not cid.startswith(OOV_TOKEN_PREFIX)]
    if words:
        ids = enrolled()
        if ids is None or not ids.isdisjoint(words):
            return ()
    return tuple(sorted(cid[len(OOV_TOKEN_PREFIX):] if cid.startswith(OOV_TOKEN_PREFIX) else cid
                        for cid in unmet))


def _candidate_questions(item, ability, kinds, state, admit=None, templates=None, shared=False):
    """Enabled, available, dependency-safe descriptors for one item x ability.

    Dependency gate (design 11 section 5, D3/D4): a template whose declared
    requirements (learning_questions.template_requires: reviewed background
    ids plus the question_dependencies entry) are all in the known set is a
    gated candidate. Templates declaring nothing are fallback candidates and
    are offered when no gated template qualifies, so an ability never
    starves on a bare word variant; a thin gated pass (fewer than two distinct
    stimuli, or a kind with fewer than two) is topped up with them (that kind's)
    after the gated ones, each carrying
    "top_up": True, so the learner does not repeat one task (guided paths skip
    top-ups and keep their own order). When neither pass yields anything, one
    allowed-field content_gap diagnostic is recorded and a third, relaxed pass
    (design 11 section 5, D4/D5, R1) keeps the item x ability from deadlocking:
    the templates with the fewest unmet requirements (one unmet = the 1T rule,
    else the smallest count available), each descriptor carrying
    "relaxed": <unmet count>. Selection only: evidence, assistance, snapshots
    and history are unchanged. Guided exposure paths (guides, the intro quiz)
    skip relaxed candidates and keep their own fallbacks. Nothing is served
    only when the item x ability has no usable template at all. Missing
    knowledge is not inferred from enrollment.

    Opening allowance (design 11 section 5, D8; early_rules): only when the
    caller sets state["early_allowance"] to True (the formal course path), a
    declared template whose unmet requirements are all rule lessons that may
    appear early is servable while the learner is in the opening. It is
    appended after everything the passes yield without it (met tasks and
    top-ups, or the fallbacks), carrying "early_rules": [the unmet rule ids]
    and never "relaxed"; when nothing is met it comes instead of the relaxed
    pass. Callers that do not set the flag (guides, the intro quiz, checks,
    the tutor) see the strict gate as before.

    Release by reviews (design 11 section 18, Q5): a template whose practice
    opens_after_reviews is above review_count(state) is no candidate at all:
    it yields nothing in any pass, adds nothing to the content gap and never
    enters the relaxed pass; every rule above is applied to the rest.

    Stem-gloss tier (design 11 section 18, Q6): only when the caller sets
    state["gloss_allowance"] to True and gives state["enrolled_ids"] (the
    formal course path; see _enrolled_reader), a declared template the opening
    allowance did not take, whose one or two unmet requirements are glossed
    stem-only words the learner has not enrolled (_stem_gloss), is servable
    with "stem_gloss": [word ids, bare oov surfaces] and never "relaxed". It
    is placed like the opening allowance: after everything else that pass
    yields (after the early tasks too), deduplicated by stimulus against all
    of them, and instead of the relaxed pass when nothing is met.

    Generated sound kinds (WP-K2) take their variants from sound_variants: with
    three or more known candidates their options are known words only (an
    encoded variant number); with fewer, the reading-choice kind keeps its
    bank variants (kana options) and the heard-word kind offers nothing yet
    (its options are written words; it waits for three known ones). They
    require nothing beyond their target, so the gate never removes them.

    admit: optional caller rule, admit(descriptor, template) -> bool, where
    template is the raw template (with its kind) and a relaxed descriptor
    already carries "relaxed". It runs on every descriptor before a pass is
    chosen, so a stricter caller still gets the next pass it can serve
    instead of an empty one. A content_gap is recorded only when the content
    itself has no met template, never because a caller refused one.

    shared=True: ``templates`` is practice_templates(item), already built by
    the caller for this very lesson (course_planning plans every ability of a
    lesson from one table); it is read here, never changed.
    """
    from . import learning_questions as questions
    if shared:
        allowed = set(questions.question_kinds(item, ability=ability, templates=templates))
    else:
        allowed = set(questions.question_kinds(item, ability=ability))
    enabled = list(dict.fromkeys(k for k in kinds if isinstance(k, str) and k in allowed))
    # The provider may append independently reviewed practice templates without
    # rewriting a stored lesson or moving its original variant indices.
    if not shared:
        provider = getattr(questions, "practice_templates", None)
        templates = provider(item) if provider else item.get("question_templates")
    retired = questions.retired_practice_variants(item, templates)
    known = state.get("known_background_ids", ())
    known = {x for x in known if isinstance(x, str)} if isinstance(known, (list, tuple, set, frozenset)) else set()
    known.add(item["id"])
    gated, early_gated, gloss_gated, fallback, relaxed, missing = [], [], [], [], [], set()
    met_any = False
    seen = {True: set(), False: set()}
    early_allowance = state.get("early_allowance") is True
    reviews, enrolled = review_count(state), _enrolled_reader(state)
    for kind in enabled:
        variants = templates.get(kind, []) if isinstance(templates, dict) else None
        sound = dict(questions.sound_variants(item, kind, known)) if kind in questions.SOUND_KINDS else None
        if sound is not None and not sound:
            # No variant for this learner yet (sound_variants), or no sound task for this word at all: never
            # the bank's unknown words instead, also for a lesson without a template table (variants is None).
            continue
        numbers = (list(sound) if sound and (variants is None or variants)
                   else range(len(variants) if variants is not None else 4))
        for variant in numbers:
            if (kind, variant) in retired:
                continue
            if variants is None:
                try:
                    raw = questions.build_learning_question(item, kind, variant=variant)
                except (ValueError, KeyError, TypeError):
                    continue
            else:
                raw = sound[variant] if sound else variants[variant]
                if not isinstance(raw, dict):
                    continue
            # The dependency entry is keyed by the raw template as stored.
            requires, stem_only = questions.template_facts(item, kind, raw)
            raw = dict(raw, kind=kind)
            from .answer_input import available
            if not available(dict(raw, family=item.get("family"), domain=item.get("domain")),
                             system_japanese=state.get("system_japanese_input") is True):
                continue
            meta = practice_metadata(raw)
            if "practice" in raw and meta is None:
                continue  # Malformed metadata is not a legacy escape hatch.
            if _opens_later(meta, reviews):
                continue  # Q5: not yet released for this unit, so not a gap either.
            unmet = requires - known if requires is not None else frozenset()
            missing |= unmet
            if not isinstance(raw.get("prompt"), str) or not isinstance(raw.get("answer"), str):
                continue
            descriptor = _descriptor(raw)
            declared = requires is not None
            entry = {**descriptor, "kind": kind, "variant": variant,
                     "input_mode": raw.get("input_mode", "choice"),
                     "reviewed_practice": meta is not None, "fallback": not declared}
            early = gloss = ()
            if unmet and early_allowance:
                from .early_rules import early_rules
                early = early_rules(unmet, known, target=item, kind=kind, question=raw)
            if unmet and not early and enrolled is not None and stem_only:
                gloss = _stem_gloss(unmet, stem_only, raw, enrolled)
            if early:
                entry["early_rules"] = list(early)
            elif gloss:
                entry["stem_gloss"] = list(gloss)
            elif unmet:
                entry["relaxed"] = len(unmet)
            else:
                met_any = True
            if admit is not None and not admit(entry, raw):
                continue
            if unmet and not early and not gloss:
                relaxed.append(entry)
                continue
            if early or gloss:
                (early_gated if early else gloss_gated).append(entry)
                continue
            if descriptor["fingerprint"] in seen[declared]:
                continue
            seen[declared].add(descriptor["fingerprint"])
            (gated if declared else fallback).append(entry)
    # Opening allowance: purely additive. The passes below are computed from the fully met
    # templates exactly as without it; a task that shows a not yet taught rule (with its
    # hint) is appended after them and never hides, replaces or precedes a task that needs
    # no hint (nor turns a requirement-free fallback into an unneeded top-up). The stem-gloss
    # tier (Q6) is additive in the same way and comes after the early tasks.
    early_extra, early_seen = [], seen[True] | seen[False]
    for entry in (*early_gated, *gloss_gated):
        if entry["fingerprint"] not in early_seen:
            early_seen = early_seen | {entry["fingerprint"]}
            early_extra.append(entry)
    if gated or fallback:
        yield from gated
        stimuli = {candidate["fingerprint"] for candidate in gated}
        if not gated:
            yield from fallback
        else:
            # Design 11 section 5, D4: a thin pass is topped up with requirement-free
            # fallback tasks after the met ones: the whole pass when it has fewer than
            # two tasks, else each kind whose met tasks are fewer than two (so one
            # family is not pinned to a single example).
            by_kind = defaultdict(set)
            for candidate in gated:
                by_kind[candidate["kind"]].add(candidate["fingerprint"])
            thin = {kind for kind, prints in by_kind.items() if len(prints) < 2}
            yield from ({**candidate, "top_up": True} for candidate in fallback
                        if candidate["fingerprint"] not in stimuli
                        and (len(stimuli) < 2 or candidate["kind"] in thin))
        yield from early_extra
        return
    if missing and not met_any:
        from .content_gaps import record_gap
        record_gap(item["id"], ability, len(missing), enabled)
    if early_extra:
        # No fully met template: a simple rule with its hint, or a stem word with its gloss, is nearer
        # than the relaxed pass.
        yield from early_extra
        return
    if not relaxed:
        return
    # Fewest unmet first; duplicates are dropped only within that tier so a
    # lower-count copy of a stimulus is never hidden by a higher-count one.
    least = min(candidate["relaxed"] for candidate in relaxed)
    tier = set()
    for candidate in relaxed:
        if candidate["relaxed"] == least and candidate["fingerprint"] not in tier:
            tier.add(candidate["fingerprint"])
            yield candidate


def practice_candidates(item, kinds, *, known_background_ids=(), system_japanese=False, availability=None,
                        admit=None):
    """Public read-only descriptors shared by course, tutor and retention paths.

    admit: the optional caller rule of _candidate_questions (for example
    not_relaxed); None keeps every pass.

    availability: the caller's content_availability.AvailabilityView (design 08
    section 8.2). Content it does not serve (disabled/missing library,
    blocked dependency, retired) yields no candidate; None checks nothing.
    """
    if availability is not None and not availability.available(
            item["id"], {"dictionary_only": item.get("dictionary_only") is True}):
        return
    from .learning_questions import KIND_ABILITIES
    state = {"known_background_ids": known_background_ids,
             "system_japanese_input": system_japanese}
    for ability in dict.fromkeys(KIND_ABILITIES[k] for k in kinds if k in KIND_ABILITIES):
        yield from _candidate_questions(item, ability, kinds, state, **({} if admit is None else {"admit": admit}))


def select_practice_plan(item, ability, kinds, state, history, *, templates=None, shared=False):
    """Choose one enabled, background-safe task, or None when none is available.

    History is oldest to newest, as returned by LearningStore.practice_history.
    The caller may add known_background_ids to the unit state. Missing knowledge
    is not inferred from enrollment; unknown extra targets remain excluded.
    A row is recognised as a candidate's stimulus by _fingerprint: a heard-meaning
    row needs its audio_text and a sound-kind row its choices for that.
    shared / templates: see _candidate_questions.
    """
    state = state or {}
    candidates = list(_candidate_questions(item, ability, kinds, state, templates=templates, shared=shared))
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
                # All else equal, a task without a not yet taught rule (design 11, D8) or a glossed
                # stem word (section 18, Q6) comes first.
                bool(candidate.get("early_rules") or candidate.get("stem_gloss")),
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
            or q["input_mode"] not in {"choice", "text", "reorder", "multi_cloze"}):
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
    from .multi_cloze import validate_snapshot, validate_draft
    validate_snapshot(q)
    validate_draft(q, draft)
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
            answer = checkpoint.get("guide_answer")

            def judged(**options):
                return answer is not None and grade_learning_answer(q, answer, **options).get("status") == "correct"
            # A result stored by the judge before 2026-11-18 (without reading forms) stays valid: it may
            # equal either judgement. Nothing stored changes.
            if checkpoint["guide_correct"] != judged() and checkpoint["guide_correct"] != judged(reading_forms=False):
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
            # Guided exposure never shows unmet material (relaxed is for
            # formal selection only; design 11 section 5, D4) and keeps its
            # order without the formal top-up of a thin gated pass. Its state
            # sets neither allowance, so no early or stem-gloss task comes here.
            if candidate["reviewed_practice"] and not candidate.get("relaxed") and not candidate.get("top_up"):
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
