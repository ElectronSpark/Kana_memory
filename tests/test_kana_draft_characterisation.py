"""What the kana practice keeps of an answer that is being typed, pinned as it behaves today.

While a question is on the page the practice holds what the learner has typed so far: the romaji of a kana
question, the kana of a word, the romaji parts chosen for a word that is answered part by part (with their plan
and the mode), and the three values of the romaji converter of a word question. These tests press the keys and
buttons of the pages, or call the entry a control calls, and pin what each of them leaves in that draft, which
of it a page kept for Back holds, what Back gives back, what the saved checkpoint and a problem report carry
of it. They read and set the draft through ``support.kana_support`` only and build every question they need, so
they run on the demo content.
"""
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from kanamemory import feedback_context
from kanamemory.app import KanaMemory
from kanamemory.domain import Question
from kanamemory.navigation import BackStack, PageState
from support import kana_support
from support.answer_option_ui_support import RECORDS, WORD, preview_app  # noqa: F401  (fixture)
from support.app_fixtures import question_app, setup_app  # noqa: F401  (fixtures)

CONVERTER = {"word_conversion_source": "", "word_conversion_convention": "reading", "word_used_conversion": False}


# --- questions and pages --------------------------------------------------------------------------------------------

def kana(kind="kana_romaji", content_id="ka"):
    record = next(r for r in RECORDS if r["id"] == content_id)
    other = next(r for r in RECORDS if r["id"] != content_id)
    return Question(content_id, kind, "hiragana", "", record["romaji"], options=[record["romaji"], other["romaji"]],
                    metadata={"kana_symbol": record["hiragana"], "romaji": record["romaji"], "content_version": "test"})


def word(kind="word_meaning_kana"):
    return Question(WORD["id"], kind, "hiragana", "", WORD["reading"], options=["か", "な"],
                    metadata={"romaji": "kana", "meaning_hint": "假名", "content_version": "test",
                              "kana_answer": WORD["reading"]})


def reverse_word():
    """A word answered in romaji, part by part (no entry of the word list stands behind it)."""
    return Question("word-without-entry", "word_romaji", "hiragana", "", "kana", options=[],
                    metadata={"romaji": "kana", "meaning_hint": "假名", "content_version": "test",
                              "kana_answer": WORD["reading"]})


def draw(app, question):
    kana_support.set_questions(app, [question, kana("kana_choice", "na")])
    app.show_question()
    return kana_support.question(app)


def type_romaji(app, text):
    app.romaji_input.value = text
    app.romaji_input.on_change(app.romaji_input)


def units(app):
    return kana_support.word_romaji_plan(app)["units"]


def choose_next_unit(app):
    """Choose the right romaji for the next part of the word; returns it."""
    index = len(kana_support.word_romaji_parts(app))
    answer = units(app)[index]["answer"]
    KanaMemory.choose_word_romaji_unit(app, answer, kana_support.question(app), index)
    return answer


@pytest.fixture
def draft_app(preview_app):
    app = preview_app
    app._preview_pending = None
    app.mixed_label = lambda *parts, **kw: SimpleNamespace(text="", children=[])
    return app


def kept_by_a_detail_page(app):
    """The learner opens a detail page; returns what the page left behind holds of the draft."""
    app.show_kana_detail("ka")
    assert app.page == "kana_detail"
    return kana_support.kept_draft_values(app.back_stack.pages[-1])


# --- what the pages and their keys write ------------------------------------------------------------------------------

def test_a_romaji_question_starts_with_an_empty_answer_and_takes_what_is_typed(draft_app):
    app = draft_app
    kana_support.set_romaji_answer(app, "left over")
    draw(app, kana())
    assert app.page == "question" and kana_support.romaji_answer(app) == "" and app.romaji_input.value == ""
    type_romaji(app, "k")
    type_romaji(app, "ka")
    assert kana_support.romaji_answer(app) == "ka"


def test_typing_is_ignored_off_the_question_page_and_from_another_control(draft_app):
    app = draft_app
    draw(app, kana())
    type_romaji(app, "ka")
    other = SimpleNamespace(value="from elsewhere")
    app.change_romaji_answer(other)
    assert kana_support.romaji_answer(app) == "ka"
    app.page = "feedback"
    type_romaji(app, "later")
    assert kana_support.romaji_answer(app) == "ka"


def test_a_choice_question_leaves_a_romaji_answer_of_an_earlier_question_alone(draft_app):
    app = draft_app
    kana_support.set_romaji_answer(app, "earlier")
    draw(app, kana("kana_choice"))
    assert kana_support.romaji_answer(app) == "earlier"


