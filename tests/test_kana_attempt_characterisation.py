"""What the kana practice keeps about one attempt, pinned as it behaves today.

An attempt is one question of one practice session. The practice remembers whether the learner saw the answer or a
hint, the submitted text and its verdict, whether a heard question was played to its end, when the question page was
drawn and when the answer was first submitted; across the session it marks attempts as assisted (help was seen) or
answered. These tests press the page's buttons, or call the entry the page calls, and read that state through
``support.kana_support`` only. Every text and record they need is built here, so they run on the demo content.
"""
import asyncio
import sqlite3
from datetime import datetime, timezone
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import domain as domain_module
from kanamemory import extra_practice_runtime, feedback_context, feedback_exposure
from kanamemory.app import KanaMemory
from kanamemory.domain import Question, StudyStore
from kanamemory.navigation import BackStack, PageState
from kanamemory.session_content import RETIRE
from support import kana_support
from support.answer_option_ui_support import RECORDS, WORD, preview_app  # noqa: F401  (fixture)
from support.app_fixtures import app_module, question_app, setup_app  # noqa: F401  (fixtures)
from support.fake_ui import descendants


SUBMITTED = datetime(2030, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
RATED = datetime(2030, 5, 6, 7, 9, 0, tzinfo=timezone.utc)
KANA_REVEALED = "已查看答案或学习提示，本题仅安排再学一次。"
WORD_REVEALED = "已查看答案或学习提示，本题不计入无提示回忆。"
KANA_LOOKUP = "已查看学习提示，本题仅安排再学一次。"
WORD_LOOKUP = "已查看学习提示，本题不计入无提示回忆。"
PLAY_FIRST = "请先完整播放声音。语音失败不会计入复习。"
ANSWER_FIRST = "请先输入答案，或选择显示答案。"
ONLY_AGAIN = "已查看学习提示，本题只能选择再学一次。"


# --- building the pages -------------------------------------------------------------------------------------------

def clock(monkeypatch, start=1000.0):
    """The practice's monotonic clock, moved by the test: returns the list holding the current time."""
    now = [start]
    monkeypatch.setattr(app_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


def utc_clock(monkeypatch, *values):
    """The UTC clock of the answer and the rating: returns each value in turn, then repeats the last."""
    values = list(values)

    def now():
        return values.pop(0) if len(values) > 1 else values[0]
    monkeypatch.setattr(domain_module, "utc_now", now)


def kana_question(kind="kana_choice", content_id="ka", answer=None):
    record = next(r for r in RECORDS if r["id"] == content_id)
    value = answer or (record["romaji"] if kind in {"kana_choice", "kana_romaji"} else record["hiragana"])
    other = RECORDS[1]["romaji" if kind in {"kana_choice", "kana_romaji"} else "hiragana"]
    return Question(content_id, kind, "hiragana", "", value, options=[value, other],
                    metadata={"kana_symbol": record["hiragana"], "romaji": record["romaji"], "content_version": "test"})


def word_question(kind="word_meaning_kana"):
    return Question(WORD["id"], kind, "hiragana", "", WORD["reading"], options=["か", "な"],
                    metadata={"romaji": "kana", "meaning_hint": "假名", "content_version": "test",
                              "kana_answer": WORD["reading"]})


def draw(app, question):
    kana_support.set_questions(app, [question])
    app.show_question()
    return kana_support.question(app)


def answer_on_page(app, question, answer, *, page=None):
    """The learner submits ``answer`` on the question page of ``question`` (sound already played)."""
    draw(app, question)
    if page:
        app.page = page
    kana_support.set_question_audio_ok(app, True)
    app.check_answer(answer)


def pressed(app, caption):
    return next(v for v in descendants(app.body) if v.text == caption)


def queue_progress(app, monkeypatch, prepared_extra, *, assisted=(), state=None):
    """A saved queue whose next question is the app's first question, with ``prepared_extra`` in what the store
    prepares (a blocked or waiting head)."""
    app.store.practice_lane = "word_romaji"
    for name in ("show_word_drill_setup", "show_practice_hub", "show_home", "resume_practice", "continue_spaced_practice"):
        setattr(app, name, Mock())
    monkeypatch.setattr(app_module, "summarize_queue_availability",
                        lambda *a, **k: SimpleNamespace(waiting_count=1, ready_count=1))
    pending = {"session_id": kana_support.session_id(app), "questions": kana_support.questions(app), "question_index": 0, "session_right": 0,
               "state": state or {}, "assisted_indices": list(assisted)}
    app.store.load_session_progress.return_value = pending
    app.store.reconcile_session_kinds.return_value = None
    app.store.prepare_next_question.return_value = {**pending, **prepared_extra}


def all_evidence(app, **values):
    defaults = dict(answer_revealed=True, answered_text="before", answer_correct="kept", question_audio_ok="kept",
                    question_started=5.0, answer_submitted_at="kept-at", answer_duration_ms=77)
    return kana_support.with_state(app, **{**defaults, **values})


# --- a question page is drawn -------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind,heard", [
    ("kana_choice", False), ("romaji_kana", False), ("kana_romaji", False),
    ("word_kana", True), ("word_meaning_kana", False), ("word_romaji", False),
    ("kana_handwriting", True), ("romaji_handwriting", False),
])
@pytest.mark.parametrize("assisted", [False, True])
def test_drawing_a_question_starts_a_fresh_attempt_but_keeps_the_last_verdict(preview_app, monkeypatch, kind, heard,
                                                                              assisted):
    app = preview_app
    now = clock(monkeypatch, 250.0)
    monkeypatch.setattr(KanaMemory, "show_reverse_word_question", Mock())
    question = word_question(kind) if kind.startswith("word_") else kana_question(kind)
    all_evidence(app, answer_revealed=not assisted)
    if assisted:
        kana_support.assisted_attempts(app).add(("session", 0))
    now[0] = 260.5
    draw(app, question)
    assert kana_support.evidence(app) == {
        "answer_revealed": assisted, "answered_text": None, "answer_correct": "kept",
        "question_audio_ok": not heard, "question_started": 260.5, "answer_submitted_at": None,
        "answer_duration_ms": 0}


