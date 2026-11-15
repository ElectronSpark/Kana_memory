"""作答结果 (a course question's submit, its result page, its detail page and its rating), pinned as they are.

What a submit refuses and in which order, what it records, what the result page shows (every control with
the arguments it is made with), what 问 AI, 详细, the speaker and the rating buttons do, what the detail page
shows and keeps, how a locked database is met on submit and on rating, and how the answer is played once.
The answering code is about to move; these tests reach its state and its seams only through
tests/support/course_support.py, so the move changes that accessor and not these tests. Expected values are
what the code does, including what looks odd (each such test says so).

Public: synthetic course items (support.learning_store_fixtures, ids "<family>:s5-synthetic:*") and the
synthetic pitch item of support.learning_pitch_controls_support.
"""
import sqlite3
from datetime import timedelta

import pytest

from kanamemory import result_readings
from kanamemory.course_planning import known_ids
from kanamemory.learning_store import LearningStore
from kanamemory.question_support import clean_prompt_preferences
from support import course_support as course_line
from support.app_fixtures import question_app, setup_app  # noqa: F401
from support.fake_ui import View
from support.learning_fixtures import NOW, learning_app, use_real_detail_back  # noqa: F401
from support.learning_pitch_controls_support import prepare as prepare_pitch
from support.learning_store_fixtures import item, question

PREFS = "learning_preferences_v1"
PROMPT_PREFS = "prompt_display_v1"
RIGHT, WRONG = "正在读书", "已经结束"
PROMPT = "今、本を読んでいます。"
EXPLANATION = "合成结果说明"
MUTED = "#58716A"
PLAIN = {"prompt_furigana_visible": False, "prompt_accent_visible": False, "audio_used": False}
BUSY_SUBMIT = "数据库正忙，答案尚未提交，请稍后再点提交。"
BUSY_RATING = "数据库正忙，评级尚未保存。请稍后再点一次。"
RATINGS = (("再学一次", "again", 1), ("有些困难", "hard", 2), ("记住了", "good", 3), ("很轻松", "easy", 4))

# The controls every result page shows after the answer and the explanation: 问 AI and 详细 at the right.
HELP_ROW = [("row", "", {}), ("col", "", {"flex": 1}),
            ("button", "问 AI", {"compact": True, "width": 72, "font_size": 12}),
            ("button", "详细", {"compact": True, "width": 72, "font_size": 12})]
RATING_ROWS = [("row", "", {}), ("button", "再学一次", {"flex": 1, "multiline": True}),
               ("button", "有些困难", {"flex": 1, "multiline": True}),
               ("row", "", {}), ("button", "记住了", {"flex": 1, "multiline": True}),
               ("button", "很轻松", {"flex": 1, "multiline": True})]
SAVE = [("button", "保存并继续", {"primary": True})]
SPEAKER = ("button", "🔊", {"compact": True, "width": 52, "height": 52, "font_size": 16, "margin_left": 6,
                           "margin_bottom": 0})
ANSWER_ROW = [("row", "", {}), ("label", RIGHT, {"size": 20, "flex": 1, "text_role": "ui"}), SPEAKER]
ENTERED_WRONG = [("row", "", {}), ("label", "我的回答：", {"size": 12, "color": MUTED, "width": 84.0}),
                 ("label", WRONG, {"size": 12, "color": MUTED, "flex": 1, "text_role": "ui"})]


# --- states -----------------------------------------------------------------------------------------------

def enrolled(app, cid, *, kind, family="grammar", **item_fields):
    """A synthetic course item taught two minutes ago, with ``kind`` the only enabled question kind."""
    app.store.set_setting(PREFS, {"enabled": [kind]})
    learning = app.learning_store
    learning.enroll(item(cid, family=family, title="合成结果", abilities=["understanding"], **item_fields),
                    now=NOW - timedelta(minutes=2))
    learning.advance_instruction(cid, "intro_done", now=NOW - timedelta(minutes=2))
    learning.advance_instruction(cid, "guide_correct", now=NOW - timedelta(minutes=2))


def asked(app, suffix="one", *, kind="grammar_meaning", family="grammar", item_fields=None, **fields):
    """Enrol a synthetic item, queue its question and draw the question page as the line draws it.
    Default: a meaning question answered by choosing 正在读书 (right) or 已经结束 (not accepted)."""
    cid = f"{family}:s5-synthetic:{suffix}"
    enrolled(app, cid, kind=kind, family=family, **(item_fields or {}))
    snapshot = question(cid, "s5-result:" + suffix, kind=kind, family=family,
                        **{"feedback": EXPLANATION, "choices": [RIGHT, WRONG], **fields})
    app.learning_store.append_questions(snapshot)
    stored = app.learning_store.get_question(snapshot["id"])
    app.show_learning_question({"question": stored, "draft": {}})
    assert app.page == "learning_question"
    return stored


def heard(app, suffix="heard", **fields):
    """A heard-word meaning question (its audio must play before it can be answered)."""
    return asked(app, suffix, kind="vocab_audio_meaning", family="word", audio_text="ほんをよむ",
                 item_fields={"reading": "ほん"}, **fields)


def reading(app, suffix="reading", **fields):
    """A reading question answered in kana (手紙 -> てがみ), wrong answers judged wrong."""
    return asked(app, suffix, kind="vocab_reading", family="word", input_mode="text", prompt="手紙",
                 answer="てがみ", choices=[], strict=True, item_fields={"reading": "てがみ"}, **fields)


def answered(app, monkeypatch, caption=RIGHT, **fields):
    """A meaning question answered with ``caption``; the controls' arguments are recorded."""
    course_line.record_controls(monkeypatch)
    q = asked(app, **fields)
    course_line.press(app, caption)
    assert app.page == "learning_feedback"
    return q


