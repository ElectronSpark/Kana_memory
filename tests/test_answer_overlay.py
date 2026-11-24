"""The answer overlay and the typed-answer rules through their readers (design 03, rules T1-T9).

Made-up lessons and templates only: the catalogue and template lookups are replaced at their source
modules, and the overlay namespace is the test's own (answer_policy.source._OVERLAY).
"""
from copy import deepcopy

import pytest

from kanamemory import content_caches, learning_probe, question_presentation, question_support
from kanamemory.answer_input import available
from kanamemory.answer_policy import source
from kanamemory.answer_policy.kana_answer import has_kana_answer
from kanamemory.answer_result_ui import known_wrong_note
from kanamemory.learning_questions import grade_learning_answer, template_fingerprint

TE = "grammar:demo:te"
DAME = "word:demo:dame"
SCHOOL = "word:demo:school"
LESSONS = {
    TE: {"id": TE, "family": "grammar", "title": "て形", "reading": "", "revision": "r1",
         "abilities": ["understanding", "application"]},
    DAME: {"id": DAME, "family": "word", "title": "だめ", "reading": "だめ", "revision": "r1",
           "abilities": ["understanding", "reading", "use"]},
    SCHOOL: {"id": SCHOOL, "family": "word", "title": "学校", "reading": "がっこう", "revision": "r1",
             "abilities": ["understanding", "reading", "use"]},
}
TRANSFORM = {"prompt": "食べる → ＿＿", "task": "改成て形。", "answer": "食べて", "accepted_answers": ["食べて"],
             "input_mode": "text", "strict": False, "known_wrong": ["食べって"], "feedback": "一段动词去る加て。"}
RECALL = {"prompt": "不行", "task": "写出本课学的单词。", "answer": "だめ", "accepted_answers": ["だめ"],
          "input_mode": "text", "strict": False}
SCHOOL_RECALL = {"prompt": "学校", "task": "本课词：学校", "answer": "学校", "accepted_answers": ["学校"],
                 "input_mode": "text", "strict": False}


def built(raw, target=TE, kind="grammar_transform", *, practice=False, **fields):
    """The question fields the rules read, as build_learning_question sets them."""
    question = {"id": "q", "target_id": target, "content_id": target, "family": LESSONS[target]["family"],
                "kind": kind, "ability": "application" if target == TE else "use", "variant": 0,
                "revision": "r1", "content_revision": "r1", **deepcopy(raw), **fields}
    if practice:
        question["practice_source"] = {"template_sha256": template_fingerprint(raw)}
    return question


@pytest.fixture
def catalogue(monkeypatch):
    """install(raw, row=None, target=..., kind=..., sha=...) registers a template and its overlay row."""
    templates, items = {}, {}
    source._LOCATED.clear()
    source.lesson_forms.cache_clear()

    def locate(item, question, **_):
        raw = templates.get((item["id"], question.get("kind")))
        return None if raw is None else (item, deepcopy(raw))

    def install(raw, row=None, *, target=TE, kind="grammar_transform", sha=None):
        templates[(target, kind)] = raw
        rows = items.setdefault(target, {}).setdefault(kind, {})
        rows.pop(sha or template_fingerprint(raw), None)
        if row is not None:
            rows[sha or template_fingerprint(raw)] = row
        source._LOCATED.clear()

    monkeypatch.setattr("kanamemory.learning_content.get_learning_item", lambda cid: deepcopy(LESSONS.get(cid)))
    monkeypatch.setattr("kanamemory.learning_content.learning_item_view", lambda cid: deepcopy(LESSONS.get(cid)))
    monkeypatch.setattr("kanamemory.learning_questions.question_template", locate)
    monkeypatch.setattr(source, "_OVERLAY", {"schema_version": 1, "items": items})
    # No curated grammar readings (the course's would make 食べて a kana answer by themselves).
    monkeypatch.setattr("kanamemory.learning_questions._CONTENT", {"GRAMMAR_READINGS": {}})
    yield install
    source._LOCATED.clear()
    source.lesson_forms.cache_clear()
    content_caches.clear_all()


