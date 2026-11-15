"""The result of a course answer and its detail page without the app object (design 13, C4).

The wording, the display rule of an explanation, the values and actions of the result pages, and the two
pages: each drawn from a hand-written model into fake controls, with the full ordered list of children, every
control's keyword arguments, where the handed-in controls stand and the action every button hands on with the
pressed control. The page module follows the shared page contract; the pure modules import exactly their
registered modules.
"""
import ast
import dataclasses
from pathlib import Path

import pytest

from kanamemory import ui_components as component_ui
from kanamemory.course_line import answer_display as display, answer_model as model
from kanamemory.course_line.answer_display import (AnswerButton, DetailModel, FeedbackModel, detail_model,
                                                   without_self_gloss)
from kanamemory.course_line.answer_model import AnswerAction, AnswerActionKind as Kind
from kanamemory.course_line.ui import answer_pages
from support.fake_ui import View
from support.ui_module_contract import assert_page_module_holds_no_app_store_or_service


@pytest.fixture
def fake_ui(monkeypatch):
    def control(kind):
        def make(*children, **style):
            return View(*children, style=(kind, style))
        return make

    monkeypatch.setattr(component_ui, "label", lambda text, **style: View(text=text, style=("label", style)))
    monkeypatch.setattr(component_ui, "button",
                        lambda text, handler, **style: View(text=text, on_press=handler, style=("button", style)))
    monkeypatch.setattr(component_ui, "row", control("row"))
    monkeypatch.setattr(component_ui, "col", control("col"))
    monkeypatch.setattr(component_ui, "font_scale", lambda: 1.0)
    monkeypatch.setattr(answer_pages, "build_answer_feedback", lambda result, *, structure_error=None: View(
        text="判定", style=("verdict", {"result": result, "structure_error": structure_error})))


def drawn(children):
    """Each control as (kind, text, keyword arguments), a box followed by what it holds; a handed-in control
    as itself."""
    flat = []
    for child in children:
        if child.style is None:
            flat.append(child)
            continue
        flat.append((child.style[0], child.text, child.style[1]))
        flat.extend(drawn(child.children))
    return flat


def recorder():
    pressed = []
    return pressed, lambda action, widget: pressed.append((action, widget))


def buttons(children):
    found = []
    for child in children:
        if child.style is not None and child.style[0] == "button":
            found.append(child)
        found.extend(buttons(child.children))
    return found


QUESTION = {"id": "q-1", "target_id": "grammar:synthetic:g", "kind": "grammar_meaning", "answer": "正在读书"}
RESULT = {"correct": True, "outcome": "correct"}
MUTED = component_ui.MUTED
TUTOR = AnswerButton("问 AI", AnswerAction(Kind.TUTOR, question_id="q-1"))
DETAIL = AnswerButton("详细", AnswerAction(Kind.DETAIL))
RATINGS = tuple(AnswerButton(caption, AnswerAction(Kind.RATE, rating=name, question_id="q-1"))
                for caption, name in (("再学一次", "again"), ("有些困难", "hard"), ("记住了", "good"), ("很轻松", "easy")))
SAVE = AnswerButton("保存并继续", AnswerAction(Kind.RATE, rating="again", question_id="q-1"))
HELP_ROW = [("row", "", {}), ("col", "", {"flex": 1}),
            ("button", "问 AI", {"compact": True, "width": 72, "font_size": 12}),
            ("button", "详细", {"compact": True, "width": 72, "font_size": 12})]
RATING_ROWS = [("row", "", {}), ("button", "再学一次", {"flex": 1, "multiline": True}),
               ("button", "有些困难", {"flex": 1, "multiline": True}),
               ("row", "", {}), ("button", "记住了", {"flex": 1, "multiline": True}),
               ("button", "很轻松", {"flex": 1, "multiline": True})]


def page_model(**fields):
    base = dict(page="learning_feedback", title="作答结果", question=QUESTION, result=RESULT, structure_error=None,
                answer_kind=display.ANSWER_LABEL, answer_text="正在读书", answer_role="ui", entered="正在读书",
                asked=None, asked_role=None, needs_teaching=None, entered_shown=None, diagnosis=None,
                explanation=None, tutor=TUTOR, detail=DETAIL, unresolved=None, ratings=RATINGS, save=None)
    return FeedbackModel(**{**base, **fields})


# --- the wording, the display rule, the vocabulary ----------------------------------------------------------