def locked():
    return sqlite3.OperationalError("database is locked")


def failing_once(real, error):
    """A store method that raises ``error`` on its first call and then works."""
    state = {"failed": False}

    def call(*args, **kwargs):
        if not state["failed"]:
            state["failed"] = True
            raise error
        return real(*args, **kwargs)
    return call


def page_controls(app):
    """(kind, text, keyword arguments) of every recorded control of the page shown, in drawing order."""
    return course_line.page_made_with(app)


def writes(log):
    """The submits, ratings and draft saves of a store-call log."""
    return course_line.calls(log, "submit", "finalize", "save_draft")


# --- a. the refusals of a submit -------------------------------------------------------------------------

def test_a_field_refusal_comes_first_then_the_audio_then_the_empty_answer_and_nothing_is_written(
        learning_app, monkeypatch):
    """On a heard question typed in kana: an unfinished romaji answer is refused by the field itself
    (请补全罗马音…) before the audio check; an empty answer is refused by 请先成功播放题目。 until the audio
    has played, then by 请先作答。. No refusal writes anything or stops speech."""
    app = learning_app
    q = reading(app, audio_text="てがみ")
    log = course_line.record_store_calls(app, monkeypatch, "submit", "finalize", "save_draft")
    stops = course_line.record_speech_stops(app, monkeypatch)
    app.submit_learning_answer("tegam", question_id=q["id"])
    assert "请补全罗马音，或切换假名按键修改。" in course_line.texts(app)
    app.submit_learning_answer("", question_id=q["id"])
    assert "请先成功播放题目。" in course_line.texts(app)
    app.submit_learning_answer("てがみ", question_id=q["id"])
    assert "请先成功播放题目。" in course_line.texts(app)
    course_line.set_audio_heard(app, True)
    app.submit_learning_answer("", question_id=q["id"])
    assert "请先作答。" in course_line.texts(app)
    assert app.page == "learning_question" and writes(log) == [] and stops == []
    stored = app.learning_store.get_question(q["id"])
    assert stored["status"] == "pending" and not stored.get("result") and app.learning_store.history() == []


def test_a_an_empty_block_answer_is_refused_with_its_text(learning_app, monkeypatch):
    """提交 on a block question with no block chosen: 请先作答。 on the status line, nothing written."""
    app = learning_app
    q = asked(app, "blocks", kind="grammar_reorder", input_mode="reorder", choices=["本を", "読んでいます"],
              answer="本を読んでいます")
    log = course_line.record_store_calls(app, monkeypatch, "submit", "finalize", "save_draft")
    course_line.press(app, "提交")
    assert app.page == "learning_question" and "请先作答。" in course_line.texts(app)
    assert writes(log) == [] and app.learning_store.get_question(q["id"])["status"] == "pending"


def test_a_an_unfinished_pitch_answer_is_refused_with_the_grading_message_without_stopping_speech(
        learning_app, monkeypatch):
    """A pitch answer that is not empty but not complete: the grader's own message on the status line
    (after grading, before speech is stopped), nothing written."""
    app = learning_app
    _, q = prepare_pitch(app)
    course_line.press(app, "▔")
    log = course_line.record_store_calls(app, monkeypatch, "submit", "finalize")
    stops = course_line.record_speech_stops(app, monkeypatch)
    app.submit_learning_answer(course_line.answer_draft(app)["answer"])
    assert app.page == "learning_question"
    assert "请完成这道题再检查。" in course_line.texts(app)
    app.submit_learning_answer("not a pitch answer")
    assert app.page == "learning_question"
    assert "这份音高作答无法读取，请重新作答。" in course_line.texts(app)
    assert writes(log) == [] and stops == []


def test_a_a_submit_for_another_question_or_off_the_question_page_does_nothing(learning_app, monkeypatch):
    """A submit naming another question id, or made when the question page is no longer shown, writes
    nothing and draws nothing."""
    app = learning_app
    q = asked(app)
    log = course_line.watch(app, monkeypatch, names=())
    stores = course_line.record_store_calls(app, monkeypatch, "submit")
    app.submit_learning_answer(RIGHT, question_id="s5-result:other")
    app.page = "learning_prerequisite"
    app.submit_learning_answer(RIGHT, question_id=q["id"])
    assert log == [] and stores == []


# --- b. what a submit records ------------------------------------------------------------------------------

def test_b_a_right_choice_records_correct_with_the_plain_presentation(learning_app, monkeypatch):
    """submit(id, the raw choice, True, assisted=False, outcome="correct", presentation=all False), no
    early permit; the stored result says the same."""
    app = learning_app
    q = asked(app)
    log = course_line.record_store_calls(app, monkeypatch, "submit")
    course_line.press(app, RIGHT)
    assert course_line.calls(log) == [("submit", (q["id"], RIGHT, True),
                                       {"assisted": False, "outcome": "correct", "presentation": PLAIN})]
    result = app.learning_store.get_question(q["id"])["result"]
    assert (result["correct"], result["outcome"], result["presentation"]) == (True, "correct", PLAIN)


@pytest.mark.parametrize("strict, correct, outcome", [(True, False, "incorrect"), (False, None, "unresolved")])
def test_b_a_wrong_choice_records_its_status(learning_app, monkeypatch, strict, correct, outcome):
    """A choice that is not accepted: judged wrong (False, "incorrect") when the question is strict, else
    left unresolved (None, "unresolved")."""
    app = learning_app
    q = asked(app, strict=strict)
    log = course_line.record_store_calls(app, monkeypatch, "submit")
    course_line.press(app, WRONG)
    assert course_line.calls(log) == [("submit", (q["id"], WRONG, correct),
                                       {"assisted": False, "outcome": outcome, "presentation": PLAIN})]


