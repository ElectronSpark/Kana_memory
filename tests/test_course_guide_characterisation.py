"""引导题 (the guide page of a course lesson) and the buttons of the introduction page, pinned as they are.

What 跟着做一题 / 跟着练习 write and draw next; how each input mode of a guide keeps its draft (saved before the
page changes, a refused save named on the page, the result cleared by an edit); the demonstration; what a guide
answer records and shows; "seen, go on" after a wrong answer; finishing a guide or one step of a guided
sequence; why a control of an earlier drawing of the same guide does nothing; the answer played aloud once;
Back to a kept guide page and a restart in the middle of a guide. The teaching code is about to move; these
tests reach its state and its seams only through tests/support/course_support.py, so the move changes that
accessor and not these tests. Expected values are what the code does, including what looks odd (each such test
says so).

Public: synthetic lessons only (ids "<family>:synthetic-guide:*"), the synthetic pitch lesson of
support.learning_pitch_controls_support and the synthetic gap lesson of support.multi_cloze_integration_support.
"""
from copy import deepcopy
from types import MethodType, SimpleNamespace

import pytest

from kanamemory import ui_components as u
from kanamemory.learning_store import LearningStore
from kanamemory.learning_ui import LEARNING_PREFS, LearningUIMixin
from support import course_support as course_line
from support.app_fixtures import question_app, setup_app  # noqa: F401
from support.learning_fixtures import NOW, learning_app, use_real_detail_back  # noqa: F401
from support.learning_pitch_controls_support import synthetic_item as pitch_lesson
from support.multi_cloze_integration_support import background_lesson, lesson as gap_lesson

EXPLANATION = "合成说明。"
REQUEST = "相手への丁寧な依頼です。"
PASS, STEP = "看过了，继续", "这一步看过了，下一步"
SAVE_FAILED = "未能保存本次作答，请重试。"
NO_INPUT = "这道旧引导缺少输入控件，可跟随示范完成；不计入掌握程度。"
SPEAKER = {"compact": True, "width": 52, "height": 52, "font_size": 16, "margin_left": 6, "margin_bottom": 0}
RIGHT_MARK = [("row", "", {"align_items": "center", "background_color": "#E1F3E6", "padding": 4, "margin_bottom": 4}),
              ("label", "✓", {"size": 30, "color": "#176B3A", "bold": True, "text_align": "center",
                              "margin_bottom": 0, "flex": 1})]
WRONG_MARK = [("row", "", {"align_items": "center", "background_color": "#FCE6E4", "padding": 4, "margin_bottom": 4}),
              ("label", "×", {"size": 30, "color": "#B3261E", "bold": True, "text_align": "center",
                              "margin_bottom": 0, "flex": 1})]


# --- synthetic lessons ------------------------------------------------------------------------------------

def word_lesson(cid="word:synthetic-guide:cat", **fields):
    """A word lesson guided by one plain meaning question (猫 -> 猫咪, not 小狗)."""
    return {"id": cid, "revision": "v1", "family": "word", "title": "猫", "reading": "ねこ", "meaning": "猫",
            "abilities": ["understanding"], "depends_on": [],
            "instruction": {"rule": "合成词汇说明。", "example": "猫がいます。"},
            "question_templates": {"vocab_meaning": [
                {"prompt": "猫", "answer": "猫咪", "choices": ["猫咪", "小狗"], "input_mode": "choice",
                 "task": "选出意思。", "feedback": EXPLANATION}]}, **fields}


def practice(prompt, answer, *, context, mode="choice", task="空白を埋めてください。", choices=()):
    """A reviewed practice template (a guide that is practised step by step)."""
    quote = "（　）" if "（　）" in prompt else prompt[:1]
    start = prompt.index(quote)
    return {"prompt": prompt, "answer": answer, "task": task, "input_mode": mode,
            "choices": list(choices) if mode != "text" else [], "strict": True, "feedback": REQUEST,
            "practice": {"schema_version": 1,
                         "task_family": "contextual_completion" if mode == "choice" else "form_transformation",
                         "context_id": context, "scaffold": "recognition" if mode == "choice" else "cued",
                         "target_spans": [{"start": start, "end": start + len(quote), "quote": quote}],
                         "background_ids": [], "reviewed": True}}


def grammar_lesson(templates, cid):
    return {"id": cid, "revision": "v1", "family": "grammar", "title": "请求", "abilities": ["application"],
            "depends_on": [], "instruction": {"rule": "向对方提出请求。", "example": "窓を開けてください。"},
            "question_templates": templates}


def two_steps():
    """A guided sequence of two: a choice (窓を開けて（　）。 -> ください) and a typed request."""
    return grammar_lesson({
        "grammar_cloze": [practice("窓を開けて（　）。", "ください", context="window", choices=["ください", "います"])],
        "grammar_transform": [practice("ドアを閉める → 丁寧な依頼", "ドアを閉めてください。", context="door",
                                       mode="text", task="只写变换后的请求句。")]}, "grammar:synthetic-guide:steps")


def typed(answer="ドアを閉めてください。"):
    """One typed practice guide (no sequence). An answer with a character the keys cannot type (扉) has no
    input control."""
    return grammar_lesson({"grammar_transform": [practice("ドアを閉める → 丁寧な依頼", answer, context="door",
                                                          mode="text", task="只写变换后的请求句。")]},
                          "grammar:synthetic-guide:typed")


def blocks():
    """One practice guide answered by putting three blocks together."""
    return grammar_lesson({"grammar_reorder": [practice("ドアを閉める → 丁寧な依頼", "ドアを閉めてください。",
                                                        context="door", mode="reorder", task="组成请求句。",
                                                        choices=["ドアを", "閉めて", "ください。"])]},
                          "grammar:synthetic-guide:blocks")


