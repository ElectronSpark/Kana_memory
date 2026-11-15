"""The kana practice's next step on the app side (design 13): the decision as a value, and what performing a
step calls.

The decision is taken on a bare object over a recording store, with a class of doubles for the app's helpers;
performing is checked the same way, the pages drawn into fake controls. The pages' own content is pinned by
the page tests, and the whole conversation with the store by the characterisation tests.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kanamemory import extra_practice_runtime, kana_dependency_repair, learning_content, new_dispatch, practice_round
from kanamemory import kana_attempt_runtime, kana_session_runtime
from kanamemory import kana_runtime as line
from kanamemory import ui_components as component_ui
from kanamemory.app_clock import FrozenClock, using_clock
from kanamemory.domain import Question
from kanamemory.kana_line import model
from kanamemory.kana_line.model import KanaAction, KanaActionKind as Kind
from kanamemory.session_content import RETIRE
from support.fake_ui import View

SOURCE = Path(line.__file__).parent
ENTRIES = ("show_word_drill_setup", "show_practice_setup", "show_practice_hub", "show_home", "show_progress",
           "resume_practice", "continue_spaced_practice", "continue_study_path", "replace_content_question",
           "enroll_learning", "show_kana_introduction", "show_kana_intro_wait")


def kana(content_id="ka", kind="kana_choice", **metadata):
    return Question(content_id, kind, "hiragana", "", "ka", options=["ka", "na"],
                    metadata={"kana_symbol": "か", "romaji": "ka", "content_version": "v1", **metadata})


@pytest.fixture
def practice(monkeypatch):
    """(app, K): a bare app object that holds the session "s" and two questions at their start, and a class of
    doubles for the app's helpers. The round's functions and the pages' controls are doubles too."""
    monkeypatch.setattr(component_ui, "label", lambda text, **style: View(text=text, style=("label", style)))
    monkeypatch.setattr(component_ui, "button",
                        lambda text, handler, **style: View(text=text, on_press=handler, style=("button", style)))
    store = Mock()
    store.practice_lane = "word_romaji"
    app = SimpleNamespace(store=store, repo=Mock(), platform=SimpleNamespace(tts_ready=False), loop=Mock(),
                          _page_token=7, screen=Mock(), note=lambda text: ("note", text),
                          content_label=lambda text, **style: ("symbols", text, style),
                          _path_next_controls=Mock(return_value=["next"]), study_path_service=lambda: "service",
                          round=SimpleNamespace(sealed=Mock(), finished=Mock()))
    for name in ENTRIES:
        setattr(app, name, Mock(name=name))
    kana_session_runtime.assign(app, session_id="s", questions=[kana("ka"), kana("na")], question_index=0,
                                session_right=0)
    kana_attempt_runtime.new_marks(app)
    kana_attempt_runtime.assign(app, revealed=True, answered_text="before", correct=True)
    monkeypatch.setattr(practice_round, "seal", app.round.sealed)
    monkeypatch.setattr(practice_round, "mark_finished", app.round.finished)
    monkeypatch.setattr(practice_round, "active", lambda value: False)
    monkeypatch.setattr(new_dispatch, "study_path_available", lambda value: False)
    monkeypatch.setattr(extra_practice_runtime, "take_over", lambda value: False)
    monkeypatch.setattr(extra_practice_runtime, "extra_button_rows", lambda value: ["extra"])
    monkeypatch.setattr(line, "summarize_queue_availability",
                        lambda *a, **k: SimpleNamespace(waiting_count=3, ready_count=2))
    monkeypatch.setattr(line, "current_route_next", lambda service: ("route", service))
    K = SimpleNamespace(
        _lazy_practice_questions=Mock(return_value=False), _join_collecting_round=Mock(return_value=None),
        _prepared_pending=Mock(return_value=None), _reconcile_practice_kinds=Mock(side_effect=lambda app, pending: pending),
        _queue_kinds=Mock(return_value=["kana_choice"]), _open_practice_deck=Mock(return_value=False),
        _ask_question=Mock(), _tick_review_wait=object())
    return app, K


