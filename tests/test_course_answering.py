"""Answering a course question without a page (design 13, C1): the service on real stores with a synthetic
lesson, and the session the app side keeps.

Opening a question (a fresh one, one whose skip was interrupted, one already answered), the mark that it was
seen, the drafts, a change of the chosen blocks (saved, nothing to change, a failing save, a question no longer
waiting), the aids, the audio mark, the release of a question this device cannot answer, and the reads of a
question page — each asked of CourseAnswering built from a StudyStore, a LearningStore and three callables.
Then the session object: it is replaced when a field is assigned and never edited, and opening a question
assigns before it writes.
"""
import ast
import sqlite3
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from kanamemory import answer_input, course_answer_runtime, course_planning
from kanamemory.course_line import answer as answer_module
from kanamemory.course_line.answer import PROMPT_PREFS, CourseAnswering
from kanamemory.course_line.answer_model import (AnswerAction, AnswerActionKind, AnswerSession, Busy, EditFailed,
                                                 EditKept, EditLeft, Edited, Graded, OpenAnswered, OpenAsk,
                                                 OpenCancelled, Rated, Recorded, Refused)
from kanamemory.question_support import clean_prompt_preferences
from support.learning_store_fixtures import START, item, question, ready, stores  # noqa: F401
from support import course_support

CID = "grammar:synthetic-answering:one"
KINDS = ["grammar_meaning"]
RIGHT, WRONG = "正在读书", "已经结束"
BLOCKS = ["本を", "読んで", "います"]


def answering(stores, *, fair=False, early=None):
    """The service over the two stores, and the list its noted errors go to."""
    store, learning = stores
    noted = []
    return CourseAnswering(learning, store, fair_new=lambda: fair,
                           early_submit=lambda question_id: dict(early or {}),
                           record_error=lambda *args: noted.append(args)), noted


def head(stores, qid="q1", **question_fields):
    """A synthetic lesson taught a minute ago with one question queued: the course head of that question, and
    the moment it is due."""
    store, learning = stores
    learning.enroll(item(CID, abilities=["understanding"]), now=START)
    due = ready(learning, CID)
    learning.append_questions(question(CID, qid, **{"choices": [RIGHT, WRONG], "feedback": "说明", **question_fields}))
    return learning.current(KINDS, now=due), due


def record(target, *names, failing=()):
    """Record the calls of these methods of ``target``: [(name, args, kwargs), ...]. They still run, except
    the ``failing`` ones, which raise RuntimeError(name)."""
    log = []
    for name in names:
        original = getattr(target, name)

        def call(*args, _name=name, _original=original, **kwargs):
            log.append((_name, args, kwargs))
            if _name in failing:
                raise RuntimeError(_name)
            return _original(*args, **kwargs)
        setattr(target, name, call)
    return log


WRITES = ("save_draft", "submit", "finalize")


# --- opening a question -------------------------------------------------------------------------------------

def test_a_question_is_opened_with_a_copy_of_its_saved_draft_and_nothing_is_written(stores):
    current, _ = head(stores)
    current = dict(current, draft={"answer": "ab"})
    service, _ = answering(stores)
    writes = record(stores[1], *WRITES)
    before = AnswerSession(used=[1], prompt_known=("another", None), feedback_view=("q0", {"correct": True}))
    session = service.begin(before, current)
    assert session is not before and session.question is current["question"]
    assert session.state == {"answer": "ab"} and session.state is not current["draft"]
    assert (session.used, session.prompt_known, session.feedback_view) == (
        before.used, before.prompt_known, before.feedback_view)
    assert (before.question, before.state) == (None, None) and writes == []


def test_a_fresh_question_starts_from_what_its_draft_holds(stores):
    current, _ = head(stores)
    service, _ = answering(stores)
    drafted = dict(current, draft={"audio_ok": True, "answer": "ab", "parts": ["a", "b"]})
    session = service.begin(AnswerSession(), drafted)
    writes = record(stores[1], *WRITES)
    opened = service.settle_open(session, drafted)
    assert isinstance(opened, OpenAsk) and opened.session is not session and writes == []
    assert (opened.session.audio_ok, opened.session.answer, opened.session.parts) == (True, "ab", ["a", "b"])
    assert opened.session.parts is not session.state["parts"] and opened.session.state is session.state
    empty = service.settle_open(service.begin(AnswerSession(), current), current).session
    assert (empty.audio_ok, empty.answer, empty.parts, empty.used) == (False, "", [], None)


