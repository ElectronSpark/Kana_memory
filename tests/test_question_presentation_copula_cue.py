"""G3: a copula gap's cue carries its answer's form category, never its text."""
from copy import deepcopy

import pytest

from kanamemory.question_kinds.grammar_cloze import COPULA_FORMS, copula_form_cue
from kanamemory.question_presentation import question_presentation


def cloze(answer, task="补全句子：上周不是休息日。", **extra):
    return {"kind": "grammar_cloze", "prompt": "先週は休み（　）。", "task": task,
            "answer": answer, "input_mode": "text", **extra}


@pytest.mark.parametrize("answer,category", [
    ("ではありません", "否定"), ("じゃありません", "否定"), ("ではない", "否定"),
    ("でした", "过去"), ("だった", "过去"),
    ("ではありませんでした", "否定・过去"), ("じゃなかった", "否定・过去"),
    ("ではありませんでした。", "否定・过去"), (" でした ", "过去"),
])
def test_marked_copula_forms_add_only_their_category(answer, category):
    q = cloze(answer)
    before = deepcopy(q)
    view = question_presentation(q)
    assert view == {"instruction": "穴埋め · " + category, "target_spans": [],
                    "folded_hint": "上周不是休息日。", "fold_hint": False, "instruction_role": "ui"}
    assert answer.strip().removesuffix("。") not in view["instruction"] and q == before


@pytest.mark.parametrize("answer", ["です", "だ", "休みでした", "ません", "が", "", None, ["でした"]])
def test_plain_or_non_copula_answers_keep_the_cue(answer):
    assert copula_form_cue(answer) is None
    assert question_presentation(cloze(answer)) == {
        "instruction": "穴埋め", "target_spans": [], "folded_hint": "上周不是休息日。", "fold_hint": False,
        "instruction_role": "study"}


def test_plain_task_cue_also_gains_the_category():
    assert question_presentation(cloze("でした", task="补全括号中的部分。"))["instruction"] == "穴埋め · 过去"


def test_constraint_tasks_kept_verbatim_are_not_rewritten():
    task = "对老师提出请求，保持委婉。"
    assert question_presentation(cloze("ではありません", task=task))["instruction"] == task


@pytest.mark.parametrize("kind", ["grammar_meaning", "grammar_transform", "vocab_context_choice"])
def test_other_kinds_are_unchanged(kind):
    view = question_presentation(cloze("ではありませんでした", kind=kind))
    assert "否定" not in view["instruction"] and "过去" not in view["instruction"]


def test_every_listed_form_is_a_copula_form():
    assert all(form.startswith(("で", "じゃ", "だ")) for form in COPULA_FORMS)
    assert set(COPULA_FORMS.values()) == {None, "否定", "过去", "否定・过去"}
