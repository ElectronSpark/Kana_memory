"""继续学习 (the continue action): which call each outcome makes, its own pages, and the two waits.

Design 13 stage S4 (docs/design_current/13_frontend_backend_separation.txt, stage S4 and acceptance A1) moves
the continue action's decision into its own module, which returns a description, and draws its three inline
pages (本轮已保存, 暂时没有可进行的假名题型, 本次学习已保存) from models; it keeps the behaviour pinned here.
The tests reach the action's state and seams only through tests/support/continue_support.py, so the move
changes that accessor and not these tests. Expected values are what the code does, including what looks odd
(each such test says so).

Outcome labels. P: before the decision (_continue_study_path), D: the decision (_continue_dispatch), each
taken by the first line that applies; "no lane" = neither lane head was chosen; a "token" is the prepared kana
head (_prepared), passed only when there is one:
  P1  resuming from W2: only the decision runs again, with the worker's course head (P2-P7 skipped).
  P2  a press (no _checkpoint_saved) whose checkpoint fails: returns, nothing drawn or called.
  P3  switching back to the main kana lane fails (its checkpoint): returns, nothing drawn, the lane stays.
  P4  the round is not sealed and W1 starts: the wait page is drawn, the action returns.
  P5  the kana session's kinds cannot be reconciled: an alert only, the current page stays.
  P6  W2 starts: the wait page is drawn, the action returns.
  P7  otherwise: the decision (D1-D14) runs inside one read context of this action.
  P8  any failure in P1-P7: record_error(kana_queue_assembly, read), page 无法准备下一题, the error raised again.
  D1  a protected (explicitly started) course head: continue_learning(_selected_current=head, _projection=read).
  D2  a protected kana head: resume_practice(preserve_audio=True, _first_teaching=True, _first_teaching_targets).
  D3  the kana lane is chosen: resume_practice(preserve_audio=True, plus the token).
  D4  the course lane is chosen: continue_learning(_projection=read).
  D5  no lane, no ready or waiting kana, no course head the round can still serve, and mark_finished ends an
      app-run round: take_over takes over (gated kana do not keep the round open).
  D6  as D5, take_over refuses: the page 本轮已保存.
  D7  no lane and a reference entry: show_study_reference(<its id>).
  D8  waiting kana due before the course head (or there is none, or it has no due time): take_over, else
      resume_practice(token only, no preserve_audio).
  D9  a course head is left: take_over only when it is "waiting"; else (or refused) continue_learning(_projection).
  D10 waiting kana without a due time (only a patched summary): take_over, else resume_practice(token only).
  D11 only gated kana: resume_practice(token only), never take_over.
  D12 a kana entry added or started in the route, nothing above: page 暂时没有可进行的假名题型, no take_over.
  D13 nothing at all: take_over takes over.
  D14 as D13, take_over refuses: the page 本次学习已保存 (the route's next section, or other routes).
  W1  the kana queue is assembled in the background (switch on, round not sealed); then the whole action reruns
      with that plan (a data change: one more attempt, then inline).
  W2  the course head of a sealed round is computed in the background (switch on, no teaching skipped); then P1.

Public: synthetic course items (support.learning_store_fixtures, ids "grammar:s4-synthetic:*"), the kana
あ/い/う of the bundled table (also in the demo content), the first all-kana section of the core route and
the core route's own next section, read from the content at run time. Course items that need real lessons
(a non-core route, the B10 note's route) are in test_continue_characterisation_course.py.
"""
import asyncio
from datetime import timedelta
from unittest.mock import Mock

import pytest

from kanamemory import diagnostic_events, explicit_learning_start, practice_round
from kanamemory import ui_components as u
from kanamemory.app import KanaMemory
from kanamemory.arrangement import display
from kanamemory.arrangement.model import Orientation
from kanamemory.arrangement.services import ArrangementPreferences, ExtraSession
from kanamemory.learning_store import LearningStore
from kanamemory.practice_availability import QueueAvailability
from support import continue_support as cs
from support.app_fixtures import question_app, setup_app  # noqa: F401
from support.fake_ui import ScrollView, View
from support.learning_fixtures import learning_app  # noqa: F401
from support.learning_store_fixtures import item, question, ready
from support.study_home_support import orientation_seen, stub_can_continue
from support.study_path_runtime_support import kana_id, path_app, seed_main  # noqa: F401

PREFS = "learning_preferences_v1"
LINES = ("continue_learning", "resume_practice", "show_study_reference")
ROUND_END = ["作答记录和复习安排已保存。", "开始新一轮"]
NO_KIND_LABEL = "已加入的小节仍保留。请检查题型设置、语音或手写模型，以及内容是否已暂停。"
NO_KIND = [NO_KIND_LABEL, "题型与练习设置", "语音与手写模型", "学习路线", "回到今日"]
ROUTE_END_LABEL = "可以继续下一小节，或稍后按复习安排回来。"
WAIT_TEXTS = ["正在准备题目…", "回到今日"]
LATE = ["准备时间比预期长。", "已保存的进度不受影响。可以重试，或先回到今日。", "重试"]
ERROR_TEXT = "准备下一题时出错，学习记录与草稿已保留。"


# --- states -----------------------------------------------------------------------------------------------

@pytest.fixture
def app(path_app):
    """path_app with the orientation seen and "can 继续学习 reach anything" answering False."""
    orientation_seen(path_app)
    stub_can_continue(path_app, False)
    return path_app


def reviewed(app, symbol, days=12):
    """A kana card of ``symbol`` rated ``days`` days ago: due now, not queued."""
    app.store.record_review(kana_id(app, symbol), "kana_choice", "hiragana", 3,
                            now=app._test_clock[0] - timedelta(days=days))


def course(app, suffix, *, taught=True, due=True, kind="grammar_meaning"):
    """A synthetic course item and its question: taught a day ago (due now), taught now (a short wait), or
    untaught (its teaching first). Returns (content id, question id)."""
    now = app._test_clock[0]
    cid, qid = "grammar:s4-synthetic:" + suffix, "s4-question:" + suffix
    if not app.store.get_setting(PREFS, None):
        app.store.set_setting(PREFS, {"enabled": ["grammar_meaning"]})
    learning = app.learning_store
    learning.enroll(item(cid, title="合成" + suffix, abilities=["understanding"]), now=now - timedelta(days=2))
    if taught:
        ready(learning, cid, when=(now - timedelta(days=1)) if due else now)
    snapshot = question(cid, qid, kind=kind, family="grammar", feedback="合成结果", choices=["正在读书", "已经结束"])
    learning.append_questions(snapshot, now=now - timedelta(days=1) if due else now)
    return cid, qid


def arm_course(app, cid):
    """The learner chose to start this course item now (an explicit start, as 开始下一小节 arms one)."""
    header = app.store.queries.course_introduction_header(cid)
    entry = {"entry_id": "s4-entry", "content_id": cid, "kind": "learning", "script": None,
             "introduction_id": header["introduction_id"]}
    db = app.store._db
    with db:
        db.execute("BEGIN")
        assert explicit_learning_start.arm(db, [entry], now=app._test_clock[0] - timedelta(minutes=1))


def first_kana_section(app):
    """The core route's first section whose entries are all kana and still new."""
    return next(s for stage in app.study_path_service().overview()["stages"]
                for unit in stage["units"] for s in unit["sections"]
                if s["new_count"] and all(e["kind"] == "kana" for e in s["entries"]))


