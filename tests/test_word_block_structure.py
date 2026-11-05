"""A narrow sentence-ending diagnostic is not a general Japanese order judge."""
from copy import deepcopy

import pytest

from kanamemory.word_blocks import SENTENCE_END_ERROR, structure_diagnostic


def question(blocks=None):
    blocks = blocks or ["庭で遊んでいる", "猫", "を見ました。"]
    return {"input_mode": "reorder", "choices": blocks, "answer": "".join(blocks),
            "accepted_answers": ["".join(blocks)], "strict": False,
            "practice": {"schema_version": 1, "task_family": "translation_wordbank", "reviewed": True}}


def test_misplaced_complete_sentence_end_is_diagnosed_without_mutation():
    q = question()
    frozen = deepcopy(q)
    assert structure_diagnostic(q, "猫を見ました。 庭で遊んでいる") == SENTENCE_END_ERROR
    assert structure_diagnostic(q, q["answer"]) is None
    assert q == frozen


@pytest.mark.parametrize("blocks", [
    ["私は", "図書館で", "本を読みます。"],
    ["私は", "本を", "読みました。"],
])
def test_unlisted_movable_arguments_are_not_marked_wrong(blocks):
    q = question(blocks)
    assert structure_diagnostic(q, blocks[1] + blocks[0] + blocks[2]) is None


def test_explicit_accepted_alternative_wins():
    q = question()
    other = q["choices"][1] + q["choices"][2] + q["choices"][0]
    q["accepted_answers"].append(other)
    assert structure_diagnostic(q, other) is None


@pytest.mark.parametrize("blocks", [
    ["雨です。", "今日は", "休みます。"],
    ["「雨です。", "」と", "言いました。"],
    ["（雨です。）", "今日は", "休みます。"],
    ["今日は", "雨", "です"],
    ["今日は", "雨です", "。"],
    ["今日は", "雨", "ですか？"],
    ["補足：今日は", "雨", "です。"],
    ["私は", "…", "読みました。"],
    ["あ", "あ", "です。"],
    ["あ", "ああ", "です。"],
])
def test_unproven_quotes_dialogue_duplicates_and_ambiguous_blocks_stay_unknown(blocks):
    q = question(blocks)
    assert structure_diagnostic(q, "".join(reversed(blocks))) is None


@pytest.mark.parametrize("answer", ["猫", "を見ました。", "猫を見ました。別の言葉", ""])
def test_partial_or_non_block_input_cannot_be_evidence_of_forgetting(answer):
    assert structure_diagnostic(question(), answer) is None


@pytest.mark.parametrize("field,value", [("input_mode", "text"), ("practice", {}),
    ("choices", ["あ"] * 13), ("answer", "別の正解。")])
def test_other_controls_and_malformed_contracts_are_not_reinterpreted(field, value):
    q = question()
    q[field] = value
    assert structure_diagnostic(q, "猫を見ました。庭で遊んでいる") is None
