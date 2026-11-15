"""讲解与引导 (the course line's teaching step): what a teaching head becomes, the page 开始前 and the stop pages.

When the course line reaches a lesson that still has to be taught, app.show_learning_instruction(item) decides
which of ten things the lesson becomes and takes that step:
  - its frozen guide question waits for a check of updated material (page 引导题待核);
  - its material is not ready (page 学习材料尚不完整 / 材料待核验);
  - a later ability of a lesson introduced by the batch cards (the batch opens that ability);
  - a lesson the open batch owns (the batch page);
  - an untouched lesson (the page 开始前 asks how familiar it is);
  - the guide is paused: no practice kind is left, or the kind of the guide question is switched off (page
    引导已保留);
  - no guide question can be built (page 引导例题暂不可用);
  - the guide page (跟着做);
  - the introduction page (初次学习 / 快速回顾).
On the way it writes four kinds of progress at fixed moments: the shared introduction it skips (shown, then
done), the guided sequence it freezes, the practice question it freezes, and the exposure of an introduction.
This file pins those outcomes, the order of those writes relative to the checks that follow them, that drawing
a guide or introduction page again selects nothing new and writes nothing again, the page 开始前 (what it
prepares, its guards, a lesson with several abilities, the last choice), every control of the stop pages,
Back to a kept page 开始前, and what a problem report captures of the teaching state.

The teaching state is read only through tests/support/course_support.py. Expected values are what the code
does, including what looks odd (each such test says so). Public: synthetic lessons only (ids
"word:synthetic-teach:*"), and lesson dicts built in the test where a state cannot be reached with them.
"""
from copy import deepcopy

import pytest

from kanamemory import feedback_context
from kanamemory import intro_batch
from kanamemory import ui_components as u
from kanamemory.learning_questions import PREFERENCE_VERSION, SOUND_PREFERENCE_VERSION
from support import course_support as course_line
from support.app_fixtures import question_app, setup_app  # noqa: F401
from support.fake_ui import View
from support.learning_fixtures import learning_app, use_real_detail_back  # noqa: F401

PREFS = "learning_preferences_v1"
KINDS = ["vocab_orthography", "vocab_reading"]
ROWS = ["〈接下来一〉", "〈接下来二〉"]
REPLACEMENT = ["〈替换〉"]
HOME = "回到今日"
LEVELS = [("初学", "new"), ("有印象", "some"), ("已熟悉", "familiar")]
WRITES = ("prepare_initial_familiarity", "save_familiarity_draft", "set_initial_familiarity",
          "mark_instruction_exposed", "advance_instruction")
UNAVAILABLE_TEXTS = ["这一项的引导例题暂不可用（旧例题已停用），可直接开始练习，或跳到下一项。",
                     "直接开始练习不会记为已掌握，之后仍按独立作答结果安排复习。"]


# --- states -----------------------------------------------------------------------------------------------

@pytest.fixture
def app(learning_app):
    return learning_app


def template(prompt, answer, context, mode, choices):
    return {"prompt": prompt, "task": "写出目标词读音。" if mode == "text" else "选出目标词写法。",
            "answer": answer, "accepted_answers": [answer], "input_mode": mode, "choices": choices,
            "strict": True, "feedback": "合成测试说明。",
            "practice": {"schema_version": 1,
                         "task_family": "contextual_reading" if mode == "text" else "contextual_orthography",
                         "context_id": context, "scaffold": "retrieval" if mode == "text" else "recognition",
                         "target_spans": [{"start": 0, "end": 1, "quote": prompt[:1]}],
                         "background_ids": [], "reviewed": True}}


def lesson(suffix="cat", *, contexts=1):
    """A synthetic word lesson with two abilities (use, reading); ``contexts`` 2 gives its "use" ability two
    examples in different sentences, so that its guide is a sequence of two."""
    spelling = [template("ねこがいます。", "猫", "syn-a", "choice", ["猫", "犬"]),
                template("ねこが好きです。", "猫", "syn-c", "choice", ["猫", "犬"])][:contexts]
    return {"id": "word:synthetic-teach:" + suffix, "revision": "synthetic-teach-v1", "family": "word", "title": "猫",
            "reading": "ねこ", "meaning": "猫", "abilities": ["use", "reading"], "depends_on": [],
            "instruction": {"rule": "合成词汇说明。", "example": "猫がいます。"},
            "question_templates": {"vocab_orthography": spelling,
                                   "vocab_reading": [template("猫が寝ています。", "ねこ", "syn-b", "text", [])]}}


def enrol(app, suffix="cat", *, contexts=1, kinds=KINDS):
    """Enrol the synthetic lesson (untouched, stage intro) with ``kinds`` switched on; returns its id."""
    app.store.set_setting(PREFS, {"enabled": list(kinds), SOUND_PREFERENCE_VERSION: PREFERENCE_VERSION})
    content = lesson(suffix, contexts=contexts)
    app.learning_store.enroll(content, now=app._test_clock[0])
    return content["id"]


def untouched(app, cid):
    return app.learning_store.get_item(cid)


def opened(app, cid, level="new"):
    """The lesson after the page 开始前 for its "use" ability: still at its introduction."""
    learning = app.learning_store
    item = learning.prepare_initial_familiarity(cid, expected_introduction_id=untouched(app, cid)["introduction_id"],
                                                ability="use")
    return learning.set_initial_familiarity(cid, {"use": level}, expected_introduction_id=item["introduction_id"],
                                            instruction_scope=item.get("instruction_scope"))


def guiding(app, cid):
    """The lesson after its introduction: at its guide, nothing frozen yet."""
    item = opened(app, cid)
    app.learning_store.advance_instruction(cid, "intro_done", instruction_scope=item.get("instruction_scope"))
    return app.learning_store.instruction_item(cid, item.get("instruction_scope"))


def made(app, *kinds):
    return [entry for entry in course_line.page_made_with(app) if not kinds or entry[0] in kinds]


def own(log, *names):
    """(name, args, kwargs) of the course line's own calls in a log (only ``names`` when given)."""
    return course_line.calls(log, *names)


