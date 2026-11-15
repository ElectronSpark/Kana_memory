"""答题 (answering one course question, up to the submit): what is written when, what each control does.

The course line shows one question at a time (app.show_learning_question), keeps a draft of what the learner
typed, chose or put together, records the aids used (再看讲解, 显示答案, the tutor) and whether the question's
audio played, and hands the answer to the submit. This file pins that behaviour as it is today, so that moving
the answering state out of the app object cannot change it unseen:

  a. a question whose skip (换一道题) was interrupted between its two writes is finished when it is shown again:
     finalize(rating=None) once, the hand-back to the line, no result page;
  b. the draft's "presented" mark is written after the page is drawn, once per question, and not when drawing
     fails;
  c. the draft: typing is saved at most once per pause, forced out before a submit, on leaving and on Back,
     retried after a locked database with its status text; a block edit is saved at once and leaves the page
     when the saved question is no longer pending;
  d. the aids: 再看讲解 and 显示答案 mark the draft assisted before anything is shown, and the mark stays; the
     audio mark is saved before the page counts the audio as heard, and a failing save leaves it unheard with
     its status text;
  e. a control of an earlier drawing of the same question does nothing (a gap choice, the text field, a block
     edit); the audio button is the app's own and still plays;
  f. Back from 作答设置 to the kept question page, and back from the tutor, shows and saves the current draft,
     the tutor's mark included;
  g. a problem report taken on the question page captures the question state under the same keys and values;
  h. for each input mode a synthetic lesson can reach (choice, typed text, blocks, gaps, the pitch editor, a
     heard question): the page's controls with their texts and style arguments, what each control calls, and
     how often one drawing reads the prompt preferences, the learner's known ids and the enabled kinds.

The answering state is read only through tests/support/course_support.py. Expected values are what the code
does, including what looks odd (each such test says so). Public: synthetic lessons only (ids
"grammar:s5-answer:*", the synthetic pitch and gap lessons of the shared support modules).
"""
import sqlite3
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import feedback_context
from kanamemory import question_readings
from kanamemory import ui_components as u
from kanamemory.app import KanaMemory
from kanamemory.pitch_practice import editor_draft as pitch_editor_draft
from support import course_support as course_line
from support.app_fixtures import elapse_draft_delay, question_app, setup_app  # noqa: F401
from support.fake_ui import View, descendants
from support.learning_fixtures import learning_app, use_real_detail_back  # noqa: F401
from support.learning_pitch_controls_support import prepare as prepare_pitch
from support.learning_store_fixtures import item, question
from support.multi_cloze_integration_support import KIND as GAP_KIND, cloze_case, enqueue  # noqa: F401

PREFS = "learning_preferences_v1"
BUSY = "草稿暂未保存（数据库正忙），继续输入或提交时会重试。"
BLOCKS_UNSAVED = "输入未保存，请重试。"
AUDIO_UNSAVED = "语音已播放，但播放状态未保存。请重试；本题尚未评分。"
AUDIO_FAILED = "播放失败，请检查日语语音后重试；本题尚未评分。"
EMPTY_BLOCKS = "点击词块组成句子"
NEEDS_INPUT = "这道旧题需要额外的日文输入。可换题，草稿保留且不计错。"
PROMPT = "今、本を読んでいます。"
CHOICES = ["正在读书", "已经结束"]
HELP = ["再看讲解", "问 AI", "显示答案"]
STATUS_MADE = ("label", "", {"size": 12, "color": u.MUTED})
HELP_MADE = [("row", "", {})] + [("button", caption, {"compact": True, "font_size": 12, "flex": 1})
                                 for caption in HELP]
# The hand-backs to the line a question page can make (the app here has no study path).
HAND_BACKS = ("continue_learning", "continue_added_learning")
SPEAKER = {"compact": True, "width": 52, "height": 52, "font_size": 16, "margin_left": 6, "margin_bottom": 0}


# --- states -----------------------------------------------------------------------------------------------

@pytest.fixture
def app(learning_app):
    return learning_app


def course(app, suffix, kind="grammar_meaning", *, draft=None, **fields):
    """A taught synthetic lesson with one due question of ``kind`` (the kind switched on); returns the question
    as the learner store keeps it."""
    now = app._test_clock[0]
    cid, qid = "grammar:s5-answer:" + suffix, "s5-answer:" + suffix
    app.store.set_setting(PREFS, {"enabled": [kind]})
    learning = app.learning_store
    learning.enroll(item(cid, title="合成" + suffix, abilities=["understanding"]), now=now - timedelta(days=2))
    learning.advance_instruction(cid, "intro_done", now=now - timedelta(days=2))
    learning.advance_instruction(cid, "guide_correct", now=now - timedelta(days=2))
    fields.setdefault("choices", list(CHOICES))
    learning.append_questions(question(cid, qid, kind=kind, family="grammar", feedback="合成结果", **fields),
                              now=now - timedelta(days=1))
    if draft is not None:
        learning.save_draft(qid, draft)
    return learning.get_question(qid)


def typed_course(app, suffix="text", **fields):
    """A question answered by typing into a plain text field (a Chinese meaning)."""
    return course(app, suffix, input_mode="text", choices=[], **fields)


def blocks_course(app, suffix="blocks"):
    """A question answered by putting three blocks together."""
    return course(app, suffix, "grammar_reorder", input_mode="reorder", prompt="正在读书",
                  choices=["本を", "読んで", "います"], answer="本を読んでいます")


def heard_course(app, suffix="heard"):
    """A heard-word question: its audio plays first, then one of two written words is chosen."""
    return course(app, suffix, "vocab_audio_word", audio_text="ほん", choices=["本", "日"], answer="本")


def needs_input_course(app, suffix="needs-input", *, draft=None):
    """A typed Japanese question this device cannot complete with its keypad (the answer's kanji are not
    offered): the page offers 换一道题 and 作答设置 instead of 提交."""
    return course(app, suffix, "grammar_cloze", input_mode="text", prompt="（　）へ行きます。", answer="学校",
                  choices=[], draft=draft)


