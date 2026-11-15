"""成批引入 (the batch introduction): what its cards 新项说明 and its quiz 批小测 write, draw and hand back today.

The batch introduction is about to be split into rules, a service and pages; these tests pin what it does
now, including what may look odd (such a test says so). They reach the batch's state and seams only through
tests/support/course_support.py, so moving the code changes that accessor and not these tests. Buttons are
pressed; the pages' controls are read with the keyword arguments they were made with.

Synthetic lessons only (word:synthetic:wN, built here).
"""
from types import MethodType

import pytest

from kanamemory import assessment_policy
from kanamemory import intro_batch
from kanamemory import ui_components as u
from kanamemory.learning_questions import KIND_LABELS, PREFERENCE_VERSION, READING_CHOICE_KIND, SOUND_PREFERENCE_VERSION
from kanamemory.learning_store import LearningStore
from kanamemory.learning_ui import LEARNING_PREFS, LearningUIMixin
from support import course_support as course
from support.app_fixtures import question_app, setup_app  # noqa: F401 (fixtures used by learning_app)
from support.fake_ui import View
from support.learning_fixtures import learning_app, use_real_detail_back  # noqa: F401

KINDS = ["vocab_meaning", "grammar_meaning"]
W1, W2, W3 = "word:synthetic:w1", "word:synthetic:w2", "word:synthetic:w3"
HOME = "回到今日"
NEXT, KNOWN, LATER = "下一项", "我已经会了", "稍后再学"
PAUSED_TEXT = "未完成题目已保留，重新开启对应题型后可继续。"
NO_QUIZ_TEXT = "这一批里有内容暂时没有可用的小测题型，请检查题型设置。"
REVIEW_HINT_TEXT = "有到期复习，建议先复习"
ROWS = ["〈接下来〉"]


def prefs(kinds):
    # An explicit current choice, so no newer kind is switched on for it.
    return {"enabled": list(kinds), SOUND_PREFERENCE_VERSION: PREFERENCE_VERSION}


def choice(prompt, answer, other):
    return {"prompt": prompt, "answer": answer, "choices": [answer, other], "feedback": "说明",
            "input_mode": "choice", "safe": True}


def word(n):
    cid = f"word:synthetic:w{n}"
    return {"id": cid, "content_id": cid, "family": "word", "title": f"語{n}", "reading": "ご",
            "meaning": f"词{n}", "revision": "1", "abilities": ["understanding"], "source_ids": ["test"],
            "instruction": {"summary": f"词{n}", "rule": "名词。", "steps": [], "example": f"語{n}です。",
                            "translation": f"是词{n}。", "prerequisite": "", "contrast": ""},
            "question_templates": {"vocab_meaning": [choice(f"語{n}", f"词{n}", "别的词")]}}


@pytest.fixture
def app(learning_app, monkeypatch):
    """The course controller on real stores, every control keeping the keyword arguments it was made with."""
    course.record_controls(monkeypatch)
    return learning_app


def open_batch(app, count=2):
    """Enroll ``count`` synthetic words and enter the line: the batch opens at its first card."""
    app.store.set_setting(LEARNING_PREFS, prefs(KINDS))
    app.learning_store.enroll([word(n) for n in range(1, count + 1)])
    app.continue_learning()
    assert app.page == "learning_card"


def to_quiz(app, count=2):
    """Open a batch and press 下一项 on every card: the quiz page shows its first question."""
    open_batch(app, count)
    while app.page == "learning_card":
        course.press(app, NEXT)
    assert app.page == "learning_batch_quiz"


def entries(app):
    return app.learning_store.intro_batch()["entries"]


def queue(app):
    return app.learning_store.intro_batch()["quiz_queue"]


def frozen(app, cid):
    return app.learning_store.instruction_item(cid)["checkpoint"][intro_batch.ITEM_KEY]


def wrong(question):
    return next(option for option in question["choices"] if option != question["answer"])


def refuse(*args, **kwargs):
    raise ValueError("拒绝")


def answer_button(app, raw):
    """The answer button of the quiz page shown that submits the raw option ``raw`` (captions are raw here)."""
    return course.control(app, raw)


def made(view):
    """(kind, text, keyword arguments) of each direct child of ``view`` (each one as it was made)."""
    return [course.made_with(child)[0] for child in view.children]


def targets(app, monkeypatch, *names):
    """The app's page targets ``names`` only record their calls (install before the page is drawn)."""
    return course.record_entries(app, monkeypatch, real=False, names=names)


def pressed(app, log, caption):
    """Press ``caption`` and return (the widget, the calls it made)."""
    start = len(log)
    widget = course.press(app, caption)
    return widget, course.calls(log[start:])


# --- the writes made while a page is shown (most dangerous first) ------------------------------------------