def test_a_skip_interrupted_between_its_two_writes_is_finished_when_the_question_is_opened(stores):
    _, due = head(stores)
    store, learning = stores
    learning.submit("q1", "", correct=None, outcome="cancelled")
    current = learning.current(KINDS, now=due)
    assert current["type"] == "question" and current["result"]["outcome"] == "cancelled"
    service, _ = answering(stores)
    session = service.begin(AnswerSession(), current)
    writes = record(learning, *WRITES)
    assert service.settle_open(session, current) == OpenCancelled()
    assert writes == [("finalize", ("q1",), {"rating": None})]
    assert (session.audio_ok, session.parts) == (None, None)
    after = learning.current(KINDS, now=due)
    assert not (after and after.get("type") == "question" and after["question"]["id"] == "q1")


def test_a_question_already_answered_leads_to_its_result_and_writes_nothing(stores):
    _, due = head(stores)
    store, learning = stores
    learning.submit("q1", RIGHT, True, assisted=False, outcome="correct", presentation={
        "prompt_furigana_visible": False, "prompt_accent_visible": False, "audio_used": False})
    current = learning.current(KINDS, now=due)
    service, _ = answering(stores)
    session = service.begin(AnswerSession(), current)
    writes = record(learning, *WRITES)
    opened = service.settle_open(session, current)
    assert opened == OpenAnswered(current["result"]) and opened.result["outcome"] == "correct" and writes == []


# --- seen, drafts ---------------------------------------------------------------------------------------------

def test_a_drawn_question_is_noted_as_seen_once(stores):
    current, _ = head(stores)
    service, _ = answering(stores)
    session = service.begin(AnswerSession(), dict(current, draft={"answer": "ab"}))
    state = session.state
    stores[1].save_draft("q1", {"answer": "ab"})
    writes = record(stores[1], *WRITES)
    service.presented(session)
    assert writes == [("save_draft", ("q1", {"presented": True}), {})]
    assert session.state is state and state == {"answer": "ab", "presented": True}
    assert stores[1].get_question("q1")["draft"] == {"answer": "ab", "presented": True}
    service.presented(session)
    assert len(writes) == 1


def test_a_draft_is_saved_as_the_store_saves_it(stores):
    head(stores)
    service, _ = answering(stores)
    saved = service.save_draft("q1", {"answer": "ab", "assisted": True})
    assert (saved["status"], saved["draft"]) == ("pending", {"answer": "ab", "assisted": True})
    assert stores[1].get_question("q1")["draft"] == saved["draft"]


# --- the chosen blocks ---------------------------------------------------------------------------------------

def blocks_session(stores):
    current, due = head(stores, input_mode="reorder", choices=BLOCKS, answer="".join(BLOCKS))
    service, noted = answering(stores)
    opened = service.settle_open(service.begin(AnswerSession(), current), current)
    return service, noted, opened.session, due


def test_a_block_is_added_and_taken_back_and_each_change_is_saved_at_once(stores):
    service, noted, session, _ = blocks_session(stores)
    writes = record(stores[1], *WRITES)
    added = service.edit_parts(session, 1)
    assert added == Edited({"used": [1], "parts": ["読んで"], "answer": "読んで"})
    assert writes == [("save_draft", ("q1", {"used": [1], "parts": ["読んで"], "answer": "読んで"}), {})]
    assert session.state == {} and session.parts == [] and noted == []      # the session is the caller's to renew
    session.state.update(added.draft)
    assert service.edit_parts(session, 1) == EditKept()                     # that block is already used
    second = service.edit_parts(session, 0)
    assert second.draft["parts"] == ["読んで", "本を"] and second.draft["used"] == [1, 0]
    session.state.update(second.draft)
    back = service.edit_parts(session, None)
    assert back.draft["parts"] == ["読んで"] and stores[1].get_question("q1")["draft"] == back.draft
    assert len(writes) == 3


def test_taking_back_with_nothing_chosen_changes_nothing(stores):
    service, _, session, _ = blocks_session(stores)
    writes = record(stores[1], *WRITES)
    assert service.edit_parts(session, None) == EditKept() and writes == []


def test_a_block_change_that_cannot_be_saved_is_noted_and_reported(stores):
    service, noted, session, _ = blocks_session(stores)
    record(stores[1], "save_draft", failing=("save_draft",))
    failed = service.edit_parts(session, 0)
    assert isinstance(failed, EditFailed) and str(failed.error) == "save_draft"
    assert noted == [("learning_word_blocks", "save", failed.error)]
    assert session.state == {}


