"""The course line's decision as a value (design 13): course_runtime.decide_course, perform_course and
run_course_action.

decide_course, on the path_app fixture with the learner store's head replaced by hand-made heads, returns
each course step (with and without the continue action's read context) and draws nothing, arms no timer
and makes no hand-off call while it decides; it reads the count, the question ids and the taught
dependencies itself. A real entry (continue_learning) decides once and performs afterwards. perform_course
makes the one call of each hand-made step with its exact arguments; run_course_action makes the call of
each page action against Mock targets (the acknowledgement with the id and scope of the drawing). The
import guard keeps course_runtime free of Toga, the control components and the app module, anywhere in
the file.

Public: hand-made heads and synthetic values only.
"""
import ast
from pathlib import Path
from unittest.mock import Mock

import pytest

from kanamemory import course_runtime
from kanamemory.course_line.model import (PAUSED_DISABLED, PAUSED_NO_HEAD, PAUSED_QUIZ, Ask, Batch, CourseAction,
                                          CourseActionKind as Kind, Diagnostic, Done, LibraryOff, MaterialBlocked,
                                          Paused, Prerequisite, Teach, Wait)
from kanamemory.learning_dependencies import TAUGHT
from support import course_support as course_line
from support.app_fixtures import question_app, setup_app  # noqa: F401
from support.fake_ui import View
from support.learning_fixtures import learning_app  # noqa: F401
from support.study_home_support import orientation_seen, stub_can_continue
from support.study_path_runtime_support import path_app  # noqa: F401

NO_HEAD_TEXT = "未完成题目已保留，重新开启对应题型后可继续。"
DISABLED_TEXT = "未完成题目与复习时间已保留，开启对应题型后可继续。"
DUE = "2026-03-04T05:06:07+00:00"
CONTENT = {"id": "grammar:synthetic-decide", "title": "合成判定", "instruction_scope": None}


@pytest.fixture
def app(path_app):
    """path_app with the orientation seen and "can 继续学习 reach anything" answering False."""
    orientation_seen(path_app)
    stub_can_continue(path_app, False)
    return path_app


def heads(app, monkeypatch, head, *, disabled=0):
    """The learner store's head is ``head`` and its counts report ``disabled`` closed items; the reads log."""
    def answer(name, *args, **kwargs):
        return head if name == "current" else {"disabled": disabled}
    return course_line.record_head_reads(app, monkeypatch, answer=answer)


def decided(app, monkeypatch, **kwargs):
    """decide_course with nothing recorded while it decides: no page, no hand-off call, no timer armed."""
    log = course_line.watch(app, monkeypatch, real=False)
    arming = course_line.record_wait_arming(app, monkeypatch)
    step = course_runtime.decide_course(app, restart_finished=False, **kwargs)
    assert course_line.calls(log) == [] and arming == []
    return step


# --- decide_course: each course step ---------------------------------------------------------------------

def test_a_head_of_a_switched_off_library_is_library_off_with_its_count(app, monkeypatch):
    heads(app, monkeypatch, {"type": "content_blocked", "reason": "library_disabled",
                             "counts": {"library_unavailable": 4}})
    assert decided(app, monkeypatch) == LibraryOff(4)


def test_a_head_of_a_switched_off_library_without_counts_is_library_off_with_zero(app, monkeypatch):
    heads(app, monkeypatch, {"type": "content_blocked", "reason": "library_disabled"})
    assert decided(app, monkeypatch) == LibraryOff(0)


def test_a_blocked_material_is_material_blocked_with_the_stored_ids_object(app, monkeypatch):
    ids = ["q:a", "q:b"]
    heads(app, monkeypatch, {"type": "content_blocked", "question_ids": ids})
    step = decided(app, monkeypatch)
    assert step == MaterialBlocked(ids) and step.question_ids is ids


