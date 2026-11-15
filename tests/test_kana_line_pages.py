"""The kana practice's next step without the app object (design 13, C4).

The vocabulary: frozen values with their fields, compared by value, and the page actions. The wording and the
display models of the six pages the practice line draws itself on the way to a question, by their literal
texts. The pages: each drawn from a hand-written model into fake controls, with the full ordered list of
children, every control's keyword arguments, where the handed-in rows stand (with and without rows) and the
action every button hands on with the pressed control. The page module follows the shared page contract; the
pure modules import exactly their registered modules.
"""
import ast
import dataclasses
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from kanamemory import ui_components as component_ui
from kanamemory.kana_line import display, model
from kanamemory.kana_line.display import (BatchDoneModel, ContentUpdatedModel, KanaButton, KindsPausedModel,
                                          PrerequisiteModel, ReviewWaitModel, WordDoneModel)
from kanamemory.kana_line.model import KanaAction, KanaActionKind as Kind
from kanamemory.kana_line.ui import line_pages
from support.fake_ui import View
from support.ui_module_contract import assert_page_module_holds_no_app_store_or_service

PACKAGE_DIR = Path(model.__file__).parent
UI_DIR = PACKAGE_DIR / "ui"
HOME = KanaButton("回到今日", KanaAction(Kind.HOME))


@pytest.fixture
def fake_ui(monkeypatch):
    def label(text, **style):
        return View(text=text, style=("label", style))

    def button(text, handler, **style):
        return View(text=text, on_press=handler, style=("button", style))

    monkeypatch.setattr(component_ui, "label", label)
    monkeypatch.setattr(component_ui, "button", button)
    return View


def drawn(children):
    """Each child as (kind, text, keyword arguments); a handed-in row as itself."""
    return [child if child.style is None else (child.style[0], child.text, child.style[1]) for child in children]


def recorder():
    pressed = []
    return pressed, lambda action, widget: pressed.append((action, widget))


def press_all(controls):
    for control in controls:
        if control.on_press is not None:
            control.on_press(control)


def button(caption, kind, **values):
    return KanaButton(caption, KanaAction(kind, **values))


# --- the vocabulary -------------------------------------------------------------------------------------------------

def fields(cls):
    return [field.name for field in dataclasses.fields(cls)]


def test_the_steps_are_frozen_values_with_these_fields():
    assert [(cls.__name__, fields(cls)) for cls in (
        model.Stopped, model.Finished, model.ContentUpdated, model.KindsPaused, model.PrerequisiteMissing,
        model.Introduce, model.IntroWait, model.ReviewWait, model.ResumeParked, model.Ask)] == [
        ("Stopped", []), ("Finished", []), ("ContentUpdated", []), ("KindsPaused", []),
        ("PrerequisiteMissing", ["blocked"]), ("Introduce", ["question"]), ("IntroWait", ["due"]),
        ("ReviewWait", ["until", "prepared"]), ("ResumeParked", []), ("Ask", ["prepared"])]
    assert model.ReviewWait("t", {"a": 1}) == model.ReviewWait("t", {"a": 1}) != model.ReviewWait("u", {"a": 1})
    with pytest.raises(dataclasses.FrozenInstanceError):
        model.Ask(None).prepared = {}


def test_the_actions_of_the_pages():
    assert [kind.value for kind in Kind] == [
        "word_setup", "practice_setup", "practice_hub", "records", "home", "use_new_content", "learn_rule",
        "check_prerequisites", "check_wait"]
    assert fields(KanaAction) == ["kind", "session_id", "retired_id", "rule"]
    assert KanaAction(Kind.HOME) == KanaAction(Kind.HOME, None, None, None)
    assert model.setup_kind(True) is Kind.WORD_SETUP and model.setup_kind(False) is Kind.PRACTICE_SETUP


def test_the_pages_on_which_a_question_is_answered():
    assert model.QUESTION_PAGES == {"question", "word_question", "ink_question"} and model.RESULT_PAGE == "feedback"


@pytest.mark.parametrize("prepared,started", [
    (None, False), ("not a head", False), ({"state": {}}, False), ({"state": {"page": "home"}}, False),
    ({"state": {"page": "question"}}, True), ({"state": {"page": "word_question"}}, True),
    ({"state": {"page": "ink_question"}}, True), ({"state": {"page": "feedback"}}, True),
])
def test_an_answer_was_started_when_the_head_was_saved_on_a_question_or_result_page(prepared, started):
    assert model.answer_started(prepared) is started