def ask(app, kind="grammar_meaning"):
    """Show the question the learner store answers as the course head for ``kind``, as the line does."""
    head = app.learning_store.current([kind])
    assert head["type"] == "question"
    app.show_learning_question(head)
    return head


def stored_draft(app, qid):
    return app.learning_store.get_question(qid)["draft"]


def with_loop(app):
    """The app gets an event loop that can arm timers (a Mock): typing then waits for a pause."""
    app.loop.call_later = Mock()


def draft_timers(app):
    from kanamemory.draft_debounce import DEFAULT_DELAY
    return [call for call in app.loop.call_later.call_args_list if call.args and call.args[0] == DEFAULT_DELAY]


def made(app, *kinds):
    """(kind, text, keyword arguments) of the page's recorded controls, only ``kinds`` when given."""
    return [entry for entry in course_line.page_made_with(app) if not kinds or entry[0] in kinds]


def gap_app(app, case, monkeypatch):
    """The app on the synthetic gap lesson's stores, its buttons carrying a style as native ones do."""
    button = u.button
    monkeypatch.setattr(u, "button", lambda text, handler, **kw: button(text, handler, **dict(kw, style=SimpleNamespace())))
    monkeypatch.setattr(app, "store", case.store)
    monkeypatch.setattr(app, "learning_store", case.learning)
    app._test_clock[0] = case.when
    case.store.set_setting(PREFS, {"enabled": [GAP_KIND]})
    return enqueue(case)


def ask_gap(app, case):
    head = case.learning.current([GAP_KIND], now=case.when)
    app.show_learning_question(head)
    return head


def old_controls(app):
    """The pressable controls of the page drawn now, by caption (kept before the page is drawn again)."""
    return {view.text: view for view in course_line.controls(app)}


# --- a. an interrupted skip -----------------------------------------------------------------------------

def test_a_question_whose_skip_was_interrupted_is_finished_when_shown_and_no_result_page_opens(app, monkeypatch):
    """A skip (换一道题) writes submit(outcome cancelled) and then finalize; when only the first write happened,
    showing the question again finishes it with finalize(rating=None) once and hands back to the line with
    continue_added_learning(); nothing is drawn, no result page is asked for; the history row has no rating and
    is not scheduled, and the lesson's due time stays as it was."""
    q = course(app, "skip")
    due = app.learning_store.get_unit(q["target_id"], "understanding")["due_at_utc"]
    app.learning_store.submit(q["id"], "", correct=None, outcome="cancelled")
    row = app.learning_store.get_question(q["id"])
    head = {"type": "question", "question": row, "draft": row["draft"], "result": row["result"]}
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.watch(app, monkeypatch, names=HAND_BACKS + ("show_learning_feedback",), log=log)
    app.show_learning_question(head)
    assert course_line.calls(log) == [("finalize", (q["id"],), {"rating": None}), ("continue_added_learning", (), {})]
    assert app.learning_store.get_question(q["id"])["status"] == "completed"
    [event] = app.learning_store.history()
    assert (event["question_id"], event["rating"], event["scheduled"], event["answer"]["outcome"]) == (
        q["id"], None, False, "cancelled")
    assert app.learning_store.get_unit(q["target_id"], "understanding")["due_at_utc"] == due
    assert course_line.course_question(app) is row
    assert course_line.answer_draft(app) == row["draft"] and course_line.answer_draft(app) is not row["draft"]


def test_the_skip_writes_the_cancelled_answer_then_finishes_it_then_hands_back(app, monkeypatch):
    """换一道题 on a question this device cannot answer: submit(id, the draft's answer, correct=None,
    outcome="cancelled"), then finalize(id, rating=None), then continue_added_learning(); nothing is drawn."""
    q = needs_input_course(app, draft={"answer": "がっ"})
    ask(app, "grammar_cloze")
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.watch(app, monkeypatch, names=HAND_BACKS, log=log)
    course_line.press(app, "换一道题")
    assert course_line.calls(log) == [("submit", (q["id"], "がっ"), {"correct": None, "outcome": "cancelled"}),
                                      ("finalize", (q["id"],), {"rating": None}), ("continue_added_learning", (), {})]


def test_the_skip_does_nothing_once_the_device_can_answer_the_question(app, monkeypatch):
    """When the system Japanese keyboard was switched on after the page was drawn, 换一道题 writes nothing."""
    from kanamemory.answer_input import SETTING
    needs_input_course(app)
    ask(app, "grammar_cloze")
    app.store.set_setting(SETTING, {"system_japanese": True})
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.watch(app, monkeypatch, names=HAND_BACKS, log=log)
    course_line.press(app, "换一道题")
    assert log == []


def test_a_question_with_a_saved_answer_opens_its_result_page_and_writes_nothing(app, monkeypatch):
    """A shown question that already has a saved (not cancelled) answer goes straight to its result page with
    that result; the draft is the one handed in, nothing is written."""
    q = course(app, "answered")
    app.learning_store.submit(q["id"], CHOICES[0], True)
    row = app.learning_store.get_question(q["id"])
    head = {"type": "question", "question": row, "draft": row["draft"], "result": row["result"]}
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.watch(app, monkeypatch, names=HAND_BACKS + ("show_learning_feedback",), log=log)
    app.show_learning_question(head)
    assert course_line.calls(log) == [("show_learning_feedback", (row["result"],), {})]
    assert course_line.course_question(app) is row


# --- b. the "presented" mark ----------------------------------------------------------------------------