def status(question, answer):
    return grade_learning_answer(question, answer)["status"]


# ---------------------------------------------------------------- T1/T2: one effective policy, both kinds of template

@pytest.mark.parametrize("practice", [False, True], ids=["catalogue", "practice"])
def test_a_row_makes_a_non_strict_typed_template_strict_and_back(catalogue, practice):
    question = built(TRANSFORM, practice=practice)
    catalogue(TRANSFORM)
    assert status(question, "食べた") == "unresolved"
    catalogue(TRANSFORM, {"strict": True})
    assert status(question, "食べた") == "incorrect"
    strict = dict(TRANSFORM, strict=True)
    catalogue(strict, {"strict": False})
    assert status(built(strict, practice=practice), "食べた") == "unresolved"
    catalogue(strict)
    assert status(built(strict, practice=practice), "食べた") == "incorrect"


@pytest.mark.parametrize("practice", [False, True], ids=["catalogue", "practice"])
def test_an_added_answer_is_accepted_without_changing_the_question(catalogue, practice):
    question = built(TRANSFORM, practice=practice)
    before = deepcopy(question)
    catalogue(TRANSFORM, {"accepted": ["食べてください"]})
    assert status(question, "食べてください") == "correct"
    assert question == before  # nothing is copied into the snapshot


def test_a_row_of_another_template_or_a_revised_lesson_is_never_read(catalogue):
    catalogue(TRANSFORM, {"accepted": ["食べてください"]}, sha="c" * 64)  # no installed template has this sha
    assert status(built(TRANSFORM), "食べてください") == "unresolved"
    catalogue(TRANSFORM, {"accepted": ["食べてください"]})
    older = built(dict(TRANSFORM, prompt="見る → ＿＿"), revision="r0", content_revision="r0")
    assert status(older, "食べてください") == "unresolved"  # another sentence of an older revision
    frozen = built(TRANSFORM, practice_source={"template_sha256": "d" * 64})
    assert status(frozen, "食べてください") == "unresolved"  # a frozen task of another template


def test_a_known_wrong_entry_is_wrong_with_its_feedback_first_on_the_result_page(catalogue):
    question = built(TRANSFORM)
    catalogue(TRANSFORM, {"known_wrong": [{"answer": "食べた", "feedback": "这是过去形，不是て形。"}]})
    judged = grade_learning_answer(question, "食べた")
    assert judged["status"] == "incorrect"
    assert judged["message"].split("\n") == ["这是过去形，不是て形。", TRANSFORM["feedback"]]
    assert known_wrong_note(question, {"correct": False, "answered_text": "食べた"}) == "这是过去形，不是て形。"
    assert known_wrong_note(question, {"correct": False, "answered_text": "食べって"}) == ""  # the template's own
    assert known_wrong_note(question, {"correct": True, "answered_text": "食べた"}) == ""
    assert status(question, "食べって") == "incorrect"  # the template's list still counts


def test_an_added_answer_makes_a_kanji_only_template_typable_and_servable(catalogue):
    question = built(TRANSFORM)
    catalogue(TRANSFORM)
    assert not has_kana_answer(question)
    assert not available(question, system_japanese=False) and not available(question, system_japanese=True)
    catalogue(TRANSFORM, {"accepted": ["たべて"]})
    assert has_kana_answer(question)
    assert available(question, system_japanese=False) and available(question, system_japanese=True)
    # The selection checks the raw template: its row is found from the template itself.
    raw = dict(TRANSFORM, kind="grammar_transform", family="grammar", target_id=TE)
    assert available(raw, template=TRANSFORM)


