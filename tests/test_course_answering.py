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
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest

from kanamemory import answer_input, course_answer_runtime, course_planning
from kanamemory.course_line import answer as answer_module
from kanamemory.course_line.answer import PROMPT_PREFS, CourseAnswering
from kanamemory.course_line.answer_model import (AnswerSession, EditFailed, EditKept, EditLeft, Edited, OpenAnswered,
                                                 OpenAsk, OpenCancelled)
from kanamemory.question_support import clean_prompt_preferences
from support.learning_store_fixtures import START, item, question, ready, stores  # noqa: F401

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
    app = SimpleNamespace(_learning_guide=guide)
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
        "__future__", "dataclasses"}
    for path, package in ((SOURCE_DIR / "course_line/answer.py", "kanamemory.course_line"),
                          (SOURCE_DIR / "course_answer_runtime.py", "kanamemory")):
        found = _imports(path, package)
        assert not [module for module in found if _within(module, (*OUTER, "kanamemory.course_line.ui"))]
        assert not [module for module in found if module.endswith("_ui")]
