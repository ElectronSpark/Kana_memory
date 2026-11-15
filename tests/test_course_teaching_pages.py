"""The stop pages of the teaching steps and the page 开始前 without the app object (design 13, C4).

The session and the values of a teaching step, the wording and the display models of the five pages
(引导题待核, 学习材料尚不完整 / 材料待核验, 引导已保留, 引导例题暂不可用, 开始前), and the pages themselves: each
drawn from a model into fake controls, with the full ordered list of children, every control's keyword
arguments, where the handed-in rows stand and the action every button hands on with the pressed control. The
page module follows the shared page contract; the pure modules import exactly their registered modules.
"""
import ast
import dataclasses
from pathlib import Path

import pytest

from kanamemory import ui_components as component_ui
from kanamemory.course_line import teaching_display as display, teaching_model as model
from kanamemory.course_line.teaching_display import (FamiliarityModel, GuideBlockedModel, GuidePausedModel,
                                                     GuideUnavailableModel, MaterialNotReadyModel, TeachingButton,
                                                     familiarity_model, guide_blocked_model, guide_paused_model,
                                                     guide_unavailable_model, material_not_ready_model)
from kanamemory.course_line.teaching_model import TeachingAction, TeachingActionKind as Kind, TeachingSession
from kanamemory.course_line.ui import teaching_pages
from support.fake_ui import View
from support.ui_module_contract import assert_page_module_holds_no_app_store_or_service


@pytest.fixture
def fake_ui(monkeypatch):
    monkeypatch.setattr(component_ui, "label", lambda text, **style: View(text=text, style=("label", style)))
    monkeypatch.setattr(component_ui, "button",
                        lambda text, handler, **style: View(text=text, on_press=handler, style=("button", style)))


def drawn(children):
    """Each child as (kind, text, keyword arguments); a handed-in row as itself."""
    return [child if child.style is None else (child.style[0], child.text, child.style[1]) for child in children]


def recorder():
    pressed = []
    return pressed, lambda action, widget: pressed.append((action, widget))


WORD = {"id": "word:synthetic:w1", "title": "語1", "family": "word", "instruction_scope": None}
RULE = {"id": "grammar:synthetic:g", "title": "〜合成", "family": "grammar"}
LEAVE = TeachingButton("回到今日", TeachingAction(Kind.LEAVE))
MUTED = component_ui.MUTED


# --- the session and the values ---------------------------------------------------------------------------

def fields(cls):
    return [(field.name, field.default) for field in dataclasses.fields(cls)]


MISSING = dataclasses.MISSING


def test_the_session_starts_empty_and_is_compared_by_identity():
    assert fields(TeachingSession) == [("item", None), ("guide", None), ("familiarity", None), ("head", None)]
    assert TeachingSession() != TeachingSession()


def test_the_values_of_a_teaching_step():
    assert [fields(cls) for cls in (model.BatchOwns, model.GuidePaused)] == [[], []]
    for cls in (model.GuideBlocked, model.OpenAbility, model.AskFamiliarity, model.GuideUnavailable):
        assert fields(cls) == [("item", MISSING)]
    assert fields(model.MaterialNotReady) == [("item", MISSING), ("missing", MISSING)]
    assert fields(model.Guide) == [("item", MISSING), ("question", MISSING), ("checkpoint", MISSING),
                                   ("known", MISSING), ("shared_intro", MISSING)]
    assert fields(model.Introduce) == [("item", MISSING), ("familiarity", MISSING), ("enrolled", MISSING)]
    assert fields(model.FamiliarityOpened) == [("item", MISSING), ("state", MISSING), ("abilities", MISSING),
                                               ("levels", MISSING), ("ability", MISSING)]
    assert fields(TeachingAction) == [("kind", MISSING), ("item", None), ("level", None)]
    assert [kind.name for kind in Kind] == ["LEAVE", "HOME", "DETAIL", "CONTINUE_OTHER", "LEARNING_SETTINGS",
                                           "START_PRACTICE", "SKIP_ITEM", "FAMILIARITY"]
    values = (model.GuideBlocked, model.MaterialNotReady, model.OpenAbility, model.BatchOwns, model.AskFamiliarity,
              model.GuidePaused, model.GuideUnavailable, model.Guide, model.Introduce, model.FamiliarityOpened,
              TeachingAction)
    assert all(cls.__dataclass_params__.frozen for cls in values)


# --- the wording and the models ----------------------------------------------------------------------------

def test_the_guide_blocked_model():
    assert guide_blocked_model(RULE) == GuideBlockedModel(
        page="learning_wait", title="引导题待核", text="说明已经更新，原引导题与草稿已保留。",
        detail=TeachingButton("查看更新后的说明", TeachingAction(Kind.DETAIL, item=RULE)), home=LEAVE)