def test_a_block_change_on_a_question_that_was_answered_meanwhile_leaves_the_page(stores):
    service, _, session, _ = blocks_session(stores)
    stores[1].submit("q1", "".join(BLOCKS), True, assisted=False, outcome="correct", presentation={
        "prompt_furigana_visible": False, "prompt_accent_visible": False, "audio_used": False})
    assert service.edit_parts(session, 0) == EditLeft()


# --- aids, audio, release ---------------------------------------------------------------------------------------

def test_an_aid_marks_the_same_draft_and_writes_nothing_by_itself(stores):
    current, _ = head(stores)
    service, _ = answering(stores)
    session = service.begin(AnswerSession(), dict(current, draft={"answer": "ab"}))
    state = session.state
    writes = record(stores[1], *WRITES)
    assert service.mark_assisted(session) is session
    assert session.state is state and state == {"answer": "ab", "assisted": True} and writes == []


def test_an_aid_on_a_pitch_question_goes_on_with_the_editors_draft(stores, monkeypatch):
    current, _ = head(stores)
    service, _ = answering(stores)
    session = service.begin(AnswerSession(), dict(current, draft={"answer": "ab"}))
    old_state = session.state
    drafts = []
    monkeypatch.setattr(answer_module, "is_pitch_question", lambda question: True)
    monkeypatch.setattr(answer_module, "pitch_editor_draft",
                        lambda question, draft: drafts.append((question, draft)) or {"editor": True})
    marked = service.mark_assisted(session)
    assert marked is not session and marked.question is session.question
    assert marked.state == {"editor": True, "assisted": True} and drafts == [(session.question, old_state)]
    assert session.state is old_state and old_state == {"answer": "ab"}


def test_the_audio_mark_is_saved_before_the_session_carries_it(stores):
    current, _ = head(stores)
    service, _ = answering(stores)
    session = service.settle_open(service.begin(AnswerSession(), dict(current, draft={"answer": "ab"})),
                                  dict(current, draft={"answer": "ab"})).session
    state = session.state
    seen = []
    original = stores[1].save_draft

    def save_draft(question_id, draft):
        seen.append((question_id, dict(draft), dict(state), session.audio_ok))
        return original(question_id, draft)
    stores[1].save_draft = save_draft
    heard = service.audio_played(session, session.question)
    assert seen == [("q1", {"answer": "ab", "audio_ok": True}, {"answer": "ab"}, False)]
    assert heard is not session and heard.audio_ok is True and heard.state is state
    assert state == {"answer": "ab", "audio_ok": True} and session.audio_ok is False


def test_an_audio_mark_that_cannot_be_saved_leaves_the_session_as_it_was(stores):
    current, _ = head(stores)
    service, _ = answering(stores)
    session = service.settle_open(service.begin(AnswerSession(), current), current).session
    record(stores[1], "save_draft", failing=("save_draft",))
    with pytest.raises(RuntimeError):
        service.audio_played(session, session.question)
    assert session.state == {} and session.audio_ok is False


def test_a_question_this_device_can_answer_is_not_released(stores):
    current, _ = head(stores)
    service, _ = answering(stores)
    session = service.begin(AnswerSession(), current)
    writes = record(stores[1], *WRITES)
    assert service.skip(session) is False and writes == []


def test_a_question_this_device_cannot_answer_is_released_with_its_two_writes_in_order(stores, monkeypatch):
    current, due = head(stores)
    service, _ = answering(stores)
    session = service.begin(AnswerSession(), dict(current, draft={"answer": "半"}))
    asked = []
    monkeypatch.setattr(answer_input, "available",
                        lambda question, *, system_japanese=False: asked.append((question, system_japanese)) and False)
    writes = record(stores[1], *WRITES)
    assert service.skip(session) is True
    assert asked == [(session.question, False)]
    assert writes == [("submit", ("q1", "半"), {"correct": None, "outcome": "cancelled"}),
                      ("finalize", ("q1",), {"rating": None})]
    after = stores[1].current(KINDS, now=due)
    assert not (after and after.get("type") == "question" and after["question"]["id"] == "q1")


# --- what a question page reads ---------------------------------------------------------------------------------