def test_b_an_assisted_draft_is_recorded_assisted(learning_app, monkeypatch):
    """A draft marked assisted (a hint was opened) records assisted=True with the right answer."""
    app = learning_app
    q = asked(app)
    course_line.answer_draft(app)["assisted"] = True
    log = course_line.record_store_calls(app, monkeypatch, "submit")
    course_line.press(app, RIGHT)
    assert course_line.calls(log) == [("submit", (q["id"], RIGHT, True),
                                       {"assisted": True, "outcome": "correct", "presentation": PLAIN})]


def test_b_showing_the_answer_records_revealed_and_assisted(learning_app, monkeypatch):
    """显示答案: the draft is saved assisted, then submit(id, "", False, assisted=True, outcome="revealed")."""
    app = learning_app
    q = asked(app)
    log = course_line.record_store_calls(app, monkeypatch, "submit", "save_draft")
    course_line.press(app, "显示答案")
    assert course_line.calls(log) == [
        ("save_draft", (q["id"], {"presented": True, "assisted": True}), {}),
        ("submit", (q["id"], "", False), {"assisted": True, "outcome": "revealed", "presentation": PLAIN})]


def test_b_a_heard_question_shown_without_its_audio_records_a_device_failure(learning_app, monkeypatch):
    """显示答案 on a heard question whose audio never played: outcome "device_failure", correct None,
    audio_used False."""
    app = learning_app
    q = heard(app)
    log = course_line.record_store_calls(app, monkeypatch, "submit")
    course_line.press(app, "显示答案")
    assert course_line.calls(log) == [("submit", (q["id"], "", None),
                                       {"assisted": True, "outcome": "device_failure", "presentation": PLAIN})]


def test_b_a_heard_question_after_its_audio_records_audio_used(learning_app, monkeypatch):
    """After the audio played, the right choice records audio_used True and its own status."""
    app = learning_app
    q = heard(app)
    course_line.set_audio_heard(app, True)
    log = course_line.record_store_calls(app, monkeypatch, "submit")
    course_line.press(app, RIGHT)
    assert course_line.calls(log) == [("submit", (q["id"], RIGHT, True), {
        "assisted": False, "outcome": "correct", "presentation": {**PLAIN, "audio_used": True}})]


@pytest.mark.parametrize("furigana, accent", [(True, False), (False, True)])
def test_b_the_presentation_is_the_plan_of_the_prompt_the_page_drew(learning_app, monkeypatch, furigana, accent):
    """The plan is asked once on submit, with this question, the cleaned prompt preferences and the known
    ids the page drew the prompt with; its furigana and accent flags are recorded as they are."""
    app = learning_app
    app.store.set_setting(PROMPT_PREFS, {"furigana": True})
    q = asked(app)
    plans = course_line.record_prompt_plans(monkeypatch, plan={"furigana_visible": furigana,
                                                               "accent_visible": accent})
    log = course_line.record_store_calls(app, monkeypatch, "submit")
    course_line.press(app, RIGHT)
    assert len(plans) == 1
    shown, preferences, known = plans[0].args
    assert shown is course_line.course_question(app) and shown["id"] == q["id"]
    assert preferences == clean_prompt_preferences({"furigana": True})
    assert known == known_ids(app.store.queries)
    assert course_line.calls(log)[0][2]["presentation"] == {
        "prompt_furigana_visible": furigana, "prompt_accent_visible": accent, "audio_used": False}


def test_b_the_plain_plan_when_the_drawn_prompt_belongs_to_another_question(learning_app, monkeypatch):
    """When the question being submitted is not the very question whose prompt the page drew (here an
    equal copy), the plan is asked without known ids (the plain plan)."""
    app = learning_app
    asked(app)
    course_line.set_course_question(app, dict(course_line.course_question(app)))
    plans = course_line.record_prompt_plans(monkeypatch)
    course_line.press(app, RIGHT)
    assert [call.args[2] for call in plans] == [None]


def test_b_the_plain_plan_when_the_page_drew_no_prompt(learning_app, monkeypatch):
    """A heard question draws no prompt (only its speaker): the plan is asked without known ids."""
    app = learning_app
    heard(app)
    course_line.set_audio_heard(app, True)
    plans = course_line.record_prompt_plans(monkeypatch)
    course_line.press(app, RIGHT)
    assert [call.args[2] for call in plans] == [None]


def test_b_a_submit_reads_the_prompt_preferences_once(learning_app, monkeypatch):
    """Between the press and the result page, the prompt preferences are read once."""
    app = learning_app
    asked(app)
    reads = course_line.record_setting_reads(app, monkeypatch, PROMPT_PREFS)
    course_line.press(app, RIGHT)
    assert [call.page for call in reads] == ["learning_question"]


def test_b_a_question_handed_out_early_is_submitted_with_the_early_permit(learning_app, monkeypatch):
    """A question extra practice handed out early: submit(..., early=True); another question: no permit."""
    app = learning_app
    q = asked(app)
    course_line.permit_early_answers(monkeypatch, q["id"])
    log = course_line.record_store_calls(app, monkeypatch, "submit", answers={"submit": lambda *a, **k: None})
    app.show_learning_feedback = lambda result: None
    course_line.press(app, RIGHT)
    assert course_line.calls(log) == [("submit", (q["id"], RIGHT, True), {
        "assisted": False, "outcome": "correct", "presentation": PLAIN, "early": True})]