# --- states -----------------------------------------------------------------------------------------------

@pytest.fixture
def app(learning_app):
    return learning_app


def enrol(app, lesson, *, orientation=None, familiar=True):
    if orientation is not None:
        course_line.set_orientation(app, orientation)
    app.store.set_setting(LEARNING_PREFS, {"enabled": list(lesson["question_templates"])})
    app.learning_store.enroll(lesson, now=NOW)
    if familiar:
        app.learning_store.set_initial_familiarity(lesson["id"], {a: "new" for a in lesson["abilities"]})


def show(app, cid):
    """Draw the lesson's teaching step from what is saved, as the line does."""
    app.show_learning_instruction(app.learning_store.get_item(cid))
    return course_line.guide_question(app)


def guide(app, lesson, **options):
    """The lesson's guide page, opened as after its introduction."""
    enrol(app, lesson, **options)
    app.learning_store.advance_instruction(lesson["id"], "intro_done", now=NOW)
    q = show(app, lesson["id"])
    assert app.page == "learning_guide"
    return q


def pitch_guide(app):
    lesson = pitch_lesson()
    app.learning_store.enroll(lesson, now=NOW)
    app.learning_store.advance_instruction(lesson["id"], "intro_done", now=NOW)
    q = show(app, lesson["id"])
    assert q["input_mode"] == "pitch_fill"
    return lesson, q


@pytest.fixture
def gap_catalogue(monkeypatch, request):
    """The synthetic gap lesson and its background lesson registered as the catalogue."""
    from kanamemory import (learning_conditions, learning_content, learning_dependencies, learning_questions,
                            question_support, structured_lessons)
    authored, background = gap_lesson(), background_lesson()
    monkeypatch.setattr(learning_content, "_ITEMS", {authored["id"]: deepcopy(authored),
                                                      background["id"]: deepcopy(background)})
    monkeypatch.setattr(learning_questions, "_PRACTICE", {"schema_version": 1, "items": {}})
    for cached in (learning_conditions._reviewed_index, learning_dependencies._bundled_dependency_signature,
                   learning_dependencies._bundled_fingerprint, question_support._background_word_senses,
                   learning_questions._word_pool, structured_lessons.catalogue):
        cached.cache_clear()
        request.addfinalizer(cached.cache_clear)
    # The gap buttons carry a style, as native ones do.
    button = u.button
    monkeypatch.setattr(u, "button", lambda text, handler, **kw: button(text, handler, **dict(kw, style=SimpleNamespace())))
    return authored


def saved(app, cid):
    return app.learning_store.get_item(cid)


def writes(log):
    """The teaching writes of a store-call log."""
    return course_line.calls(log, "advance_instruction", "advance_guided_sequence")


def record_writes(app, monkeypatch, log=None, fail=None):
    """Record the teaching writes (and the reads named) with the guide's unsent draft at each write;
    ``fail``: an exception the next advance_instruction raises instead of writing."""
    log = [] if log is None else log
    real = app.learning_store.advance_instruction
    seen = []

    def advance(*args, **kwargs):
        seen.append(deepcopy(course_line.guide_draft(app)))
        if fail is not None:
            raise fail
        return real(*args, **kwargs)
    course_line.record_store_calls(app, monkeypatch, "advance_instruction", "advance_guided_sequence",
                                   "instruction_item", log=log, answers={"advance_instruction": advance})
    return log, seen


def old_controls(app):
    """The pressable controls of the page drawn now, by caption (kept before the page is drawn again)."""
    return {view.text: view for view in course_line.controls(app)}


def hand_backs(app, monkeypatch, log=None):
    """Record the teaching step drawn again (it still draws) and the hand-back to the line (it only records)."""
    log = [] if log is None else log
    course_line.record_entries(app, monkeypatch, real=True, log=log, names=("show_learning_instruction",))
    course_line.record_entries(app, monkeypatch, real=False, log=log, names=("continue_learning",))
    return log


# --- the introduction page's buttons ------------------------------------------------------------------------

def test_follow_one_writes_intro_done_reads_the_lesson_again_and_shows_its_teaching_step(app, monkeypatch):
    """跟着做一题 (a lesson without practice templates, primary): intro_done is written, the lesson is read
    again and its teaching step is drawn with what was read: the guide page."""
    course_line.record_controls(monkeypatch)
    lesson = word_lesson()
    enrol(app, lesson)
    show(app, lesson["id"])
    assert course_line.page(app) == ("learning_intro", "初次学习")
    assert ("button", "跟着做一题", {"primary": True}) in course_line.page_made_with(app)
    log, _ = record_writes(app, monkeypatch)
    hand_backs(app, monkeypatch, log=log)
    course_line.press(app, "跟着做一题")
    shown = [call for call in log if call.name == "show_learning_instruction"]
    assert course_line.calls(log, "advance_instruction", "instruction_item", "show_learning_instruction") == [
        ("advance_instruction", (lesson["id"], "intro_done"), {"instruction_scope": None}),
        ("instruction_item", (lesson["id"], None), {}),
        ("show_learning_instruction", (shown[0].args[0],), {})]
    assert shown[0].args[0]["stage"] == "guide"
    assert course_line.page(app) == ("learning_guide", "跟着做")