def test_an_added_answer_is_protected_from_glosses(catalogue, monkeypatch):
    question = built(TRANSFORM)
    span = {"start": 0, "end": 3, "surface": "食べる", "reading": "たべる", "role": "background",
            "reading_safe": True, "reading_source_id": "test"}
    monkeypatch.setattr(question_support, "_reviewed", lambda q: {"reading_safe": True, "accent_safe": True})
    monkeypatch.setattr(question_support, "_aligned_segments", lambda q, s: [dict(span)])
    monkeypatch.setattr(question_support, "_sentence_spans", lambda q, s, spans: list(spans))
    monkeypatch.setattr(question_support, "_source", lambda value: True)
    catalogue(TRANSFORM)
    shown = [s["reading"] for s in question_support.prompt_support(question)["segments"]]
    assert "たべる" in shown
    catalogue(TRANSFORM, {"accepted": ["たべる"]})
    assert "たべる" in question_support._protected_answer_forms(question)
    assert "たべる" not in question_support._own_answer_forms(question)
    shown = [s["reading"] for s in question_support.prompt_support(question)["segments"]]
    assert "たべる" not in shown  # the gloss would give the added answer away
    catalogue(TRANSFORM)  # the table tool asks before the row is installed
    assert question_support.glosses_showing(question, ["たべる"]) == [("食べる", "たべる")]
    assert question_support.glosses_showing(question, ["たべて"]) == []


# ---------------------------------------------------------------- T4: fold

def test_the_hint_folds_exactly_when_the_row_says_fold_and_the_question_has_a_folded_hint(catalogue):
    question = built(SCHOOL_RECALL, target=SCHOOL, kind="vocab_recall")
    catalogue(SCHOOL_RECALL, target=SCHOOL, kind="vocab_recall")
    view = question_presentation.question_presentation(question)
    assert view["folded_hint"] == "学校" and view["fold_hint"] is False
    assert question_presentation.hint_folds(question) is False
    catalogue(SCHOOL_RECALL, {"fold": True}, target=SCHOOL, kind="vocab_recall")
    assert question_presentation.hint_folds(question) is True
    assert question_presentation.question_presentation(question)["fold_hint"] is True
    plain = dict(SCHOOL_RECALL, task="写出本课学的单词。")
    catalogue(plain, {"fold": True}, target=SCHOOL, kind="vocab_recall")
    assert question_presentation.hint_folds(built(plain, target=SCHOOL, kind="vocab_recall")) is False
    legacy = dict(question, hint_policy="fold")  # the template field stays unread
    catalogue(SCHOOL_RECALL, target=SCHOOL, kind="vocab_recall")
    assert question_presentation.hint_folds(legacy) is False


# ---------------------------------------------------------------- T5: numbers

def test_kanji_numerals_and_digits_compare_the_same_in_typed_answers(catalogue):
    raw = dict(TRANSFORM, answer="二さつ", accepted_answers=["二さつ"], known_wrong=["三さつ"])
    catalogue(raw)
    question = built(raw)
    assert status(question, "2さつ") == "correct" and status(question, "２さつ") == "correct"
    assert status(question, "二さつ") == "correct"
    assert status(question, "3さつ") == "incorrect"  # the known-wrong answer, as digits
    assert status(question, "にさつ") == "unresolved"  # no reading is inferred
    digits = dict(TRANSFORM, answer="23人", accepted_answers=["23人"], known_wrong=[])
    catalogue(digits)
    assert status(built(digits), "二十三人") == "correct"


def test_numbers_do_not_fold_in_a_choice_question(catalogue):
    raw = {"prompt": "本を（　）買った。", "answer": "二冊", "accepted_answers": ["二冊"],
           "choices": ["二冊", "2冊"], "input_mode": "choice", "strict": True}
    catalogue(raw, kind="grammar_cloze")
    assert status(built(raw, kind="grammar_cloze"), "2冊") == "incorrect"


# ---------------------------------------------------------------- T6: listed kana forms

