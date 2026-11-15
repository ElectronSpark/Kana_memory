"""The batch introduction's values, wording, display models and pages without the app object (design 13, C4).

The vocabulary: frozen values with their fields and defaults, and the page
actions. The wording and the display models of the pages (说明卡暂不可用, 新项说明,
批小测暂不可用, 批小测 asking a question and once it is settled, what follows an
answer, 新能力暂不可用) by their literal texts. The pages: each drawn from a model
into fake controls, with the full ordered list of children, every control's
keyword arguments (a default label records none), where the handed-in rows
stand and the action every button hands on with the pressed control. The page
module follows the shared page contract; the pure modules import exactly their
registered modules; the service and the app-side module import no Toga, no
control components and no app module.
"""
import ast
import dataclasses
from pathlib import Path

import pytest

from kanamemory import ui_components as component_ui
from kanamemory.answer_result_ui import shown_answer, shown_explanation
from kanamemory.course_line import batch_display as display, batch_model as model
from kanamemory.course_line.batch_display import (AbilityUnavailableModel, BatchButton, CardPageModel,
                                                  CardUnavailableModel, QuizPageModel, QuizResultModel,
                                                  QuizSettledModel, QuizUnavailableModel, ability_hint,
                                                  ability_unavailable_model, card_model, card_unavailable_model,
                                                  quiz_model, quiz_result_model, quiz_settled_model,
                                                  quiz_unavailable_model)
from kanamemory.course_line.batch_model import BatchAction, BatchActionKind as Kind
from kanamemory.course_line.ui import batch_pages
from kanamemory.learning_questions import KIND_LABELS
from kanamemory.question_support import learning_text_role
from support.fake_ui import View
from support.ui_module_contract import assert_page_module_holds_no_app_store_or_service


@pytest.fixture
def fake_ui(monkeypatch):
    """Fake controls; returns the log of the cue builder's calls (the control module, the question, the known
    ids)."""
    def label(text, **style):
        return View(text=text, style=("label", style))

    def button(text, handler, **style):
        return View(text=text, on_press=handler, style=("button", style))

    def col(*children, **style):
        return View(*children, style=("col", style))

    cues = []

    def instruction_widgets(ui, question, known_ids=None):
        cues.append((ui, question, known_ids))
        return [CUE]

    monkeypatch.setattr(component_ui, "label", label)
    monkeypatch.setattr(component_ui, "button", button)
    monkeypatch.setattr(component_ui, "col", col)
    monkeypatch.setattr(batch_pages, "instruction_widgets", instruction_widgets)
    monkeypatch.setattr(batch_pages, "build_answer_feedback",
                        lambda result, **kwargs: View(text="判定", style=("verdict", (dict(result), kwargs))))
    return cues


def drawn(children):
    """Each child as (kind, text, keyword arguments); a handed-in row as itself."""
    return [child if child.style is None else (child.style[0], child.text, child.style[1]) for child in children]


def recorder():
    pressed = []
    return pressed, lambda action, widget: pressed.append((action, widget))


CUE = View(text="题目提示")
MUTED = {"size": 13, "color": component_ui.MUTED}
HOME = BatchButton("回到今日", BatchAction(Kind.LEAVE))
ENTRY = {"content_id": "word:synthetic:w1", "introduction_id": "intro-1"}
QUESTION = {"id": "q-1", "target_id": "word:synthetic:w1", "kind": "vocab_meaning", "ability": "understanding",
            "prompt": "語1", "answer": "词1", "choices": ["词1", "别的词"], "feedback": "说明", "input_mode": "choice"}


# --- the vocabulary ----------------------------------------------------------------------------------

def fields(cls):
    return [(field.name, field.default) for field in dataclasses.fields(cls)]


MISSING = dataclasses.MISSING
VALUES = (model.BatchOver, model.BatchClosed, model.CardStep, model.QuizStep, model.CardUnavailable,
          model.CardOpened, model.QuizUnavailable, model.QuizSettled, model.QuizPaused, model.QuizAsked,
          model.QuizStale, model.QuizAnswered, model.BatchHelp, model.BatchAction)