# --- c. the order around stopping speech, a locked database -------------------------------------------------

def test_c_speech_is_stopped_after_grading_and_before_the_plan_and_the_record(learning_app, monkeypatch):
    """Order of one submit: stop speech, ask the plan, record, draw the result page, play the answer."""
    app = learning_app
    q = asked(app)
    log = course_line.record_speech_stops(app, monkeypatch)
    course_line.record_prompt_plans(monkeypatch, log=log)
    course_line.record_store_calls(app, monkeypatch, "submit", log=log)
    course_line.record_screens(app, monkeypatch, log=log)
    course_line.record_answer_autoplay(monkeypatch, log=log)
    course_line.press(app, RIGHT)
    assert [call.name for call in log] == ["stop", "prompt_plan", "submit", "screen", "autoplay"]
    assert log[3].args == ("learning_feedback", "作答结果")
    assert log[4].args == (PROMPT, ("learning", q["id"]))


def test_c_a_locked_database_on_submit_writes_nothing_and_the_same_press_works_afterwards(
        learning_app, monkeypatch):
    """The record raises "database is locked": noted as ("learning_submit", "save"), the status line says
    数据库正忙…, the question stays pending on its page (speech was already stopped); pressing the same
    choice again records it and draws the result page."""
    app = learning_app
    q = asked(app)
    errors = course_line.record_errors(monkeypatch)
    error = locked()
    real = app.learning_store.submit
    log = course_line.record_store_calls(app, monkeypatch, "submit",
                                         answers={"submit": failing_once(real, error)})
    stops = course_line.record_speech_stops(app, monkeypatch)
    widget = course_line.control(app, RIGHT)
    widget.on_press(widget)
    assert app.page == "learning_question" and BUSY_SUBMIT in course_line.texts(app)
    assert errors == [("learning_submit", "save", error)] and len(stops) == 1
    assert app.learning_store.get_question(q["id"])["status"] == "pending"
    assert not app.learning_store.get_question(q["id"]).get("result")
    widget.on_press(widget)
    assert app.page == "learning_feedback" and len(course_line.calls(log, "submit")) == 2
    assert app.learning_store.get_question(q["id"])["result"]["correct"] is True


def test_c_another_error_on_submit_is_raised(learning_app, monkeypatch):
    """An error that is not a locked database leaves the submit as it is: it is raised, nothing is noted."""
    app = learning_app
    asked(app)
    errors = course_line.record_errors(monkeypatch)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")
    course_line.record_store_calls(app, monkeypatch, "submit", answers={"submit": broken})
    with pytest.raises(sqlite3.OperationalError):
        course_line.press(app, RIGHT)
    assert errors == []


def test_c_a_locked_database_on_rating_alerts_and_the_same_press_works_afterwards(learning_app, monkeypatch):
    """The rating raises "database is locked": noted as ("learning_submit", "commit"), the alert
    数据库正忙，评级尚未保存。…, no queueing, no hand-back, the result page stays; the same press then rates."""
    app = learning_app
    q = answered(app, monkeypatch)
    errors = course_line.record_errors(monkeypatch)
    error = locked()
    real = app.learning_store.finalize
    log = course_line.record_store_calls(app, monkeypatch, "finalize",
                                         answers={"finalize": failing_once(real, error)})
    entries = course_line.record_entries(app, monkeypatch, real=False,
                                         names=("_queue_learning_questions",) + course_line.HAND_BACKS[1:2])
    widget = course_line.control(app, "记住了")
    widget.on_press(widget)
    assert app.alert.call_args_list == [((BUSY_RATING,), {})]
    assert errors == [("learning_submit", "commit", error)] and entries == []
    assert app.page == "learning_feedback" and app.learning_store.history() == []
    widget.on_press(widget)
    assert course_line.calls(log) == [("finalize", (q["id"], 3), {"fair_new": False})] * 2
    assert [call.name for call in entries] == ["_queue_learning_questions", "continue_learning"]
    assert len(app.learning_store.history()) == 1


def test_c_another_error_on_rating_is_raised(learning_app, monkeypatch):
    """An error on rating that is not a locked database is raised; no alert."""
    app = learning_app
    answered(app, monkeypatch)

    def broken(*args, **kwargs):
        raise ValueError("broken")
    course_line.record_store_calls(app, monkeypatch, "finalize", answers={"finalize": broken})
    with pytest.raises(ValueError):
        course_line.press(app, "记住了")
    assert app.alert.call_args_list == []


# --- d. the ratings -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("caption, rating, value", RATINGS)
def test_d_each_rating_button_calls_the_rating_with_its_name(learning_app, monkeypatch, caption, rating, value):
    """Each of the four buttons of a right, unassisted answer asks for its rating with the question id."""
    app = learning_app
    course_line.record_controls(monkeypatch)
    presses = course_line.record_presses(app, "finish_learning_question")
    q = asked(app)
    course_line.press(app, RIGHT)
    course_line.press(app, caption)
    assert [(call.name, call.args, call.kwargs) for call in presses] == [
        ("finish_learning_question", (rating,), {"question_id": q["id"]})]


@pytest.mark.parametrize("caption, rating, value", RATINGS)
def test_d_each_rating_is_saved_as_its_number_then_queued_then_handed_back(
        learning_app, monkeypatch, caption, rating, value):
    """finalize(id, 1..4, fair_new=False) (no study path), then the item's next questions are queued (the
    item as the store has it now, no early ability), then continue_learning()."""
    app = learning_app
    q = answered(app, monkeypatch)
    log = course_line.record_store_calls(app, monkeypatch, "finalize")
    course_line.record_entries(app, monkeypatch, real=False, log=log,
                               names=("_queue_learning_questions", "continue_learning"))
    course_line.press(app, caption)
    assert [call.name for call in log] == ["finalize", "_queue_learning_questions", "continue_learning"]
    assert log[0].args == (q["id"], value) and log[0].kwargs == {"fair_new": False}
    assert log[1].args == (app.learning_store.get_item(q["target_id"]),) and log[1].kwargs == {}
    assert log[2].args == () and log[2].kwargs == {}
    assert app.learning_store.history()[-1]["rating"] == value