def test_the_known_ids_are_read_through_the_planning_module_at_the_moment_of_the_call(stores, monkeypatch):
    head(stores)
    service, _ = answering(stores)
    assert service.known_ids() == course_planning.known_ids(stores[0].queries)
    asked = []
    monkeypatch.setattr(course_planning, "known_ids", lambda queries: asked.append(queries) or {"word:known"})
    assert service.known_ids() == {"word:known"}
    assert [type(queries) for queries in asked] == [type(stores[0].queries)]


def test_the_prompt_preferences_and_the_keyboard_setting_are_read_as_saved_now(stores):
    service, _ = answering(stores)
    assert PROMPT_PREFS == "prompt_display_v1"
    assert service.prompt_preferences() == clean_prompt_preferences({})
    stores[0].set_setting(PROMPT_PREFS, {"furigana": False, "number": True})
    assert service.prompt_preferences() == clean_prompt_preferences({"furigana": False, "number": True})
    assert service.system_japanese() is False
    stores[0].set_setting(answer_input.SETTING, {"system_japanese": True})
    assert service.system_japanese() is True


def test_the_replaced_question_and_the_lesson_of_the_question(stores):
    current, _ = head(stores)
    service, _ = answering(stores)
    reads = record(stores[1], "get_question", "get_item")
    assert service.replaced(current["question"]) is None and reads == []
    old = service.replaced(dict(current["question"], replacement_of="q1"))
    assert old["id"] == "q1" and reads == [("get_question", ("q1",), {})]
    assert service.replaced(dict(current["question"], replacement_of="gone")) is None
    session = service.begin(AnswerSession(), current)
    assert service.hint_item(session) == stores[1].get_item(CID)
    assert reads[-1] == ("get_item", (CID,), {})


# --- the session the app keeps ------------------------------------------------------------------------------------

def test_a_session_starts_empty_and_its_fields_that_pages_set_are_none_until_then():
    assert [(field.name, getattr(AnswerSession(), field.name)) for field in fields(AnswerSession)] == [
        ("question", None), ("state", None), ("answer", ""), ("parts", None), ("used", None), ("audio_ok", None),
        ("prompt_known", (None, None)), ("feedback_view", (None, {}))]
    assert AnswerSession() != AnswerSession()      # compared by identity: a page holds ITS session


def test_assigning_a_field_gives_the_app_a_new_session_and_leaves_the_old_one_alone():
    app = SimpleNamespace()
    empty = course_answer_runtime.answer_session(app)
    assert isinstance(empty, AnswerSession) and not hasattr(app, "_course_answer")
    draft = {"answer": "ab"}
    course_answer_runtime.assign(app, question={"id": "q1"}, state=draft)
    first = course_answer_runtime.answer_session(app)
    assert first is course_answer_runtime.answer_session(app) and first.state is draft
    course_answer_runtime.set_draft(app, {"answer": "cd"})
    second = course_answer_runtime.answer_session(app)
    assert second is not first and second.question is first.question
    assert (first.state, second.state) == ({"answer": "ab"}, {"answer": "cd"})
    kept = first
    course_answer_runtime.set_session(app, kept)           # what Back does with the session a page was left with
    assert course_answer_runtime.answer_session(app) is kept


def test_the_question_an_answer_control_belongs_to():
    question, guide = {"id": "q1"}, {"id": "g1"}
    app = SimpleNamespace()
    course_support.set_guide_question(app, guide)
    course_answer_runtime.assign(app, question=question)
    assert course_answer_runtime.shown_question(app) is question
    assert course_answer_runtime.shown_question(app, guided=True) is guide
    assert course_answer_runtime.shown_question(SimpleNamespace(), guided=True) is None
    assert course_answer_runtime.shown_question(SimpleNamespace()) is None


def line_app(stores, log):
    """An object with what opening a question reads of the app: the two stores and the two line entries."""
    store, learning = stores
    return SimpleNamespace(
        store=store, learning_store=learning, _learning_input="an old field",
        continue_added_learning=lambda: log.append(("continue_added_learning", course_answer_runtime.answer_session(app_holder[0]).question["id"])),
        show_learning_feedback=lambda result: log.append(("show_learning_feedback", result["outcome"])))


app_holder = [None]


def test_opening_a_question_assigns_the_session_before_the_interrupted_skip_is_finished(stores):
    _, due = head(stores)
    store, learning = stores
    learning.submit("q1", "", correct=None, outcome="cancelled")
    current = learning.current(KINDS, now=due)
    log = []
    app = app_holder[0] = line_app(stores, log)
    original = learning.finalize

    def finalize(question_id, **kwargs):
        session = course_answer_runtime.answer_session(app)
        log.append(("finalize", question_id, kwargs, session.question["id"], session.state, app._learning_input))
        return original(question_id, **kwargs)
    learning.finalize = finalize
    assert course_answer_runtime.open_question(app, current) is False
    assert log == [("finalize", "q1", {"rating": None}, "q1", {}, None), ("continue_added_learning", "q1")]


