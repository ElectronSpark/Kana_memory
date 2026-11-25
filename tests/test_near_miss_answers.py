"""A typed kana answer one or two kana from an accepted answer of a word question is wrong (design 03).

Made-up words only: the catalogue lookups are replaced at their source modules and the answer overlay
is the test's own. The pure rule (answer_policy.near_miss), the judge (learning_questions.
grade_learning_answer through answer_policy.source.graded_near_miss), the result page (spelling_display.
near_miss_block on the course result page) and an old record whose verdict stays what it was.
"""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from kanamemory import content_caches, spelling_display, text_layout, ui_components
from kanamemory.answer_policy import near_miss, source
from kanamemory.learning_questions import build_learning_question, grade_learning_answer
from kanamemory.romaji_input import convert_romaji
from support.app_fixtures import question_app, setup_app  # noqa: F401
from support.fake_ui import text_of
from support.learning_fixtures import LEARNING_PREFS, NOW, learning_app  # noqa: F401
from support.pitch_display_support import Node


# ---------------------------------------------------------------- the pure rule (P2, P3)

@pytest.mark.parametrize("target,typed,line", [
    ("もりせい", "もりせ", "长音：少了「い」"),
    ("ぬまこう", "ぬまこうう", "长音：多了「う」"),
    ("ポリーナ", "ポリナ", "长音：少了「ー」"),
    ("ぬけっぽ", "ぬけぽ", "促音：少了「っ」"),
    ("ぬけっぽ", "ぬけつぽ", "促音：「つ」应为「っ」"),
    ("ぬけぽ", "ぬけっぽ", "促音：多了「っ」"),
    ("らんぽこ", "らぽこ", "拨音：少了「ん」"),
    ("らぽこ", "らんぽこ", "拨音：多了「ん」"),
    ("かばとり", "かぱとり", "浊音·半浊音：「ぱ」应为「ば」"),
    ("かばとり", "かはとり", "浊音·半浊音：「は」应为「ば」"),
    ("がもすけ", "かもすけ", "浊音·半浊音：「か」应为「が」"),
    ("みょうか", "みようか", "拗音：「よ」应为「ょ」"),
    ("みょうか", "みうか", "拗音：少了「ょ」"),
    ("ミョウカ", "ミヨウカ", "拗音：「ヨ」应为「ョ」"),
    ("そにまる", "そねまる", "「ね」应为「に」"),
    ("そにまる", "そまる", "少了「に」"),
    ("そにまる", "そにまるぬ", "多了「ぬ」"),
])
def test_each_kind_of_difference_is_named_with_its_kana(target, typed, line):
    found = near_miss.find(typed, [target])
    assert found is not None and found.distance == 1 and found.target == target and found.answer == typed
    assert found.lines == (line,)
    assert "".join(a for a, _ in found.pairs) == target and "".join(b for _, b in found.pairs) == typed


@pytest.mark.parametrize("target,typed,distance", [
    ("ぬこ", "ぬか", None),            # 2 kana: never
    ("ぬこた", "ぬこ", 1),             # 3 kana: distance 1
    ("ぬこた", "ぬ", None),
    ("ぬこたま", "ぬこ", None),        # 4 kana: distance 1 only
    ("ぬこたま", "ぬこた", 1),
    ("ぬこたまり", "ぬこたま", 1),     # 5 kana: distance 1 or 2
    ("ぬこたまり", "ぬこた", 2),
    ("ぬこたまり", "ぬこ", None),
    ("ぬこた", "ヌコタ", None),        # the same kana in the other script: the judge's script rule
])
def test_the_distance_allowed_follows_the_length_of_the_accepted_answer(target, typed, distance):
    found = near_miss.find(typed, [target])
    assert (found.distance if found else None) == distance


def test_the_nearest_kana_answer_counts_and_kanji_answers_are_skipped():
    found = near_miss.find("もりせ", ["森生", "もりたろう", "もりせい"])
    assert found.target == "もりせい" and found.distance == 1
    assert near_miss.find("森せ", ["もりせい"]) is None  # not a kana answer
    assert near_miss.find("morise", ["もりせい"]) is None  # romaji the input did not convert
    assert near_miss.allowed_distance(2) == 0 and near_miss.allowed_distance(4) == 1
    assert near_miss.allowed_distance(5) == 2