def test_a_word_question_starts_empty_with_the_converter_reset_and_takes_keys_and_deletions(draft_app):
    app = draft_app
    kana_support.with_state(app, word_answer="left over", word_conversion_source="gakk",
                            word_conversion_convention="keyboard", word_used_conversion=True)
    draw(app, word())
    assert app.page == "word_question"
    assert kana_support.word_answer(app) == "" and app.word_answer_label.text == "＿"
    assert {name: kana_support.draft(app)[name] for name in CONVERTER} == CONVERTER
    app.append_word_key("か")
    app.append_word_key("な")
    assert kana_support.word_answer(app) == "かな" == app.word_answer_label.text
    app.delete_word_key()
    assert kana_support.word_answer(app) == "か" == app.word_answer_label.text
    app.delete_word_key()
    app.delete_word_key()
    assert kana_support.word_answer(app) == "" and app.word_answer_label.text == "＿"


def test_a_word_answered_part_by_part_starts_with_its_plan_no_parts_and_the_mode_on(draft_app):
    app = draft_app
    old_parts = ["left", "over"]
    kana_support.with_state(app, word_romaji_parts=old_parts, word_romaji_plan=None, word_romaji_unit_mode=False)
    draw(app, reverse_word())
    assert app.page == "question" and kana_support.word_romaji_unit_mode(app) is True
    parts = kana_support.word_romaji_parts(app)
    assert parts == [] and parts is not old_parts and old_parts == ["left", "over"]
    assert [unit["kana"] for unit in units(app)] and all("options" in unit for unit in units(app))


def test_choosing_and_deleting_a_part_change_the_same_list_in_place(draft_app):
    app = draft_app
    draw(app, reverse_word())
    parts = kana_support.word_romaji_parts(app)
    plan = kana_support.word_romaji_plan(app)
    first = choose_next_unit(app)
    assert kana_support.word_romaji_parts(app) is parts and parts == [first]
    KanaMemory.choose_word_romaji_unit(app, "not an option", kana_support.question(app), 1)
    KanaMemory.choose_word_romaji_unit(app, first, kana_support.question(app), 0)
    assert parts == [first]
    KanaMemory.delete_word_romaji_unit(app)
    assert kana_support.word_romaji_parts(app) is parts and parts == []
    KanaMemory.delete_word_romaji_unit(app)
    assert parts == [] and kana_support.word_romaji_plan(app) is plan


def test_the_parts_the_learner_chose_are_handed_to_the_record_only_for_their_own_answer(draft_app):
    app = draft_app
    draw(app, reverse_word())
    while len(kana_support.word_romaji_parts(app)) < len(units(app)):
        choose_next_unit(app)
    parts, plan = kana_support.word_romaji_parts(app), kana_support.word_romaji_plan(app)
    composed = "".join(parts)
    kana_support.set_answer_revealed(app, False)
    handed = KanaMemory._word_romaji_submission(app, composed)
    assert handed == {"word_romaji_parts": parts, "word_romaji_plan": plan}
    assert handed["word_romaji_parts"] is not parts and handed["word_romaji_plan"] is plan
    assert KanaMemory._word_romaji_submission(app, composed + "x") == {}
    kana_support.set_word_romaji_unit_mode(app, False)
    assert KanaMemory._word_romaji_submission(app, composed) == {}


# --- what a page kept for Back holds ----------------------------------------------------------------------------------

def test_a_kept_romaji_question_holds_the_typed_romaji_and_nothing_of_a_word(draft_app):
    app = draft_app
    kana_support.set_word_answer(app, "an earlier word")
    draw(app, kana())
    type_romaji(app, "ka")
    assert kept_by_a_detail_page(app) == {"romaji_answer": "ka"}


def test_a_kept_word_question_holds_the_typed_word_and_the_converter_values_but_no_romaji(draft_app):
    app = draft_app
    kana_support.set_romaji_answer(app, "an earlier answer")
    draw(app, word())
    app.append_word_key("か")
    assert kept_by_a_detail_page(app) == {"word_answer": "か", **CONVERTER}


def test_a_kept_question_page_after_a_word_holds_the_converter_values_too(draft_app):
    app = draft_app
    draw(app, word())
    draw(app, kana())
    type_romaji(app, "k")
    assert kept_by_a_detail_page(app) == {"romaji_answer": "k", **CONVERTER}


def test_a_kept_page_of_a_word_answered_part_by_part_holds_the_same_list_of_parts(draft_app):
    app = draft_app
    draw(app, reverse_word())
    first = choose_next_unit(app)
    held = kept_by_a_detail_page(app)
    assert set(held) == {"word_romaji_parts", "word_romaji_plan", "word_romaji_unit_mode"}
    assert held["word_romaji_parts"] is kana_support.word_romaji_parts(app) and held["word_romaji_parts"] == [first]
    assert held["word_romaji_plan"] is kana_support.word_romaji_plan(app) and held["word_romaji_unit_mode"] is True


