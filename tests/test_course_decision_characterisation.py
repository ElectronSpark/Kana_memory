"""课程学习 (the course line's decision): what each next step is, in which order it is settled, and its own pages.

Design 13 stage S5 (docs/design_current/13_frontend_backend_separation.txt) turns the decision of the course
line (app.continue_learning) into a value and the call that performs it, and draws its six inline pages
(所属内容库已停用, 材料待核, 需要日语语音 / 题型已关闭, 本次学习已保存, 稍后继续, 先巩固前置知识) from models; it
keeps the behaviour pinned here. The tests reach the decision's state and seams only through
tests/support/course_support.py, so the move changes that accessor and not these tests. Expected values are
what the code does, including what looks odd (each such test says so).

Labels. P: what the decision does before it branches, in this order; D: where it goes, each taken by the
first line that applies (an entry through the continue action passes a selected head and its read
context; a direct entry from a page passes neither):
  P1  begin the round; a press (a widget is passed) restarts a finished round, a hand-back does not.
  P2  read whether the round was sealed before this entry (before anything is queued).
  P3  read the enabled kinds (the saved kinds this device can present).
  P4  admit today's heard-word tasks, only when that kind is enabled.
  P5  repair the prerequisite rows.
  P6  only when the round was not sealed before: queue the missing questions (one withdrawn-task pass first).
  P7  check the selected head: dropped when its question is gone or superseded, an unanswered question whose
      content is blocked, its prerequisite blocked, a teaching head whose frozen practice guide uses a kind
      now off, or a teaching head the learner passed over in this run.
  P8  the head: the selected one, else the learner store's current (with the entry's read context, if any).
  P9  a question head of a withdrawn task form is exchanged and the head read again, live (at most 8 times).
  P10 seal the round.
  P11 remember the head.
  D1  a blocked head because its library is off: page 所属内容库已停用.
  D2  another blocked head: page 材料待核 with the replacement action for its questions.
  D3  the batch introduction takes the head (it returns without a page of the decision).
  N12 a sealed round's "disabled" head that is only work the round could not queue is dropped (then D5).
  D4  no head but disabled work is counted: the paused page (needs a voice, or the kinds are off).
  D5  no head: the round is marked finished, page 本次学习已保存 with 开始新一轮.
  D6  a disabled head: the paused page, another text.
  D7  a diagnostic head: the diagnostic line continues it (its answer is returned).
  D8  a waiting head: page 稍后继续, its timer armed for the due time.
  D9  a head waiting for its prerequisites: page 先巩固前置知识, its timer armed.
  D10 a teaching head: its teaching step.
  D11 a question head: the question (or its saved result).
  D12 any other head: ValueError.

Public: synthetic course items (support.learning_store_fixtures, ids "grammar:s5-synthetic:*"), the
synthetic extension library of support.library_availability_support, and heads built in the test where a
state cannot be reached with synthetic content (a blocked material, a diagnostic, an unknown type). The
next-content and extra-practice rows are replaced by marked placeholders where a page's own controls are
pinned, so only their place is asserted (the rows themselves are the continue action's and extra
practice's).
"""
from datetime import timedelta
from unittest.mock import Mock

import pytest

from kanamemory import learning_questions as lq
from kanamemory import practice_round
from kanamemory import ui_components as u
from kanamemory import word_sound_questions as sound
from kanamemory.app import KanaMemory
from kanamemory.learning_dependencies import TAUGHT
from support import course_support as course_line
from support.app_fixtures import question_app, setup_app  # noqa: F401
from support.fake_ui import View
from support.learning_fixtures import learning_app  # noqa: F401
from support.learning_store_fixtures import item, question, ready
from support.library_availability_support import EXT_ID, Learner, course as library_course
from support.library_availability_support import question as library_question
from support.review_wait_ui_support import ControlledLoop
from support.study_home_support import orientation_seen, stub_can_continue
from support.study_path_runtime_support import path_app  # noqa: F401

PREFS = "learning_preferences_v1"
ROWS = ["〈接下来一〉", "〈接下来二〉"]
EXTRA = ["〈加练〉"]
HOME = "回到今日"
LIBRARY_TEXTS = ["剩下的 1 项课程所属的内容库已停用、缺失或已被取代，暂不出题。",
                 "学习记录、草稿和复习时间均已保留；重新启用该库后即可继续。"]
MATERIAL_TEXTS = ["相关说明已更新，部分旧题仍需核对。", "原题、草稿和复习时间均已保留。"]
D4_TEXT = "未完成题目已保留，重新开启对应题型后可继续。"
D6_TEXT = "未完成题目与复习时间已保留，开启对应题型后可继续。"
VOICE_TEXTS = ["听词选词需要设备上的日语语音，安装后即可继续。", "未完成题目与复习时间已保留；也可在题型设置中关闭听词选词。"]
DONE_TEXT = "本次学习已保存。"
AGAIN = "开始新一轮"
LATER = "到期后自动继续，也可以先离开。"
CHECK = "检查并继续"
NO_DUE = "当前题型没有可答题目。"
PREREQUISITE_LINE = "先接着巩固前置内容，完成后自动回到本项。"
PREREQUISITE_NO_DUE = "前置内容的练习暂不可用，请检查题型或暂停设置。"
TAUGHT_LINE = "已学过、可以接着往后学的前置："
REPLACE = "使用更新后的练习继续"
SKIP = course_line.SKIP_PREREQUISITES


# --- states -----------------------------------------------------------------------------------------------

@pytest.fixture
def app(path_app):
    """path_app (the study path present, as in production) with the orientation seen and "can 继续学习 reach
    anything" answering False."""
    orientation_seen(path_app)
    stub_can_continue(path_app, False)
    return path_app


def course(app, suffix, *, taught=True, due=True, depends_on=None, kinds=("grammar_meaning",)):
    """A synthetic course item and its question: taught a day ago (due now), taught now (a short wait), or
    untaught (its teaching first). Returns (content id, question id)."""
    now = app._test_clock[0]
    cid, qid = "grammar:s5-synthetic:" + suffix, "s5-question:" + suffix
    if kinds is not None:
        app.store.set_setting(PREFS, {"enabled": list(kinds)})
    learning = app.learning_store
    extra = {"depends_on": depends_on} if depends_on else {}
    learning.enroll(item(cid, title="合成" + suffix, abilities=["understanding"], **extra),
                    now=now - timedelta(days=2))
    if taught:
        ready(learning, cid, when=(now - timedelta(days=1)) if due else now)
    snapshot = question(cid, qid, kind="grammar_meaning", family="grammar", feedback="合成结果",
                        choices=["正在读书", "已经结束"])
    learning.append_questions(snapshot, now=now - timedelta(days=1) if due else now)
    return cid, qid


def disabled_course(app, *, voice=False):
    """A taught, due course question whose kind is off: with ``voice`` the heard-word kind is kept on but the
    device has no Japanese voice (so no kind can be presented)."""
    course(app, "off", kinds=None)
    if voice:
        app.store.set_setting(PREFS, {"enabled": [lq.AUDIO_WORD_KIND],
                                      lq.SOUND_PREFERENCE_VERSION: lq.PREFERENCE_VERSION})
        app.platform.tts_ready = False
    else:
        app.store.set_setting(PREFS, {"enabled": []})


def orphaned_course(app, tmp_path):
    """One due synthetic course whose content library was switched off."""
    app.store.set_setting(PREFS, {"enabled": ["grammar_meaning"]})
    learner = Learner(app.store, tmp_path / "libraries", learning=app.learning_store)
    start = app._test_clock[0] - timedelta(minutes=5)
    learning = app.learning_store
    learning.enroll(library_course(EXT_ID), now=start)
    learning.advance_instruction(EXT_ID, "intro_done", now=start)
    learning.advance_instruction(EXT_ID, "guide_correct", now=start)
    learning.append_questions(library_question(EXT_ID, "q:" + EXT_ID), now=start)
    learner.disable()