def no_usable_kind(app):
    """A kana section added, but the only kana kind needs the handwriting model, which is missing."""
    section = first_kana_section(app)
    app.study_path_service().add_sections([section["id"]])
    app._practice_preferences = KanaMemory._clean_preferences({
        "kind": "看罗马音手写假名", "mixed_kinds": ["romaji_handwriting"],
        "question_plan_version": 2, "listening_handwriting_opt_in_version": 1})
    app.model_state = "missing"
    return section


def due_unqueued_kana(app):
    """あ queued; い and う due and not queued yet: a kana queue assembly has work (W1 starts)."""
    reviewed(app, "い")
    reviewed(app, "う")
    sid, _ = seed_main(app)
    return sid


def sealed_course_round(app):
    """App-run rounds on, two due synthetic courses, one inline continue: the round is sealed (W2 can start)."""
    practice_round.enable(app.store)
    course(app, "w2a")
    course(app, "w2b")
    app.continue_study_path()
    assert practice_round.sealed(app.store) and app.page == "learning_question"
    app.page = "home"


def availability(**counts):
    value = QueueAvailability(**counts)
    return lambda *args, **kwargs: value


def errors(operation, stage):
    return sum(event["count"] for event in diagnostic_events.error_events()
               if (event["operation"], event["stage"]) == (operation, stage))


def lines(log):
    return [call for call in log if call.name in LINES]


def drawn(log):
    return [call.args for call in log if call.name == "screen"]


def production_slot(app, monkeypatch):
    """The fake screen sets only app.body; production keeps the page under content_slot (the notice needs it)."""
    original = app.screen

    def screen(*args, **kwargs):
        body = original(*args, **kwargs)
        app.content_slot = View(ScrollView(content=body))
        return body
    monkeypatch.setattr(app, "screen", screen)


# --- a. one scenario per outcome ------------------------------------------------------------------------

def test_d1_an_explicitly_started_course_head_is_passed_selected_with_the_actions_read(app, monkeypatch):
    """D1: a protected course head (explicit start over a due kana review) -> continue_learning(
    _selected_current=<the instruction head>, _projection=<this action's live read context>), nothing else."""
    reviewed(app, "あ")
    seed_main(app)
    cid, qid = course(app, "d1", taught=False)
    arm_course(app, cid)
    log = cs.watch(app, monkeypatch)
    app.continue_study_path()
    (call,) = lines(log)
    assert call.name == "continue_learning" and call.args == () and set(call.kwargs) == {"_selected_current",
                                                                                          "_projection"}
    head = call.kwargs["_selected_current"]
    assert (head["type"], head["question"]["id"], head["content"]["id"]) == ("instruction", qid, cid)
    assert call.projection == "action read" and call.projection_attached and call.projection_active
    assert call.read_ended                                   # the read context ends with the action
    assert "take_over" not in cs.names(log)


def test_d2_an_explicitly_started_kana_head_opens_its_first_teaching_without_a_prepared_token(app, monkeypatch):
    """D2: a protected kana head -> resume_practice(preserve_audio=True, _first_teaching=True,
    _first_teaching_targets={(content id, script)}); no _prepared, as the token is taken only for an unprotected
    head."""
    reviewed(app, "う")
    seed_main(app, symbol="う")
    section = first_kana_section(app)
    app.study_path_service().add_sections([section["id"]], start_learning=True)
    log = cs.watch(app, monkeypatch)
    app.continue_study_path()
    (call,) = lines(log)
    assert call.name == "resume_practice" and call.args == ()
    assert call.kwargs == {"preserve_audio": True, "_first_teaching": True,
                           "_first_teaching_targets": {(kana_id(app, "あ"), "hiragana")}}
    assert cs.page(app) == ("kana_introduction", "开始前") and "take_over" not in cs.names(log)


def test_d3_a_due_kana_review_resumes_the_kana_line_with_its_prepared_token(app, monkeypatch):
    """D3: lane kana -> resume_practice(preserve_audio=True, _prepared=<PreparedKana of the main session>)."""
    reviewed(app, "あ")
    sid, _ = seed_main(app)
    log = cs.watch(app, monkeypatch)
    app.continue_study_path()
    (call,) = lines(log)
    assert call.name == "resume_practice" and call.args == () and set(call.kwargs) == {"preserve_audio", "_prepared"}
    token = call.kwargs["_prepared"]
    assert call.kwargs["preserve_audio"] is True
    assert type(token).__name__ == "PreparedKana" and token.session_id == sid and token.kinds == ("kana_choice",)
    assert app.page == "question" and "take_over" not in cs.names(log)


def test_d4_a_due_course_question_continues_the_course_line_with_the_actions_read(app, monkeypatch):
    """D4: lane course -> continue_learning(_projection=<this action's live read context>) only."""
    course(app, "d4")
    log = cs.watch(app, monkeypatch)
    app.continue_study_path()
    (call,) = lines(log)
    assert (call.name, call.args, set(call.kwargs)) == ("continue_learning", (), {"_projection"})
    assert call.projection == "action read" and call.read_ended
    assert cs.page(app) == ("learning_question", "语法含义")


@pytest.mark.parametrize("takes_over", [True, False], ids=["D5-taken-over", "D6-page"])
def test_d5_d6_a_round_with_nothing_left_ends_then_asks_the_take_over_once(app, monkeypatch, takes_over):
    """D5/D6: app-run rounds on, nothing left: mark_finished, then take_over once; True draws nothing,
    False draws 本轮已保存 (the round stays finished either way)."""
    practice_round.enable(app.store)
    log = cs.watch(app, monkeypatch, take_over=takes_over)
    app.continue_study_path()
    assert cs.names(log) == (["take_over"] if takes_over else ["take_over", "screen"])
    assert log[0].result is takes_over
    if not takes_over:
        assert drawn(log) == [("learning_done", "本轮已保存")]
    sealed, finished, *_ = cs.round_plan(app)
    assert sealed and finished


def test_d7_a_reference_entry_opens_the_reference_page_by_position(app, monkeypatch):
    """D7: service.next_reference_entry() truthy -> show_study_reference(<its id>), the id passed positionally,
    no take_over. Reached with a patched entry (the demo content has no reference-only symbols)."""
    monkeypatch.setattr(app.study_path_service(), "next_reference_entry", lambda: {"id": "ref:synthetic"})
    log = cs.watch(app, monkeypatch, real=False)
    app.continue_study_path()
    assert [(call.name, call.args, call.kwargs) for call in log] == [("show_study_reference", ("ref:synthetic",), {})]


@pytest.mark.parametrize("takes_over", [True, False])
def test_d8_a_waiting_kana_without_a_course_head_asks_the_take_over_then_resumes_without_preserve_audio(
        app, monkeypatch, takes_over):
    """D8: a kana wait and no course head: take_over once; refused -> resume_practice(_prepared=…) without
    preserve_audio (unlike D2/D3, which pass it); taken over -> nothing else."""
    sid, _ = seed_main(app, waiting=True)
    log = cs.watch(app, monkeypatch, take_over=takes_over)
    app.continue_study_path()
    if takes_over:
        assert cs.names(log) == ["take_over"]
        return
    assert cs.names(log)[:2] == ["take_over", "resume_practice"]
    call = log[1]
    assert call.args == () and set(call.kwargs) == {"_prepared"} and call.kwargs["_prepared"].session_id == sid
    assert cs.page(app) == ("session_wait", "本轮先歇一会儿")