def test_every_fixed_text_of_the_result_pages():
    assert (display.FEEDBACK_PAGE, display.FEEDBACK_TITLE) == ("learning_feedback", "作答结果")
    assert (display.DETAIL_PAGE, display.DETAIL_TITLE) == ("learning_feedback_detail", "详细")
    assert (display.NEED_AUDIO, display.NEED_ANSWER, display.NEED_COMPLETE) == (
        "请先成功播放题目。", "请先作答。", "请完成作答。")
    assert display.BUSY_SUBMIT == "数据库正忙，答案尚未提交，请稍后再点提交。"
    assert display.BUSY_RATING == "数据库正忙，评级尚未保存。请稍后再点一次。"
    assert display.NEEDS_TEACHING == "先补学这项内容，再安排独立练习。"
    assert (display.STRUCTURE_WORD, display.STRUCTURE_OTHER) == (
        "句末词块后还有内容；本次不调整单词复习。", "句末词块后还有内容；本次不调整复习。")
    assert display.SPELLING_UNRESOLVED == "本次不调整读音复习。"
    assert (display.ASK_AI, display.DETAIL, display.SAVE_AND_CONTINUE, display.BACK_TO_RESULT) == (
        "问 AI", "详细", "保存并继续", "返回结果")
    assert display.RATINGS == (("再学一次", "again"), ("有些困难", "hard"), ("记住了", "good"), ("很轻松", "easy"))


def test_an_explanation_that_only_repeats_its_headword_loses_the_repetition():
    assert without_self_gloss("学校（がっこう）：学校") == "学校（がっこう）"
    assert without_self_gloss("学校（がっこう）:学校。") == "学校（がっこう）"
    assert without_self_gloss("例句译文。\n学校（がっこう）：学校") == "例句译文。\n学校（がっこう）"
    for kept in ("学校（がっこう）：学习的地方", "这是一句说明。", ""):
        assert without_self_gloss(kept) == kept
    assert without_self_gloss(None) == ""


def test_the_values_and_the_actions_of_the_result_pages():
    def fields(cls):
        return [(field.name, field.default) for field in dataclasses.fields(cls)]
    missing = dataclasses.MISSING
    assert fields(model.Refused) == [("message", missing)]
    assert fields(model.Graded) == [("result", missing), ("outcome", missing)]
    assert fields(model.Busy) == [] and fields(model.Recorded) == [("result", missing)]
    assert fields(model.Rated) == [("item", missing)]
    assert fields(AnswerAction) == [("kind", missing), ("rating", None), ("question_id", None)]
    assert [kind.name for kind in Kind] == ["RATE", "TUTOR", "DETAIL", "BACK"]
    assert all(cls.__dataclass_params__.frozen for cls in (
        model.Refused, model.Graded, model.Busy, model.Recorded, model.Rated, AnswerAction, AnswerButton,
        FeedbackModel, DetailModel))


def test_the_detail_model_reads_a_word_in_the_study_face():
    word = detail_model({"title": "学校", "family": "word"})
    assert word == DetailModel(page="learning_feedback_detail", title="详细", item_title="学校", title_role="study",
                               back=AnswerButton("返回结果", AnswerAction(Kind.BACK)))
    assert detail_model({"title": "〜ている", "family": "grammar"}).title_role == "ui"


# --- the result page -----------------------------------------------------------------------------------------

def test_a_right_answer_with_its_speaker_and_the_four_ratings(fake_ui):
    answer, speaker = View(text="正在读书"), View(text="🔊")
    pressed, on_action = recorder()
    children = answer_pages.feedback_page(page_model(), answer=answer, speaker=speaker, notes=[], on_action=on_action)
    assert drawn(children) == [("verdict", "判定", {"result": RESULT, "structure_error": None}),
                               ("row", "", {}), answer, speaker, *HELP_ROW, *RATING_ROWS]
    for control in buttons(children):
        control.on_press(control)
    assert pressed == [(TUTOR.action, buttons(children)[0]), (DETAIL.action, buttons(children)[1]),
                       *[(button.action, control) for button, control in zip(RATINGS, buttons(children)[2:])]]


def test_an_answer_without_a_speaker_stands_alone(fake_ui):
    answer = View(text="正在读书")
    children = answer_pages.feedback_page(page_model(), answer=answer, speaker=None, notes=[],
                                          on_action=lambda action, widget: None)
    assert drawn(children)[:2] == [("verdict", "判定", {"result": RESULT, "structure_error": None}), answer]
    assert children[1] is answer