def order(log):
    """The names of the calls in a log in order, leaving out the learner store's calls to itself; a drawn page
    appears as ("screen", page name)."""
    return [("screen", call.args[0]) if call.name == "screen" else call.name
            for call in course_line.own_calls(log)]


def writes(log):
    return [call for call in own(log) if call[0] in WRITES]


def watched(app, monkeypatch, *names, answers=None):
    """One ordered log of the teaching step's learner-store calls, the pages drawn and ``names`` (recorded
    only: they do not run)."""
    log = course_line.record_teaching_calls(app, monkeypatch, answers=answers)
    course_line.record_screens(app, monkeypatch, log=log)
    if names:
        course_line.record_entries(app, monkeypatch, real=False, log=log, names=names)
    return log


def placeholders(app, monkeypatch):
    rows = course_line.replace_next_content_rows(app, monkeypatch, lambda: [View(text=text) for text in ROWS])
    replacement = course_line.replace_replacement_controls(
        app, monkeypatch, lambda question_ids, item: [View(text=text) for text in REPLACEMENT])
    return rows, replacement


def old_controls(app):
    return {view.text: view for view in course_line.controls(app)}


# --- a. the ten outcomes -------------------------------------------------------------------------------------

def test_a_guide_whose_frozen_question_waits_for_a_check_of_updated_material_shows_its_page(app, monkeypatch):
    """A lesson at its guide whose frozen guide question is blocked by a newer material version: page
    learning_wait 引导题待核 with its line, the replacement rows for this lesson (built first), 查看更新后的说明
    -> show_learning_detail(item), the next-content rows, 回到今日 -> leave_learning(widget). Nothing is asked of
    or written to the learner store, and the lesson the teaching pages are about is not changed."""
    cid = enrol(app)
    item = dict(guiding(app, cid), checkpoint={"guide_question": {"id": "syn-frozen"}})
    asked = []
    course_line.patch_guide_blocked(monkeypatch, lambda store, current, guide: asked.append((current, guide)) or True)
    course_line.record_controls(monkeypatch)
    rows, replacement = placeholders(app, monkeypatch)
    pressed = course_line.record_presses(app, "show_learning_detail", "leave_learning")
    before = {"id": "syn-earlier"}
    course_line.set_taught_item(app, before)
    log = watched(app, monkeypatch)
    app.show_learning_instruction(item)
    assert asked == [(item, {"id": "syn-frozen"})]
    assert course_line.page(app) == ("learning_wait", "引导题待核")
    assert course_line.texts(app) == ["说明已经更新，原引导题与草稿已保留。", *REPLACEMENT, "查看更新后的说明", *ROWS, HOME]
    assert made(app) == [("label", "说明已经更新，原引导题与草稿已保留。", {}), ("button", "查看更新后的说明", {}),
                         ("button", HOME, {})]
    assert [(call.name, call.args, call.kwargs) for call in replacement] == [("replacement_rows", (), {"item": item})]
    assert len(rows) == 1
    assert order(log) == [("screen", "learning_wait")]
    assert course_line.taught_item(app) is before
    course_line.press(app, "查看更新后的说明")
    home = course_line.press(app, HOME)
    assert course_line.calls(pressed) == [("show_learning_detail", (item,), {}), ("leave_learning", (home,), {})]


def test_a_frozen_guide_question_that_is_not_blocked_is_led_on(app, monkeypatch):
    """The same check answering "not blocked" goes on to the guide page."""
    cid = enrol(app)
    item = guiding(app, cid)
    app.show_learning_instruction(item)
    item = app.learning_store.instruction_item(cid, item.get("instruction_scope"))
    course_line.patch_guide_blocked(monkeypatch, lambda store, current, guide: False)
    app.show_learning_instruction(item)
    assert course_line.page(app) == ("learning_guide", "跟着做")


@pytest.mark.parametrize("status,title,caption", [
    ("instruction_missing", "学习材料尚不完整", "查看材料"),
    ("unverified", "材料待核验", "预览草稿"),
])
def test_material_that_is_not_ready_shows_its_page_and_keeps_the_taught_lesson(app, monkeypatch, status, title,
                                                                                caption):
    """Teaching that may not be shown: page learning_wait with the title for missing or unchecked material,
    its line, 查看材料 / 预览草稿 -> show_learning_detail(item), 回到今日 -> leave_learning(widget). Nothing
    is asked of the learner store; the lesson the teaching pages are about is not changed."""
    cid = enrol(app)
    item = dict(untouched(app, cid), instruction_status=status)
    course_line.replace_teaching_allowed(monkeypatch, lambda current: False)
    course_line.record_controls(monkeypatch)
    pressed = course_line.record_presses(app, "show_learning_detail", "leave_learning")
    log = watched(app, monkeypatch)
    app.show_learning_instruction(item)
    assert course_line.page(app) == ("learning_wait", title)
    assert made(app) == [("label", "原有进度与题目已保存，暂不开始正式教学。", {}), ("button", caption, {}),
                         ("button", HOME, {})]
    assert order(log) == [("screen", "learning_wait")]
    assert course_line.taught_item(app) is None
    course_line.press(app, caption)
    home = course_line.press(app, HOME)
    assert course_line.calls(pressed) == [("show_learning_detail", (item,), {}), ("leave_learning", (home,), {})]


def test_a_later_ability_of_a_batch_lesson_opens_that_ability(app, monkeypatch):
    """An enrolled lesson with an ability scope, at its introduction, without a familiarity choice, that the
    batch cards introduced: open_batch_ability(item) only, after asking is_batch_route(id); the lesson is now
    the one the teaching pages are about."""
    cid = enrol(app)
    item = dict(untouched(app, cid), instruction_scope="syn-scope")
    log = watched(app, monkeypatch, "open_batch_ability", "show_learning_familiarity",
                  answers={"is_batch_route": lambda content_id: True})
    app.show_learning_instruction(item)
    assert own(log) == [("is_batch_route", (cid,), {}), ("open_batch_ability", (item,), {})]
    assert course_line.taught_item(app) is item