def head_of(app, **fields):
    """The store holds progress of the session and prepares a head with ``fields`` over it."""
    session = app._kana_session
    pending = {"session_id": "s", "questions": session.questions, "question_index": 0, "session_right": 0,
               "state": {}, "assisted_indices": []}
    app.store.load_session_progress.return_value = pending
    head = {**pending, **fields}
    app.store.prepare_next_question.return_value = head
    return head


# --- the decision ---------------------------------------------------------------------------------------------------

def test_a_queue_without_a_session_asks_its_question_without_a_prepared_head(practice):
    app, K = practice
    kana_session_runtime.assign(app, session_id=None)
    step = line.decide_kana(app, K)
    assert step == model.Ask(None)
    assert app._kana_session.question is app._kana_session.questions[0] and app._kana_session.script == "hiragana"
    app.store.load_session_progress.assert_not_called()
    app.round.sealed.assert_called_once_with(app.store)
    app.screen.assert_not_called()


def test_a_prepared_head_gives_the_position_the_marks_and_the_question_to_ask(practice):
    app, K = practice
    questions = [kana("na"), kana("ka")]
    head = head_of(app, questions=questions, question_index=1, session_right=1, assisted_indices=[0],
                   state={"answer_revealed": True})
    step = line.decide_kana(app, K)
    assert step == model.Ask(head) and step.prepared is head
    session = app._kana_session
    assert (session.questions, session.question_index, session.session_right, session.question) == (
        questions, 1, 1, questions[1])
    assert app._kana_marks.assisted == {("s", 0), ("s", 1)}
    K._reconcile_practice_kinds.assert_called_once_with(app, app.store.load_session_progress.return_value)
    app.store.prepare_next_question.assert_called_once_with("s")


def test_nothing_prepared_is_the_end_of_the_queue(practice):
    app, K = practice
    head_of(app)
    app.store.prepare_next_question.return_value = None
    assert line.decide_kana(app, K) == model.Finished()
    assert app._kana_session.question_index == 2
    assert app._kana_session.get("question", "never set") == "never set"


def test_a_refused_reconcile_stops_before_the_round_is_sealed(practice):
    app, K = practice
    head_of(app)
    K._reconcile_practice_kinds.side_effect = None
    K._reconcile_practice_kinds.return_value = False
    assert line.decide_kana(app, K) == model.Stopped()
    app.round.sealed.assert_not_called()
    app.store.prepare_next_question.assert_not_called()


@pytest.mark.parametrize("fields,step", [
    ({"content_blocked": True, "settings_blocked": True}, model.ContentUpdated()),
    ({"settings_blocked": True, "dependency_blocked": True}, model.KindsPaused()),
    ({"dependency_blocked": True, "blocked_prerequisites": ["kana:ka:hiragana"], "waiting_until_utc": "t"},
     model.PrerequisiteMissing(["kana:ka:hiragana"])),
    ({"dependency_blocked": True}, model.PrerequisiteMissing(())),
])
def test_a_blocked_head_is_the_first_stop_that_applies(practice, fields, step):
    app, K = practice
    app.research_enabled = "kept"
    head_of(app, **fields)
    assert line.decide_kana(app, K) == step
    assert app.research_enabled == "kept"
    assert app._kana_session.question is app._kana_session.questions[0]


def test_a_waiting_head_is_the_review_wait_with_the_head(practice):
    app, K = practice
    head = head_of(app, waiting_until_utc="2030-01-01T09:00:00+00:00",
                   questions=[kana("ka", research_content=True, word_table="full"), kana("na")])
    step = line.decide_kana(app, K)
    assert step == model.ReviewWait("2030-01-01T09:00:00+00:00", head) and step.prepared is head
    assert (app.research_enabled, app.use_full_keyboard) == (True, True)


