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
from .question_kinds.vocab_text import READING as READING_CUE


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
)
KANA_FILL_GAP = "かなで穴埋め"
# The "word by its meaning" task family (brief J6 R9): its two frames, the meaning X in “ ”, ‘ ’ or " ".
_WORD_BY_MEANING = re.compile(
    r"(?:用本课表示(?:“(?P<xa>[^”]+)”|‘(?P<xb>[^’]+)’|\"(?P<xc>[^\"]+)\")的单词补全"
    r"|填入表示(?:“(?P<xd>[^”]+)”|‘(?P<xe>[^’]+)’|\"(?P<xf>[^\"]+)\")的本课单词)(?P<rest>.*)", re.DOTALL)
_KANA_INSTRUCTION, _FREE_INSTRUCTIONS = "用假名作答", ("可写汉字或假名", "可写假名或汉字")
_SEPARATORS, _OPEN, _CLOSE = "，,。；;", "（(「『", "）)」』"


def _rest_segments(rest):
    """The rest of a word-by-meaning task cut at top-level commas and full stops; None: unbalanced brackets."""
    segments, current, depth = [], [], 0
    for char in rest:
        if char in _OPEN:
            depth += 1
        elif char in _CLOSE:
            depth -= 1
            if depth < 0:
                return None
        if depth == 0 and char in _SEPARATORS:
            segments.append("".join(current))
            current = []
            continue
        current.append(char)
    if depth:
        return None
    segments.append("".join(current))
    return [segment.strip() for segment in segments if segment.strip()]


def _bracketed(segment):
    """The text inside a segment that is one outer （…） / (…) pair, else None."""
    if len(segment) < 2 or segment[0] not in "（(" or segment[-1] not in "）)":
        return None
    depth = 0
    for index, char in enumerate(segment):
        depth += char in _OPEN
        depth -= char in _CLOSE
        if depth == 0 and index < len(segment) - 1:
            return None
    return segment[1:-1].strip()


def word_by_meaning_task(task, *, choice):
    """(cue, line) of a "word by its meaning" task, or None when the task fits neither frame.

    The frames 「用本课表示 X 的单词补全…」 and 「填入表示 X 的本课单词…」 (X in “ ”, ‘ ’ or " "). The input
    instruction gives the cue and is not shown: 「用假名作答」 gives かなで穴埋め; 「可写汉字或假名」, no
    instruction or a choice question give 穴埋め. Everything else the line says limits the answer and is
    kept as a form hint: the line is X, then each hint, joined by 「 · 」, a hint that is one bracketed
    group without its outer brackets. Pure; reads no answer.
    """
    if not isinstance(task, str):
        return None
    match = _WORD_BY_MEANING.fullmatch(task.strip())
    if match is None:
        return None
    meaning = next(match[key] for key in ("xa", "xb", "xc", "xd", "xe", "xf") if match[key] is not None).strip()
    segments = _rest_segments(match["rest"])
    if segments is None or not meaning:
        return None
    kana, hints = False, []
    for segment in segments:
        # An instruction may carry a bracketed hint right after it: 「用假名作答（不用「きれい」）」.
        for instruction in (_KANA_INSTRUCTION, *_FREE_INSTRUCTIONS):
            if segment.startswith(instruction):
                kana = kana or instruction == _KANA_INSTRUCTION
                segment = segment[len(instruction):].strip()
                break
        if not segment:
            continue
        inner = _bracketed(segment)
        hints.append(inner if inner else segment)
    cue = FILL_GAP if choice or not kana else KANA_FILL_GAP
    return cue, " · ".join([meaning, *hints])


# Japanese study cues: every presenter's study cues and verbatim study tasks
# (Registry.study_cues), plus the folded-hint cues above. Pinned by
# test_question_kind_registry against its former literal value.
_JAPANESE_CUES = REGISTRY.study_cues() | {cue for _, cue in _HINT_PATTERNS} | {KANA_FILL_GAP}
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
    # A choice question: its stored input mode says so (a snapshot without one: it has options).
    mode = question.get("input_mode", question.get("response"))
    by_meaning = word_by_meaning_task(task, choice=mode == "choice" or mode is None and bool(question.get("choices")))
    if by_meaning is not None:
        return by_meaning
    practice = question.get("practice")
    if isinstance(practice, Mapping) and practice.get("task_family") in _TRANSLATION_TASK_FAMILIES:
        return FILL_GAP, task
    return None


def _policy_folds(question):
    """The effective answer policy's fold mark (the overlay row of the question's own template)."""
    from .answer_policy.source import question_row
    row = question_row(question)
    return row is not None and row.get("fold") is True


_GAP_SENTENCE_KINDS = frozenset({"controlled_sentence_production", "grammar_transform", "targeted_correction"})
_ONE_GAP = re.compile(r"（[\s　]*）")
_GAP_SENTENCE_TASKS = (r"补全句子：(?P<hint>.+)", r"按给定意思补全句子：(?P<hint>.+)")


def gap_sentence_task(question):
    """(hint, gap span) of a production question that is a sentence with one blank, else None (brief J6 R10).

    A controlled_sentence_production, grammar_transform or targeted_correction question whose prompt holds
    exactly one gap （　） and whose task is 「补全句子：X」 or 「按给定意思补全句子：X」 is shown as a cloze:
    the cue 穴埋め, X under it, the gap highlighted. Display only; its answer and judging do not change.
    """
    if not isinstance(question, Mapping) or question.get("kind") not in _GAP_SENTENCE_KINDS:
        return None
    prompt, task = question.get("prompt"), question.get("task")
    if not isinstance(prompt, str) or not isinstance(task, str):
        return None
    gaps = list(_ONE_GAP.finditer(prompt))
    if len(gaps) != 1:
        return None
    for pattern in _GAP_SENTENCE_TASKS:
        match = re.fullmatch(pattern, task.strip(), re.DOTALL)
        if match and match["hint"].strip():
            gap = gaps[0]
            return match["hint"].strip(), {"start": gap.start(), "end": gap.end(), "quote": gap.group()}
    return None


def hint_folds(question):
    """Whether the Chinese hint waits behind a button instead of being shown (rule T4).

    Exactly when the question has a folded hint and its effective answer policy says fold: the
    content marks it answerable from the Japanese context alone (answer_overlay ``fold``). Nothing
    else folds; the template field hint_policy is not read.
    """
    return question_presentation(question)["fold_hint"]


def _finish(result, question):
    result["instruction_role"] = "study" if result["instruction"] in _JAPANESE_CUES else "ui"
    result["fold_hint"] = bool(result["folded_hint"]) and _policy_folds(question)
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
    gap_sentence = gap_sentence_task(question) if public else None
    if gap_sentence is not None:
        result.update(instruction=FILL_GAP, folded_hint=gap_sentence[0], target_spans=[gap_sentence[1]])
        return _finish(result, question)
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
            # A probe page (probe_rules.bare_view) shows nothing of the item's own meaning: 読み alone (J6 R6).
            result["instruction"] = (cue.split(" · ", 1)[0] if question.get("probe_view") is True
                                     and cue.startswith(READING_CUE + " · ") else cue)
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