def test_follow_practice_is_offered_for_a_lesson_with_practice_and_freezes_its_sequence(app, monkeypatch):
    """跟着练习 (practice templates, primary): intro_done, the lesson read again, the teaching step; that step
    then freezes the guided sequence of two (guide, with the first step as the guide question)."""
    course_line.record_controls(monkeypatch)
    lesson = two_steps()
    enrol(app, lesson)
    show(app, lesson["id"])
    assert ("button", "跟着练习", {"primary": True}) in course_line.page_made_with(app)
    log, _ = record_writes(app, monkeypatch)
    course_line.press(app, "跟着练习")
    assert [(name, args[1]) for name, args, _ in writes(log)] == [("advance_instruction", "intro_done"),
                                                                  ("advance_instruction", "guide")]
    checkpoint = saved(app, lesson["id"])["checkpoint"]
    assert len(checkpoint["guide_sequence"]) == 2 and checkpoint["guide_index"] == 0
    assert course_line.guide_question(app) == checkpoint["guide_sequence"][0]


def test_later_leaves_the_line_and_writes_nothing(app, monkeypatch):
    """稍后再学 calls leave_learning with the button; nothing is written."""
    lesson = word_lesson()
    enrol(app, lesson)
    log, _ = record_writes(app, monkeypatch)
    course_line.watch(app, monkeypatch, names=("leave_learning",), log=log)
    show(app, lesson["id"])
    del log[:]
    widget = course_line.press(app, "稍后再学")
    assert course_line.calls(log, "leave_learning", "advance_instruction") == [("leave_learning", (widget,), {})]


def test_the_prerequisite_note_opens_its_page_and_back_to_teaching_draws_the_same_lesson(app, monkeypatch):
    """还不熟悉接续？ (compact) opens 接续说明 with each line of the note as a point; 返回讲解 (primary) draws the
    teaching step of the lesson the pages are about."""
    course_line.record_controls(monkeypatch)
    lesson = word_lesson(instruction={"rule": "合成词汇说明。", "example": "猫がいます。",
                                      "prerequisite": "先学甲\n先学乙"})
    enrol(app, lesson)
    show(app, lesson["id"])
    assert ("button", "还不熟悉接续？", {"compact": True}) in course_line.page_made_with(app)
    course_line.press(app, "还不熟悉接续？")
    assert course_line.page(app) == ("learning_prerequisite", "接续说明")
    assert {"先学甲", "先学乙"} <= {text.lstrip("• ") for text in course_line.texts(app)}
    assert ("button", "返回讲解", {"primary": True}) in course_line.page_made_with(app)
    log = course_line.watch(app, monkeypatch, names=("show_learning_instruction",), real=False)
    course_line.press(app, "返回讲解")
    assert course_line.calls(log, "show_learning_instruction") == [
        ("show_learning_instruction", (course_line.taught_item(app),), {})]


def test_a_focused_reading_lesson_folds_its_explanation_and_the_toggle_writes_nothing(app, monkeypatch):
    """A word taught for its reading only: the explanation is folded behind 展开讲解 (compact); pressing shows
    it and names the button 收起讲解, pressing again folds it; nothing is written."""
    course_line.record_controls(monkeypatch)
    lesson = word_lesson(abilities=["reading"], question_templates={"vocab_reading": [
        {"prompt": "猫", "answer": "ねこ", "choices": [], "input_mode": "text", "task": "写出读音。",
         "feedback": EXPLANATION}]})
    enrol(app, lesson)
    show(app, lesson["id"])
    assert app.page == "learning_intro"
    assert ("button", "展开讲解", {"compact": True}) in course_line.page_made_with(app)
    folded = course_line.texts(app)
    log, _ = record_writes(app, monkeypatch)
    toggle = course_line.press(app, "展开讲解")
    assert toggle.text == "收起讲解" and "• 合成词汇说明。" in course_line.texts(app)
    toggle.on_press(toggle)
    assert toggle.text == "展开讲解" and course_line.texts(app) == folded
    assert writes(log) == []


# --- the draft of each input mode --------------------------------------------------------------------------

def test_typing_saves_the_draft_before_the_page_holds_it(app, monkeypatch):
    """A typed guide: each change writes guide with the frozen question, the draft, no answer and not
    correct; at that moment the page still holds the earlier draft; afterwards it holds the new one, the
    status line is empty and 提交 is enabled."""
    lesson = typed()
    q = guide(app, lesson)
    log, seen = record_writes(app, monkeypatch)
    course_line.type_into(course_line.guide_field(app), "ドア")
    assert writes(log) == [("advance_instruction", (lesson["id"], "guide"), {"state": {
        "guide_question": q, "guide_draft": {"answer": "ドア"}, "guide_answer": None, "guide_correct": False},
        "instruction_scope": None})]
    assert seen == [{}]
    assert course_line.guide_draft(app) == {"answer": "ドア"} and course_line.guide_status(app) == ""
    assert course_line.control(app, "提交").enabled is True


def test_a_refused_draft_is_named_on_the_page_and_the_page_keeps_the_earlier_draft(app, monkeypatch):
    """A save refused by the store (ValueError) shows 暂未保存：<reason>; the page keeps the earlier draft, 提交
    stays disabled and the result column is left as it was."""
    lesson = typed()
    guide(app, lesson)
    course_line.type_into(course_line.guide_field(app), "ドア")
    course_line.press(app, "提交")
    result = course_line.guide_result_texts(app)
    assert "×" in result
    record_writes(app, monkeypatch, fail=ValueError("合成原因"))
    course_line.type_into(course_line.guide_field(app), "ドアを")
    assert course_line.guide_status(app) == "暂未保存：合成原因"
    assert course_line.guide_draft(app) == {"answer": "ドア"}
    assert course_line.guide_result_texts(app) == result


def test_an_edit_after_a_wrong_answer_clears_the_result(app, monkeypatch):
    """After a wrong typed answer the result column shows it; the next saved edit empties the column."""
    lesson = typed()
    guide(app, lesson)
    course_line.type_into(course_line.guide_field(app), "ドア")
    course_line.press(app, "提交")
    assert course_line.guide_result_texts(app)[:2] == ["×", "ドアを閉めてください。"]
    course_line.type_into(course_line.guide_field(app), "ドアを")
    assert course_line.guide_result_texts(app) == []
    assert saved(app, lesson["id"])["checkpoint"]["guide_answer"] is None