def placeholders(app, monkeypatch):
    """The next-content and 继续加练 rows become marked placeholders; returns their build logs."""
    rows = course_line.replace_next_content_rows(app, monkeypatch, lambda: [View(text=text) for text in ROWS])
    extra = course_line.replace_extra_rows(monkeypatch, lambda app: [View(text=text) for text in EXTRA])
    return rows, extra


def withdrawn_head(qid="s5-withdrawn"):
    """A question head whose task form the app no longer asks (heard word, kana spellings)."""
    return {"type": "question", "question": {"id": qid, "kind": lq.AUDIO_WORD_KIND,
                                             "variant": sound.KANA_VARIANT_BASE}}


class Targets:
    """Records the page targets ``names``; they run for real until drawn() is called, then only record."""

    def __init__(self, app, monkeypatch, *names):
        self.app, self.state = app, {"drawn": False}
        self.log = course_line.record_entries(app, monkeypatch, real=lambda: not self.state["drawn"], names=names)

    def drawn(self):
        self.state["drawn"] = True
        self.log.clear()

    def press(self, caption):
        """Press the control with this caption; (the widget, the (name, args, kwargs) of the calls it made)."""
        start = len(self.log)
        widget = course_line.press(self.app, caption)
        return widget, course_line.calls(self.log[start:])

    def presses(self, *pairs):
        """Each caption presses its target with the widget and nothing else."""
        for caption, target in pairs:
            widget, made = self.press(caption)
            assert made == [(target, (widget,), {})], caption


def due_label(app, due):
    return app._learning_due_label(due)


# --- a. each destination ------------------------------------------------------------------------------------

def test_d1_a_head_of_a_switched_off_library_draws_its_page_with_the_library_button(app, monkeypatch, tmp_path):
    """D1: page learning_wait 所属内容库已停用: the count of items left (plain label), the kept-records line
    (MUTED), 查看内容库 -> show_content_libraries(widget), the next-content rows in place, 回到今日 ->
    leave_learning(widget). No timer, no hand-off call."""
    orphaned_course(app, tmp_path)
    course_line.record_controls(monkeypatch)
    rows, _ = placeholders(app, monkeypatch)
    log = course_line.watch(app, monkeypatch, names=course_line.DECISION_CALLS)
    arming = course_line.record_wait_arming(app, monkeypatch)
    route = course_line.record_batch_route(app, monkeypatch)
    targets = Targets(app, monkeypatch, "show_content_libraries", "leave_learning")
    app.continue_learning()
    targets.drawn()
    assert route == []
    assert course_line.page(app) == ("learning_wait", "所属内容库已停用")
    assert course_line.texts(app) == LIBRARY_TEXTS + ["查看内容库"] + ROWS + [HOME]
    assert course_line.page_made_with(app) == [
        ("label", LIBRARY_TEXTS[0], {}), ("label", LIBRARY_TEXTS[1], {"color": u.MUTED}),
        ("button", "查看内容库", {}), ("button", HOME, {})]
    assert course_line.calls(log) == [("screen", ("learning_wait", "所属内容库已停用"), {})]
    assert len(rows) == 1 and arming == []
    head = course_line.course_head(app)
    assert (head["type"], head["reason"]) == ("content_blocked", "library_disabled")
    targets.presses(("查看内容库", "show_content_libraries"), (HOME, "leave_learning"))


def test_d1_without_a_library_page_the_library_button_is_left_out(app, monkeypatch, tmp_path):
    """D1: 查看内容库 is offered only when the app has a callable show_content_libraries."""
    orphaned_course(app, tmp_path)
    placeholders(app, monkeypatch)
    app.show_content_libraries = None
    app.continue_learning()
    assert course_line.texts(app) == LIBRARY_TEXTS + ROWS + [HOME]


def test_d1_a_head_without_counts_names_zero_items(app, monkeypatch):
    """D1: the count comes from the head's counts; a head without them says 0 items."""
    placeholders(app, monkeypatch)
    course_line.replace_course_heads(app, monkeypatch, {"type": "content_blocked", "reason": "library_disabled"})
    app.continue_learning()
    assert course_line.texts(app)[0] == "剩下的 0 项课程所属的内容库已停用、缺失或已被取代，暂不出题。"


def test_d2_a_blocked_material_head_draws_its_page_with_the_replacement_rows_for_its_questions(app, monkeypatch):
    """D2: page learning_wait 材料待核: two labels (the second MUTED), the replacement rows built for the
    head's question ids (no item), the next-content rows after them, 回到今日 -> leave_learning(widget)."""
    course_line.record_controls(monkeypatch)
    order = []
    course_line.replace_next_content_rows(app, monkeypatch, lambda: [View(text=text) for text in ROWS], log=order)
    head = {"type": "content_blocked", "reason": "material", "question_ids": ["s5-a", "s5-b"]}
    course_line.replace_course_heads(app, monkeypatch, head)
    built = course_line.replace_replacement_controls(app, monkeypatch, lambda ids, item: [View(text="〈替换〉")],
                                                     log=order)
    log = course_line.watch(app, monkeypatch, names=course_line.DECISION_CALLS)
    route = course_line.record_batch_route(app, monkeypatch)
    targets = Targets(app, monkeypatch, "leave_learning")
    app.continue_learning()
    targets.drawn()
    assert route == [] and [call.name for call in order] == ["replacement_rows", "next_content_rows"]
    assert built[0].args[0] is head["question_ids"] and len(built[0].args) == 1 and built[0].kwargs == {}
    assert course_line.page(app) == ("learning_wait", "材料待核")
    assert course_line.texts(app) == MATERIAL_TEXTS + ["〈替换〉"] + ROWS + [HOME]
    assert course_line.page_made_with(app) == [
        ("label", MATERIAL_TEXTS[0], {}), ("label", MATERIAL_TEXTS[1], {"color": u.MUTED}), ("button", HOME, {})]
    assert course_line.calls(built, "replacement_rows") == [("replacement_rows", (["s5-a", "s5-b"],), {})]
    assert course_line.calls(log) == [("screen", ("learning_wait", "材料待核"), {})]
    assert course_line.course_head(app) is head
    targets.presses((HOME, "leave_learning"))


def test_d2_a_question_that_cannot_be_replaced_offers_no_replacement_button(app, monkeypatch):
    """D2: the real replacement rows for a pending question that has nothing newer to use: none."""
    _, qid = course(app, "plain")
    placeholders(app, monkeypatch)
    course_line.replace_course_heads(app, monkeypatch, {"type": "content_blocked", "question_ids": [qid]})
    app.continue_learning()
    assert course_line.texts(app) == MATERIAL_TEXTS + ROWS + [HOME]


@pytest.mark.parametrize("route", [True, False], ids=["batch-takes-it", "taught-alone"])
def test_d3_the_batch_route_is_asked_with_the_head_and_kinds_after_the_head_is_remembered(app, monkeypatch, route):
    """D3 (P11 before D3): the batch route is asked (head, enabled kinds) once the head is remembered; when it
    takes the step the decision draws and calls nothing more; otherwise a teaching head goes to D10."""
    cid, _ = course(app, "batch", taught=False)
    if route:
        asked = course_line.batch_takes_head(app, monkeypatch)
    else:
        asked = course_line.record_batch_route(app, monkeypatch, answer=False)
    log = course_line.watch(app, monkeypatch, names=course_line.DECISION_CALLS)
    app.continue_learning()
    (call,) = asked
    head = course_line.course_head(app)
    assert call.args == (head, app.learning_preferences()["enabled"]) and call.args[1][0] == "grammar_meaning"
    assert call.head is head and call.args[0] is head and head["content"]["id"] == cid
    if route:
        assert course_line.calls(log) == [("show_intro_batch", (), {})]
    else:
        assert course_line.calls(log) == [("show_learning_instruction", (head["content"],), {})]