def test_a_head_the_batch_takes_is_batch_and_the_batch_page_is_not_shown_while_deciding(app, monkeypatch):
    head = {"type": "instruction", "content": dict(CONTENT)}
    heads(app, monkeypatch, head)
    asked = course_line.batch_takes_head(app, monkeypatch)
    assert decided(app, monkeypatch) == Batch()
    assert [call.args[0] for call in asked] == [head]


def test_no_head_with_closed_work_is_paused_no_head(app, monkeypatch):
    heads(app, monkeypatch, None, disabled=2)
    assert decided(app, monkeypatch) == Paused(PAUSED_NO_HEAD, NO_HEAD_TEXT)


def test_no_head_is_done_and_the_round_is_finished(app, monkeypatch):
    heads(app, monkeypatch, None)
    rounds = course_line.record_round(monkeypatch)
    assert decided(app, monkeypatch) == Done()
    assert [call.name for call in rounds if call.name != "sealed"] == ["begin", "seal", "mark_finished"]


def test_a_disabled_head_is_paused_disabled(app, monkeypatch):
    heads(app, monkeypatch, {"type": "disabled"})
    course_line.record_batch_route(app, monkeypatch, answer=False)
    assert decided(app, monkeypatch) == Paused(PAUSED_DISABLED, DISABLED_TEXT)


def test_a_left_out_head_of_a_sealed_round_is_done(app, monkeypatch):
    head = {"type": "disabled"}
    reads = heads(app, monkeypatch, head, disabled=3)
    course_line.patch_round_sealed(monkeypatch, lambda store: True)
    course_line.record_batch_route(app, monkeypatch, answer=False)
    asked = course_line.record_left_out(app, monkeypatch, answer=True)
    assert decided(app, monkeypatch) == Done()
    assert [call.args for call in asked] == [(head, None)]
    assert course_line.course_head(app) is None
    assert "counts" not in [call.name for call in course_line.own_calls(reads)]


def test_a_diagnostic_head_is_the_diagnostic_line(app, monkeypatch):
    heads(app, monkeypatch, {"type": "diagnostic", "episode_id": "episode:7"})
    course_line.record_batch_route(app, monkeypatch, answer=False)
    assert decided(app, monkeypatch) == Diagnostic("episode:7")


@pytest.mark.parametrize("due", [DUE, None], ids=["due", "no-due"])
def test_a_waiting_head_is_wait_with_its_due_time(app, monkeypatch, due):
    heads(app, monkeypatch, {"type": "waiting", "due_at_utc": due})
    course_line.record_batch_route(app, monkeypatch, answer=False)
    assert decided(app, monkeypatch) == Wait(due)


def test_a_prerequisite_head_is_prerequisite_with_the_taught_titles_read_in_the_decision(app, monkeypatch):
    content = dict(CONTENT)
    status = {"dependencies": [{"title": "甲", "status": TAUGHT}, {"title": "乙", "status": "waiting"},
                               {"title": "丙", "status": TAUGHT}]}
    heads(app, monkeypatch, {"type": "prerequisite", "content": content, "due_at_utc": DUE,
                             "dependency_status": status})
    course_line.record_batch_route(app, monkeypatch, answer=False)
    step = decided(app, monkeypatch)
    assert step == Prerequisite(content, DUE, ("甲", "丙")) and step.content is content
    assert isinstance(step.taught, tuple)


def test_a_prerequisite_head_without_due_or_status_is_prerequisite_with_nothing_taught(app, monkeypatch):
    heads(app, monkeypatch, {"type": "prerequisite", "content": dict(CONTENT)})
    course_line.record_batch_route(app, monkeypatch, answer=False)
    assert decided(app, monkeypatch) == Prerequisite(dict(CONTENT), None, ())


def test_a_teaching_head_is_teach(app, monkeypatch):
    content = dict(CONTENT)
    heads(app, monkeypatch, {"type": "instruction", "content": content})
    course_line.record_batch_route(app, monkeypatch, answer=False)
    step = decided(app, monkeypatch)
    assert step == Teach(content) and step.content is content