def test_the_values_are_frozen_with_their_fields_and_defaults():
    assert all(dataclasses.is_dataclass(cls) and cls.__dataclass_params__.frozen for cls in VALUES)
    assert [fields(cls) for cls in (model.BatchOver, model.BatchClosed, model.QuizStep, model.CardUnavailable,
                                    model.QuizStale)] == [[]] * 5
    assert fields(model.CardStep) == [("entry", MISSING)]
    assert fields(model.CardOpened) == [("item", MISSING), ("entry", MISSING), ("index", MISSING),
                                        ("total", MISSING)]
    assert fields(model.QuizUnavailable) == [("message", MISSING)]
    assert fields(model.QuizSettled) == [("item", MISSING)]
    assert fields(model.QuizPaused) == [("item", MISSING)]
    assert fields(model.QuizAsked) == [("item", MISSING), ("question", MISSING), ("known", MISSING),
                                       ("labels", MISSING), ("role", MISSING)]
    assert fields(model.QuizAnswered) == [("correct", MISSING)]
    assert fields(model.BatchHelp) == [("hint", MISSING), ("card", MISSING)]
    assert fields(model.BatchAction) == [("kind", MISSING), ("entry", None), ("question", None), ("answer", None)]


def test_the_page_actions():
    assert [kind.name for kind in Kind] == ["CARD_NEXT", "CARD_KNOWN", "LEAVE", "LEARNING_SETTINGS", "QUIZ_ANSWER",
                                           "QUIZ_CONTINUE", "QUIZ_FINISH"]
    assert BatchAction(Kind.CARD_NEXT, entry=dict(ENTRY)) == BatchAction(Kind.CARD_NEXT, entry=ENTRY)
    assert BatchAction(Kind.CARD_NEXT, entry=ENTRY) != BatchAction(Kind.CARD_KNOWN, entry=ENTRY)


# --- the wording ---------------------------------------------------------------------------------------

def test_every_fixed_text_of_the_batch_pages():
    assert (display.CARD_PAGE, display.QUIZ_PAGE) == ("learning_card", "learning_batch_quiz")
    assert display.REVIEW_HINT == "有到期复习，建议先复习"
    assert display.QUIZ_PAUSED == "未完成题目已保留，重新开启对应题型后可继续。"
    assert display.CARD_BUTTON == "说明卡"
    assert (display.CARD_UNAVAILABLE_TITLE, display.CARD_UNAVAILABLE_TEXT) == (
        "说明卡暂不可用", "这一项的说明已变化，原有进度已保存。")
    assert (display.CARD_TITLE, display.CARD_NEXT, display.CARD_KNOWN, display.CARD_LATER) == (
        "新项说明", "下一项", "我已经会了", "稍后再学")
    assert (display.QUIZ_UNAVAILABLE_TITLE, display.QUIZ_UNAVAILABLE_TEXT) == ("批小测暂不可用", "说明卡进度已保存。")
    assert display.NO_QUIZ_KIND == "这一批里有内容暂时没有可用的小测题型，请检查题型设置。"
    assert (display.QUIZ_TITLE, display.QUIZ_SETTLED_TEXT, display.CONTINUE) == ("批小测", "本批小测已完成。", "继续")
    assert (display.ABILITY_UNAVAILABLE_TITLE, display.ABILITY_UNAVAILABLE_TEXT) == (
        "新能力暂不可用", "原有进度已保存。")


def test_the_pages_of_the_batch_are_the_ones_the_line_knows():
    from kanamemory.intro_batch_ui import INTRO_BATCH_PAGES
    assert INTRO_BATCH_PAGES == {display.CARD_PAGE, display.QUIZ_PAGE}