@pytest.mark.parametrize("voice", [False, True], ids=["kinds-off", "no-voice"])
@pytest.mark.parametrize("which", ["D4", "D6"])
def test_d4_d6_the_paused_page_names_the_missing_voice_or_the_closed_kinds(app, monkeypatch, which, voice):
    """D4 (no head, disabled work counted) and D6 (a disabled head) draw the same paused page with their own
    text: 题型已关闭 with that text and 题型设置 (primary) -> show_learning_settings(widget); or, when the heard-word
    kind is kept on without a voice, 需要日语语音 whose two texts ignore it (odd: D4 and D6 cannot be told
    apart there), 语音与手写 (primary) -> show_settings(widget) and 题型设置; then the next-content rows and
    回到今日 -> leave_learning(widget)."""
    disabled_course(app, voice=voice)
    course_line.record_controls(monkeypatch)
    placeholders(app, monkeypatch)
    if which == "D4":
        course_line.replace_course_heads(app, monkeypatch, None)
    targets = Targets(app, monkeypatch, "show_settings", "show_learning_settings", "leave_learning")
    app.continue_learning()
    targets.drawn()
    if voice:
        assert course_line.page(app) == ("learning_wait", "需要日语语音")
        assert course_line.texts(app) == VOICE_TEXTS + ["语音与手写", "题型设置"] + ROWS + [HOME]
        assert course_line.page_made_with(app) == [
            ("label", VOICE_TEXTS[0], {}), ("label", VOICE_TEXTS[1], {"color": u.MUTED}),
            ("button", "语音与手写", {"primary": True}), ("button", "题型设置", {}), ("button", HOME, {})]
        targets.presses(("语音与手写", "show_settings"), ("题型设置", "show_learning_settings"), (HOME, "leave_learning"))
    else:
        text = D4_TEXT if which == "D4" else D6_TEXT
        assert course_line.page(app) == ("learning_wait", "题型已关闭")
        assert course_line.texts(app) == [text, "题型设置"] + ROWS + [HOME]
        assert course_line.page_made_with(app) == [
            ("label", text, {}), ("button", "题型设置", {"primary": True}), ("button", HOME, {})]
        targets.presses(("题型设置", "show_learning_settings"), (HOME, "leave_learning"))
    assert (course_line.course_head(app) or {}).get("type") == (None if which == "D4" else "disabled")


def test_d4_the_counts_are_asked_with_the_enabled_kinds_only_when_there_is_no_head(app, monkeypatch):
    """D4: counts(enabled) is read after the head only when there is none (a head never asks it)."""
    disabled_course(app)
    with monkeypatch.context() as patch:
        reads = course_line.replace_course_heads(app, patch, None)
        app.continue_learning()
        assert course_line.calls(reads, "counts") == [("counts", ([],), {})]
    course(app, "head")
    reads = course_line.record_head_reads(app, monkeypatch)
    app.continue_learning()
    assert [call.name for call in course_line.own_calls(reads)] == ["current"]


@pytest.mark.parametrize("voice", [False, True], ids=["kinds-off", "no-voice"])
def test_the_batch_quizs_closed_kind_draws_the_same_paused_page(app, monkeypatch, voice):
    """The paused page as the batch quiz draws it for a closed kind (its own text): the same two variants,
    with the next-content rows and 回到今日."""
    from kanamemory.intro_batch_ui import QUIZ_PAUSED
    disabled_course(app, voice=voice)
    placeholders(app, monkeypatch)
    course_line.show_paused(app, QUIZ_PAUSED)
    if voice:
        assert course_line.page(app) == ("learning_wait", "需要日语语音")
        assert course_line.texts(app) == VOICE_TEXTS + ["语音与手写", "题型设置"] + ROWS + [HOME]
    else:
        assert course_line.page(app) == ("learning_wait", "题型已关闭")
        assert course_line.texts(app) == [QUIZ_PAUSED, "题型设置"] + ROWS + [HOME]


@pytest.mark.parametrize("saved", ["heard-word-saved", "not-saved"])
def test_the_paused_page_builds_its_rows_before_it_reads_the_kinds(app, monkeypatch, saved):
    """D6: the next-content rows are built before the saved kinds are read; the kinds this device can present
    are read again only when the heard-word kind is saved (odd: a second read of the same setting)."""
    disabled_course(app, voice=saved == "heard-word-saved")
    order = []
    course_line.replace_next_content_rows(app, monkeypatch, lambda: [View(text=text) for text in ROWS], log=order)
    course_line.record_kind_reads(app, monkeypatch, log=order)
    app.continue_learning()
    names = [call.name for call in order]
    tail = names[names.index("next_content_rows"):]
    # The kinds this device can present are read from the saved kinds (the inner read follows its own).
    again = ["enabled_kinds", "saved_kinds"] if saved == "heard-word-saved" else []
    assert tail == ["next_content_rows", "saved_kinds"] + again


def test_d5_with_nothing_left_the_round_is_finished_and_the_end_page_drawn(app, monkeypatch):
    """D5: no head (the type test for "empty"/"done" heads is dead: no head of those types exists, so only
    "no head" reaches it): seal, then mark_finished, page learning_done 课程学习: 本次学习已保存。 (size 20),
    开始新一轮 (primary) -> continue_learning(widget), the next-content rows, 回到今日 -> leave_learning(widget)."""
    course_line.record_controls(monkeypatch)
    placeholders(app, monkeypatch)
    steps = course_line.record_round(monkeypatch)
    log = course_line.watch(app, monkeypatch, names=course_line.DECISION_CALLS)
    targets = Targets(app, monkeypatch, "continue_learning", "leave_learning")
    app.continue_learning()
    targets.drawn()
    assert course_line.page(app) == ("learning_done", "课程学习")
    assert course_line.texts(app) == [DONE_TEXT, AGAIN] + ROWS + [HOME]
    assert course_line.page_made_with(app) == [
        ("label", DONE_TEXT, {"size": 20}), ("button", AGAIN, {"primary": True}), ("button", HOME, {})]
    assert [call.name for call in steps if call.name in ("seal", "mark_finished")] == ["seal", "mark_finished"]
    assert course_line.calls(log) == [("screen", ("learning_done", "课程学习"), {})]
    assert course_line.course_head(app) is None
    targets.presses((AGAIN, "continue_learning"), (HOME, "leave_learning"))


def test_d7_a_diagnostic_head_is_handed_to_the_diagnostic_line_and_its_answer_returned(app, monkeypatch):
    """D7: continue_diagnostic(<episode id>) only; continue_learning returns what it returns; the round was
    sealed and is not finished."""
    app.continue_diagnostic = lambda identity: "〈诊断已继续〉"
    course_line.replace_course_heads(app, monkeypatch, {"type": "diagnostic", "episode_id": "s5-episode"})
    steps = course_line.record_round(monkeypatch)
    log = course_line.watch(app, monkeypatch, real=True, names=course_line.DECISION_CALLS)
    assert app.continue_learning() == "〈诊断已继续〉"
    assert course_line.calls(log) == [("continue_diagnostic", ("s5-episode",), {})]
    assert "seal" in [call.name for call in steps] and "mark_finished" not in [call.name for call in steps]


def test_d8_a_waiting_head_draws_the_wait_page_and_arms_its_timer(app, monkeypatch):
    """D8: page learning_wait 稍后继续: the due label (size 20), 到期后自动继续… (MUTED), 检查并继续 (primary) ->
    check_learning_wait(widget), the next-content rows, the 继续加练 rows (built with the app), 题型设置 ->
    show_learning_settings(widget), 回到今日 -> leave_learning(widget); then the timer armed for the due time
    (one call_soon of the tick with the page token and the due time)."""
    course(app, "wait", due=False)
    app.loop = ControlledLoop()
    course_line.record_controls(monkeypatch)
    _, extra = placeholders(app, monkeypatch)
    arming = course_line.record_wait_arming(app, monkeypatch)
    targets = Targets(app, monkeypatch, "check_learning_wait", "show_learning_settings", "leave_learning")
    app.continue_learning()
    targets.drawn()
    head = course_line.course_head(app)
    due = head["due_at_utc"]
    assert head["type"] == "waiting" and due
    assert course_line.page(app) == ("learning_wait", "稍后继续")
    assert course_line.texts(app) == [due_label(app, due), LATER, CHECK] + ROWS + EXTRA + ["题型设置", HOME]
    assert course_line.page_made_with(app) == [
        ("label", due_label(app, due), {"size": 20}), ("label", LATER, {"color": u.MUTED}),
        ("button", CHECK, {"primary": True}), ("button", "题型设置", {}), ("button", HOME, {})]
    assert course_line.calls(extra) == [("extra_rows", (app,), {})]
    assert course_line.calls(arming) == [("arm_wait", (due,), {})] and arming[0].page == "learning_wait"
    assert course_line.wait_due(app) == due
    timer = course_line.wait_timer(app)
    assert app.loop.handles == [timer] and timer.delay is None and timer.args == (app._page_token, due)
    targets.presses((CHECK, "check_learning_wait"), ("题型设置", "show_learning_settings"), (HOME, "leave_learning"))