def test_opening_an_answered_question_shows_its_result_and_a_fresh_one_is_left_to_the_page(stores):
    _, due = head(stores)
    store, learning = stores
    log = []
    app = app_holder[0] = line_app(stores, log)
    fresh = learning.current(KINDS, now=due)
    assert course_answer_runtime.open_question(app, fresh) is True and log == []
    session = course_answer_runtime.answer_session(app)
    assert (session.question["id"], session.state, session.answer, session.parts, session.audio_ok) == (
        "q1", {}, "", [], False)
    assert app._learning_input is None
    learning.submit("q1", RIGHT, True, assisted=False, outcome="correct", presentation={
        "prompt_furigana_visible": False, "prompt_accent_visible": False, "audio_used": False})
    assert course_answer_runtime.open_question(app, learning.current(KINDS, now=due)) is False
    assert log == [("show_learning_feedback", "correct")]


def test_an_aid_assigns_then_saves_and_the_pitch_editors_draft_is_saved_then_kept(stores):
    current, _ = head(stores)
    log = []
    app = app_holder[0] = line_app(stores, log)
    course_answer_runtime.open_question(app, current)
    state = course_answer_runtime.answer_session(app).state
    writes = record(stores[1], *WRITES)
    course_answer_runtime.assist(app)
    assert writes == [("save_draft", ("q1", {"assisted": True}), {})]
    assert course_answer_runtime.answer_session(app).state is state and state == {"assisted": True}
    editor = {"cells": [1], "assisted": True}
    course_answer_runtime.keep_draft(app, "q1", editor)
    assert writes[-1] == ("save_draft", ("q1", editor), {})
    assert course_answer_runtime.answer_session(app).state is editor
    course_answer_runtime.audio_played(app, current["question"])
    assert course_answer_runtime.answer_session(app).audio_ok is True and editor["audio_ok"] is True


# --- the submit ---------------------------------------------------------------------------------------------------

PLAIN = {"prompt_furigana_visible": False, "prompt_accent_visible": False, "audio_used": False}


def opened(stores, **question_fields):
    """The service, its noted errors and the session of a fresh synthetic question."""
    current, due = head(stores, **question_fields)
    service, noted = answering(stores)
    session = service.settle_open(service.begin(AnswerSession(), current), current).session
    return service, noted, session, due


def test_a_submit_is_refused_before_anything_is_judged_or_written(stores, monkeypatch):
    service, _, session, _ = opened(stores, audio_text="今、本を読んでいます。")
    writes = record(stores[1], *WRITES)
    judged = []
    monkeypatch.setattr(answer_module, "grade_learning_answer",
                        lambda question, answer: judged.append(answer) or {"status": "correct"})
    assert service.grade(session, RIGHT, revealed=False) == Refused("请先成功播放题目。")
    heard = replace(session, audio_ok=True)
    assert service.grade(heard, "   ", revealed=False) == Refused("请先作答。")
    assert judged == [] and writes == []
    assert service.grade(heard, RIGHT, revealed=False) == Graded({"status": "correct"}, "correct")
    # The learner asked for the answer: neither refusal applies; a question never heard is a device failure.
    assert service.grade(heard, "", revealed=True) == Graded({"status": "correct"}, "revealed")
    assert service.grade(session, "", revealed=True) == Graded({"status": "correct"}, "device_failure")
    assert writes == []


def test_an_unfinished_pitch_answer_is_refused_with_the_judges_words(stores, monkeypatch):
    service, _, session, _ = opened(stores)
    monkeypatch.setattr(answer_module, "is_pitch_question", lambda question: True)
    verdicts = iter([{"status": "empty", "message": "请完成这道题再检查。"}, {"status": "invalid"},
                     {"status": "invalid", "message": "无法读取"}])
    monkeypatch.setattr(answer_module, "grade_learning_answer", lambda question, answer: next(verdicts))
    assert service.grade(session, "x", revealed=False) == Refused("请完成这道题再检查。")
    assert service.grade(session, "x", revealed=False) == Refused("请完成作答。")
    assert service.grade(session, "x", revealed=True) == Graded({"status": "invalid", "message": "无法读取"}, "revealed")