def test_an_exempt_reading_is_never_a_near_miss_in_either_script():
    assert near_miss.find("もりせん", ["もりせい"], exempt=["もりせん"]) is None
    assert near_miss.find("モリセン", ["もりせい"], exempt=["もりせん"]) is None
    assert near_miss.find("もりせ", ["もりせい"], exempt=["もりせん"]).target == "もりせい"


def test_romaji_turned_into_kana_by_the_input_is_measured_as_kana():
    typed = convert_romaji("nukepo", final=True)["text"]
    assert typed == "ぬけぽ"
    assert near_miss.find(typed, ["ぬけっぽ"]).lines == ("促音：少了「っ」",)


# ---------------------------------------------------------------- grading (P1, P2 exemptions)

WORD = "word:demo:morisei"
OTHER = "word:demo:morisen"
GRAMMAR = "grammar:demo:te"
LESSONS = {
    WORD: {"id": WORD, "family": "word", "title": "森生", "reading": "もりせい", "revision": "r1",
           "accepted_readings": ["もりせい"], "alternative_readings": ["もりすい"],
           "abilities": ["reading", "use"]},
    OTHER: {"id": OTHER, "family": "word", "title": "森千", "reading": "もりせん", "revision": "r1",
            "accepted_readings": ["もりせん"], "abilities": ["reading", "use"]},
    GRAMMAR: {"id": GRAMMAR, "family": "grammar", "title": "て形", "reading": "", "revision": "r1",
              "abilities": ["application"]},
}
RECALL = {"prompt": "（造的词）", "task": "写出本课学的单词。", "answer": "森生",
          "accepted_answers": ["森生", "もりせい"], "input_mode": "text", "strict": False, "feedback": "森生（もりせい）"}
USAGE = {"prompt": "（　）に行きます。", "answer": "もりせい", "accepted_answers": ["もりせい"],
         "input_mode": "text", "strict": False}
TRANSFORM = {"prompt": "たべる → ＿＿", "task": "改成て形。", "answer": "たべて", "accepted_answers": ["たべて"],
             "input_mode": "text", "strict": False, "feedback": "一段动词去る加て。"}


def built(raw, target=WORD, kind="vocab_recall", **fields):
    return {"id": "q", "target_id": target, "content_id": target, "family": LESSONS[target]["family"],
            "kind": kind, "ability": "application" if target == GRAMMAR else "use", "variant": 0,
            "revision": "r1", "content_revision": "r1", **deepcopy(raw), **fields}


@pytest.fixture
def course(monkeypatch):
    """rows[(kind)] = row: the overlay row of the question's own template (located without a catalogue)."""
    rows = {}
    content_caches.clear_all()
    source._LOCATED.clear()
    monkeypatch.setattr("kanamemory.learning_content.get_learning_item", lambda cid: deepcopy(LESSONS.get(cid)))
    monkeypatch.setattr("kanamemory.learning_content.learning_item_view", lambda cid: deepcopy(LESSONS.get(cid)))
    monkeypatch.setattr("kanamemory.learning_content.learning_item_views",
                        lambda family=None: [deepcopy(x) for x in LESSONS.values()
                                             if family is None or x["family"] == family])
    monkeypatch.setattr(source, "question_row", lambda question: rows.get(question.get("kind")))
    monkeypatch.setattr("kanamemory.learning_questions._CONTENT", {"GRAMMAR_READINGS": {}})
    yield rows
    content_caches.clear_all()
    source._LOCATED.clear()


def judged(question, answer):
    return grade_learning_answer(question, answer)


@pytest.mark.parametrize("raw,kind", [(RECALL, "vocab_recall"), (USAGE, "vocab_usage")])
def test_a_near_miss_of_a_word_question_without_a_strict_row_is_an_ordinary_wrong_answer(course, raw, kind):
    question = built(raw, kind=kind)
    result = judged(question, "もりせ")
    assert result["status"] == "incorrect" and result["correct"] is False and result["scorable"] is True
    assert result["message"].split("\n")[0] == "长音：少了「い」"
    assert "unresolved_reason" not in result
    romaji = judged(question, convert_romaji("morise", final=True)["text"])
    assert romaji["status"] == "incorrect"
    assert judged(question, "もりせい")["status"] == "correct"