def test_d8_a_waiting_head_without_a_due_time_says_no_question_can_be_answered(app, monkeypatch):
    """D8 without a due time: the label 当前题型没有可答题目。; the timer armed with None (nothing scheduled)."""
    app.loop = ControlledLoop()
    placeholders(app, monkeypatch)
    course_line.replace_course_heads(app, monkeypatch, {"type": "waiting"})
    arming = course_line.record_wait_arming(app, monkeypatch)
    app.continue_learning()
    assert course_line.texts(app) == [NO_DUE, LATER, CHECK] + ROWS + EXTRA + ["题型设置", HOME]
    assert course_line.calls(arming) == [("arm_wait", (None,), {})]
    assert course_line.wait_due(app) is None and app.loop.handles == []


def test_d9_a_head_waiting_for_its_prerequisite_draws_its_page_and_arms_the_timer(app, monkeypatch):
    """D9: page learning_wait 先巩固前置知识: the item's title (21, bold), the line (16), the due label (14),
    到期后自动继续… (MUTED), 检查并继续 (primary) -> check_learning_wait(widget), 我已了解前置，继续本课 (plain),
    the next-content rows, 回到今日 -> leave_learning(widget); the timer armed for the due time."""
    base, _ = course(app, "base", due=False)
    top, _ = course(app, "top", taught=False, depends_on=[base])
    app.loop = ControlledLoop()
    course_line.record_controls(monkeypatch)
    placeholders(app, monkeypatch)
    arming = course_line.record_wait_arming(app, monkeypatch)
    targets = Targets(app, monkeypatch, "check_learning_wait", "leave_learning")
    app.continue_learning()
    targets.drawn()
    head = course_line.course_head(app)
    due = head["due_at_utc"]
    assert head["type"] == "prerequisite" and head["content"]["id"] == top and due
    assert course_line.page(app) == ("learning_wait", "先巩固前置知识")
    assert course_line.texts(app) == ["合成top", PREREQUISITE_LINE, due_label(app, due), LATER, CHECK, SKIP] + ROWS + [HOME]
    assert course_line.page_made_with(app) == [
        ("label", "合成top", {"size": 21, "bold": True}), ("label", PREREQUISITE_LINE, {"size": 16}),
        ("label", due_label(app, due), {"size": 14}), ("label", LATER, {"color": u.MUTED}),
        ("button", CHECK, {"primary": True}), ("button", SKIP, {}), ("button", HOME, {})]
    assert course_line.calls(arming) == [("arm_wait", (due,), {})] and course_line.wait_due(app) == due
    targets.presses((CHECK, "check_learning_wait"), (HOME, "leave_learning"))


def test_d9_without_a_due_time_offers_the_settings_and_names_the_prerequisites_already_taught(app, monkeypatch):
    """D9 without a due time: 前置内容的练习暂不可用… (MUTED) and 学习设置 -> show_learning_settings(widget) in
    place of the due lines, then 已学过、可以接着往后学的前置：甲、乙 (14, MUTED) naming only the TAUGHT ones;
    the timer armed with None."""
    app.loop = ControlledLoop()
    course_line.record_controls(monkeypatch)
    placeholders(app, monkeypatch)
    head = {"type": "prerequisite", "content": {"id": "grammar:s5-synthetic:p", "title": "合成p",
                                                "instruction_scope": "s5-scope"},
            "dependency_status": {"dependencies": [{"title": "甲", "status": TAUGHT}, {"title": "丙", "status": "new"},
                                                   {"title": "乙", "status": TAUGHT}]}}
    course_line.replace_course_heads(app, monkeypatch, head)
    arming = course_line.record_wait_arming(app, monkeypatch)
    targets = Targets(app, monkeypatch, "show_learning_settings")
    app.continue_learning()
    targets.drawn()
    taught = TAUGHT_LINE + "甲、乙"
    assert course_line.texts(app) == ["合成p", PREREQUISITE_LINE, PREREQUISITE_NO_DUE, "学习设置", taught, CHECK,
                                      SKIP] + ROWS + [HOME]
    assert course_line.page_made_with(app)[2:5] == [
        ("label", PREREQUISITE_NO_DUE, {"color": u.MUTED}), ("button", "学习设置", {}),
        ("label", taught, {"size": 14, "color": u.MUTED})]
    assert course_line.calls(arming) == [("arm_wait", (None,), {})] and app.loop.handles == []
    targets.presses(("学习设置", "show_learning_settings"))


def test_d10_a_teaching_head_opens_its_teaching_step_with_its_content(app, monkeypatch):
    """D10: show_learning_instruction(<the head's content>) only (here the untouched synthetic lesson is not
    taken by the batch: its batch quiz cannot be presented)."""
    cid, _ = course(app, "teach", taught=False)
    log = course_line.watch(app, monkeypatch, names=course_line.DECISION_CALLS)
    app.continue_learning()
    head = course_line.course_head(app)
    assert head["type"] == "instruction"
    ((name, args, kwargs),) = course_line.calls(log)
    assert (name, kwargs) == ("show_learning_instruction", {}) and args[0] is head["content"] and len(args) == 1
    assert args[0]["id"] == cid


def test_d11_a_question_head_opens_the_question_with_the_whole_head(app, monkeypatch):
    """D11: show_learning_question(<the head>) only, the same object the line remembers."""
    _, qid = course(app, "ask")
    log = course_line.watch(app, monkeypatch, names=course_line.DECISION_CALLS)
    app.continue_learning()
    ((name, args, kwargs),) = course_line.calls(log)
    assert (name, kwargs) == ("show_learning_question", {}) and args[0] is course_line.course_head(app)
    assert args[0]["question"]["id"] == qid


def test_d12_an_unknown_head_raises_after_the_round_was_sealed_and_the_head_remembered(app, monkeypatch):
    """D12: ValueError("Unsupported learning dispatch state"); seal already ran, the head is remembered,
    nothing was drawn or called."""
    head = {"type": "s5-unknown"}
    course_line.replace_course_heads(app, monkeypatch, head)
    steps = course_line.record_round(monkeypatch)
    log = course_line.watch(app, monkeypatch, names=course_line.DECISION_CALLS)
    with pytest.raises(ValueError, match="^Unsupported learning dispatch state$"):
        app.continue_learning()
    assert "seal" in [call.name for call in steps] and course_line.course_head(app) is head and log == []


# --- b. the order before the branches ------------------------------------------------------------------------------

def test_p1_a_press_restarts_a_finished_round_and_an_entry_without_a_widget_does_not(app, monkeypatch):
    """P1: begin(restart_finished=True) with a widget, False without; with app-run rounds a finished round
    stays finished (same order token) on an entry without a widget and starts again on a press."""
    practice_round.enable(app.store)
    steps = course_line.record_round(monkeypatch)
    app.continue_learning()
    first = course_line.round_plan(app)
    assert first[1]
    app.continue_learning()
    assert course_line.round_plan(app) == first
    app.continue_learning(View())
    again = course_line.round_plan(app)
    assert again[1] and again[3] != first[3]
    assert [call.kwargs for call in steps if call.name == "begin"] == [
        {"restart_finished": False}, {"restart_finished": False}, {"restart_finished": True}]


