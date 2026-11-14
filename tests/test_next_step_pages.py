"""The next_step package without the app object (design 13 S4, acceptance A3 and C4).

The vocabulary: frozen values with their fields and defaults. The wording and
the display models of the three end pages, by their literal texts. The pages:
each drawn from a hand-written model into fake controls, with the full ordered
list of children, every control's keyword arguments, where the handed-in rows
stand, the action every button hands on (with the pressed control) and B10's
note rows with and without a note. The page modules follow the shared page
contract; the two pure modules import no Toga, page, app or store module, and
the page module imports exactly its model, its display, the controls and the
note rows.
"""
import ast
import dataclasses
from pathlib import Path

import pytest

from kanamemory import ui_components as component_ui
from kanamemory.arrangement.display import NO_NEW_TEXT, SectionNote
from kanamemory.next_step import display, model
from kanamemory.next_step.display import (EndButton, NoKanaKindModel, RoundEndModel, StudyEndModel,
                                          no_kana_kind_model, round_end_model, study_end_model)
from kanamemory.next_step.model import EndAction, EndActionKind as Kind
from kanamemory.next_step.ui import end_pages
from support.fake_ui import View, descendants
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


NOTE = SectionNote("section:next", "下一节", route_id="route:side")


# --- the vocabulary ----------------------------------------------------------------------------------

def fields(cls):
    return [(field.name, field.default) for field in dataclasses.fields(cls)]


MISSING = dataclasses.MISSING


def test_the_steps_are_frozen_values_with_their_fields_and_defaults():
    assert fields(model.CourseItem) == [("selected_current", None)]
    assert fields(model.KanaItem) == [("first_teaching_targets", None), ("prepared", None), ("preserve_audio", False)]
    assert fields(model.ExtraOr) == [("fallback", MISSING)]
    assert fields(model.RoundEnd) == [] and fields(model.NoKanaKind) == []
    assert fields(model.Reference) == [("entry_id", MISSING)]
    assert fields(model.RouteEnd) == [("service", MISSING), ("path", MISSING)]
    assert fields(model.WaitKanaPlan) == [("loop", MISSING), ("options", MISSING), ("attempt", MISSING)]
    assert fields(model.WaitCourseHead) == [("loop", MISSING), ("snapshot", MISSING), ("pending", MISSING),
                                            ("now", MISSING), ("prepared_kana", MISSING)]
    assert fields(model.Stay) == [("reason", MISSING)]
    assert fields(model.Failed) == [("error", MISSING), ("title", None)]
    assert (model.STAY_CHECKPOINT, model.STAY_LANE, model.STAY_KINDS) == ("checkpoint", "lane", "kinds")
    steps = (model.CourseItem, model.KanaItem, model.ExtraOr, model.RoundEnd, model.Reference, model.NoKanaKind,
             model.RouteEnd, model.WaitKanaPlan, model.WaitCourseHead, model.Stay, model.Failed)
    assert set(model.NextStep.__args__) == set(steps)
    for cls in (*steps, EndAction):
        assert cls.__dataclass_params__.frozen, cls
    with pytest.raises(dataclasses.FrozenInstanceError):
        model.KanaItem().prepared = object()


def test_steps_compare_by_value():
    head = {"id": "course:1"}
    assert model.CourseItem() == model.CourseItem(None) != model.CourseItem(head)
    assert model.CourseItem(head) == model.CourseItem({"id": "course:1"})
    assert model.KanaItem(preserve_audio=True) != model.KanaItem()
    assert model.ExtraOr(model.RoundEnd()) == model.ExtraOr(model.RoundEnd()) != model.ExtraOr(model.NoKanaKind())
    assert model.Reference("kana:a") == model.Reference("kana:a") != model.Reference("kana:i")
    error = ValueError("x")
    assert model.Failed(error) == model.Failed(error, None) != model.Failed(error, "课程")
    assert model.Stay(model.STAY_LANE) != model.Stay(model.STAY_KINDS)


def test_the_end_actions():
    assert [kind.name for kind in Kind] == ["NEW_ROUND", "ADD_SECTIONS", "PRACTICE_HUB", "SETTINGS", "ROUTE_PAGE",
                                            "HOME"]
    assert fields(EndAction) == [("kind", MISSING), ("section_id", None), ("route_id", None)]
    assert EndAction(Kind.HOME) == EndAction(Kind.HOME, None, None) != EndAction(Kind.NEW_ROUND)
    assert EndAction(Kind.ADD_SECTIONS, "section:a", "route:b") != EndAction(Kind.ADD_SECTIONS, "section:a")


# --- the wording and the display models ----------------------------------------------------------------