def test_the_presented_mark_is_written_after_the_page_is_drawn_and_only_the_mark(app, monkeypatch):
    """The first drawing of a question writes save_draft(id, {"presented": True}) after screen(); the draft in
    memory keeps what was saved before and gains the mark; the text field starts with the saved answer."""
    q = typed_course(app, draft={"answer": "正在"})
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.watch(app, monkeypatch, names=(), log=log)
    head = ask(app)
    assert course_line.calls(log) == [("screen", ("learning_question", "语法含义"), {}),
                                      ("save_draft", (q["id"], {"presented": True}), {})]
    # The line works on its own copy of the draft handed in: the head's draft is left as it was.
    assert course_line.answer_draft(app) is not head["draft"] and head["draft"] == {"answer": "正在"}
    assert course_line.answer_draft(app) == {"answer": "正在", "presented": True}
    assert course_line.typed_answer(app) == "正在" and course_line.answer_field(app).value == "正在"
    assert stored_draft(app, q["id"]) == {"answer": "正在", "presented": True}


def test_a_question_drawn_again_does_not_write_the_mark_a_second_time(app, monkeypatch):
    """Drawing the same question again from what the store keeps writes nothing."""
    course(app, "again")
    ask(app)
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.watch(app, monkeypatch, names=(), log=log)
    ask(app)
    assert course_line.calls(log) == [("screen", ("learning_question", "语法含义"), {})]


def test_a_drawing_that_fails_writes_no_presented_mark(app, monkeypatch):
    """When drawing the page raises, the error goes up and nothing is written."""
    q = course(app, "broken")
    log = course_line.record_answer_writes(app, monkeypatch)

    def broken(*args, **kwargs):
        raise RuntimeError("drawing failed")
    monkeypatch.setattr(app, "screen", broken)
    with pytest.raises(RuntimeError, match="drawing failed"):
        ask(app)
    assert log == [] and stored_draft(app, q["id"]) == {}


# --- c. the draft ---------------------------------------------------------------------------------------

def test_typing_is_saved_once_after_a_pause(app, monkeypatch):
    """With an event loop, each change updates the draft in memory at once and arms one pause timer; when the
    pause elapses the draft is written once (the mark and the last text)."""
    q = typed_course(app)
    with_loop(app)
    ask(app)
    log = course_line.record_answer_writes(app, monkeypatch)
    field = course_line.answer_field(app)
    for text in ("正", "正在", "正在读书"):
        course_line.type_into(field, text)
    assert course_line.answer_draft(app)["answer"] == "正在读书"
    assert log == [] and len(draft_timers(app)) == 1
    elapse_draft_delay(app)
    assert course_line.calls(log) == [("save_draft", (q["id"], {"presented": True, "answer": "正在读书"}), {})]


def test_without_an_event_loop_every_change_is_saved_at_once(app, monkeypatch):
    """Without a loop that can arm timers, each change is written straight away."""
    q = typed_course(app)
    ask(app)
    log = course_line.record_answer_writes(app, monkeypatch)
    field = course_line.answer_field(app)
    course_line.type_into(field, "正")
    course_line.type_into(field, "正在")
    assert course_line.calls(log) == [("save_draft", (q["id"], {"presented": True, "answer": "正"}), {}),
                                      ("save_draft", (q["id"], {"presented": True, "answer": "正在"}), {})]


def test_the_typed_draft_is_saved_before_the_submit(app, monkeypatch):
    """提交 first forces out the waiting draft, then submits the field's text."""
    q = typed_course(app)
    with_loop(app)
    ask(app)
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.type_into(course_line.answer_field(app), "正在读书")
    course_line.press(app, "提交")
    assert [(call.name, call.args[:2]) for call in course_line.own_calls(log)] == [
        ("save_draft", (q["id"], {"presented": True, "answer": "正在读书"})), ("submit", (q["id"], "正在读书"))]


def test_leaving_forces_out_the_draft_once(app, monkeypatch):
    """The app's shared flush (what every page change, Back, exit and backgrounding call first) writes the
    waiting draft once; a second flush finds nothing."""
    q = typed_course(app)
    with_loop(app)
    ask(app)
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.type_into(course_line.answer_field(app), "正在")
    KanaMemory.flush_drafts(app)
    assert app.flush_learning_draft() is None
    assert course_line.calls(log) == [("save_draft", (q["id"], {"presented": True, "answer": "正在"}), {})]


def test_a_forced_flush_on_the_page_writes_the_live_draft_and_after_leaving_the_last_one_shown(app, monkeypatch):
    """On the question page the flush writes the draft as it is now (flags saved by other paths included);
    once another page is shown it writes the copy taken at the last change instead."""
    q = typed_course(app)
    with_loop(app)
    ask(app)
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.type_into(course_line.answer_field(app), "正在")
    course_line.answer_draft(app)["audio_ok"] = True
    app.flush_learning_draft()
    course_line.type_into(course_line.answer_field(app), "正在读")
    app.screen("home", "今日")
    course_line.answer_draft(app)["answer"] = "后来"
    app.flush_learning_draft()
    assert course_line.calls(log) == [
        ("save_draft", (q["id"], {"presented": True, "answer": "正在", "audio_ok": True}), {}),
        ("save_draft", (q["id"], {"presented": True, "answer": "正在读", "audio_ok": True}), {})]


def test_a_locked_database_keeps_the_draft_waiting_says_so_and_the_next_flush_writes_it(app, monkeypatch):
    """A lock timeout while writing the draft: the flush answers False, the status line says the draft is not
    saved yet, the error is reported, the draft in memory is kept; the next forced flush writes it and clears
    the status line."""
    q = typed_course(app)
    with_loop(app)
    ask(app)
    locked = sqlite3.OperationalError("database is locked")
    failing = {}

    def fail(*args, **kwargs):
        if failing:
            raise locked
    log = course_line.record_answer_writes(app, monkeypatch, before={"save_draft": fail})
    errors = course_line.record_diagnostic_errors(monkeypatch)
    course_line.type_into(course_line.answer_field(app), "正在")
    failing["on"] = True
    assert app.flush_learning_draft() is False
    assert course_line.status_text(app) == BUSY
    assert course_line.calls(errors) == [("record_error", ("learning_draft", "save", locked), {})]
    assert course_line.answer_draft(app)["answer"] == "正在" and stored_draft(app, q["id"]) == {"presented": True}
    failing.clear()
    saved = app.flush_learning_draft()
    assert saved["draft"] == {"presented": True, "answer": "正在"}
    assert course_line.status_text(app) == ""
    assert [call.name for call in log] == ["save_draft", "save_draft"]