@pytest.mark.parametrize("restoring,step", [(False, model.ResumeParked()), (True, None)])
def test_a_started_word_is_restored_unless_the_practice_is_being_restored(practice, restoring, step):
    app, K = practice
    if restoring:
        app._restoring_practice = True
    head = head_of(app, questions=[kana("word", "word_kana"), kana("na")], state={"page": "word_question"})
    assert line.decide_kana(app, K) == (step or model.Ask(head))


def test_a_kana_to_introduce_is_introduced_and_one_that_waits_waits(practice, monkeypatch):
    app, K = practice
    app.learning_store = object()
    head_of(app)
    introduce, due = Mock(return_value=True), Mock()
    monkeypatch.setattr(line, "should_introduce", introduce)
    monkeypatch.setattr(line, "intro_wait_until", due)
    question = app._kana_session.questions[0]
    assert line.decide_kana(app, K) == model.Introduce(question)
    introduce.assert_called_once_with(app, question)
    due.assert_not_called()
    introduce.return_value = False
    with using_clock(FrozenClock("2030-01-01T08:00:00+00:00")):
        later = datetime(2030, 1, 1, 8, 0, 1, tzinfo=timezone.utc)
        due.return_value = later
        assert line.decide_kana(app, K) == model.IntroWait(later)
        due.return_value = datetime(2030, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
        assert isinstance(line.decide_kana(app, K), model.Ask)
        due.return_value = None
        assert isinstance(line.decide_kana(app, K), model.Ask)


def test_the_helpers_are_looked_up_on_the_class_at_the_moment_they_are_called(practice, monkeypatch):
    app, K = practice
    head_of(app)
    K._lazy_practice_questions = Mock(return_value=True)
    K._queue_kinds = Mock(return_value=["replaced"])
    repair = Mock(return_value={"added": 0})
    monkeypatch.setattr(kana_dependency_repair, "ensure_kana_prerequisites", repair)
    app.store.practice_lane = "main"
    line.decide_kana(app, K, first_teaching=True, first_teaching_targets=("ka",))
    assert repair.call_args.args[2] == ["replaced"] and repair.call_args.kwargs["lazy_questions"] is True
    assert app.store.prepare_next_question.call_args.kwargs == {
        "lazy_questions": True, "fair_new": True, "teaching_only": True, "teaching_kinds": ["replaced"],
        "teaching_targets": ("ka",)}


# --- the buttons ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind,entry", [
    (Kind.WORD_SETUP, "show_word_drill_setup"), (Kind.PRACTICE_SETUP, "show_practice_setup"),
    (Kind.PRACTICE_HUB, "show_practice_hub"), (Kind.RECORDS, "show_progress"), (Kind.HOME, "show_home"),
    (Kind.CHECK_PREREQUISITES, "resume_practice"), (Kind.CHECK_WAIT, "continue_spaced_practice")])
def test_a_button_calls_its_entry_with_the_pressed_control(practice, kind, entry):
    app, _ = practice
    getattr(app, entry).return_value = "returned"
    assert line.run_kana_action(app, KanaAction(kind), "button") == "returned"
    getattr(app, entry).assert_called_once_with("button")
    assert sum(getattr(app, name).call_count for name in ENTRIES) == 1


def test_the_two_buttons_with_values_hand_them_on_without_the_control(practice):
    app, _ = practice
    rule = {"title": "长音怎么读"}
    line.run_kana_action(app, KanaAction(Kind.USE_NEW_CONTENT, session_id="s", retired_id="old"), "button")
    app.replace_content_question.assert_called_once_with("s", "old")
    line.run_kana_action(app, KanaAction(Kind.LEARN_RULE, rule=rule), "button")
    app.enroll_learning.assert_called_once_with(rule)


def test_an_unknown_action_and_an_unknown_step_are_errors(practice):
    app, K = practice
    with pytest.raises(ValueError):
        line.run_kana_action(app, SimpleNamespace(kind="no such kind"))
    with pytest.raises(ValueError):
        line.perform_kana(app, K, "no such step")


# --- performing a step ----------------------------------------------------------------------------------------------