def test_d_with_the_study_path_the_rating_is_fair_new_and_hands_back_to_the_continue_action(
        learning_app, monkeypatch):
    """With the study path present: finalize(..., fair_new=True), then continue_study_path(
    _checkpoint_saved=True) and not continue_learning."""
    app = learning_app
    hand_backs = course_line.record_presses(app, "continue_study_path")
    q = answered(app, monkeypatch)
    log = course_line.record_store_calls(app, monkeypatch, "finalize")
    lines = course_line.record_entries(app, monkeypatch, real=False, names=("continue_learning",))
    course_line.press(app, "很轻松")
    assert course_line.calls(log) == [("finalize", (q["id"], 4), {"fair_new": True})]
    assert [(call.name, call.args, call.kwargs) for call in hand_backs] == [
        ("continue_study_path", (), {"_checkpoint_saved": True})]
    assert lines == []


def test_d_an_early_question_queues_its_next_question_as_early(learning_app, monkeypatch):
    """A question extra practice handed out early: the queueing after its rating names its ability."""
    app = learning_app
    q = answered(app, monkeypatch)
    course_line.permit_early_answers(monkeypatch, q["id"])
    log = course_line.record_entries(app, monkeypatch, real=False,
                                     names=("_queue_learning_questions", "continue_learning"))
    course_line.press(app, "记住了")
    assert log[0].kwargs == {"early_ability": "understanding"}


def test_d_a_second_press_after_the_line_went_on_does_nothing(learning_app, monkeypatch):
    """After a rating the line moves on; pressing the same rating button again rates nothing more."""
    app = learning_app
    answered(app, monkeypatch)
    log = course_line.record_store_calls(app, monkeypatch, "finalize")
    widget = course_line.press(app, "记住了")
    assert app.page != "learning_feedback"
    widget.on_press(widget)
    assert len(course_line.calls(log, "finalize")) == 1 and len(app.learning_store.history()) == 1


def test_d_a_rating_of_an_earlier_result_page_does_nothing(learning_app, monkeypatch):
    """A rating button of the result page of an earlier question, pressed on the next question's result
    page, rates nothing; a rating for another id, or off the result page, does nothing either."""
    app = learning_app
    answered(app, monkeypatch)
    old = course_line.control(app, "记住了")
    second = asked(app, "two")
    course_line.press(app, RIGHT)
    log = course_line.record_store_calls(app, monkeypatch, "finalize")
    old.on_press(old)
    app.finish_learning_question("good", question_id="s5-result:other")
    app.page = "learning_question"
    app.finish_learning_question("good", question_id=second["id"])
    assert log == []


# --- e. the result page -----------------------------------------------------------------------------------------

def test_e_a_right_answer_shows_the_answer_the_explanation_and_four_ratings(learning_app, monkeypatch):
    """Page learning_feedback 作答结果: the green ✓ verdict, the answer (size 20) with its speaker, the
    explanation, 问 AI / 详细, then two rows of ratings."""
    app = learning_app
    answered(app, monkeypatch)
    assert course_line.page(app) == ("learning_feedback", "作答结果")
    assert page_controls(app) == [
        ("row", "", {"align_items": "center", "background_color": "#E1F3E6", "padding": 4, "margin_bottom": 4}),
        ("label", "✓", {"size": 30, "color": "#176B3A", "bold": True, "text_align": "center", "margin_bottom": 0,
                        "flex": 1}),
        *ANSWER_ROW, ("label", EXPLANATION, {}), *HELP_ROW, *RATING_ROWS]


def test_e_a_wrong_answer_shows_the_entered_answer_and_only_save_and_continue(learning_app, monkeypatch):
    """A strict question answered wrong: the red × verdict, the answer, 我的回答：<entered> (MUTED, 12), the
    explanation, 问 AI / 详细, 保存并继续 (primary)."""
    app = learning_app
    answered(app, monkeypatch, WRONG, strict=True)
    assert page_controls(app) == [
        ("row", "", {"align_items": "center", "background_color": "#FCE6E4", "padding": 4, "margin_bottom": 4}),
        ("label", "×", {"size": 30, "color": "#B3261E", "bold": True, "text_align": "center", "margin_bottom": 0,
                        "flex": 1}),
        *ANSWER_ROW, *ENTERED_WRONG, ("label", EXPLANATION, {}), *HELP_ROW, *SAVE]


def test_e_an_unresolved_answer_shows_its_message_and_save_and_continue(learning_app, monkeypatch):
    """An answer neither accepted nor judged wrong: 未评分, the entered answer, the explanation, 问 AI / 详细,
    the reason it is not counted (MUTED, 13), 保存并继续."""
    app = learning_app
    answered(app, monkeypatch, WRONG)
    assert page_controls(app) == [
        ("row", "", {"align_items": "center", "background_color": "#E8EDF0", "padding": 4, "margin_bottom": 4}),
        ("label", "…", {"size": 30, "color": "#52616B", "bold": True, "text_align": "center", "margin_bottom": 0,
                        "width": 52.0}),
        ("label", "未评分", {"size": 13, "color": "#52616B", "flex": 1, "margin_bottom": 0}),
        *ANSWER_ROW, *ENTERED_WRONG, ("label", EXPLANATION, {}), *HELP_ROW,
        ("label", "你的表达可能有其它合理写法。本题暂不计入复习，请与示例核对。", {"size": 13, "color": MUTED}),
        *SAVE]


