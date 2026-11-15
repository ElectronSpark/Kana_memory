"""The course_line package without the app object (design 13, C4).

The vocabulary: frozen values with their fields and defaults, compared by
value, and the page actions. The wording and the display models of the six
pages the course line draws itself, by their literal texts (both variants of
the paused page, with and without the content library page, with and without a
due time, with and without taught dependencies). The pages: each drawn from a
hand-written model into fake controls, with the full ordered list of children,
every control's keyword arguments (a default label records none), where the
handed-in rows stand (with and without rows) and the action every button hands
on with the pressed control. The page modules follow the shared page contract;
the pure modules import exactly their registered modules and no Toga, page,
app or store module.
"""
import ast
import dataclasses
from pathlib import Path

import pytest

from kanamemory import ui_components as component_ui
from kanamemory.course_line import display, model
from kanamemory.course_line.display import (CourseButton, DoneModel, LibraryOffModel, MaterialBlockedModel,
                                            PausedModel, PrerequisiteModel, WaitModel, done_model,
                                            library_off_model, material_blocked_model, paused_model,
                                            prerequisite_model, wait_model)
from kanamemory.course_line.model import CourseAction, CourseActionKind as Kind
from kanamemory.course_line.ui import line_pages
from support.fake_ui import View
from support.ui_module_contract import assert_page_module_holds_no_app_store_or_service


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
        control.on_press(control)


MUTED = {"color": component_ui.MUTED}
HOME = CourseButton("回到今日", CourseAction(Kind.HOME))
CONTENT = {"id": "grammar:synthetic", "title": "合成语法", "instruction_scope": "scope:a"}


# --- the vocabulary ----------------------------------------------------------------------------------

def fields(cls):
    return [(field.name, field.default) for field in dataclasses.fields(cls)]


MISSING = dataclasses.MISSING
STEPS = (model.LibraryOff, model.MaterialBlocked, model.Batch, model.Paused, model.Done, model.Diagnostic,
         model.Wait, model.Prerequisite, model.Teach, model.Ask)


def test_the_steps_are_frozen_values_with_their_fields_and_defaults():
    assert fields(model.LibraryOff) == [("count", MISSING)]
    assert fields(model.MaterialBlocked) == [("question_ids", MISSING)]
    assert fields(model.Batch) == [] and fields(model.Done) == []
    assert fields(model.Paused) == [("reason", MISSING), ("text", MISSING)]
    assert fields(model.Diagnostic) == [("episode_id", MISSING)]
    assert fields(model.Wait) == [("due", None)]
    assert fields(model.Prerequisite) == [("content", MISSING), ("due", None), ("taught", ())]
    assert fields(model.Teach) == [("content", MISSING)]
    assert fields(model.Ask) == [("current", MISSING)]
    assert (model.PAUSED_NO_HEAD, model.PAUSED_DISABLED, model.PAUSED_QUIZ) == ("no_head", "disabled", "batch_quiz")
    assert set(model.CourseStep.__args__) == set(STEPS)
    for cls in (*STEPS, CourseAction):
        assert cls.__dataclass_params__.frozen, cls
    with pytest.raises(dataclasses.FrozenInstanceError):
        model.Wait().due = "2026-01-01T00:00:00+00:00"


def test_steps_compare_by_value():
    assert model.LibraryOff(2) == model.LibraryOff(2) != model.LibraryOff(0)
    assert model.MaterialBlocked(["q:1"]) == model.MaterialBlocked(["q:1"]) != model.MaterialBlocked(["q:2"])
    assert model.Batch() == model.Batch() and model.Done() == model.Done()
    assert model.Paused(model.PAUSED_NO_HEAD, "甲") != model.Paused(model.PAUSED_DISABLED, "甲")
    assert model.Diagnostic("episode:1") == model.Diagnostic("episode:1")
    assert model.Wait() == model.Wait(None) != model.Wait("2026-01-01T00:00:00+00:00")
    assert model.Prerequisite(CONTENT) == model.Prerequisite(dict(CONTENT), None, ())
    assert model.Prerequisite(CONTENT, taught=("甲",)) != model.Prerequisite(CONTENT)
    assert model.Teach(CONTENT) == model.Teach(dict(CONTENT))
    assert model.Ask({"type": "question"}) == model.Ask({"type": "question"})