def test_the_hint_line_of_a_later_ability():
    assert ability_hint({"ability": "reading", "kind": "vocab_reading"}) == "新能力：读法 — 写出读法"
    assert ability_hint({"ability": "understanding", "kind": "vocab_meaning"}) == (
        f"新能力：理解 — {KIND_LABELS['vocab_meaning']}")
    assert ability_hint({}) == "新能力：练习 — 练习"
    assert display.ABILITY_NAMES == {"understanding": "理解", "reading": "读法", "listening": "听辨", "use": "使用",
                                     "application": "应用"}


# --- the display models ----------------------------------------------------------------------------------

def test_the_unavailable_models():
    assert card_unavailable_model() == CardUnavailableModel(
        page="learning_wait", title="说明卡暂不可用", text="这一项的说明已变化，原有进度已保存。", home=HOME)
    assert quiz_unavailable_model() == QuizUnavailableModel(
        page="learning_wait", title="批小测暂不可用", text="说明卡进度已保存。",
        settings=BatchButton("题型设置", BatchAction(Kind.LEARNING_SETTINGS)), home=HOME)
    assert ability_unavailable_model() == AbilityUnavailableModel(
        page="learning_wait", title="新能力暂不可用", text="原有进度已保存。", home=HOME)


def test_the_card_model_counts_from_one_and_its_choices_carry_the_entry():
    assert card_model(0, 2, ENTRY) == CardPageModel(
        page="learning_card", title="新项说明", position="第 1 / 2 项",
        next=BatchButton("下一项", BatchAction(Kind.CARD_NEXT, entry=ENTRY)),
        known=BatchButton("我已经会了", BatchAction(Kind.CARD_KNOWN, entry=ENTRY)),
        later=BatchButton("稍后再学", BatchAction(Kind.LEAVE)))
    assert card_model(2, 3, ENTRY).position == "第 3 / 3 项"


def test_the_settled_quiz_model():
    assert quiz_settled_model() == QuizSettledModel(
        page="learning_batch_quiz", title="批小测", text="本批小测已完成。",
        finish=BatchButton("继续", BatchAction(Kind.QUIZ_FINISH)))


def test_the_quiz_model_shows_the_labels_and_answers_with_the_raw_choice_and_the_same_question():
    known = {"word:known"}
    page = quiz_model(QUESTION, {"词1": "词1（显示）"}, "ui", known)
    assert (page.page, page.title, page.heading, page.role) == ("learning_batch_quiz", "批小测", "批小测", "ui")
    assert page.question is QUESTION and page.known is known
    assert [choice.caption for choice in page.choices] == ["词1（显示）", "别的词"]
    assert [(choice.action.kind, choice.action.answer) for choice in page.choices] == [
        (Kind.QUIZ_ANSWER, "词1"), (Kind.QUIZ_ANSWER, "别的词")]
    assert all(choice.action.question is QUESTION for choice in page.choices)
    assert quiz_model({"id": "q"}, {}, "ui", None).choices == ()


def test_what_follows_a_right_answer():
    assert quiz_result_model(QUESTION, correct=True, done=False) == QuizResultModel(
        correct=True, answer=None, answer_role=None, explanation=shown_explanation(QUESTION, "说明"),
        card_heading=None, proceed=BatchButton("继续", BatchAction(Kind.QUIZ_CONTINUE)))
    assert quiz_result_model(QUESTION, correct=True, done=True).proceed == BatchButton(
        "继续", BatchAction(Kind.QUIZ_FINISH))


def test_what_follows_a_wrong_answer():
    result = quiz_result_model(QUESTION, correct=False, done=False)
    assert result == QuizResultModel(
        correct=False, answer=shown_answer(QUESTION), answer_role=learning_text_role(QUESTION, "answer"),
        explanation=shown_explanation(QUESTION, "说明"), card_heading="说明卡",
        proceed=BatchButton("继续", BatchAction(Kind.QUIZ_CONTINUE)))
    assert result.answer == "词1" and result.explanation == "说明"