@pytest.mark.parametrize("takes_over", [True, False])
def test_d9_a_waiting_course_head_asks_the_take_over_then_continues_the_course_line(app, monkeypatch, takes_over):
    """D9: a course head of type "waiting" (taught just now): take_over; refused -> continue_learning(
    _projection=<this action's read>)."""
    course(app, "d9", due=False)
    log = cs.watch(app, monkeypatch, take_over=takes_over)
    app.continue_study_path()
    if takes_over:
        assert cs.names(log) == ["take_over"]
        return
    assert cs.names(log)[:2] == ["take_over", "continue_learning"]
    assert set(log[1].kwargs) == {"_projection"} and log[1].projection == "action read"
    assert cs.page(app) == ("learning_wait", "稍后继续")


def test_d9_a_disabled_course_head_continues_the_course_line_without_asking_the_take_over(app, monkeypatch):
    """D9 with a current that is not "waiting" (the only course question's kind switched off): no take_over."""
    course(app, "d9off")
    app.store.set_setting(PREFS, {"enabled": ["grammar_cloze"]})
    log = cs.watch(app, monkeypatch, take_over=True)
    app.continue_study_path()
    assert cs.names(log)[:1] == ["continue_learning"] and "take_over" not in cs.names(log)
    assert app.page == "learning_wait"


@pytest.mark.parametrize("takes_over", [True, False])
def test_d10_waiting_kana_without_a_due_time_is_reached_only_with_a_patched_summary(app, monkeypatch, takes_over):
    """D10: waiting_count without next_due_utc cannot come from the real summary (it always gives waiting kana a
    due time, so D8 or D9 comes first; the branch looks dead); patched: take_over, then resume_practice() with
    no arguments (no pending queue: no token)."""
    cs.patch_dispatch_availability(monkeypatch, availability(waiting_count=1))
    log = cs.watch(app, monkeypatch, real=False, take_over=takes_over)
    app.continue_study_path()
    expected = ["take_over"] if takes_over else ["take_over", "resume_practice"]
    assert cs.names(log) == expected
    if not takes_over:
        assert (log[1].args, log[1].kwargs) == ((), {})


def test_d11_gated_kana_alone_resume_the_kana_line_and_never_ask_the_take_over(app, monkeypatch):
    """D11 (patched summary, as the home characterisation does): blocked_count only -> resume_practice()."""
    cs.patch_dispatch_availability(monkeypatch, availability(blocked_count=1))
    log = cs.watch(app, monkeypatch, real=False, take_over=True)
    app.continue_study_path()
    assert [(call.name, call.args, call.kwargs) for call in log] == [("resume_practice", (), {})]


def test_d12_an_added_kana_section_without_a_usable_kind_draws_its_page_and_never_asks_the_take_over(
        app, monkeypatch):
    """D12: a kana entry added in the core route but no usable kana kind: 暂时没有可进行的假名题型,
    no take_over, nothing queued, the section still added."""
    section = no_usable_kind(app)
    log = cs.watch(app, monkeypatch, take_over=True)
    app.continue_study_path()
    assert cs.names(log) == ["screen"]
    assert drawn(log) == [("learning_wait", "暂时没有可进行的假名题型")]
    assert app.store.load_session_progress() is None
    assert app.study_path_service().section(section["id"])["state"] == "added"


def test_d13_d14_with_nothing_at_all_the_take_over_is_asked_once_then_the_route_end_page(app, monkeypatch):
    """D13: nothing to do and no rounds: take_over True draws nothing; D14: refused -> 本次学习已保存."""
    log = cs.watch(app, monkeypatch, take_over=True)
    app.continue_study_path()
    assert cs.names(log) == ["take_over"] and app.page == "home"
    log = cs.watch(app, monkeypatch, take_over=False)
    app.continue_study_path()
    assert cs.names(log) == ["take_over", "screen"]
    assert drawn(log) == [("learning_done", "本次学习已保存")]


def test_p2_a_failed_checkpoint_of_a_button_press_draws_nothing(app, monkeypatch):
    """P2: a press (no _checkpoint_saved) whose checkpoint fails returns: nothing drawn or called."""
    reviewed(app, "あ")
    seed_main(app)
    app._checkpoint = Mock(return_value=False)
    log = cs.watch(app, monkeypatch, take_over=True)
    app.continue_study_path()
    assert log == [] and app.page == "home"
    app._checkpoint.assert_called_once_with()


def test_p3_a_lane_switch_that_cannot_save_draws_nothing(app, monkeypatch):
    """P3: coming from the word lane, the lane switch's checkpoint fails: the lane stays, nothing drawn."""
    seed_main(app)
    app.store.practice_lane = "word_romaji"
    app._checkpoint = Mock(return_value=False)
    log = cs.watch(app, monkeypatch, take_over=True)
    app.continue_study_path(_checkpoint_saved=True)
    assert log == [] and app.page == "home" and app.store.practice_lane == "word_romaji"


def test_p5_a_failing_kind_reconcile_alerts_and_leaves_the_page(app, monkeypatch):
    """P5: the kana session's kinds cannot be reconciled: one alert, nothing drawn, no line entry."""
    seed_main(app)

    def refused(*args, **kwargs):
        raise ValueError("合成原因")
    monkeypatch.setattr(app.store, "reconcile_session_kinds", refused)
    log = cs.watch(app, monkeypatch, take_over=True)
    app.continue_study_path()
    assert log == [] and app.page == "home"
    app.alert.assert_called_once_with("题型设置暂未应用，原练习已保留：合成原因")


def test_p8_a_failing_decision_draws_the_error_page_records_it_and_raises(app, monkeypatch):
    """P8: the decision raises -> record_error(kana_queue_assembly, read), the error page, the mark
    "already shown" on the exception, then the exception again."""
    seed_main(app)
    error = ValueError("合成故障")
    calls = cs.make_dispatch_raise(monkeypatch, error)
    before = errors("kana_queue_assembly", "read")
    with pytest.raises(ValueError) as raised:
        app.continue_study_path()
    assert raised.value is error and cs.error_shown(error) and calls == [app]
    assert errors("kana_queue_assembly", "read") == before + 1
    assert cs.page(app) == ("learning_wait", "无法准备下一题")


# --- b. the pages ------------------------------------------------------------------------------------------

def spy_targets(app, monkeypatch, *names, until_drawn=("continue_study_path",)):
    """Record the end pages' targets; continue_study_path runs for real until the page is drawn."""
    state = {"drawn": False}
    log = cs.record_entries(app, monkeypatch, real=lambda: not state["drawn"], names=until_drawn)
    cs.record_entries(app, monkeypatch, real=False, log=log, names=names)
    return log, state


def extra_available(monkeypatch, value=True):
    """继续加练 is offered (ExtraSession.available answers ``value``)."""
    monkeypatch.setattr(ExtraSession, "available", lambda self, at: value)


def own(made, *texts):
    """The (kind, text, kwargs) of these texts, in drawing order."""
    return [entry for entry in made if entry[1] in texts]


def builder_texts(views):
    from support.fake_ui import descendants
    return [str(view.text) for root in views for view in descendants(root) if view.text]


def press_and_take(app, log, caption):
    """Press the control and return the calls it made."""
    start = len(log)
    cs.press(app, caption)
    return [(call.name, call.args, call.kwargs) for call in log[start:]]


