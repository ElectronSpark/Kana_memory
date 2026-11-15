"""What the app keeps of handwriting, pinned as it behaves today.

The handwriting question of the kana practice and the free writing page share one pad: the strokes written so
far (each a list of points with their times) and the size of the pad they were written on. These tests draw
with the page's own handlers, press its buttons, and pin what each of them leaves in the pad, what a page kept
for Back holds of it and what Back gives back, and what the saved checkpoint and a problem report carry. They
read and set the pad through ``support.kana_support`` only, so they run on the demo content.
"""
import asyncio
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import feedback_context
from kanamemory.app import KanaMemory
from kanamemory.domain import Question
from kanamemory.navigation import BackStack, PageState
from support import kana_support
from support.handwriting_model_ui_support import View, model_app  # noqa: F401  (fixture)

PAD = {"strokes": [], "ink_width": 360, "ink_height": 210}


def ink_question(kind="kana_handwriting"):
    return Question("ka", kind, "hiragana", "", "か", options=["か", "な"],
                    metadata={"kana_symbol": "か", "romaji": "ka", "content_version": "test"})


def draw_stroke(app, *points):
    """Write one stroke on the canvas: press at the first point, drag over the others, release at the last."""
    first, *middle, last = points
    app.ink_press(app.canvas, *first)
    for point in middle:
        app.ink_drag(app.canvas, *point)
    app.ink_release(app.canvas, *last)


def places(app):
    return [[point[:2] for point in stroke] for stroke in kana_support.strokes(app)]


@pytest.fixture
def pad_app(model_app):
    app = model_app
    app.loop = Mock()
    return app


# --- what the page and its handlers write ---------------------------------------------------------------------------

@pytest.mark.parametrize("free", [True, False])
def test_an_ink_page_starts_with_no_strokes_on_a_pad_of_the_standard_size(pad_app, free):
    app = pad_app
    old = kana_support.strokes(app)
    kana_support.with_state(app, ink_width=100, ink_height=50, question=ink_question())
    app.show_ink_page(free, "手写")
    assert app.page == ("free_ink" if free else "ink_question")
    assert kana_support.ink(app) == PAD
    assert kana_support.strokes(app) is not old and old == [[(12.0, 18.0, 1), (20.0, 30.0, 2)]]


def test_a_stroke_is_added_to_the_same_list_point_by_point(pad_app):
    app = pad_app
    app.show_ink_page(True, "手写")
    strokes = kana_support.strokes(app)
    draw_stroke(app, (10, 20), (30, 40), (50, 60))
    draw_stroke(app, (5, 5), (6, 6))
    assert kana_support.strokes(app) is strokes
    assert places(app) == [[(10.0, 20.0), (30.0, 40.0), (50.0, 60.0)], [(5.0, 5.0), (6.0, 6.0)]]
    assert all(isinstance(point[2], int) for stroke in strokes for point in stroke)


def test_a_stroke_that_starts_off_the_pad_is_not_written_and_one_that_leaves_it_ends_there(pad_app):
    app = pad_app
    app.show_ink_page(True, "手写")
    app.ink_press(app.canvas, 400, 20)
    app.ink_drag(app.canvas, 10, 20)
    assert kana_support.strokes(app) == []
    app.ink_press(app.canvas, 10, 20)
    app.ink_drag(app.canvas, 10, 300)
    app.ink_drag(app.canvas, 20, 30)
    assert places(app) == [[(10.0, 20.0)]]


def test_drawing_on_another_control_or_while_recognition_runs_writes_nothing(pad_app):
    app = pad_app
    app.show_ink_page(True, "手写")
    app.ink_press(View(), 10, 20)
    app._ink_busy = True
    app.ink_press(app.canvas, 10, 20)
    assert kana_support.strokes(app) == []


def test_the_pad_keeps_its_own_size_when_the_canvas_is_resized(pad_app):
    app = pad_app
    app.show_ink_page(True, "手写")
    app.ink_resize(app.canvas, 720, 420)
    assert (kana_support.ink_width(app), kana_support.ink_height(app)) == (360, 210)
    draw_stroke(app, (100, 200), (300, 400))
    assert places(app) == [[(50.0, 100.0), (150.0, 200.0)]]