def test_the_page_actions():
    assert [kind.name for kind in Kind] == ["CHECK_WAIT", "NEW_ROUND", "ACKNOWLEDGE_PREREQUISITES",
                                            "CONTENT_LIBRARIES", "LEARNING_SETTINGS", "DEVICE_SETTINGS", "HOME"]
    assert fields(CourseAction) == [("kind", MISSING), ("content_id", None), ("instruction_scope", None)]
    assert CourseAction(Kind.HOME) == CourseAction(Kind.HOME, None, None) != CourseAction(Kind.NEW_ROUND)
    assert (CourseAction(Kind.ACKNOWLEDGE_PREREQUISITES, "c:1", "s")
            != CourseAction(Kind.ACKNOWLEDGE_PREREQUISITES, "c:1"))


# --- the wording and the display models ----------------------------------------------------------------

def test_every_fixed_text_of_the_line_pages():
    assert (display.WAIT_PAGE, display.DONE_PAGE) == ("learning_wait", "learning_done")
    assert (display.HOME, display.NEW_ROUND) == ("回到今日", "开始新一轮")
    assert display.LIBRARY_OFF_TITLE == "所属内容库已停用"
    assert display.LIBRARY_OFF_TEXT == "剩下的 {count} 项课程所属的内容库已停用、缺失或已被取代，暂不出题。"
    assert display.LIBRARY_OFF_KEPT == "学习记录、草稿和复习时间均已保留；重新启用该库后即可继续。"
    assert display.CONTENT_LIBRARIES == "查看内容库"
    assert display.MATERIAL_TITLE == "材料待核"
    assert display.MATERIAL_TEXT == "相关说明已更新，部分旧题仍需核对。"
    assert display.MATERIAL_KEPT == "原题、草稿和复习时间均已保留。"
    assert display.VOICE_TITLE == "需要日语语音"
    assert display.VOICE_TEXT == "听词选词需要设备上的日语语音，安装后即可继续。"
    assert display.VOICE_NOTE == "未完成题目与复习时间已保留；也可在题型设置中关闭听词选词。"
    assert display.DEVICE_SETTINGS_CAPTION == "语音与手写"
    assert display.KINDS_TITLE == "题型已关闭"
    assert display.KIND_SETTINGS == "题型设置"
    assert display.PAUSED_NO_HEAD_TEXT == "未完成题目已保留，重新开启对应题型后可继续。"
    assert display.PAUSED_DISABLED_TEXT == "未完成题目与复习时间已保留，开启对应题型后可继续。"
    assert display.DONE_TITLE == "课程学习"
    assert display.DONE_TEXT == "本次学习已保存。"
    assert display.WAIT_TITLE == "稍后继续"
    assert display.WAIT_NO_DUE == "当前题型没有可答题目。"
    assert display.WAIT_NOTE == "到期后自动继续，也可以先离开。"
    assert display.CHECK == "检查并继续"
    assert display.PREREQ_TITLE == "先巩固前置知识"
    assert display.PREREQ_TEXT == "先接着巩固前置内容，完成后自动回到本项。"
    assert display.PREREQ_UNAVAILABLE == "前置内容的练习暂不可用，请检查题型或暂停设置。"
    assert display.LEARNING_SETTINGS_CAPTION == "学习设置"
    assert display.TAUGHT_PREFIX == "已学过、可以接着往后学的前置："
    assert display.ACKNOWLEDGE == "我已了解前置，继续本课"


def test_the_shared_names_are_the_continue_actions_own():
    from kanamemory.next_step import display as end_display
    assert (display.WAIT_PAGE, display.DONE_PAGE, display.HOME, display.NEW_ROUND) == (
        end_display.WAIT_PAGE, end_display.DONE_PAGE, end_display.HOME, end_display.NEW_ROUND)


def test_the_paused_texts_are_the_batch_quizs_and_the_lines_own():
    from kanamemory.intro_batch_ui import QUIZ_PAUSED
    assert display.PAUSED_NO_HEAD_TEXT == QUIZ_PAUSED


