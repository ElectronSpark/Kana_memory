"""Authored word kinds answered by free text: the reading and the word itself.

Pure rules: no Toga, database, platform, network or content loading. This
module owns the kind-specific parts of vocab_reading and vocab_recall: the
legacy question built from a lesson's own fields (reviewed templates go
through the generic template path), the grading steps (reading normal form,
visible-reading guard, modern-kana spelling), the instruction cues and the
romaji composition style. The written-form check is shared with
vocab_orthography, whose choices are graded as written words too.
"""
from __future__ import annotations

from collections.abc import Mapping
import re

from ..kana_orthography import allowed_word_spellings, same_kana_sound
from ..question_kind_registry import KindGrader, KindPresentation

READING_KIND = "vocab_reading"
RECALL_KIND = "vocab_recall"
KINDS = (READING_KIND, RECALL_KIND)

# Instruction cues; the same literals are study text in question_presentation.
READING, BASE_FORM, WORD = "読み", "基本形", "単語"
# input_mode_policy results: the whole draft is a kana reading, or only its
# Latin runs are converted (public kanji, digits and kana stay as written).
ROMAJI_READING, ROMAJI_RUNS = "reading", "runs"


def _build_reading(item, base, env):
    form, reading, meaning = item["title"], item["reading"], item["meaning"]
    base.update(prompt=form, task=f"本课义项：{meaning}。写出日语假名读法。",
                answer=reading, accepted_answers=[reading],
                alternative_answers=list(dict.fromkeys([
                    *item.get("alternative_readings", []),
                    *[r for r in item.get("accepted_readings", [])
                      if env.reading_normal(r) != env.reading_normal(reading)]])),
                choices=[], input_mode="text", strict=True, safe=False)
    return base


def _build_recall(item, base, env):
    form, reading = item["title"], item["reading"]
    base.update(prompt=item["recall"], task="写出本课学的单词。",
                answer=form, accepted_answers=[form, reading], choices=[],
                input_mode="text", strict=False, known_wrong=item["wrong_forms"], safe=False)
    return base


_BUILDERS = {READING_KIND: _build_reading, RECALL_KIND: _build_recall}


def build(item: Mapping, spec, env, variant: int) -> dict:
    """Builder for a lesson without reviewed templates (legacy raw ignores variant)."""
    return _BUILDERS[spec.kind](item, env.legacy_word_base(item), env)


def _visible_reading(question, normal):
    if normal(question["prompt"]) == normal(question["answer"]):
        # Keep old pending snapshots intact, but copying a visible kana reading
        # cannot establish recall or change its stored review interval.
        return {"status": "unresolved", "correct": None,
                "message": "题目已显示假名读法，本题不计入复习。", "scorable": False}
    return None


def _reading_match(question, value, normal, accepted):
    target = normal(question["answer"])
    permitted = {normal(x) for x in allowed_word_spellings(question["answer"])}
    if value in permitted:
        return ({"status": "correct", "correct": True, "message": question.get("feedback", "正确。"),
                 "scorable": True, "accepted_spelling": value != target}, accepted)
    if same_kana_sound(target, value, fold_script=True):
        # This ability tracks recalling the reading. A same-sound letter
        # error does not establish forgetting that reading; preserve its
        # due date while keeping an explicit spelling diagnosis.
        return ({"status": "unresolved", "correct": None, "scorable": False,
                 "error_category": "spelling", "error_type": "orthography",
                 "reading_correct": True, "spelling_correct": False,
                 "message": "读音对应，但这个词的假名写法不同。本次不调整读音复习。"}, accepted)
    # Existing immutable snapshots may still include other dictionary
    # readings as accepted answers. Recognize them without awarding the
    # selected reading's ability, and never rewrite a saved draft/queue.
    alternatives = {normal(x) for x in question.get("alternative_answers", [])}
    alternatives.update(accepted - {target})
    if value != target and value in alternatives:
        return ({"status": "unresolved", "correct": None,
                 "message": "这是词典收录的其他读法，可能限于其他语境；未检验本课目标读法。本次不计入复习。",
                 "scorable": False}, accepted)
    return None, accepted


def written_form_match(question, value, normal, accepted):
    """Modern-kana spellings of a kana word; never another same-sound word."""
    target = normal(question["answer"])
    kana_targets = [candidate for candidate in accepted if same_kana_sound(candidate, candidate)]
    # Keep reviewed kanji/other expression alternatives, but never treat a
    # legacy same-sound spelling as permission to write a different word.
    if same_kana_sound(target, target):
        kana_targets = [target]
        accepted = {candidate for candidate in accepted
                    if not same_kana_sound(target, candidate, fold_script=True)}
    permitted = {normal(form) for candidate in kana_targets
                 for form in allowed_word_spellings(candidate)}
    accepted.update(permitted)
    if value in permitted:
        return ({"status": "correct", "correct": True, "message": question.get("feedback", "正确。"),
                 "scorable": True, "accepted_spelling": value not in kana_targets}, accepted)
    if any(same_kana_sound(candidate, value, fold_script=True) for candidate in kana_targets):
        return ({"status": "incorrect", "correct": False, "scorable": True,
                 "error_category": "spelling", "error_type": "orthography",
                 "reading_correct": True, "spelling_correct": False,
                 "message": "读音对应，但这个词的写法不同。"}, accepted)
    return None, accepted


READING_GRADER = KindGrader(
    normalizer="reading", visible_answer=_visible_reading, match=_reading_match,
    incorrect_fields=(("error_category", "reading"), ("reading_correct", False),
                      ("spelling_correct", False)))
WRITTEN_FORM_GRADER = KindGrader(match=written_form_match)


def _reading_sense_cue(task):
    # A sense can disambiguate homographs. Keep it verbatim, but remove
    # only the known trailing operation, not arbitrary Chinese sentences.
    match = re.fullmatch(r"本课义项：(.+)。(?:写出本课所学的假名读法|写出日语假名读法)。", task)
    return READING + " · " + match[1] if match else None


READING_PRESENTATION = KindPresentation(
    quoted_tasks=(r"只写句中「(?P<word>[^「」\n]+)」在本课中的假名读法，不必写整句。",
                  r"只写「(?P<word>[^「」\n]+)」在本课中的假名读法。"),
    quoted_cue=READING,
    focused_tasks=(("标记部分怎么读？", READING),),
    plain_tasks=(("写出假名读法。", READING), ("写出日语假名读法。", READING)),
    task_cue=_reading_sense_cue)
RECALL_PRESENTATION = KindPresentation(
    focused_tasks=(("标记部分的基本形是？", BASE_FORM),),
    plain_tasks=(("写出本课学的单词。", WORD), ("写出本课单词，用假名作答。", "単語 · かな"),
                 ("写出本课所学的单词基本形，可用本课汉字写法或假名。", "基本形 · 漢字 / かな")),
    folds_translation=True)


def reading_input(question):
    """input_mode_policy: a reading draft is converted as one kana string."""
    return ROMAJI_READING


def recall_input(question):
    """input_mode_policy: a word draft may mix kanji; convert Latin runs only."""
    return ROMAJI_RUNS