def test_showing_a_card_records_that_it_was_seen_before_its_page_is_drawn(app, monkeypatch):
    """Each drawing of a card first records the exposure of its lesson (expected introduction id passed),
    then draws 新项说明; the lesson the page is about is the store's answer of that write."""
    open_batch(app)
    first = entries(app)[0]
    log = course.record_batch_store_calls(app, monkeypatch, "mark_instruction_exposed")
    course.record_screens(app, monkeypatch, log=log)
    app.show_intro_batch()
    assert course.calls(log) == [
        ("mark_instruction_exposed", (W1,), {"expected_introduction_id": first["introduction_id"]}),
        ("screen", ("learning_card", "新项说明"), {})]
    assert course.batch_lesson(app) is log[0].result
    assert course.batch_card_entry(app) == first
    assert app.learning_store.instruction_item(W1)["checkpoint"]["instruction_exposed_at_utc"]


@pytest.mark.parametrize("why", ["teaching-not-allowed", "introduction-changed", "lesson-gone"])
def test_an_unavailable_card_draws_its_page_and_records_nothing(app, monkeypatch, why):
    """A card whose lesson is gone, was introduced again or may not be taught: page learning_wait
    说明卡暂不可用 (one label, 回到今日 primary -> leave_learning(widget)); no exposure is recorded and the
    card entry kept from the earlier drawing stays."""
    open_batch(app)
    before = course.batch_card_entry(app)
    real = app.learning_store.instruction_item
    answers = {}
    if why == "teaching-not-allowed":
        monkeypatch.setattr(assessment_policy, "teaching_allowed", lambda item, store=None: False)
    elif why == "introduction-changed":
        answers["instruction_item"] = lambda cid: {**real(cid), "introduction_id": "changed"}
    else:
        answers["instruction_item"] = lambda cid: None
    log = course.record_batch_store_calls(app, monkeypatch, "mark_instruction_exposed", "instruction_item",
                                          answers=answers)
    leave = targets(app, monkeypatch, "leave_learning")
    app.show_intro_batch()
    assert [call for call in course.calls(log) if call[0] == "mark_instruction_exposed"] == []
    assert course.page(app) == ("learning_wait", "说明卡暂不可用")
    assert course.page_made_with(app) == [("label", "这一项的说明已变化，原有进度已保存。", {}),
                                          ("button", HOME, {"primary": True})]
    assert course.batch_card_entry(app) == before
    widget, calls = pressed(app, leave, HOME)
    assert calls == [("leave_learning", (widget,), {})]


@pytest.mark.parametrize("study_path", [True, False], ids=["study-path", "no-study-path"])
def test_a_batch_whose_every_card_is_known_closes_with_two_writes_and_hands_back(app, monkeypatch, study_path):
    """Every card answered 我已经会了: the empty quiz is started, the batch completed, then the line is handed
    back (continue_study_path(_checkpoint_saved=True) when the app offers the study path, else
    continue_learning()), in this order and with no quiz page drawn."""
    open_batch(app)
    first, second = entries(app)
    log = course.record_batch_store_calls(app, monkeypatch, "choose_intro_card", "start_intro_quiz",
                                          "complete_intro_batch")
    course.record_screens(app, monkeypatch, log=log)
    course.record_batch_hand_back(app, monkeypatch, study_path=study_path, log=log)
    course.press(app, KNOWN)
    course.press(app, KNOWN)
    hand_back = (("continue_study_path", (), {"_checkpoint_saved": True}) if study_path
                 else ("continue_learning", (), {}))
    assert course.calls(log) == [
        ("choose_intro_card", (W1, "known"), {"expected_introduction_id": first["introduction_id"],
                                              "fair_new": study_path}),
        ("screen", ("learning_card", "新项说明"), {}),
        ("choose_intro_card", (W2, "known"), {"expected_introduction_id": second["introduction_id"],
                                              "fair_new": study_path}),
        ("start_intro_quiz", ({},), {}),
        ("complete_intro_batch", (), {"fair_new": study_path}),
        hand_back]
    assert app.learning_store.intro_batch() is None


def test_the_quiz_is_frozen_when_it_is_first_shown_and_never_again(app, monkeypatch):
    """The quiz questions are written (start_intro_quiz, one list per quiz item in batch order) when the
    quiz is first shown, before its page is drawn; showing it again writes nothing and asks the same
    frozen question."""
    open_batch(app)
    course.press(app, NEXT)
    log = course.record_batch_store_calls(app, monkeypatch, "start_intro_quiz")
    course.record_screens(app, monkeypatch, log=log)
    course.press(app, NEXT)
    names = [call[0] if call[0] != "screen" else call[1] for call in course.calls(log)]
    assert names == ["start_intro_quiz", ("learning_batch_quiz", "批小测")]
    (start,) = [call for call in log if call.name == "start_intro_quiz"]
    assert list(start.args[0]) == [W1, W2] and start.kwargs == {}
    assert {cid: frozen(app, cid)["questions"] for cid in (W1, W2)} == start.args[0]
    asked = course.batch_quiz_question(app)
    assert asked in start.args[0][asked["target_id"]]
    del log[:]
    app.show_intro_batch()
    app.show_intro_batch_quiz()
    assert course.calls(log) == [("screen", ("learning_batch_quiz", "批小测"), {})] * 2
    assert course.batch_quiz_question(app) == asked