def test_the_material_model_in_both_variants():
    assert material_not_ready_model(RULE, missing=True) == MaterialNotReadyModel(
        page="learning_wait", title="学习材料尚不完整", text="原有进度与题目已保存，暂不开始正式教学。",
        detail=TeachingButton("查看材料", TeachingAction(Kind.DETAIL, item=RULE)), home=LEAVE)
    waiting = material_not_ready_model(RULE, missing=False)
    assert (waiting.title, waiting.detail.caption) == ("材料待核验", "预览草稿")


def test_the_paused_model_goes_home_directly():
    assert guide_paused_model() == GuidePausedModel(
        page="learning_wait", title="引导已保留", text="这类练习已关闭，输入已保存。",
        go_on=TeachingButton("继续其他学习", TeachingAction(Kind.CONTINUE_OTHER)),
        settings=TeachingButton("练习设置", TeachingAction(Kind.LEARNING_SETTINGS)),
        home=TeachingButton("回到今日", TeachingAction(Kind.HOME)))


def test_the_unavailable_model_reads_a_word_in_the_study_face():
    assert guide_unavailable_model(WORD) == GuideUnavailableModel(
        page="learning_wait", title="引导例题暂不可用", item_title="語1", title_role="study",
        text="这一项的引导例题暂不可用（旧例题已停用），可直接开始练习，或跳到下一项。",
        note="直接开始练习不会记为已掌握，之后仍按独立作答结果安排复习。",
        practice=TeachingButton("直接开始练习", TeachingAction(Kind.START_PRACTICE)),
        skip=TeachingButton("跳到下一项", TeachingAction(Kind.SKIP_ITEM)), home=LEAVE)
    bare = guide_unavailable_model({"id": "x"})
    assert (bare.item_title, bare.title_role) == ("", "ui")
    assert display.NOT_SAVED == "暂未保存："


@pytest.mark.parametrize("item, ability, shown, role", [
    (WORD, "understanding", "词义", "study"), (RULE, "understanding", "理解规则", "ui"), (WORD, "reading", "读音", "study"),
    (RULE, "use", "提取与使用", "ui"), (WORD, "listening", "听辨理解", "study"), (RULE, "application", "应用规则", "ui"),
    (RULE, "another", "another", "ui")])
def test_the_familiarity_model_names_the_ability_in_the_learners_words(item, ability, shown, role):
    page = familiarity_model(item, ability)
    assert page == FamiliarityModel(
        page="learning_familiarity", title="开始前", item_title=item["title"], title_role=role, ability=shown,
        levels=(TeachingButton("初学", TeachingAction(Kind.FAMILIARITY, level="new")),
                TeachingButton("有印象", TeachingAction(Kind.FAMILIARITY, level="some")),
                TeachingButton("已熟悉", TeachingAction(Kind.FAMILIARITY, level="familiar"))))


# --- the pages -----------------------------------------------------------------------------------------

def test_the_guide_blocked_page(fake_ui):
    replacement, rows = [View(text="使用更新后的练习继续")], [View(text="下一内容")]
    pressed, on_action = recorder()
    children = teaching_pages.guide_blocked_page(guide_blocked_model(RULE), replacement_rows=replacement,
                                                 next_content_rows=rows, on_action=on_action)
    assert drawn(children) == [("label", "说明已经更新，原引导题与草稿已保留。", {}), *replacement,
                               ("button", "查看更新后的说明", {}), *rows, ("button", "回到今日", {})]
    for control in (children[2], children[4]):
        control.on_press(control)
    assert pressed == [(TeachingAction(Kind.DETAIL, item=RULE), children[2]), (TeachingAction(Kind.LEAVE), children[4])]
    assert pressed[0][0].item is RULE


def test_the_material_page(fake_ui):
    pressed, on_action = recorder()
    children = teaching_pages.material_not_ready_page(material_not_ready_model(RULE, missing=False),
                                                      on_action=on_action)
    assert drawn(children) == [("label", "原有进度与题目已保存，暂不开始正式教学。", {}),
                               ("button", "预览草稿", {}), ("button", "回到今日", {})]
    for control in children[1:]:
        control.on_press(control)
    assert pressed == [(TeachingAction(Kind.DETAIL, item=RULE), children[1]), (TeachingAction(Kind.LEAVE), children[2])]


def test_the_paused_page(fake_ui):
    pressed, on_action = recorder()
    children = teaching_pages.guide_paused_page(guide_paused_model(), on_action=on_action)
    assert drawn(children) == [("label", "这类练习已关闭，输入已保存。", {"size": 16}),
                               ("button", "继续其他学习", {"primary": True}),
                               ("button", "练习设置", {"compact": True}),
                               ("button", "回到今日", {})]
    for control in children[1:]:
        control.on_press(control)
    assert pressed == [(TeachingAction(Kind.CONTINUE_OTHER), children[1]),
                       (TeachingAction(Kind.LEARNING_SETTINGS), children[2]), (TeachingAction(Kind.HOME), children[3])]