def test_an_assisted_mark_of_another_attempt_does_not_reveal_the_drawn_question(preview_app):
    app = preview_app
    kana_support.assisted_attempts(app).update({("session", 1), ("other", 0)})
    draw(app, kana_question())
    assert kana_support.answer_revealed(app) is False


@pytest.mark.parametrize("blocked", ["content_blocked", "settings_blocked", "dependency_blocked", "waiting"])
def test_a_page_that_stops_the_queue_clears_only_revealed_text_and_verdict(preview_app, monkeypatch, blocked):
    app = preview_app
    question = kana_question()
    if blocked == "content_blocked":
        question = Question(question.content_id, question.kind, question.script, "", question.answer,
                            options=question.options, metadata={**question.metadata, RETIRE: "retired-ka"})
    kana_support.set_questions(app, [question])
    extra = {"waiting_until_utc": "2030-01-01T09:00:00+00:00"} if blocked == "waiting" else {blocked: True}
    queue_progress(app, monkeypatch, extra)
    all_evidence(app)
    app.show_question()
    assert app.page == ("dependency_wait" if blocked == "dependency_blocked" else "session_wait")
    assert kana_support.evidence(app) == {
        "answer_revealed": False, "answered_text": None, "answer_correct": False, "question_audio_ok": "kept",
        "question_started": 5.0, "answer_submitted_at": "kept-at", "answer_duration_ms": 77}


def test_drawing_from_a_prepared_queue_rebuilds_the_assisted_marks_from_its_saved_indices(preview_app, monkeypatch):
    app = preview_app
    kana_support.set_questions(app, [kana_question(), kana_question("kana_choice", "na")])
    queue_progress(app, monkeypatch, {}, assisted=[1], state={"answer_revealed": True})
    kana_support.set_assisted_attempts(app, {("session", 5), ("old", 0)})
    kana_support.answered_attempts(app).add(("session", 0))
    app.show_question()
    assert kana_support.assisted_attempts(app) == {("session", 1), ("session", 0)}
    assert kana_support.answer_revealed(app) is True
    assert kana_support.answered_attempts(app) == {("session", 0)}


# --- a submit -----------------------------------------------------------------------------------------------------

def test_a_heard_question_cannot_be_submitted_before_its_sound_was_played(preview_app):
    app = preview_app
    draw(app, word_question("word_kana"))
    kana_support.set_answer_correct(app, "no verdict yet")
    kana_support.assisted_attempts(app).add(("session", 0))
    before = kana_support.evidence(app), kana_support.marks(app)
    app.check_answer("かな")
    app.alert.assert_called_once_with(PLAY_FIRST)
    assert app.page == "word_question"
    assert (kana_support.evidence(app), kana_support.marks(app)) == before


def test_an_empty_answer_is_refused_unless_the_answer_was_revealed(preview_app):
    app = preview_app
    draw(app, kana_question("kana_romaji"))
    kana_support.set_answer_correct(app, "no verdict yet")
    before = kana_support.evidence(app), kana_support.marks(app)
    app.check_answer("  ")
    app.alert.assert_called_once_with(ANSWER_FIRST)
    assert app.page == "question" and (kana_support.evidence(app), kana_support.marks(app)) == before


def test_an_empty_answer_after_help_was_seen_is_taken_as_revealed(preview_app):
    app = preview_app
    draw(app, kana_question("kana_romaji"))
    kana_support.assisted_attempts(app).add(("session", 0))
    app.check_answer("")
    app.alert.assert_not_called()
    assert app.page == "feedback" and kana_support.answer_revealed(app) is True
    assert kana_support.answered_text(app) == ""


def test_a_submit_on_another_page_changes_nothing(preview_app):
    app = preview_app
    draw(app, kana_question())
    kana_support.set_answer_correct(app, "no verdict yet")
    app.page = "feedback"
    before = kana_support.evidence(app), kana_support.marks(app)
    app.check_answer("ka")
    assert (kana_support.evidence(app), kana_support.marks(app)) == before
    app.alert.assert_not_called()


@pytest.mark.parametrize("answer,correct", [("ka", True), ("na", False)])
def test_a_first_submit_keeps_the_text_freezes_its_time_and_marks_the_attempt_answered(preview_app, monkeypatch,
                                                                                       answer, correct):
    app = preview_app
    now = clock(monkeypatch, 40.0)
    utc_clock(monkeypatch, SUBMITTED)
    draw(app, kana_question())
    now[0] = 42.3456
    pressed(app, answer).on_press(None)
    assert app.page == "feedback"
    assert kana_support.evidence(app) == {
        "answer_revealed": False, "answered_text": answer, "answer_correct": correct, "question_audio_ok": True,
        "question_started": 40.0, "answer_submitted_at": SUBMITTED.isoformat(), "answer_duration_ms": 2345}
    assert kana_support.marks(app) == (set(), {("session", 0)})


def test_a_second_submit_of_the_same_attempt_keeps_the_first_time_and_duration(preview_app, monkeypatch):
    app = preview_app
    now = clock(monkeypatch, 10.0)
    utc_clock(monkeypatch, SUBMITTED, RATED)
    draw(app, kana_question())
    now[0] = 11.0
    app.check_answer("na")
    app.page = "question"
    now[0] = 30.0
    app.check_answer("ka")
    assert kana_support.answered_text(app) == "ka" and kana_support.answer_correct(app) is True
    assert kana_support.answer_submitted_at(app) == SUBMITTED.isoformat()
    assert kana_support.answer_duration_ms(app) == 1000