# --- stale presses -------------------------------------------------------------------------------------------

def test_a_card_button_of_an_earlier_card_or_on_another_page_does_nothing(app, monkeypatch):
    """下一项 / 我已经会了 of a card no longer current, and of the current card once another page is shown,
    write nothing and draw nothing."""
    open_batch(app, 3)
    old = [course.control(app, NEXT), course.control(app, KNOWN)]
    course.press(app, NEXT)
    log = course.record_batch_store_calls(app, monkeypatch)
    course.record_screens(app, monkeypatch, log=log)
    for widget in old:
        widget.on_press(widget)
    assert course.calls(log) == []
    current = [course.control(app, NEXT), course.control(app, KNOWN)]
    app.screen("home", "今日学习", View(text="首页"))
    del log[:]
    for widget in current:
        widget.on_press(widget)
    assert course.calls(log) == []
    assert app.learning_store.intro_batch()["card_index"] == 1


def test_a_card_choice_the_store_refuses_redraws_the_card_at_the_saved_cursor(app, monkeypatch):
    """A repeated or stale choice the store refuses (ValueError) is swallowed: the batch is shown again from
    its saved cursor (the same card, its exposure recorded again)."""
    open_batch(app)
    first = entries(app)[0]
    log = course.record_batch_store_calls(app, monkeypatch, "choose_intro_card", "mark_instruction_exposed",
                                          answers={"choose_intro_card": refuse})
    course.record_screens(app, monkeypatch, log=log)
    course.press(app, NEXT)
    assert course.calls(log) == [
        ("choose_intro_card", (W1, "next"), {"expected_introduction_id": first["introduction_id"],
                                             "fair_new": False}),
        ("mark_instruction_exposed", (W1,), {"expected_introduction_id": first["introduction_id"]}),
        ("screen", ("learning_card", "新项说明"), {})]
    assert "第 1 / 2 项" in course.texts(app)


def test_an_answer_button_of_an_earlier_drawing_of_the_quiz_does_nothing(app, monkeypatch):
    """The quiz page is drawn again (an equal question, but another object): a button of the earlier drawing
    grades nothing, writes nothing and draws nothing."""
    to_quiz(app)
    asked = course.batch_quiz_question(app)
    old = answer_button(app, asked["answer"])
    app.show_intro_batch()
    assert course.batch_quiz_question(app) == asked and course.batch_quiz_question(app) is not asked
    log = course.record_batch_store_calls(app, monkeypatch)
    course.record_screens(app, monkeypatch, log=log)
    old.on_press(old)
    assert course.calls(log) == []
    assert course.batch_quiz_result_texts(app) == []
    assert frozen(app, asked["target_id"])["answers"] == {}


def test_a_second_press_after_an_answer_does_nothing(app, monkeypatch):
    """Once an answer is given the question is forgotten: another answer button of the same drawing (or
    the same one again) does nothing, and the feedback stays as it was."""
    to_quiz(app)
    asked = course.batch_quiz_question(app)
    course.press(app, asked["answer"])
    assert course.batch_quiz_question(app) is None
    shown = course.batch_quiz_result_texts(app)
    log = course.record_batch_store_calls(app, monkeypatch)
    course.record_screens(app, monkeypatch, log=log)
    for caption in (wrong(asked), asked["answer"]):
        widget = answer_button(app, caption)
        widget.on_press(widget)
    assert course.calls(log) == []
    assert course.batch_quiz_result_texts(app) == shown


# --- a closed kind ----------------------------------------------------------------------------------------------

def test_a_closed_kind_at_answer_time_draws_the_quiz_again_without_grading(app, monkeypatch):
    """The asked question's kind is closed before the answer: nothing is graded or written; the quiz is
    shown again, which for a closed kind is the paused page (题型已关闭 with the kept-questions text), and
    the question is forgotten."""
    to_quiz(app)
    asked, saved = course.batch_quiz_question(app), queue(app)
    log = course.record_batch_store_calls(app, monkeypatch, "answer_intro_quiz", "start_intro_quiz")
    course.record_screens(app, monkeypatch, log=log)
    app.store.set_setting(LEARNING_PREFS, prefs(["grammar_meaning"]))
    course.press(app, asked["answer"])
    assert course.calls(log) == [("screen", ("learning_wait", "题型已关闭"), {})]
    assert PAUSED_TEXT in course.texts(app)
    assert course.batch_quiz_question(app) is None
    assert queue(app) == saved and frozen(app, asked["target_id"])["answers"] == {}