def test_a_scoped_lesson_the_batch_cards_did_not_introduce_asks_familiarity(app, monkeypatch):
    cid = enrol(app)
    item = dict(untouched(app, cid), instruction_scope="syn-scope")
    log = watched(app, monkeypatch, "open_batch_ability", "show_learning_familiarity",
                  answers={"is_batch_route": lambda content_id: False})
    app.show_learning_instruction(item)
    assert own(log) == [("is_batch_route", (cid,), {}), ("show_learning_familiarity", (item,), {})]


@pytest.mark.parametrize("stage,extra", [("intro", {}), ("guide", {intro_batch.ITEM_KEY: {}})])
def test_a_lesson_the_open_batch_owns_shows_the_batch(app, monkeypatch, stage, extra):
    """A lesson of the batch route (no scope) at its introduction without a familiarity choice, or at its guide
    with a batch entry in its checkpoint, that the open batch lists: show_intro_batch() only, after reading
    the open batch once."""
    cid = enrol(app)
    item = dict(untouched(app, cid), stage=stage,
                checkpoint={intro_batch.ROUTE_KEY: intro_batch.ROUTE, **extra})
    batch = {"entries": [{"content_id": "word:synthetic-teach:other"}, {"content_id": cid}]}
    log = watched(app, monkeypatch, "show_intro_batch", "show_learning_familiarity",
                  answers={"intro_batch": lambda: batch})
    app.show_learning_instruction(item)
    assert own(log) == [("intro_batch", (), {}), ("show_intro_batch", (), {})]
    assert course_line.taught_item(app) is item


def test_a_batch_route_lesson_the_open_batch_does_not_list_asks_familiarity(app, monkeypatch):
    cid = enrol(app)
    item = dict(untouched(app, cid), checkpoint={intro_batch.ROUTE_KEY: intro_batch.ROUTE})
    log = watched(app, monkeypatch, "show_intro_batch", "show_learning_familiarity",
                  answers={"intro_batch": lambda: {"entries": [{"content_id": "word:synthetic-teach:other"}]}})
    app.show_learning_instruction(item)
    assert own(log) == [("intro_batch", (), {}), ("show_learning_familiarity", (item,), {})]


def test_an_untouched_lesson_asks_how_familiar_it_is(app, monkeypatch):
    """An enrolled lesson at its introduction without a familiarity choice: show_learning_familiarity(item)
    only (no batch question for a lesson without a scope or batch route), and the page 开始前 is drawn."""
    cid = enrol(app)
    item = untouched(app, cid)
    log = course_line.record_teaching_calls(app, monkeypatch, "is_batch_route", "intro_batch")
    entries = course_line.record_entries(app, monkeypatch, names=("show_learning_familiarity",))
    app.show_learning_instruction(item)
    assert own(entries) == [("show_learning_familiarity", (item,), {})]
    assert own(log) == []
    assert course_line.page(app) == ("learning_familiarity", "开始前")


def test_no_guide_kind_left_freezes_the_guided_sequence_first_then_pauses(app, monkeypatch):
    """At the guide with none of its kinds switched on: the guided sequence is still asked for (with no
    kinds) and, when one comes back and none is frozen, frozen with advance_instruction(id, "guide",
    state={sequence, index 0, no completed ids, its first question, an empty draft}) BEFORE the page
    引导已保留 is drawn; no guide question is selected. Odd but today's behaviour: the sequence is frozen
    although the guide is paused."""
    cid = enrol(app, contexts=2)
    item = guiding(app, cid)
    sequence = app.learning_store.guided_sequence(item, ["vocab_orthography"])
    assert len(sequence) == 2
    app.store.set_setting(PREFS, {"enabled": []})
    log = watched(app, monkeypatch, answers={"guided_sequence": lambda *args, **kwargs: deepcopy(sequence)})
    app.show_learning_instruction(item)
    asked = [call for call in course_line.own_calls(log) if call.name == "guided_sequence"]
    assert asked[0].args[1] == [] and asked[0].kwargs == {"known_background_ids": {cid}}
    assert order(log) == ["has_other_instruction_exposure", "guided_sequence", "advance_instruction",
                          ("screen", "learning_wait")]
    assert writes(log) == [("advance_instruction", (cid, "guide"), {"state": {
        "guide_sequence": sequence, "guide_index": 0, "guide_completed_ids": [], "guide_question": sequence[0],
        "guide_draft": {}}, "instruction_scope": None})]
    assert course_line.page(app) == ("learning_wait", "引导已保留")
    assert course_line.taught_item(app)["checkpoint"]["guide_sequence"] == sequence
    assert course_line.guide_question(app) is None


def test_the_paused_guide_page_and_its_buttons(app, monkeypatch):
    """Page 引导已保留: its line (size 16), 继续其他学习 (primary) -> continue_added_learning(widget), 练习设置
    (compact) -> show_learning_settings(widget), 回到今日 -> show_home(widget) (not leave_learning)."""
    cid = enrol(app)
    item = guiding(app, cid)
    app.store.set_setting(PREFS, {"enabled": []})
    course_line.record_controls(monkeypatch)
    pressed = course_line.record_presses(app, "continue_added_learning", "show_learning_settings", "show_home",
                                         "leave_learning")
    app.show_learning_instruction(item)
    assert made(app) == [("label", "这类练习已关闭，输入已保存。", {"size": 16}),
                         ("button", "继续其他学习", {"primary": True}), ("button", "练习设置", {"compact": True}),
                         ("button", HOME, {})]
    widgets = [course_line.press(app, caption) for caption in ("继续其他学习", "练习设置", HOME)]
    assert course_line.calls(pressed) == [("continue_added_learning", (widgets[0],), {}),
                                          ("show_learning_settings", (widgets[1],), {}), ("show_home", (widgets[2],), {})]