def test_a_block_edit_is_saved_at_once_and_updates_the_page_in_place(app, monkeypatch):
    """Pressing a block writes the proposed draft at once (no pause timer); the composition, the blocks' and
    撤回末项's enabled states and the status line change in place; 撤回末项 takes the block back the same way."""
    q = blocks_course(app)
    with_loop(app)
    ask(app, "grammar_reorder")
    log = course_line.record_answer_writes(app, monkeypatch)
    composition = next(view for view in descendants(app.body) if view.text == EMPTY_BLOCKS)
    undo = course_line.control(app, "撤回末项")
    block = course_line.press(app, "本を")
    assert course_line.calls(log) == [
        ("save_draft", (q["id"], {"presented": True, "answer": "本を", "parts": ["本を"], "used": [0]}), {})]
    assert draft_timers(app) == []
    assert (composition.text, block.enabled, undo.enabled) == ("本を", False, True)
    assert course_line.chosen_blocks(app) == ["本を"] and course_line.used_blocks(app) == [0]
    assert course_line.answer_draft(app) == stored_draft(app, q["id"])
    assert course_line.status_text(app) == ""
    course_line.press(app, "撤回末项")
    assert (composition.text, block.enabled, undo.enabled) == (EMPTY_BLOCKS, True, False)
    assert course_line.chosen_blocks(app) == [] and course_line.used_blocks(app) == []
    assert log[-1].args == (q["id"], {"presented": True, "answer": "", "parts": [], "used": []})


def test_a_block_edit_that_cannot_be_saved_changes_nothing_and_says_so(app, monkeypatch):
    """A failing write of a block edit: the status line says 输入未保存，请重试。, the error is reported, the
    blocks chosen and the composition stay as they were."""
    blocks_course(app)
    ask(app, "grammar_reorder")
    error = RuntimeError("disk")

    def fail(*args, **kwargs):
        raise error
    course_line.record_answer_writes(app, monkeypatch, before={"save_draft": fail})
    errors = course_line.record_diagnostic_errors(monkeypatch)
    course_line.press(app, "本を")
    assert course_line.status_text(app) == BLOCKS_UNSAVED
    assert course_line.calls(errors) == [("record_error", ("learning_word_blocks", "save", error), {})]
    assert course_line.chosen_blocks(app) == [] and EMPTY_BLOCKS in course_line.texts(app)


def test_a_block_edit_leaves_the_page_when_the_question_was_answered_meanwhile(app, monkeypatch):
    """When the saved question already has an answer (answered elsewhere), a block edit hands back to the line
    with continue_learning() and changes nothing on the page."""
    q = blocks_course(app)
    ask(app, "grammar_reorder")
    app.learning_store.submit(q["id"], "本を読んでいます", True)
    log = course_line.watch(app, monkeypatch, names=HAND_BACKS)
    course_line.press(app, "本を")
    assert course_line.calls(log) == [("continue_learning", (), {})]
    assert course_line.chosen_blocks(app) == [] and EMPTY_BLOCKS in course_line.texts(app)


# --- d. aids --------------------------------------------------------------------------------------------

def test_the_hint_marks_the_draft_assisted_before_the_teaching_page_is_shown(app, monkeypatch):
    """再看讲解 writes the draft with assisted=True (the same draft object, changed in place), then shows the
    teaching page 回看讲解 with 返回题目, which hands back with continue_learning(widget)."""
    q = course(app, "hint")
    ask(app)
    draft = course_line.answer_draft(app)
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.watch(app, monkeypatch, names=HAND_BACKS, log=log)
    course_line.press(app, "再看讲解")
    assert course_line.calls(log) == [("save_draft", (q["id"], {"presented": True, "assisted": True}), {}),
                                      ("screen", ("learning_prerequisite", "回看讲解"), {})]
    assert course_line.answer_draft(app) is draft and draft["assisted"] is True
    widget = course_line.press(app, "返回题目")
    assert course_line.calls(log)[2:] == [("continue_learning", (widget,), {})]


def test_the_assisted_mark_stays_when_the_question_is_shown_again_and_is_submitted(app, monkeypatch):
    """After the hint the question shown again keeps the mark, and the answer submitted is recorded assisted."""
    q = course(app, "sticky")
    ask(app)
    course_line.press(app, "再看讲解")
    ask(app)
    assert course_line.answer_draft(app)["assisted"] is True
    course_line.press(app, CHOICES[0])
    assert app.learning_store.get_question(q["id"])["result"]["assisted"] is True


def test_the_hint_of_an_unverified_material_shows_the_material_page_and_marks_nothing(app, monkeypatch):
    """When the lesson's teaching may not be shown, 再看讲解 draws 材料待核验 and writes nothing."""
    from kanamemory import assessment_policy
    q = course(app, "unverified")
    ask(app)
    monkeypatch.setattr(assessment_policy, "teaching_allowed", lambda item, store=None: False)
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.press(app, "再看讲解")
    assert log == [] and course_line.page(app) == ("learning_prerequisite", "材料待核验")
    assert "assisted" not in course_line.answer_draft(app) and stored_draft(app, q["id"]) == {"presented": True}


def test_show_answer_marks_the_draft_assisted_before_the_submit_and_the_result_page(app, monkeypatch):
    """显示答案 writes the assisted draft first, then submits as revealed, then draws the result page."""
    q = course(app, "reveal")
    ask(app)
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.watch(app, monkeypatch, names=(), log=log)
    course_line.press(app, "显示答案")
    made_calls = course_line.calls(log)
    assert [name for name, _, _ in made_calls] == ["save_draft", "submit", "screen"]
    assert made_calls[0] == ("save_draft", (q["id"], {"presented": True, "assisted": True}), {})
    assert made_calls[1][2]["outcome"] == "revealed" and made_calls[1][2]["assisted"] is True
    assert made_calls[2][1] == ("learning_feedback", "作答结果")