def order_log(app, monkeypatch, *, heads=None):
    log = []
    course_line.record_round(monkeypatch, log=log)
    course_line.record_queueing(app, monkeypatch, log=log)
    names = ("admit_word_listening", "ensure_prerequisites")
    course_line.record_store_calls(app, monkeypatch, *names, log=log)
    if heads is None:
        course_line.record_head_reads(app, monkeypatch, log=log)
    else:
        course_line.replace_course_heads(app, monkeypatch, *heads, log=log)
    course_line.record_batch_route(app, monkeypatch, log=log)
    return log


def first(log, name):
    return next(index for index, call in enumerate(log) if call.name == name)


@pytest.mark.parametrize("listening", [False, True])
def test_p1_to_p11_run_in_order_and_the_head_is_remembered_before_the_batch_route(app, monkeypatch, listening):
    """P1-P11 in order: begin, sealed (False), [admit_word_listening(enabled_kinds=...) only with the heard-word
    kind], ensure_prerequisites(), the queueing, current(...), seal, then the batch route with the head already
    remembered."""
    course(app, "order")
    if listening:
        app.store.set_setting(PREFS, {"enabled": ["grammar_meaning", lq.AUDIO_WORD_KIND],
                                      lq.SOUND_PREFERENCE_VERSION: lq.PREFERENCE_VERSION})
        app.platform.tts_ready = True
    log = order_log(app, monkeypatch)
    app.continue_learning()
    wanted = ["begin", "sealed", "admit_word_listening", "ensure_prerequisites", course_line.QUEUEING, "current",
              "seal", "route_intro_batch"]
    seen = [name for name in wanted if any(call.name == name for call in log)]
    assert seen == [name for name in wanted if listening or name != "admit_word_listening"]
    assert [first(log, name) for name in seen] == sorted(first(log, name) for name in seen)
    assert log[first(log, "sealed")].result is False
    enabled = log[first(log, "current")].args[0]
    if listening:
        assert log[first(log, "admit_word_listening")].kwargs == {"enabled_kinds": enabled}
    assert log[first(log, "ensure_prerequisites")].args == () == tuple(log[first(log, "ensure_prerequisites")].kwargs)
    assert log[first(log, "seal")].args == ()
    route = log[first(log, "route_intro_batch")]
    assert route.head is log[first(log, "current")].result is course_line.course_head(app)


def test_p2_p6_a_round_sealed_before_the_entry_queues_nothing(app, monkeypatch):
    """P2/P6: with app-run rounds, the first entry queues (the round was not sealed) and seals it; the next
    entry reads it sealed and queues nothing."""
    practice_round.enable(app.store)
    course(app, "sealed")
    log = order_log(app, monkeypatch)
    app.continue_learning()
    assert [call.name for call in log].count(course_line.QUEUEING) == 1
    del log[:]
    app.continue_learning()
    assert course_line.QUEUEING not in [call.name for call in log]
    assert log[first(log, "sealed")].result is True and first(log, "sealed") < first(log, "current")


@pytest.mark.parametrize("projection", [False, True], ids=["direct", "with-read"])
def test_p9_a_withdrawn_head_is_exchanged_and_read_again_live_at_most_eight_times(app, monkeypatch, projection):
    """P9: a withdrawn question head is exchanged (its question id) and the head read again without the read
    context; 8 exchanges at most, then the head is shown as it was stored (D11)."""
    reads = course_line.replace_course_heads(app, monkeypatch, withdrawn_head())
    exchanges = course_line.record_withdrawn_exchange(app, monkeypatch, answer=lambda question_id: 1)
    log = course_line.watch(app, monkeypatch, names=course_line.DECISION_CALLS)
    if projection:
        with app.learning_store.read_operation() as read:
            app.continue_learning(_projection=read)
    else:
        read = None
        app.continue_learning()
    passes = [call for call in exchanges if call.args]
    assert [call.args for call in passes] == [("s5-withdrawn",)] * 8
    heads = [call for call in reads if call.name == "current"]
    assert len(heads) == 9
    assert ("projection" in heads[0].kwargs) is projection and (heads[0].kwargs.get("projection") is read)
    assert all("projection" not in call.kwargs for call in heads[1:])
    assert [call.name for call in log] == ["show_learning_question"]


def test_p9_a_withdrawn_head_that_cannot_be_exchanged_is_read_once_and_shown(app, monkeypatch):
    """P9: an exchange that changes nothing (0) ends the loop: one read of the head, the head shown."""
    reads = course_line.replace_course_heads(app, monkeypatch, withdrawn_head())
    exchanges = course_line.record_withdrawn_exchange(app, monkeypatch, answer=lambda question_id: 0)
    log = course_line.watch(app, monkeypatch, names=course_line.DECISION_CALLS)
    app.continue_learning()
    assert [call.args for call in exchanges if call.args] == [("s5-withdrawn",)]
    assert [call.name for call in reads] == ["current"]
    assert course_line.calls(log) == [("show_learning_question", (withdrawn_head(),), {})]


def test_p9_an_exchanged_head_is_followed_by_the_new_head(app, monkeypatch):
    """P9: after one exchange the re-read head is the one taken (here a question that is not withdrawn)."""
    _, qid = course(app, "fresh")
    fresh = app.learning_store.current(["grammar_meaning"])
    course_line.replace_course_heads(app, monkeypatch, withdrawn_head(), fresh)
    course_line.record_withdrawn_exchange(app, monkeypatch, answer=lambda question_id: 1)
    log = course_line.watch(app, monkeypatch, names=course_line.DECISION_CALLS)
    app.continue_learning()
    assert course_line.calls(log) == [("show_learning_question", (fresh,), {})]
    assert course_line.course_head(app) is fresh


def test_p8_the_head_is_asked_with_the_kinds_the_fairness_switch_and_the_skipped_teaching(app, monkeypatch):
    """P8: current(enabled, fair_new=<new-work fairness on the main lane>), plus skip_teaching_ids=<frozenset>
    only when teaching was passed over in this run; a direct entry passes no read context."""
    course(app, "head")
    enabled = app.learning_preferences()["enabled"]
    reads = course_line.record_head_reads(app, monkeypatch)
    app.continue_learning()
    assert course_line.calls(reads) == [("current", (enabled,), {"fair_new": True})]
    course_line.set_skipped_teaching(app, {"grammar:s5-synthetic:other"})
    del reads[:]
    app.continue_learning()
    assert course_line.calls(reads) == [("current", (enabled,), {
        "fair_new": True, "skip_teaching_ids": frozenset({"grammar:s5-synthetic:other"})})]


# --- c. the two kinds of entry ------------------------------------------------------------------------------------

def test_an_entry_through_the_continue_action_reads_the_head_inside_its_read_context(app, monkeypatch):
    """Through the continue action: continue_study_path -> continue_learning(_projection=read): current(..., projection=<that read>);
    a direct entry (a page's button) passes no projection at all."""
    course(app, "entry")
    log = course_line.record_entries(app, monkeypatch, real=True, names=("continue_learning",))
    course_line.record_head_reads(app, monkeypatch, log=log)
    app.continue_study_path()
    start = first(log, "continue_learning")
    read = log[start].kwargs["_projection"]
    (head,) = [call for call in log[start:] if call.name == "current"]
    assert head.kwargs["projection"] is read
    del log[:]
    app.continue_learning()
    (head,) = [call for call in log if call.name == "current"]
    assert "projection" not in head.kwargs


def selected_setup(app):
    """Two taught courses: "live" is due (the store's head), "kept" waits; returns the kept course's head as
    the continue action would select it (a question head)."""
    course(app, "live")
    cid, qid = course(app, "kept", due=False)
    return {"type": "question", "content": app.learning_store.get_item(cid),
            "question": app.learning_store.get_question(qid)}