def test_a_practice_choice_is_answered_at_once_with_the_choice_as_its_draft(app, monkeypatch):
    """A practice choice guide: pressing a choice writes no draft first; the answer is written with the draft
    holding that choice (wrong: guide_failed), and the page then holds that draft."""
    lesson = two_steps()
    q = guide(app, lesson)
    log, _ = record_writes(app, monkeypatch)
    course_line.press(app, "います")
    assert writes(log) == [("advance_instruction", (lesson["id"], "guide_failed"), {"state": {
        "guide_answer": "います", "guide_correct": False, "guide_question": q,
        "guide_draft": {"answer": "います"}}, "instruction_scope": None})]
    assert course_line.guide_draft(app) == {"answer": "います"}


def test_blocks_save_each_step_and_undo_and_submit_follows_the_draft(app, monkeypatch):
    """Blocks: each block pressed writes the draft (answer, parts, used) and is disabled; 撤回末项 writes the
    shorter draft; 提交 is enabled only with something chosen; all three blocks in order answer right."""
    lesson = blocks()
    q = guide(app, lesson)
    log, _ = record_writes(app, monkeypatch)
    at = q["choices"].index("ドアを")
    first = course_line.press(app, "ドアを")
    assert course_line.guide_draft(app) == {"answer": "ドアを", "parts": ["ドアを"], "used": [at]}
    assert first.enabled is False and course_line.control(app, "提交").enabled is True
    course_line.press(app, "撤回末项")
    assert course_line.guide_draft(app) == {"answer": "", "parts": [], "used": []}
    assert first.enabled is True and course_line.control(app, "提交").enabled is False
    course_line.press(app, "ドアを")
    course_line.press(app, "閉めて")
    course_line.press(app, "撤回末项")
    assert course_line.guide_draft(app) == {"answer": "ドアを", "parts": ["ドアを"], "used": [at]}
    course_line.press(app, "撤回末项")
    for caption in ("ドアを", "閉めて", "ください。"):
        course_line.press(app, caption)
    course_line.press(app, "提交")
    drafts = [kwargs["state"]["guide_draft"] for _, _, kwargs in writes(log)]
    assert drafts[:2] == [{"answer": "ドアを", "parts": ["ドアを"], "used": [at]},
                          {"answer": "", "parts": [], "used": []}]
    assert writes(log)[-1][1] == (lesson["id"], "guide")
    assert writes(log)[-1][2]["state"]["guide_correct"] is True
    assert writes(log)[-1][2]["state"]["guide_question"] == q


def test_a_gap_choice_saves_the_draft_and_an_unreadable_answer_is_refused(app, monkeypatch, gap_catalogue):
    """Gaps: a choice writes guide with the draft the page then holds; an answer that cannot be read is refused
    with 作答格式不完整，请检查空位。 and nothing is written."""
    lesson = gap_catalogue
    q = guide(app, lesson)
    assert q["input_mode"] == "multi_cloze"
    log, _ = record_writes(app, monkeypatch)
    course_line.press(app, "は")
    assert [(args, sorted(kwargs["state"])) for _, args, kwargs in writes(log)] == [
        ((lesson["id"], "guide"), ["guide_answer", "guide_correct", "guide_draft", "guide_question"])]
    assert writes(log)[0][2]["state"]["guide_draft"] == course_line.guide_draft(app)
    assert course_line.guide_draft(app) != {}
    app.answer_learning_guide("unreadable", draft=course_line.guide_draft(app), question_id=q["id"])
    assert course_line.guide_status(app) == "作答格式不完整，请检查空位。"
    assert len(writes(log)) == 1


def test_a_pitch_answer_is_written_with_its_draft(app, monkeypatch):
    """The pitch editor: a wrong pitch submitted writes guide_failed with the answer, the question and the
    editor's draft."""
    lesson, q = pitch_guide(app)
    log, _ = record_writes(app, monkeypatch)
    for _ in range(3):
        course_line.press(app, "▁")
    course_line.press(app, "提交")
    name, args, kwargs = writes(log)[-1]
    assert [call[1][1] for call in writes(log)] == ["guide"] * (len(writes(log)) - 1) + ["guide_failed"]
    assert args == (lesson["id"], "guide_failed") and kwargs["instruction_scope"] is None
    state = kwargs["state"]
    assert sorted(state) == ["guide_answer", "guide_correct", "guide_draft", "guide_question"]
    assert state["guide_correct"] is False and state["guide_question"] == q and state["guide_draft"]


# --- the demonstration -------------------------------------------------------------------------------------

def test_the_first_step_shows_its_demonstration_and_the_second_offers_it(app, monkeypatch):
    """The first step of a sequence shows 示范 with the answer; the second offers 看示范 (compact, font 12,
    120 x 40) and hides its answer."""
    course_line.record_controls(monkeypatch)
    lesson = two_steps()
    guide(app, lesson)
    assert "示范" in course_line.texts(app) and "ください" in course_line.texts(app)
    course_line.press(app, "ください")
    course_line.press(app, "继续")
    second = course_line.guide_question(app)
    assert ("button", "看示范", {"compact": True, "font_size": 12, "width": 120, "height": 40}) in \
        course_line.page_made_with(app)
    assert second["answer"] not in course_line.texts(app)


def second_step(app):
    lesson = two_steps()
    guide(app, lesson)
    course_line.press(app, "ください")
    course_line.press(app, "继续")
    return lesson, course_line.guide_question(app)