def test_a_resize_is_ignored_while_the_app_has_no_strokes_at_all(pad_app):
    app = pad_app
    kana_support.forget_ink(app, "strokes")
    app._ink_view_width = "kept"
    app.ink_resize(app.canvas, 720, 420)
    assert app._ink_view_width == "kept"
    app._schedule_ink_draw.assert_not_called()


def test_a_resize_is_taken_once_the_app_holds_strokes_even_when_they_are_none(pad_app):
    app = pad_app
    kana_support.set_strokes(app, None)
    drawn = app._schedule_ink_draw.call_count
    app.ink_resize(app.canvas, 720, 420)
    assert (app._ink_view_width, app._ink_view_height) == (720, 420)
    assert app._schedule_ink_draw.call_count == drawn + 1


def test_undo_takes_the_last_stroke_from_the_same_list_and_clear_starts_a_new_one(pad_app):
    app = pad_app
    app.show_ink_page(True, "手写")
    strokes = kana_support.strokes(app)
    draw_stroke(app, (10, 20), (30, 40))
    draw_stroke(app, (5, 5), (6, 6))
    app.undo_ink()
    assert kana_support.strokes(app) is strokes and places(app) == [[(10.0, 20.0), (30.0, 40.0)]]
    app.undo_ink()
    app.undo_ink()
    assert kana_support.strokes(app) is strokes and strokes == []
    draw_stroke(app, (10, 20), (30, 40))
    app.clear_ink()
    assert kana_support.strokes(app) == [] and kana_support.strokes(app) is not strokes
    assert len(strokes) == 1   # the list that was replaced keeps its stroke
    assert (kana_support.ink_width(app), kana_support.ink_height(app)) == (360, 210)


def test_undo_and_clear_do_nothing_while_recognition_runs(pad_app):
    app = pad_app
    strokes = kana_support.strokes(app)
    app._ink_busy = True
    app.undo_ink()
    app.clear_ink()
    assert kana_support.strokes(app) is strokes and len(strokes) == 1


def test_recognition_is_given_a_copy_of_the_strokes_and_the_size_of_the_pad(pad_app):
    app = pad_app
    app.model_state = "ready"
    for name in ("_refresh_ink_action",):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))
    kana_support.with_state(app, ink_width=300, ink_height=200)
    strokes = kana_support.strokes(app)
    asyncio.run(app.recognize_ink())
    given, width, height = app.platform.recognize.call_args.args
    assert given == [[(12.0, 18.0, 1), (20.0, 30.0, 2)]] and given is not strokes and given[0] is not strokes[0]
    assert (width, height) == (300, 200)


@pytest.mark.parametrize("size", [{"ink_width": 300, "ink_height": 200}, {"ink_height": 0}, {}])
def test_deleting_the_learning_data_starts_a_new_list_of_strokes_and_leaves_the_size_of_the_pad(size):
    strokes = [[(1.0, 2.0, 3)]]
    app = kana_support.with_state(SimpleNamespace(
        page="settings", _page_token=1, store=Mock(), alert=Mock(), platform=Mock(),
        main_window=SimpleNamespace(dialog=AsyncMock(return_value=True)), back_stack=BackStack(),
        show_settings=Mock(), _ink_revision=0, _audio_serial=0, _active_stroke=strokes[0]),
        assisted_attempts=set(), answered_attempts=set(), session_id="s", questions=[], question_index=0,
        session_right=0, question=None, script="hiragana", strokes=strokes, **size)
    asyncio.run(KanaMemory.delete_learning_data(app))
    app.store.clear_learning_data.assert_called_once_with()
    assert kana_support.ink(app) == {"strokes": [], **size}
    assert kana_support.strokes(app) is not strokes and len(strokes) == 1 and app._active_stroke is None


# --- what a page kept for Back holds, and what Back gives back --------------------------------------------------------

def kept_page(app, name):
    """The page ``name`` as it is kept when the learner leaves it now."""
    app.page = name
    for method in ("_capture_page", "flush_drafts", "save_material_reference_draft"):
        setattr(app, method, MethodType(getattr(KanaMemory, method), app))
    return app._capture_page()


@pytest.mark.parametrize("name", ["free_ink", "ink_question"])
def test_a_kept_ink_page_holds_the_very_list_of_strokes_and_the_size_of_the_pad(pad_app, name):
    app = pad_app
    kana_support.with_state(app, ink_width=300, ink_height=200)
    held = kana_support.kept_ink_values(kept_page(app, name))
    assert held == {"strokes": kana_support.strokes(app), "ink_width": 300, "ink_height": 200}
    assert held["strokes"] is kana_support.strokes(app)