def test_the_hint_on_a_pitch_question_replaces_the_draft_with_the_editor_draft_marked_assisted(app, monkeypatch):
    """On a pitch question the hint first completes the draft as the pitch editor keeps it (a NEW draft object),
    then marks it assisted and writes it."""
    _, q = prepare_pitch(app)
    before = course_line.answer_draft(app)
    expected = {**pitch_editor_draft(q, deepcopy(before)), "assisted": True}
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.press(app, "再看讲解")
    assert course_line.calls(log) == [("save_draft", (q["id"], expected), {})]
    assert course_line.answer_draft(app) is not before and course_line.answer_draft(app) == expected


def test_a_pitch_edit_is_saved_and_becomes_the_draft_the_line_holds(app, monkeypatch):
    """A key of the pitch editor writes the edited draft at once and the line then holds that draft (the editor
    replaces the line's draft); the next hint marks that draft assisted."""
    _, q = prepare_pitch(app)
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.press(app, "▔")
    [write] = course_line.own_calls(log)
    assert write.name == "save_draft" and write.args[0] == q["id"]
    assert write.args[1]["pitch_state"]["actions"] == ["H"]
    assert course_line.answer_draft(app) == write.args[1]
    assert stored_draft(app, q["id"])["pitch_state"]["actions"] == ["H"]
    course_line.press(app, "再看讲解")
    assert log[-1].args[1]["pitch_state"]["actions"] == ["H"] and log[-1].args[1]["assisted"] is True


def test_the_audio_mark_is_saved_before_the_page_counts_the_audio_as_heard(app, monkeypatch):
    """播放题目 plays the question's audio; then the draft is written with audio_ok=True while the page still
    counts the audio as unheard; after the write the page counts it heard and the draft carries the flag."""
    q = heard_course(app)
    ask(app, "vocab_audio_word")
    heard_at_write = []
    log = course_line.record_answer_writes(app, monkeypatch, before={
        "save_draft": lambda *args, **kwargs: heard_at_write.append(course_line.audio_heard(app))})
    course_line.press(app, "🔊")
    app.platform.play.assert_awaited_once_with("ほん")
    assert course_line.calls(log) == [("save_draft", (q["id"], {"presented": True, "audio_ok": True}), {})]
    assert heard_at_write == [False]
    assert course_line.audio_heard(app) is True and course_line.answer_draft(app)["audio_ok"] is True


def test_a_heard_question_resumed_after_its_audio_played_counts_the_audio_as_heard(app, monkeypatch):
    """A heard question drawn again from a draft that carries audio_ok counts its audio as heard at once (no
    write), so it can be answered without playing again; without the flag it starts unheard."""
    q = heard_course(app)
    ask(app, "vocab_audio_word")
    assert course_line.audio_heard(app) is False
    app.learning_store.save_draft(q["id"], {"audio_ok": True})
    log = course_line.record_answer_writes(app, monkeypatch)
    ask(app, "vocab_audio_word")
    assert log == [] and course_line.audio_heard(app) is True


def test_an_audio_mark_that_cannot_be_saved_leaves_the_audio_unheard_and_says_so(app, monkeypatch):
    """The audio played but its mark could not be written: the page keeps it unheard, the draft has no flag,
    the status line says so and the error is reported."""
    heard_course(app)
    ask(app, "vocab_audio_word")
    error = RuntimeError("disk")

    def fail(*args, **kwargs):
        raise error
    course_line.record_answer_writes(app, monkeypatch, before={"save_draft": fail})
    errors = course_line.record_diagnostic_errors(monkeypatch)
    course_line.press(app, "🔊")
    assert course_line.audio_heard(app) is False and "audio_ok" not in course_line.answer_draft(app)
    assert course_line.status_text(app) == AUDIO_UNSAVED
    assert course_line.calls(errors) == [("record_error", ("learning_audio", "save", error), {})]


def test_audio_that_fails_to_play_writes_nothing_and_says_so(app, monkeypatch):
    """A failing playback writes nothing, keeps the audio unheard and says so in the status line."""
    heard_course(app)
    ask(app, "vocab_audio_word")
    error = RuntimeError("no voice")
    app.platform.play = AsyncMock(side_effect=error)
    log = course_line.record_answer_writes(app, monkeypatch)
    errors = course_line.record_diagnostic_errors(monkeypatch)
    course_line.press(app, "🔊")
    assert log == [] and course_line.audio_heard(app) is False
    assert course_line.status_text(app) == AUDIO_FAILED
    assert course_line.calls(errors) == [("record_error", ("learning_audio", "play", error), {})]


def test_audio_whose_page_was_drawn_again_while_playing_writes_nothing(app, monkeypatch):
    """When the page is drawn anew while the audio plays, the finished playback writes no mark."""
    heard_course(app)
    ask(app, "vocab_audio_word")
    app.platform.play = AsyncMock(side_effect=lambda text: ask(app, "vocab_audio_word"))
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.press(app, "🔊")
    assert log == [] and course_line.audio_heard(app) is False


# --- e. controls of an earlier drawing ------------------------------------------------------------------

def test_a_gap_choice_of_an_earlier_drawing_of_the_same_question_does_nothing(app, cloze_case, monkeypatch):
    """The gap page drawn again (from the store): a choice pressed on the earlier drawing writes nothing; the
    same choice on the current drawing writes the gap."""
    q = gap_app(app, cloze_case, monkeypatch)
    ask_gap(app, cloze_case)
    old = old_controls(app)
    ask_gap(app, cloze_case)
    log = course_line.record_answer_writes(app, monkeypatch)
    old["は"].on_press(old["は"])
    assert log == [] and cloze_case.learning.get_question(q["id"])["draft"] == {"presented": True}
    course_line.press(app, "は")
    assert [call.name for call in log] == ["save_draft"]
    assert cloze_case.learning.get_question(q["id"])["draft"]["cloze_state"]["values"] == {"slot1": "は"}