def test_the_library_off_model_with_and_without_the_library_page():
    assert library_off_model(3, libraries=True) == LibraryOffModel(
        page="learning_wait", title="所属内容库已停用",
        text="剩下的 3 项课程所属的内容库已停用、缺失或已被取代，暂不出题。",
        kept="学习记录、草稿和复习时间均已保留；重新启用该库后即可继续。",
        libraries=CourseButton("查看内容库", CourseAction(Kind.CONTENT_LIBRARIES)), home=HOME)
    without = library_off_model(0, libraries=False)
    assert without.libraries is None
    assert without.text == "剩下的 0 项课程所属的内容库已停用、缺失或已被取代，暂不出题。"
    count = 7
    assert library_off_model(count, libraries=False).text == f'剩下的 {count} 项课程所属的内容库已停用、缺失或已被取代，暂不出题。'


def test_the_material_and_done_models():
    assert material_blocked_model() == MaterialBlockedModel(
        page="learning_wait", title="材料待核", text="相关说明已更新，部分旧题仍需核对。",
        kept="原题、草稿和复习时间均已保留。", home=HOME)
    assert done_model() == DoneModel(page="learning_done", title="课程学习", text="本次学习已保存。",
                                     new_round=CourseButton("开始新一轮", CourseAction(Kind.NEW_ROUND)), home=HOME)


def test_the_paused_model_in_both_variants():
    kinds = CourseButton("题型设置", CourseAction(Kind.LEARNING_SETTINGS))
    assert paused_model("甲", voice=True) == PausedModel(
        page="learning_wait", title="需要日语语音", text="听词选词需要设备上的日语语音，安装后即可继续。",
        note="未完成题目与复习时间已保留；也可在题型设置中关闭听词选词。",
        buttons=(CourseButton("语音与手写", CourseAction(Kind.DEVICE_SETTINGS)), kinds), home=HOME)
    assert paused_model("甲", voice=False) == PausedModel(
        page="learning_wait", title="题型已关闭", text="甲", note=None, buttons=(kinds,), home=HOME)


def test_the_wait_model_with_and_without_a_due_time():
    check = CourseButton("检查并继续", CourseAction(Kind.CHECK_WAIT))
    settings = CourseButton("题型设置", CourseAction(Kind.LEARNING_SETTINGS))
    assert wait_model("下次复习：01月02日 03:04:05") == WaitModel(
        page="learning_wait", title="稍后继续", when="下次复习：01月02日 03:04:05",
        note="到期后自动继续，也可以先离开。", check=check, settings=settings, home=HOME)
    assert wait_model(None).when == "当前题型没有可答题目。"


def test_the_prerequisite_model_with_a_due_time_and_taught_dependencies():
    page = prerequisite_model(CONTENT, "下次复习：01月02日 03:04:05", ("甲", "乙"))
    assert page == PrerequisiteModel(
        page="learning_wait", title="先巩固前置知识", item_title="合成语法",
        text="先接着巩固前置内容，完成后自动回到本项。", due_label="下次复习：01月02日 03:04:05",
        due_note="到期后自动继续，也可以先离开。", unavailable=None, settings=None,
        taught_line="已学过、可以接着往后学的前置：甲、乙",
        check=CourseButton("检查并继续", CourseAction(Kind.CHECK_WAIT)),
        acknowledge=CourseButton("我已了解前置，继续本课",
                                 CourseAction(Kind.ACKNOWLEDGE_PREREQUISITES, "grammar:synthetic", "scope:a")),
        home=HOME)


def test_the_prerequisite_model_without_a_due_time_or_taught_dependencies():
    content = {"id": "word:synthetic", "title": "合成词"}
    page = prerequisite_model(content, None, ())
    assert (page.due_label, page.due_note, page.taught_line) == (None, None, None)
    assert page.unavailable == "前置内容的练习暂不可用，请检查题型或暂停设置。"
    assert page.settings == CourseButton("学习设置", CourseAction(Kind.LEARNING_SETTINGS))
    # The action carries the id and the scope of the drawing (no scope: None).
    assert page.acknowledge.action == CourseAction(Kind.ACKNOWLEDGE_PREREQUISITES, "word:synthetic", None)


