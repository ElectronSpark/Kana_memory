"""Probe-first start of a new course item: pure rules (design 02 section 4, design 03; 2026-11-21).

A new word, grammar or pronunciation-rule lesson is not introduced by a
self-rating any more. Each of its abilities, in a fixed order, starts with one
probe question: a definite, unguided question whose other material is already
known. A right, unaided answer is that ability's first formal review; a wrong,
revealed or assisted answer, or the learner's "还不会", sends the whole lesson
to teaching, and it is never probed again.

This module decides only from the values it is given: the order of the
abilities to probe, whether a question qualifies as a probe, the shape of the
profile a probe writes into the lesson's checkpoint, whether a lesson's
explanation has been shown, and the spacing between two answers of one
lesson. No Toga, database, platform or package import; the learning store owns
every read and transaction.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta

EVIDENCE = "probe_v1"
DECLINED_KEY = "probe_declined"
# Spacing (design 02 section 4, item 5): between two probes of one lesson, and
# between the end of its teaching and its first formal question.
OTHER_ANSWERS = 3
MAX_WAIT = timedelta(minutes=10)

_ORDER = {"word": ("understanding", "reading", "use"),
          "grammar": ("understanding", "application"),
          "pronunciation": ("understanding", "application")}
_KINDS = {("word", "understanding"): ("vocab_meaning",),
          ("word", "reading"): ("vocab_reading",),
          ("word", "use"): ("vocab_usage", "vocab_recall"),
          ("grammar", "understanding"): ("grammar_meaning",),
          ("grammar", "application"): ("grammar_cloze", "grammar_reorder", "grammar_transform"),
          ("pronunciation", "understanding"): ("pronunciation_rule",),
          ("pronunciation", "application"): ("pronunciation_application",)}
# A word's use is probed by a typed answer only: a choice among words would
# show the target's spelling among the options.
_TYPED_ONLY = {("word", "use")}
_CHOICE_MODES = frozenset({"choice", "reorder"})
_TYPED_MODES = frozenset({"text", "typing", "free_text"})
# Candidate marks of the dependency gate that loosen it (learning_practice_policy).
_LOOSENED = ("relaxed", "early_rules", "stem_gloss")
_KANA_CHARS = frozenset(chr(c) for c in (*range(0x3041, 0x3097), *range(0x30A1, 0x30FB))) | {"ー", "ゝ", "ゞ", "ヽ", "ヾ"}


def track(family: str | None, domain: str | None = None) -> str | None:
    """The probe track of a lesson: "word", "grammar" or "pronunciation"; None for anything else."""
    if domain == "pronunciation":
        return "pronunciation"
    return family if family in ("word", "grammar") else None


def order(track_name: str | None) -> tuple[str, ...]:
    """The abilities probed, in order. Listening is never probed."""
    return _ORDER.get(track_name or "", ())


def probe_kinds(track_name: str | None, ability: str) -> tuple[str, ...]:
    """The question kinds a probe of this ability may use, in preference order."""
    return _KINDS.get((track_name or "", ability), ())


def probed(track_name: str | None, ability: str) -> bool:
    return ability in order(track_name)


def first_ability(track_name: str | None, admitted: Iterable[str]) -> str | None:
    """The first ability in probe order among the admitted ones (None: nothing can be probed)."""
    admitted = set(admitted)
    return next((ability for ability in order(track_name) if ability in admitted), None)


def kana_text(value: object) -> bool:
    """A non-empty text of kana only (spaces ignored): an answer that can be typed in kana."""
    if not isinstance(value, str):
        return False
    text = "".join(value.split())
    return bool(text) and all(char in _KANA_CHARS for char in text)


def definite(question: Mapping, *, kana_answer: bool) -> bool:
    """One answer or answer set: a choice or block order, a pitch task, or a strict typed task answerable in kana.

    ``kana_answer``: the caller's finding that one accepted answer can be typed
    in kana (an all-kana accepted answer, or reading forms that apply).
    """
    mode = question.get("input_mode", question.get("response", "choice"))
    if mode in _CHOICE_MODES:
        return True
    if question.get("pitch_practice") or question.get("tests_pitch") is True:
        return True
    if mode == "multi_cloze":
        return question.get("strict") is True
    return mode in _TYPED_MODES and question.get("strict") is True and kana_answer


def accepts_kana(question: Mapping) -> bool:
    """Whether one of the question's own accepted answers is written in kana only."""
    answers = [question.get("answer"), *(question.get("accepted_answers") or ())]
    return any(kana_text(answer) for answer in answers)


def qualifies(question: Mapping, track_name: str | None, ability: str, *, kana_answer: bool,
              candidate: Mapping | None = None) -> bool:
    """Whether ``question`` may be the probe of ``ability``.

    ``candidate``: the dependency gate's descriptor it was built from, when
    there is one; a loosened tier (relaxed, an early rule, a glossed stem word)
    never makes a probe. The caller checks requirements against the known set
    and the assessment and input checks.
    """
    kind = question.get("kind")
    if kind not in probe_kinds(track_name, ability) or question.get("ability") != ability:
        return False
    if question.get("guided") is True or question.get("evidence_mode") == "exposure_only":
        return False
    if question.get("audio_text") or question.get("requires_audio") is True:
        return False
    if candidate is not None and any(candidate.get(mark) for mark in _LOOSENED):
        return False
    mode = question.get("input_mode", question.get("response", "choice"))
    if (track_name, ability) in _TYPED_ONLY and mode not in _TYPED_MODES:
        return False
    return definite(question, kana_answer=kana_answer)


PROBE_VIEW = "probe_view"