def test_a_submit_before_the_page_was_drawn_counts_no_negative_duration(preview_app, monkeypatch):
    app = preview_app
    now = clock(monkeypatch, 10.0)
    draw(app, kana_question())
    now[0] = 9.0
    app.check_answer("ka")
    assert kana_support.answer_duration_ms(app) == 0


def test_a_submit_after_help_was_seen_is_revealed_assisted_and_never_correct(preview_app):
    app = preview_app
    draw(app, kana_question())
    kana_support.assisted_attempts(app).add(("session", 0))
    app.check_answer("ka")
    assert kana_support.answer_revealed(app) is True and kana_support.answer_correct(app) is False
    assert kana_support.marks(app) == ({("session", 0)}, {("session", 0)})


def test_a_revealed_attempt_may_submit_an_empty_answer_and_is_marked_assisted(preview_app):
    app = preview_app
    draw(app, kana_question("kana_romaji"))
    kana_support.set_answer_revealed(app, True)
    app.check_answer("")
    app.alert.assert_not_called()
    assert app.page == "feedback" and kana_support.answered_text(app) == ""
    assert kana_support.answer_correct(app) is False
    assert kana_support.marks(app) == ({("session", 0)}, {("session", 0)})


# --- showing the answer -------------------------------------------------------------------------------------------

def reveal_page(audio_ok):
    seen = []
    page = kana_support.with_state(SimpleNamespace(alert=Mock()), question_audio_ok=audio_ok, answer_revealed=False)
    page.check_answer = Mock(side_effect=lambda text: seen.append((text, kana_support.answer_revealed(page))))
    return page, seen


def test_showing_the_answer_waits_for_the_sound():
    page, seen = reveal_page(False)
    KanaMemory.reveal_answer(page)
    page.alert.assert_called_once_with(PLAY_FIRST)
    assert seen == [] and kana_support.answer_revealed(page) is False


def test_showing_the_answer_reveals_then_submits_an_empty_answer():
    page, seen = reveal_page(True)
    KanaMemory.reveal_answer(page)
    assert seen == [("", True)]
    page.alert.assert_not_called()


# --- a rating -----------------------------------------------------------------------------------------------------

@pytest.fixture
def rating_app(preview_app, monkeypatch):
    app = preview_app
    app.store.record_review.return_value = {"scheduled": True}
    dispatched = Mock()
    monkeypatch.setattr(KanaMemory, "_dispatch_after_answer", dispatched)
    app.dispatched = dispatched
    return app


def review_arguments(app):
    call = app.store.record_review.call_args
    names = ("answered_text", "outcome", "audio_ok", "assisted", "duration_ms", "now", "finalized_at")
    return call.args[3], {name: call.kwargs[name] for name in names}


@pytest.mark.parametrize("answer,outcome,rating", [("ka", "correct", 3), ("na", "incorrect", 1)])
def test_a_kana_rating_records_the_evidence_with_the_time_of_the_answer(rating_app, monkeypatch, answer, outcome,
                                                                         rating):
    app = rating_app
    now = clock(monkeypatch, 0.0)
    utc_clock(monkeypatch, SUBMITTED, RATED)
    draw(app, kana_question())
    now[0] = 1.5
    app.check_answer(answer)
    KanaMemory.save_rating(app, rating)
    assert review_arguments(app) == (rating, {
        "answered_text": answer, "outcome": outcome, "audio_ok": True, "assisted": False, "duration_ms": 1500,
        "now": SUBMITTED, "finalized_at": RATED})
    assert kana_support.question_index(app) == 1 and kana_support.session_right(app) == int(outcome == "correct")
    app.dispatched.assert_called_once_with(app)


def test_a_word_rating_records_the_time_of_the_rating_and_no_grade(rating_app, monkeypatch):
    app = rating_app
    now = clock(monkeypatch, 0.0)
    utc_clock(monkeypatch, SUBMITTED, RATED)
    draw(app, word_question())
    now[0] = 0.25
    for kana in WORD["reading"]:
        app.append_word_key(kana)
    pressed(app, "提交答案").on_press(None)
    KanaMemory.save_rating(app, None)
    assert review_arguments(app) == (None, {
        "answered_text": WORD["reading"], "outcome": "correct", "audio_ok": True, "assisted": False,
        "duration_ms": 250, "now": RATED, "finalized_at": RATED})


def test_a_kana_rating_without_a_submission_time_records_no_answer_time(rating_app, monkeypatch):
    app = rating_app
    utc_clock(monkeypatch, SUBMITTED, RATED)
    answer_on_page(app, kana_question(), "ka")
    kana_support.set_answer_submitted_at(app, None)
    KanaMemory.save_rating(app, 3)
    assert review_arguments(app)[1]["now"] is None


def test_a_rating_passes_on_the_sound_flag_of_the_attempt_as_it_stands(rating_app):
    # A heard question cannot reach its result unplayed through the page; the flag is still passed on as it is.
    app = rating_app
    answer_on_page(app, kana_question(), "ka")
    kana_support.set_question_audio_ok(app, False)
    KanaMemory.save_rating(app, 3)
    assert review_arguments(app)[1]["audio_ok"] is False


def test_a_revealed_kana_attempt_takes_only_again(rating_app):
    app = rating_app
    draw(app, kana_question())
    app.reveal_answer()
    app.alert.reset_mock()
    KanaMemory.save_rating(app, 3)
    app.alert.assert_called_once_with(ONLY_AGAIN)
    app.store.record_review.assert_not_called()
    KanaMemory.save_rating(app, 1)
    assert review_arguments(app)[0] == 1
    assert review_arguments(app)[1]["assisted"] is True and review_arguments(app)[1]["outcome"] == "incorrect"