def test_the_acknowledge_action_keeps_the_values_of_the_drawing():
    content = dict(CONTENT)
    page = prerequisite_model(content, None, ())
    content["id"], content["instruction_scope"] = "other", "other"
    assert page.acknowledge.action == CourseAction(Kind.ACKNOWLEDGE_PREREQUISITES, "grammar:synthetic", "scope:a")


def test_the_display_models_are_frozen():
    for value in (library_off_model(1, libraries=True), material_blocked_model(), paused_model("甲", voice=False),
                  done_model(), wait_model(None), prerequisite_model(CONTENT, None, ()), HOME):
        assert type(value).__dataclass_params__.frozen


# --- the pages -----------------------------------------------------------------------------------------

def test_the_library_off_page(fake_ui):
    rows = [View(text="下一内容")]
    pressed, on_action = recorder()
    children = line_pages.library_off_page(library_off_model(2, libraries=True), next_content_rows=rows,
                                           on_action=on_action)
    assert drawn(children) == [("label", "剩下的 2 项课程所属的内容库已停用、缺失或已被取代，暂不出题。", {}),
                               ("label", "学习记录、草稿和复习时间均已保留；重新启用该库后即可继续。", MUTED),
                               ("button", "查看内容库", {}),
                               *rows,
                               ("button", "回到今日", {})]
    press_all((children[2], children[4]))
    assert pressed == [(CourseAction(Kind.CONTENT_LIBRARIES), children[2]), (CourseAction(Kind.HOME), children[4])]


def test_the_library_off_page_without_the_library_page_or_rows(fake_ui):
    children = line_pages.library_off_page(library_off_model(0, libraries=False), next_content_rows=[],
                                           on_action=lambda action, widget: None)
    assert [child.text for child in children] == [
        "剩下的 0 项课程所属的内容库已停用、缺失或已被取代，暂不出题。",
        "学习记录、草稿和复习时间均已保留；重新启用该库后即可继续。", "回到今日"]


def test_the_material_blocked_page(fake_ui):
    replacement, rows = [View(text="使用更新后的练习继续")], [View(text="下一内容"), View(text="添加内容")]
    pressed, on_action = recorder()
    children = line_pages.material_blocked_page(material_blocked_model(), replacement_rows=replacement,
                                                next_content_rows=rows, on_action=on_action)
    assert drawn(children) == [("label", "相关说明已更新，部分旧题仍需核对。", {}),
                               ("label", "原题、草稿和复习时间均已保留。", MUTED),
                               *replacement, *rows,
                               ("button", "回到今日", {})]
    press_all(children[-1:])
    assert pressed == [(CourseAction(Kind.HOME), children[-1])]
    bare = line_pages.material_blocked_page(material_blocked_model(), replacement_rows=[], next_content_rows=[],
                                            on_action=on_action)
    assert [child.text for child in bare] == ["相关说明已更新，部分旧题仍需核对。", "原题、草稿和复习时间均已保留。",
                                              "回到今日"]


def test_the_paused_page_asking_for_the_voice(fake_ui):
    rows = [View(text="下一内容")]
    pressed, on_action = recorder()
    children = line_pages.paused_page(paused_model("甲", voice=True), next_content_rows=rows, on_action=on_action)
    assert drawn(children) == [("label", "听词选词需要设备上的日语语音，安装后即可继续。", {}),
                               ("label", "未完成题目与复习时间已保留；也可在题型设置中关闭听词选词。", MUTED),
                               ("button", "语音与手写", {"primary": True}),
                               ("button", "题型设置", {}),
                               *rows,
                               ("button", "回到今日", {})]
    press_all((children[2], children[3], children[5]))
    assert pressed == [(CourseAction(Kind.DEVICE_SETTINGS), children[2]),
                       (CourseAction(Kind.LEARNING_SETTINGS), children[3]),
                       (CourseAction(Kind.HOME), children[5])]


def test_the_paused_page_naming_the_closed_kinds(fake_ui):
    pressed, on_action = recorder()
    children = line_pages.paused_page(paused_model("未完成题目已保留，重新开启对应题型后可继续。", voice=False),
                                      next_content_rows=[], on_action=on_action)
    assert drawn(children) == [("label", "未完成题目已保留，重新开启对应题型后可继续。", {}),
                               ("button", "题型设置", {"primary": True}),
                               ("button", "回到今日", {})]
    press_all(children[1:])
    assert pressed == [(CourseAction(Kind.LEARNING_SETTINGS), children[1]), (CourseAction(Kind.HOME), children[2])]