def test_a_guide_question_whose_kind_is_off_is_set_with_a_new_result_column_before_the_pause(app, monkeypatch):
    """When the guide question selected is of a kind that is switched off, the question is the guide question
    on the page and a new result column exists BEFORE the page 引导已保留 is drawn; the practice question is not
    frozen (nothing is written)."""
    cid = enrol(app, kinds=["vocab_orthography"])
    item = guiding(app, cid)
    real = app.learning_store.guided_question
    offered = []

    def closed(current, kind, **kwargs):
        offered.append(dict(real(current, kind, **kwargs), kind="vocab_reading"))
        return offered[-1]
    assert course_line.guide_result_column(app) is None
    log = watched(app, monkeypatch, answers={"guided_question": closed})
    seen = []
    record_screen = app.screen

    def screen(*args, **kwargs):
        seen.append((course_line.guide_question(app), course_line.guide_result_column(app)))
        return record_screen(*args, **kwargs)
    monkeypatch.setattr(app, "screen", screen)
    app.show_learning_instruction(item)
    assert course_line.page(app) == ("learning_wait", "引导已保留")
    app.show_learning_instruction(item)
    assert seen[0][0] is offered[0] and seen[1][0] is offered[1]
    assert seen[0][1] is not None and seen[1][1] is not seen[0][1]
    assert writes(log) == []


def test_no_guide_question_that_can_be_built_shows_the_unavailable_page(app, monkeypatch):
    """Every kind tried yields no guide question: page learning_wait 引导例题暂不可用 with the lesson's title
    (21, bold, study role for a word), its two lines (16; 13 MUTED), an empty status line (13 MUTED), 直接开始练习
    (primary), 跳到下一项 and 回到今日 -> leave_learning(widget). The kinds are tried in the preferred order, each
    once with variant 1; nothing is written."""
    cid = enrol(app)
    item = guiding(app, cid)
    course_line.record_controls(monkeypatch)
    pressed = course_line.record_presses(app, "leave_learning")
    log = watched(app, monkeypatch, answers={"guided_question": lambda current, kind, **kwargs: None})
    app.show_learning_instruction(item)
    assert course_line.page(app) == ("learning_wait", "引导例题暂不可用")
    assert [(call.args[1], call.kwargs) for call in course_line.own_calls(log) if call.name == "guided_question"] == [
        ("vocab_orthography", {"variant": 1})]
    assert writes(log) == []
    assert made(app) == [("label", "猫", {"size": 21, "bold": True, "text_role": "study"}),
                         ("label", UNAVAILABLE_TEXTS[0], {"size": 16}),
                         ("label", UNAVAILABLE_TEXTS[1], {"size": 13, "color": u.MUTED}),
                         ("label", "", {"size": 13, "color": u.MUTED}),
                         ("button", "直接开始练习", {"primary": True}), ("button", "跳到下一项", {}),
                         ("button", HOME, {})]
    home = course_line.press(app, HOME)
    assert course_line.calls(pressed) == [("leave_learning", (home,), {})]


def test_a_grammar_lesson_title_on_the_unavailable_page_has_the_ui_role(app, monkeypatch):
    cid = enrol(app)
    app.store.set_setting(PREFS, {"enabled": ["grammar_meaning"], SOUND_PREFERENCE_VERSION: PREFERENCE_VERSION})
    item = dict(guiding(app, cid), family="grammar", title="〜ている", enrolled_abilities=["understanding"],
                question_templates={"grammar_meaning": [{"prompt": "読んでいます。", "answer": "正在读",
                                                         "choices": ["正在读", "读过"]}]})
    course_line.record_controls(monkeypatch)
    course_line.record_teaching_calls(app, monkeypatch, "guided_question", "guided_sequence", answers={
        "guided_question": lambda current, kind, **kwargs: None, "guided_sequence": lambda *args, **kwargs: []})
    app.show_learning_instruction(item)
    assert course_line.page(app) == ("learning_wait", "引导例题暂不可用")
    assert made(app)[0] == ("label", "〜ている", {"size": 21, "bold": True, "text_role": "ui"})


def unavailable(app, monkeypatch, cid):
    item = guiding(app, cid)
    course_line.record_teaching_calls(app, monkeypatch, "guided_question",
                                      answers={"guided_question": lambda current, kind, **kwargs: None})
    app.show_learning_instruction(item)
    assert course_line.page(app) == ("learning_wait", "引导例题暂不可用")
    return item


def test_starting_practice_from_the_unavailable_page_moves_the_lesson_on_and_hands_back(app, monkeypatch):
    """直接开始练习: advance_instruction(id, "bridge", fair_new=<new-work fairness>, instruction_scope=…), then
    continue_added_learning() with nothing. With a study path the fairness switch is on."""
    cid = enrol(app)
    course_line.record_presses(app, "continue_study_path")
    unavailable(app, monkeypatch, cid)
    log = watched(app, monkeypatch, "continue_added_learning")
    course_line.press(app, "直接开始练习")
    assert own(log) == [("advance_instruction", (cid, "bridge"), {"fair_new": True, "instruction_scope": None}),
                        ("continue_added_learning", (), {})]
    assert untouched(app, cid)["stage"] == "bridge"


def test_starting_practice_that_cannot_be_saved_says_so_on_the_page(app, monkeypatch):
    """A refused write: the status line says "暂未保存：<error>" and nothing hands back."""
    cid = enrol(app)
    unavailable(app, monkeypatch, cid)

    def refuse(*args, **kwargs):
        raise ValueError("合成拒绝")
    log = watched(app, monkeypatch, "continue_added_learning", answers={"advance_instruction": refuse})
    course_line.press(app, "直接开始练习")
    assert "暂未保存：合成拒绝" in course_line.texts(app)
    assert order(log) == ["advance_instruction"]


def test_the_unavailable_page_buttons_of_an_earlier_drawing_do_nothing(app, monkeypatch):
    """直接开始练习 and 跳到下一项 act only on the drawing they belong to (the page token): after the same page is
    drawn again, the earlier page's two buttons write nothing, skip nothing and hand nothing back."""
    cid = enrol(app)
    item = unavailable(app, monkeypatch, cid)
    earlier = old_controls(app)
    app.show_learning_instruction(item)
    log = watched(app, monkeypatch, "continue_added_learning")
    for caption in ("直接开始练习", "跳到下一项"):
        earlier[caption].on_press(earlier[caption])
    assert own(log) == []
    assert course_line.skipped_teaching(app) is None
    course_line.press(app, "跳到下一项")
    assert course_line.skipped_teaching(app) == {cid}