def test_a_question_head_is_ask_with_the_whole_head(app, monkeypatch):
    head = {"type": "question", "question": {"id": "q:synthetic-decide", "kind": "grammar_meaning"}, "content": CONTENT}
    heads(app, monkeypatch, head)
    course_line.record_batch_route(app, monkeypatch, answer=False)
    step = decided(app, monkeypatch)
    assert step == Ask(head) and step.current is head
    assert course_line.course_head(app) is head


def test_an_unknown_head_raises_after_the_round_is_sealed_and_the_head_remembered(app, monkeypatch):
    head = {"type": "synthetic-unknown"}
    heads(app, monkeypatch, head)
    course_line.record_batch_route(app, monkeypatch, answer=False)
    rounds = course_line.record_round(monkeypatch)
    with pytest.raises(ValueError, match="Unsupported learning dispatch state"):
        decided(app, monkeypatch)
    assert [call.name for call in rounds if call.name != "sealed"] == ["begin", "seal"]
    assert course_line.course_head(app) is head


# --- decide_course: the read context --------------------------------------------------------------------

@pytest.mark.parametrize("with_read", [True, False], ids=["projection", "live"])
def test_the_read_context_reaches_the_head_and_the_counts(app, monkeypatch, with_read):
    read = object() if with_read else None
    reads = heads(app, monkeypatch, None, disabled=1)
    step = decided(app, monkeypatch, **({"projection": read} if with_read else {}))
    assert step == Paused(PAUSED_NO_HEAD, NO_HEAD_TEXT)
    expected = {"projection": read} if with_read else {}
    made = course_line.calls(reads)
    assert [name for name, _, _ in made] == ["current", "counts"]
    assert {key: value for key, value in made[0][2].items() if key == "projection"} == expected
    assert made[1][2] == expected


@pytest.mark.parametrize("with_read", [True, False], ids=["projection", "live"])
def test_the_read_context_reaches_the_left_out_check(app, monkeypatch, with_read):
    read = object() if with_read else None
    heads(app, monkeypatch, {"type": "disabled"})
    course_line.patch_round_sealed(monkeypatch, lambda store: True)
    course_line.record_batch_route(app, monkeypatch, answer=False)
    asked = course_line.record_left_out(app, monkeypatch, answer=True)
    assert decided(app, monkeypatch, **({"projection": read} if with_read else {})) == Done()
    assert [call.args[1] for call in asked] == [read]


def test_the_selected_head_is_kept_when_nothing_drops_it(app, monkeypatch):
    head = {"type": "waiting", "due_at_utc": DUE}
    reads = heads(app, monkeypatch, {"type": "synthetic-never"})
    course_line.record_batch_route(app, monkeypatch, answer=False)
    assert decided(app, monkeypatch, selected_current=head) == Wait(DUE)
    assert course_line.calls(reads, "current") == [] and course_line.course_head(app) is head


# --- the entry: decide inside continue_learning, then perform ---------------------------------------------

def test_continue_learning_decides_once_with_its_arguments_and_performs_afterwards(app, monkeypatch):
    heads(app, monkeypatch, {"type": "waiting", "due_at_utc": None})
    course_line.record_batch_route(app, monkeypatch, answer=False)
    log = course_line.record_screens(app, monkeypatch)
    course_line.record_decisions(monkeypatch, log=log)
    app.continue_learning(View(text="按钮"), _projection=None)
    # The decision is logged once it has settled: nothing was drawn before it, the page after it.
    assert [call.name for call in log] == ["decide", "screen"]
    assert log[0].kwargs == {"restart_finished": True, "selected_current": None, "projection": None}
    assert log[0].result == Wait(None) and log[1].args == ("learning_wait", "稍后继续")


def test_a_replaced_decision_is_performed_by_the_entry(app, monkeypatch):
    log = course_line.record_decisions(monkeypatch, answer=lambda app, **kwargs: Done())
    screens = course_line.record_screens(app, monkeypatch)
    app.continue_learning()
    assert [call.result for call in log] == [Done()]
    assert course_line.screens(screens) == [("learning_done", "课程学习")]