@pytest.mark.parametrize("reason", ["kept", "gone", "superseded", "blocked", "blocked-answered", "prerequisite"])
def test_p7_a_selected_question_head_is_kept_unless_a_check_drops_it(app, monkeypatch, reason):
    """P7 for a question head: kept as given (the same object is shown); dropped (the store's head is shown)
    when its question is gone or superseded, when it is unanswered and its content is blocked, or when its
    prerequisite is blocked. A blocked content does not drop an answered question."""
    selected = selected_setup(app)
    kept_id, kept_qid = selected["content"]["id"], selected["question"]["id"]
    store = app.learning_store
    blocked, status = store.content_question_blocked, store.prerequisite_status
    answers = {}
    if reason == "gone":
        selected["question"] = dict(selected["question"], id="s5-question:none")
    elif reason == "superseded":
        fetch = store.get_question
        answers["get_question"] = lambda qid, *a, **k: (
            dict(fetch(qid, *a, **k), status="superseded") if qid == kept_qid else fetch(qid, *a, **k))
    elif reason.startswith("blocked"):
        answers["content_question_blocked"] = lambda content, q, *a, **k: (
            q.get("id") == kept_qid or blocked(content, q, *a, **k))
        if reason == "blocked-answered":
            selected["question"] = dict(selected["question"], result={"status": "correct"})
    elif reason == "prerequisite":
        answers["prerequisite_status"] = lambda cid, *a, **k: (
            {"blocked": True} if cid == kept_id else status(cid, *a, **k))
    asked = course_line.record_store_calls(app, monkeypatch, "content_question_blocked", "prerequisite_status",
                                           "get_question", answers=answers)
    log = course_line.watch(app, monkeypatch, names=course_line.DECISION_CALLS)
    app.continue_learning(_selected_current=selected)
    ((name, (shown,), _),) = course_line.calls(log)
    assert name == "show_learning_question"
    if reason in {"kept", "blocked-answered"}:
        assert shown is selected
    else:
        assert shown["question"]["id"] == "s5-question:live"
    if reason == "prerequisite":
        assert course_line.calls(asked, "prerequisite_status")[0] == ("prerequisite_status", (kept_id, None), {})


@pytest.mark.parametrize("reason", ["kept", "guide-kind-off", "guide-kind-on", "skipped"])
def test_p7_a_selected_teaching_head_is_dropped_for_a_closed_guide_kind_or_passed_over_teaching(
        app, monkeypatch, reason):
    """P7 for a teaching head: dropped when its frozen practice guide uses a kind that is off, or when the
    learner passed over its teaching in this run; else its teaching step opens with its content."""
    selected = selected_setup(app)
    content = dict(selected["content"], stage="guide")
    if reason.startswith("guide"):
        kind = "vocab_meaning" if reason == "guide-kind-off" else "grammar_meaning"
        content["checkpoint"] = {"guide_question": {"practice": True, "kind": kind}}
    if reason == "skipped":
        course_line.set_skipped_teaching(app, {content["id"]})
    selected = dict(selected, type="instruction", content=content)
    log = course_line.watch(app, monkeypatch, names=course_line.DECISION_CALLS)
    app.continue_learning(_selected_current=selected)
    ((name, (shown,), _),) = course_line.calls(log)
    if reason in {"guide-kind-off", "skipped"}:
        assert name == "show_learning_question" and shown["question"]["id"] == "s5-question:live"
    else:
        assert name == "show_learning_instruction" and shown is content


def test_p7_a_kept_selected_head_is_not_read_from_the_store(app, monkeypatch):
    """P8: a selected head that passes P7 is the head; current() is not asked."""
    selected = selected_setup(app)
    reads = course_line.record_head_reads(app, monkeypatch)
    app.continue_learning(_selected_current=selected)
    assert reads == [] and course_line.course_head(app) is selected


# --- d. a sealed round's left-out work ---------------------------------------------------------------------------

@pytest.mark.parametrize("left_out", [True, False])
def test_a_disabled_head_left_out_of_a_sealed_round_ends_the_round(app, monkeypatch, left_out):
    """N12: sealed before the entry and a "disabled" head: the left-out check is asked (head, read context);
    True drops the head (the line remembers None) and ends the round (D5: 本次学习已保存); False keeps it (D6)."""
    disabled_course(app)
    placeholders(app, monkeypatch)
    course_line.patch_round_sealed(monkeypatch, lambda store: True)
    asked = course_line.record_left_out(app, monkeypatch, answer=left_out)
    app.continue_learning()
    ((_, (current, read), _),) = course_line.calls(asked)
    assert current["type"] == "disabled" and read is None
    if left_out:
        assert course_line.page(app) == ("learning_done", "课程学习") and course_line.course_head(app) is None
    else:
        assert course_line.page(app) == ("learning_wait", "题型已关闭")
        assert course_line.texts(app)[0] == D6_TEXT


def test_the_left_out_check_is_asked_only_for_a_round_sealed_before_the_entry(app, monkeypatch):
    """N12: a round not sealed before the entry never asks the check (the head stays "disabled": D6)."""
    disabled_course(app)
    placeholders(app, monkeypatch)
    asked = course_line.record_left_out(app, monkeypatch, answer=True)
    app.continue_learning()
    assert asked == [] and course_line.page(app) == ("learning_wait", "题型已关闭")


def test_the_left_out_check_gets_the_entry_read_context_until_an_exchange_drops_it(app, monkeypatch):
    """N12 with P9: the check gets the entry's read context; after a withdrawn-task exchange it gets None."""
    disabled = {"type": "disabled"}
    for heads, exchanged in (((disabled,), False), ((withdrawn_head(), disabled), True)):
        with monkeypatch.context() as patch:
            course_line.patch_round_sealed(patch, lambda store: True)
            placeholders(app, patch)
            asked = course_line.record_left_out(app, patch, answer=False)
            course_line.replace_course_heads(app, patch, *heads)
            course_line.record_withdrawn_exchange(app, patch, answer=lambda question_id: 1)
            with app.learning_store.read_operation() as read:
                app.continue_learning(_projection=read)
            assert course_line.calls(asked) == [("left_out", (disabled, None if exchanged else read), {})]


def test_a_standalone_course_controller_has_no_left_out_check(learning_app, monkeypatch):
    """N12 without the study path (learning_app): no check exists, so a sealed round's disabled head keeps
    the paused page; the next-content row is the standalone 加入新内容 (primary) -> show_learning_library."""
    app = learning_app
    disabled_course(app)
    course_line.patch_round_sealed(monkeypatch, lambda store: True)
    course_line.record_controls(monkeypatch)
    app.continue_learning()
    assert course_line.page(app) == ("learning_wait", "题型已关闭")
    assert course_line.texts(app) == [D6_TEXT, "题型设置", "加入新内容", HOME]
    assert ("button", "加入新内容", {"primary": True}) in course_line.page_made_with(app)


# --- e. what the decision's pages hand back ------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["waiting", "prerequisite"])
def test_check_and_continue_hands_back_to_the_continue_action_without_arguments(app, monkeypatch, which):
    """D8/D9: 检查并继续 -> check_learning_wait(widget) -> continue_study_path() with no argument at all (no
    widget, no _checkpoint_saved: the round restarts if it is finished)."""
    if which == "waiting":
        course(app, "check", due=False)
    else:
        base, _ = course(app, "cbase", due=False)
        course(app, "ctop", taught=False, depends_on=[base])
    placeholders(app, monkeypatch)
    app.continue_learning()
    log = course_line.record_entries(app, monkeypatch, real=False, names=("continue_study_path", "continue_learning"))
    course_line.press(app, CHECK)
    assert course_line.calls(log) == [("continue_study_path", (), {})]


def test_check_and_continue_does_nothing_once_the_page_was_left_or_while_leaving_is_asked(app, monkeypatch):
    """D8: a kept 检查并继续 acts only while a wait page is shown and no 返回今日？ question is open."""
    course(app, "gone", due=False)
    placeholders(app, monkeypatch)
    app.continue_learning()
    widget = course_line.control(app, CHECK)
    log = course_line.record_entries(app, monkeypatch, real=False, names=("continue_study_path", "continue_learning"))
    app._leave_confirmation_active = True
    widget.on_press(widget)
    app._leave_confirmation_active = False
    app.page = "home"
    widget.on_press(widget)
    assert log == []