def test_the_unavailable_page_buttons_do_nothing_once_another_page_is_shown(app, monkeypatch):
    cid = enrol(app)
    unavailable(app, monkeypatch, cid)
    earlier = old_controls(app)
    app.page = "learning_intro"
    log = watched(app, monkeypatch, "continue_added_learning")
    for caption in ("直接开始练习", "跳到下一项"):
        earlier[caption].on_press(earlier[caption])
    assert own(log) == [] and course_line.skipped_teaching(app) is None


def test_skipping_a_lesson_keeps_it_passed_over_for_this_run_and_hands_back(app, monkeypatch):
    """跳到下一项 adds the lesson to the lessons passed over in this run (a set kept in place: an earlier
    skip stays) and calls continue_added_learning() with nothing; nothing is written."""
    cid = enrol(app)
    unavailable(app, monkeypatch, cid)
    kept = {"word:synthetic-teach:earlier"}
    course_line.set_skipped_teaching(app, kept)
    log = watched(app, monkeypatch, "continue_added_learning")
    course_line.press(app, "跳到下一项")
    assert own(log) == [("continue_added_learning", (), {})]
    assert course_line.skipped_teaching(app) is kept and kept == {"word:synthetic-teach:earlier", cid}


def test_a_skipped_lesson_is_not_offered_again_in_this_run(app, monkeypatch):
    """After 跳到下一项 the line goes on without that lesson: its teaching step is not shown again."""
    cid = enrol(app)
    unavailable(app, monkeypatch, cid)
    entries = course_line.record_entries(app, monkeypatch, names=("show_learning_instruction",))
    course_line.press(app, "跳到下一项")
    assert [call.args[0]["id"] for call in entries if call.name == "show_learning_instruction"] == []
    assert course_line.page(app) != ("learning_wait", "引导例题暂不可用")


def test_the_guide_page_freezes_its_practice_question_once_before_it_is_drawn(app, monkeypatch):
    """A single practice guide not frozen yet: guided_question(item, kind, variant=1), then
    advance_instruction(id, "guide", state={a copy of the question, an empty draft}) BEFORE the page 跟着做 is
    drawn; the guide question on the page is the one selected and the taught lesson holds the frozen one."""
    cid = enrol(app)
    item = guiding(app, cid)
    log = watched(app, monkeypatch)
    app.show_learning_instruction(item)
    assert course_line.page(app) == ("learning_guide", "跟着做")
    question = course_line.guide_question(app)
    assert order(log) == ["has_other_instruction_exposure", "guided_sequence", "guided_question",
                          "advance_instruction", ("screen", "learning_guide")]
    assert writes(log) == [("advance_instruction", (cid, "guide"), {
        "state": {"guide_question": question, "guide_draft": {}}, "instruction_scope": None})]
    frozen = course_line.taught_item(app)["checkpoint"]["guide_question"]
    assert frozen == question and frozen is not question
    assert question["kind"] == "vocab_orthography" and question["practice"]["context_id"] == "syn-a"


def test_drawing_a_frozen_single_guide_again_selects_the_same_question_and_writes_nothing(app, monkeypatch):
    cid = enrol(app)
    app.show_learning_instruction(guiding(app, cid))
    first = deepcopy(course_line.guide_question(app))
    stored = untouched(app, cid)
    log = watched(app, monkeypatch)
    app.show_learning_instruction(course_line.taught_item(app))
    assert writes(log) == []
    assert course_line.guide_question(app) == first
    assert untouched(app, cid) == stored


def test_the_guided_sequence_is_frozen_once_and_its_first_question_is_shown(app, monkeypatch):
    """A guide with two examples: the sequence is frozen (advance_instruction(id, "guide", state={…})) before
    the guide question is selected; that question is the sequence's first and is not frozen a second time."""
    cid = enrol(app, contexts=2)
    item = guiding(app, cid)
    log = watched(app, monkeypatch)
    app.show_learning_instruction(item)
    assert order(log) == ["has_other_instruction_exposure", "guided_sequence", "advance_instruction",
                          "guided_question", ("screen", "learning_guide")]
    (write,) = writes(log)
    sequence = write[2]["state"]["guide_sequence"]
    assert len(sequence) == 2 and write[2]["state"]["guide_question"] == sequence[0]
    assert course_line.guide_question(app) == sequence[0]
    guided = [call for call in course_line.own_calls(log) if call.name == "guided_question"][0]
    assert guided.args[0]["checkpoint"]["guide_sequence"] == sequence


def test_drawing_a_frozen_sequence_again_selects_nothing_new_and_writes_nothing(app, monkeypatch):
    cid = enrol(app, contexts=2)
    app.show_learning_instruction(guiding(app, cid))
    first = deepcopy(course_line.guide_question(app))
    stored = untouched(app, cid)
    log = watched(app, monkeypatch)
    app.show_learning_instruction(course_line.taught_item(app))
    assert writes(log) == []
    assert course_line.guide_question(app) == first
    assert untouched(app, cid) == stored


def test_a_shared_introduction_is_skipped_with_its_two_writes_before_the_guide(app, monkeypatch):
    """A lesson at its introduction with one enrolled ability whose material another ability already showed,
    nothing of a guide saved and its prerequisites not blocked: mark_instruction_exposed(id,
    expected_introduction_id=…, instruction_scope=…), then advance_instruction(id, "intro_done",
    instruction_scope=…); the lesson the store answers is the taught one, and the guide follows."""
    cid = enrol(app)
    item = opened(app, cid)
    log = watched(app, monkeypatch, answers={"has_other_instruction_exposure": lambda *args, **kwargs: True})
    app.show_learning_instruction(item)
    assert order(log)[:5] == ["has_other_instruction_exposure", "prerequisite_status", "mark_instruction_exposed",
                              "advance_instruction", "guided_sequence"]
    assert own(log, "has_other_instruction_exposure", "prerequisite_status")[:2] == [
        ("has_other_instruction_exposure", (cid,), {"instruction_scope": None, "personal_revision": None}),
        ("prerequisite_status", (cid, None), {})]
    assert writes(log)[:2] == [
        ("mark_instruction_exposed", (cid,), {"expected_introduction_id": item["introduction_id"],
                                              "instruction_scope": None}),
        ("advance_instruction", (cid, "intro_done"), {"instruction_scope": None})]
    assert course_line.page(app) == ("learning_guide", "跟着做")
    assert course_line.taught_item(app)["stage"] == "guide"