@pytest.mark.parametrize("name", ["question", "word_question", "feedback", "session_wait", "kana_detail", "home"])
def test_a_kept_page_that_is_no_ink_page_holds_nothing_of_the_pad(pad_app, name):
    assert kana_support.kept_ink_values(kept_page(pad_app, name)) == {}


def test_a_kept_ink_page_holds_only_what_the_app_holds_of_the_pad(pad_app):
    app = pad_app
    kana_support.forget_ink(app, "ink_width", "ink_height")
    assert set(kana_support.kept_ink_values(kept_page(app, "ink_question"))) == {"strokes"}
    kana_support.forget_ink(app, "strokes")
    assert kana_support.kept_ink_values(kept_page(app, "ink_question")) == {}


def away_from(name, **held):
    """A hand-made app on a detail page whose way back leads to a kept page ``name`` that holds ``held`` of the
    pad; the app itself holds other values."""
    question = SimpleNamespace(content_id="ka", kind="kana_choice")
    value = kana_support.with_state(SimpleNamespace(
        page="kana_detail", _view_key=("show_kana_detail", "ka"), back_stack=BackStack(),
        question_notice=SimpleNamespace(text=""), platform=Mock(),
        store=Mock(is_content_available=lambda content_id: True), alert=Mock(), _page_token=5, _ink_revision=2,
        _audio_serial=0, _paint_handle=None, content_slot=Mock(), title_label=SimpleNamespace(text=""),
        _checkpoint=Mock(), _sync_back_handler=Mock(), loop=Mock(), refresh_model_ui=Mock(),
        _schedule_ink_draw=Mock()),
        assisted_attempts=set(), answered_attempts=set(), answer_revealed=False, session_id="now",
        questions=[question], question_index=0, session_right=0, question=question, script="hiragana",
        strokes=[["now"]], ink_width=1, ink_height=2)
    value._attempt_key = MethodType(KanaMemory._attempt_key, value)
    value.back_stack.pages.append(PageState(
        ("show_question", None), name, "手写", object(),
        {**kana_support.kept_position(session_id="now", question_index=0), **kana_support.kept_ink(**held),
         "question_notice": SimpleNamespace(text="")}))
    return value


@pytest.mark.parametrize("name,held", [
    ("ink_question", {"strokes": [["then"]], "ink_width": 360, "ink_height": 210}),
    ("ink_question", {"strokes": []}),
    ("free_ink", {"ink_width": 300}),
    ("feedback", {}),
])
def test_back_gives_back_exactly_the_values_of_the_pad_the_kept_page_holds(name, held):
    value = away_from(name, **held)
    before = kana_support.ink(value)
    KanaMemory.go_back(value)
    assert value.page == name
    assert kana_support.ink(value) == {**before, **held}
    if "strokes" in held:
        assert kana_support.strokes(value) is held["strokes"]


@pytest.mark.parametrize("held", [{"strokes": [["then"]]}, {"ink_width": 300, "ink_height": 0}])
def test_back_over_an_app_that_holds_nothing_of_the_pad_gives_only_the_kept_values(held):
    value = away_from("ink_question", **held)
    kana_support.forget_ink(value, "strokes", "ink_width", "ink_height")
    KanaMemory.go_back(value)
    assert value.page == "ink_question" and kana_support.ink(value) == held


# --- the saved checkpoint ---------------------------------------------------------------------------------------------

INK_KEYS = {"strokes", "ink_width", "ink_height"}


def checkpoint_app(kind, **ink):
    """A hand-made practice on the page of a question of ``kind`` that holds ``ink`` of the pad."""
    question = ink_question(kind)
    store = Mock()
    app = kana_support.with_state(SimpleNamespace(
        page="ink_question" if "handwriting" in kind else "question", store=store, use_full_keyboard=False,
        _restoring_practice=False),
        assisted_attempts=set(), answered_attempts=set(), answer_revealed=False, answered_text="",
        question_audio_ok=True, session_id="s", questions=[question], question_index=0, session_right=0,
        question=question, script="hiragana", **ink)
    app.save_material_reference_draft = lambda: None
    return app