def test_opening_the_demonstration_saves_it_and_a_new_drawing_shows_it_open(app, monkeypatch):
    """看示范 writes guide with the question and the draft marked demonstration_open (no answer fields), shows
    本题示范 with the answer; drawn again, the demonstration is open and 看示范 is gone."""
    lesson, q = second_step(app)
    course_line.type_into(course_line.guide_field(app), "ドア")
    log, _ = record_writes(app, monkeypatch)
    course_line.press(app, "看示范")
    assert writes(log) == [("advance_instruction", (lesson["id"], "guide"), {"state": {
        "guide_question": q, "guide_draft": {"answer": "ドア", "demonstration_open": True}},
        "instruction_scope": None})]
    assert course_line.guide_draft(app) == {"answer": "ドア", "demonstration_open": True}
    assert "本题示范" in course_line.texts(app) and q["answer"] in course_line.texts(app)
    show(app, lesson["id"])
    assert "本题示范" in course_line.texts(app) and "看示范" not in course_line.captions(app)


def test_a_refused_demonstration_is_named_and_stays_closed(app, monkeypatch):
    """A refused 看示范 shows 暂未保存：<reason>; the demonstration stays closed and the draft unchanged."""
    lesson, q = second_step(app)
    record_writes(app, monkeypatch, fail=KeyError("合成"))
    course_line.press(app, "看示范")
    assert course_line.guide_status(app) == "暂未保存：'合成'"
    assert "本题示范" not in course_line.texts(app) and course_line.guide_draft(app) == {}


def test_a_guide_without_an_input_control_is_followed_from_its_demonstration(app, monkeypatch):
    """A typed guide whose answer the keys cannot type shows the note (13, muted) and 跟随示范完成 (primary)
    instead of the field; pressing it writes the draft with the answer, demonstration_open and the draft as it
    was, then the answer itself as right."""
    course_line.record_controls(monkeypatch)
    lesson = typed("扉を閉めてください。")
    q = guide(app, lesson)
    made = course_line.page_made_with(app)
    assert ("label", NO_INPUT, {"size": 13, "color": u.MUTED}) in made
    assert ("button", "跟随示范完成", {"primary": True}) in made
    assert course_line.captions(app) == ["跟随示范完成"]
    log, _ = record_writes(app, monkeypatch)
    course_line.press(app, "跟随示范完成")
    draft = {"answer": q["answer"], "demonstration_open": True, "input_unavailable_draft": {}}
    assert writes(log) == [
        ("advance_instruction", (lesson["id"], "guide"), {"state": {
            "guide_question": q, "guide_draft": draft, "guide_answer": None, "guide_correct": False},
            "instruction_scope": None}),
        ("advance_instruction", (lesson["id"], "guide"), {"state": {
            "guide_answer": q["answer"], "guide_correct": True, "guide_question": q, "guide_draft": draft},
            "instruction_scope": None})]
    assert course_line.guide_locked(app) is True


# --- a guide answer ----------------------------------------------------------------------------------------

def test_an_empty_answer_is_refused_with_its_message_and_nothing_is_written(app, monkeypatch):
    """An empty typed answer, submitted as the page submits it: 请先作答。 in the status line, nothing written,
    no result."""
    q = guide(app, typed())
    log, _ = record_writes(app, monkeypatch)
    app.answer_learning_guide("", draft=course_line.guide_draft(app), question_id=q["id"])
    assert course_line.guide_status(app) == "请先作答。"
    assert writes(log) == [] and course_line.guide_result_texts(app) == []


def test_a_right_plain_guide_answer_is_written_without_a_draft(app, monkeypatch):
    """A plain choice guide answered right writes guide with the answer, correct and the question (no draft)."""
    lesson = word_lesson()
    q = guide(app, lesson)
    log, _ = record_writes(app, monkeypatch)
    course_line.press(app, "猫咪")
    assert writes(log) == [("advance_instruction", (lesson["id"], "guide"), {"state": {
        "guide_answer": "猫咪", "guide_correct": True, "guide_question": q}, "instruction_scope": None})]


def test_a_wrong_plain_guide_answer_is_written_as_guide_failed(app, monkeypatch):
    lesson = word_lesson()
    q = guide(app, lesson)
    log, _ = record_writes(app, monkeypatch)
    course_line.press(app, "小狗")
    assert writes(log) == [("advance_instruction", (lesson["id"], "guide_failed"), {"state": {
        "guide_answer": "小狗", "guide_correct": False, "guide_question": q}, "instruction_scope": None})]
    assert saved(app, lesson["id"])["stage"] == "guide"


def test_a_typed_answer_is_written_with_its_draft_holding_the_answer(app, monkeypatch):
    """A typed guide: the answer is written with the draft, whose answer is the submitted one."""
    lesson = typed()
    q = guide(app, lesson)
    course_line.type_into(course_line.guide_field(app), "ドアを")
    log, _ = record_writes(app, monkeypatch)
    app.answer_learning_guide("ドアを閉めてください。", draft={"answer": "ドアを"}, question_id=q["id"])
    assert writes(log) == [("advance_instruction", (lesson["id"], "guide"), {"state": {
        "guide_answer": "ドアを閉めてください。", "guide_correct": True, "guide_question": q,
        "guide_draft": {"answer": "ドアを閉めてください。"}}, "instruction_scope": None})]
    assert course_line.guide_draft(app) == {"answer": "ドアを閉めてください。"}