def test_every_optional_line_in_its_place_and_save_alone_without_ratings(fake_ui):
    answer, notes = View(text="答案"), [View(text="我的笔记"), View(text="笔记内容")]
    result = {"correct": None, "outcome": "unresolved"}
    pressed, on_action = recorder()
    page = page_model(result=result, structure_error="sentence_end", asked="今、本を読んでいます。",
                      asked_role="study", needs_teaching="先补学这项内容，再安排独立练习。", entered_shown="已经结束",
                      diagnosis="音高核位置不对", explanation="合成结果说明", unresolved="本次不调整复习。",
                      ratings=None, save=SAVE)
    children = answer_pages.feedback_page(page, answer=answer, speaker=None, notes=notes, on_action=on_action)
    assert drawn(children) == [
        ("verdict", "判定", {"result": result, "structure_error": "sentence_end"}),
        answer,
        ("label", "今、本を読んでいます。", {"size": 15, "color": MUTED, "text_role": "study"}),
        ("label", "先补学这项内容，再安排独立练习。", {"size": 14, "color": MUTED}),
        ("row", "", {}), ("label", "我的回答：", {"size": 12, "color": MUTED, "width": 84.0}),
        ("label", "已经结束", {"size": 12, "color": MUTED, "flex": 1, "text_role": "ui"}),
        ("label", "音高核位置不对", {"size": 13, "color": MUTED}),
        ("label", "合成结果说明", {}),
        *notes,
        *HELP_ROW,
        ("label", "本次不调整复习。", {"size": 13, "color": MUTED}),
        ("button", "保存并继续", {"primary": True})]
    last = buttons(children)[-1]
    last.on_press(last)
    assert pressed == [(SAVE.action, last)]


def test_an_empty_asked_line_is_not_drawn(fake_ui):
    children = answer_pages.feedback_page(page_model(asked="", asked_role=None), answer=View(text="答案"),
                                          speaker=None, notes=[], on_action=lambda action, widget: None)
    assert [entry for entry in drawn(children) if isinstance(entry, tuple) and entry[0] == "label"] == []


# --- the detail page -----------------------------------------------------------------------------------------

def test_the_detail_page(fake_ui):
    rows = [View(text="错误详情"), View(text="讲解")]
    pressed, on_action = recorder()
    children = answer_pages.feedback_detail_page(detail_model({"title": "学校", "family": "word"}), rows=rows,
                                                 on_action=on_action)
    assert drawn(children) == [("label", "学校", {"size": 22, "text_role": "study"}), *rows,
                               ("button", "返回结果", {"primary": True})]
    children[-1].on_press(children[-1])
    assert pressed == [(AnswerAction(Kind.BACK), children[-1])]


def test_the_page_functions_take_only_a_model_handed_in_controls_and_one_callback():
    tree = ast.parse(Path(answer_pages.__file__).read_text("utf-8"))
    signatures = {node.name: ast.unparse(node.args) for node in tree.body
                  if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}
    assert signatures == {
        "feedback_page": "model: FeedbackModel, *, answer, speaker, notes: Sequence, on_action: OnAction",
        "feedback_detail_page": "model: DetailModel, *, rows: Sequence, on_action: OnAction"}


# --- the contract and the imports ----------------------------------------------------------------------

UI_DIR = Path(answer_pages.__file__).parent
PACKAGE_DIR = UI_DIR.parent


def _imports(path, package):
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package.split(".")[:len(package.split(".")) - node.level + 1] if node.level else []
            names.add(".".join([*base, node.module] if node.module else base))
    return names


def test_the_answer_page_module_holds_no_app_store_or_service():
    assert_page_module_holds_no_app_store_or_service(UI_DIR / "answer_pages.py")


def test_the_pure_modules_and_the_page_module_import_exactly_their_registered_modules():
    assert _imports(PACKAGE_DIR / "answer_model.py", "kanamemory.course_line") == {"__future__", "dataclasses", "enum"}
    assert _imports(PACKAGE_DIR / "answer_display.py", "kanamemory.course_line") == {
        "__future__", "dataclasses", "re", "kanamemory.course_line.answer_model"}
    assert _imports(UI_DIR / "answer_pages.py", "kanamemory.course_line.ui") == {
        "__future__", "collections.abc", "kanamemory.ui_components", "kanamemory.answer_feedback",
        "kanamemory.answer_result_ui", "kanamemory.course_line.answer_display",
        "kanamemory.course_line.answer_model"}