def test_the_text_field_of_an_earlier_drawing_of_the_same_question_does_nothing(app, monkeypatch):
    """The text field kept from an earlier drawing changes neither the draft nor arms a timer."""
    typed_course(app)
    with_loop(app)
    ask(app)
    old = course_line.answer_field(app)
    ask(app)
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.type_into(old, "旧")
    assert log == [] and draft_timers(app) == []
    assert "answer" not in course_line.answer_draft(app)


def test_a_block_of_an_earlier_drawing_of_the_same_question_does_nothing(app, monkeypatch):
    """A block or 撤回末项 of an earlier drawing writes nothing and leaves the blocks chosen as they are."""
    blocks_course(app)
    ask(app, "grammar_reorder")
    course_line.press(app, "本を")
    old = old_controls(app)
    ask(app, "grammar_reorder")
    log = course_line.record_answer_writes(app, monkeypatch)
    old["読んで"].on_press(old["読んで"])
    old["撤回末项"].on_press(old["撤回末项"])
    assert log == [] and course_line.chosen_blocks(app) == ["本を"]


def test_the_audio_button_of_an_earlier_drawing_still_plays_and_marks_the_current_question(app, monkeypatch):
    """Odd but today's behaviour: the audio button is the app's own (play_learning_audio), not tied to its
    drawing, so pressing it on an earlier drawing plays the question shown now and saves its mark."""
    q = heard_course(app)
    ask(app, "vocab_audio_word")
    old = old_controls(app)
    ask(app, "vocab_audio_word")
    log = course_line.record_answer_writes(app, monkeypatch)
    old["🔊"].on_press(old["🔊"])
    assert course_line.calls(log) == [("save_draft", (q["id"], {"presented": True, "audio_ok": True}), {})]
    assert course_line.audio_heard(app) is True


# --- f. Back to the kept question page and from the tutor ----------------------------------------------

def test_back_from_the_answer_settings_shows_and_saves_the_current_draft(app, monkeypatch):
    """作答设置 keeps the question page (the waiting draft is written on leaving); Back restores that page with
    the same draft, field and question; typing there goes on from it and is written on the next flush."""
    from types import MethodType
    from kanamemory.learning_ui import LearningUIMixin
    app = use_real_detail_back(app)
    app.show_learning_settings = MethodType(LearningUIMixin.show_learning_settings, app)
    q = needs_input_course(app)
    ask(app, "grammar_cloze")
    shown, draft, field = course_line.course_question(app), course_line.answer_draft(app), course_line.answer_field(app)
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.type_into(field, "がっ")
    course_line.press(app, "作答设置")
    assert course_line.page(app)[0] == "learning_settings"
    assert stored_draft(app, q["id"]) == {"presented": True, "answer": "がっ"}
    app.go_back()
    assert course_line.page(app)[0] == "learning_question"
    assert course_line.course_question(app) is shown and course_line.answer_draft(app) is draft
    assert course_line.answer_field(app) is field and field.value == "がっ"
    assert NEEDS_INPUT in course_line.texts(app)
    course_line.type_into(field, "がっこう")
    app.flush_learning_draft()
    assert stored_draft(app, q["id"]) == {"presented": True, "answer": "がっこう"}
    assert [call.name for call in log] == ["save_draft", "save_draft"]


def test_asking_the_tutor_marks_the_draft_assisted_and_back_shows_the_question_with_that_mark(app, monkeypatch):
    """问 AI opens the tutor chat for this question; sending a question writes the draft with assisted and
    hint_used and replaces the draft the line holds with that one; 返回题目 hands back with continue_learning(),
    and the question shown again carries the tutor's mark into the submit."""
    q = course(app, "tutor")
    ready = {"ready": False}
    monkeypatch.setattr(app, "ai_connection_status", lambda: dict(ready))
    requests = course_line.record_presses(app, "start_tutor_request")
    monkeypatch.setattr(u.toga, "MultilineTextInput", lambda **kw: View(**kw))
    ask(app)
    course_line.press(app, "问 AI")
    assert course_line.page(app) == ("tutor_chat", "问 AI")
    chat = next(view for view in descendants(app.body) if view.value is not None and view.on_press is None
                and view.on_change is not None)
    chat.value = "为什么？"
    ready["ready"] = True
    log = course_line.record_answer_writes(app, monkeypatch)
    course_line.press(app, "发送")
    marked = {"presented": True, "assisted": True, "hint_used": True}
    assert course_line.calls(log) == [("save_draft", (q["id"], marked), {})]
    assert course_line.answer_draft(app) == marked
    assert [call.name for call in requests] == ["start_tutor_request"]
    hand_backs = course_line.watch(app, monkeypatch, names=HAND_BACKS)
    course_line.press(app, "返回题目")
    assert course_line.calls(hand_backs) == [("continue_learning", (), {})]
    ask(app)
    assert course_line.answer_draft(app) == marked
    course_line.press(app, CHOICES[1])
    assert app.learning_store.get_question(q["id"])["result"]["assisted"] is True


# --- g. the problem report's capture ---------------------------------------------------------------------

def test_a_report_on_a_blocks_question_captures_the_draft_the_blocks_and_the_audio_flag(app, monkeypatch):
    """The report's practice part of the question page: the question, the draft, the blocks chosen and used,
    the audio flag and the saved row, under these keys; the content part names the lesson."""
    q = blocks_course(app)
    ask(app, "grammar_reorder")
    course_line.press(app, "本を")
    report = feedback_context.capture_feedback_context(app)
    practice = report["practice"]
    assert set(practice) == {"question", "maintainer_only", "learning_question_state", "learning_parts",
                             "learning_used", "learning_audio_ok", "saved_state"}
    assert practice["learning_question_state"] == course_line.answer_draft(app)
    assert (practice["learning_parts"], practice["learning_used"], practice["learning_audio_ok"]) == (
        ["本を"], [0], False)
    saved = app.learning_store.get_question(q["id"])
    assert practice["saved_state"] == {key: saved.get(key)
                                       for key in ("question_id", "status", "draft", "result", "not_before_utc")}
    assert practice["question"]["id"] == q["id"] and "answer" not in practice["question"]
    assert practice["maintainer_only"]["hidden_solution_and_metadata"]["answer"] == "本を読んでいます"
    assert report["content"]["learning_item"]["id"] == q["target_id"]