def test_the_done_page(fake_ui):
    rows = [View(text="下一内容")]
    pressed, on_action = recorder()
    children = line_pages.done_page(done_model(), next_content_rows=rows, on_action=on_action)
    assert drawn(children) == [("label", "本次学习已保存。", {"size": 20}),
                               ("button", "开始新一轮", {"primary": True}),
                               *rows,
                               ("button", "回到今日", {})]
    press_all((children[1], children[3]))
    assert pressed == [(CourseAction(Kind.NEW_ROUND), children[1]), (CourseAction(Kind.HOME), children[3])]


def test_the_wait_page(fake_ui):
    rows, extra = [View(text="下一内容")], (View(text="继续加练"),)
    pressed, on_action = recorder()
    children = line_pages.wait_page(wait_model(None), next_content_rows=rows, extra_rows=extra, on_action=on_action)
    assert drawn(children) == [("label", "当前题型没有可答题目。", {"size": 20}),
                               ("label", "到期后自动继续，也可以先离开。", MUTED),
                               ("button", "检查并继续", {"primary": True}),
                               *rows, *extra,
                               ("button", "题型设置", {}),
                               ("button", "回到今日", {})]
    press_all((children[2], children[5], children[6]))
    assert pressed == [(CourseAction(Kind.CHECK_WAIT), children[2]),
                       (CourseAction(Kind.LEARNING_SETTINGS), children[5]),
                       (CourseAction(Kind.HOME), children[6])]


def test_the_wait_page_without_handed_in_rows(fake_ui):
    children = line_pages.wait_page(wait_model("下次复习：01月02日 03:04:05"), next_content_rows=[], extra_rows=[],
                                    on_action=lambda action, widget: None)
    assert [child.text for child in children] == ["下次复习：01月02日 03:04:05", "到期后自动继续，也可以先离开。",
                                                  "检查并继续", "题型设置", "回到今日"]


def test_the_prerequisite_page_with_a_due_time_and_taught_dependencies(fake_ui):
    rows = [View(text="下一内容")]
    pressed, on_action = recorder()
    page = prerequisite_model(CONTENT, "下次复习：01月02日 03:04:05", ("甲",))
    children = line_pages.prerequisite_page(page, next_content_rows=rows, on_action=on_action)
    assert drawn(children) == [("label", "合成语法", {"size": 21, "bold": True}),
                               ("label", "先接着巩固前置内容，完成后自动回到本项。", {"size": 16}),
                               ("label", "下次复习：01月02日 03:04:05", {"size": 14}),
                               ("label", "到期后自动继续，也可以先离开。", MUTED),
                               ("label", "已学过、可以接着往后学的前置：甲", {"size": 14, **MUTED}),
                               ("button", "检查并继续", {"primary": True}),
                               ("button", "我已了解前置，继续本课", {}),
                               *rows,
                               ("button", "回到今日", {})]
    press_all((children[5], children[6], children[8]))
    assert pressed == [(CourseAction(Kind.CHECK_WAIT), children[5]),
                       (CourseAction(Kind.ACKNOWLEDGE_PREREQUISITES, "grammar:synthetic", "scope:a"), children[6]),
                       (CourseAction(Kind.HOME), children[8])]


def test_the_prerequisite_page_without_a_due_time(fake_ui):
    pressed, on_action = recorder()
    children = line_pages.prerequisite_page(prerequisite_model(CONTENT, None, ()), next_content_rows=[],
                                            on_action=on_action)
    assert drawn(children) == [("label", "合成语法", {"size": 21, "bold": True}),
                               ("label", "先接着巩固前置内容，完成后自动回到本项。", {"size": 16}),
                               ("label", "前置内容的练习暂不可用，请检查题型或暂停设置。", MUTED),
                               ("button", "学习设置", {}),
                               ("button", "检查并继续", {"primary": True}),
                               ("button", "我已了解前置，继续本课", {}),
                               ("button", "回到今日", {})]
    press_all(children[3:4])
    assert pressed == [(CourseAction(Kind.LEARNING_SETTINGS), children[3])]