def test_round_end_page_texts_order_styles_and_handlers(app, monkeypatch):
    """Page 本轮已保存 (D6): label 17, 开始新一轮 primary -> continue_study_path(widget) (a press:
    restart_finished), the next-content rows in place, 继续加练 before 回到今日 -> leave_learning(widget).
    The handlers still act after the page was left (no guard; odd, but it is what the page does)."""
    cs.record_controls(monkeypatch)
    practice_round.enable(app.store)
    extra_available(monkeypatch)
    log, state = spy_targets(app, monkeypatch, "leave_learning")
    app.continue_study_path()
    state["drawn"] = True
    assert cs.page(app) == ("learning_done", "本轮已保存")
    rows = builder_texts(app.learning_next_content_controls())
    assert rows and cs.texts(app) == ROUND_END + rows + [display.EXTRA_BUTTON, "回到今日"]
    made = cs.page_made_with(app)
    assert own(made, *ROUND_END, "回到今日") == [
        ("label", "作答记录和复习安排已保存。", {"size": 17}), ("button", "开始新一轮", {"primary": True}),
        ("button", "回到今日", {})]
    kept = cs.control(app, "开始新一轮"), cs.control(app, "回到今日")
    assert press_and_take(app, log, "开始新一轮") == [("continue_study_path", (kept[0],), {})]
    assert press_and_take(app, log, "回到今日") == [("leave_learning", (kept[1],), {})]
    app.page = "home"                                   # the page was left; the kept widgets still act
    kept[0].on_press(kept[0])
    assert log[-1].name == "continue_study_path" and log[-1].args == (kept[0],)


def test_no_usable_kind_page_texts_order_styles_and_handlers(app, monkeypatch):
    """Page 暂时没有可进行的假名题型 (D12): label 17 and four buttons, 题型与练习设置 primary; each presses its
    target with the widget; no 继续加练 even when it is available; a kept button still acts after the page was
    left (no guard)."""
    cs.record_controls(monkeypatch)
    no_usable_kind(app)
    extra_available(monkeypatch)
    log, state = spy_targets(app, monkeypatch, "show_practice_hub", "show_settings", "show_study_path",
                             "leave_learning")
    app.continue_study_path()
    state["drawn"] = True
    assert cs.texts(app) == NO_KIND and cs.captions(app) == NO_KIND[1:]
    assert cs.page_made_with(app) == [
        ("label", NO_KIND_LABEL, {"size": 17}), ("button", "题型与练习设置", {"primary": True}),
        ("button", "语音与手写模型", {}), ("button", "学习路线", {}), ("button", "回到今日", {})]
    for caption, target in zip(NO_KIND[1:], ("show_practice_hub", "show_settings", "show_study_path",
                                             "leave_learning")):
        widget = cs.control(app, caption)
        assert press_and_take(app, log, caption) == [(target, (widget,), {})]
    widget = cs.control(app, "学习路线")
    app.page = "home"
    widget.on_press(widget)
    assert (log[-1].name, log[-1].args) == ("show_study_path", (widget,))


def test_route_end_page_texts_order_styles_and_handlers(app, monkeypatch):
    """Page 本次学习已保存 without a note (D14): label 17, the core route's next controls in place
    (_path_next_controls(overview)), 继续加练, 题型与练习设置 -> show_practice_hub, 回到今日 -> leave_learning;
    both plain buttons; a kept button still acts after the page was left (no guard)."""
    cs.record_controls(monkeypatch)
    extra_available(monkeypatch)
    log, state = spy_targets(app, monkeypatch, "show_practice_hub", "leave_learning")
    app.continue_study_path()
    state["drawn"] = True
    assert cs.page(app) == ("learning_done", "本次学习已保存")
    rows = builder_texts(app._path_next_controls(app.study_path_service().overview()))
    assert rows and cs.texts(app) == [ROUTE_END_LABEL] + rows + [display.EXTRA_BUTTON, "题型与练习设置", "回到今日"]
    made = cs.page_made_with(app)
    assert own(made, ROUTE_END_LABEL, "题型与练习设置", "回到今日") == [
        ("label", ROUTE_END_LABEL, {"size": 17}), ("button", "题型与练习设置", {}), ("button", "回到今日", {})]
    for caption, target in (("题型与练习设置", "show_practice_hub"), ("回到今日", "leave_learning")):
        widget = cs.control(app, caption)
        assert press_and_take(app, log, caption) == [(target, (widget,), {})]
    app.page = "home"
    widget = cs.control(app, "题型与练习设置")
    widget.on_press(widget)
    assert log[-1].name == "show_practice_hub"


def test_route_end_page_under_breadth_starts_with_the_note_whose_button_passes_a_list(app, monkeypatch):
    """Page 本次学习已保存 under breadth (D14, B10): the note rows first; its button calls
    _add_study_sections([section id]) with a LIST and no route; the builder's 学习下一小节 a TUPLE (odd: two
    container forms for the same add)."""
    ArrangementPreferences(app.store).set_orientation(Orientation.BREADTH)
    log, state = spy_targets(app, monkeypatch, "_add_study_sections")
    app.continue_study_path()
    state["drawn"] = True
    nxt = app.study_path_service().overview()["next_section"]
    texts = cs.texts(app)
    assert texts[:3] == [display.NO_NEW_TEXT, f"{display.ADD_NEXT} · {nxt['title']}", ROUTE_END_LABEL]
    assert press_and_take(app, log, texts[1]) == [("_add_study_sections", ([nxt["id"]],), {})]
    assert press_and_take(app, log, "学习下一小节") == [("_add_study_sections", ((nxt["id"],),), {})]


@pytest.mark.parametrize("rounds", [True, False], ids=["本轮已保存", "本次学习已保存"])
@pytest.mark.parametrize("takes_over", [False, True])
def test_end_pages_ask_can_continue_once_after_the_take_over_and_never_when_it_takes_over(
        app, monkeypatch, rounds, takes_over):
    """D5/D6 and D13/D14: 本轮已保存 and 本次学习已保存 ask study_path_can_continue once per drawing, after take_over
    refused; not at all when take_over takes over."""
    if rounds:
        practice_round.enable(app.store)
    log = cs.spy_take_over(monkeypatch, takes_over)
    cs.record_entries(app, monkeypatch, real=True, log=log, names=("study_path_can_continue",))
    app.continue_study_path()
    assert cs.names(log) == (["take_over"] if takes_over else ["take_over", "study_path_can_continue"])
    assert app.page == ("home" if takes_over else "learning_done")


def test_error_page_texts_order_styles_handlers_and_reason(app, monkeypatch):
    """Page 无法准备下一题 (P8): text 16, 原因 13 MUTED with the first line cut at 80, 重试
    primary -> continue_study_path(widget), 回到今日 -> leave_learning; no 反馈 without begin_page_feedback."""
    cs.record_controls(monkeypatch)
    log, state = spy_targets(app, monkeypatch, "leave_learning")
    cs.make_dispatch_raise(monkeypatch, ValueError("甲" * 90 + "\n第二行"))
    with pytest.raises(ValueError):
        app.continue_study_path()
    state["drawn"] = True
    reason = "原因：" + "甲" * 80
    assert cs.texts(app) == [ERROR_TEXT, reason, "重试", "回到今日"]
    assert cs.page_made_with(app) == [
        ("label", ERROR_TEXT, {"size": 16}), ("label", reason, {"size": 13, "color": u.MUTED}),
        ("button", "重试", {"primary": True}), ("button", "回到今日", {})]
    retry, home = cs.control(app, "重试"), cs.control(app, "回到今日")
    assert press_and_take(app, log, "回到今日") == [("leave_learning", (home,), {})]
    assert press_and_take(app, log, "重试") == [("continue_study_path", (retry,), {})]