def test_e_an_assisted_right_answer_offers_only_save_and_continue(learning_app, monkeypatch):
    """A right answer after a hint: no rating buttons, 保存并继续 (it rates "again")."""
    app = learning_app
    course_line.record_controls(monkeypatch)
    presses = course_line.record_presses(app, "finish_learning_question")
    q = asked(app)
    course_line.answer_draft(app)["assisted"] = True
    course_line.press(app, RIGHT)
    controls = page_controls(app)
    assert controls[-len(HELP_ROW) - 1:] == HELP_ROW + SAVE
    assert not {"再学一次", "有些困难", "记住了", "很轻松"} & set(course_line.texts(app))
    course_line.press(app, "保存并继续")
    assert [(call.args, call.kwargs) for call in presses] == [(("again",), {"question_id": q["id"]})]


def test_e_a_revealed_answer_shows_the_assisted_verdict_and_save_and_continue(learning_app, monkeypatch):
    """显示答案: the verdict 已用提示, the answer with its speaker, the explanation, 问 AI / 详细, 保存并继续
    (no entered answer: none was given)."""
    app = learning_app
    answered(app, monkeypatch, "显示答案")
    assert page_controls(app) == [
        ("row", "", {"align_items": "center", "background_color": "#FFF1CF", "padding": 4, "margin_bottom": 4}),
        ("label", "↪", {"size": 30, "color": "#865800", "bold": True, "text_align": "center", "margin_bottom": 0,
                        "width": 52.0}),
        ("label", "已用提示", {"size": 13, "color": "#865800", "flex": 1, "margin_bottom": 0}),
        *ANSWER_ROW, ("label", EXPLANATION, {}), *HELP_ROW, *SAVE]


def test_e_a_device_failure_shows_that_it_is_not_counted(learning_app, monkeypatch):
    """A heard question shown without its audio: 未评分, the answer, the explanation, 问 AI / 详细,
    这题暂不计入复习。 (MUTED, 13), 保存并继续."""
    app = learning_app
    course_line.record_controls(monkeypatch)
    heard(app)
    course_line.press(app, "显示答案")
    assert page_controls(app) == [
        ("row", "", {"align_items": "center", "background_color": "#E8EDF0", "padding": 4, "margin_bottom": 4}),
        ("label", "…", {"size": 30, "color": "#52616B", "bold": True, "text_align": "center", "margin_bottom": 0,
                        "width": 52.0}),
        ("label", "未评分", {"size": 13, "color": "#52616B", "flex": 1, "margin_bottom": 0}),
        *ANSWER_ROW, ("label", EXPLANATION, {}), *HELP_ROW,
        ("label", "这题暂不计入复习。", {"size": 13, "color": MUTED}), *SAVE]


def test_e_a_result_needing_teaching_says_so_below_the_answer(learning_app, monkeypatch):
    """A right result that needs teaching first: 先补学这项内容，再安排独立练习。 (MUTED, 14) after the answer,
    the ratings stay."""
    app = learning_app
    q = answered(app, monkeypatch)
    saved = app.learning_store.get_question(q["id"])["result"]
    app.show_learning_feedback({**saved, "diagnostic_needs_teaching": True})
    assert page_controls(app)[2:] == [
        *ANSWER_ROW, ("label", "先补学这项内容，再安排独立练习。", {"size": 14, "color": MUTED}),
        ("label", EXPLANATION, {}), *HELP_ROW, *RATING_ROWS]


def test_e_an_old_result_outside_formal_review_is_viewed_as_unresolved(learning_app, monkeypatch):
    """When the settlement (looked up at call time) says the result is not formal review, the page views
    it as unresolved (未评分, its reason, 保存并继续) and remembers that view; the stored first answer stays."""
    app = learning_app
    course_line.record_controls(monkeypatch)
    q = asked(app)
    log = course_line.replace_settlement(
        monkeypatch, lambda real, *args, **kwargs: {**real(*args, **kwargs), "settlement_channel": "preview",
                                                     "reason": None})
    course_line.press(app, RIGHT)
    stored = app.learning_store.get_question(q["id"])["result"]
    assert len(log) == 1
    target, shown, submitted = log[0].args
    assert target == app.learning_store.get_item(q["target_id"]) and shown is course_line.course_question(app)
    assert submitted == stored["assessment"] and log[0].kwargs == {"store": app.store}
    texts = course_line.texts(app)
    assert texts[:2] == ["…", "未评分"] and texts[-2:] == ["这题暂不计入复习。", "保存并继续"]
    identity, viewed = course_line.result_view(app)
    assert identity == q["id"] and viewed["correct"] is None and viewed["outcome"] == "unresolved"
    assert viewed["assessment"]["settlement_channel"] == "preview"
    assert stored["correct"] is True and stored["outcome"] == "correct"


def test_e_the_result_view_is_a_copy_of_the_result_drawn(learning_app, monkeypatch):
    """The remembered view is (question id, a copy of the result the page drew)."""
    app = learning_app
    q = answered(app, monkeypatch)
    identity, viewed = course_line.result_view(app)
    stored = app.learning_store.get_question(q["id"])["result"]
    assert identity == q["id"] and viewed == stored
    viewed["correct"] = "changed"
    app.show_learning_feedback(stored)
    assert course_line.result_view(app)[1]["correct"] is True