def test_the_pages_take_their_captions_and_actions_from_the_model(fake_ui):
    """A hand-made model with other captions and actions: the page draws and hands on what the model carries."""
    custom = CourseButton("甲", CourseAction(Kind.ACKNOWLEDGE_PREREQUISITES, "c:x", "s:y"))
    page = dataclasses.replace(done_model(), text="说明", new_round=custom)
    pressed, on_action = recorder()
    children = line_pages.done_page(page, next_content_rows=[], on_action=on_action)
    assert [child.text for child in children] == ["说明", "甲", "回到今日"]
    children[1].on_press(children[1])
    assert pressed == [(custom.action, children[1])]


def test_the_page_functions_take_only_a_model_rows_and_one_callback():
    tree = ast.parse(Path(line_pages.__file__).read_text("utf-8"))
    signatures = {node.name: ast.unparse(node.args) for node in tree.body
                  if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}
    assert signatures == {
        "library_off_page": "model: LibraryOffModel, *, next_content_rows: Sequence, on_action: OnAction",
        "material_blocked_page": "model: MaterialBlockedModel, *, replacement_rows: Sequence, "
                                 "next_content_rows: Sequence, on_action: OnAction",
        "paused_page": "model: PausedModel, *, next_content_rows: Sequence, on_action: OnAction",
        "done_page": "model: DoneModel, *, next_content_rows: Sequence, on_action: OnAction",
        "wait_page": "model: WaitModel, *, next_content_rows: Sequence, extra_rows: Sequence, on_action: OnAction",
        "prerequisite_page": "model: PrerequisiteModel, *, next_content_rows: Sequence, on_action: OnAction"}


# --- the contract and the imports ----------------------------------------------------------------------

UI_DIR = Path(line_pages.__file__).parent
PACKAGE_DIR = UI_DIR.parent


@pytest.mark.parametrize("name", sorted(path.name for path in UI_DIR.glob("*.py")))
def test_the_line_page_modules_hold_no_app_store_or_service(name):
    assert_page_module_holds_no_app_store_or_service(UI_DIR / name)


def _imports(path, package):
    """Absolute module names a source file imports, anywhere in the file."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package.split(".")[:len(package.split(".")) - node.level + 1] if node.level else []
            names.add(".".join([*base, node.module] if node.module else base))
    return names


FORBIDDEN = ("toga", "travertino", "kanamemory.app", "kanamemory.ui_components", "kanamemory.course_line.ui",
             "kanamemory.next_step.ui")
PURE_REACH = ("kanamemory.next_step.display", "kanamemory.course_line.model")


def _within(module, names):
    return any(module == name or module.startswith(name + ".") for name in names)


@pytest.mark.parametrize("name", ["__init__.py", "model.py", "display.py"])
def test_the_pure_modules_import_no_toga_no_page_no_app_and_no_store_module(name):
    found = _imports(PACKAGE_DIR / name, "kanamemory.course_line")
    assert not [module for module in found if _within(module, FORBIDDEN) or module.endswith("_ui")]
    assert not [module for module in found if module.startswith("kanamemory.") and not _within(module, PURE_REACH)]


def test_the_pure_modules_import_exactly_their_registered_modules():
    assert _imports(PACKAGE_DIR / "__init__.py", "kanamemory.course_line") == set()
    assert _imports(PACKAGE_DIR / "model.py", "kanamemory.course_line") == {
        "__future__", "dataclasses", "enum", "typing"}
    assert _imports(PACKAGE_DIR / "display.py", "kanamemory.course_line") == {
        "__future__", "dataclasses", "kanamemory.next_step.display", "kanamemory.course_line.model"}


def test_the_page_module_imports_exactly_the_controls_its_display_and_its_model():
    assert _imports(UI_DIR / "line_pages.py", "kanamemory.course_line.ui") == {
        "__future__", "collections.abc", "kanamemory.ui_components", "kanamemory.course_line.display",
        "kanamemory.course_line.model"}
    assert _imports(UI_DIR / "__init__.py", "kanamemory.course_line.ui") == set()