def test_the_real_judge_calls_a_right_choice_correct(stores):
    service, _, session, _ = opened(stores)
    graded = service.grade(session, RIGHT, revealed=False)
    assert isinstance(graded, Graded) and graded.outcome == "correct" and graded.result["status"] == "correct"
    assert service.grade(session, WRONG, revealed=False).outcome == service.grade(session, WRONG, revealed=False).result["status"]


@pytest.mark.parametrize("outcome, status, correct", [
    ("correct", "correct", True), ("incorrect", "incorrect", False), ("unresolved", "unresolved", None),
    ("revealed", "correct", True), ("revealed", "incorrect", False), ("device_failure", "correct", None)])
def test_the_record_of_an_answer(stores, monkeypatch, outcome, status, correct):
    service, _, session, _ = opened(stores)
    session.state["assisted"] = outcome == "revealed"
    plans = []
    monkeypatch.setattr(answer_module, "prompt_support", lambda question, preferences, known: plans.append(
        (question, preferences, known)) or {"furigana_visible": True, "accent_visible": False})
    writes = record(stores[1], *WRITES)
    recorded = service.record(session, RIGHT, Graded({"status": status}, outcome))
    assert isinstance(recorded, Recorded) and recorded.result["outcome"] == outcome
    assert writes == [("submit", ("q1", RIGHT, correct), {
        "assisted": outcome == "revealed", "outcome": outcome,
        "presentation": {"prompt_furigana_visible": True, "prompt_accent_visible": False, "audio_used": False}})]
    assert plans == [(session.question, service.prompt_preferences(), None)]


def test_the_record_uses_the_plan_of_the_prompt_that_was_drawn_for_this_very_question(stores, monkeypatch):
    service, _, session, _ = opened(stores, qid="q1")
    asked = []
    monkeypatch.setattr(answer_module, "prompt_support", lambda question, preferences, known: asked.append(known) or {
        "furigana_visible": False, "accent_visible": True})
    known = {"word:known"}
    drawn = replace(session, prompt_known=(session.question, known), audio_ok=True)
    stores[1].submit = lambda *args, **kwargs: {"args": args, "kwargs": kwargs}
    recorded = service.record(drawn, 12, Graded({"status": "correct"}, "correct"))
    assert asked == [known]
    assert recorded.result["args"] == ("q1", "12", True)             # the answer is recorded as text
    assert recorded.result["kwargs"]["presentation"] == {
        "prompt_furigana_visible": False, "prompt_accent_visible": True, "audio_used": True}
    other = replace(session, prompt_known=(dict(session.question), known))      # an equal question, another object
    service.record(other, RIGHT, Graded({"status": "correct"}, "correct"))
    assert asked == [known, None]


def test_a_question_handed_out_early_is_recorded_with_its_permit(stores):
    current, _ = head(stores)
    store, learning = stores
    asked = []
    service = CourseAnswering(learning, store, fair_new=lambda: False,
                              early_submit=lambda question_id: asked.append(question_id) or {"early": True},
                              record_error=lambda *args: None)
    session = service.settle_open(service.begin(AnswerSession(), current), current).session
    seen = {}
    learning.submit = lambda *args, **kwargs: seen.update(kwargs) or {"outcome": "correct"}
    service.record(session, RIGHT, Graded({"status": "correct"}, "correct"))
    assert asked == ["q1"] and seen["early"] is True


def test_a_locked_database_records_nothing_and_any_other_error_is_raised(stores):
    service, noted, session, _ = opened(stores)
    locked = sqlite3.OperationalError("database is locked")

    def submit(*args, **kwargs):
        raise locked
    stores[1].submit = submit
    assert service.record(session, RIGHT, Graded({"status": "correct"}, "correct")) == Busy()
    assert noted == [("learning_submit", "save", locked)]
    assert stores[1].get_question("q1")["result"] is None

    def broken(*args, **kwargs):
        raise ValueError("another failure")
    stores[1].submit = broken
    with pytest.raises(ValueError):
        service.record(session, RIGHT, Graded({"status": "correct"}, "correct"))
    assert len(noted) == 1


# --- the result and the rating ---------------------------------------------------------------------------------------

def answered(stores, answer=RIGHT):
    service, noted, session, due = opened(stores)
    recorded = service.record(session, answer, service.grade(session, answer, revealed=False))
    return service, noted, session, recorded.result, due