def test_every_fixed_text_of_the_end_pages():
    assert (display.DONE_PAGE, display.WAIT_PAGE) == ("learning_done", "learning_wait")
    assert display.ROUND_END_TITLE == "本轮已保存"
    assert display.ROUND_END_TEXT == "作答记录和复习安排已保存。"
    assert display.NEW_ROUND == "开始新一轮"
    assert display.NO_KIND_TITLE == "暂时没有可进行的假名题型"
    assert display.NO_KIND_TEXT == "已加入的小节仍保留。请检查题型设置、语音或手写模型，以及内容是否已暂停。"
    assert display.PRACTICE_HUB == "题型与练习设置"
    assert display.SETTINGS == "语音与手写模型"
    assert display.ROUTE_PAGE == "学习路线"
    assert display.STUDY_END_TITLE == "本次学习已保存"
    assert display.STUDY_END_TEXT == "可以继续下一小节，或稍后按复习安排回来。"
    assert display.HOME == "回到今日"


def test_the_display_models():
    home = EndButton("回到今日", EndAction(Kind.HOME))
    hub = EndButton("题型与练习设置", EndAction(Kind.PRACTICE_HUB))
    assert round_end_model() == RoundEndModel(
        page="learning_done", title="本轮已保存", text="作答记录和复习安排已保存。",
        new_round=EndButton("开始新一轮", EndAction(Kind.NEW_ROUND)), home=home)
    assert no_kana_kind_model() == NoKanaKindModel(
        page="learning_wait", title="暂时没有可进行的假名题型",
        text="已加入的小节仍保留。请检查题型设置、语音或手写模型，以及内容是否已暂停。", practice_hub=hub,
        settings=EndButton("语音与手写模型", EndAction(Kind.SETTINGS)),
        route_page=EndButton("学习路线", EndAction(Kind.ROUTE_PAGE)), home=home)
    assert study_end_model(None, "route:side") == StudyEndModel(
        page="learning_done", title="本次学习已保存", note=None, note_add=None,
        text="可以继续下一小节，或稍后按复习安排回来。", practice_hub=hub, home=home)
    with_note = study_end_model(NOTE, "route:other")
    assert (with_note.note, with_note.note_add) == (NOTE, EndAction(Kind.ADD_SECTIONS, "section:next", "route:other"))
    assert study_end_model(NOTE, None).note_add == EndAction(Kind.ADD_SECTIONS, "section:next", None)
    for value in (round_end_model(), no_kana_kind_model(), with_note, home):
        assert type(value).__dataclass_params__.frozen


# --- the pages -----------------------------------------------------------------------------------------

def test_the_round_end_page(fake_ui):
    page = round_end_model()
    content, extra = View(text="下一内容"), View(text="继续加练")
    pressed, on_action = recorder()
    children = end_pages.round_end_page(page, next_content_rows=[content], extra_rows=(extra,), on_action=on_action)
    assert drawn(children) == [("label", "作答记录和复习安排已保存。", {"size": 17}),
                               ("button", "开始新一轮", {"primary": True}),
                               content, extra,
                               ("button", "回到今日", {})]
    for control in (children[1], children[4]):
        control.on_press(control)
    assert pressed == [(EndAction(Kind.NEW_ROUND), children[1]), (EndAction(Kind.HOME), children[4])]


def test_the_round_end_page_without_handed_in_rows(fake_ui):
    children = end_pages.round_end_page(round_end_model(), next_content_rows=[], extra_rows=[],
                                        on_action=lambda action, widget: None)
    assert [child.text for child in children] == ["作答记录和复习安排已保存。", "开始新一轮", "回到今日"]


def test_the_no_kana_kind_page(fake_ui):
    pressed, on_action = recorder()
    children = end_pages.no_kana_kind_page(no_kana_kind_model(), on_action=on_action)
    assert drawn(children) == [
        ("label", "已加入的小节仍保留。请检查题型设置、语音或手写模型，以及内容是否已暂停。", {"size": 17}),
        ("button", "题型与练习设置", {"primary": True}),
        ("button", "语音与手写模型", {}),
        ("button", "学习路线", {}),
        ("button", "回到今日", {})]
    for control in children[1:]:
        control.on_press(control)
    assert pressed == [(EndAction(kind), control) for kind, control in
                       zip((Kind.PRACTICE_HUB, Kind.SETTINGS, Kind.ROUTE_PAGE, Kind.HOME), children[1:])]


def test_the_study_end_page_without_a_note(fake_ui):
    rows, extra = [View(text="下一小节"), View(text="其他路线")], [View(text="继续加练")]
    pressed, on_action = recorder()
    children = end_pages.study_end_page(study_end_model(None, None), next_rows=rows, extra_rows=extra,
                                        on_action=on_action)
    assert drawn(children) == [("label", "可以继续下一小节，或稍后按复习安排回来。", {"size": 17}),
                               *rows, *extra,
                               ("button", "题型与练习设置", {}),   # not primary on this page
                               ("button", "回到今日", {})]
    for control in children[-2:]:
        control.on_press(control)
    assert pressed == [(EndAction(Kind.PRACTICE_HUB), children[-2]), (EndAction(Kind.HOME), children[-1])]