def test_help_seen_after_the_submit_turns_the_rating_into_only_again(rating_app):
    app = rating_app
    answer_on_page(app, kana_question(), "ka")
    assert kana_support.answer_revealed(app) is False
    kana_support.assisted_attempts(app).add(("session", 0))
    KanaMemory.save_rating(app, 3)
    assert kana_support.answer_revealed(app) is True
    app.alert.assert_called_once_with(ONLY_AGAIN)
    app.store.record_review.assert_not_called()


def test_a_revealed_word_attempt_is_saved_as_assisted_without_an_alert(rating_app):
    app = rating_app
    draw(app, word_question())
    app.reveal_answer()
    KanaMemory.save_rating(app, 3)
    app.alert.assert_not_called()
    assert review_arguments(app)[0] is None and review_arguments(app)[1]["assisted"] is True


@pytest.mark.parametrize("failure,message", [
    (sqlite3.OperationalError("database is locked"), "数据库正忙，复习记录尚未保存。请稍后再点一次评级。"),
    (RuntimeError("disk full"), "复习记录尚未保存：disk full"),
])
def test_a_failed_rating_keeps_the_position_and_can_be_pressed_again(rating_app, failure, message):
    app = rating_app
    answer_on_page(app, kana_question(), "ka")
    app.store.record_review.side_effect = [failure, {"scheduled": True}]
    KanaMemory.save_rating(app, 3)
    app.alert.assert_called_once_with(message)
    assert app.page == "feedback" and kana_support.question_index(app) == 0 and kana_support.session_right(app) == 0
    app.dispatched.assert_not_called()
    KanaMemory.save_rating(app, 3)
    assert app.store.record_review.call_count == 2 and kana_support.question_index(app) == 1
    app.dispatched.assert_called_once_with(app)


def test_a_rating_the_store_does_not_schedule_keeps_the_position(rating_app):
    app = rating_app
    answer_on_page(app, kana_question(), "ka")
    app.store.record_review.return_value = {"scheduled": False, "reason": "已过期"}
    KanaMemory.save_rating(app, 3)
    app.alert.assert_called_once_with("这次作答未安排复习：已过期")
    assert kana_support.question_index(app) == 0
    app.dispatched.assert_not_called()


def test_a_rating_off_the_result_page_does_nothing(rating_app):
    app = rating_app
    draw(app, kana_question())
    kana_support.assisted_attempts(app).add(("session", 0))
    KanaMemory.save_rating(app, 3)
    assert kana_support.answer_revealed(app) is False
    app.store.record_review.assert_not_called()


# --- the sound ----------------------------------------------------------------------------------------------------

def playback_page(change=None, failure=None):
    """A heard word question on its page; the playback runs ``change`` on the page while the sound plays."""
    question = Question("word", "word_kana", "hiragana", "", "かな", audio_text="かな")
    page = kana_support.with_state(SimpleNamespace(_page_token=3, _audio_serial=0,
        repo=Mock(), _checkpoint=Mock(), alert=Mock()), question_audio_ok=False,
        question=question)

    async def play(text):
        if change:
            change(page)
        if failure:
            raise failure
    page.platform = SimpleNamespace(play=AsyncMock(side_effect=play))
    return page


def test_a_sound_played_to_its_end_on_the_same_page_counts_and_is_saved():
    page = playback_page()
    asyncio.run(KanaMemory.play_question_audio(page))
    assert kana_support.question_audio_ok(page) is True
    page._checkpoint.assert_called_once_with()
    assert page._audio_serial == 1


@pytest.mark.parametrize("change", [
    pytest.param(lambda p: setattr(p, "_page_token", 4), id="page-left"),
    pytest.param(lambda p: kana_support.set_question(p, Question("x", "word_kana", "hiragana", "", "x")), id="question"),
    pytest.param(lambda p: setattr(p, "_audio_serial", p._audio_serial + 1), id="later-playback"),
])
def test_a_sound_that_ends_after_the_page_moved_on_does_not_count(change):
    page = playback_page(change)
    asyncio.run(KanaMemory.play_question_audio(page))
    assert kana_support.question_audio_ok(page) is False
    page._checkpoint.assert_not_called()


@pytest.mark.parametrize("change,alerted", [
    pytest.param(None, True, id="same-page"),
    pytest.param(lambda p: kana_support.set_question(p, Question("x", "word_kana", "hiragana", "", "x")), True,
                 id="question-changed"),
    pytest.param(lambda p: setattr(p, "_page_token", 4), False, id="page-left"),
    pytest.param(lambda p: setattr(p, "_audio_serial", p._audio_serial + 1), False, id="later-playback"),
])
def test_a_failed_sound_alerts_only_while_its_page_and_playback_are_current(change, alerted):
    page = playback_page(change, RuntimeError("no voice"))
    asyncio.run(KanaMemory.play_question_audio(page))
    assert kana_support.question_audio_ok(page) is False
    assert page.alert.call_args_list == ([(("no voice",),)] if alerted else [])
    page._checkpoint.assert_not_called()


# --- a lookup during an attempt -----------------------------------------------------------------------------------

def lookup_page(page="question", kind="kana_choice", session="session-1"):
    value = kana_support.with_state(SimpleNamespace(page=page, back_stack=BackStack(), question_notice=SimpleNamespace(text=""),
        _checkpoint=Mock()),
        assisted_attempts=set(), answered_attempts=set(), answer_revealed=False,
        session_id=session, question_index=0, question=SimpleNamespace(kind=kind))
    value._attempt_key = MethodType(KanaMemory._attempt_key, value)
    return value


def kept_question(session="session-1", index=0, name="question"):
    return PageState(("show_question", None), name, "练习", object(),
                     kana_support.kept_position(session_id=session, question_index=index))


@pytest.mark.parametrize("kind,notice", [("kana_choice", KANA_LOOKUP), ("word_meaning_kana", WORD_LOOKUP)])
def test_a_lookup_on_the_question_page_marks_the_attempt_assisted(kind, notice):
    value = lookup_page(kind=kind)
    KanaMemory._mark_lookup_assisted(value)
    assert kana_support.marks(value) == ({("session-1", 0)}, set())
    assert kana_support.answer_revealed(value) is True
    assert value.question_notice.text == notice
    value._checkpoint.assert_called_once_with()