def test_a_question_without_an_explanation_shows_none():
    plain = {key: value for key, value in QUESTION.items() if key != "feedback"}
    assert quiz_result_model(plain, correct=True, done=True).explanation is None
    assert quiz_result_model(dict(plain, feedback=""), correct=False, done=True).explanation is None


def test_the_display_models_are_frozen():
    for value in (card_unavailable_model(), card_model(0, 1, ENTRY), quiz_unavailable_model(), quiz_settled_model(),
                  quiz_model(QUESTION, {}, "ui", None), quiz_result_model(QUESTION, correct=True, done=True),
                  ability_unavailable_model(), HOME):
        with pytest.raises(dataclasses.FrozenInstanceError):
            value.page = "x"


# --- the pages -----------------------------------------------------------------------------------------

def test_the_card_unavailable_page(fake_ui):
    pressed, on_action = recorder()
    children = batch_pages.card_unavailable_page(card_unavailable_model(), on_action=on_action)
    assert drawn(children) == [("label", "这一项的说明已变化，原有进度已保存。", {}),
                               ("button", "回到今日", {"primary": True})]
    children[1].on_press(children[1])
    assert pressed == [(BatchAction(Kind.LEAVE), children[1])]


def test_the_card_page(fake_ui):
    hint, body = [View(text="复习提示")], [View(text="卡片标题"), View(text="卡片例句")]
    pressed, on_action = recorder()
    children = batch_pages.card_page(card_model(0, 2, ENTRY), hint_rows=hint, body_rows=body, on_action=on_action)
    assert drawn(children) == [("label", "第 1 / 2 项", MUTED), *hint, *body,
                               ("button", "下一项", {"primary": True}),
                               ("button", "我已经会了", {}),
                               ("button", "稍后再学", {})]
    for control in children[-3:]:
        control.on_press(control)
    assert pressed == [(BatchAction(Kind.CARD_NEXT, entry=ENTRY), children[-3]),
                       (BatchAction(Kind.CARD_KNOWN, entry=ENTRY), children[-2]),
                       (BatchAction(Kind.LEAVE), children[-1])]


def test_the_card_page_without_a_review_hint(fake_ui):
    children = batch_pages.card_page(card_model(1, 2, ENTRY), hint_rows=[], body_rows=[],
                                     on_action=lambda action, widget: None)
    assert [child.text for child in children] == ["第 2 / 2 项", "下一项", "我已经会了", "稍后再学"]


def test_the_quiz_unavailable_page(fake_ui):
    pressed, on_action = recorder()
    children = batch_pages.quiz_unavailable_page(quiz_unavailable_model(), on_action=on_action)
    assert drawn(children) == [("label", "说明卡进度已保存。", {}),
                               ("button", "题型设置", {"primary": True}),
                               ("button", "回到今日", {})]
    for control in children[1:]:
        control.on_press(control)
    assert pressed == [(BatchAction(Kind.LEARNING_SETTINGS), children[1]), (BatchAction(Kind.LEAVE), children[2])]


def test_the_settled_quiz_page_keeps_an_unshown_result_column_and_no_choices(fake_ui):
    pressed, on_action = recorder()
    view = batch_pages.quiz_settled_page(quiz_settled_model(), on_action=on_action)
    assert drawn(view.children) == [("label", "本批小测已完成。", {"size": 18}),
                                    ("button", "继续", {"primary": True})]
    assert view.result.style == ("col", {}) and view.result not in view.children and view.buttons == []
    view.children[1].on_press(view.children[1])
    assert pressed == [(BatchAction(Kind.QUIZ_FINISH), view.children[1])]