def test_a_closed_kind_at_show_time_pauses_and_the_same_frozen_head_comes_back(app, monkeypatch):
    """Shown while the head's kind is closed: the paused page (nothing written, the head's lesson kept as the
    page's lesson); with the kind open again the same frozen question is asked."""
    to_quiz(app)
    asked = course.batch_quiz_question(app)
    app.store.set_setting(LEARNING_PREFS, prefs(["grammar_meaning"]))
    log = course.record_batch_store_calls(app, monkeypatch, "start_intro_quiz", "answer_intro_quiz")
    course.record_screens(app, monkeypatch, log=log)
    app.show_intro_batch()
    assert course.calls(log) == [("screen", ("learning_wait", "题型已关闭"), {})]
    assert course.batch_quiz_question(app) is None
    assert course.batch_lesson(app)["id"] == asked["target_id"]
    app.store.set_setting(LEARNING_PREFS, prefs(KINDS))
    app.show_intro_batch()
    assert course.page(app) == ("learning_batch_quiz", "批小测")
    assert course.batch_quiz_question(app) == asked
    assert [call.name for call in log if call.name != "screen"] == []


# --- the feedback ---------------------------------------------------------------------------------------------

def test_a_wrong_answer_shows_the_verdict_the_answer_the_explanation_the_card_and_continue(app, monkeypatch):
    """A wrong answer: every answer button is disabled; the result area holds, in this order, the verdict,
    the right answer (bold, the answer's text role), the explanation, 说明卡 (13, MUTED), the card's rows and
    继续 (primary), which shows the batch again (with its widget) at the next question."""
    to_quiz(app)
    asked = course.batch_quiz_question(app)
    log = course.record_batch_store_calls(app, monkeypatch, "answer_intro_quiz")
    shows = course.record_entries(app, monkeypatch, real=True, names=("show_intro_batch",))
    course.press(app, wrong(asked))
    assert course.calls(log) == [("answer_intro_quiz", (asked["target_id"], asked["id"]),
                                  {"answered_text": wrong(asked), "correct": False})]
    assert [enabled for _, enabled in course.batch_quiz_choices(app)] == [False, False]
    result = course.batch_quiz_result(app)
    rows = made(result)
    assert [row[0] for row in rows] == [rows[0][0], "label", "label", "label", "col", "button"]
    assert rows[1] == ("label", asked["answer"], {"bold": True, "text_role": rows[1][2]["text_role"]})
    assert rows[2] == ("label", "说明", {})
    assert rows[3] == ("label", "说明卡", {"size": 13, "color": u.MUTED})
    assert rows[5] == ("button", "继续", {"primary": True})
    card = course.made_with(result.children[4])
    assert card[1] == ("label", f"語{asked['target_id'][-1]}", {"size": 25, "bold": True, "color": u.GREEN,
                                                               "text_role": "study"})
    widget = course.press(app, "继续")
    assert course.calls(shows) == [("show_intro_batch", (widget,), {})]
    assert course.page(app) == ("learning_batch_quiz", "批小测")
    assert course.batch_quiz_question(app)["target_id"] != asked["target_id"]
    assert course.batch_lesson(app)["id"] == course.batch_quiz_question(app)["target_id"]


def test_each_quiz_drawing_makes_the_asked_item_the_pages_lesson(app, monkeypatch):
    """The lesson of the quiz page is the item asked, drawing by drawing (the wrong-answer card shows it)."""
    to_quiz(app)
    for _ in range(2):
        asked = course.batch_quiz_question(app)
        assert course.batch_lesson(app)["id"] == asked["target_id"]
        course.press(app, asked["answer"])
        if queue(app):
            course.press(app, "继续")


def test_the_card_shows_the_loanword_origin_and_so_does_the_wrong_answer_card(app, monkeypatch):
    """A word with a loanword origin: the card page shows the origin line (15) and its note (13, MUTED) after
    the meaning; the card rows under a wrong answer show them too."""
    from kanamemory import loanword_origins
    monkeypatch.setattr(loanword_origins, "origin_texts", lambda item, items=None: (("〈来源〉", "〈注〉"),))
    open_batch(app, 1)
    app.show_intro_batch()
    shown = course.page_made_with(app)
    meaning = shown.index(("label", "词1", {"size": 18}))
    assert shown[meaning + 1:meaning + 3] == [("label", "〈来源〉", {"size": 15}),
                                              ("label", "〈注〉", {"size": 13, "color": u.MUTED})]
    course.press(app, NEXT)
    course.press(app, wrong(course.batch_quiz_question(app)))
    assert [text for text in course.batch_quiz_result_texts(app) if text.startswith("〈")] == ["〈来源〉", "〈注〉"]


def test_the_cards_example_may_use_the_other_words_of_the_batch(app, monkeypatch):
    """The card's example is chosen with the known items plus every word of the open batch."""
    open_batch(app)
    seen = []
    real = app.example_cards

    def example_cards(item, **kwargs):
        seen.append(kwargs)
        return real(item, **kwargs)
    monkeypatch.setattr(app, "example_cards", example_cards)
    app.show_intro_batch()
    (kwargs,) = seen
    assert kwargs["limit"] == 1 and {W1, W2} <= set(kwargs["known_ids"])