def test_a_failing_save_of_an_answer_is_named_noted_and_shows_no_result(app, monkeypatch):
    """Any error while the answer is written: 未能保存本次作答，请重试。, the error noted as (learning_guide,
    submit), no result, the controls not locked."""
    q = guide(app, typed())
    course_line.type_into(course_line.guide_field(app), "ドア")
    error = RuntimeError("synthetic failure")
    errors = course_line.record_diagnostic_errors(monkeypatch)
    record_writes(app, monkeypatch, fail=error)
    course_line.press(app, "提交")
    assert course_line.guide_status(app) == SAVE_FAILED
    assert [call.args for call in errors] == [("learning_guide", "submit", error)]
    assert course_line.guide_result_texts(app) == [] and course_line.guide_locked(app) is False
    assert course_line.guide_question(app) is q


def test_a_right_practice_answer_locks_the_controls(app, monkeypatch):
    """After a right practice answer the page is locked, every answer control is disabled and a further
    choice writes nothing."""
    lesson = two_steps()
    guide(app, lesson)
    course_line.press(app, "ください")
    assert course_line.guide_locked(app) is True
    assert [control.enabled for control in course_line.guide_answer_controls(app)] == [False, False]
    log, _ = record_writes(app, monkeypatch)
    course_line.press(app, "います")
    assert writes(log) == []


def test_a_plain_guide_is_not_locked_and_a_later_choice_overwrites_a_right_answer(app, monkeypatch):
    """Looks odd, pinned as it is: a plain choice guide has no lock; after a right answer, pressing the other
    choice records it as guide_failed and the result turns wrong."""
    lesson = word_lesson()
    guide(app, lesson)
    course_line.press(app, "猫咪")
    log, _ = record_writes(app, monkeypatch)
    course_line.press(app, "小狗")
    assert [args for _, args, _ in writes(log)] == [(lesson["id"], "guide_failed")]
    assert saved(app, lesson["id"])["checkpoint"]["guide_correct"] is False
    assert course_line.guide_result_texts(app)[0] == "×"


def test_the_result_of_a_right_plain_answer(app, monkeypatch):
    """Right: the mark, the explanation (flex 1) beside the speaker, 继续 (primary)."""
    course_line.record_controls(monkeypatch)
    guide(app, word_lesson())
    course_line.press(app, "猫咪")
    assert course_line.guide_result_made_with(app) == [("col", "", {}), *RIGHT_MARK, ("row", "", {}),
        ("label", EXPLANATION, {"flex": 1}), ("button", "🔊", SPEAKER), ("button", "继续", {"primary": True})]


def test_the_result_of_a_wrong_plain_answer_under_depth_first(app, monkeypatch):
    """Wrong, depth first: the mark, the right answer (bold, flex 1) beside the speaker, the explanation; no
    way on."""
    course_line.record_controls(monkeypatch)
    guide(app, word_lesson())
    course_line.press(app, "小狗")
    assert course_line.guide_result_made_with(app) == [("col", "", {}), *WRONG_MARK, ("row", "", {}),
        ("label", "猫咪", {"bold": True, "flex": 1, "text_role": "ui"}), ("button", "🔊", SPEAKER),
        ("label", EXPLANATION, {})]


def test_the_result_of_a_practice_answer(app, monkeypatch):
    """A practice step answered right: the mark, the explanation (flex 1) beside the speaker, 继续 (primary)."""
    course_line.record_controls(monkeypatch)
    guide(app, two_steps())
    course_line.press(app, "ください")
    made = course_line.guide_result_made_with(app)
    assert made[0] == ("col", "", {}) and made[1:3] == RIGHT_MARK
    assert made[3:] == [("row", "", {}), ("label", REQUEST, {"flex": 1}), ("button", "🔊", SPEAKER),
                        ("button", "继续", {"primary": True})]


def test_the_result_of_a_wrong_practice_answer(app, monkeypatch):
    """A practice step answered wrong, depth first: the mark, the right answer (bold, flex 1) beside the
    speaker, the explanation."""
    course_line.record_controls(monkeypatch)
    guide(app, two_steps())
    course_line.press(app, "います")
    made = course_line.guide_result_made_with(app)
    assert made[1:3] == WRONG_MARK
    assert made[3:] == [("row", "", {}), ("label", "ください", {"bold": True, "flex": 1, "text_role": "study"}),
                        ("button", "🔊", SPEAKER), ("label", REQUEST, {})]


# --- "seen, go on" -----------------------------------------------------------------------------------------

def test_seen_go_on_is_offered_after_a_wrong_answer_only_under_breadth_first(app, monkeypatch):
    """Breadth first: 看过了，继续 (no style) after a wrong answer of a single guide, 这一步看过了，下一步 on a step
    that is not the last; depth first: neither."""
    course_line.record_controls(monkeypatch)
    guide(app, word_lesson(), orientation="breadth")
    course_line.press(app, "小狗")
    assert course_line.guide_result_made_with(app)[-1] == ("button", PASS, {})


def test_a_step_of_a_sequence_is_seen_with_its_own_caption(app):
    guide(app, two_steps(), orientation="breadth")
    course_line.press(app, "います")
    assert course_line.guide_result_texts(app)[-1] == STEP and PASS not in course_line.guide_result_texts(app)


@pytest.mark.parametrize("orientation", ["thorough", None])
def test_no_seen_go_on_under_depth_first(app, orientation):
    guide(app, two_steps(), orientation=orientation)
    course_line.press(app, "います")
    assert {STEP, PASS}.isdisjoint(course_line.guide_result_texts(app))