def test_a_listed_other_script_form_of_a_kana_word_is_right_and_an_unlisted_one_is_a_spelling_error(catalogue):
    question = built(RECALL, target=DAME, kind="vocab_recall")
    catalogue(RECALL, target=DAME, kind="vocab_recall")
    judged = grade_learning_answer(question, "ダメ")
    assert judged["status"] == "incorrect" and judged["error_category"] == "spelling"
    listed = dict(RECALL, accepted_answers=["だめ", "ダメ"])
    catalogue(listed, target=DAME, kind="vocab_recall")
    assert status(built(listed, target=DAME, kind="vocab_recall"), "ダメ") == "correct"
    catalogue(RECALL, {"accepted": ["ダメ"]}, target=DAME, kind="vocab_recall")
    assert status(question, "ダメ") == "correct"
    assert grade_learning_answer(question, "だめ")["status"] == "correct"


# ---------------------------------------------------------------- T7: script fold in typed grammar

def test_typed_grammar_answers_may_use_either_kana_script_but_never_a_listed_wrong_one(catalogue):
    raw = dict(TRANSFORM, answer="たべて", accepted_answers=["たべて"], known_wrong=["タベテ"])
    catalogue(raw)
    question = built(raw)
    assert status(question, "タベテ") == "incorrect"  # equals a known-wrong form as written
    assert status(question, "たベて") == "correct"
    loose = dict(raw, known_wrong=[])
    catalogue(loose)
    assert status(built(loose), "タベテ") == "correct"


def test_word_kinds_keep_their_script_rules(catalogue):
    usage = {"prompt": "これは（　）です。", "answer": "だめ", "accepted_answers": ["だめ"], "input_mode": "text",
             "strict": True}
    catalogue(usage, target=DAME, kind="vocab_usage")
    assert status(built(usage, target=DAME, kind="vocab_usage"), "ダメ") == "incorrect"
    choice = dict(usage, input_mode="choice", choices=["だめ", "ダメ"])
    catalogue(choice, kind="grammar_cloze")
    assert status(built(choice, kind="grammar_cloze"), "ダメ") == "incorrect"


# ---------------------------------------------------------------- T8: the lesson reading in vocab_recall

def test_the_lesson_reading_is_accepted_when_the_answer_is_the_lessons_own_form(catalogue):
    question = built(SCHOOL_RECALL, target=SCHOOL, kind="vocab_recall")
    catalogue(SCHOOL_RECALL, target=SCHOOL, kind="vocab_recall")
    assert status(question, "がっこう") == "correct"
    assert has_kana_answer(question)
    phrase = dict(SCHOOL_RECALL, answer="学校へ", accepted_answers=["学校へ"])
    catalogue(phrase, target=SCHOOL, kind="vocab_recall")
    assert status(built(phrase, target=SCHOOL, kind="vocab_recall"), "がっこう") != "correct"


# ---------------------------------------------------------------- T9: no kana answer, never served

@pytest.mark.parametrize("system_japanese", [False, True])
def test_a_typed_template_without_a_kana_answer_is_never_available(catalogue, system_japanese):
    raw = dict(TRANSFORM, prompt="食べる（たべる） → ＿＿")
    catalogue(raw)
    question = built(raw)
    # 食 and べ are on the keypad / in the prompt: typable before, but no kana answer exists.
    assert not available(question, system_japanese=system_japanese)
    kana = dict(raw, accepted_answers=["食べて", "たべて"])
    catalogue(kana)
    assert available(built(kana), system_japanese=system_japanese)
    choice = dict(raw, input_mode="choice", choices=["食べて", "食べた"])
    assert available(built(choice), system_japanese=system_japanese)  # not typed: no kana needed


# ---------------------------------------------------------------- probes read the effective policy

def test_a_non_strict_typed_template_with_a_strict_row_can_be_a_probe(catalogue):
    raw = dict(TRANSFORM, answer="たべて", accepted_answers=["たべて"])
    question = built(raw)
    catalogue(raw)
    assert not learning_probe.probe_qualifies(question, "grammar", "application")
    catalogue(raw, {"strict": True})
    assert learning_probe.probe_qualifies(question, "grammar", "application")
    assert question.get("strict") is False  # the probe stays the question; only the check reads the row
    catalogue(dict(raw, strict=True), {"strict": False})
    assert not learning_probe.probe_qualifies(built(dict(raw, strict=True)), "grammar", "application")
