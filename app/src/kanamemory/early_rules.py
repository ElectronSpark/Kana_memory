"""Rule lessons a question may show before they are taught, at the very start of learning.

Design 11 section 5, D8. Pure rules: no Toga, learner database, platform or app import.

The dependency gate (learning_practice_policy) serves a template only when everything it
declares is known. At the very start that holds back natural practice words and sentences
for one small rule taught a few lessons later (きんぎょ under 音拍 needs 拨音; 私の友達 needs の).
While the learner is in the OPENING, a template whose only unmet requirements are rule
lessons that may appear early counts as met, and the page explains each of them in a hint
the learner can open (question_support.unmet_rule_hints).

Opening: at most EARLY_LESSON_LIMIT lessons introduced (the gate's known set without kana
ids, the target included). Later the gate is as strict as before.

A requirement may appear early when it is a rule lesson (a grammar-family lesson, the
pronunciation rules included) that has a rule text to show, and it is
- simple: its level is the entry level of its track (ENTRY_LEVELS: grammar 入门,
  pronunciation 基础), or
- without bearing on the task: a pronunciation rule in a task that neither belongs to a
  pronunciation lesson nor tests a reading or a sound (question_readings.READING_SENSITIVE_KINDS);
and the task does not ask for the rule itself, nor would the hint show its answer: no
Japanese form named in the rule's title is the answer, part of it, or contains it (a sentence
to be written with の is still held back), and no answer stands in the hint's title or lines.

Never: a word, a kana, an out-of-vocabulary token, a lesson without a rule text, more than
EARLY_RULE_LIMIT rules in one template. Selection and display only: snapshots, drafts,
grading, evidence and due dates do not know about this module.
"""
from __future__ import annotations

from collections.abc import Mapping
import re

EARLY_LESSON_LIMIT = 80
EARLY_RULE_LIMIT = 2
ENTRY_LEVELS = {"grammar": frozenset({"入门"}), "pronunciation": frozenset({"基础"})}
_KANA_ID = "kana:"
_HINT_LINE_LIMIT = 200
# A Japanese form in a rule title: a run of kana and kanji that holds at least one kana
# (の, この, です, ある; 名词句 and 相对位置 are Chinese labels, not forms).
_FORM = re.compile(r"[ぁ-ゖァ-ヺー㐀-䶿一-鿿々]+")
_KANA = re.compile(r"[ぁ-ゖァ-ヺ]")


def _lesson(rule_id):
    from .learning_content import get_learning_item
    return get_learning_item(rule_id) if isinstance(rule_id, str) and rule_id else None


def rule_track(lesson):
    """"pronunciation" or "grammar" for a rule lesson, else None (a word, an unknown id)."""
    if not isinstance(lesson, Mapping) or lesson.get("family") != "grammar":
        return None
    return "pronunciation" if lesson.get("domain") == "pronunciation" else "grammar"


def in_opening(known_ids):
    """The learner is at the very start: at most EARLY_LESSON_LIMIT lessons introduced (kana not counted)."""
    return sum(1 for cid in known_ids if counts_in_opening(cid)) <= EARLY_LESSON_LIMIT


def counts_in_opening(cid):
    """One member of a known set that in_opening counts: a lesson id, not a kana."""
    return isinstance(cid, str) and not cid.startswith(_KANA_ID)


def _hint_of(rule_id, lesson):
    if rule_track(lesson) is None:
        return None
    title = lesson.get("title")
    instruction = lesson.get("instruction") if isinstance(lesson.get("instruction"), Mapping) else {}
    lines = []
    for value in (instruction.get("summary") or lesson.get("meaning"), instruction.get("rule")):
        text = value.strip() if isinstance(value, str) else ""
        if text and text not in lines and len(text) <= _HINT_LINE_LIMIT:
            lines.append(text)
    if not isinstance(title, str) or not title.strip() or not lines:
        return None
    return {"id": rule_id, "title": title.strip(), "lines": tuple(lines)}


def rule_hint(rule_id):
    """{"id", "title", "lines"} of a rule lesson (its title and one or two short lines of its rule), or None."""
    return _hint_of(rule_id, _lesson(rule_id))


def _forms(lesson):
    title = lesson.get("title") if isinstance(lesson.get("title"), str) else ""
    return [form for form in _FORM.findall(title) if _KANA.search(form)]


def asks_for(lesson, question):
    """The task asks for the rule itself, or the rule's hint would show the task's answer.

    True when an answer (the answer, an accepted answer, a multi-gap slot's answer) is,
    holds or lies inside a Japanese form the rule's title names (の, この, です), or stands
    anywhere in the text the hint shows: its title and lines (です in 形容词＋です, 下 in
    上・下・中など, a meaning that repeats the title's gloss).
    """
    if not isinstance(lesson, Mapping) or not isinstance(question, Mapping):
        return False
    forms = _forms(lesson)
    hint = _hint_of(lesson.get("id"), lesson)
    title = lesson.get("title")
    shown = (hint["title"], *hint["lines"]) if hint else ((title,) if isinstance(title, str) else ())
    accepted = question.get("accepted_answers")
    answers = [question.get("answer"), *(accepted if isinstance(accepted, (list, tuple)) else ())]
    cloze = question.get("multi_cloze")
    if isinstance(cloze, Mapping):
        answers.extend(slot.get("answer") for slot in cloze.get("slots") or () if isinstance(slot, Mapping))
    for answer in answers:
        text = answer.strip() if isinstance(answer, str) else ""
        if text and (any(form in text or text in form for form in forms) or any(text in part for part in shown)):
            return True
    return False


def may_appear_early(rule_id, *, target, kind, question=None):
    """One unmet requirement may be shown before its lesson (see the module text); the opening is not tested here."""
    lesson = _lesson(rule_id)
    track = rule_track(lesson)
    if track is None or _hint_of(rule_id, lesson) is None:
        return False
    if question is not None and asks_for(lesson, question):
        return False
    if lesson.get("level") in ENTRY_LEVELS[track]:
        return True
    if track != "pronunciation" or (isinstance(target, Mapping) and target.get("domain") == "pronunciation"):
        return False
    from .question_readings import READING_SENSITIVE_KINDS
    return kind not in READING_SENSITIVE_KINDS


def early_rules(unmet, known_ids, *, target, kind, question=None, opening=None):
    """The unmet requirements, sorted, when the template may be served in the opening; else ().

    unmet: the template's declared requirements the known set lacks (never empty for a
    served result). Everything must be a rule lesson that may appear early.
    opening: in_opening(known_ids) when the caller has it (a planning pass counts it once); None counts here.
    """
    unmet = [cid for cid in unmet if isinstance(cid, str)] if isinstance(unmet, (set, frozenset, list, tuple)) else []
    if not 1 <= len(unmet) <= EARLY_RULE_LIMIT or not (in_opening(known_ids) if opening is None else opening):
        return ()
    if not all(may_appear_early(cid, target=target, kind=kind, question=question) for cid in unmet):
        return ()
    return tuple(sorted(unmet))