def test_seen_go_on_moves_on_with_bridge_and_the_guide_stays_not_right(app, monkeypatch):
    """Pressed: bridge is written (fair_new as the app's, False here) and the line goes on with
    continue_learning(); the lesson is in its bridge stage with guide_correct false."""
    lesson = word_lesson()
    guide(app, lesson, orientation="breadth")
    course_line.press(app, "小狗")
    log, _ = record_writes(app, monkeypatch)
    hand_backs(app, monkeypatch, log=log)
    course_line.press(app, PASS)
    assert course_line.calls(log, "advance_instruction", "advance_guided_sequence", "continue_learning",
                             "continue_study_path") == [
        ("advance_instruction", (lesson["id"], "bridge"), {"fair_new": False, "instruction_scope": None}),
        ("continue_learning", (), {})]
    after = saved(app, lesson["id"])
    assert after["stage"] == "bridge" and after["checkpoint"]["guide_correct"] is False


def test_seen_go_on_of_a_step_goes_to_the_next_step(app, monkeypatch):
    """On a step that is not the last: the sequence is advanced with the step's id and the next step drawn."""
    lesson = two_steps()
    first = guide(app, lesson, orientation="breadth")
    course_line.press(app, "います")
    log, _ = record_writes(app, monkeypatch)
    course_line.press(app, STEP)
    assert writes(log) == [("advance_guided_sequence", (lesson["id"],),
                            {"question_id": first["id"], "instruction_scope": None})]
    assert course_line.guide_question(app)["input_mode"] == "text" and app.page == "learning_guide"


def test_seen_go_on_after_the_orientation_changed_draws_the_page_again(app, monkeypatch):
    """Depth first chosen meanwhile: pressing writes nothing; the lesson is read again and its teaching step
    drawn again, now without the button."""
    lesson = word_lesson()
    guide(app, lesson, orientation="breadth")
    course_line.press(app, "小狗")
    course_line.set_orientation(app, "thorough")
    log, _ = record_writes(app, monkeypatch)
    hand_backs(app, monkeypatch, log=log)
    course_line.press(app, PASS)
    shown = [call for call in log if call.name == "show_learning_instruction"]
    assert course_line.calls(log) == [("instruction_item", (lesson["id"], None), {}),
                                      ("show_learning_instruction", (shown[0].args[0],), {})]
    assert app.page == "learning_guide" and PASS not in course_line.captions(app)


def test_seen_go_on_after_the_answer_changed_draws_the_page_again(app, monkeypatch):
    """A right answer saved meanwhile: pressing writes nothing and the page is drawn again with that result."""
    lesson = word_lesson()
    q = guide(app, lesson, orientation="breadth")
    course_line.press(app, "小狗")
    app.learning_store.advance_instruction(lesson["id"], "guide", state={
        "guide_answer": "猫咪", "guide_correct": True, "guide_question": q})
    log, _ = record_writes(app, monkeypatch)
    hand_backs(app, monkeypatch, log=log)
    course_line.press(app, PASS)
    assert writes(log) == [] and "show_learning_instruction" in course_line.names(log)
    assert course_line.guide_result_texts(app)[0] == "✓" and "继续" in course_line.captions(app)


# --- finishing ---------------------------------------------------------------------------------------------

def test_continue_after_a_step_advances_the_sequence_and_draws_the_next_step(app, monkeypatch):
    """继续 on the first step: the saved lesson is read, the sequence advanced with the step's id, and the
    teaching step drawn with the lesson it returns; nothing else is written."""
    lesson = two_steps()
    first = guide(app, lesson)
    course_line.press(app, "ください")
    log, _ = record_writes(app, monkeypatch)
    hand_backs(app, monkeypatch, log=log)
    course_line.press(app, "继续")
    shown = [call for call in log if call.name == "show_learning_instruction"]
    assert course_line.calls(log, "instruction_item", "advance_instruction", "advance_guided_sequence",
                             "show_learning_instruction")[:3] == [
        ("instruction_item", (lesson["id"], None), {}),
        ("advance_guided_sequence", (lesson["id"],), {"question_id": first["id"], "instruction_scope": None}),
        ("show_learning_instruction", (shown[0].args[0],), {})]
    assert shown[0].args[0]["checkpoint"]["guide_index"] == 1
    assert [name for name, _, _ in writes(log)] == ["advance_guided_sequence"]


def test_continue_after_the_last_step_writes_guide_correct_and_hands_back(app, monkeypatch):
    """继续 on the last step: the sequence is advanced (finished), guide_correct written, then
    continue_learning() (no study path here, fair_new False); both steps are completed."""
    lesson, second = second_step(app)
    course_line.type_into(course_line.guide_field(app), second["answer"])
    course_line.press(app, "提交")
    log, _ = record_writes(app, monkeypatch)
    hand_backs(app, monkeypatch, log=log)
    course_line.press(app, "继续")
    assert course_line.calls(log, "advance_instruction", "advance_guided_sequence", "continue_learning",
                             "continue_study_path", "show_learning_instruction") == [
        ("advance_guided_sequence", (lesson["id"],), {"question_id": second["id"], "instruction_scope": None}),
        ("advance_instruction", (lesson["id"], "guide_correct"), {"fair_new": False, "instruction_scope": None}),
        ("continue_learning", (), {})]
    after = saved(app, lesson["id"])
    assert after["stage"] == "bridge" and len(after["checkpoint"]["guide_completed_ids"]) == 2


def test_continue_after_a_single_guide_hands_back_to_the_study_path_with_the_checkpoint_saved(app, monkeypatch):
    """A single guide in an app with a study path: no sequence step; guide_correct with fair_new True, then
    continue_study_path(_checkpoint_saved=True)."""
    lesson = word_lesson()
    guide(app, lesson)
    course_line.press(app, "猫咪")
    presses = course_line.record_presses(app, "continue_study_path")
    log, _ = record_writes(app, monkeypatch)
    course_line.press(app, "继续")
    assert writes(log) == [("advance_instruction", (lesson["id"], "guide_correct"),
                            {"fair_new": True, "instruction_scope": None})]
    assert [(call.name, call.args, call.kwargs) for call in presses] == [
        ("continue_study_path", (), {"_checkpoint_saved": True})]