def test_a_kept_result_page_holds_only_the_converter_values(draft_app):
    app = draft_app
    draw(app, word())
    app.append_word_key("か")
    app.check_answer("か")
    assert app.page == "feedback"
    assert kept_by_a_detail_page(app) == CONVERTER


# --- what Back gives back ---------------------------------------------------------------------------------------------

def test_back_to_a_question_gives_back_what_was_typed_there_and_leaves_what_the_page_never_held(draft_app):
    app = draft_app
    draw(app, kana())
    type_romaji(app, "ka")
    app.show_kana_detail("ka")
    kana_support.set_romaji_answer(app, "changed while away")
    kana_support.set_word_answer(app, "set while away")
    app.go_back()
    assert app.page == "question"
    assert kana_support.romaji_answer(app) == "ka" and kana_support.word_answer(app) == "set while away"


def away_from(name, **held):
    """A hand-made practice on a detail page whose way back leads to a kept page ``name`` that holds ``held`` of
    the draft; the practice itself holds other values."""
    question = SimpleNamespace(content_id="ka", kind="kana_choice")
    value = kana_support.with_state(SimpleNamespace(
        page="kana_detail", _view_key=("show_kana_detail", "ka"), back_stack=BackStack(),
        question_notice=SimpleNamespace(text=""), platform=Mock(),
        store=Mock(is_content_available=lambda content_id: True), alert=Mock(), _page_token=5, _ink_revision=2,
        _audio_serial=0, _paint_handle=None, content_slot=Mock(), title_label=SimpleNamespace(text=""),
        _checkpoint=Mock(), _sync_back_handler=Mock(), loop=Mock()),
        assisted_attempts=set(), answered_attempts=set(), answer_revealed=False, session_id="now",
        questions=[question], question_index=0, session_right=0, question=question, script="hiragana",
        romaji_answer="now", word_answer="now", word_romaji_parts=["now"], word_romaji_plan="plan now",
        word_romaji_unit_mode=True, word_conversion_source="now", word_conversion_convention="keyboard",
        word_used_conversion=True)
    value._attempt_key = MethodType(KanaMemory._attempt_key, value)
    value.back_stack.pages.append(PageState(
        ("show_question", None), name, "练习", object(),
        {**kana_support.kept_position(session_id="now", question_index=0), **kana_support.kept_draft(**held),
         "question_notice": SimpleNamespace(text="")}))
    return value


@pytest.mark.parametrize("name,held", [
    ("question", {"romaji_answer": "then"}),
    ("word_question", {"word_answer": "then", "word_conversion_source": "then", "word_used_conversion": False}),
    ("question", {"word_romaji_parts": ["then"], "word_romaji_plan": "plan then", "word_romaji_unit_mode": False}),
    ("feedback", {"word_conversion_convention": "reading"}),
    ("feedback", {}),
    ("question", {"romaji_answer": ""}),
    ("word_question", {"word_answer": None}),
    ("question", {"word_romaji_parts": [], "word_romaji_plan": None}),
    ("session_done", {"word_used_conversion": False}),
    ("session_wait", {"word_conversion_source": ""}),
    ("dependency_wait", {}),
])
def test_back_gives_back_exactly_the_values_the_kept_page_holds(name, held):
    value = away_from(name, **held)
    before = kana_support.draft(value)
    KanaMemory.go_back(value)
    assert value.page == name
    assert kana_support.draft(value) == {**before, **held}


def test_back_gives_the_practice_the_very_list_of_parts_the_kept_page_holds():
    kept_parts = ["then"]
    value = away_from("question", word_romaji_parts=kept_parts)
    KanaMemory.go_back(value)
    assert kana_support.word_romaji_parts(value) is kept_parts


# --- the saved checkpoint ---------------------------------------------------------------------------------------------

DRAFT_KEYS = {"romaji_answer", "word_answer", "word_romaji_parts", "word_romaji_plan", "word_conversion_source",
              "word_conversion_convention", "word_used_conversion"}


def saved_state(app):
    app.store.save_session_progress.reset_mock()
    KanaMemory._checkpoint(app)
    state = app.store.save_session_progress.call_args.args[4]
    return {name: value for name, value in state.items() if name in DRAFT_KEYS}


def test_the_checkpoint_of_a_romaji_question_saves_the_typed_romaji(draft_app):
    app = draft_app
    draw(app, kana())
    type_romaji(app, "ka")
    assert saved_state(app) == {"romaji_answer": "ka"}


def test_the_checkpoint_of_a_choice_question_saves_nothing_of_the_draft(draft_app):
    app = draft_app
    kana_support.with_state(app, romaji_answer="earlier", word_answer="earlier")
    draw(app, kana("kana_choice"))
    assert saved_state(app) == {}