@pytest.mark.parametrize("name", ["question", "word_question", "ink_question"])
def test_a_lookup_away_from_a_kept_question_page_of_the_attempt_marks_it_without_a_notice(name):
    value = lookup_page(page="kana_detail")
    value.back_stack.pages.append(kept_question(name=name))
    KanaMemory._mark_lookup_assisted(value)
    assert kana_support.assisted_attempts(value) == {("session-1", 0)}
    assert kana_support.answer_revealed(value) is True and value.question_notice.text == ""
    value._checkpoint.assert_called_once_with()


@pytest.mark.parametrize("case", ["answered", "no-session", "no-page", "other-index", "other-session", "result-page"])
def test_a_lookup_outside_an_unanswered_attempt_changes_nothing(case):
    value = lookup_page(page="question" if case in {"answered", "no-session"} else "kana_detail",
                        session=None if case == "no-session" else "session-1")
    if case == "answered":
        kana_support.answered_attempts(value).add(("session-1", 0))
    elif case == "other-index":
        value.back_stack.pages.append(kept_question(index=1))
    elif case == "other-session":
        value.back_stack.pages.append(kept_question(session="session-2"))
    elif case == "result-page":
        value.back_stack.pages.append(kept_question(name="feedback"))
    before = kana_support.marks(value)
    KanaMemory._mark_lookup_assisted(value)
    assert kana_support.marks(value) == before and kana_support.answer_revealed(value) is False
    assert value.question_notice.text == ""
    value._checkpoint.assert_not_called()


# --- Back -------------------------------------------------------------------------------------------------------

def back_page(page, kind="kana_choice"):
    question = SimpleNamespace(content_id="ka", kind=kind)
    value = kana_support.with_state(SimpleNamespace(
        page=page, _view_key=("show_question", None),
        back_stack=BackStack(), question_notice=SimpleNamespace(text=""), platform=Mock(), store=Mock(is_content_available=lambda content_id: True), alert=Mock(),
        _page_token=5, _ink_revision=2, _audio_serial=0, _paint_handle=None, content_slot=Mock(),
        title_label=SimpleNamespace(text=""), _checkpoint=Mock(), _sync_back_handler=Mock(), loop=Mock()),
        assisted_attempts=set(), answered_attempts=set(),
        session_id="session-1", question_index=0, question=question)
    value._attempt_key = MethodType(KanaMemory._attempt_key, value)
    return value, question


def kept_with_evidence(question, notice, name="question", **evidence):
    bindings = {**kana_support.kept_position(session_id="session-1", question_index=0, question=question),
                "question_notice": notice, **kana_support.kept_evidence(**evidence)}
    return PageState(("show_question", None), name, "练习", object(), bindings)


SEVEN = {"answer_revealed": False, "answered_text": None, "answer_correct": False, "question_audio_ok": True,
         "question_started": 12.5, "answer_submitted_at": None, "answer_duration_ms": 0}


def test_back_on_the_result_page_asks_to_leave_and_keeps_the_attempt_answered():
    # The result page is a focused practice page: Back asks whether to leave the practice and never returns to the
    # kept question page, so the rule for "returning after seeing the result" is not reached from here.
    value, question = back_page("feedback")
    value.store.practice_lane = "main"
    all_evidence(value, answer_revealed=False, answered_text="ka", answer_correct=True)
    kana_support.answered_attempts(value).add(("session-1", 0))
    notice = SimpleNamespace(text="")
    value.back_stack.pages.append(kept_with_evidence(question, notice, **SEVEN))
    before = kana_support.evidence(value)
    KanaMemory.go_back(value)
    asked = value.loop.create_task.call_args.args[0]
    asked.close()
    assert value.page == "feedback" and len(value.back_stack.pages) == 1
    assert kana_support.marks(value) == (set(), {("session-1", 0)})
    assert kana_support.evidence(value) == before and notice.text == ""


@pytest.mark.parametrize("kind,notice", [("kana_choice", KANA_REVEALED), ("word_meaning_kana", WORD_REVEALED)])
def test_back_to_a_kept_question_page_recomputes_revealed_from_the_marks(kind, notice):
    value, question = back_page("kana_detail", kind)
    kana_support.assisted_attempts(value).add(("session-1", 0))
    shown = SimpleNamespace(text="")
    value.back_stack.pages.append(kept_with_evidence(question, shown, **SEVEN))
    KanaMemory.go_back(value)
    assert kana_support.answer_revealed(value) is True and shown.text == notice
    assert kana_support.marks(value) == ({("session-1", 0)}, set())


def test_back_to_a_kept_question_page_without_help_shows_no_notice():
    value, question = back_page("kana_detail")
    shown = SimpleNamespace(text="")
    value.back_stack.pages.append(kept_with_evidence(question, shown, **SEVEN))
    KanaMemory.go_back(value)
    assert kana_support.answer_revealed(value) is False and shown.text == ""


def test_back_from_a_detail_page_is_not_a_return_after_the_result():
    value, question = back_page("kana_detail")
    kana_support.answered_attempts(value).add(("session-1", 0))
    value.back_stack.pages.append(kept_with_evidence(question, SimpleNamespace(text=""), **SEVEN))
    KanaMemory.go_back(value)
    assert kana_support.marks(value) == (set(), {("session-1", 0)})