def test_error_page_reason_is_the_type_name_for_an_empty_message_and_offers_feedback_when_callable(
        app, monkeypatch):
    """P8: an empty message gives "原因：<type name>"; a callable begin_page_feedback adds 反馈 last."""
    app.begin_page_feedback = Mock()
    cs.make_dispatch_raise(monkeypatch, KeyError())
    with pytest.raises(KeyError):
        app.continue_study_path()
    assert cs.texts(app) == [ERROR_TEXT, "原因：KeyError", "重试", "回到今日", "反馈"]
    cs.press(app, "反馈")
    app.begin_page_feedback.assert_called_once()


def test_error_page_names_the_course_item_the_action_reached(app, monkeypatch):
    """P8: a course line entry that set _learning_item and then failed: the error page's first line is its
    title (size 19, bold); the error is raised again."""
    cs.record_controls(monkeypatch)
    course(app, "title")

    def failing(*args, **kwargs):
        app._learning_item = {"content": {"title": "合成课程标题"}}
        raise RuntimeError("合成")
    app.continue_learning = failing
    with pytest.raises(RuntimeError):
        app.continue_study_path()
    assert cs.texts(app)[:2] == ["合成课程标题", ERROR_TEXT]
    assert cs.page_made_with(app)[0] == ("label", "合成课程标题", {"size": 19, "bold": True})


def test_wait_page_texts_styles_handler_timer_and_late_rows(app, monkeypatch):
    """Page kana_queue_assembly (W1): 继续学习, 正在准备题目… (17, MUTED), an empty column,
    回到今日 -> leave_learning; a 20 s timer; when it fires: 准备时间比预期长。(16), the hint (13, MUTED),
    重试 (primary) -> continue_study_path; once only; the timer cancelled when the result comes back."""
    cs.record_controls(monkeypatch)
    due_unqueued_kana(app)
    cs.background_on(app)
    clock = cs.controlled_wait_clock(monkeypatch)
    log, state = spy_targets(app, monkeypatch, "leave_learning")
    seen = {}

    def while_waiting():
        seen["page"] = cs.page(app)
        seen["texts"] = cs.texts(app)
        seen["made"] = cs.page_made_with(app)
        (timer,) = cs.wait_timers(clock)
        seen["timer"] = timer
        assert cs.wait_timer(app) is timer and timer.delay == cs.wait_seconds() == 20
        cs.fire_wait_timers(clock)
        cs.fire_wait_timers(clock)                     # a second run adds nothing
        seen["late"] = cs.texts(app)
        seen["late_made"] = cs.page_made_with(app)
        state["drawn"] = True
        retry = cs.control(app, "重试")
        retry.on_press(retry)
        seen["retry"] = (log[-1].name, log[-1].args == (retry,))
        state["drawn"] = False
    cs.in_loop(app, app.continue_study_path, while_waiting=while_waiting)
    assert seen["page"] == (cs.WAIT_PAGE, "继续学习") and seen["texts"] == WAIT_TEXTS
    assert seen["made"] == [("label", "正在准备题目…", {"size": 17, "color": u.MUTED}), ("col", "", {}),
                            ("button", "回到今日", {})]
    assert seen["late"] == WAIT_TEXTS[:1] + LATE + WAIT_TEXTS[1:]
    assert seen["late_made"][2:5] == [("label", LATE[0], {"size": 16}),
                                      ("label", LATE[1], {"size": 13, "color": u.MUTED}),
                                      ("button", "重试", {"primary": True})]
    assert seen["retry"] == ("continue_study_path", True)
    assert seen["timer"].cancelled and cs.wait_timer(app) is None


def test_a_new_wait_page_cancels_the_timer_an_earlier_one_left_armed(app):
    """W1/W2: showing the waiting page again cancels the timer still armed by the earlier one (W2's fallback
    after drawing leaves one armed); the page and token of the new one are recorded."""
    clock = cs.ControlledLoop()
    assert cs.show_wait_page(app, clock) and cs.show_wait_page(app, clock)
    first, second = cs.wait_timers(clock)
    assert first.cancelled and not second.cancelled and cs.wait_timer(app) is second
    assert cs.wait_view(app) == (cs.WAIT_PAGE, app._page_token)


def test_wait_page_home_button_leaves_and_drops_the_result(app, monkeypatch):
    """W1: pressing the wait page's own 回到今日 (leave_learning) drops the worker result."""
    sid = due_unqueued_kana(app)
    cs.background_on(app)
    before = len(app.store.load_session_progress(lane="main", lazy_questions=True)["questions"])
    cs.in_loop(app, app.continue_study_path, while_waiting=lambda: cs.press(app, "回到今日"))
    after = app.store.load_session_progress(lane="main", lazy_questions=True)
    assert app.page == "home" and after["session_id"] == sid and len(after["questions"]) == before


# --- c. order and precedence ------------------------------------------------------------------------------

def test_a_reference_entry_comes_before_a_waiting_kana(app, monkeypatch):
    """D7 before D8: a reference entry and a kana wait: the reference page, no take_over."""
    seed_main(app, waiting=True)
    monkeypatch.setattr(app.study_path_service(), "next_reference_entry", lambda: {"id": "ref:first"})
    log = cs.watch(app, monkeypatch, real=False, take_over=True)
    app.continue_study_path()
    assert [(call.name, call.args) for call in log] == [("show_study_reference", ("ref:first",))]


def test_a_waiting_kana_comes_before_a_course_head_without_a_due_time(app, monkeypatch):
    """D8 before D9: a kana wait and a disabled course head (no due time): the kana line (D8), not the course
    line."""
    seed_main(app, waiting=True)
    course(app, "gap12")
    app.store.set_setting(PREFS, {"enabled": ["grammar_cloze"]})
    log = cs.watch(app, monkeypatch, real=False, take_over=False)
    app.continue_study_path()
    assert cs.names(log) == ["take_over", "resume_practice"] and set(log[1].kwargs) == {"_prepared"}


def test_gated_kana_do_not_keep_a_round_open(app, monkeypatch):
    """D6 before D11: rounds on, only gated kana (patched summary): the round ends (本轮已保存), not D11, as the
    round-end check counts only ready and waiting kana."""
    practice_round.enable(app.store)
    cs.patch_dispatch_availability(monkeypatch, availability(blocked_count=2))
    log = cs.watch(app, monkeypatch, real=False, take_over=False)
    app.continue_study_path()
    assert cs.names(log) == ["take_over", "screen"] and drawn(log) == [("learning_done", "本轮已保存")]


# --- d. the waits ------------------------------------------------------------------------------------------

def counted_kana_runner(monkeypatch, before=None, error=None):
    runs, real = [], cs.kana_runner()

    async def runner(*args, **kwargs):
        runs.append(args[2])
        if before is not None:
            before()
        if error is not None:
            raise error
        return await real(*args, **kwargs)
    cs.replace_kana_runner(monkeypatch, runner)
    return runs


def test_w1_writes_nothing_while_shown_and_the_ticket_holds_the_revision_after_drawing(app, monkeypatch):
    """W1: the store revision seen right after the wait page is drawn is the one the worker starts
    with; the result then appears (question)."""
    due_unqueued_kana(app)
    cs.background_on(app)
    revisions = []
    counted_kana_runner(monkeypatch, before=lambda: revisions.append(tuple(app.store.read_revision())))
    seen, tasks = cs.in_loop(app, app.continue_study_path,
                             while_waiting=lambda: revisions.append(tuple(app.store.read_revision())))
    assert seen[0] == cs.WAIT_PAGE and len(tasks) == 1 and tasks[0] is cs.kana_wait_task(app)
    assert revisions[0] == revisions[1] and app.page == "question"
    assert cs.course_wait_task(app) is None


