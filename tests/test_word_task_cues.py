"""Brief J6 R9-R11: the "word by its meaning" task lines, production questions with one gap, glosses beside
short answers. Made-up questions only."""
import pytest

from kanamemory import learning_questions, question_support
from kanamemory.question_presentation import gap_sentence_task, question_presentation, word_by_meaning_task
from kanamemory.question_support import gloss_reveals, learning_text_role


# R9

@pytest.mark.parametrize("task,choice,expected", [
    ("用本课表示“车票”的单词补全，用假名作答。", False, ("かなで穴埋め", "车票")),
    ("用本课表示‘车票’的单词补全，可写汉字或假名。", False, ("穴埋め", "车票")),
    ('用本课表示"车票"的单词补全。', False, ("穴埋め", "车票")),
    ("填入表示“老师”的本课单词。", True, ("穴埋め", "老师")),
    ("用本课表示“吃”的单词补全，用假名作答。（用词典形）", False, ("かなで穴埋め", "吃 · 用词典形")),
    ("用本课表示“吃”的单词补全（用「〜ました」形），用假名作答。", False,
     ("かなで穴埋め", "吃 · 用「〜ました」形")),
    ("用本课表示“漂亮”的单词补全，用假名作答（不用「きれい」）。", False, ("かなで穴埋め", "漂亮 · 不用「きれい」")),
    ("用本课表示“一点”的单词补全，用假名作答（三个假名）。", False, ("かなで穴埋め", "一点 · 三个假名")),
    ("用本课表示“热”的单词补全，说现在的情况（非过去），可写汉字或假名。", False,
     ("穴埋め", "热 · 说现在的情况（非过去）")),
    ("用本课表示“合适”的单词补全，用礼貌体，非过去，可写汉字或假名。", False, ("穴埋め", "合适 · 用礼貌体 · 非过去")),
    # A choice question gives 穴埋め whatever the line says about the input.
    ("用本课表示“车票”的单词补全，用假名作答。", True, ("穴埋め", "车票")),
])
def test_the_reader_of_the_word_by_meaning_family(task, choice, expected):
    assert word_by_meaning_task(task, choice=choice) == expected


@pytest.mark.parametrize("task", [
    "填入表示“热”的い形容词（四个假名）。",      # another frame: shown as written
    "用本课表示“热”的单词补全（未闭合。",          # unbalanced brackets
    "用本课表示“热的单词补全。",                  # unmatched quote
    "补全句子。",
])
def test_a_task_that_fits_no_frame_is_not_read(task):
    assert word_by_meaning_task(task, choice=False) is None


def test_the_page_shows_the_cue_and_the_line_and_keeps_an_unread_task():
    question = {"kind": "vocab_usage", "prompt": "（　）を買いました。", "input_mode": "text", "answer": "きっぷ",
                "task": "用本课表示‘车票’的单词补全，用假名作答。（三个假名）"}
    view = question_presentation(question)
    assert (view["instruction"], view["folded_hint"], view["instruction_role"]) == (
        "かなで穴埋め", "车票 · 三个假名", "study")
    unread = {**question, "task": "填入表示“车票”的名词（两个假名）。"}
    assert question_presentation(unread)["instruction"] == unread["task"]
    chosen = {**question, "input_mode": "choice", "choices": ["きっぷ", "かさ"], "task": "填入表示“车票”的本课单词。"}
    assert question_presentation(chosen)["instruction"] == "穴埋め"


# R10

PRODUCTION = {"kind": "controlled_sentence_production", "prompt": "明日、学校へ（　）。", "answer": "行きます",
              "task": "补全句子：明天去学校。", "input_mode": "text"}


@pytest.mark.parametrize("kind", ["controlled_sentence_production", "grammar_transform", "targeted_correction"])
@pytest.mark.parametrize("task,hint", [("补全句子：明天去学校。", "明天去学校。"),
                                       ("按给定意思补全句子：明天去学校。", "明天去学校。")])
def test_a_production_question_with_one_gap_is_shown_as_a_cloze(kind, task, hint):
    question = {**PRODUCTION, "kind": kind, "task": task}
    view = question_presentation(question)
    gap = question["prompt"].index("（")
    assert (view["instruction"], view["folded_hint"], view["instruction_role"]) == ("穴埋め", hint, "study")
    assert view["target_spans"] == [{"start": gap, "end": gap + 3, "quote": "（　）"}]
    assert learning_text_role(question) == "study"


@pytest.mark.parametrize("prompt", ["明日、学校へ行きます。", "（　）、学校へ（　）。"])
def test_without_exactly_one_gap_the_presentation_is_unchanged(prompt):
    question = {**PRODUCTION, "prompt": prompt}
    assert gap_sentence_task(question) is None
    view = question_presentation(question)
    assert view["instruction"] == question["task"] and view["target_spans"] == []
    assert learning_text_role(question) == "ui"