def bare_view(question: Mapping) -> dict:
    """A display copy of a probe question without reading, accent or audio aid (never stored or graded).

    The probe tests the item itself: its page shows the task and the prompt
    as plain text, so neither the item's reading nor a spoken prompt gives the
    answer of this probe or of the next one away.
    """
    view = dict(question)
    support = dict(question.get("support") or {})
    support.update(reading_safe=False, accent_safe=False, audio_safe=False, segments=[])
    # PROBE_VIEW (brief J6 R6): the page's cue drops the item's own meaning (読み alone), and the page draws
    # no gloss line, no gloss of the target itself and no option kana (question_presentation, learning_ui).
    view.update(support=support, spans=[], optional_audio_text="", readings={}, gloss="")
    view[PROBE_VIEW] = True
    return view


def probe_profile(ability: str, now: datetime) -> dict:
    """The profile a started probe writes: the shape of today's "familiar, test first", from a probe."""
    return {"version": 1, "evidence": EVIDENCE, "levels": {ability: "familiar"}, "route": "diagnostic",
            "selected_at_utc": now.isoformat(), "diagnostic_state": "active"}


def plain_profile(abilities: Iterable[str], now: datetime) -> dict:
    """The profile of a scope that is not probed: taught (stage intro) or opened for an ordinary first check."""
    return {"version": 1, "evidence": EVIDENCE, "levels": {ability: "new" for ability in abilities},
            "route": "full", "selected_at_utc": now.isoformat(), "diagnostic_state": "not_requested"}


def is_probe(profile: Mapping | None) -> bool:
    """A profile written by a probe (active, passed or failed)."""
    return isinstance(profile, Mapping) and profile.get("evidence") == EVIDENCE and (
        profile.get("route") == "diagnostic" or profile.get("diagnostic_state") == "needs_teaching")


def explanation_shown(checkpoints: Iterable[Mapping], *, batch_route: bool = False) -> bool:
    """Whether the lesson's explanation has been shown in any of its scopes (its "taught" state).

    Shown: an instruction exposure stamp, a guide that was opened, the batch
    cards of an earlier release, or an earlier self-report that chose teaching
    (new / some). A probe that failed but whose teaching has not been opened yet
    is not shown.
    """
    if batch_route:
        return True
    for checkpoint in checkpoints:
        if checkpoint.get("instruction_exposed_at_utc"):
            return True
        if any(key in checkpoint for key in ("guide_question", "guide_sequence", "guide_answer", "intro_batch")):
            return True
        profile = checkpoint.get("initial_familiarity") or {}
        if (profile.get("evidence") == "self_report" and profile.get("route") in ("full", "compact")
                and profile.get("diagnostic_state") == "not_requested"):
            return True
    return False


def teaching_pending(checkpoints: Iterable[Mapping]) -> bool:
    """Some scope failed its probe or diagnostic and waits for teaching."""
    return any((checkpoint.get("initial_familiarity") or {}).get("diagnostic_state") == "needs_teaching"
               for checkpoint in checkpoints)


def probing(checkpoints: Iterable[Mapping], *, batch_route: bool = False) -> bool:
    """A lesson whose next abilities still open as probes: never taught, and no probe failed."""
    checkpoints = list(checkpoints)
    return not explanation_shown(checkpoints, batch_route=batch_route) and not teaching_pending(checkpoints)


def spacing_ready(anchor: datetime | None, other_answers: int, now: datetime) -> bool:
    """Enough other work since ``anchor``: OTHER_ANSWERS answers of other items, or MAX_WAIT."""
    return anchor is None or other_answers >= OTHER_ANSWERS or now >= anchor + MAX_WAIT


def spacing_until(anchor: datetime) -> datetime:
    """The latest moment the spacing can hold an answer back."""
    return anchor + MAX_WAIT


def earlier_in_flight(track_name: str | None, ability: str, abilities: Mapping[str, Mapping]) -> bool:
    """An earlier probed ability of the same lesson has an open, unanswered probe.

    ``abilities``: per admitted ability {"active": its scope's profile is an
    active diagnostic, "rated": it has a formal rating}. An earlier ability
    without a qualifying probe yet (not opened) does not hold the later ones.
    """
    sequence = order(track_name)
    if ability not in sequence:
        return any(value.get("active") and not value.get("rated") for value in abilities.values())
    return any(abilities.get(earlier, {}).get("active") and not abilities.get(earlier, {}).get("rated")
               for earlier in sequence[:sequence.index(ability)])


def other_probe_candidates(track_name: str | None, abilities: Mapping[str, Mapping], answered: str,
                           *, probing_now: bool) -> tuple[str, ...]:
    """The other probed abilities of a lesson that could still be probed: not ``answered``, no formal rating.

    Empty when the lesson is not probing any more (taught, or a probe failed).
    Whether one of them has a qualifying probe at this moment is the store's
    question; ``reduced_result`` combines the two.
    """
    if not probing_now:
        return ()
    return tuple(ability for ability in order(track_name)
                 if ability in abilities and ability != answered and not abilities[ability].get("rated"))


def reduced_result(*, lesson_probing: bool, next_probe_available: bool) -> bool:
    """A right answer's result page is reduced (no reading, meaning or notes) only when BOTH hold:
    the lesson is still probing (never taught, no probe failed), and another ability of the
    same lesson than the answered one still has a probe that is open (active, unrated) or can
    be opened now (a qualifying probe exists for it). The answered question may be a probe or
    any later question of the lesson, such as a due review of an ability that passed its probe.
    In every other case the full result page is shown; a wrong or revealed answer always gets
    the full page (the caller asks only for a right one)."""
    return lesson_probing and next_probe_available