def test_a_head_without_a_saved_state_cannot_say_whether_it_was_started():
    with pytest.raises(KeyError):
        model.answer_started({})


# --- the wording and the models -------------------------------------------------------------------------------------

def test_the_word_end_model():
    assert display.word_done_model(5, 3) == WordDoneModel(
        page="session_done", title="本组单词完成", result="完成 5 词 · 答对 3 词",
        saved="拼写记录已保存；符合条件的假名复习时间已调整。", again=button("再练一组单词", Kind.WORD_SETUP), home=HOME)


@pytest.mark.parametrize("items,extra,summary", [
    (4, "", "练习 4 个假名记忆项 · 完成 7 题\n答对 2 题"),
    (0, "", "完成 7 题\n答对 2 题"),
    (4, "\n跳过 1 题（保留原有进度）", "练习 4 个假名记忆项 · 完成 7 题\n答对 2 题\n跳过 1 题（保留原有进度）"),
])
def test_the_batch_end_model(items, extra, summary):
    assert display.batch_done_model(items, 7, 2, extra) == BatchDoneModel(
        page="session_done", title="本批完成", summary=summary, records=button("查看复习记录", Kind.RECORDS), home=HOME)


def test_the_counts_of_a_batch_come_from_the_summary_or_from_the_queue():
    first, second = object(), object()
    questions = [SimpleNamespace(content_id="ka"), SimpleNamespace(content_id="na"), SimpleNamespace(content_id="ka")]
    assert display.batch_counts(questions, None) == (2, 3, None)
    assert display.batch_counts(questions, "not a summary") == (2, 3, None)
    assert display.batch_counts([first, second], None) == (2, 2, None)
    summary = {"answered": 9, "content_count": 5, "skipped": 0}
    assert display.batch_counts(questions, summary) == (5, 9, summary)
    assert display.batch_counts([], None) == (0, 0, None)


def test_the_extra_lines_of_a_batch_tell_what_was_skipped_and_what_was_practised():
    asked = []

    def practised_line(value):
        asked.append(value)
        return f"另练 {value}"
    assert display.batch_extra(None, practised_line) == ""
    assert display.batch_extra({"skipped": 0}, practised_line) == ""
    assert display.batch_extra({"skipped": 2}, practised_line) == "\n跳过 2 题（保留原有进度）"
    assert asked == []
    assert display.batch_extra({"skipped": 2, "practised": 3}, practised_line) == "\n跳过 2 题（保留原有进度）\n另练 3"
    assert display.batch_extra({"skipped": 0, "practised": 3}, practised_line) == "\n另练 3"
    assert asked == [3, 3]


@pytest.mark.parametrize("word_lane,setup", [(True, Kind.WORD_SETUP), (False, Kind.PRACTICE_SETUP)])
def test_the_updated_content_model_and_the_paused_kinds_model(word_lane, setup):
    assert display.content_updated_model("session", "retired", word_lane=word_lane) == ContentUpdatedModel(
        page="session_wait", title="题目内容已更新", kept="原题和草稿已保存。更新后继续使用原有学习记录。",
        use_new=button("使用更新后的题目", Kind.USE_NEW_CONTENT, session_id="session", retired_id="retired"),
        setup=button("练习设置", setup), home=HOME)
    assert display.kinds_paused_model(word_lane=word_lane) == KindsPausedModel(
        page="session_wait", title="题型已暂停", kept="未完成的题目和草稿已保留。开启题型后可继续。",
        setup=button("练习设置", setup), home=HOME)


def test_the_prerequisite_model_names_each_kana_once_and_offers_each_rule():
    rule = {"id": "pronunciation:long", "title": "长音怎么读"}
    assert display.prerequisite_model(["か", "ナ", "か"], []) == PrerequisiteModel(
        page="dependency_wait", title="先完成前置假名", symbols="か · ナ", rules=(),
        kept="原题与进度已保留。请检查这些假名是否已暂停，或相应题型是否可用。",
        check=button("检查并继续", Kind.CHECK_PREREQUISITES), setup=button("练习设置", Kind.PRACTICE_HUB), home=HOME)
    with_rule = display.prerequisite_model([], [rule])
    assert (with_rule.title, with_rule.symbols, with_rule.kept) == (
        "先学所需规则", "", "原题与进度已保留。先看规则讲解，再继续练习。")
    assert with_rule.rules == (button("长音怎么读", Kind.LEARN_RULE, rule=rule),)
    with pytest.raises(KeyError):
        display.prerequisite_model([], [{"id": "no title"}])


