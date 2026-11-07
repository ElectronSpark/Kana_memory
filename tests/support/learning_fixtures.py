"""Native-screen controller integration with real persistent learning stores.

Controls use the same lightweight view adapter as existing application UI tests;
Android screenshot/touch validation remains a separate release requirement.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from support.fake_ui import View, descendants
from kanamemory.app import KanaMemory
from kanamemory.domain import StudyStore
from kanamemory.learning_content import get_learning_item
from kanamemory.learning_questions import AUDIO_WORD_KIND, SOUND_PREFERENCE_VERSION, WORD_KINDS, build_learning_question
from kanamemory.learning_store import LearningStore
from kanamemory.learning_ui import LearningUIMixin, LEARNING_PREFS
from kanamemory import learning_store as store_module
from kanamemory.app_clock import SystemClock, install_clock, restore_clock

NOW = datetime(2026, 10, 25, 18, tzinfo=timezone.utc)

@pytest.fixture
def learning_app(question_app, tmp_path, monkeypatch, request):
    app = question_app
    members = {name: value for base in reversed(LearningUIMixin.__mro__[:-1]) for name, value in vars(base).items()}
    for name, descriptor in members.items():
        if name.startswith("__"):
            continue
        value = getattr(LearningUIMixin, name)
        if isinstance(descriptor, staticmethod):
            setattr(app, name, value)
        elif callable(value):
            setattr(app, name, MethodType(getattr(value, "__wrapped__", value), app))
    clock = [NOW]
    app._test_clock = clock
    class TestClock(SystemClock):
        def utc_now(self):
            return clock[0]
    previous_clock = install_clock(TestClock())
    request.addfinalizer(lambda: restore_clock(previous_clock))
    # All late imports and UI time checks share this mutable synthetic instant.
    monkeypatch.setattr(store_module, "_utc", lambda value=None: value or clock[0])
    app.store = StudyStore(tmp_path / "native-learning", time_zone="UTC")
    app.learning_store = LearningStore(app.store)
    app.speak_text = Mock()
    app.show_home = Mock()
    app.show_words = Mock()
    app.show_kana = Mock()
    app.show_word_drill_setup = Mock()
    app.show_practice_setup = Mock()
    app._pitch_preferences = Mock(return_value={"line": True, "number": True})
    app.show_notices = Mock()
    for name in ("show_progress", "export_learning_backup", "import_learning_backup", "export_previous_backup", "convert_legacy_learning_records", "delete_learning_data", "show_feedback_manager"):
        setattr(app, name, Mock())
    app.platform.play = AsyncMock()
    app.platform.stop = Mock()
    app.main_window.dialog = AsyncMock(return_value=True)
    app.loop = SimpleNamespace(create_task=lambda coroutine: asyncio.run(coroutine))
    app._practice_context = False
    yield app
    app.store.close()

def click(app, caption):
    widget = next(x for x in descendants(app.body) if x.text == caption and x.on_press)
    widget.on_press(widget)
    return widget

def use_real_detail_back(app):
    """Route "详细"/"返回结果" through the production page history.

    Since fd986f7 the result detail returns with go_back and keeps the original
    result view, so learning_app's Mock go_back would stay on the detail page.
    Call before ready(): the retained page must be built by the real screen.
    """
    app._navigating = False
    app._view_key = ("continue_learning", None)
    app._page_scroll = None
    app._paint_handle = None
    app._ink_revision = app._audio_serial = 0
    app.question = None
    app.content_slot, app.title_label, app.subtitle_label = View(), View(), View()
    app.platform.set_back_enabled = Mock()
    app.loop.call_soon = lambda callback, *args: callback(*args)
    app.loop.call_later = Mock()
    for name in ("_capture_page", "_sync_back_handler"):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))
    app.learning_feedback_detail = MethodType(LearningUIMixin.learning_feedback_detail, app)
    def screen(*args, **kwargs):
        app.body = KanaMemory.screen(app, *args, **kwargs)
        if app._page_scroll is not None:
            app._page_scroll.position = (0, 0)
        return app.body
    app.screen = screen
    real_back = MethodType(KanaMemory.go_back, app)
    def go_back(widget=None, **kwargs):
        result = real_back(widget, **kwargs)
        # The restored view never passes through screen(); keep app.body current.
        app.body = app.content_slot.children[0]
        return result
    app.go_back = go_back
    return app

def finish_guided_learning(app, limit=8):
    """Answer each guided step until independent work or a wait page.

    Reviewed lessons may guide more than one task ("跟着练习"), and each
    ability is scoped separately: a later ability asks familiarity, then
    guides without repeating the shared introduction (0.29.3, 7ce729d).
    """
    for _ in range(limit):
        if app.page == "learning_guide":
            click(app, app._learning_guide["answer"])
            click(app, "继续")
        elif app.page == "learning_familiarity":
            click(app, "初学")
        else:
            return app.page
    raise AssertionError("guided learning did not finish")

def legacy_lesson_snapshot(item_id):
    """Isolate a synthetic older lesson from current bundled prerequisites.

    These controller tests isolate answer, navigation and draft behavior.
    Actual old bundled snapshots now receive corrected dependencies before
    independent learning. That repair is covered in the dedicated dependency
    tests; this explicitly distinct fixture isolates answer/navigation behavior.
    """
    item = get_learning_item(item_id)
    item.pop("depends_on", None)
    item["source_ids"] = ["test-legacy-snapshot"]
    return item

def ready(app, kind, *, item_id=None):
    item_id = item_id or ("word:tegami:letter" if kind in WORD_KINDS else "grammar:te_iru:progressive")
    item = legacy_lesson_snapshot(item_id)
    # An explicit choice of one kind on a device that can actually present it.
    app.store.set_setting(LEARNING_PREFS, {"enabled": [kind], SOUND_PREFERENCE_VERSION: 1})
    if kind == AUDIO_WORD_KIND:
        app.platform.tts_ready = True
    app.learning_store.enroll(item, now=NOW - timedelta(minutes=2))
    app.learning_store.advance_instruction(item_id, "intro_done", now=NOW-timedelta(minutes=2))
    app.learning_store.advance_instruction(item_id, "guide_correct", now=NOW-timedelta(minutes=2))
    q = build_learning_question(item, kind)
    app.learning_store.append_questions(q)
    app.continue_learning()
    assert app.page == "learning_question"
    return item, q

def legacy_dump(store):
    return {name: [tuple(row) for row in store._db.execute("SELECT * FROM " + name + " ORDER BY rowid")]
            for name in ("study_card", "study_session", "review_event", "session_checkpoint", "session_answer", "word_spelling_attempt")}