def test_a_far_answer_and_the_same_lessons_forms_stay_as_today(course):
    question = built(RECALL)
    assert judged(question, "かきくけ")["status"] == "unresolved"          # far
    assert judged(question, "かきくけ")["unresolved_reason"] == "other_form"
    assert judged(question, "もりすい")["status"] == "unresolved"          # the lesson's dictionary reading
    assert judged(question, "モリスイ")["status"] == "unresolved"
    listed = built(dict(RECALL, alternative_answers=["もりさい"]))
    assert judged(listed, "もりさい")["status"] == "unresolved"            # the question's own alternative
    assert judged(listed, "もりそい")["status"] == "incorrect"
    course["vocab_recall"] = {"accepted": ["もりしい"]}
    assert judged(question, "もりしい")["status"] == "correct"             # an overlay row's accepted form


def test_another_lessons_word_one_kana_away_is_wrong_with_its_line(course):
    question = built(RECALL)
    for typed in ("もりせん", "モリセン"):
        result = judged(question, typed)
        assert result["status"] == "incorrect" and result["correct"] is False
        assert result["message"].split("\n")[0] == f"「{typed[-1]}」应为「い」"


def test_strict_and_known_wrong_rows_and_grammar_answers_keep_their_verdicts(course):
    strict = built(dict(RECALL, strict=True))
    assert judged(strict, "もりせ")["status"] == "incorrect"
    assert judged(strict, "かきくけ")["status"] == "incorrect"
    assert judged(strict, "もりせ")["message"] == RECALL["feedback"]  # the strict path's own message
    course["vocab_recall"] = {"strict": True}
    assert judged(built(RECALL), "かきくけ")["status"] == "incorrect"
    course["vocab_recall"] = {"known_wrong": [{"answer": "もりせえ", "feedback": "长音写作い。"}]}
    wrong = judged(built(RECALL), "もりせえ")
    assert wrong["status"] == "incorrect" and wrong["message"].split("\n") == ["长音写作い。", RECALL["feedback"]]
    course.clear()
    grammar = built(TRANSFORM, target=GRAMMAR, kind="grammar_transform")
    assert judged(grammar, "たべで")["status"] == "unresolved"   # a grammar answer keeps its verdict
    assert judged(grammar, "たべた")["status"] == "unresolved"


def test_the_judge_stores_nothing_new_on_the_question(course):
    question = built(RECALL)
    before = deepcopy(question)
    result = judged(question, "もりせ")
    assert question == before
    assert set(result) == {"status", "correct", "message", "scorable"}


# ---------------------------------------------------------------- the result page (P4, P5)

@pytest.fixture
def nodes(monkeypatch):
    monkeypatch.setattr(ui_components, "label", lambda text, **style: Node("label", text=text, **style))
    monkeypatch.setattr(ui_components, "row", lambda *children, **style: Node("row", *children, **style))
    monkeypatch.setattr(ui_components, "col", lambda *children, **style: Node("col", *children, **style))
    monkeypatch.setattr(text_layout, "font_scale", lambda: 1)
    return SimpleNamespace(main_window=SimpleNamespace(size=SimpleNamespace(width=360)))


def comparison_cells(block):
    grid = block.children[0].children[1]
    return [cell for line in grid.children for cell in line.children]


def test_the_block_marks_the_differing_kana_in_both_lines_and_names_them(course, nodes):
    block = spelling_display.near_miss_block(nodes, built(RECALL), {"correct": False, "answered_text": "もり せ"})
    assert block.children[0].children[0].text == "我的回答："
    cells = comparison_cells(block)
    assert [(c.children[0].text, c.children[1].text) for c in cells] == [
        ("も", "も"), ("り", "り"), ("せ", "せ"), ("い", " ")]
    assert [c.children[0].text for c in cells if c.children[0].style["color"] == spelling_display.ERROR_RED] == ["い"]
    assert [line.text for line in block.children[1:]] == ["长音：少了「い」"]
    extra = spelling_display.near_miss_block(nodes, built(RECALL), {"correct": False, "answered_text": "もりせいい"})
    assert [c.children[1].text for c in comparison_cells(extra)
            if c.children[1].style["color"] == spelling_display.ERROR_RED] == ["い"]
    lines_only = spelling_display.near_miss_block(nodes, built(RECALL), {"correct": False, "answered_text": "もりせ"},
                                                  comparison=False)
    assert [line.text for line in lines_only.children] == ["长音：少了「い」"]