def test_a_report_on_a_first_choice_question_has_no_blocks_used_key(app):
    """Odd but today's behaviour: the blocks used are kept only once a blocks question was drawn, so a report on
    the first question of a run, a choice question, has no learning_used key (the blocks chosen are [])."""
    course(app, "report")
    ask(app)
    practice = feedback_context.capture_feedback_context(app)["practice"]
    assert set(practice) == {"question", "maintainer_only", "learning_question_state", "learning_parts",
                             "learning_audio_ok", "saved_state"}
    assert (practice["learning_question_state"], practice["learning_parts"], practice["learning_audio_ok"]) == (
        {"presented": True}, [], False)


# --- h. each input mode: controls, what they call, what one drawing reads -----------------------------------

def reads(log):
    names = [call.name for call in log]
    return {name: names.count(name) for name in ("prompt_preferences", "known_ids", "enabled_kinds")}


def test_a_choice_question_page(app, monkeypatch):
    """A choice question: the prompt (size 20, study role, no wrap), the task column, one button per option
    (multiline, the option's text role) submitting that option for this question, the status line (12,
    MUTED), then the help row 再看讲解 / 问 AI / 显示答案 (compact, 12, flex 1). One drawing reads the prompt
    preferences once and the known ids once, and not the enabled kinds."""
    q = course(app, "choice")
    course_line.record_controls(monkeypatch)
    pressed = course_line.record_presses(app, "submit_learning_answer", "learning_hint", "reveal_learning_answer",
                                         "open_tutor_question")
    log = course_line.record_question_page_reads(app, monkeypatch)
    ask(app)
    assert course_line.page(app) == ("learning_question", "语法含义")
    assert made(app) == [("label", PROMPT, {"size": 20, "text_role": "study", "wrap": False}), ("col", "", {}),
                         *[("button", choice, {"multiline": True, "text_role": "ui"}) for choice in CHOICES],
                         STATUS_MADE, *HELP_MADE]
    assert course_line.texts(app) == [PROMPT, *CHOICES, *HELP]
    assert reads(log) == {"prompt_preferences": 1, "known_ids": 1, "enabled_kinds": 0}
    hint = course_line.press(app, "再看讲解")
    course_line.press(app, "问 AI")
    reveal = course_line.press(app, "显示答案")
    course_line.press(app, CHOICES[1])
    assert course_line.calls(pressed) == [
        ("learning_hint", (hint,), {}), ("open_tutor_question", (), {"source": "learning", "question_id": q["id"]}),
        ("reveal_learning_answer", (reveal,), {}),
        ("submit_learning_answer", (CHOICES[1],), {"question_id": q["id"]})]


def test_a_typed_question_page(app, monkeypatch):
    """A typed question: the prompt, the task column, the text field (starting with the saved answer, its
    change going to save_learning_input), 提交 (primary) submitting the field's text for this question, the
    status line and the help row. Reads: prompt preferences once, known ids once, enabled kinds never."""
    q = typed_course(app, draft={"answer": "正在"})
    course_line.record_controls(monkeypatch)
    pressed = course_line.record_presses(app, "submit_learning_answer", "save_learning_input")
    log = course_line.record_question_page_reads(app, monkeypatch)
    ask(app)
    assert made(app) == [("label", PROMPT, {"size": 20, "text_role": "study", "wrap": False}), ("col", "", {}),
                         ("button", "提交", {"primary": True}), STATUS_MADE, *HELP_MADE]
    assert course_line.texts(app) == [PROMPT, "提交", *HELP]
    assert reads(log) == {"prompt_preferences": 1, "known_ids": 1, "enabled_kinds": 0}
    field = course_line.answer_field(app)
    assert field.value == "正在" and course_line.typed_answer(app) == "正在"
    course_line.type_into(field, "正在读书")
    course_line.press(app, "提交")
    assert course_line.calls(pressed) == [("save_learning_input", (field,), {}),
                                          ("submit_learning_answer", ("正在读书",), {"question_id": q["id"]})]


def test_a_typed_question_this_device_cannot_answer(app, monkeypatch):
    """A typed Japanese question the keypad cannot complete: after the keypad, the notice (13, MUTED), 换一道题
    (primary) asking to skip this question, 作答设置 (compact) opening the answer settings; no 提交."""
    q = needs_input_course(app)
    course_line.record_controls(monkeypatch)
    pressed = course_line.record_presses(app, "skip_unavailable_learning_input", "show_learning_settings")
    ask(app, "grammar_cloze")
    tail = made(app)[-8:]
    assert tail == [("label", NEEDS_INPUT, {"size": 13, "color": u.MUTED}), ("button", "换一道题", {"primary": True}),
                    ("button", "作答设置", {"compact": True}), STATUS_MADE, *HELP_MADE]
    assert "提交" not in course_line.captions(app)
    course_line.press(app, "换一道题")
    settings = course_line.press(app, "作答设置")
    assert course_line.calls(pressed) == [("skip_unavailable_learning_input", (q["id"],), {}),
                                          ("show_learning_settings", (settings,), {})]