def test_the_steps_that_are_one_call(practice):
    app, K = practice
    question, head = kana(), {"a": "head"}
    line.perform_kana(app, K, model.Stopped())
    assert sum(getattr(app, name).call_count for name in ENTRIES) == 0 and not app.screen.called
    line.perform_kana(app, K, model.Introduce(question))
    app.show_kana_introduction.assert_called_once_with(question)
    line.perform_kana(app, K, model.IntroWait("due"))
    app.show_kana_intro_wait.assert_called_once_with("due")
    line.perform_kana(app, K, model.ResumeParked())
    app.resume_practice.assert_called_once_with(preserve_audio=True)
    line.perform_kana(app, K, model.Ask(head))
    K._ask_question.assert_called_once_with(app, head)
    app.screen.assert_not_called()


def texts(app):
    name, title, *children = app.screen.call_args.args
    return name, title, [child[1] if isinstance(child, tuple) else getattr(child, "text", child) for child in children]


def cleared(app):
    attempt = app._kana_attempt
    return (attempt.revealed, attempt.answered_text, attempt.correct) == (False, None, False)


def test_updated_content_clears_the_attempt_and_offers_the_retired_question_from_the_place_on(practice):
    app, K = practice
    old = kana("ka", **{RETIRE: "before"})
    kana_session_runtime.assign(app, questions=[old, kana("na"), kana("ka", **{RETIRE: "the one"})], question_index=1)
    line.perform_kana(app, K, model.ContentUpdated())
    assert texts(app) == ("session_wait", "题目内容已更新",
                          ["原题和草稿已保存。更新后继续使用原有学习记录。", "使用更新后的题目", "练习设置", "回到今日"])
    assert cleared(app)
    use_new, setup = app.screen.call_args.args[3], app.screen.call_args.args[4]
    use_new.on_press("button")
    app.replace_content_question.assert_called_once_with("s", "the one")
    setup.on_press("button")
    app.show_word_drill_setup.assert_called_once_with("button")


def test_paused_kinds_clear_the_attempt_and_lead_to_the_settings_of_the_lane(practice):
    app, K = practice
    app.store.practice_lane = "main"
    line.perform_kana(app, K, model.KindsPaused())
    assert texts(app) == ("session_wait", "题型已暂停", ["未完成的题目和草稿已保留。开启题型后可继续。", "练习设置", "回到今日"])
    assert cleared(app)
    app.screen.call_args.args[3].on_press("button")
    app.show_practice_setup.assert_called_once_with("button")


def test_missing_prerequisites_are_looked_up_and_named(practice, monkeypatch):
    app, K = practice
    app.repo.get_kana.side_effect = lambda content_id: {"ka": {"hiragana": "か"}, "na": {"katakana": "ナ"}}.get(content_id)
    rule = {"title": "长音怎么读"}
    monkeypatch.setattr(learning_content, "get_learning_item", lambda identity: rule if identity.endswith("long") else None)
    blocked = ["kana:ka:hiragana", "pronunciation:long", "kana:na:hiragana", "kana:gone:hiragana", "pronunciation:gone",
               "kana:na:katakana"]
    line.perform_kana(app, K, model.PrerequisiteMissing(blocked))
    name, title, symbols, rule_button, *_ = app.screen.call_args.args
    assert (name, title) == ("dependency_wait", "先学所需规则")
    assert symbols == ("symbols", "か · ナ", {"size": 26, "color": component_ui.GREEN, "text_role": "study"})
    assert cleared(app)
    rule_button.on_press("button")
    app.enroll_learning.assert_called_once_with(rule)