NOW = datetime(2030, 1, 1, 8, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("seconds,checked,text", [
    (0, False, "已到期，可以继续复习。"), (-5, True, "已到期，可以继续复习。"),
    (0.2, False, "距离下一题还有 1 秒。"), (30, False, "距离下一题还有 30 秒。"),
    (60, False, "距离下一题还有 1 分 0 秒。"), (3600, False, "距离下一题还有 1 小时 0 分 0 秒。"),
    (3725, True, "已检查：尚未到期，还需 1 小时 2 分 5 秒。"), (59, True, "已检查：尚未到期，还需 59 秒。"),
])
def test_the_status_of_the_review_wait_keeps_seconds_visible(seconds, checked, text):
    assert display.wait_status(NOW + timedelta(seconds=seconds), NOW, checked) == text


def test_the_time_of_the_next_question_names_the_day_only_when_it_is_another_day():
    until = datetime(2030, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
    assert display.wait_due_text(until, date(2030, 1, 1)) == "09:00:00"
    assert display.wait_due_text(until, date(2029, 12, 31)) == "01月01日 09:00:00"


def test_the_review_wait_model():
    assert display.review_wait_model("状态", "09:00:00", 3) == ReviewWaitModel(
        page="session_wait", title="本轮先歇一会儿", status="状态", due="下一题可在 09:00:00 开始",
        saved="已保存 3 道后续复习题，到期后自动继续。也可以先回到今日。", check=button("检查并继续复习", Kind.CHECK_WAIT),
        home=HOME)


# --- the pages ------------------------------------------------------------------------------------------------------

def test_the_word_end_page(fake_ui):
    pressed, on_action = recorder()
    saved_row = fake_ui(text="note")
    page_model = display.word_done_model(5, 3)
    children = line_pages.word_done_page(page_model, saved_row=saved_row, on_action=on_action)
    assert drawn(children) == [("label", "完成 5 词 · 答对 3 词", {"size": 24, "bold": True}), saved_row,
                               ("button", "再练一组单词", {"primary": True}), ("button", "回到今日", {})]
    press_all(children)
    assert pressed == [(page_model.again.action, children[2]), (page_model.home.action, children[3])]


@pytest.mark.parametrize("rows", [0, 2])
def test_the_batch_end_page_puts_the_route_and_extra_practice_between_the_summary_and_the_records(fake_ui, rows):
    pressed, on_action = recorder()
    next_rows = [fake_ui(text=f"next {index}") for index in range(rows)]
    extra_rows = [fake_ui(text=f"extra {index}") for index in range(rows)]
    page_model = display.batch_done_model(4, 7, 2, "")
    children = line_pages.batch_done_page(page_model, next_rows=next_rows, extra_rows=extra_rows, on_action=on_action)
    assert drawn(children) == [("label", page_model.summary, {"size": 24, "bold": True}), *next_rows, *extra_rows,
                               ("button", "查看复习记录", {}), ("button", "回到今日", {})]
    press_all(children)
    assert pressed == [(page_model.records.action, children[-2]), (page_model.home.action, children[-1])]


def test_the_updated_content_page(fake_ui):
    pressed, on_action = recorder()
    kept_row = fake_ui(text="note")
    page_model = display.content_updated_model("session", "retired", word_lane=False)
    children = line_pages.content_updated_page(page_model, kept_row=kept_row, on_action=on_action)
    assert drawn(children) == [kept_row, ("button", "使用更新后的题目", {"primary": True}), ("button", "练习设置", {}),
                               ("button", "回到今日", {})]
    press_all(children)
    assert pressed == [(page_model.use_new.action, children[1]), (page_model.setup.action, children[2]),
                       (page_model.home.action, children[3])]
    assert pressed[0][0] == KanaAction(Kind.USE_NEW_CONTENT, session_id="session", retired_id="retired")


def test_the_paused_kinds_page(fake_ui):
    pressed, on_action = recorder()
    kept_row = fake_ui(text="note")
    page_model = display.kinds_paused_model(word_lane=True)
    children = line_pages.kinds_paused_page(page_model, kept_row=kept_row, on_action=on_action)
    assert drawn(children) == [kept_row, ("button", "练习设置", {"primary": True}), ("button", "回到今日", {})]
    press_all(children)
    assert pressed == [(KanaAction(Kind.WORD_SETUP), children[1]), (KanaAction(Kind.HOME), children[2])]


@pytest.mark.parametrize("rules", [[], [{"title": "长音怎么读"}, {"title": "促音怎么读"}]])
def test_the_prerequisite_page_offers_a_button_per_rule_under_the_kana(fake_ui, rules):
    pressed, on_action = recorder()
    symbols_row = fake_ui(text="か · ナ")
    page_model = display.prerequisite_model(["か", "ナ"], rules)
    children = line_pages.prerequisite_page(page_model, symbols_row=symbols_row, on_action=on_action)
    assert drawn(children) == [
        symbols_row, *(("button", rule["title"], {"primary": True, "multiline": True}) for rule in rules),
        ("label", page_model.kept, {"size": 15, "color": component_ui.MUTED}),
        ("button", "检查并继续", {"primary": True}), ("button", "练习设置", {}), ("button", "回到今日", {})]
    press_all(children)
    assert [action for action, _ in pressed] == [
        *(KanaAction(Kind.LEARN_RULE, rule=rule) for rule in rules), KanaAction(Kind.CHECK_PREREQUISITES),
        KanaAction(Kind.PRACTICE_HUB), KanaAction(Kind.HOME)]
    assert all(action.rule is rule for (action, _), rule in zip(pressed, rules))


def test_a_button_takes_whatever_else_a_press_hands_it(fake_ui):
    pressed, on_action = recorder()
    children = line_pages.kinds_paused_page(display.kinds_paused_model(word_lane=False), kept_row=fake_ui(),
                                            on_action=on_action)
    children[1].on_press("button", extra="ignored")
    children[2].on_press()
    assert pressed == [(KanaAction(Kind.PRACTICE_SETUP), "button"), (KanaAction(Kind.HOME), None)]


def test_the_line_of_kana_symbols_is_drawn_large_green_and_as_study_text():
    assert line_pages.SYMBOLS_STYLE == {"size": 26, "color": component_ui.GREEN, "text_role": "study"}


@pytest.mark.parametrize("rows", [0, 2])
def test_the_review_wait_page_hands_back_its_status_line_and_its_button(fake_ui, rows):
    pressed, on_action = recorder()
    saved_row = fake_ui(text="note")
    next_rows = [fake_ui(text=f"next {index}") for index in range(rows)]
    extra_rows = [fake_ui(text=f"extra {index}") for index in range(rows)]
    page_model = display.review_wait_model("距离下一题还有 30 秒。", "09:00:00", 3)
    page = line_pages.review_wait_page(page_model, saved_row=saved_row, next_rows=next_rows, extra_rows=extra_rows,
                                       on_action=on_action)
    assert drawn(page.children) == [
        ("label", "距离下一题还有 30 秒。", {"size": 20, "bold": True, "color": component_ui.GREEN}),
        ("label", "下一题可在 09:00:00 开始", {"size": 15, "color": component_ui.MUTED}), saved_row,
        ("button", "检查并继续复习", {"primary": True}), *next_rows, *extra_rows, ("button", "回到今日", {})]
    assert page.status is page.children[0] and page.check is page.children[3]
    press_all(page.children)
    assert pressed == [(KanaAction(Kind.CHECK_WAIT), page.check), (KanaAction(Kind.HOME), page.children[-1])]


# --- the contracts of the modules -----------------------------------------------------------------------------------

def _imports(path, package):
    """Absolute module names a source file imports, anywhere in the file."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package.split(".")
            base = base[:len(base) - (node.level - 1)] if node.level else []
            names.add(".".join([*base, node.module] if node.module else base))
    return names


def test_the_page_module_follows_the_shared_page_contract():
    assert_page_module_holds_no_app_store_or_service(UI_DIR / "line_pages.py")


def test_the_pure_modules_import_exactly_their_registered_modules():
    assert _imports(PACKAGE_DIR / "model.py", "kanamemory.kana_line") == {
        "__future__", "dataclasses", "enum", "typing"}
    assert _imports(PACKAGE_DIR / "display.py", "kanamemory.kana_line") == {
        "__future__", "dataclasses", "math", "kanamemory.next_step.display", "kanamemory.kana_line.model"}


def test_the_page_module_imports_exactly_the_controls_its_display_and_its_model():
    assert _imports(UI_DIR / "line_pages.py", "kanamemory.kana_line.ui") == {
        "__future__", "collections.abc", "typing", "kanamemory.ui_components", "kanamemory.kana_line.display",
        "kanamemory.kana_line.model"}
    assert _imports(UI_DIR / "__init__.py", "kanamemory.kana_line.ui") == set()