def test_the_answer_and_judging_do_not_change():
    from kanamemory.learning_questions import grade_learning_answer
    question = {**PRODUCTION, "id": "q", "target_id": "grammar:synthetic:x", "accepted_answers": ["行きます"]}
    before = dict(question)
    assert grade_learning_answer(question, "行きます")["correct"] is True
    question_presentation(question)
    assert question == before


# R11

def test_whole_words_show_the_weekday_beside_the_answer_ni():
    assert not gloss_reveals("日曜日", "星期日", "にちようび", "に")


@pytest.mark.parametrize("surface,meaning,reading,value,japanese", [
    ("二", "二", "に", "に", True),               # read like the answer
    ("在", "在で", "", "で", True),               # the meaning holds the answer as a word
    ("学生", "学生", "がくせい", "学校", True),    # a kanji shared with a Japanese answer
    ("日曜日", "星期日", "にちようび", "星期日那天", False),  # its meaning inside a Chinese answer
])
def test_real_leaks_are_still_caught(surface, meaning, reading, value, japanese):
    assert gloss_reveals(surface, meaning, reading, value, japanese)


def test_a_chinese_answer_is_compared_by_its_words():
    assert not gloss_reveals("日曜日", "星期日", "にちようび", "那天", japanese=False)


def test_the_practice_display_test_uses_whole_words(monkeypatch):
    prompt = "日曜日（　）二人で行きます。"
    gloss = {"start": 0, "end": 3, "surface": "日曜日", "reading": "にちようび", "meaning": "星期日",
             "target_id": "word:synthetic:sunday"}
    leak = {"start": 6, "end": 7, "surface": "二", "reading": "に", "meaning": "二", "target_id": None}
    monkeypatch.setattr(learning_questions, "reviewed_practice_context",
                        lambda question: {"background_glosses": [gloss, leak]})
    question = {"kind": "grammar_cloze", "practice_source": "x", "prompt": prompt, "answer": "に",
                "accepted_answers": ["に"], "target_id": "grammar:synthetic:ni", "ability": "application",
                "practice": {"target_spans": []}}
    shown = question_support._practice_background_context(question)["background_glosses"]
    assert [part["surface"] for part in shown] == ["日曜日"]
    blocked = question_support._practice_background_context({**question, "answer": "日", "accepted_answers": ["日"]})
    assert blocked == {} or all(part["surface"] != "日曜日" for part in blocked["background_glosses"])


@pytest.mark.parametrize("kind,raw", [
    ("vocab_usage", {"prompt": "（　）を買いました。", "answer": "きっぷ", "accepted_answers": ["きっぷ"],
                     "input_mode": "text", "task": "用本课表示‘车票’的单词补全，用假名作答。（三个假名）"}),
    ("controlled_sentence_production", {"prompt": "明日、学校へ（　）。", "answer": "行きます", "input_mode": "text",
                                        "task": "补全句子：明天去学校。"}),
])
def test_with_a_fold_row_the_whole_line_under_the_cue_is_folded(monkeypatch, kind, raw):
    """J6M meeting 7: R9/R10 lines (X and its form hints) follow the overlay row's fold mark as one line."""
    from copy import deepcopy
    from kanamemory import content_caches
    from kanamemory.answer_policy import source as policy_source
    from kanamemory.learning_questions import template_fingerprint
    cid = "word:demo:kippu" if kind == "vocab_usage" else "grammar:demo:iku"
    lesson = {"id": cid, "family": "word" if kind == "vocab_usage" else "grammar", "title": "切符",
              "reading": "きっぷ", "revision": "r1", "abilities": ["understanding", "use"]
              if kind == "vocab_usage" else ["understanding", "application"]}
    rows = {}
    monkeypatch.setattr("kanamemory.learning_content.get_learning_item", lambda c: deepcopy(lesson) if c == cid else None)
    monkeypatch.setattr("kanamemory.learning_content.learning_item_view", lambda c: deepcopy(lesson) if c == cid else None)
    monkeypatch.setattr("kanamemory.learning_questions.question_template",
                        lambda item, question, **_: (item, deepcopy(raw)))
    monkeypatch.setattr(policy_source, "_OVERLAY", {"schema_version": 1, "items": {cid: {kind: rows}}})
    question = {"id": "q", "target_id": cid, "content_id": cid, "family": lesson["family"], "kind": kind,
                "variant": 0, "revision": "r1", "content_revision": "r1", **deepcopy(raw)}
    try:
        policy_source._LOCATED.clear()
        before = question_presentation(question)
        assert before["fold_hint"] is False and " · " in before["folded_hint"] or kind != "vocab_usage"
        rows[template_fingerprint(raw)] = {"fold": True}
        policy_source._LOCATED.clear()
        content_caches.clear_all()
        view = question_presentation(question)
        assert view["fold_hint"] is True and view["folded_hint"] == before["folded_hint"]
        assert view["instruction"] in {"かなで穴埋め", "穴埋め"}
    finally:
        policy_source._LOCATED.clear()
        content_caches.clear_all()