def test_after_a_skipped_shared_introduction_the_taught_lesson_is_the_store_answer_even_when_paused(
        app, monkeypatch):
    """The lesson the store answers after the skip (now at its guide) is the taught one at once: also when the
    guide then pauses for lack of kinds and nothing else is written."""
    cid = enrol(app)
    item = opened(app, cid)
    app.store.set_setting(PREFS, {"enabled": []})
    log = watched(app, monkeypatch, answers={"has_other_instruction_exposure": lambda *args, **kwargs: True,
                                             "guided_sequence": lambda *args, **kwargs: []})
    app.show_learning_instruction(item)
    assert course_line.page(app) == ("learning_wait", "引导已保留")
    (advanced,) = [call for call in course_line.own_calls(log) if call.name == "advance_instruction"]
    assert course_line.taught_item(app) is advanced.result
    assert advanced.result["stage"] == "guide"


def test_a_shared_introduction_with_blocked_prerequisites_is_shown(app, monkeypatch):
    cid = enrol(app)
    item = opened(app, cid)
    log = watched(app, monkeypatch, answers={"has_other_instruction_exposure": lambda *args, **kwargs: True,
                                             "prerequisite_status": lambda *args, **kwargs: {"blocked": True}})
    app.show_learning_instruction(item)
    assert course_line.page(app) == ("learning_intro", "初次学习")
    assert [call[1][1] for call in writes(log) if call[0] == "advance_instruction"] == []


def test_the_introduction_records_its_exposure_before_it_is_drawn(app, monkeypatch):
    """An enrolled lesson at its introduction: mark_instruction_exposed(id, expected_introduction_id=…,
    instruction_scope=…), then instruction_item(id, scope) — the taught lesson — and only then the page 初次学习;
    the page's main button is 跟着练习 for a lesson with practice examples."""
    cid = enrol(app)
    item = opened(app, cid)
    log = watched(app, monkeypatch)
    app.show_learning_instruction(item)
    assert order(log) == ["has_other_instruction_exposure", "mark_instruction_exposed", "instruction_item",
                          "intro_batch", ("screen", "learning_intro")]
    assert writes(log) == [("mark_instruction_exposed", (cid,), {"expected_introduction_id": item["introduction_id"],
                                                                 "instruction_scope": None})]
    assert own(log, "instruction_item") == [("instruction_item", (cid, None), {})]
    assert course_line.taught_item(app)["checkpoint"]["instruction_exposed_at_utc"]
    assert course_line.page(app) == ("learning_intro", "初次学习")
    assert "跟着练习" in course_line.captions(app)


def test_a_compact_familiarity_choice_shows_the_quick_review(app, monkeypatch):
    cid = enrol(app)
    item = opened(app, cid, level="some")
    app.show_learning_instruction(item)
    assert course_line.page(app) == ("learning_intro", "快速回顾")


def test_a_lesson_that_is_not_enrolled_is_introduced_without_an_exposure(app, monkeypatch):
    """A lesson dict not enrolled (no introduction id, no units) goes straight to its introduction page:
    nothing is asked of or written to the learner store, and the dict itself is the taught lesson."""
    item = lesson("loose")
    app.store.set_setting(PREFS, {"enabled": list(KINDS)})
    log = watched(app, monkeypatch)
    app.show_learning_instruction(item)
    assert order(log) == ["intro_batch", ("screen", "learning_intro")]
    assert course_line.taught_item(app) is item


def test_the_exposure_stays_recorded_when_drawing_the_introduction_fails(app, monkeypatch):
    cid = enrol(app)
    item = opened(app, cid)

    def broken(*args, **kwargs):
        raise RuntimeError("合成绘制失败")
    monkeypatch.setattr(app, "example_cards", broken)
    with pytest.raises(RuntimeError):
        app.show_learning_instruction(item)
    assert untouched(app, cid)["checkpoint"]["instruction_exposed_at_utc"]


def test_drawing_the_introduction_again_marks_it_again_but_changes_nothing(app, monkeypatch):
    """Drawing the introduction again repeats mark_instruction_exposed and instruction_item and nothing else;
    the stored lesson is unchanged (the first exposure time is kept)."""
    cid = enrol(app)
    app.show_learning_instruction(opened(app, cid))
    stored = untouched(app, cid)
    log = watched(app, monkeypatch)
    app.show_learning_instruction(course_line.taught_item(app))
    assert [call[0] for call in writes(log)] == ["mark_instruction_exposed"]
    assert untouched(app, cid) == stored


# --- c. the page 开始前 ---------------------------------------------------------------------------------------

def test_the_familiarity_page_prepares_the_lesson_and_holds_its_state(app, monkeypatch):
    """show_learning_familiarity(item): prepare_initial_familiarity(id, expected_introduction_id=…,
    instruction_scope=…, ability=<the first enrolled ability that is switched on>) BEFORE the page is drawn; the
    prepared lesson is the taught one; the page holds {content id, introduction id, scope, the ability asked,
    the levels so far, busy False}; the title (24, bold, study role), the ability (18, bold) and 初学 / 有印象 /
    已熟悉."""
    cid = enrol(app)
    item = untouched(app, cid)
    course_line.record_controls(monkeypatch)
    log = watched(app, monkeypatch)
    app.show_learning_familiarity(item)
    assert order(log) == ["prepare_initial_familiarity", ("screen", "learning_familiarity")]
    assert writes(log) == [("prepare_initial_familiarity", (cid,), {
        "expected_introduction_id": item["introduction_id"], "instruction_scope": None, "ability": "use"})]
    prepared = log[0].result
    assert course_line.taught_item(app) is prepared
    assert course_line.familiarity_state(app) == {
        "content_id": cid, "introduction_id": prepared["introduction_id"], "instruction_scope": None,
        "ability": "use", "levels": {}, "busy": False}
    assert course_line.page(app) == ("learning_familiarity", "开始前")
    assert made(app) == [("label", "猫", {"size": 24, "bold": True, "text_role": "study"}),
                         ("label", "提取与使用", {"size": 18, "bold": True}),
                         *[("button", caption, {}) for caption, _ in LEVELS]]