def test_skip_prerequisites_acknowledges_then_hands_back_and_still_acts_after_the_page_was_left(app, monkeypatch):
    """D9: 我已了解前置，继续本课 -> acknowledge_prerequisites(<item id>, instruction_scope=<its scope>), then
    continue_study_path() with no argument; a kept button still acts after the page was left (no guard: odd)."""
    base, _ = course(app, "sbase", due=False)
    top, _ = course(app, "stop", taught=False, depends_on=[base])
    placeholders(app, monkeypatch)
    app.continue_learning()
    widget = course_line.control(app, SKIP)
    log = course_line.record_store_calls(app, monkeypatch, "acknowledge_prerequisites",
                                         answers={"acknowledge_prerequisites": lambda *a, **k: None})
    course_line.record_entries(app, monkeypatch, real=False, log=log, names=("continue_study_path", "continue_learning"))
    course_line.press_skip_prerequisites(app)
    assert course_line.calls(log) == [("acknowledge_prerequisites", (top,), {"instruction_scope": None}),
                                      ("continue_study_path", (), {})]
    app.page = "home"
    widget.on_press(widget)
    assert [call.name for call in log[2:]] == ["acknowledge_prerequisites", "continue_study_path"]


def test_skip_prerequisites_passes_the_heads_instruction_scope(app, monkeypatch):
    """D9: the acknowledgement names the head's own instruction scope."""
    placeholders(app, monkeypatch)
    head = {"type": "prerequisite", "content": {"id": "grammar:s5-synthetic:sc", "title": "合成sc",
                                                "instruction_scope": "s5-scope"}, "due_at_utc": None}
    course_line.replace_course_heads(app, monkeypatch, head)
    app.continue_learning()
    log = course_line.record_store_calls(app, monkeypatch, "acknowledge_prerequisites",
                                         answers={"acknowledge_prerequisites": lambda *a, **k: None})
    course_line.record_entries(app, monkeypatch, real=False, log=log, names=("continue_study_path",))
    course_line.press_skip_prerequisites(app)
    assert course_line.calls(log) == [
        ("acknowledge_prerequisites", ("grammar:s5-synthetic:sc",), {"instruction_scope": "s5-scope"}),
        ("continue_study_path", (), {})]


def test_without_the_study_path_the_pages_hand_back_to_the_course_line_itself(learning_app, monkeypatch):
    """Standalone (learning_app): 检查并继续 -> continue_learning() and 我已了解前置 -> continue_learning(), both
    without arguments."""
    app = learning_app
    head = {"type": "prerequisite", "content": {"id": "grammar:s5-synthetic:alone", "title": "合成alone"},
            "due_at_utc": None}
    course_line.replace_course_heads(app, monkeypatch, head)
    app.continue_learning()
    check, skip = course_line.control(app, CHECK), course_line.control(app, SKIP)
    log = course_line.record_store_calls(app, monkeypatch, "acknowledge_prerequisites",
                                         answers={"acknowledge_prerequisites": lambda *a, **k: None})
    course_line.record_entries(app, monkeypatch, real=False, log=log, names=("continue_learning",))
    check.on_press(check)
    skip.on_press(skip)
    assert course_line.calls(log) == [
        ("continue_learning", (), {}),
        ("acknowledge_prerequisites", ("grammar:s5-synthetic:alone",), {"instruction_scope": None}),
        ("continue_learning", (), {})]


def test_start_again_restarts_the_finished_round_through_the_course_line(app, monkeypatch):
    """D5: 开始新一轮 -> continue_learning(widget): begin(restart_finished=True), so a finished app-run round
    starts again; a kept button still acts after the page was left (no guard)."""
    practice_round.enable(app.store)
    placeholders(app, monkeypatch)
    app.continue_learning()
    finished = course_line.round_plan(app)
    assert finished[1]
    steps = course_line.record_round(monkeypatch)
    targets = Targets(app, monkeypatch, "continue_learning")
    course_line.press(app, AGAIN)
    targets.drawn()
    assert [call.kwargs for call in steps if call.name == "begin"] == [{"restart_finished": True}]
    assert course_line.round_plan(app)[3] != finished[3]
    widget = course_line.control(app, AGAIN)
    app.page = "home"
    widget.on_press(widget)
    assert course_line.calls(targets.log) == [("continue_learning", (widget,), {})]


def replaceable(app, monkeypatch, *, commit_error=None):
    """A pending synthetic question whose replacement is prepared by a stand-in record; returns its id and the
    log of prepare / commit calls."""
    _, qid = course(app, "repl")
    log = []

    def prepare_question(self, question_id, **kwargs):
        log.append(("prepare_question", question_id, kwargs))
        return {"prepared": question_id}

    def commit(self, prepared):
        log.append(("commit", prepared))
        if commit_error is not None:
            raise commit_error
    course_line.patch_replacement_service(monkeypatch, prepare_question=prepare_question, commit=commit)
    return qid, log


def test_the_replacement_action_is_prepared_when_drawn_and_committed_when_pressed(app, monkeypatch):
    """D2's replacement rows: one primary 使用更新后的练习继续, prepared (prepare_question(<id>)) when drawn and
    committed only when pressed; then the line forgets its head and continues with continue_learning() (no
    argument). A kept button still acts after the page was left (no guard)."""
    qid, service = replaceable(app, monkeypatch)
    course_line.record_controls(monkeypatch)
    course_line.set_course_head(app, {"type": "content_blocked"})
    rows = course_line.replacement_controls(app, [qid])
    assert [(view.text, view._made) for view in rows] == [(REPLACE, ("button", {"primary": True}))]
    assert service == [("prepare_question", qid, {})]
    log = course_line.record_entries(app, monkeypatch, real=False, names=("continue_learning",
                                                                          "show_learning_instruction"))
    rows[0].on_press(rows[0])
    assert service[1:] == [("commit", {"prepared": qid})]
    assert course_line.calls(log) == [("continue_learning", (), {})] and course_line.course_head(app) is None
    app.page = "home"
    rows[0].on_press(rows[0])
    assert service[2:] == [("commit", {"prepared": qid})] and len(log) == 2


def test_a_failed_replacement_commit_alerts_and_continues_without_forgetting_the_head(app, monkeypatch):
    """D2's replacement action: a ValueError on commit -> alert(<its message>), then continue_learning(); the
    remembered head is left as it was."""
    qid, _ = replaceable(app, monkeypatch, commit_error=ValueError("合成故障"))
    head = {"type": "content_blocked"}
    course_line.set_course_head(app, head)
    (button,) = course_line.replacement_controls(app, [qid])
    app.alert = Mock()
    log = course_line.record_entries(app, monkeypatch, real=False, names=("continue_learning",))
    button.on_press(button)
    app.alert.assert_called_once_with("合成故障")
    assert course_line.calls(log) == [("continue_learning", (), {})] and course_line.course_head(app) is head


def test_a_replacement_that_cannot_be_prepared_is_not_offered(app, monkeypatch):
    """D2's replacement rows: a question that is answered or no longer pending is not a candidate; one whose
    preparation raises is skipped: no rows."""
    qid, service = replaceable(app, monkeypatch)

    def refuse(self, question_id, **kwargs):
        raise ValueError("合成")
    assert course_line.replacement_controls(app, ["s5-question:none"]) == [] and service == []
    course_line.patch_replacement_service(monkeypatch, prepare_question=refuse)
    assert course_line.replacement_controls(app, [qid]) == []


