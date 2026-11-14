"""Grammar cloze: complete one gap of a sentence with the lesson's form.

Pure rules: no Toga, database, platform, network or content loading. This
module owns the kind-specific parts of grammar_cloze: its instruction cues
(a completion cue that is only a translation of the gap moves into the folded
hint; a copula gap adds the form category of its answer) and its romaji
composition style. It is built from the shared grammar
legacy builder (question_kinds/grammar_meaning) or the generic template path
and graded by the generic exact match; multi-gap shapes go through
multi_cloze. Its spec flags (builtin) gloss learned background words and bind
an order translation to the prompt with its single gap filled.
"""
from __future__ import annotations

from ..question_kind_registry import KindPresentation

KIND = "grammar_cloze"
KINDS = (KIND,)

# Instruction cue; the same literal is study text in question_presentation.
FILL_GAP = "穴埋め"
# input_mode_policy result: convert only Latin runs of the draft.
ROMAJI_RUNS = "runs"

# Copula form cues: the negation/tense category of the canonical answer, so a
# gap such as 先週は休み（　）。 is decidable without the folded hint. Only the
# category is shown, never answer text; affirmative non-past forms add nothing.
NEGATIVE, PAST, NEGATIVE_PAST = "否定", "过去", "否定・过去"
COPULA_FORMS = {
    "です": None, "だ": None,
    "ではありません": NEGATIVE, "じゃありません": NEGATIVE, "ではない": NEGATIVE,
    "じゃない": NEGATIVE, "ではないです": NEGATIVE, "じゃないです": NEGATIVE,
    "でした": PAST, "だった": PAST,
    "ではありませんでした": NEGATIVE_PAST, "じゃありませんでした": NEGATIVE_PAST,
    "ではなかった": NEGATIVE_PAST, "じゃなかった": NEGATIVE_PAST,
    "ではなかったです": NEGATIVE_PAST, "じゃなかったです": NEGATIVE_PAST,
}


def copula_form_cue(answer):
    """Form category of a copula answer (one whole form, final 。 ignored), else None."""
    if not isinstance(answer, str):
        return None
    return COPULA_FORMS.get(answer.strip().removesuffix("。"))


def form_cue(question, instruction):
    """KindPresentation.form_cue: the gap cue plus the copula form category."""
    category = copula_form_cue(question.get("answer"))
    return f"{FILL_GAP} · {category}" if category and instruction == FILL_GAP else None


PRESENTATION = KindPresentation(plain_tasks=(("补全括号中的部分。", FILL_GAP),),
                                folds_translation=True, form_cue=form_cue)


def romaji_input(question):
    """input_mode_policy: a grammar draft may mix kanji; convert Latin runs only."""
    return ROMAJI_RUNS