def test_the_quiz_page(fake_ui):
    hint, prompt, known = [View(text="复习提示")], View(text="题干"), {"word:known"}
    pressed, on_action = recorder()
    page = quiz_model(QUESTION, {"词1": "词1（显示）"}, "study", known)
    view = batch_pages.quiz_page(page, hint_rows=hint, prompt=prompt, on_action=on_action)
    choice = {"multiline": True, "text_role": "study"}
    assert drawn(view.children) == [("label", "批小测", MUTED), *hint, prompt, CUE,
                                    ("button", "词1（显示）", choice), ("button", "别的词", choice),
                                    ("col", "", {})]
    assert view.result is view.children[-1] and view.result.children == []
    assert view.buttons == view.children[-3:-1]
    assert fake_ui == [(component_ui, QUESTION, known)]
    for control in view.buttons:
        control.on_press(control)
    assert [(action.kind, action.answer, widget) for action, widget in pressed] == [
        (Kind.QUIZ_ANSWER, "词1", view.buttons[0]), (Kind.QUIZ_ANSWER, "别的词", view.buttons[1])]
    assert all(action.question is QUESTION for action, _ in pressed)


def test_a_wrong_answer_shows_the_answer_the_explanation_and_the_card_again(fake_ui):
    pressed, on_action = recorder()
    view = batch_pages.quiz_page(quiz_model(QUESTION, {}, "ui", None), hint_rows=[], prompt=View(text="题干"),
                                 on_action=on_action)
    view.result.add(View(text="旧内容"))
    card = [View(text="卡片标题"), View(text="卡片例句")]
    result = quiz_result_model(QUESTION, correct=False, done=False)
    batch_pages.show_quiz_result(view.result, view.buttons, result, card_rows=card, on_action=on_action)
    assert [button.enabled for button in view.buttons] == [False, False]
    shown = view.result.children
    assert drawn(shown[:4]) == [("verdict", "判定", ({"correct": False}, {})),
                                ("label", "词1", {"bold": True, "text_role": result.answer_role}),
                                ("label", "说明", {}),
                                ("label", "说明卡", MUTED)]
    assert shown[4].style == ("col", {}) and shown[4].children == card
    assert drawn(shown[5:]) == [("button", "继续", {"primary": True})]
    shown[5].on_press(shown[5])
    assert pressed == [(BatchAction(Kind.QUIZ_CONTINUE), shown[5])]


def test_a_right_answer_shows_no_answer_and_no_card(fake_ui):
    pressed, on_action = recorder()
    plain = {key: value for key, value in QUESTION.items() if key != "feedback"}
    view = batch_pages.quiz_page(quiz_model(plain, {}, "ui", None), hint_rows=[], prompt=View(text="题干"),
                                 on_action=on_action)
    batch_pages.show_quiz_result(view.result, view.buttons, quiz_result_model(plain, correct=True, done=True),
                                 card_rows=[View(text="不显示")], on_action=on_action)
    assert drawn(view.result.children) == [("verdict", "判定", ({"correct": True}, {})),
                                           ("button", "继续", {"primary": True})]
    assert [button.enabled for button in view.buttons] == [False, False]
    view.result.children[1].on_press(view.result.children[1])
    assert pressed == [(BatchAction(Kind.QUIZ_FINISH), view.result.children[1])]


def test_the_ability_unavailable_page(fake_ui):
    rows = [View(text="下一内容")]
    pressed, on_action = recorder()
    children = batch_pages.ability_unavailable_page(ability_unavailable_model(), next_content_rows=rows,
                                                    on_action=on_action)
    assert drawn(children) == [("label", "原有进度已保存。", {}), *rows, ("button", "回到今日", {})]
    children[-1].on_press(children[-1])
    assert pressed == [(BatchAction(Kind.LEAVE), children[-1])]


def test_the_pages_take_their_captions_and_actions_from_the_model(fake_ui):
    """A hand-made model with other captions and actions: the page draws and hands on what the model carries."""
    custom = BatchButton("甲", BatchAction(Kind.QUIZ_FINISH))
    page = dataclasses.replace(card_model(0, 1, ENTRY), position="位置", next=custom)
    pressed, on_action = recorder()
    children = batch_pages.card_page(page, hint_rows=[], body_rows=[], on_action=on_action)
    assert [child.text for child in children] == ["位置", "甲", "我已经会了", "稍后再学"]
    children[1].on_press(children[1])
    assert pressed == [(custom.action, children[1])]


