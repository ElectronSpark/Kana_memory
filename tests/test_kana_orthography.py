"""Word-specific permission never follows sound equivalence alone."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

import pytest

from kanamemory.kana_orthography import allowed_word_spellings, same_kana_sound
from kanamemory.learning_questions import grade_learning_answer
from kanamemory.learning_store import validate_backup_rows
from support.learning_store_fixtures import stores, item, question, ready, START, legacy_dump


@pytest.mark.parametrize("a,b", [("じ", "ぢ"), ("ず", "づ"), ("じゃ", "ぢゃ"),
    ("ジュ", "ヂュ"), ("じょ", "ぢょ"), ("お", "を"), ("い", "ゐ"), ("え", "ゑ"),
    ("はなぢ", "はなじ"), ("ヂャズ", "ジャヅ")])
def test_sound_equivalence_is_symmetric_but_not_permission(a, b):
    assert same_kana_sound(a, b) and same_kana_sound(b, a)
    assert allowed_word_spellings(a) == (a,)
    assert allowed_word_spellings(a, accepted_answers=[b], metadata={"orthography_policy": "modern-kana-word-v1"}) == (a,)


@pytest.mark.parametrize("a,b", [("じゅ", "じゆ"), ("こうこう", "こおこお"),
    ("コーヒー", "コオヒイ"), ("き", "ぎ"), ("しち", "なな"), ("", ""),
    ("a", "a"), ("か゛", "が"), ("じゃ", "ジャ")])
def test_no_size_length_voicing_or_implicit_script_folding(a, b):
    assert not same_kana_sound(a, b)


def test_explicit_script_diagnosis_and_script_constraints():
    assert same_kana_sound("ぢゃ", "ジャ", fold_script=True)
    assert not same_kana_sound("じゃ", "ぢゃ", "katakana")
    assert same_kana_sound("し\u3099ゅ", "ぢゅ", "hiragana")
    with pytest.raises(ValueError): same_kana_sound("じゃ", "ぢゃ", "latin")


@pytest.mark.parametrize("canonical,variant", [("せかいじゅう", "せかいぢゅう"),
    ("いなずま", "いなづま"), ("きずな", "きづな"), ("ほおずき", "ほおづき"),
    ("ひとりずつ", "ひとりづつ"), ("ゆうずう", "ゆうづう"), ("セカイジュウ", "セカイヂュウ")])
def test_reviewed_word_permissions_are_symmetric_and_whole_word(canonical, variant):
    assert allowed_word_spellings(canonical) == (canonical, variant)
    assert allowed_word_spellings(variant) == (variant, canonical)
    assert allowed_word_spellings("お"+canonical) == ("お"+canonical,)


def test_permission_does_not_spread_to_other_letters():
    assert "ほをづき" not in allowed_word_spellings("ほおずき")
    assert "ちゃのみじゃわん" not in allowed_word_spellings("ちゃのみぢゃわん")
    assert "ちじむ" not in allowed_word_spellings("ちぢむ")
    assert "ぢめん" not in allowed_word_spellings("じめん")


def q(expected="はなぢ", kind="vocab_reading", **extra):
    return {"id": "orthography", "kind": kind, "family": "word", "answer": expected,
            "prompt": "鼻血", "accepted_answers": [expected], "strict": True,
            "input_mode": "text", **extra}


def test_reading_diagnosis_never_says_same_sound_is_forgotten():
    question = q(accepted_answers=["はなぢ", "はなじ"])
    before = deepcopy(question)
    same_sound = grade_learning_answer(question, "はなじ")
    assert same_sound["status"] == "unresolved" and not same_sound["scorable"]
    assert same_sound["error_category"] == "spelling" and same_sound["reading_correct"]
    wrong_sound = grade_learning_answer(question, "はなち")
    assert wrong_sound["status"] == "incorrect" and wrong_sound["error_category"] == "reading"
    assert question == before
    approved = grade_learning_answer(q("せかいじゅう", prompt="世界中"), "せかいぢゅう")
    assert approved["status"] == "correct" and approved["accepted_spelling"]


def test_word_use_keeps_spelling_error_and_permitted_variant():
    for kind in ("vocab_recall", "vocab_orthography"):
        same_sound = grade_learning_answer(q(kind=kind), "はなじ")
        assert same_sound["status"] == "incorrect" and same_sound["error_category"] == "spelling"
        assert grade_learning_answer(q("いなずま", kind=kind), "いなづま")["status"] == "correct"


def test_word_reading_spelling_diagnosis_is_frozen_and_keeps_real_due(stores):
    store, learning = stores
    cid = "word:orthography:nosebleed"
    learning.enroll(item(cid, family="word", abilities=["reading", "use"]), now=START)
    first = question(cid, "reading-1", ability="reading", kind="vocab_reading",
                     prompt="鼻血", answer="はなぢ", input_mode="text", strict=True)
    other = question(cid, "use-1", ability="use", kind="vocab_recall", prompt="流鼻血", answer="鼻血")
    learning.append_questions([first, other], now=START)
    when = ready(learning, cid)
    learning.submit("reading-1", "はなぢ", True, now=when)
    learning.finalize("reading-1", 3, now=when)
    unit_before = learning.get_unit(cid, "reading")
    when = datetime.fromisoformat(unit_before["due_at_utc"]) + timedelta(seconds=1)
    second = {**first, "id": "reading-2", "accepted_answers": ["はなぢ", "はなじ"]}
    learning.append_questions(second, now=when)
    learning.save_draft("reading-2", {"answer": "はなじ"})
    legacy = legacy_dump(store)
    other_before = tuple(store._db.execute("SELECT * FROM learning_question WHERE question_id='use-1'").fetchone())
    result = learning.submit("reading-2", "はなじ", False, outcome="incorrect", now=when)
    assert result["outcome"] == "unresolved" and result["error_category"] == "spelling"
    assert learning.submit("reading-2", "はなぢ", True, now=when) == result
    final = learning.finalize("reading-2", 1, now=when)
    assert not final["scheduled"] and final["due_at_utc"] == unit_before["due_at_utc"]
    unit_after = learning.get_unit(cid, "reading")
    assert unit_after["attempt_count"] == unit_before["attempt_count"] + 1
    assert {k: v for k, v in unit_after.items() if k != "attempt_count"} == {k: v for k, v in unit_before.items() if k != "attempt_count"}
    assert legacy_dump(store) == legacy
    assert tuple(store._db.execute("SELECT * FROM learning_question WHERE question_id='use-1'").fetchone()) == other_before
    validate_backup_rows(store._db)
    saved = json.loads(store._db.execute("SELECT result_json FROM learning_question WHERE question_id='reading-2'").fetchone()[0])
    assert saved == result
