"""Shared review wait ui fixtures and assertions; no collected scenarios."""
from kanamemory.app_clock import FrozenClock
from kanamemory.app import KanaMemory
from support.fake_ui import View
from support.joint_practice_ui_support import bind
from datetime import datetime
import pytest
from support.joint_practice_ui_support import queue_question
from datetime import timezone
from kanamemory.app_clock import using_clock


UTC = timezone.utc


class Scheduled:
    def __init__(self, callback, args, delay):
        self.callback, self.args, self.delay = callback, args, delay
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        if not self.cancelled:
            self.callback(*self.args)


class ControlledLoop:
    def __init__(self):
        self.handles, self.confirmations = [], []

    def call_soon(self, callback, *args):
        handle = Scheduled(callback, args, None)
        self.handles.append(handle)
        return handle

    def call_later(self, delay, callback, *args):
        handle = Scheduled(callback, args, delay)
        self.handles.append(handle)
        return handle

    def create_task(self, coroutine):
        self.confirmations.append(coroutine)


@pytest.fixture
def clock(monkeypatch):
    clock = FrozenClock(datetime(2026, 10, 15, 15, 24, 10, tzinfo=UTC))
    with using_clock(clock):
        yield clock


@pytest.fixture
def waiting_app(queued_app, clock):
    app = queued_app
    # These synthetic timed questions use typed romanization. Keep that kind
    # explicitly enabled so this fixture exercises waiting rather than pruning.
    app._practice_preferences = KanaMemory._clean_preferences(app._practice_preferences)
    app._practice_preferences["kind"] = "看假名拼罗马音"
    app.store.set_setting("practice_preferences", app._practice_preferences)
    app.loop = ControlledLoop()
    app._review_wait_handle = app._paint_handle = None
    app._ink_revision = app._audio_serial = 0
    app._leave_confirmation_active = False
    app.content_slot, app.title_label, app.subtitle_label = View(), View(), View()
    app._view_key, app._page_scroll = None, None
    bind(app, "_cancel_review_wait", "_tick_review_wait")

    def screen(name, title, *children, **kwargs):
        app.body = KanaMemory.screen(app, name, title, *children, **kwargs)
        return app.body

    app.screen = screen
    yield app
    app._cancel_review_wait()
    for coroutine in app.loop.confirmations:
        coroutine.close()


def start_wait(app, due):
    question = queue_question("kana-wait")
    question.metadata["not_before_utc"] = due.isoformat()
    app.questions = [question]
    app.session_id = app.store.start_session(app.questions)
    app.question_index, app.session_right = 0, 0
    app.store.save_session_progress(app.session_id, 0)
    app.show_question()
    assert app.page == "session_wait"
    return question