def test_a_blocked_frozen_guide_is_replaced_and_its_teaching_reopened(app, monkeypatch):
    """The replacement rows for a teaching item in its guide stage whose frozen guide is blocked: prepared with
    prepare_guide(<id>, <scope>); pressed: commit, then show_learning_instruction(<the item read again>)."""
    cid, _ = course(app, "guide")
    log = []
    course_line.patch_guide_blocked(monkeypatch, lambda store, current, guide: True)
    course_line.patch_replacement_service(
        monkeypatch, prepare_guide=lambda self, content_id, scope: log.append(("prepare_guide", content_id, scope))
        or {"guide": content_id}, commit=lambda self, prepared: log.append(("commit", prepared)))
    teaching = dict(app.learning_store.get_item(cid), stage="guide", checkpoint={"guide_question": {"id": "g"}})
    (button,) = course_line.replacement_controls(app, item=teaching)
    assert log == [("prepare_guide", cid, None)]
    calls = course_line.record_entries(app, monkeypatch, real=False, names=("show_learning_instruction",
                                                                            "continue_learning"))
    button.on_press(button)
    assert log[1:] == [("commit", {"guide": cid})]
    ((name, (shown,), kwargs),) = course_line.calls(calls)
    assert (name, kwargs, shown["id"]) == ("show_learning_instruction", {}, cid)


# --- f. page names and Back ---------------------------------------------------------------------------------------

def reach(app, monkeypatch, tmp_path, which):
    placeholders(app, monkeypatch)
    if which == "library":
        orphaned_course(app, tmp_path)
    elif which == "material":
        course_line.replace_course_heads(app, monkeypatch, {"type": "content_blocked", "question_ids": []})
    elif which == "paused":
        disabled_course(app)
    elif which == "voice":
        disabled_course(app, voice=True)
    elif which == "waiting":
        course(app, "back", due=False)
    elif which == "prerequisite":
        base, _ = course(app, "bbase", due=False)
        course(app, "btop", taught=False, depends_on=[base])
    app.continue_learning()


PAGES = [("library", "learning_wait", "所属内容库已停用"), ("material", "learning_wait", "材料待核"),
         ("paused", "learning_wait", "题型已关闭"), ("voice", "learning_wait", "需要日语语音"),
         ("done", "learning_done", "课程学习"), ("waiting", "learning_wait", "稍后继续"),
         ("prerequisite", "learning_wait", "先巩固前置知识")]


@pytest.mark.parametrize("which,name,title", PAGES, ids=[entry[0] for entry in PAGES])
def test_the_decisions_pages_are_focused_learning_pages_that_back_leaves_at_once(
        app, monkeypatch, tmp_path, which, name, title):
    """The six pages carry the page names learning_wait / learning_done: focused learning pages that Back
    leaves at once (no 返回今日？): system Back (and the header button, the same go_back) -> leave_learning ->
    home, the back stack cleared."""
    reach(app, monkeypatch, tmp_path, which)
    assert course_line.page(app) == (name, title)
    assert course_line.is_learning_page(name) and course_line.is_focused_page(name)
    assert course_line.leaves_at_once(name)
    app.loop = Mock()
    log = course_line.record_entries(app, monkeypatch, real=True, names=("leave_learning",))
    assert KanaMemory.go_back(app) is True
    assert app.page == "home" and not app.loop.create_task.called and not app.main_window.dialog.called
    assert course_line.calls(log) == [("leave_learning", (), {})] and app.back_stack.pages == []


# --- the design's further pins ----------------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["waiting", "prerequisite"])
def test_the_wait_timer_is_armed_after_the_page_is_drawn(app, monkeypatch, which):
    """D8/D9: arm_learning_wait comes after the page's screen() call, with the head's stored due time."""
    if which == "waiting":
        course(app, "after", due=False)
    else:
        base, _ = course(app, "abase", due=False)
        course(app, "atop", taught=False, depends_on=[base])
    placeholders(app, monkeypatch)
    log = course_line.watch(app, monkeypatch, names=())
    course_line.record_wait_arming(app, monkeypatch, log=log)
    app.continue_learning()
    assert [call.name for call in log] == ["screen", "arm_wait"]
    assert log[1].args == (course_line.course_head(app)["due_at_utc"],) == (course_line.wait_due(app),)


@pytest.mark.parametrize("caption", ["题型设置", "学习设置"])
def test_back_from_the_settings_page_rearms_the_wait_timer_of_the_retained_page(app, monkeypatch, caption):
    """D8/D9: from the wait page 题型设置 / 学习设置 open the settings page (it remembers the wait page); Back
    remounts that same page and arms its timer again with the due time it was drawn with."""
    from types import MethodType
    from kanamemory.learning_ui import LearningUIMixin
    from support.learning_fixtures import use_real_detail_back
    use_real_detail_back(app)
    app._view_key = ("show_home", None)
    app.screen("home", "今日学习", View(text="首页"))
    for name in ("continue_learning", "show_learning_settings"):
        setattr(app, name, MethodType(getattr(LearningUIMixin, name), app))
    if caption == "题型设置":
        course(app, "reback", due=False)
    else:
        course_line.replace_course_heads(app, monkeypatch, {
            "type": "prerequisite", "content": {"id": "grammar:s5-synthetic:rb", "title": "合成rb"}})
    placeholders(app, monkeypatch)
    arming = course_line.record_wait_arming(app, monkeypatch)
    app.continue_learning()
    name, view, due = app.page, app.content_slot.children[0], course_line.wait_due(app)
    course_line.press(app, caption)
    assert app.page == "learning_settings"
    del arming[:]
    app.go_back()
    assert app.page == name and app.content_slot.children[0] is view
    assert course_line.calls(arming) == [("arm_wait", (due,), {})]


def test_after_an_exchange_the_counts_get_no_read_context(app, monkeypatch):
    """D4 with P9: without an exchange counts(enabled, projection=<the entry's read>); after a withdrawn-task
    exchange counts(enabled) without one."""
    disabled_course(app)
    placeholders(app, monkeypatch)
    for heads, exchanged in (((None,), False), ((withdrawn_head(), None), True)):
        with monkeypatch.context() as patch:
            reads = course_line.replace_course_heads(app, patch, *heads)
            course_line.record_withdrawn_exchange(app, patch, answer=lambda question_id: 1)
            with app.learning_store.read_operation() as read:
                app.continue_learning(_projection=read)
            (counts,) = [call for call in reads if call.name == "counts"]
            assert counts.args == ([],) and counts.kwargs == ({} if exchanged else {"projection": read})
            assert course_line.page(app) == ("learning_wait", "题型已关闭")


def test_a_head_left_out_of_a_sealed_round_never_reads_the_counts(app, monkeypatch):
    """N12: the left-out path ends the round without asking counts (the D4 check is skipped)."""
    disabled_course(app)
    placeholders(app, monkeypatch)
    course_line.patch_round_sealed(monkeypatch, lambda store: True)
    course_line.record_left_out(app, monkeypatch, answer=True)
    reads = course_line.record_head_reads(app, monkeypatch)
    app.continue_learning()
    assert [call.name for call in course_line.own_calls(reads)] == ["current"]
    assert course_line.page(app)[0] == "learning_done"


def test_skip_prerequisites_hands_back_to_a_continue_action_that_restarts_a_finished_round(app, monkeypatch):
    """D9: the hand-back of 我已了解前置，继续本课 is continue_study_path() with no argument, so the continue action
    begins with restart_finished=True."""
    base, _ = course(app, "rbase", due=False)
    course(app, "rtop", taught=False, depends_on=[base])
    placeholders(app, monkeypatch)
    app.continue_learning()
    steps = course_line.record_round(monkeypatch)
    course_line.press_skip_prerequisites(app)
    assert [call.kwargs for call in steps if call.name == "begin"][0] == {"restart_finished": True}


def test_the_failure_page_of_the_continue_action_names_the_head_this_action_remembered(app, monkeypatch):
    """P11 and the continue action's error page: a failure after the line remembered its head names that head's
    title, even when an equal head (another object) was remembered before the action."""
    course(app, "title")
    expected = app.learning_store.current(app.learning_preferences()["enabled"], fair_new=True)
    course_line.set_course_head(app, dict(expected))

    def failing(current):
        raise RuntimeError("合成")
    app.show_learning_question = failing
    with pytest.raises(RuntimeError):
        app.continue_study_path()
    assert course_line.page(app) == ("learning_wait", "无法准备下一题")
    assert course_line.texts(app)[0] == "合成title"