def test_next_on_a_card_never_asks_for_a_fair_new_choice(app, monkeypatch):
    """With the study path offered (the fair new choice on), 下一项 still passes fair_new=False and
    我已经会了 passes True."""
    open_batch(app, 3)
    log = course.record_batch_store_calls(app, monkeypatch, "choose_intro_card")
    course.record_batch_hand_back(app, monkeypatch)
    course.press(app, NEXT)
    course.press(app, KNOWN)
    assert [call[2]["fair_new"] for call in course.calls(log)] == [False, True]


def test_a_right_answer_shows_no_card_and_the_last_one_completes_the_batch_and_hands_back(app, monkeypatch):
    """A right answer: the verdict, the explanation and 继续 only (no right-answer line, no card). After the
    last answer 继续 completes the batch and hands back with _checkpoint_saved=True."""
    to_quiz(app)
    first = course.batch_quiz_question(app)
    course.press(app, first["answer"])
    rows = made(course.batch_quiz_result(app))
    assert [row[0] for row in rows][1:] == ["label", "button"]
    assert rows[1] == ("label", "说明", {}) and rows[2] == ("button", "继续", {"primary": True})
    assert "说明卡" not in course.batch_quiz_result_texts(app)
    course.press(app, "继续")
    last = course.batch_quiz_question(app)
    log = course.record_batch_store_calls(app, monkeypatch, "answer_intro_quiz", "complete_intro_batch")
    course.record_screens(app, monkeypatch, log=log)
    course.record_batch_hand_back(app, monkeypatch, log=log)
    course.press(app, last["answer"])
    course.press(app, "继续")
    assert course.calls(log) == [
        ("answer_intro_quiz", (last["target_id"], last["id"]), {"answered_text": last["answer"], "correct": True}),
        ("complete_intro_batch", (), {"fair_new": True}),
        ("continue_study_path", (), {"_checkpoint_saved": True})]
    assert app.learning_store.intro_batch() is None


def test_an_answer_the_store_refuses_shows_the_batch_again(app, monkeypatch):
    """The store refuses the answer (ValueError): no feedback; the batch is shown again (the same frozen
    question on a new quiz page, its buttons enabled)."""
    to_quiz(app)
    asked = course.batch_quiz_question(app)
    log = course.record_batch_store_calls(app, monkeypatch, "answer_intro_quiz", answers={"answer_intro_quiz": refuse})
    course.record_screens(app, monkeypatch, log=log)
    course.press(app, asked["answer"])
    assert course.calls(log) == [
        ("answer_intro_quiz", (asked["target_id"], asked["id"]), {"answered_text": asked["answer"], "correct": True}),
        ("screen", ("learning_batch_quiz", "批小测"), {})]
    assert course.batch_quiz_question(app) == asked
    assert course.batch_quiz_result_texts(app) == []
    assert all(enabled for _, enabled in course.batch_quiz_choices(app))


# --- the five pages --------------------------------------------------------------------------------------------

def test_the_card_page_controls_and_its_three_buttons(app, monkeypatch):
    """新项说明: the position (13, MUTED), the review hint when reviews are due (13, MUTED), the card (title 25
    bold GREEN), then 下一项 (primary), 我已经会了, 稍后再学. 下一项 / 我已经会了 ask choose_intro_card with the
    drawn entry; 稍后再学 -> leave_learning(widget)."""
    open_batch(app)
    monkeypatch.setattr(app, "intro_batch_review_due", lambda: 1)
    log = targets(app, monkeypatch, "choose_intro_card", "leave_learning")
    app.show_intro_batch()
    shown = course.page_made_with(app)
    assert shown[:3] == [("label", "第 1 / 2 项", {"size": 13, "color": u.MUTED}),
                         ("label", REVIEW_HINT_TEXT, {"size": 13, "color": u.MUTED}),
                         ("label", "語1", {"size": 25, "bold": True, "color": u.GREEN, "text_role": "study"})]
    assert shown[-3:] == [("button", NEXT, {"primary": True}), ("button", KNOWN, {}), ("button", LATER, {})]
    assert course.captions(app)[-3:] == [NEXT, KNOWN, LATER]
    entry = course.batch_card_entry(app)
    assert pressed(app, log, NEXT)[1] == [("choose_intro_card", ("next",), {"expected": entry})]
    assert pressed(app, log, KNOWN)[1] == [("choose_intro_card", ("known",), {"expected": entry})]
    widget, calls = pressed(app, log, LATER)
    assert calls == [("leave_learning", (widget,), {})]