def test_a_blocks_question_page(app, monkeypatch):
    """A blocks question: the prompt (ui role here), the task column, the composition (20, ui role while
    empty), one button per block (multiline, study role) adding that position for THIS drawing's question,
    撤回末项 (disabled while empty) and 提交 (primary) submitting the blocks joined, the status line and the
    help row. Reads: prompt preferences once, known ids once."""
    q = blocks_course(app)
    course_line.record_controls(monkeypatch)
    pressed = course_line.record_presses(app, "add_learning_part", "delete_learning_part", "submit_learning_answer")
    log = course_line.record_question_page_reads(app, monkeypatch)
    ask(app, "grammar_reorder")
    assert course_line.page(app) == ("learning_question", "句子重组")
    blocks = ["本を", "読んで", "います"]
    assert made(app) == [("label", "正在读书", {"size": 20, "text_role": "ui", "wrap": False}), ("col", "", {}),
                         ("label", EMPTY_BLOCKS, {"size": 20, "text_role": "ui"}),
                         *[("button", block, {"multiline": True, "text_role": "study"}) for block in blocks],
                         ("button", "撤回末项", {}), ("button", "提交", {"primary": True}), STATUS_MADE, *HELP_MADE]
    assert reads(log) == {"prompt_preferences": 1, "known_ids": 1, "enabled_kinds": 0}
    assert course_line.control(app, "撤回末项").enabled is False
    assert [course_line.control(app, block).enabled for block in blocks] == [True, True, True]
    shown = course_line.course_question(app)
    course_line.press(app, "読んで")
    course_line.press(app, "撤回末项")
    course_line.press(app, "提交")
    made_calls = course_line.calls(pressed)
    assert made_calls == [("add_learning_part", (1,), {"expected_question": shown}),
                          ("delete_learning_part", (), {"expected_question": shown}),
                          ("submit_learning_answer", ("",), {"question_id": q["id"]})]
    assert made_calls[0][2]["expected_question"] is shown


def test_a_heard_question_page(app, monkeypatch):
    """A heard question: the speaker (播放题目, the speaker style) first and no written prompt, the task column,
    the written options (study role), the status line and the help row. Reads: the prompt preferences never,
    the known ids once."""
    q = heard_course(app)
    course_line.record_controls(monkeypatch)
    pressed = course_line.record_presses(app, "play_learning_audio", "submit_learning_answer")
    log = course_line.record_question_page_reads(app, monkeypatch)
    ask(app, "vocab_audio_word")
    assert course_line.page(app) == ("learning_question", "听词选词")
    assert made(app) == [("button", "🔊", SPEAKER), ("col", "", {}),
                         *[("button", word, {"multiline": True, "text_role": "study"}) for word in ("本", "日")],
                         STATUS_MADE, *HELP_MADE]
    assert reads(log) == {"prompt_preferences": 0, "known_ids": 1, "enabled_kinds": 0}
    speaker = course_line.press(app, "🔊")
    course_line.press(app, "日")
    assert course_line.calls(pressed) == [("play_learning_audio", (speaker,), {}),
                                          ("submit_learning_answer", ("日",), {"question_id": q["id"]})]


def test_a_gap_question_page(app, cloze_case, monkeypatch):
    """A gap question: the sentence with its numbered gaps (20, study role) above the task, the gap tabs (the
    selected one bold on the accent colour), the choices, ⌫ and 提交, then the status lines and the help row.
    Reads: prompt preferences once, known ids once."""
    # Independent of the content installed: the readings are on, and the course has no word.
    monkeypatch.setattr(question_readings, "_READINGS", {"schema_version": 1, "items": {}})
    monkeypatch.setattr(question_readings, "_course_words", lambda: {})
    course_line.record_controls(monkeypatch)
    gap_app(app, cloze_case, monkeypatch)
    log = course_line.record_question_page_reads(app, monkeypatch)
    ask_gap(app, cloze_case)
    assert course_line.texts(app) == ["私（①）学校（②）行きます。", "穴埋（あなう）め", "中文提示", "①", "②",
                                      "は", "を", "も", "⌫", "提交", *HELP]
    assert made(app, "label", "button") == [
        ("label", "私（①）学校（②）行きます。", {"size": 20, "text_role": "study", "wrap": False}),
        ("label", "穴埋（あなう）め", {"size": 13, "color": u.MUTED, "text_role": "study"}),
        ("button", "中文提示", {"compact": True, "font_size": 12, "style": SimpleNamespace()}),
        ("button", "①", {"flex": 1, "compact": True, "multiline": True, "text_role": "study",
                         "style": SimpleNamespace(font_weight="bold", background_color="#185C50", color="#FFFFFF")}),
        ("button", "②", {"flex": 1, "compact": True, "multiline": True, "text_role": "study",
                         "style": SimpleNamespace(font_weight="normal", background_color="#E4EEE8", color="#185C50")}),
        *[("button", choice, {"flex": 1, "multiline": True, "text_role": "study", "style": SimpleNamespace()})
          for choice in ("は", "を", "も")],
        ("button", "⌫", {"compact": True, "flex": 1, "style": SimpleNamespace()}),
        ("button", "提交", {"primary": True, "flex": 2, "style": SimpleNamespace()}),
        STATUS_MADE, STATUS_MADE,
        *[("button", caption, {"compact": True, "font_size": 12, "flex": 1, "style": SimpleNamespace()})
          for caption in HELP]]
    assert reads(log) == {"prompt_preferences": 1, "known_ids": 1, "enabled_kinds": 0}


def test_a_pitch_question_page(app, monkeypatch):
    """A pitch question: the task line (13, MUTED) instead of a prompt, the diagram, the editor's keys ▔ ▁ ⌫
    (flex 1, compact, 22) and 提交 (primary), two status lines (the editor's and the page's) and the help row.
    Reads: the prompt preferences never, the known ids once."""
    course_line.record_controls(monkeypatch)
    log = course_line.record_question_page_reads(app, monkeypatch)
    prepare_pitch(app)
    labels_and_buttons = made(app, "label", "button")
    assert labels_and_buttons[0] == ("label", "試験用の入力", {"size": 13, "color": u.MUTED})
    keys = [("button", key, {"flex": 1, "compact": True, "font_size": 22}) for key in ("▔", "▁", "⌫")]
    assert labels_and_buttons[-9:] == [*keys, ("button", "提交", {"primary": True}), STATUS_MADE, STATUS_MADE,
                                        *HELP_MADE[1:]]
    assert course_line.texts(app)[-7:] == ["▔", "▁", "⌫", "提交", *HELP]
    assert reads(log) == {"prompt_preferences": 0, "known_ids": 1, "enabled_kinds": 0}