def test_the_checkpoint_of_a_word_question_saves_the_word_and_the_converter_values(draft_app):
    app = draft_app
    draw(app, word())
    app.append_word_key("か")
    assert saved_state(app) == {"word_answer": "か", **CONVERTER}
    kana_support.with_state(app, word_conversion_source="gakk", word_conversion_convention="keyboard",
                            word_used_conversion="yes")
    assert saved_state(app) == {"word_answer": "か", "word_conversion_source": "gakk",
                                "word_conversion_convention": "keyboard", "word_used_conversion": False}


def test_the_checkpoint_of_a_word_answered_part_by_part_saves_a_copy_of_the_parts_and_the_plan(draft_app):
    app = draft_app
    draw(app, reverse_word())
    first = choose_next_unit(app)
    state = saved_state(app)
    assert set(state) == {"word_romaji_parts", "word_romaji_plan"}
    assert state["word_romaji_parts"] == [first] and state["word_romaji_parts"] is not kana_support.word_romaji_parts(app)
    assert state["word_romaji_plan"] is kana_support.word_romaji_plan(app)
    kana_support.set_word_romaji_unit_mode(app, False)
    assert saved_state(app) == {}


# --- a problem report -------------------------------------------------------------------------------------------------

def report_of(app, page="question"):
    return feedback_context._capture_practice(feedback_context._Capture(), app, page)


def report_app(**draft):
    question = kana()
    return kana_support.with_state(
        SimpleNamespace(use_full_keyboard=False, store=SimpleNamespace(practice_lane="main")),
        session_id="s", questions=[question], question_index=0, session_right=0, question=question,
        script="hiragana", **draft)


def test_a_problem_report_carries_the_typed_values_the_practice_holds():
    parts, plan = ["ka"], {"units": [{"kana": "か"}]}
    app = report_app(romaji_answer="k", word_answer="か", word_romaji_parts=parts, word_romaji_plan=plan,
                     word_romaji_unit_mode=True, word_conversion_source="gakk", word_used_conversion=True)
    result = report_of(app)["draft"]
    assert {name: result[name] for name in ("word_answer", "romaji_answer", "word_romaji_parts", "word_romaji_plan",
                                            "word_romaji_unit_mode")} == {
        "word_answer": "か", "romaji_answer": "k", "word_romaji_parts": ["ka"], "word_romaji_plan": plan,
        "word_romaji_unit_mode": True}
    assert result["word_romaji_parts"] is not parts
    assert list(result) == ["word_answer", "romaji_answer", "word_romaji_parts", "word_romaji_plan",
                            "word_romaji_unit_mode"]
    assert "word_conversion_source" not in result and "word_used_conversion" not in result


def test_a_problem_report_names_no_typed_value_the_practice_never_held():
    assert "draft" not in report_of(report_app())
    assert report_of(report_app(romaji_answer=""))["draft"] == {"romaji_answer": ""}


def test_a_problem_report_off_the_question_pages_carries_no_draft():
    assert "draft" not in report_of(report_app(romaji_answer="k"), "session_done")


# --- values that were never typed -------------------------------------------------------------------------------------

def test_the_checkpoint_of_a_word_saves_an_empty_word_and_the_converter_defaults_for_what_was_never_typed(draft_app):
    app = draft_app
    draw(app, word())
    kana_support.forget_draft(app, "word_answer", *CONVERTER)
    assert saved_state(app) == {"word_answer": "", **CONVERTER}
    assert kana_support.draft(app) == {}


def test_the_checkpoint_of_a_romaji_question_saves_an_empty_answer_for_one_that_was_never_typed(draft_app):
    app = draft_app
    draw(app, kana())
    kana_support.forget_draft(app, "romaji_answer")
    assert saved_state(app) == {"romaji_answer": ""}


def test_a_word_whose_mode_was_never_set_is_not_answered_part_by_part(draft_app):
    app = draft_app
    draw(app, reverse_word())
    first = choose_next_unit(app)
    kana_support.forget_draft(app, "word_romaji_unit_mode")
    assert saved_state(app) == {}
    kana_support.set_answer_revealed(app, False)
    assert KanaMemory._word_romaji_submission(app, first) == {}


def test_a_word_answer_without_a_converter_flag_is_judged_as_typed_without_the_converter(draft_app):
    app = draft_app
    draw(app, word())
    kana_support.set_answer_revealed(app, False)
    kana_support.forget_draft(app, "word_used_conversion")
    assert "used_conversion" not in KanaMemory._word_spelling_plan(app, WORD["reading"])[1]
    kana_support.set_word_used_conversion(app, True)
    assert KanaMemory._word_spelling_plan(app, WORD["reading"])[1]["used_conversion"] is True