def test_back_to_a_kept_page_restores_the_evidence_of_its_moment_but_not_the_marks():
    value, question = back_page("kana_detail")
    all_evidence(value, answer_revealed=False)
    kana_support.set_assisted_attempts(value, {("session-1", 3)})
    kana_support.set_answered_attempts(value, {("session-1", 2)})
    kept = {"answer_revealed": False, "answered_text": "kept-text", "answer_correct": "kept-verdict",
            "question_audio_ok": "kept-audio", "question_started": 1.25, "answer_submitted_at": "kept-submitted",
            "answer_duration_ms": 900}
    value.back_stack.pages.append(kept_with_evidence(question, SimpleNamespace(text=""), name="feedback", **kept))
    KanaMemory.go_back(value)
    assert value.page == "feedback"
    assert kana_support.evidence(value) == kept
    assert kana_support.marks(value) == ({("session-1", 3)}, {("session-1", 2)})


# --- a restore ----------------------------------------------------------------------------------------------------

@pytest.fixture
def restore(preview_app, tmp_path):
    app = preview_app
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        app.store = store
        app._checkpoint = MethodType(KanaMemory._checkpoint, app)
        app.resume_practice = MethodType(KanaMemory.resume_practice, app)

        def saved(questions, state, assisted=(), index=0):
            session = store.start_session(questions)
            store.save_session_progress(session, index, 0, list(assisted), state)
            return session
        yield app, store, saved


def test_a_restore_rebuilds_the_assisted_marks_and_empties_the_answered_ones(restore):
    app, store, saved = restore
    session = saved([kana_question(), kana_question("kana_choice", "na")], {"page": "question"}, assisted=[1], index=1)
    kana_support.set_assisted_attempts(app, {("earlier", 0)})
    kana_support.set_answered_attempts(app, {("earlier", 0)})
    app.resume_practice()
    assert app.page == "question" and kana_support.question_index(app) == 1
    assert kana_support.marks(app) == ({(session, 1)}, set())
    assert kana_support.answer_revealed(app) is True
    assert app.question_notice.text == KANA_REVEALED


@pytest.mark.parametrize("revealed,expected", [(False, {("s", 2)}), (True, {("s", 2), ("s", 1)})])
def test_a_restore_sets_the_marks_itself_before_the_question_is_drawn(preview_app, revealed, expected):
    # The drawing that follows rebuilds the marks again from the prepared queue; this pins what the restore
    # itself has set by the time it draws.
    app = preview_app
    app.store.practice_lane = "word_romaji"
    app.store.reconcile_session_kinds.return_value = None
    app.store.load_session_progress.return_value = {
        "session_id": "s", "questions": [kana_question(), kana_question(), kana_question()], "question_index": 1,
        "session_right": 0, "assisted_indices": [2], "state": {"page": "question", "answer_revealed": revealed}}
    kana_support.set_assisted_attempts(app, {("old", 0)})
    kana_support.set_answered_attempts(app, {("s", 1)})
    seen = []
    app.show_question = Mock(side_effect=lambda **kw: seen.append(kana_support.marks(app)))
    KanaMemory.resume_practice(app)
    assert seen == [(expected, set())]


def test_a_restore_of_a_revealed_question_marks_its_attempt_assisted(restore):
    app, store, saved = restore
    session = saved([kana_question()], {"page": "question", "answer_revealed": True})
    app.resume_practice()
    assert kana_support.assisted_attempts(app) == {(session, 0)}
    assert kana_support.answer_revealed(app) is True


@pytest.mark.parametrize("preserve,saved_audio,expected", [
    (True, True, True), (True, False, False), (False, True, False)])
def test_a_restore_keeps_a_played_sound_only_when_asked_to(restore, preserve, saved_audio, expected):
    app, store, saved = restore
    saved([word_question("word_kana")], {"page": "word_question", "question_audio_ok": saved_audio})
    app.resume_practice(preserve_audio=preserve)
    assert app.page == "word_question" and kana_support.question_audio_ok(app) is expected


def test_a_restored_result_replays_the_submit_with_the_saved_time_and_duration(restore, monkeypatch):
    app, store, saved = restore
    clock(monkeypatch, 500.0)
    utc_clock(monkeypatch, RATED)
    session = saved([word_question("word_kana")], {"page": "feedback", "feedback_pending": True,
        "answered_text": "なか", "question_audio_ok": True, "submitted_at_utc": SUBMITTED.isoformat(),
        "answer_duration_ms": 4321})
    app.resume_practice()
    assert app.page == "feedback"
    assert kana_support.evidence(app) == {
        "answer_revealed": False, "answered_text": "なか", "answer_correct": False, "question_audio_ok": True,
        "question_started": 500.0, "answer_submitted_at": SUBMITTED.isoformat(), "answer_duration_ms": 4321}
    assert kana_support.marks(app) == (set(), {(session, 0)})
    app.alert.assert_not_called()


def test_a_restored_result_of_an_unheard_sound_is_not_submitted(restore):
    app, store, saved = restore
    saved([word_question("word_kana")], {"page": "feedback", "feedback_pending": True, "answered_text": "かな",
                                         "question_audio_ok": False})
    app.resume_practice()
    assert app.page == "word_question" and kana_support.answered_text(app) is None
    app.alert.assert_called_once_with(PLAY_FIRST)


def test_a_restored_result_without_a_saved_time_takes_the_time_of_the_replayed_submit(restore, monkeypatch):
    app, store, saved = restore
    clock(monkeypatch, 500.0)
    utc_clock(monkeypatch, RATED)
    saved([kana_question()], {"page": "feedback", "feedback_pending": True, "answered_text": "ka"})
    app.resume_practice()
    assert app.page == "feedback" and kana_support.answer_correct(app) is True
    assert kana_support.answer_submitted_at(app) == RATED.isoformat()
    assert kana_support.answer_duration_ms(app) == 0


# --- the checkpoint -----------------------------------------------------------------------------------------------

def checkpoint_page(page, question=None, **evidence):
    value = kana_support.with_state(SimpleNamespace(page=page, store=Mock(),
        use_full_keyboard=False), **evidence,
        session_id="s", question_index=0, session_right=0, questions=[question or kana_question()],
        question=question or kana_question())
    return value