def test_the_quiz_page_controls(app, monkeypatch):
    """批小测: the small caption (13, MUTED) first, the review hint when due, the prompt, one multiline answer
    button per option in order (the choice text role), then the empty result area."""
    to_quiz(app)
    monkeypatch.setattr(app, "intro_batch_review_due", lambda: 2)
    app.show_intro_batch()
    asked = course.batch_quiz_question(app)
    rows = made(app.body)
    assert rows[0] == ("label", "批小测", {"size": 13, "color": u.MUTED})
    assert rows[1] == ("label", REVIEW_HINT_TEXT, {"size": 13, "color": u.MUTED})
    buttons = [row for row in rows if row[0] == "button"]
    assert [row[1] for row in buttons] == asked["choices"]
    assert all(row[2] == {"multiline": True, "text_role": buttons[0][2]["text_role"]} for row in buttons)
    assert rows[-1] == ("col", "", {}) and app.body.children[-1].children == []
    assert course.batch_quiz_choices(app) == [(option, True) for option in asked["choices"]]


@pytest.mark.parametrize("why", ["no-quiz-question", "store-refuses"])
def test_the_quiz_unavailable_page_alerts_then_offers_settings_and_home(app, monkeypatch, why):
    """When the quiz cannot be frozen: the alert with the reason (no question for an item, or the store's
    refusal text), then page learning_wait 批小测暂不可用 (说明卡进度已保存。, 题型设置 primary ->
    show_learning_settings(widget), 回到今日 -> leave_learning(widget)); nothing frozen."""
    open_batch(app)
    course.press(app, NEXT)
    answers = {"start_intro_quiz": refuse} if why == "store-refuses" else {}
    if why == "no-quiz-question":
        app.store.set_setting(LEARNING_PREFS, prefs(["grammar_meaning"]))
    course.record_batch_store_calls(app, monkeypatch, "start_intro_quiz", answers=answers)
    log = targets(app, monkeypatch, "alert", "show_learning_settings", "leave_learning")
    course.press(app, NEXT)
    text = "拒绝" if why == "store-refuses" else NO_QUIZ_TEXT
    assert course.calls(log) == [("alert", (text,), {})]
    assert course.page(app) == ("learning_wait", "批小测暂不可用")
    assert course.page_made_with(app) == [("label", "说明卡进度已保存。", {}),
                                          ("button", "题型设置", {"primary": True}), ("button", HOME, {})]
    del log[:]
    widget, calls = pressed(app, log, "题型设置")
    assert calls == [("show_learning_settings", (widget,), {})]
    widget, calls = pressed(app, log, HOME)
    assert calls == [("leave_learning", (widget,), {})]
    assert app.learning_store.intro_batch()["phase"] == "cards"


def test_the_settled_quiz_page_and_its_continue(app, monkeypatch):
    """Shown again after the last answer and before its 继续: 本批小测已完成。 (18) and 继续 (primary), the
    question None and the page's lesson the last quiz item; 继续 completes the batch and hands back."""
    to_quiz(app)
    while course.batch_quiz_question(app) is not None:
        course.press(app, course.batch_quiz_question(app)["answer"])
        if queue(app):
            course.press(app, "继续")
    app.show_intro_batch()
    assert course.page(app) == ("learning_batch_quiz", "批小测")
    assert course.page_made_with(app) == [("label", "本批小测已完成。", {"size": 18}),
                                          ("button", "继续", {"primary": True})]
    assert course.batch_quiz_question(app) is None
    assert course.batch_lesson(app)["id"] == intro_batch.quiz_items(app.learning_store.intro_batch())[-1]
    log = course.record_batch_store_calls(app, monkeypatch, "complete_intro_batch")
    course.record_batch_hand_back(app, monkeypatch, study_path=False, log=log)
    course.press(app, "继续")
    assert course.calls(log) == [("complete_intro_batch", (), {"fair_new": False}), ("continue_learning", (), {})]


def test_continue_on_a_quiz_page_no_longer_shown_does_nothing(app, monkeypatch):
    """继续 after the last answer checks only that the quiz page is still shown: on another page it does
    nothing."""
    to_quiz(app, 1)
    course.press(app, course.batch_quiz_question(app)["answer"])
    widget = course.control(app, "继续")
    app.screen("home", "今日学习", View(text="首页"))
    log = course.record_batch_store_calls(app, monkeypatch, "complete_intro_batch")
    course.record_batch_hand_back(app, monkeypatch, log=log)
    widget.on_press(widget)
    assert course.calls(log) == []


# --- a later ability ---------------------------------------------------------------------------------------------

LATER_ABILITY = {"id": W1, "instruction_scope": "reading", "introduction_id": "intro-1"}


def test_opening_a_later_ability_writes_it_and_hands_back(app, monkeypatch):
    """open_batch_ability(item): the store opens the scope (the item's id, scope and introduction id, the
    enabled kinds, fair_new) and the line is handed back with _checkpoint_saved=True."""
    app.store.set_setting(LEARNING_PREFS, prefs(KINDS))
    log = course.record_batch_store_calls(app, monkeypatch, "open_batch_ability",
                                          answers={"open_batch_ability": lambda *args, **kwargs: None})
    course.record_screens(app, monkeypatch, log=log)
    course.record_batch_hand_back(app, monkeypatch, log=log)
    app.open_batch_ability(dict(LATER_ABILITY))
    assert course.calls(log) == [
        ("open_batch_ability", (W1,), {"instruction_scope": "reading", "expected_introduction_id": "intro-1",
                                       "enabled_kinds": KINDS, "fair_new": True}),
        ("continue_study_path", (), {"_checkpoint_saved": True})]


