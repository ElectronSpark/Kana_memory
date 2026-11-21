"""Small, read-only question cues derived solely from public authored fields.

Unknown instructions remain intact. This is neither a new question format nor
an annotation/speech authorization: answers never supply highlighting anchors.
A kind's form_cue may add only a form category of the canonical answer (a
copula gap's negation/tense), never answer text.

Known Chinese translation cues move to folded_hint (the text) under a short
Japanese cue. The screen shows that text directly under the cue unless fold_hint
is True; only then is it one tap away. The hint is display only: showing or
opening it changes no draft, assist flag or grade.
"""
from collections.abc import Mapping
import re

from .question_kind_registry import KindPresentation
from .question_kinds import REGISTRY
from .question_kinds.vocab_context import CONTEXT_CHOICE_TASK, CONTEXT_WORD_BANK_TASK


FILL_GAP, WORD = "穴埋め", "単語"
# Every kind's cues are its registered presenter (question_kinds/<family>);
# a kind without one keeps its task as written.
_GENERIC = KindPresentation()
# Chinese-free context word choice: one gap, or several gaps sharing a word bank.
CONTEXT_CHOICE, CONTEXT_WORD_BANK = CONTEXT_CHOICE_TASK, CONTEXT_WORD_BANK_TASK
# Known translation cues of folding kinds, whichever kind shows them.
_HINT_PATTERNS = (
    (r"补全句子：(?P<hint>.+)", FILL_GAP),
    (r"按给定意思补全句子：(?P<hint>.+)", FILL_GAP),
    (r"本课词：(?P<hint>.+)", WORD),
    (r"用本课表示“(?P<hint>.+)”的单词补全，用假名作答。", "かなで穴埋め"),
)
# Japanese study cues: every presenter's study cues and verbatim study tasks
# (Registry.study_cues), plus the folded-hint cues above. Pinned by
# test_question_kind_registry against its former literal value.
_JAPANESE_CUES = REGISTRY.study_cues() | {cue for _, cue in _HINT_PATTERNS}
# In these families the whole task is the translation of the sentence.
_TRANSLATION_TASK_FAMILIES = {"contextual_completion", "multi_gap", "dialogue_response"}


def _kind_presentation(kind):
    spec = REGISTRY.get(kind)
    return spec.presenter if spec is not None and spec.presenter is not None else _GENERIC


def _folded_translation(question, presentation, task):
    """(cue, hint) when a known cue is only a translation, otherwise None."""
    if not presentation.folds_translation or not task:
        return None
    for pattern, cue in _HINT_PATTERNS:
        match = re.fullmatch(pattern, task, re.DOTALL)
        if match:
            return cue, match["hint"]
    practice = question.get("practice")
    if isinstance(practice, Mapping) and practice.get("task_family") in _TRANSLATION_TASK_FAMILIES:
        return FILL_GAP, task
    return None


def hint_folds(question):
    """Whether the Chinese hint waits behind a button instead of being shown.

    Folding is for questions the content marks as answerable from the Japanese
    context alone. The content's marks will be read here; no content carries
    them yet, so every hint is shown.
    """
    return False


def _finish(result, question):
    result["instruction_role"] = "study" if result["instruction"] in _JAPANESE_CUES else "ui"
    result["fold_hint"] = bool(result["folded_hint"]) and hint_folds(question)
    return result


def question_presentation(question):
    """Keep a minimal operation cue, indispensable constraints and exact focus.

    Frozen legacy sentence-reading questions encode their target in a known
    public instruction. Only an unambiguous occurrence can replace that text.
    Valid explicit spans may identify one of several identical occurrences.
    """
    if not isinstance(question, Mapping):
        return {"instruction": "", "target_spans": [], "folded_hint": "", "fold_hint": False,
                "instruction_role": "ui"}
    task = question.get("task", "")
    task = task if isinstance(task, str) else ""
    kind, prompt = question.get("kind"), question.get("prompt", "")
    result = {"instruction": task, "target_spans": [], "folded_hint": "", "fold_hint": False,
              "instruction_role": "ui"}
    if not isinstance(prompt, str):
        return result
    public = (question.get("prompt_visible") is not False
              and not any(question.get(key) for key in ("audio_only", "has_hidden_transcript", "audio_text")))
    roles = question.get('text_roles')
    instruction_prompt = (isinstance(roles, Mapping) and roles.get('prompt') == 'ui'
        or _kind_presentation(kind).prompt_is_instruction
        or isinstance(question.get('practice'), Mapping)
        and question['practice'].get('task_family') == 'translation_wordbank')
    if public and question.get("practice") is not None:
        from .learning_questions import validate_practice
        try:
            practice = validate_practice(question, target_id=question.get("target_id"))
        except (ValueError, KeyError, TypeError):
            return _finish(result, question)
        # Translation stimuli and category labels are instructions. Their
        # publication anchors must not turn Chinese into Japanese study text.
        result["target_spans"] = practice["target_spans"] if practice and not instruction_prompt else []
    presentation = _kind_presentation(kind)
    for pattern in presentation.quoted_tasks:
        match = re.fullmatch(pattern, task)
        if not match:
            continue
        word = match["word"]
        spans = result["target_spans"]
        if public and not spans:
            positions = [m.start() for m in re.finditer("(?=" + re.escape(word) + ")", prompt)]
            if len(positions) == 1:
                start = positions[0]
                spans = [{"start": start, "end": start + len(word), "quote": word}]
        if spans and all(span["quote"] == word for span in spans):
            result.update(target_spans=spans, instruction=presentation.quoted_cue)
        return _finish(result, question)
    if (question.get("input_mode", "choice") == "choice"
            and task in {"选择最符合本题语境的答案。", "选择最符合语境的答案。"}):
        result["instruction"] = ""
        return _finish(result, question)
    focused, plain = dict(presentation.focused_tasks), dict(presentation.plain_tasks)
    if result["target_spans"] and task in focused:
        result["instruction"] = focused[task]
    elif task in plain:
        result["instruction"] = plain[task]
    elif presentation.task_cue is not None:
        cue = presentation.task_cue(task)
        if cue:
            result["instruction"] = cue
    folded = _folded_translation(question, presentation, task)
    if folded and result["instruction"] == task:
        result["instruction"], result["folded_hint"] = folded
    if presentation.form_cue is not None:
        cue = presentation.form_cue(question, result["instruction"])
        if cue:
            result["instruction"] = cue
    return _finish(result, question)


def question_instruction(question):
    return question_presentation(question)["instruction"]