def test_w1_a_token_change_on_the_same_page_name_drops_the_result(app, monkeypatch):
    """W1: the same page name with a new page token (redrawn) drops the worker result silently."""
    sid = due_unqueued_kana(app)
    cs.background_on(app)
    before = len(app.store.load_session_progress(lane="main", lazy_questions=True)["questions"])

    def redraw():
        app._page_token += 1
    log = cs.watch(app, monkeypatch, real=True, take_over=False)
    cs.in_loop(app, app.continue_study_path, while_waiting=redraw)
    after = app.store.load_session_progress(lane="main", lazy_questions=True)
    assert app.page == cs.WAIT_PAGE and len(after["questions"]) == before and after["session_id"] == sid
    assert drawn(log) == [(cs.WAIT_PAGE, "继续学习")] and lines(log) == []


def test_w1_after_the_attempts_limit_the_queue_is_assembled_inline(app, monkeypatch):
    """W1: data changing during every worker run: KANA_ASSEMBLY_ATTEMPTS (2) runs, then inline."""
    due_unqueued_kana(app)
    cs.background_on(app)
    counter = [0]

    def write():
        counter[0] += 1
        app.store.set_setting("s4_probe", counter[0])
    runs = counted_kana_runner(monkeypatch, before=write)
    cs.in_loop(app, app.continue_study_path)
    assert len(runs) == cs.KANA_ASSEMBLY_ATTEMPTS == 2 and app.page == "question"


def test_w1_a_worker_error_is_recorded_then_the_action_runs_inline_once(app, monkeypatch):
    """W1: a worker error -> record_error(kana_queue_assembly, read), one inline continue, no second run."""
    due_unqueued_kana(app)
    cs.background_on(app)
    runs = counted_kana_runner(monkeypatch, error=RuntimeError("合成"))
    before = errors("kana_queue_assembly", "read")
    cs.in_loop(app, app.continue_study_path)
    assert len(runs) == 1 and errors("kana_queue_assembly", "read") == before + 1 and app.page == "question"


def test_w1_does_not_start_without_the_switch(app, monkeypatch):
    """W1 preconditions: without the background switch, under a running loop: inline."""
    due_unqueued_kana(app)
    cs.background_on(app)
    cs.background_off(app)
    runs = counted_kana_runner(monkeypatch)
    seen, tasks = cs.in_loop(app, app.continue_study_path)
    assert runs == [] and tasks == [] and seen[0] == "question" and cs.kana_wait_task(app) is None


def test_w1_a_failure_after_the_wait_draws_the_error_page_once(app, monkeypatch):
    """W1: the resumed continue fails (continue_failed): one error page, the task ends without error."""
    due_unqueued_kana(app)
    cs.background_on(app)
    cs.make_dispatch_raise(monkeypatch, RuntimeError("合成"))
    log = cs.record_screens(app, monkeypatch)
    cs.in_loop(app, app.continue_study_path)
    assert drawn(log) == [(cs.WAIT_PAGE, "继续学习"), ("learning_wait", "无法准备下一题")]
    assert cs.kana_wait_task(app).exception() is None


def test_w1_reconcile_failing_on_the_way_back_leaves_the_wait_page_without_retry(app, monkeypatch):
    """W1, then P5 on the wait page: the resumed continue's reconcile fails: the alert only, the wait page
    stays, its timer was already cancelled on the way back, so 重试 never appears (odd: a wait page that waits
    for nothing)."""
    due_unqueued_kana(app)
    cs.background_on(app)
    clock = cs.controlled_wait_clock(monkeypatch)

    def refuse():
        def refused(*args, **kwargs):
            raise ValueError("合成")
        monkeypatch.setattr(app.store, "reconcile_session_kinds", refused)
    cs.in_loop(app, app.continue_study_path, while_waiting=refuse)
    assert app.page == cs.WAIT_PAGE and cs.texts(app) == WAIT_TEXTS
    app.alert.assert_called_once_with("题型设置暂未应用，原练习已保留：合成")
    cs.fire_wait_timers(clock)
    assert cs.texts(app) == WAIT_TEXTS and all(handle.cancelled for handle in cs.wait_timers(clock))


def test_w1_overdue_adds_nothing_after_the_page_or_its_token_changed(app, monkeypatch):
    """W1: the late rows appear only on the same page name AND token."""
    cs.background_on(app)
    due_unqueued_kana(app)
    clock = cs.controlled_wait_clock(monkeypatch)

    def change_token():
        app._page_token += 1
        cs.fire_wait_timers(clock)
    cs.in_loop(app, app.continue_study_path, while_waiting=change_token)
    assert "重试" not in cs.captions(app)


def test_the_enrollment_notice_waits_over_a_real_wait_and_lands_on_the_review_page(app, monkeypatch):
    """W1 and the enrollment notice: a notice armed before the action is kept over the wait page (anchored on its
    page and token) and shown once on the review page reached after the wait; then it is gone."""
    production_slot(app, monkeypatch)
    due_unqueued_kana(app)
    cs.background_on(app)
    cs.set_enrollment_notice(app, {"text": "合成通知", "new_ids": frozenset({"grammar:s4-none"}), "anchor": None})
    seen = {}
    cs.in_loop(app, app.continue_study_path,
               while_waiting=lambda: seen.update(notice=cs.enrollment_notice(app), view=cs.wait_view(app),
                                                 page=(app.page, app._page_token)))
    assert seen["view"] == seen["page"] and seen["notice"]["anchor"] == seen["view"]
    assert app.page == "question" and app.body.children[0].text == "合成通知"
    assert cs.texts(app).count("合成通知") == 1 and cs.enrollment_notice(app) is None


def test_an_add_drops_an_older_armed_notice_before_it_continues(app, monkeypatch):
    """The enrollment notice as study_path_ui arms it: _add_study_sections clears an armed notice first, so an add
    that adds nothing continues without the older notice; the review page reached shows none."""
    production_slot(app, monkeypatch)
    reviewed(app, "あ")
    seed_main(app, draft={"romaji_answer": "a"})
    cs.set_enrollment_notice(app, {"text": "旧的通知", "new_ids": frozenset(), "anchor": None})
    app._add_study_sections([])
    assert app.page == "question" and "旧的通知" not in cs.texts(app) and cs.enrollment_notice(app) is None


def course_calls(monkeypatch):
    calls = []
    import threading
    original = LearningStore._current

    def counted(self, *args, **kwargs):
        calls.append(threading.current_thread() is threading.main_thread())
        return original(self, *args, **kwargs)
    monkeypatch.setattr(LearningStore, "_current", counted)
    return calls