def test_the_result_of_a_right_answer_offers_the_four_ratings(stores):
    service, _, session, result, _ = answered(stores)
    reads = record(stores[1], "get_item", *WRITES)
    shown, page = service.feedback(session, result)
    assert reads == [("get_item", (CID,), {})]                         # the lesson is read once; nothing is written
    assert shown is not session and shown.feedback_view == ("q1", result) and shown.feedback_view[1] is not result
    assert session.feedback_view == (None, {})
    assert (page.page, page.title, page.question, page.result) == ("learning_feedback", "作答结果", session.question, result)
    assert (page.answer_kind, page.answer_text, page.entered) == ("label", RIGHT, RIGHT)
    assert (page.structure_error, page.needs_teaching, page.entered_shown, page.diagnosis, page.unresolved) == (
        None, None, None, None, None)
    assert page.explanation == "说明" and page.save is None
    assert [(button.caption, button.action.rating, button.action.question_id) for button in page.ratings] == [
        ("再学一次", "again", "q1"), ("有些困难", "hard", "q1"), ("记住了", "good", "q1"), ("很轻松", "easy", "q1")]
    assert all(button.action.kind is AnswerActionKind.RATE for button in page.ratings)
    assert (page.tutor.caption, page.tutor.action) == ("问 AI", AnswerAction(AnswerActionKind.TUTOR, question_id="q1"))
    assert (page.detail.caption, page.detail.action) == ("详细", AnswerAction(AnswerActionKind.DETAIL))


def test_the_result_of_an_answer_that_does_not_count_offers_only_save(stores):
    service, _, session, result, _ = answered(stores, WRONG)
    _, page = service.feedback(session, result)
    assert page.ratings is None
    assert (page.save.caption, page.save.action) == (
        "保存并继续", AnswerAction(AnswerActionKind.RATE, rating="again", question_id="q1"))
    assert page.entered_shown == WRONG and page.entered == WRONG
    assert (page.unresolved is not None) == (result["outcome"] in {"unresolved", "device_failure", "invalid"})


def test_an_old_result_that_no_longer_settles_formally_is_shown_as_unresolved_and_stays_as_stored(stores, monkeypatch):
    from kanamemory import assessment_policy
    service, _, session, result, _ = answered(stores)
    stored = dict(result)
    decision = {"settlement_channel": "observation", "assessment_eligible": False, "reason": "合成原因"}
    asked = []
    monkeypatch.setattr(assessment_policy, "settlement_decision", lambda item, question, submitted, **kwargs:
                        asked.append((item["id"], question["id"], submitted, kwargs)) or decision)
    shown, page = service.feedback(session, result)
    assert asked == [(CID, "q1", result.get("assessment"), {"store": stores[0]})]
    assert (page.result["correct"], page.result["outcome"], page.result["assessment"]) == (None, "unresolved", decision)
    assert shown.feedback_view == ("q1", page.result) and page.ratings is None and page.unresolved is not None
    assert result == stored and stores[1].get_question("q1")["result"]["outcome"] == "correct"


@pytest.mark.parametrize("rating, number", [("again", 1), ("hard", 2), ("good", 3), ("easy", 4)])
def test_a_rating_finishes_the_question_and_reads_its_lesson(stores, rating, number):
    current, _ = head(stores)
    store, learning = stores
    service = CourseAnswering(learning, store, fair_new=lambda: True, early_submit=lambda question_id: {},
                              record_error=lambda *args: None)
    session = service.settle_open(service.begin(AnswerSession(), current), current).session
    service.record(session, RIGHT, service.grade(session, RIGHT, revealed=False))
    calls = record(learning, "finalize", "get_item")
    rated = service.rate(session, rating)
    assert calls == [("finalize", ("q1", number), {"fair_new": True}), ("get_item", (CID,), {})]
    assert rated == Rated(learning.get_item(CID))


def test_a_locked_database_keeps_the_rating_for_another_press(stores):
    service, noted, session, _, _ = answered(stores)
    locked = sqlite3.OperationalError("database is locked")
    original = stores[1].finalize

    def finalize(*args, **kwargs):
        raise locked
    stores[1].finalize = finalize
    assert service.rate(session, "good") == Busy() and noted == [("learning_submit", "commit", locked)]
    stores[1].finalize = original
    assert isinstance(service.rate(session, "good"), Rated)
    with pytest.raises(KeyError):
        service.rate(session, "splendid")


# --- the app side of the submit, the result and the rating -------------------------------------------------------------