def test_a_refused_later_ability_draws_its_page_with_the_next_content_rows(app, monkeypatch):
    """The store refuses: page learning_wait 新能力暂不可用 (原有进度已保存。, the next-content rows, 回到今日
    -> leave_learning(widget)); no hand-back."""
    app.store.set_setting(LEARNING_PREFS, prefs(KINDS))
    course.record_batch_store_calls(app, monkeypatch, "open_batch_ability", answers={"open_batch_ability": refuse})
    course.replace_next_content_rows(app, monkeypatch, lambda: [View(text=text) for text in ROWS])
    back = course.record_batch_hand_back(app, monkeypatch)
    leave = targets(app, monkeypatch, "leave_learning")
    app.open_batch_ability(dict(LATER_ABILITY))
    assert back == []
    assert course.page(app) == ("learning_wait", "新能力暂不可用")
    assert course.texts(app) == ["原有进度已保存。", *ROWS, HOME]
    assert course.page_made_with(app) == [("label", "原有进度已保存。", {}), ("button", HOME, {})]
    widget, calls = pressed(app, leave, HOME)
    assert calls == [("leave_learning", (widget,), {})]


# --- Back to a kept page ---------------------------------------------------------------------------------------

def kept_pages(app):
    """The real page history: Back from the settings page (as the app binds it) remounts the page that
    opened it."""
    use_real_detail_back(app)
    app.show_learning_settings = MethodType(LearningUIMixin.show_learning_settings, app)
    app._view_key = ("show_home", None)
    app.screen("home", "今日学习", View(text="首页"))


def test_back_to_a_kept_card_restores_its_entry_and_its_buttons_still_work(app, monkeypatch):
    """From a card the settings page is opened; Back remounts the same card page with its entry, and 下一项
    there advances to the next card."""
    kept_pages(app)
    open_batch(app)
    view, entry = app.content_slot.children[0], course.batch_card_entry(app)
    app.show_learning_settings()
    assert app.page == "learning_settings"
    app.go_back()
    assert app.page == "learning_card" and app.content_slot.children[0] is view
    assert course.batch_card_entry(app) == entry
    course.press(app, NEXT)
    assert (app.page, app.title_label.text) == ("learning_card", "新项说明") and "第 2 / 2 项" in course.texts(app)


def test_back_to_a_kept_quiz_page_after_an_answer_keeps_the_result_and_continue(app, monkeypatch):
    """After an answer the settings page is opened; Back remounts the quiz page with its result still
    shown and the question still forgotten; 继续 there asks the next question."""
    kept_pages(app)
    to_quiz(app)
    asked = course.batch_quiz_question(app)
    course.press(app, asked["answer"])
    view, shown = app.content_slot.children[0], course.texts(app)
    app.show_learning_settings()
    app.go_back()
    assert app.page == "learning_batch_quiz" and app.content_slot.children[0] is view
    assert course.texts(app) == shown and course.batch_quiz_question(app) is None
    other = answer_button(app, wrong(asked))
    other.on_press(other)
    assert course.texts(app) == shown
    course.press(app, "继续")
    assert (app.page, app.title_label.text) == ("learning_batch_quiz", "批小测")
    assert course.batch_quiz_question(app)["target_id"] != asked["target_id"]


# --- the help rows of a formal question and the review hint -----------------------------------------------------

def help_for(app, monkeypatch, *, batch=True, unit=None, scope="reading", source=intro_batch.CARD_SOURCE,
             kind=READING_CHOICE_KIND, ability="reading"):
    log = course.record_batch_store_calls(app, monkeypatch, "is_batch_route", "get_unit", answers={
        "is_batch_route": lambda target: batch, "get_unit": lambda target, ability: unit})
    current = {"question": {"target_id": W1, "ability": ability, "kind": kind},
               "content": {"instruction_scope": scope, "checkpoint": {"initial_familiarity": {"source": source}}}}
    hints, actions = app.intro_batch_question_help(current)
    return log, course.made_with(View(*hints)), course.made_with(View(*actions)), actions


def test_question_help_outside_a_batch_route_is_empty(app, monkeypatch):
    """A question of a lesson not taught through a batch: no hint and no 说明卡, and no unit is read."""
    log, hints, actions, _ = help_for(app, monkeypatch, batch=False)
    assert hints == [] and actions == []
    assert course.calls(log) == [("is_batch_route", (W1,), {})]