# --- controls of an earlier drawing ------------------------------------------------------------------------

@pytest.mark.parametrize("make,caption", [(two_steps, "います"), (word_lesson, "小狗")], ids=["practice", "plain"])
def test_a_choice_of_an_earlier_drawing_does_nothing(app, monkeypatch, make, caption):
    """The guide drawn again from the store: a choice pressed on the earlier drawing writes nothing; the same
    choice on the current drawing writes."""
    lesson = make()
    guide(app, lesson)
    old = old_controls(app)
    show(app, lesson["id"])
    log, _ = record_writes(app, monkeypatch)
    old[caption].on_press(old[caption])
    assert writes(log) == []
    course_line.press(app, caption)
    assert [args[1] for _, args, _ in writes(log)] == ["guide_failed"]


def test_the_demonstration_of_an_earlier_drawing_does_nothing(app, monkeypatch):
    lesson, _ = second_step(app)
    old = old_controls(app)
    show(app, lesson["id"])
    log, _ = record_writes(app, monkeypatch)
    old["看示范"].on_press(old["看示范"])
    assert writes(log) == [] and "看示范" in course_line.captions(app)


def test_continue_of_an_earlier_drawing_does_nothing(app, monkeypatch):
    lesson = word_lesson()
    guide(app, lesson)
    course_line.press(app, "猫咪")
    old = old_controls(app)
    show(app, lesson["id"])
    log, _ = record_writes(app, monkeypatch)
    hand_backs(app, monkeypatch, log=log)
    old["继续"].on_press(old["继续"])
    assert course_line.calls(log) == [] and app.page == "learning_guide"


def test_seen_go_on_of_an_earlier_drawing_does_nothing(app, monkeypatch):
    lesson = word_lesson()
    guide(app, lesson, orientation="breadth")
    course_line.press(app, "小狗")
    old = old_controls(app)
    show(app, lesson["id"])
    log, _ = record_writes(app, monkeypatch)
    hand_backs(app, monkeypatch, log=log)
    old[PASS].on_press(old[PASS])
    assert course_line.calls(log) == [] and saved(app, lesson["id"])["stage"] == "guide"


def test_a_guide_control_does_nothing_once_another_page_is_shown(app, monkeypatch):
    """An answer control and 继续 pressed after another page was drawn write nothing and draw nothing."""
    lesson = word_lesson()
    guide(app, lesson)
    course_line.press(app, "猫咪")
    old = old_controls(app)
    app.screen("learning_wait", "别的页面")
    log, _ = record_writes(app, monkeypatch)
    hand_backs(app, monkeypatch, log=log)
    old["小狗"].on_press(old["小狗"])
    old["继续"].on_press(old["继续"])
    assert course_line.calls(log) == [] and app.page == "learning_wait"


# --- the answer played aloud -------------------------------------------------------------------------------

def test_the_answer_is_played_once_per_guide_with_the_introduction_in_its_key(app, monkeypatch):
    """Right or wrong, each drawn result asks to play the answer with the key (learning-guide, the lesson's
    introduction id, the question id); drawing the page again asks again with the same key and nothing is
    played a second time."""
    app.platform.tts_ready = True
    lesson = word_lesson()
    q = guide(app, lesson)
    log = course_line.record_guide_autoplay(monkeypatch)
    course_line.press(app, "小狗")
    key = ("learning-guide", course_line.taught_item(app)["introduction_id"], q["id"])
    assert course_line.taught_item(app)["introduction_id"]
    assert [call.args for call in log] == [("ねこ", key)]
    app.platform.play.assert_awaited_once_with("ねこ")
    show(app, lesson["id"])
    assert [call.args for call in log] == [("ねこ", key), ("ねこ", key)]
    app.platform.play.assert_awaited_once_with("ねこ")


# --- Back to a kept guide page, a restart ------------------------------------------------------------------

def test_back_to_a_kept_guide_page_keeps_its_question_and_draft_and_a_press_there_works(app, monkeypatch):
    """练习设置 opened from a typed guide; Back shows the same guide page with the same question and draft;
    typing there writes the next draft."""
    app = use_real_detail_back(app)
    app.show_learning_settings = MethodType(LearningUIMixin.show_learning_settings, app)
    lesson = typed()
    q = guide(app, lesson)
    course_line.type_into(course_line.guide_field(app), "ドア")
    app.show_learning_settings()
    assert app.page == "learning_settings"
    app.go_back()
    assert app.page == "learning_guide"
    assert course_line.guide_question(app) is q and course_line.guide_field(app).value == "ドア"
    log, _ = record_writes(app, monkeypatch)
    course_line.type_into(course_line.guide_field(app), "ドアを")
    assert [kwargs["state"]["guide_draft"] for _, _, kwargs in writes(log)] == [{"answer": "ドアを"}]
    course_line.press(app, "提交")
    assert [args[1] for _, args, _ in writes(log)] == ["guide", "guide_failed"]


def test_a_restart_in_the_middle_of_a_guide_shows_the_saved_draft_and_answer(app):
    """A new process (a new learner store) draws the guide with the same question, the saved draft in the
    field and the saved wrong answer's result."""
    lesson = typed()
    q = guide(app, lesson)
    course_line.type_into(course_line.guide_field(app), "ドア")
    course_line.press(app, "提交")
    result = course_line.guide_result_texts(app)
    app.learning_store = LearningStore(app.store)
    again = show(app, lesson["id"])
    assert again == q and again is not q
    assert course_line.guide_field(app).value == "ドア" and course_line.guide_draft(app) == {"answer": "ドア"}
    assert course_line.guide_result_texts(app) == result