def test_the_block_shows_only_under_a_wrong_verdict_and_also_for_strict_grammar(course, nodes):
    for result in ({"correct": None, "answered_text": "もりせ"}, {"correct": True, "answered_text": "もりせ"},
                   {"correct": False, "answered_text": "かきくけ"}, {"correct": False, "answered_text": ""},
                   {"correct": False, "answered_text": "もりせ", "assisted": True},
                   {"correct": False, "answered_text": "もりせ", "outcome": "revealed"}):
        assert spelling_display.near_miss_block(nodes, built(RECALL), result) is None
    grammar = built(dict(TRANSFORM, strict=True), target=GRAMMAR, kind="grammar_transform")
    block = spelling_display.near_miss_block(nodes, grammar, {"correct": False, "answered_text": "たべで"})
    assert [line.text for line in block.children[1:]] == ["浊音·半浊音：「で」应为「て」"]


ITEM = {
    "id": "word:synthetic-near-miss", "revision": "synthetic-v1", "family": "word", "title": "森生",
    "reading": "もりせい", "meaning": "（造的词）", "abilities": ["use"], "depends_on": [],
    "instruction": {"rule": "合成说明。", "example": "森生に行きます。"},
    "question_templates": {"vocab_recall": [dict(RECALL, prompt="写出“（造的词）”。")]},
}


@pytest.fixture
def recall_page(learning_app, monkeypatch):  # noqa: F811
    app = learning_app
    rows = {}
    monkeypatch.setattr(source, "question_row", lambda question: rows.get(question.get("kind")))
    content_caches.clear_all()
    app.store.set_setting(LEARNING_PREFS, {"enabled": ["vocab_recall"]})
    app.learning_store.enroll(ITEM, now=NOW - timedelta(minutes=2))
    app.learning_store.advance_instruction(ITEM["id"], "intro_done", now=NOW - timedelta(minutes=2))
    app.learning_store.advance_instruction(ITEM["id"], "guide_correct", now=NOW - timedelta(minutes=2))
    question = build_learning_question(ITEM, "vocab_recall")
    app.learning_store.append_questions(question)
    app.continue_learning()
    assert app.page == "learning_question"
    yield app, question, rows
    content_caches.clear_all()


def test_the_course_result_page_shows_the_marked_answers_and_the_lines(recall_page):
    app, question, _ = recall_page
    app.submit_learning_answer("もりせ")
    saved = app.learning_store.get_question(question["id"])
    assert saved["result"]["outcome"] == "incorrect" and saved["result"]["correct"] is False
    assert saved["result"]["answered_text"] == "もりせ"
    assert not any(key.startswith("near") for key in saved["result"])  # nothing new is stored
    text = text_of(app.body)
    assert text.count("我的回答：") == 1 and "长音：少了「い」" in text
    before = tuple(app.store._db.iterdump())
    app.show_learning_feedback(saved["result"])  # drawn again from the stored answer: nothing changes
    assert tuple(app.store._db.iterdump()) == before and app.learning_store.get_question(question["id"]) == saved


def test_a_rows_own_feedback_stays_before_the_lines(recall_page):
    app, question, rows = recall_page
    rows["vocab_recall"] = {"known_wrong": [{"answer": "もりせえ", "feedback": "这里的长音写作「い」。"}]}
    app.submit_learning_answer("もりせえ")
    assert app.learning_store.get_question(question["id"])["result"]["correct"] is False
    text = text_of(app.body)
    assert text.index("这里的长音写作「い」。") < text.index("长音：「え」应为「い」")
    assert text.index("我的回答：") < text.index("长音：「え」应为「い」")


def test_an_old_unresolved_record_keeps_its_verdict_and_gets_no_marking(recall_page):
    app, question, _ = recall_page
    # The judge before this rule left the near miss unresolved; that stored verdict is never derived again.
    saved = app.learning_store.submit(question["id"], "もりせ", None, outcome="unresolved")
    assert saved["outcome"] == "unresolved" and saved["correct"] is None
    stored = deepcopy(app.learning_store.get_question(question["id"]))
    app.show_learning_feedback(stored["result"])
    assert "我的回答：" in text_of(app.body) and "长音：少了「い」" not in text_of(app.body)
    assert app.learning_store.get_question(question["id"]) == stored