def saved_state(value):
    KanaMemory._checkpoint(value)
    call = value.store.save_session_progress.call_args
    return call.args[3], call.args[4]


def test_a_question_page_saves_its_evidence_and_the_assisted_indices_of_its_session():
    value = checkpoint_page("question", answer_revealed=1, answered_text=None, question_audio_ok=0,
                            assisted_attempts={("s", 2), ("s", 0), ("t", 1)}, answered_attempts=set(),
                            answer_submitted_at="ignored", answer_duration_ms=5, answer_correct=True)
    assisted, state = saved_state(value)
    assert sorted(assisted) == [0, 2]
    assert state == {"page": "question", "answer_revealed": True, "feedback_pending": False, "answered_text": "",
                     "question_audio_ok": False, "use_full_keyboard": False}
    assert state["answer_revealed"] is True and state["question_audio_ok"] is False


def test_a_question_page_never_drawn_saves_the_absent_evidence_as_not_seen():
    value = checkpoint_page("question", assisted_attempts=set())
    assert saved_state(value)[1] == {"page": "question", "answer_revealed": False, "feedback_pending": False,
                                     "answered_text": "", "question_audio_ok": False, "use_full_keyboard": False}


def test_the_result_page_saves_the_submission_time_the_duration_and_the_verdict():
    value = checkpoint_page("feedback", answer_revealed=False, answered_text="ka", question_audio_ok=True,
                            answer_submitted_at=SUBMITTED.isoformat(), answer_duration_ms=812, answer_correct=1,
                            assisted_attempts=set())
    state = saved_state(value)[1]
    assert state == {"page": "feedback", "answer_revealed": False, "feedback_pending": True,
        "answered_text": "ka", "question_audio_ok": True, "use_full_keyboard": False,
        "submitted_at_utc": SUBMITTED.isoformat(), "answer_duration_ms": 812, "answer_correct": True}
    assert state["answer_correct"] is True


def test_the_result_page_without_a_submission_time_saves_no_timing():
    value = checkpoint_page("feedback", answer_revealed=True, answered_text="", question_audio_ok=True,
                            answer_submitted_at=None, assisted_attempts={("s", 0)})
    assisted, state = saved_state(value)
    assert assisted == [0]
    assert "submitted_at_utc" not in state and "answer_duration_ms" not in state and "answer_correct" not in state


@pytest.mark.parametrize("page", ["session_wait", "dependency_wait"])
def test_a_wait_page_saves_none_of_the_evidence(page):
    value = checkpoint_page(page, answer_revealed=True, answered_text="ka", question_audio_ok=True,
                            answer_submitted_at=SUBMITTED.isoformat(), answer_duration_ms=812, answer_correct=True,
                            assisted_attempts={("s", 0)})
    assert saved_state(value) == ([0], {"page": page, "use_full_keyboard": False})


# --- queue changes ------------------------------------------------------------------------------------------------

def reconcile_page(revised):
    value = kana_support.with_state(SimpleNamespace(back_stack=BackStack(), platform=Mock(), alert=Mock(), store=Mock(),
        _practice_preferences=KanaMemory._clean_preferences({}), _ink_revision=0, _audio_serial=0),
        assisted_attempts={("s", 4)}, answered_attempts={("s", 4)},
        session_id="s", questions=[], question_index=0, session_right=0, question=None)
    value.store.practice_lane = "word_romaji"
    value.store.reconcile_session_kinds.return_value = revised
    return value


def test_a_reconcile_that_changed_the_queue_rebuilds_the_assisted_marks_and_empties_the_answered_ones():
    questions = [kana_question(), kana_question("kana_choice", "na")]
    value = reconcile_page({"changed": True, "session_id": "t", "questions": questions, "question_index": 1,
                            "session_right": 0, "assisted_indices": [0, 1]})
    kept = kana_support.answered_attempts(value)
    KanaMemory._reconcile_practice_kinds(value, {"session_id": "s", "questions": questions})
    assert kana_support.marks(value) == ({("t", 0), ("t", 1)}, set())
    assert kana_support.answered_attempts(value) is kept


def test_a_reconcile_that_changed_nothing_keeps_both_marks():
    value = reconcile_page({"changed": False})
    KanaMemory._reconcile_practice_kinds(value, {"session_id": "s", "questions": [kana_question()]})
    assert kana_support.marks(value) == ({("s", 4)}, {("s", 4)})


def test_a_new_practice_from_the_setup_page_empties_both_marks_in_place(setup_app, monkeypatch):
    app = setup_app
    assisted, answered = {("old", 0)}, {("old", 1)}
    kana_support.with_state(app, assisted_attempts=assisted, answered_attempts=answered)
    monkeypatch.setattr(app_module, "build_questions", Mock(return_value=[kana_question()]))
    asyncio.run(app.start_practice())
    app.show_question.assert_called_once()
    assert kana_support.assisted_attempts(app) is assisted and kana_support.answered_attempts(app) is answered
    assert assisted == answered == set()


def test_a_new_word_drill_empties_both_marks_in_place(monkeypatch):
    assisted, answered = {("old", 0)}, {("old", 1)}
    app = kana_support.with_state(SimpleNamespace(page="word_drill_setup", _page_token=1, store=Mock(), repo=Mock(),
        alert=Mock(), back_stack=BackStack(), show_question=Mock(), refresh_word_drill_setup=Mock(),
        word_drill_start_button=SimpleNamespace(enabled=True, text="")),
        assisted_attempts=assisted, answered_attempts=answered)
    app.store._practice_run_enabled = False
    app.store.load_session_progress.return_value = None
    app.store.start_session.return_value = "drill"
    monkeypatch.setattr(KanaMemory, "_word_drill_config", lambda self: {"word_table": "partial"})
    monkeypatch.setattr(KanaMemory, "_activate_practice_lane", lambda self, lane: True)
    monkeypatch.setattr(KanaMemory, "_reconcile_practice_kinds", lambda self, *a: None)
    monkeypatch.setattr(app_module, "build_word_romaji_drill", lambda *a, **k: [word_question("word_romaji")])
    asyncio.run(KanaMemory.start_word_drill(app))
    app.alert.assert_not_called()
    app.show_question.assert_called_once_with()
    assert kana_support.session_id(app) == "drill"
    assert kana_support.assisted_attempts(app) is assisted and kana_support.answered_attempts(app) is answered
    assert assisted == answered == set()