def test_w2_the_worker_head_is_used_once_and_the_line_gets_the_actions_read(app, monkeypatch):
    """W2 (P1, P6): a sealed round: the wait page, the course head computed once on the worker, the
    resumed decision continues the course line with its own live read context; timer cancelled."""
    sealed_course_round(app)
    cs.background_on(app)
    clock = cs.controlled_wait_clock(monkeypatch)
    calls = course_calls(monkeypatch)
    log = cs.watch(app, monkeypatch, real=True, take_over=False)
    cs.record_entries(app, monkeypatch, real=True, log=log, names=("_queue_missing_learning_questions",))
    seen, tasks = cs.in_loop(app, lambda: app.continue_study_path(_checkpoint_saved=True))
    assert seen[0] == cs.WAIT_PAGE and tasks == [cs.course_wait_task(app)] and cs.kana_wait_task(app) is None
    assert cs.names(log).count("_queue_missing_learning_questions") == 1      # the resume skips P2-P7
    assert calls[0] is False and True not in calls
    (line,) = [call for call in lines(log) if call.name == "continue_learning"][:1]
    assert set(line.kwargs) == {"_projection"} and line.projection == "action read"
    assert app.page == "learning_question" and all(h.cancelled for h in cs.wait_timers(clock))


@pytest.mark.parametrize("change", ["left", "error", "kinds", "kinds-unwritten", "seed"])
def test_w2_guards_on_the_way_back(app, monkeypatch, change):
    """W2: page left -> nothing more drawn or called; worker error -> recorded, inline; enabled
    kinds changed while waiting (stored, or only as the app reports them, the revision unchanged) -> inline;
    the seed refused -> inline (a main-thread current)."""
    sealed_course_round(app)
    cs.background_on(app)
    calls = course_calls(monkeypatch)
    before = errors("kana_queue_assembly", "read")
    if change == "error":
        async def broken(*args, **kwargs):
            raise RuntimeError("合成")
        cs.replace_course_runner(monkeypatch, broken)
    if change == "seed":
        from kanamemory import learning_store
        monkeypatch.setattr(learning_store._LearningRead, "seed", lambda self, *args: False)
    log = cs.watch(app, monkeypatch, real=True, take_over=False)
    # The whole preparation (P2-P7) runs again only on the inline fallback, not on the resumed decision.
    cs.record_entries(app, monkeypatch, real=True, log=log, names=("_queue_missing_learning_questions",))

    def meanwhile():
        if change == "left":
            app.leave_learning()
        elif change == "kinds":
            app.store.set_setting(PREFS, {"enabled": ["grammar_meaning", "grammar_cloze"]})
        elif change == "kinds-unwritten":
            reported = app.learning_preferences
            app.learning_preferences = lambda: {**reported(), "enabled": ["grammar_meaning", "grammar_cloze"]}
    cs.in_loop(app, lambda: app.continue_study_path(_checkpoint_saved=True), while_waiting=meanwhile)
    if change == "left":
        assert app.page == "home" and lines(log) == []
        return
    assert app.page == "learning_question" and True in calls
    assert cs.names(log).count("_queue_missing_learning_questions") == 2      # once before the wait, once inline
    if change == "error":
        assert errors("kana_queue_assembly", "read") == before + 1


def test_w2_does_not_start_with_skipped_teaching_or_outside_rounds(app, monkeypatch):
    """W2 preconditions: teaching passed over this run, or no app-run round: inline, no task."""
    sealed_course_round(app)
    cs.background_on(app)
    cs.set_skipped_teaching(app, {"grammar:s4-none"})
    seen, tasks = cs.in_loop(app, lambda: app.continue_study_path(_checkpoint_saved=True))
    assert tasks == [] and seen[0] == "learning_question" and cs.course_wait_task(app) is None


def test_w2_falls_back_inline_when_the_wait_page_itself_wrote(app, monkeypatch):
    """W2: drawing the wait page moved the revision: the inline result is drawn on top, the wait timer stays
    armed (odd but harmless), and firing it adds nothing, as the late rows check the page and its token."""
    sealed_course_round(app)
    cs.background_on(app)
    clock = cs.controlled_wait_clock(monkeypatch)
    original = app.screen

    def writing_screen(name, *args, **kwargs):
        body = original(name, *args, **kwargs)
        if name == cs.WAIT_PAGE:
            app.store.set_setting("s4_wait_write", 1)
        return body
    monkeypatch.setattr(app, "screen", writing_screen)
    seen, tasks = cs.in_loop(app, lambda: app.continue_study_path(_checkpoint_saved=True))
    assert tasks == [] and seen[0] == "learning_question" and cs.course_wait_task(app) is None
    (timer,) = cs.wait_timers(clock)
    assert not timer.cancelled
    texts = cs.texts(app)
    cs.fire_wait_timers(clock)
    assert cs.texts(app) == texts


def test_w2_a_failure_on_the_way_back_draws_the_error_page(app, monkeypatch):
    """W2 continue_failed: the resumed decision fails: the error page, the task ends cleanly."""
    sealed_course_round(app)
    cs.background_on(app)
    cs.make_dispatch_raise(monkeypatch, RuntimeError("合成"))
    log = cs.record_screens(app, monkeypatch)
    cs.in_loop(app, lambda: app.continue_study_path(_checkpoint_saved=True))
    assert drawn(log) == [(cs.WAIT_PAGE, "继续学习"), ("learning_wait", "无法准备下一题")]
    assert cs.course_wait_task(app).exception() is None


# --- e. round restart and protected heads --------------------------------------------------------------------

def test_a_press_restarts_a_finished_round_and_a_resume_does_not(app, monkeypatch):
    """Round restart: begin(restart_finished=not _checkpoint_saved): with _checkpoint_saved the finished round (its
    run) stays; a press (no flag) starts a new run."""
    practice_round.enable(app.store)
    app.continue_study_path()
    first = cs.round_plan(app)
    assert first[1]
    app.continue_study_path(_checkpoint_saved=True)
    assert cs.round_plan(app) == first
    app.continue_study_path()
    again = cs.round_plan(app)
    assert again[1] and again[3] != first[3]


def test_a_skipped_explicit_course_head_is_not_protected_under_thorough(app, monkeypatch):
    """No D1 under thorough: the explicitly started course item's teaching passed over this run: no
    _selected_current; the kana line is resumed instead."""
    reviewed(app, "あ")
    seed_main(app)
    cid, _ = course(app, "gap5", taught=False)
    arm_course(app, cid)
    cs.set_skipped_teaching(app, {cid})
    log = cs.watch(app, monkeypatch, real=False, take_over=False)
    app.continue_study_path()
    assert all("_selected_current" not in call.kwargs for call in lines(log))
    assert lines(log)[0].name == "resume_practice"


@pytest.mark.parametrize("lane", ["course", "kana"])
def test_a_protected_head_is_promoted_in_a_collecting_round(app, monkeypatch, lane):
    """D1/D2 in a collecting round: rounds on, a protected head: promote_initial before seal; the round is sealed
    afterwards, the course head first in the frozen course order; promote_initial leaves practice_round.promoted
    None."""
    practice_round.enable(app.store)
    if lane == "course":
        reviewed(app, "あ")
        seed_main(app)
        cid, qid = course(app, "gap6", taught=False)
        arm_course(app, cid)
    else:
        reviewed(app, "う")
        seed_main(app, symbol="う")
        app.study_path_service().add_sections([first_kana_section(app)["id"]], start_learning=True)
    cs.record_entries(app, monkeypatch, real=False)
    app.continue_study_path()
    sealed, finished, promoted, token = cs.round_plan(app)
    assert sealed and not finished and promoted is None
    if lane == "course":
        assert token[3][:1] == (qid,)


# --- f. failures mid-way --------------------------------------------------------------------------------------