def test_e_the_result_view_does_not_follow_later_changes_of_the_result(learning_app, monkeypatch):
    """The view is copied when the page is drawn: changing the result handed in afterwards leaves it."""
    app = learning_app
    q = answered(app, monkeypatch)
    result = dict(app.learning_store.get_question(q["id"])["result"])
    app.show_learning_feedback(result)
    result["correct"] = "changed"
    assert course_line.result_view(app)[1]["correct"] is True


def test_e_an_answer_without_japanese_to_read_has_no_speaker_and_does_not_stretch(learning_app, monkeypatch):
    """A meaning question whose prompt has a gap gives nothing to read aloud: the answer label alone (size 20, no flex), no
    speaker, nothing played."""
    app = learning_app
    plays = course_line.record_answer_autoplay(monkeypatch)
    answered(app, monkeypatch, prompt="本を（　）います。")
    controls = page_controls(app)
    assert ("label", RIGHT, {"size": 20, "flex": 0, "text_role": "ui"}) in controls
    assert SPEAKER not in controls and app.platform.play.await_count == 0
    assert [call.args[0] for call in plays] == [""]


def test_e_a_wrong_reading_shows_the_spelling_comparison_instead_of_the_entered_answer(
        learning_app, monkeypatch):
    """A reading answered wrong: 读音不符, the answer shown kana by kana against the entered one (the wrong
    kana in red), the speaker beside it, no 我的回答 row, then the explanation, 问 AI / 详细, 保存并继续."""
    app = learning_app
    course_line.record_controls(monkeypatch)
    q = reading(app)
    app.submit_learning_answer("てかみ", question_id=q["id"])
    width = 28.666666666666664

    def kana(shown, entered, color):
        return [("col", "", {"width": width}),
                ("label", shown, {"size": 20.0, "color": color, "width": width, "text_align": "center",
                                  "wrap": False, "margin_bottom": 0, "text_role": "study"}),
                ("label", entered, {"size": 13.333333333333334, "color": MUTED, "width": width,
                                    "text_align": "center", "wrap": False, "margin_bottom": 0,
                                    "text_role": "study"})]
    assert page_controls(app) == [
        ("row", "", {"align_items": "center", "background_color": "#FCE6E4", "padding": 4, "margin_bottom": 4}),
        ("label", "×", {"size": 30, "color": "#B3261E", "bold": True, "text_align": "center", "margin_bottom": 0,
                        "width": 52.0}),
        ("label", "读音不符", {"size": 13, "color": "#B3261E", "flex": 1, "margin_bottom": 0}),
        ("row", "", {}), ("col", "", {"margin_bottom": 8}), ("row", "", {"margin_bottom": 4, "align_items": "start"}),
        *kana("て", "て", "#1E3430"), *kana("が", "か", "#B3261E"), *kana("み", "み", "#1E3430"),
        SPEAKER, ("label", EXPLANATION, {}), *HELP_ROW, *SAVE]


def test_e_a_revealed_pitch_answer_shows_the_answer_diagram_without_a_speaker(learning_app, monkeypatch):
    """A pitch question shown: the verdict, then the answer's pitch diagram in place of a label (no speaker:
    a pitch answer is not read aloud), no asked text, then the explanation and 保存并继续."""
    app = learning_app
    course_line.record_controls(monkeypatch)
    plays = course_line.record_answer_autoplay(monkeypatch)
    # Independent of the content installed: the course's word table holds this one word.
    words = {("一つ", "ひとつ"): frozenset({"word:synthetic-result:one"})}
    monkeypatch.setattr(result_readings, "_course_words", lambda: words)
    prepare_pitch(app)
    course_line.press(app, "显示答案")
    children = app.body.children
    assert [course_line.made_with(child)[0][:2] for child in children] == [
        ("row", ""), ("col", ""), ("label", "一（ひと）つの詞と助詞。"), ("row", ""), ("button", "保存并继续")]
    assert getattr(children[1], "_pitch_diagram", False)
    assert "🔊" not in course_line.texts(app)
    assert [call.args[0] for call in plays] == [""] and app.platform.play.await_count == 0


def test_e_the_speaker_reads_the_answer_only_on_its_own_result_page(learning_app, monkeypatch):
    """🔊 on the result page speaks the answer's Japanese; the same control pressed after the page was left
    says nothing."""
    app = learning_app
    answered(app, monkeypatch)
    speaker = course_line.press(app, "🔊")
    assert app.speak_text.call_args_list == [((PROMPT,), {})]
    app.page = "learning_question"
    speaker.on_press(speaker)
    assert len(app.speak_text.call_args_list) == 1


def test_e_ask_ai_and_detail_call_their_targets(learning_app, monkeypatch):
    """问 AI -> open_tutor_question(source="learning", question_id=<id>, after_answer=True);
    详细 -> learning_feedback_detail()."""
    app = learning_app
    presses = course_line.record_presses(app, "open_tutor_question", "learning_feedback_detail")
    q = answered(app, monkeypatch)
    course_line.press(app, "问 AI")
    course_line.press(app, "详细")
    assert [(call.name, call.args, call.kwargs) for call in presses] == [
        ("open_tutor_question", (), {"source": "learning", "question_id": q["id"], "after_answer": True}),
        ("learning_feedback_detail", (), {})]


# --- f. the detail page --------------------------------------------------------------------------------------