# --- perform_course: the one call of each step -----------------------------------------------------------

def performer(app, monkeypatch):
    """Record the targets (rows answer [] / the replacement rows a marked row) and screen."""
    log = course_line.watch(app, monkeypatch, real=False, names=("show_intro_batch", "continue_diagnostic",
                                                                  "show_learning_instruction",
                                                                  "show_learning_question", "arm_learning_wait"))
    course_line.replace_next_content_rows(app, monkeypatch, lambda: [View(text="〈接下来〉")], log=log)
    course_line.replace_replacement_controls(app, monkeypatch, lambda ids, item: [View(text="〈替换〉")], log=log)
    course_line.replace_extra_rows(monkeypatch, lambda app: [View(text="〈加练〉")], log=log)
    return log


def made(log):
    return [(call.name, call.args, call.kwargs) for call in log]


@pytest.mark.parametrize("step, call", [
    (Batch(), ("show_intro_batch", (), {})),
    (Teach(CONTENT), ("show_learning_instruction", (CONTENT,), {})),
    (Ask({"type": "question"}), ("show_learning_question", ({"type": "question"},), {})),
], ids=["batch", "teach", "ask"])
def test_perform_hands_the_step_on_with_its_value_and_returns_none(app, monkeypatch, step, call):
    log = performer(app, monkeypatch)
    assert course_runtime.perform_course(app, step) is None
    assert made(log) == [call]


def test_perform_returns_the_diagnostic_lines_answer(app, monkeypatch):
    answer = object()
    monkeypatch.setattr(app, "continue_diagnostic", Mock(return_value=answer))
    assert course_runtime.perform_course(app, Diagnostic("episode:7")) is answer
    app.continue_diagnostic.assert_called_once_with("episode:7")


def test_perform_draws_library_off_with_the_count(app, monkeypatch):
    log = performer(app, monkeypatch)
    course_runtime.perform_course(app, LibraryOff(5))
    assert made(log) == [("next_content_rows", (), {}), ("screen", ("learning_wait", "所属内容库已停用"), {})]
    assert course_line.texts(app)[0] == "剩下的 5 项课程所属的内容库已停用、缺失或已被取代，暂不出题。"


def test_perform_draws_the_material_page_with_one_positional_argument_before_the_rows(app, monkeypatch):
    log = performer(app, monkeypatch)
    ids = ("q:a",)
    course_runtime.perform_course(app, MaterialBlocked(ids))
    assert made(log) == [("replacement_rows", (ids,), {}), ("next_content_rows", (), {}),
                         ("screen", ("learning_wait", "材料待核"), {})]
    assert log[0].args[0] is ids


@pytest.mark.parametrize("reason", [PAUSED_NO_HEAD, PAUSED_DISABLED, PAUSED_QUIZ])
def test_perform_draws_the_paused_page_with_the_steps_text(app, monkeypatch, reason):
    app.store.set_setting("learning_preferences_v1", {"enabled": []})
    log = performer(app, monkeypatch)
    course_runtime.perform_course(app, Paused(reason, "合成文字"))
    assert made(log) == [("next_content_rows", (), {}), ("screen", ("learning_wait", "题型已关闭"), {})]
    assert course_line.texts(app) == ["合成文字", "题型设置", "〈接下来〉", "回到今日"]


def test_perform_draws_done(app, monkeypatch):
    log = performer(app, monkeypatch)
    course_runtime.perform_course(app, Done())
    assert made(log) == [("next_content_rows", (), {}), ("screen", ("learning_done", "课程学习"), {})]


@pytest.mark.parametrize("step", [Wait(DUE), Wait(None)], ids=["due", "no-due"])
def test_perform_draws_wait_then_arms_the_timer_with_the_due(app, monkeypatch, step):
    log = performer(app, monkeypatch)
    course_runtime.perform_course(app, step)
    assert made(log) == [("next_content_rows", (), {}), ("extra_rows", (app,), {}),
                         ("screen", ("learning_wait", "稍后继续"), {}), ("arm_learning_wait", (step.due,), {})]