def test_the_study_end_page_with_a_note_draws_the_note_rows_first(fake_ui):
    page = study_end_model(NOTE, "route:other")
    rows = [View(text="下一小节")]
    pressed, on_action = recorder()
    children = end_pages.study_end_page(page, next_rows=rows, extra_rows=[], on_action=on_action)
    assert drawn(children) == [("label", NO_NEW_TEXT, {"size": 15, "bold": True, "margin_bottom": 4}),
                               ("button", "加入下一小节 · 下一节", {"multiline": True}),
                               ("label", "可以继续下一小节，或稍后按复习安排回来。", {"size": 17}),
                               *rows,
                               ("button", "题型与练习设置", {}),
                               ("button", "回到今日", {})]
    note_button = children[1]
    note_button.on_press(note_button)
    # The note row's callback takes no control; the action carries the section and the route of the drawing.
    assert pressed == [(EndAction(Kind.ADD_SECTIONS, "section:next", "route:other"), None)]


def test_the_pages_take_their_captions_and_actions_from_the_model(fake_ui):
    """A hand-made model with other captions and actions: the page draws and hands on what the model carries."""
    custom = EndButton("甲", EndAction(Kind.ADD_SECTIONS, "section:x", "route:y"))
    page = dataclasses.replace(no_kana_kind_model(), text="说明", settings=custom)
    pressed, on_action = recorder()
    children = end_pages.no_kana_kind_page(page, on_action=on_action)
    assert [child.text for child in children] == ["说明", "题型与练习设置", "甲", "学习路线", "回到今日"]
    children[2].on_press(children[2])
    assert pressed == [(custom.action, children[2])]


def test_the_page_functions_take_only_a_model_rows_and_one_callback():
    tree = ast.parse(Path(end_pages.__file__).read_text("utf-8"))
    signatures = {node.name: ast.unparse(node.args) for node in tree.body
                  if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}
    assert signatures == {
        "round_end_page": "model: RoundEndModel, *, next_content_rows: Sequence, extra_rows: Sequence, "
                          "on_action: OnAction",
        "no_kana_kind_page": "model: NoKanaKindModel, *, on_action: OnAction",
        "study_end_page": "model: StudyEndModel, *, next_rows: Sequence, extra_rows: Sequence, on_action: OnAction"}


# --- the contract and the imports ----------------------------------------------------------------------

UI_DIR = Path(end_pages.__file__).parent
PACKAGE_DIR = UI_DIR.parent


@pytest.mark.parametrize("name", sorted(path.name for path in UI_DIR.glob("*.py")))
def test_the_end_page_modules_hold_no_app_store_or_service(name):
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


FORBIDDEN = ("toga", "travertino", "kanamemory.app", "kanamemory.ui_components", "kanamemory.next_step.ui")
PURE_REACH = ("kanamemory.arrangement.display", "kanamemory.next_step.model")


def _within(module, names):
    return any(module == name or module.startswith(name + ".") for name in names)


@pytest.mark.parametrize("name", ["__init__.py", "model.py", "display.py"])
def test_the_pure_modules_import_no_toga_no_page_no_app_and_no_store_module(name):
    found = _imports(PACKAGE_DIR / name, "kanamemory.next_step")
    assert not [module for module in found if _within(module, FORBIDDEN) or module.endswith("_ui")]
    assert not [module for module in found if module.startswith("kanamemory.") and not _within(module, PURE_REACH)]


def test_the_pure_modules_import_exactly_their_standard_modules():
    assert _imports(PACKAGE_DIR / "__init__.py", "kanamemory.next_step") == set()
    assert _imports(PACKAGE_DIR / "model.py", "kanamemory.next_step") == {
        "__future__", "dataclasses", "datetime", "enum", "typing"}
    assert _imports(PACKAGE_DIR / "display.py", "kanamemory.next_step") == {
        "__future__", "dataclasses", "kanamemory.arrangement.display", "kanamemory.next_step.model"}


def test_the_page_module_imports_exactly_the_controls_the_note_rows_its_display_and_its_model():
    assert _imports(UI_DIR / "end_pages.py", "kanamemory.next_step.ui") == {
        "__future__", "collections.abc", "kanamemory.ui_components", "kanamemory.arrangement.ui.section_note",
        "kanamemory.next_step.display", "kanamemory.next_step.model"}
    assert _imports(UI_DIR / "__init__.py", "kanamemory.next_step.ui") == set()