def test_a_line_entry_failing_behind_a_sealed_round_leaves_it_sealed(app, monkeypatch):
    """P8 after seal(): continue_learning raises after seal(): error page, the round stays sealed and unfinished, and
    the next continue serves the same head."""
    practice_round.enable(app.store)
    cid, qid = course(app, "gap26")
    real = app.continue_learning

    def failing(*args, **kwargs):
        raise RuntimeError("合成")
    app.continue_learning = failing
    with pytest.raises(RuntimeError):
        app.continue_study_path()
    plan = cs.round_plan(app)
    assert plan[:2] == (True, False) and cs.page(app) == ("learning_wait", "无法准备下一题")
    app.continue_learning = real
    app.continue_study_path()
    assert cs.round_plan(app) == plan and app._learning_question["id"] == qid


def test_a_take_over_failing_at_a_round_end_leaves_the_round_finished(app, monkeypatch):
    """P8 at D5: take_over raises at D5: the error page; mark_finished already happened."""
    practice_round.enable(app.store)

    def failing(app):
        raise RuntimeError("合成")
    cs.spy_take_over(monkeypatch, failing)
    with pytest.raises(RuntimeError):
        app.continue_study_path()
    assert cs.round_plan(app)[:2] == (True, True) and cs.page(app) == ("learning_wait", "无法准备下一题")


# --- g. chrome ------------------------------------------------------------------------------------------------

def reach(app, monkeypatch, which):
    if which == "round_end":
        practice_round.enable(app.store)
        app.continue_study_path()
    elif which == "route_end":
        app.continue_study_path()
    elif which == "no_kind":
        no_usable_kind(app)
        app.continue_study_path()
    else:
        cs.make_dispatch_raise(monkeypatch, RuntimeError("合成"))
        with pytest.raises(RuntimeError):
            app.continue_study_path()


@pytest.mark.parametrize("which,name", [("round_end", "learning_done"), ("route_end", "learning_done"),
                                        ("no_kind", "learning_wait"), ("error", "learning_wait")])
def test_system_back_on_a_dispatch_page_returns_home_without_a_dialog(app, monkeypatch, which, name):
    """D6, D14, D12 and P8: the dispatch's pages carry the page names test_focused_navigation treats as saved focused
    pages: system Back (and the header button, the same go_back) returns home at once."""
    reach(app, monkeypatch, which)
    assert app.page == name
    app.loop = Mock()
    assert KanaMemory.go_back(app) is True
    assert app.page == "home" and not app.loop.create_task.called


def test_system_back_on_the_wait_page_asks_before_leaving(app, monkeypatch):
    """W1: the wait page is focused but not a learning page: Back asks 返回今日？ and stays."""
    due_unqueued_kana(app)
    cs.background_on(app)
    seen = {}

    def back():
        app.loop = Mock()
        seen["result"] = KanaMemory.go_back(app)
        seen["page"] = app.page
        (coroutine,), _ = app.loop.create_task.call_args
        coroutine.close()
        app._leave_confirmation_active = False
    cs.in_loop(app, app.continue_study_path, while_waiting=back)
    assert seen == {"result": True, "page": cs.WAIT_PAGE}


def real_navigation(app, *wrapped):
    """The production screen, Back and page history (use_real_detail_back), the continue action and ``wrapped``
    (StudyPathUIMixin / LearningUIMixin method names) with their page_view wrappers, from a drawn home."""
    from types import MethodType
    from kanamemory.learning_ui import LearningUIMixin
    from kanamemory.study_path_runtime import StudyPathRuntimeMixin
    from kanamemory.study_path_ui import StudyPathUIMixin
    from support.learning_fixtures import use_real_detail_back
    use_real_detail_back(app)
    app._view_key = ("show_home", None)
    app.screen("home", "今日学习", View(text="首页"))
    app.continue_study_path = MethodType(StudyPathRuntimeMixin.continue_study_path, app)
    for name in wrapped:
        owner = StudyPathUIMixin if hasattr(StudyPathUIMixin, name) else LearningUIMixin
        setattr(app, name, MethodType(getattr(owner, name), app))


@pytest.mark.parametrize("which,caption", [("no_kind", "学习路线"), ("route_end", "题型与练习设置")])
def test_back_from_a_target_page_remounts_the_retained_end_page(app, which, caption):
    """D12 and D14: from 暂时没有可进行的假名题型 / 本次学习已保存 a @page_view target remembers the page;
    Back remounts that same widget tree (not redrawn) with its title."""
    real_navigation(app, "show_study_path", "show_practice_hub")
    if which == "no_kind":
        no_usable_kind(app)
    app.continue_study_path()
    name, title = app.page, app.title_label.text
    view = app.content_slot.children[0]
    cs.press(app, caption)
    assert app.page != name and len(app.back_stack.pages) == 1
    app.go_back()
    assert (app.page, app.title_label.text) == (name, title) and app.content_slot.children[0] is view


def test_the_wait_page_is_checkpointed_after_it_is_drawn(app):
    """W1: the production screen() checkpoints after drawing kana_queue_assembly
    (the end pages' checkpoint returns early inside _checkpoint itself)."""
    real_navigation(app)
    due_unqueued_kana(app)
    cs.background_on(app)
    pages = []
    app._checkpoint.side_effect = lambda: pages.append(app.page)
    cs.in_loop(app, app.continue_study_path)
    assert cs.WAIT_PAGE in pages and pages.index(cs.WAIT_PAGE) < pages.index("question")


# --- the app's methods replaced on the instance are honoured ---------------------------------------------------

def test_replacements_of_the_apps_methods_on_the_instance_are_what_the_action_calls(app):
    """D3 and D4: resume_practice, continue_learning, show_study_reference, _add_study_sections and
    study_path_can_continue replaced on the app object are the ones called."""
    reviewed(app, "あ")
    seed_main(app)
    app.resume_practice = Mock()
    app.continue_study_path()
    app.resume_practice.assert_called_once()
    assert app.resume_practice.call_args.kwargs["preserve_audio"] is True
    course(app, "inst")
    app.store.clear_session_progress()
    app.continue_learning = Mock()
    app.continue_study_path()
    app.continue_learning.assert_called_once()
    assert "_projection" in app.continue_learning.call_args.kwargs


def test_replaced_end_page_targets_are_the_ones_pressed(app):
    """D14: _add_study_sections and study_path_can_continue replaced on the instance: the route end page
    under breadth asks the replaced can_continue and its note button calls the replaced _add_study_sections."""
    ArrangementPreferences(app.store).set_orientation(Orientation.BREADTH)
    app._add_study_sections = Mock()
    asked = []
    app.study_path_can_continue = lambda: asked.append(1) or False
    app.continue_study_path()
    assert asked == [1]
    caption = cs.texts(app)[1]
    cs.press(app, caption)
    app._add_study_sections.assert_called_once_with([app.study_path_service().overview()["next_section"]["id"]])


def test_reference_replacement_is_called(app, monkeypatch):
    monkeypatch.setattr(app.study_path_service(), "next_reference_entry", lambda: {"id": "ref:x"})
    app.show_study_reference = Mock()
    app.continue_study_path()
    app.show_study_reference.assert_called_once_with("ref:x")


def test_without_the_switch_a_running_loop_starts_no_wait(app):
    """The waits need the background switch: under a running loop without it, the action ends inline."""
    due_unqueued_kana(app)
    seen, tasks = cs.in_loop(app, app.continue_study_path)
    assert tasks == [] and seen[0] == "question"
    assert not cs.background_enabled(app) and asyncio.iscoroutinefunction(cs.settle)