def test_a_restored_learning_backup_empties_both_marks_in_place(monkeypatch):
    from kanamemory import backup_ui

    async def stage(app, action, title, data=None):
        return SimpleNamespace(value={"action": action}, committed=action == "restore")
    monkeypatch.setattr(backup_ui, "_stage", stage)
    monkeypatch.setattr(backup_ui, "_ready", lambda app: None)
    monkeypatch.setattr(backup_ui, "_restore_summary", lambda summary: "")
    monkeypatch.setattr(backup_ui.progress_backup, "is_legacy_backup", lambda data: False)
    assisted, answered = {("old", 0)}, {("old", 1)}
    app = kana_support.with_state(SimpleNamespace(store=Mock(), back_stack=BackStack(), show_home=Mock(), alert=Mock(),
        platform=SimpleNamespace(import_backup=AsyncMock(return_value=b"{}")),
        main_window=SimpleNamespace(dialog=AsyncMock(return_value=True)),
        _clean_preferences=KanaMemory._clean_preferences),
        assisted_attempts=assisted, answered_attempts=answered,
        session_id="s", question_index=3, session_right=1)
    asyncio.run(backup_ui.restore(app))
    app.show_home.assert_called_once_with()
    assert kana_support.session_id(app) is None
    assert kana_support.assisted_attempts(app) is assisted and kana_support.answered_attempts(app) is answered
    assert assisted == answered == set()


def test_an_early_batch_taken_into_memory_empties_both_marks():
    app = kana_support.with_state(SimpleNamespace(store=Mock()), assisted_attempts={("a", 0)},
                                  answered_attempts={("a", 1)})
    app.store.get_session_questions.return_value = ["q"]
    extra_practice_runtime._hold_batch(app, "batch")
    assert kana_support.marks(app) == (set(), set())
    assert (kana_support.session_id(app), kana_support.questions(app), kana_support.question_index(app), kana_support.session_right(app)) == ("batch", ["q"], 0, 0)


def test_an_early_batch_taken_into_memory_needs_no_marks():
    app = SimpleNamespace(store=Mock())
    app.store.get_session_questions.return_value = []
    extra_practice_runtime._hold_batch(app, "batch")
    assert kana_support.session_id(app) == "batch"


# --- reports ------------------------------------------------------------------------------------------------------

def test_an_exposure_marks_its_attempts_assisted_and_reveals_the_current_one(monkeypatch):
    app = exposure_app(monkeypatch, [("s", 0), ("old", 3)])
    feedback_exposure.expose(app, {"context": {}}, [])
    assert kana_support.assisted_attempts(app) == {("s", 0), ("old", 3), ("s", 5)}
    assert kana_support.answer_revealed(app) is True


def test_an_exposure_of_other_attempts_leaves_the_current_one_unrevealed(monkeypatch):
    app = exposure_app(monkeypatch, [("old", 3)])
    feedback_exposure.expose(app, {"context": {}}, [])
    assert kana_support.assisted_attempts(app) == {("old", 3), ("s", 5)}
    assert kana_support.answer_revealed(app) is False


def test_an_exposure_of_no_saved_attempt_changes_neither(monkeypatch):
    app = exposure_app(monkeypatch, [])
    feedback_exposure.expose(app, {"context": {}}, [])
    assert kana_support.assisted_attempts(app) == {("s", 5)} and kana_support.answer_revealed(app) is False


def exposure_app(monkeypatch, keys):
    monkeypatch.setattr(feedback_exposure, "mark_legacy_heads", lambda store, origins: list(keys))
    app = kana_support.with_state(SimpleNamespace(store=Mock(),
        feedback_analysis=lambda: SimpleNamespace(resources={}), diagnostics=Mock(),
        _mark_visible_tutor_help=Mock()), assisted_attempts={("s", 5)}, answer_revealed=False,
        session_id="s", question_index=0)
    return app


def report_of(app, page="question"):
    return feedback_context._capture_practice(feedback_context._Capture(), app, page)


def report_app(**evidence):
    return kana_support.with_state(SimpleNamespace(use_full_keyboard=False, store=SimpleNamespace(practice_lane="main")), **evidence,
        session_id="s", question_index=2, session_right=0, script="hiragana", questions=[], question=kana_question())


def test_a_problem_report_carries_the_attempt_evidence_and_its_two_marks():
    app = report_app(answer_revealed=True, answered_text=None, answer_correct=False, question_audio_ok=True,
                     assisted_attempts={("s", 2)}, answered_attempts={("s", 1), ("t", 2)})
    result = report_of(app)
    assert result["draft"] == {"answer_revealed": True, "answered_text": None, "answer_correct": False,
                               "question_audio_ok": True}
    assert (result["assisted_attempts"], result["answered_attempts"]) == (True, False)


def test_a_problem_report_leaves_out_evidence_never_set_and_reads_missing_marks_as_false():
    app = report_app(question_audio_ok=False)
    result = report_of(app)
    assert result["draft"] == {"question_audio_ok": False}
    assert (result["assisted_attempts"], result["answered_attempts"]) == (False, False)


def test_a_problem_report_off_the_question_pages_carries_no_attempt_evidence():
    app = report_app(answer_revealed=True, answered_text="ka", assisted_attempts={("s", 2)})
    result = report_of(app, "session_wait")
    assert "draft" not in result and "assisted_attempts" not in result