def result_app(stores, log):
    store, learning = stores
    app = SimpleNamespace(store=store, learning_store=learning, page="learning_question", _learning_input=None)
    app.platform = SimpleNamespace(stop=lambda: log.append("stop"))
    app.show_learning_feedback = lambda result: log.append(("feedback", result["outcome"]))
    app.alert = lambda text: log.append(("alert", text))
    app._queue_learning_questions = lambda item, **kwargs: log.append(("queue", item["id"], kwargs))
    app.continue_learning = lambda: log.append("continue_learning")
    return app


def test_the_app_side_stops_speech_between_the_judgment_and_the_record_and_then_shows_the_result(stores):
    current, _ = head(stores)
    log = []
    app = result_app(stores, log)
    course_answer_runtime.open_question(app, current)
    assert course_answer_runtime.submit(app, "  ", revealed=False) == "请先作答。" and log == []
    original = stores[1].submit
    stores[1].submit = lambda *args, **kwargs: log.append("submit") or original(*args, **kwargs)
    assert course_answer_runtime.submit(app, RIGHT, revealed=False) is None
    assert log == ["stop", "submit", ("feedback", "correct")]


def test_the_app_side_says_when_the_database_is_busy(stores):
    current, _ = head(stores)
    log = []
    app = result_app(stores, log)
    course_answer_runtime.open_question(app, current)

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    original_submit, stores[1].submit = stores[1].submit, locked
    assert course_answer_runtime.submit(app, RIGHT, revealed=False) == "数据库正忙，答案尚未提交，请稍后再点提交。"
    assert log == ["stop"]
    stores[1].submit = original_submit
    assert course_answer_runtime.submit(app, RIGHT, revealed=False) is None
    original_finalize, stores[1].finalize = stores[1].finalize, locked
    course_answer_runtime.rate(app, "good")
    assert log[-1] == ("alert", "数据库正忙，评级尚未保存。请稍后再点一次。")
    stores[1].finalize = original_finalize
    course_answer_runtime.rate(app, "good")
    assert log[-2:] == [("queue", CID, {}), "continue_learning"]


def test_a_pressed_button_of_the_result_pages_makes_its_call():
    log = []
    app = SimpleNamespace(
        finish_learning_question=lambda rating, *, question_id=None: log.append(("finish", rating, question_id)),
        open_tutor_question=lambda **kwargs: log.append(("tutor", kwargs)),
        learning_feedback_detail=lambda: log.append("detail"),
        go_back=lambda widget: log.append(("back", widget)))
    for action in (AnswerAction(AnswerActionKind.RATE, rating="hard", question_id="q1"),
                   AnswerAction(AnswerActionKind.TUTOR, question_id="q1"), AnswerAction(AnswerActionKind.DETAIL),
                   AnswerAction(AnswerActionKind.BACK)):
        course_answer_runtime.run_answer_action(app, action, "the button")
    assert log == [("finish", "hard", "q1"),
                   ("tutor", {"source": "learning", "question_id": "q1", "after_answer": True}),
                   "detail", ("back", "the button")]


# --- the imports -----------------------------------------------------------------------------------------------

SOURCE_DIR = Path(course_answer_runtime.__file__).parent
OUTER = ("toga", "travertino", "kanamemory.app", "kanamemory.ui_components")


def _imports(path, package):
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package.split(".")[:len(package.split(".")) - node.level + 1] if node.level else []
            names.add(".".join([*base, node.module] if node.module else base))
    return names


def _within(module, names):
    return any(module == name or module.startswith(name + ".") for name in names)


def test_the_session_module_is_pure_and_the_service_and_the_app_side_module_import_no_controls_or_app():
    assert _imports(SOURCE_DIR / "course_line/answer_model.py", "kanamemory.course_line") == {
        "__future__", "dataclasses", "enum"}
    service_imports = _imports(SOURCE_DIR / "course_line/answer.py", "kanamemory.course_line")
    assert not [module for module in service_imports if _within(module, (*OUTER, "kanamemory.course_line.ui"))]
    # answer_result_ui holds the shared result typography only (pure); no other page module is imported.
    assert [module for module in service_imports if module.endswith("_ui")] == ["kanamemory.answer_result_ui"]
    path = SOURCE_DIR / "course_answer_runtime.py"
    assert not [module for module in _imports(path, "kanamemory") if _within(module, OUTER) or module.endswith("_ui")]
    module_level = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ImportFrom):
            module_level.add(node.module)
    assert "course_line.ui" not in module_level      # the page module is imported only when a page is drawn