def test_the_familiarity_page_asks_the_ability_of_the_line_head_for_this_introduction(app, monkeypatch):
    """When the line's head is a question of this introduction, its ability is the one prepared and asked."""
    cid = enrol(app)
    item = untouched(app, cid)
    course_line.set_course_head(app, {"content": {"introduction_id": item["introduction_id"]},
                                      "question": {"ability": "reading"}})
    log = watched(app, monkeypatch)
    app.show_learning_familiarity(item)
    assert writes(log)[0][2]["ability"] == "reading"
    assert course_line.familiarity_state(app)["ability"] == "reading"
    assert "读音" in course_line.texts(app)


def test_the_familiarity_page_ignores_a_head_ability_that_is_switched_off(app, monkeypatch):
    cid = enrol(app, kinds=["vocab_orthography"])
    item = untouched(app, cid)
    course_line.set_course_head(app, {"content": {"introduction_id": item["introduction_id"]},
                                      "question": {"ability": "reading"}})
    log = watched(app, monkeypatch)
    app.show_learning_familiarity(item)
    assert writes(log)[0][2]["ability"] == "use"


def test_the_familiarity_page_of_material_that_is_not_ready_shows_the_teaching_step(app, monkeypatch):
    cid = enrol(app)
    item = untouched(app, cid)
    course_line.replace_teaching_allowed(monkeypatch, lambda current: False)
    log = watched(app, monkeypatch)
    entries = course_line.record_entries(app, monkeypatch, names=("show_learning_instruction",))
    app.show_learning_familiarity(item)
    assert own(entries) == [("show_learning_instruction", (item,), {})]
    assert writes(log) == []
    assert course_line.page(app) == ("learning_wait", "材料待核验")


def test_the_last_choice_saves_the_familiarity_and_shows_the_teaching_step(app, monkeypatch):
    """The choice for the last ability: set_initial_familiarity(id, {ability: level}, expected_introduction_id=…,
    title_answer_exposed=<a pronunciation lesson>, fair_new=<new-work fairness>, instruction_scope=…); a lesson
    still at its introduction then goes to show_learning_instruction(<the saved lesson>); busy is reset."""
    cid = enrol(app)
    app.show_learning_familiarity(untouched(app, cid))
    state = course_line.familiarity_state(app)
    log = watched(app, monkeypatch)
    entries = course_line.record_entries(app, monkeypatch, names=("show_learning_instruction",))
    course_line.press(app, "初学")
    (saved,) = [call for call in course_line.own_calls(log) if call.name == "set_initial_familiarity"]
    assert (saved.args, saved.kwargs) == ((cid, {"use": "new"}), {
        "expected_introduction_id": state["introduction_id"], "title_answer_exposed": False, "fair_new": False,
        "instruction_scope": None})
    assert own(entries) == [("show_learning_instruction", (saved.result,), {})]
    assert state["busy"] is False
    assert course_line.page(app) == ("learning_intro", "初次学习")


@pytest.mark.parametrize("study_path", [False, True])
def test_a_last_choice_that_leaves_the_introduction_hands_back(app, monkeypatch, study_path):
    """已熟悉 for an untouched lesson moves it past its introduction (a check): the line takes over with
    continue_study_path(_checkpoint_saved=True) when the app has a study path, else continue_learning(); with
    the study path the fairness switch is on."""
    cid = enrol(app)
    names = ("continue_study_path", "continue_learning") if study_path else ("continue_learning",)
    pressed = course_line.record_presses(app, *names)
    app.show_learning_familiarity(untouched(app, cid))
    log = watched(app, monkeypatch, "show_learning_instruction")
    course_line.press(app, "已熟悉")
    (saved,) = [call for call in course_line.own_calls(log) if call.name == "set_initial_familiarity"]
    assert saved.kwargs["fair_new"] is study_path
    assert saved.result["stage"] != "intro"
    assert own(log, "show_learning_instruction") == []
    assert course_line.calls(pressed) == ([("continue_study_path", (), {"_checkpoint_saved": True})] if study_path
                                          else [("continue_learning", (), {})])


def several_abilities(app, monkeypatch, cid):
    """The page 开始前 of a lesson whose preparation keeps both abilities in one introduction."""
    real = app.learning_store.instruction_item
    log = watched(app, monkeypatch, answers={
        "prepare_initial_familiarity": lambda content_id, **kwargs: real(content_id, kwargs.get("instruction_scope"))})
    app.show_learning_familiarity(untouched(app, cid))
    return log


def test_a_lesson_with_several_abilities_saves_a_draft_and_asks_the_next_ability(app, monkeypatch):
    """A choice while abilities remain: save_familiarity_draft(id, {chosen so far}, expected_introduction_id=…,
    instruction_scope=…), then the page 开始前 again for the saved lesson asking the next ability (读音), its
    levels the draft; the last choice saves both levels."""
    cid = enrol(app)
    log = several_abilities(app, monkeypatch, cid)
    first = course_line.familiarity_state(app)
    assert first["ability"] == "use"
    log.clear()
    course_line.press(app, "有印象")
    assert order(log) == ["save_familiarity_draft", "prepare_initial_familiarity", ("screen", "learning_familiarity")]
    assert writes(log)[0] == ("save_familiarity_draft", (cid, {"use": "some"}), {
        "expected_introduction_id": first["introduction_id"], "instruction_scope": None})
    assert first["busy"] is False
    second = course_line.familiarity_state(app)
    assert second is not first
    assert (second["ability"], second["levels"]) == ("reading", {"use": "some"})
    assert "读音" in course_line.texts(app)
    log.clear()
    course_line.press(app, "初学")
    assert [call[1] for call in writes(log) if call[0] == "set_initial_familiarity"] == [
        (cid, {"use": "some", "reading": "new"})]