def test_the_page_functions_take_only_a_model_rows_and_one_callback():
    tree = ast.parse(Path(batch_pages.__file__).read_text("utf-8"))
    signatures = {node.name: ast.unparse(node.args) for node in tree.body
                  if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}
    assert signatures == {
        "card_unavailable_page": "model: CardUnavailableModel, *, on_action: OnAction",
        "card_page": "model: CardPageModel, *, hint_rows: Sequence, body_rows: Sequence, on_action: OnAction",
        "quiz_unavailable_page": "model: QuizUnavailableModel, *, on_action: OnAction",
        "quiz_settled_page": "model: QuizSettledModel, *, on_action: OnAction",
        "quiz_page": "model: QuizPageModel, *, hint_rows: Sequence, prompt, on_action: OnAction",
        "show_quiz_result": "result, buttons: Sequence, model: QuizResultModel, *, card_rows: Sequence, "
                            "on_action: OnAction",
        "ability_unavailable_page": "model: AbilityUnavailableModel, *, next_content_rows: Sequence, "
                                    "on_action: OnAction"}


# --- the contract and the imports ----------------------------------------------------------------------

UI_DIR = Path(batch_pages.__file__).parent
PACKAGE_DIR = UI_DIR.parent
SOURCE_DIR = PACKAGE_DIR.parent


def test_the_batch_page_module_holds_no_app_store_or_service():
    assert_page_module_holds_no_app_store_or_service(UI_DIR / "batch_pages.py")


def _imports(path, package, *, module_level=False):
    """Absolute module names a source file imports (anywhere in the file, or only at module level)."""
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


def test_the_pure_modules_import_exactly_their_registered_modules():
    assert _imports(PACKAGE_DIR / "batch_model.py", "kanamemory.course_line") == {"__future__", "dataclasses", "enum"}
    assert _imports(PACKAGE_DIR / "batch_display.py", "kanamemory.course_line") == {
        "__future__", "dataclasses", "kanamemory.answer_result_ui", "kanamemory.learning_questions",
        "kanamemory.next_step.display", "kanamemory.question_support", "kanamemory.course_line.batch_model",
        "kanamemory.course_line.display"}
    assert _imports(SOURCE_DIR / "intro_batch_quiz.py", "kanamemory") == {
        "__future__", "kanamemory.learning_questions", "kanamemory.learning_practice_policy"}


def test_the_page_module_imports_exactly_the_controls_two_shared_builders_its_display_and_its_model():
    assert _imports(UI_DIR / "batch_pages.py", "kanamemory.course_line.ui") == {
        "__future__", "collections.abc", "dataclasses", "kanamemory.ui_components", "kanamemory.answer_feedback",
        "kanamemory.question_instruction_ui", "kanamemory.course_line.batch_display",
        "kanamemory.course_line.batch_model"}


OUTER = ("toga", "travertino", "kanamemory.app", "kanamemory.ui_components")


def test_the_service_imports_no_toga_no_controls_no_page_and_no_app_module():
    found = _imports(PACKAGE_DIR / "batch.py", "kanamemory.course_line")
    assert not [module for module in found if _within(module, (*OUTER, "kanamemory.course_line.ui"))]
    assert not [module for module in found if module.endswith("_ui") or module.endswith("_runtime")]


def test_the_app_side_module_imports_no_toga_no_controls_and_no_app_module_and_its_pages_only_when_it_draws():
    path = SOURCE_DIR / "intro_batch_runtime.py"
    assert not [module for module in _imports(path, "kanamemory") if _within(module, OUTER)]
    assert not [module for module in _imports(path, "kanamemory", module_level=True)
                if _within(module, ("kanamemory.course_line.ui",))]