def test_f_the_detail_page_shows_the_item_and_its_teaching_in_order(learning_app, monkeypatch):
    """Page learning_feedback_detail 详细: the item's title (size 22, the interface role for grammar), its
    error details, the teaching block, the example, the natural translation, 返回结果 (primary)."""
    app = learning_app
    details = course_line.replace_error_details(monkeypatch, lambda q, result: [View(text="〈错误详情〉")])
    answered(app, monkeypatch)
    course_line.press(app, "详细")
    assert course_line.page(app) == ("learning_feedback_detail", "详细")
    assert len(details) == 1
    assert page_controls(app) == [
        ("label", "合成结果", {"size": 22, "text_role": "ui"}),
        ("col", "", {}), ("label", "合成结果", {"wrap": False, "size": 18, "margin_bottom": 12}),
        ("col", "", {"padding": 12, "margin_bottom": 10, "background_color": "#E8F0EC"}),
        ("label", "用法", {"size": 12, "bold": True, "color": MUTED, "margin_bottom": 5}),
        ("label", "• て形＋います", {"wrap": False, "size": 16, "margin_bottom": 5}),
        ("col", "", {}),
        ("label", "", {"size": 20, "text_role": "study", "wrap": False}),
        ("label", "", {"wrap": False, "text_role": "ui"}),
        ("button", "返回结果", {"primary": True})]
    assert course_line.texts(app)[:2] == ["合成结果", "〈错误详情〉"]


def test_f_error_details_are_built_from_the_result_just_viewed(learning_app, monkeypatch):
    """The error details are asked with the question and the remembered view of its result page."""
    app = learning_app
    details = course_line.replace_error_details(monkeypatch, lambda q, result: [])
    q = answered(app, monkeypatch)
    course_line.press(app, "详细")
    shown, result = details[0].args
    assert shown is course_line.course_question(app) and result == course_line.result_view(app)[1]
    assert shown["id"] == q["id"]


def test_f_no_error_details_for_a_result_viewed_for_another_question(learning_app, monkeypatch):
    """When the remembered view belongs to another question id, the detail page builds no error details."""
    app = learning_app
    details = course_line.replace_error_details(monkeypatch, lambda q, result: [View(text="〈错误详情〉")])
    answered(app, monkeypatch)
    course_line.set_course_question(app, {**course_line.course_question(app), "id": "s5-result:other"})
    course_line.press(app, "详细")
    assert details == [] and "〈错误详情〉" not in course_line.texts(app)


def test_f_back_from_the_detail_page_restores_the_result_and_asks_for_no_other_question(
        learning_app, monkeypatch):
    """返回结果 goes back to the very result page (same controls, same texts) without asking the line for
    another question, without a store write and without playing the answer again."""
    app = use_real_detail_back(learning_app)
    q = answered(app, monkeypatch)
    before, body = course_line.texts(app), app.content_slot.children[0]
    view = course_line.result_view(app)
    log = course_line.watch(app, monkeypatch, names=course_line.HAND_BACKS[1:] + ("show_learning_question",))
    stores = course_line.record_store_calls(app, monkeypatch, "submit", "finalize", "save_draft", "current")
    plays = course_line.record_answer_autoplay(monkeypatch)
    course_line.press(app, "详细")
    assert app.page == "learning_feedback_detail"
    course_line.press(app, "返回结果")
    # The page name is restored; the fake screen of this fixture does not restore the title.
    assert app.page == "learning_feedback"
    assert app.content_slot.children[0] is body and course_line.texts(app) == before
    assert [call.name for call in log] == ["screen"] and stores == [] and plays == []
    assert course_line.result_view(app) == view and course_line.course_question(app)["id"] == q["id"]
    assert app.platform.play.await_count == 1


def test_f_material_not_verified_shows_its_page_instead_of_the_details(learning_app, monkeypatch):
    """When the item's teaching is not checked: page learning_feedback_detail 材料待核验 with
    材料尚待核验，仅可预览草稿。 (15), 预览草稿 -> show_learning_detail(item), 返回结果 (primary) -> go_back."""
    app = learning_app
    presses = course_line.record_presses(app, "show_learning_detail")
    q = answered(app, monkeypatch)
    course_line.replace_teaching_allowed(monkeypatch, lambda item: False)
    course_line.press(app, "详细")
    assert course_line.page(app) == ("learning_feedback_detail", "材料待核验")
    assert page_controls(app) == [("label", "材料尚待核验，仅可预览草稿。", {"size": 15}),
                                  ("button", "预览草稿", {}), ("button", "返回结果", {"primary": True})]
    course_line.press(app, "预览草稿")
    widget = course_line.press(app, "返回结果")
    assert [(call.name, call.args) for call in presses] == [
        ("show_learning_detail", (app.learning_store.get_item(q["target_id"]),))]
    assert app.go_back.call_args_list == [((widget,), {})]


# --- g. the answer played once ------------------------------------------------------------------------------

def test_g_the_answer_is_played_once_per_question_in_a_process(learning_app, monkeypatch):
    """Each result page asks to play the answer with the key ("learning", <id>); it is played the first
    time only: drawing the result again, or reopening it after the store was reopened, plays nothing; the
    next question's answer plays."""
    app = learning_app
    plays = course_line.record_answer_autoplay(monkeypatch)
    q = answered(app, monkeypatch)
    stored = app.learning_store.get_question(q["id"])
    app.show_learning_feedback(stored["result"])
    app.learning_store = LearningStore(app.store)
    app.show_learning_question({"question": stored, "draft": stored["draft"], "result": stored["result"]})
    assert app.page == "learning_feedback"
    assert [call.args for call in plays] == [(PROMPT, ("learning", q["id"]))] * 3
    assert [call.result for call in plays] == [True, False, False]
    assert app.platform.play.await_args_list == [((PROMPT,), {})]
    second = asked(app, "two")
    course_line.press(app, RIGHT)
    assert plays[-1].args == (PROMPT, ("learning", second["id"])) and app.platform.play.await_count == 2