def saved_ink(app):
    KanaMemory._checkpoint(app)
    state = app.store.save_session_progress.call_args.args[4]
    return {name: value for name, value in state.items() if name in INK_KEYS}


@pytest.mark.parametrize("kind", ["kana_handwriting", "romaji_handwriting"])
def test_the_checkpoint_of_a_handwriting_question_saves_the_very_strokes_and_the_size_of_the_pad(kind):
    strokes = [[(1.0, 2.0, 3)]]
    state = saved_ink(checkpoint_app(kind, strokes=strokes, ink_width=300, ink_height=200))
    assert state == {"strokes": strokes, "ink_width": 300, "ink_height": 200} and state["strokes"] is strokes


def test_the_checkpoint_of_a_handwriting_question_saves_an_empty_standard_pad_for_one_that_was_never_set():
    assert saved_ink(checkpoint_app("kana_handwriting")) == PAD


@pytest.mark.parametrize("ink,saved", [
    ({"strokes": [[(1.0, 2.0, 3)]]}, {"strokes": [[(1.0, 2.0, 3)]], "ink_width": 360, "ink_height": 210}),
    ({"ink_width": 300}, {"strokes": [], "ink_width": 300, "ink_height": 210}),
    ({"strokes": [], "ink_height": 0}, {"strokes": [], "ink_width": 360, "ink_height": 0}),
    ({"strokes": None, "ink_width": None}, {"strokes": None, "ink_width": None, "ink_height": 210}),
])
def test_the_checkpoint_saves_the_standard_value_only_for_a_part_of_the_pad_that_was_never_set(ink, saved):
    assert saved_ink(checkpoint_app("kana_handwriting", **ink)) == saved


def test_the_checkpoint_of_another_question_saves_nothing_of_the_pad():
    assert saved_ink(checkpoint_app("kana_choice", strokes=[[(1.0, 2.0, 3)]], ink_width=300, ink_height=200)) == {}


# --- a problem report -------------------------------------------------------------------------------------------------

def report_of(app, page):
    return feedback_context._capture_practice(feedback_context._Capture(), app, page)


def report_app(**ink):
    question = ink_question()
    return kana_support.with_state(
        SimpleNamespace(use_full_keyboard=False, store=SimpleNamespace(practice_lane="main"), _active_stroke=None,
                        _ink_view_width=720, _ink_view_height=420, _ink_revision=4),
        session_id="s", questions=[question], question_index=0, session_right=0, question=question,
        script="hiragana", **ink)


@pytest.mark.parametrize("page", ["free_ink", "ink_question"])
def test_a_problem_report_of_an_ink_page_carries_a_copy_of_the_strokes_and_the_sizes(page):
    strokes = [[(1.0, 2.0, 3)]]
    result = report_of(report_app(strokes=strokes, ink_width=300, ink_height=200), page)["ink"]
    assert list(result) == ["strokes", "_active_stroke", "ink_width", "ink_height", "_ink_view_width",
                            "_ink_view_height", "_ink_revision"]
    assert result == {"strokes": [[[1.0, 2.0, 3]]], "_active_stroke": None, "ink_width": 300, "ink_height": 200,
                      "_ink_view_width": 720, "_ink_view_height": 420, "_ink_revision": 4}
    assert result["strokes"] is not strokes


def test_a_problem_report_of_an_ink_page_names_what_the_app_never_held_as_none():
    result = report_of(report_app(), "ink_question")["ink"]
    assert (result["strokes"], result["ink_width"], result["ink_height"]) == (None, None, None)


@pytest.mark.parametrize("ink,reported", [
    ({"strokes": [[(1.0, 2.0, 3)]]}, ([[[1.0, 2.0, 3]]], None, None)),
    ({"ink_width": 300}, (None, 300, None)),
    ({"strokes": [], "ink_height": 0}, ([], None, 0)),
])
def test_a_problem_report_of_an_ink_page_carries_each_part_of_the_pad_the_app_holds(ink, reported):
    result = report_of(report_app(**ink), "free_ink")["ink"]
    assert (result["strokes"], result["ink_width"], result["ink_height"]) == reported


def test_a_problem_report_of_another_page_carries_no_ink():
    assert "ink" not in report_of(report_app(strokes=[[(1.0, 2.0, 3)]]), "question")