def test_a_second_press_while_a_choice_is_being_saved_does_nothing(app, monkeypatch):
    """While a choice is being saved (busy), another press of the page does nothing; busy is reset after."""
    cid = enrol(app)
    real = app.learning_store.instruction_item
    real_draft = app.learning_store.save_familiarity_draft
    seen = []

    def save(*args, **kwargs):
        seen.append(course_line.familiarity_state(app)["busy"])
        course_line.press(app, "已熟悉")
        return real_draft(*args, **kwargs)
    log = watched(app, monkeypatch, answers={
        "prepare_initial_familiarity": lambda content_id, **kwargs: real(content_id, kwargs.get("instruction_scope")),
        "save_familiarity_draft": save})
    app.show_learning_familiarity(untouched(app, cid))
    state = course_line.familiarity_state(app)
    course_line.press(app, "初学")
    assert seen == [True]
    assert [call[0] for call in writes(log)].count("save_familiarity_draft") == 1
    assert [call[0] for call in writes(log)].count("set_initial_familiarity") == 0
    assert state["busy"] is False


def test_a_choice_that_fails_to_save_resets_busy_and_raises(app, monkeypatch):
    cid = enrol(app)
    app.show_learning_familiarity(untouched(app, cid))
    state = course_line.familiarity_state(app)

    def refuse(*args, **kwargs):
        raise ValueError("合成拒绝")
    watched(app, monkeypatch, answers={"set_initial_familiarity": refuse})
    with pytest.raises(ValueError):
        course_line.press(app, "初学")
    assert state["busy"] is False


def test_the_buttons_of_an_earlier_familiarity_page_do_nothing(app, monkeypatch):
    """After the page 开始前 is drawn again, the earlier drawing's buttons write nothing and show nothing."""
    cid = enrol(app)
    item = untouched(app, cid)
    app.show_learning_familiarity(item)
    earlier = old_controls(app)
    app.show_learning_familiarity(course_line.taught_item(app))
    log = watched(app, monkeypatch, "show_learning_instruction", "continue_learning")
    for caption, _ in LEVELS:
        earlier[caption].on_press(earlier[caption])
    assert own(log) == []


@pytest.mark.parametrize("change", ["page", "introduction", "scope"])
def test_a_familiarity_press_does_nothing_when_the_page_or_the_lesson_changed(app, monkeypatch, change):
    """A press does nothing once another page is shown, or when the taught lesson is another introduction or
    another scope than the page's."""
    cid = enrol(app)
    app.show_learning_familiarity(untouched(app, cid))
    taught = course_line.taught_item(app)
    if change == "page":
        app.page = "learning_intro"
    elif change == "introduction":
        course_line.set_taught_item(app, dict(taught, introduction_id="syn-other"))
    else:
        course_line.set_taught_item(app, dict(taught, instruction_scope="syn-other"))
    log = watched(app, monkeypatch, "show_learning_instruction", "continue_learning")
    course_line.press(app, "初学")
    assert own(log) == []


# --- k. Back to a kept page 开始前 ------------------------------------------------------------------------------

def test_back_to_a_kept_familiarity_page_and_a_press_there(app, monkeypatch):
    """练习设置 keeps the page 开始前; Back restores it with the same state and taught lesson, and a press there
    saves the choice and goes on."""
    from types import MethodType
    from kanamemory.learning_ui import LearningUIMixin
    app = use_real_detail_back(app)
    app.show_learning_settings = MethodType(LearningUIMixin.show_learning_settings, app)
    cid = enrol(app)
    app.show_learning_familiarity(untouched(app, cid))
    state, taught = course_line.familiarity_state(app), course_line.taught_item(app)
    app.show_learning_settings()
    assert course_line.page(app)[0] == "learning_settings"
    app.go_back()
    assert course_line.page(app)[0] == "learning_familiarity"
    assert course_line.texts(app) == ["猫", "提取与使用", *[caption for caption, _ in LEVELS]]
    assert course_line.familiarity_state(app) is state and course_line.taught_item(app) is taught
    log = watched(app, monkeypatch, "show_learning_instruction")
    course_line.press(app, "初学")
    assert [call[0] for call in own(log)] == ["set_initial_familiarity", "show_learning_instruction"]


# --- l. what a problem report captures --------------------------------------------------------------------------

def test_a_report_on_the_familiarity_page_captures_the_lesson_and_the_choices(app):
    cid = enrol(app)
    app.show_learning_familiarity(untouched(app, cid))
    content = feedback_context.capture_feedback_context(app)["content"]
    assert content["learning_item"] == course_line.taught_item(app)
    assert content["familiarity_selections"] == course_line.familiarity_state(app)


def test_a_report_on_the_introduction_captures_the_taught_lesson(app):
    cid = enrol(app)
    app.show_learning_instruction(opened(app, cid))
    content = feedback_context.capture_feedback_context(app)["content"]
    assert content["learning_item"] == course_line.taught_item(app)
    assert "familiarity_selections" not in content


def test_a_report_on_the_guide_page_captures_the_lesson_and_the_guide_question(app):
    """The guide question is captured as practice/question without its answer-bearing fields, which go to the
    maintainer-only part."""
    cid = enrol(app)
    app.show_learning_instruction(guiding(app, cid))
    report = feedback_context.capture_feedback_context(app)
    guide = course_line.guide_question(app)
    assert report["content"]["learning_item"] == course_line.taught_item(app)
    question = report["practice"]["question"]
    assert question["id"] == guide["id"] and "answer" not in question and "feedback" not in question
    hidden = report["practice"]["maintainer_only"]["hidden_solution_and_metadata"]
    assert hidden["answer"] == guide["answer"] and hidden["guide"] == guide["guide"]
