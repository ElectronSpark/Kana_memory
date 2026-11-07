"""Timed reviews explain short waits without changing the saved study schedule."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from support.joint_practice_ui_support import bind
from support.joint_practice_ui_support import control
from support.joint_practice_ui_support import queue_question
from support.joint_practice_ui_support import queued_app
from support.fake_ui import View, text_of
from support.app_fixtures import app_module, question_app, setup_app
from kanamemory import domain
from kanamemory.app_clock import FrozenClock, using_clock
from kanamemory.app import KanaMemory
from kanamemory.domain import StudyStore
from kanamemory.navigation import PageState


from support.review_wait_ui_support import UTC, Scheduled, ControlledLoop, clock, waiting_app, start_wait


def database_contents(app):
    return tuple(app.store._db.iterdump())


@pytest.mark.parametrize("seconds,expected", [
    (0.000001, "1 秒"), (0.2, "1 秒"), (1.0, "1 秒"),
    (1.001, "2 秒"), (39, "39 秒"), (60, "1 分 0 秒"),
    (61, "1 分 1 秒"), (3601, "1 小时 0 分 1 秒"),
])
def test_countdown_rounds_up_and_does_not_treat_current_minute_as_due(seconds, expected):
    now = datetime(2026, 10, 15, 15, 24, 10, tzinfo=UTC)
    due = now + timedelta(seconds=seconds)
    message = app_module.review_wait_status(due, now=now)
    assert expected in message
    assert "已到期" not in message
    assert "尚未到期" in app_module.review_wait_status(due, now=now, checked=True)


@pytest.mark.parametrize("offset", [0, -0.000001, -60])
def test_due_boundary_and_overdue_message_are_unambiguous(offset):
    now = datetime(2026, 10, 15, 15, 24, 59, tzinfo=UTC)
    message = app_module.review_wait_status(now + timedelta(seconds=offset), now=now, checked=True)
    assert "已到期，可以继续复习" in message
    assert "尚未到期" not in message


def test_initial_wait_uses_ready_callback_then_counts_down_without_rebuilding(waiting_app, clock):
    app = waiting_app
    start_wait(app, clock.now + timedelta(seconds=39))
    initial_handle = app._review_wait_handle
    initial_widgets = (app.body, app._review_wait_status, app._review_wait_button)
    token, before = app._page_token, database_contents(app)
    assert initial_handle.delay is None
    assert "39 秒" in app._review_wait_status.text
    initial_handle.run()
    assert app._review_wait_handle.delay == 1

    clock.now += timedelta(seconds=1)
    app._review_wait_handle.run()
    assert "38 秒" in app._review_wait_status.text
    assert (app.body, app._review_wait_status, app._review_wait_button) == initial_widgets
    assert app._page_token == token and app.question_index == 0
    assert database_contents(app) == before
    assert app.store.stats()["total_reviews"] == 0


def test_repeated_early_checks_show_feedback_and_preserve_queue_and_review_records(waiting_app, clock):
    app = waiting_app
    due = clock.now + timedelta(seconds=39)
    start_wait(app, due)
    # Existing evidence must remain intact when the user repeatedly checks.
    app.store.record_review("previous", "kana_choice", "hiragana", 3,
                            now=clock.now - timedelta(days=1))
    reviews = app.store.review_history("previous")
    session_id = app.session_id
    for _ in range(3):
        control(app, "检查并继续复习").on_press(None)
        saved = app.store.load_session_progress()
        assert app.page == "session_wait"
        assert "尚未到期" in app._review_wait_status.text
        assert "39 秒" in app._review_wait_status.text
        assert saved["session_id"] == session_id and saved["question_index"] == 0
        assert saved["questions"][0].metadata["not_before_utc"] == due.isoformat()
        assert len(saved["questions"]) == 1
        assert app.store.review_history("previous") == reviews
        assert app.store.stats()["total_reviews"] == 1
    app.alert.assert_not_called()


def test_due_tick_enters_same_question_once_and_old_button_keeps_new_draft(waiting_app, clock):
    app = waiting_app
    due = clock.now + timedelta(seconds=39)
    start_wait(app, due)
    clock.now = due - timedelta(microseconds=1)
    control(app, "检查并继续复习").on_press(None)
    assert app.page == "session_wait" and "1 秒" in app._review_wait_status.text
    saved_before = app.store.load_session_progress()
    old_button = app._review_wait_button
    clock.now = due
    app._review_wait_handle.run()
    assert "已到期，可以继续复习" in app._review_wait_status.text
    assert old_button.text == "继续复习"
    assert app.page == "question" and app.question.content_id == "kana-wait"
    assert app.question_index == 0 and len(app.questions) == 1
    assert app._review_wait_handle is None
    saved_after = app.store.load_session_progress()
    assert saved_after["session_id"] == saved_before["session_id"]
    assert saved_after["questions"] == saved_before["questions"]
    token = app._page_token
    # A double tap arriving from the retired wait page cannot reset the input.
    app.romaji_input.value = "a"
    app.romaji_input.on_change(app.romaji_input)
    entered = database_contents(app)
    old_button.on_press(None)
    assert app.romaji_answer == "a" and app._page_token == token
    assert database_contents(app) == entered
    assert app.store.stats()["total_reviews"] == 0


def test_due_review_timer_defers_navigation_during_exit_confirmation(waiting_app, clock):
    app = waiting_app
    due = clock.now + timedelta(seconds=2)
    start_wait(app, due)
    before = database_contents(app)
    token = app._page_token
    app._leave_confirmation_active = True
    clock.now = due
    app._review_wait_handle.run()
    assert app.page == "session_wait" and app._page_token == token
    assert database_contents(app) == before
    assert app._review_wait_handle.delay == 1
    app._leave_confirmation_active = False
    app._review_wait_handle.run()
    assert app.page == "question" and app.question.content_id == "kana-wait"
    assert app.store.stats()["total_reviews"] == 0


@pytest.mark.parametrize("stale", ["page", "token"])
def test_stale_tick_cannot_repaint_another_page_or_schedule_more_work(waiting_app, clock, stale):
    app = waiting_app
    start_wait(app, clock.now + timedelta(seconds=20))
    token, handle = app._page_token, app._review_wait_handle
    before, label_text = database_contents(app), app._review_wait_status.text
    if stale == "page":
        app.page = "home"
    else:
        app._page_token += 1
    clock.now += timedelta(seconds=20)
    handles_before = len(app.loop.handles)
    app._tick_review_wait(token)
    assert app._review_wait_status.text == label_text
    assert app._review_wait_handle is handle
    assert len(app.loop.handles) == handles_before
    assert database_contents(app) == before


def test_exit_and_reopen_preserve_absolute_due_time_and_remaining_queue(waiting_app, clock):
    app = waiting_app
    due = clock.now + timedelta(seconds=39)
    start_wait(app, due)
    initial = app.store.load_session_progress()
    pending_timer = app._review_wait_handle
    app.screen("home", "今日")
    assert pending_timer.cancelled and app._review_wait_handle is None
    clock.now += timedelta(seconds=14)
    original_store = app.store
    # Reopen the persisted SQLite data through a fresh store, as after restart.
    with StudyStore(original_store.data_dir, time_zone="UTC") as reopened:
        app.store = reopened
        app.questions, app.session_id, app.question = [], None, None
        KanaMemory.resume_practice(app)
        restored = app.store.load_session_progress()
        assert restored["session_id"] == initial["session_id"]
        assert restored["question_index"] == initial["question_index"] == 0
        assert restored["questions"][0].metadata["not_before_utc"] == due.isoformat()
        assert app.page == "session_wait" and "25 秒" in app._review_wait_status.text
        clock.now = due
        app._review_wait_handle.run()
        assert app.page == "question" and len(app.questions) == 1
        assert reopened.stats()["total_reviews"] == 0
    app.store = original_store


def test_wait_exit_returns_directly_and_cancels_pending_countdown(waiting_app, clock):
    app = waiting_app
    start_wait(app, clock.now + timedelta(seconds=2))
    app.main_window.dialog = AsyncMock(return_value=True)
    app.show_home = lambda: app.screen("home", "今日")
    timer = app._review_wait_handle
    KanaMemory.request_practice_exit(app)
    token = app._page_token
    assert app.page == "home" and app._review_wait_handle is None
    assert timer.cancelled and not app.loop.confirmations
    app.main_window.dialog.assert_not_called()
    clock.now += timedelta(seconds=2)
    timer.run()
    app.continue_spaced_practice()
    assert app.page == "home" and app._page_token == token
    assert not app._leave_confirmation_active
    saved = app.store.load_session_progress()
    assert saved["question_index"] == 0 and len(saved["questions"]) == 1
    assert app.store.stats()["total_reviews"] == 0


def test_back_restoration_cancels_wait_callback(waiting_app, clock):
    app = waiting_app
    start_wait(app, clock.now + timedelta(seconds=20))
    pending = app._review_wait_handle
    app.page = "kana_detail"
    app.back_stack.pages.append(PageState(("show_words", None), "words", "词库", View()))
    KanaMemory.go_back(app)
    assert app.page == "words" and pending.cancelled
    assert app._review_wait_handle is None


@pytest.mark.parametrize("elapsed", [5, 25])
def test_back_to_wait_restores_deadline_and_widgets_then_auto_resumes(waiting_app, clock, elapsed):
    from support.session_snapshots import assert_fresh_native_snapshot
    app = waiting_app
    due = clock.now + timedelta(seconds=20)
    question = start_wait(app, due)
    session = app.session_id
    question = assert_fresh_native_snapshot(app.store, session, [question])[0]
    original = (app._review_wait_until, app._review_wait_status,
                app._review_wait_button, app._review_wait_checked)
    pending = app._review_wait_handle
    app._view_key = ("show_question", None)
    app._page_scroll.position = (0, 0)
    previous = KanaMemory._capture_page(app)
    app.back_stack.pages.append(previous)
    app.screen("kana_detail", "假名详情", View(text="参考内容"))
    assert pending.cancelled and app._review_wait_handle is None
    # Nested pages may replace the controller's widget bindings. Back must
    # restore the original wait's actual controls, not repaint these stand-ins.
    app._review_wait_until = due + timedelta(minutes=4)
    app._review_wait_status = View(text="另一页面")
    app._review_wait_button = View(text="另一按钮")
    app._review_wait_checked = not original[3]
    clock.now += timedelta(seconds=elapsed)
    KanaMemory.go_back(app)
    assert app.page == "session_wait"
    assert app.content_slot.children[0] is previous.content
    assert (app._review_wait_until, app._review_wait_status,
            app._review_wait_button, app._review_wait_checked) == original
    timer = app._review_wait_handle
    assert timer is not None and timer is not pending and timer.delay is None
    saved = app.store.load_session_progress()
    assert saved["session_id"] == session and saved["questions"] == [question]
    if elapsed < 20:
        before = database_contents(app)
        timer.run()
        assert app.page == "session_wait" and "15 秒" in original[1].text
        assert database_contents(app) == before
        clock.now = due
    app._review_wait_handle.run()
    assert app.page == "question" and app.question == question
    assert app.session_id == session and app.question_index == 0
    assert app.store.get_session_questions(session) == [question]
    assert app.store.stats()["total_reviews"] == 0


def test_application_exit_cancels_wait_callback_before_resources_close():
    handle = Scheduled(Mock(), (), 1)
    app = SimpleNamespace(page="session_wait", _review_wait_handle=handle, _paint_handle=None,
                          _page_token=1, _checkpoint=Mock(), platform=Mock(),
                          repo=Mock(), store=Mock())
    # Exit archives the round through the real store (464a3d9); store here is a stand-in.
    with patch("kanamemory.practice_archive.archive_previous_rounds") as archive:
        assert KanaMemory.on_exit(app)
    archive.assert_called_once_with(app.store)
    assert handle.cancelled and app._review_wait_handle is None
    handle.run()
    handle.callback.assert_not_called()
    app.platform.close.assert_called_once()
    app.store.close.assert_called_once()