@pytest.mark.parametrize("due", [DUE, None], ids=["due", "no-due"])
def test_perform_draws_the_prerequisite_page_then_arms_the_timer_with_the_due(app, monkeypatch, due):
    log = performer(app, monkeypatch)
    course_runtime.perform_course(app, Prerequisite(CONTENT, due, ("甲",)))
    assert made(log) == [("next_content_rows", (), {}), ("screen", ("learning_wait", "先巩固前置知识"), {}),
                         ("arm_learning_wait", (due,), {})]
    assert "已学过、可以接着往后学的前置：甲" in course_line.texts(app)


def test_the_acknowledgement_carries_the_id_and_scope_of_the_drawing(app, monkeypatch):
    content = {"id": "grammar:synthetic-ack", "title": "合成", "instruction_scope": "scope:synthetic"}
    performer(app, monkeypatch)
    course_runtime.perform_course(app, Prerequisite(content, None, ()))
    content["id"], content["instruction_scope"] = "changed", "changed"
    acknowledged = course_line.record_store_calls(app, monkeypatch, "acknowledge_prerequisites",
                                                  answers={"acknowledge_prerequisites": lambda *a, **k: None})
    monkeypatch.setattr(app, "continue_added_learning", Mock())
    course_line.press_skip_prerequisites(app)
    assert course_line.calls(acknowledged) == [
        ("acknowledge_prerequisites", ("grammar:synthetic-ack",), {"instruction_scope": "scope:synthetic"})]
    app.continue_added_learning.assert_called_once_with()


def test_perform_refuses_an_unknown_step(app):
    with pytest.raises(TypeError):
        course_runtime.perform_course(app, object())


# --- run_course_action: each kind -------------------------------------------------------------------------

@pytest.mark.parametrize("kind, target", [
    (Kind.CHECK_WAIT, "check_learning_wait"), (Kind.NEW_ROUND, "continue_learning"),
    (Kind.CONTENT_LIBRARIES, "show_content_libraries"), (Kind.LEARNING_SETTINGS, "show_learning_settings"),
    (Kind.DEVICE_SETTINGS, "show_settings"), (Kind.HOME, "leave_learning")])
def test_each_page_action_calls_its_target_with_the_pressed_control(kind, target):
    app, widget = Mock(), object()
    result = course_runtime.run_course_action(app, CourseAction(kind), widget)
    getattr(app, target).assert_called_once_with(widget)
    assert result is getattr(app, target).return_value


def test_the_acknowledge_action_takes_no_control_and_hands_back():
    app = Mock()
    course_runtime.run_course_action(app, CourseAction(Kind.ACKNOWLEDGE_PREREQUISITES, "c:1", "s:1"), object())
    assert app.mock_calls == [
        ("learning_store.acknowledge_prerequisites", ("c:1",), {"instruction_scope": "s:1"}),
        ("continue_added_learning", (), {})]


def test_an_unknown_page_action_is_refused():
    with pytest.raises(ValueError):
        course_runtime.run_course_action(Mock(), Mock(kind="synthetic-unknown"), None)


# --- the import guard ----------------------------------------------------------------------------------

FORBIDDEN = ("toga", "travertino", "kanamemory.app", "kanamemory.ui_components")


def _imports(path, package):
    """Absolute module names a source file imports anywhere (inside functions too), with imported names."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package.split(".")[:len(package.split(".")) - node.level + 1] if node.level else []
            module = ".".join([*base, node.module] if node.module else base)
            names.add(module)
            names.update(module + "." + alias.name for alias in node.names)
    return names


def test_course_runtime_imports_no_toga_no_controls_and_no_app_module():
    imported = _imports(Path(course_runtime.__file__), "kanamemory")
    assert "kanamemory.course_line.model" in imported      # the resolver sees the relative imports
    assert not [name for name in imported
                if any(name == forbidden or name.startswith(forbidden + ".") for forbidden in FORBIDDEN)]