def test_the_unavailable_page_returns_its_status_line(fake_ui):
    pressed, on_action = recorder()
    view = teaching_pages.guide_unavailable_page(guide_unavailable_model(WORD), on_action=on_action)
    assert drawn(view.children) == [
        ("label", "語1", {"size": 21, "bold": True, "text_role": "study"}),
        ("label", "这一项的引导例题暂不可用（旧例题已停用），可直接开始练习，或跳到下一项。", {"size": 16}),
        ("label", "直接开始练习不会记为已掌握，之后仍按独立作答结果安排复习。", {"size": 13, "color": MUTED}),
        ("label", "", {"size": 13, "color": MUTED}),
        ("button", "直接开始练习", {"primary": True}), ("button", "跳到下一项", {}), ("button", "回到今日", {})]
    assert view.status is view.children[3]
    for control in view.children[4:]:
        control.on_press(control)
    assert pressed == [(TeachingAction(Kind.START_PRACTICE), view.children[4]),
                       (TeachingAction(Kind.SKIP_ITEM), view.children[5]), (TeachingAction(Kind.LEAVE), view.children[6])]


def test_the_familiarity_page(fake_ui):
    pressed, on_action = recorder()
    children = teaching_pages.familiarity_page(familiarity_model(WORD, "reading"), on_action=on_action)
    assert drawn(children) == [("label", "語1", {"size": 24, "bold": True, "text_role": "study"}),
                               ("label", "读音", {"size": 18, "bold": True}),
                               ("button", "初学", {}), ("button", "有印象", {}), ("button", "已熟悉", {})]
    for control in children[2:]:
        control.on_press(control)
    assert [(action.kind, action.level, widget) for action, widget in pressed] == [
        (Kind.FAMILIARITY, "new", children[2]), (Kind.FAMILIARITY, "some", children[3]),
        (Kind.FAMILIARITY, "familiar", children[4])]


def test_the_page_functions_take_only_a_model_rows_and_one_callback():
    tree = ast.parse(Path(teaching_pages.__file__).read_text("utf-8"))
    signatures = {node.name: ast.unparse(node.args) for node in tree.body
                  if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}
    assert signatures == {
        "guide_blocked_page": "model: GuideBlockedModel, *, replacement_rows: Sequence, next_content_rows: Sequence, "
                              "on_action: OnAction",
        "material_not_ready_page": "model: MaterialNotReadyModel, *, on_action: OnAction",
        "guide_paused_page": "model: GuidePausedModel, *, on_action: OnAction",
        "guide_unavailable_page": "model: GuideUnavailableModel, *, on_action: OnAction",
        "familiarity_page": "model: FamiliarityModel, *, on_action: OnAction"}


# --- the contract and the imports ----------------------------------------------------------------------

UI_DIR = Path(teaching_pages.__file__).parent
PACKAGE_DIR = UI_DIR.parent
SOURCE_DIR = PACKAGE_DIR.parent
OUTER = ("toga", "travertino", "kanamemory.app", "kanamemory.ui_components")


def _imports(path, package, *, module_level=False):
    names = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in (tree.body if module_level else ast.walk(tree)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package.split(".")[:len(package.split(".")) - node.level + 1] if node.level else []
            names.add(".".join([*base, node.module] if node.module else base))
    return names


def _within(module, names):
    return any(module == name or module.startswith(name + ".") for name in names)


def test_the_teaching_page_module_holds_no_app_store_or_service():
    assert_page_module_holds_no_app_store_or_service(UI_DIR / "teaching_pages.py")


def test_the_pure_modules_and_the_page_module_import_exactly_their_registered_modules():
    assert _imports(PACKAGE_DIR / "teaching_model.py", "kanamemory.course_line") == {"__future__", "dataclasses", "enum"}
    assert _imports(PACKAGE_DIR / "teaching_display.py", "kanamemory.course_line") == {
        "__future__", "dataclasses", "kanamemory.next_step.display", "kanamemory.course_line.teaching_model"}
    assert _imports(UI_DIR / "teaching_pages.py", "kanamemory.course_line.ui") == {
        "__future__", "collections.abc", "dataclasses", "kanamemory.ui_components",
        "kanamemory.course_line.teaching_display", "kanamemory.course_line.teaching_model"}


def test_the_service_and_the_app_side_module_import_no_toga_no_controls_and_no_app_module():
    service = _imports(PACKAGE_DIR / "teaching.py", "kanamemory.course_line")
    assert not [module for module in service if _within(module, (*OUTER, "kanamemory.course_line.ui"))]
    assert not [module for module in service if module.endswith("_ui") or module.endswith("_runtime")]
    path = SOURCE_DIR / "course_teaching_runtime.py"
    assert not [module for module in _imports(path, "kanamemory") if _within(module, OUTER) or module.endswith("_ui")]
    assert not [module for module in _imports(path, "kanamemory", module_level=True)
                if _within(module, ("kanamemory.course_line.ui",))]