def test_question_help_on_the_first_question_of_a_later_ability(app, monkeypatch):
    """A batch item's first question of a later ability opened from the card: one hint line (13, MUTED)
    naming the ability and the kind's cue, and 说明卡 (compact, 12, flex 1) -> learning_hint(widget, card=True)."""
    log, hints, actions, controls = help_for(app, monkeypatch, unit={"attempt_count": 0, "review_count": 0})
    assert course.calls(log) == [("is_batch_route", (W1,), {}), ("get_unit", (W1, "reading"), {})]
    assert hints == [("label", "新能力：读法 — 先选读音", {"size": 13, "color": u.MUTED})]
    assert actions == [("button", "说明卡", {"compact": True, "font_size": 12, "flex": 1})]
    seen = []
    monkeypatch.setattr(app, "learning_hint", lambda *args, **kwargs: seen.append((args, kwargs)))
    controls[0].on_press(controls[0])
    assert seen == [((controls[0],), {"card": True})]


@pytest.mark.parametrize("unit, scope, source, hint, card", [
    ({"attempt_count": 1, "review_count": 1}, "reading", intro_batch.CARD_SOURCE, False, True),
    ({"attempt_count": 0, "review_count": 2}, "reading", intro_batch.CARD_SOURCE, True, False),
    (None, "reading", intro_batch.CARD_SOURCE, True, True),
    ({}, None, intro_batch.CARD_SOURCE, False, True),
    ({}, "reading", "picker", False, True),
], ids=["answered-before", "two-reviews", "no-unit", "no-scope", "other-source"])
def test_question_help_hint_only_before_any_attempt_and_card_only_below_two_reviews(
        app, monkeypatch, unit, scope, source, hint, card):
    """The hint line needs a later ability (a scope) opened from a card and no attempt yet; 说明卡 is offered
    while the unit has fewer than two reviews."""
    _, hints, actions, _ = help_for(app, monkeypatch, unit=unit, scope=scope, source=source)
    assert bool(hints) is hint and bool(actions) is card


def test_question_help_hint_for_other_kinds(app, monkeypatch):
    """The hint names the ability (练习 when unknown) and the kind's cue, else the kind's label."""
    _, hints, _, _ = help_for(app, monkeypatch, unit={}, kind="vocab_reading", ability="understanding")
    assert hints[0][1] == "新能力：理解 — 写出读法"
    _, hints, _, _ = help_for(app, monkeypatch, unit={}, kind="vocab_meaning", ability="other")
    assert hints[0][1] == f"新能力：练习 — {KIND_LABELS['vocab_meaning']}"


def test_the_review_hint_needs_an_open_batch_first_and_then_due_reviews(app, monkeypatch):
    """No open batch: no hint and the due reviews are not counted; an open batch: the hint (13, MUTED) only
    while reviews are due."""
    asked = []
    due = [0]

    def review_due():
        asked.append(True)
        return due[0]
    monkeypatch.setattr(app, "intro_batch_review_due", review_due)
    assert app.intro_batch_review_hint() == [] and asked == []
    open_batch(app)
    del asked[:]
    assert app.intro_batch_review_hint() == [] and asked == [True]
    due[0] = 3
    assert course.made_with(View(*app.intro_batch_review_hint())) == [
        ("label", REVIEW_HINT_TEXT, {"size": 13, "color": u.MUTED})]


def test_the_due_review_count_is_the_learner_stores_ready_count(app, monkeypatch):
    """Without the study path the due count is the learner store's ready count for the enabled kinds."""
    open_batch(app)
    log = course.record_store_calls(app, monkeypatch, "counts")
    assert app.intro_batch_review_due() == log[0].result["ready"]
    assert course.calls(log) == [("counts", (KINDS,), {})]


# --- reads on one drawing ----------------------------------------------------------------------------------------

class Reads(list):
    """The reads of the enabled kinds and of the known items, in order (the saved-kinds reads the enabled
    kinds make themselves are left out)."""

    def names(self):
        return [call.name for call in self if call.name in {"enabled_kinds", "known_ids"}]


def reads(app, monkeypatch):
    log = Reads()
    course.record_kind_reads(app, monkeypatch, log=log)
    course.record_known_reads(monkeypatch, log=log)
    return log


def test_the_reads_on_one_card_drawing(app, monkeypatch):
    """One drawing of a card reads the enabled kinds once (the due-review hint) and the known items once (the
    card's example)."""
    open_batch(app)
    log = reads(app, monkeypatch)
    app.show_intro_batch()
    assert log.names() == ["enabled_kinds", "known_ids"]


def test_the_reads_on_one_quiz_drawing(app, monkeypatch):
    """The first drawing of the quiz (it freezes the questions) and a later one: the enabled kinds and the
    known items, in this order."""
    open_batch(app, 1)
    log = reads(app, monkeypatch)
    course.press(app, NEXT)
    first = log.names()
    del log[:]
    app.show_intro_batch()
    assert (first, log.names()) == (
        ["known_ids", "enabled_kinds", "enabled_kinds", "enabled_kinds"],
        ["enabled_kinds", "known_ids", "enabled_kinds"])