def test_the_review_wait_keeps_its_time_and_controls_on_the_app_and_arms_its_timer_after_the_page(practice,
                                                                                                  monkeypatch):
    app, K = practice
    order = Mock()
    app.screen = order.screen
    app.loop = order.loop
    head = {"the": "head"}
    asked = Mock(return_value=SimpleNamespace(waiting_count=4))
    monkeypatch.setattr(line, "summarize_queue_availability", asked)
    with using_clock(FrozenClock("2030-01-01T08:59:30+00:00")):
        line.perform_kana(app, K, model.ReviewWait("2030-01-01T09:00:00+00:00", head))
    assert [entry[0] for entry in order.mock_calls] == ["screen", "loop.call_soon"]
    name, title, status, due, saved, check, *rest = order.screen.call_args.args
    assert (name, title, status.text, due.text) == ("session_wait", "本轮先歇一会儿", "距离下一题还有 30 秒。",
                                                    "下一题可在 09:00:00 开始")
    assert saved == ("note", "已保存 4 道后续复习题，到期后自动继续。也可以先回到今日。")
    assert rest[:-1] == ["extra"] and rest[-1].text == "回到今日"
    asked.assert_called_once_with(app.store, head)
    assert app._review_wait_until == datetime(2030, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
    assert app._review_wait_checked is False
    assert app._review_wait_status is status and app._review_wait_button is check
    order.loop.call_soon.assert_called_once_with(K._tick_review_wait, app, 7)
    assert app._review_wait_handle is order.loop.call_soon.return_value
    assert cleared(app)
    check.on_press("button")
    app.continue_spaced_practice.assert_called_once_with("button")


def at_the_end(app):
    kana_session_runtime.assign(app, question_index=2, session_right=1)
    app.store.get_session_summary.return_value = {"answered": 5, "content_count": 3, "skipped": 0}


def test_the_end_of_the_word_lane_and_of_a_batch(practice, monkeypatch):
    app, K = practice
    at_the_end(app)
    line.perform_kana(app, K, model.Finished())
    app.round.finished.assert_called_once_with(app.store)
    K._open_practice_deck.assert_called_once_with(app)
    assert texts(app) == ("session_done", "本组单词完成",
                          ["完成 5 词 · 答对 1 词", "拼写记录已保存；符合条件的假名复习时间已调整。", "再练一组单词", "回到今日"])
    app.store.practice_lane = "main"
    monkeypatch.setattr(new_dispatch, "study_path_available", lambda value: True)
    line.perform_kana(app, K, model.Finished())
    assert texts(app) == ("session_done", "本批完成",
                          ["练习 3 个假名记忆项 · 完成 5 题\n答对 1 题", "next", "extra", "查看复习记录", "回到今日"])
    app._path_next_controls.assert_called_once_with("route", "service")


@pytest.mark.parametrize("ending", ["route", "deck", "taken over"])
def test_the_ends_that_draw_no_page(practice, monkeypatch, ending):
    app, K = practice
    at_the_end(app)
    app.store.practice_lane = "main"
    monkeypatch.setattr(practice_round, "active", lambda value: ending == "route")
    monkeypatch.setattr(new_dispatch, "study_path_available", lambda value: ending == "route")
    K._open_practice_deck.return_value = ending == "deck"
    monkeypatch.setattr(extra_practice_runtime, "take_over", lambda value: ending == "taken over")
    line.perform_kana(app, K, model.Finished())
    app.screen.assert_not_called()
    assert app.continue_study_path.call_args_list == ([((), {"_checkpoint_saved": True})] if ending == "route" else [])
    assert app.round.finished.called is (ending != "route")
    assert app.store.get_session_summary.called is (ending == "taken over")


# --- what the module may import -------------------------------------------------------------------------------------

def imported(path, *, top_level_only=False):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in (tree.body if top_level_only else ast.walk(tree)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level + (node.module or ""))
    return names


def test_the_app_side_module_imports_no_controls_and_not_the_app():
    everywhere = imported(SOURCE / "kana_runtime.py")
    assert not {name for name in everywhere if name in {".app", ".ui_components", "toga"} or name.startswith("toga.")}
    assert imported(SOURCE / "kana_runtime.py", top_level_only=True) == {
        "__future__", "datetime", ".", ".kana_learning_intro", ".kana_line", ".kana_line.model",
        ".practice_availability", ".practice_preferences", ".study_path_runtime"}
    # The page module, which creates controls, is imported only where a page is drawn.
    assert ".kana_line.ui" in everywhere
