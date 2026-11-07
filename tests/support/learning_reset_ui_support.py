"""Shared learning reset ui fixtures and assertions; no collected scenarios."""
from support.paths import ROOT as _TEST_ROOT
from unittest.mock import AsyncMock
from kanamemory.content import ContentRepository
from kanamemory.app import KanaMemory
from types import MethodType
from unittest.mock import Mock
from kanamemory.navigation import PageState
from pathlib import Path
from types import SimpleNamespace
from kanamemory.domain import StudyStore
from support.fake_ui import View
from record_test_support import decode_current
from support.learning_reset_support import populate
import pytest


CONTENT = _TEST_ROOT / "app/src/kanamemory/resources/content.sqlite3"


@pytest.fixture
def reset_app(setup_app, tmp_path):
    app = setup_app
    with ContentRepository(CONTENT) as repo, StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        app.repo, app.store = repo, store
        app.session_id, app.questions = populate(store)
        app.question, app.question_index, app.session_right = app.questions[2], 2, 2
        app.page, app._page_token = "settings", app._page_token + 1
        app._ink_revision, app._audio_serial = 2, 3
        app._assisted_attempts = {(app.session_id, 2)}
        app._answered_attempts = {(app.session_id, 0), (app.session_id, 1)}
        app.strokes, app._active_stroke = [[[1, 2, 3]]], [(1, 2, 3)]
        app._preview_pending, app._practice_context = object(), True
        app._practice_start_token, app._practice_start_task = object(), None
        app._navigating, app._view_key = False, ("show_settings", None)
        app._sync_back_handler = Mock()
        app._capture_page = Mock(return_value=PageState(("show_settings", None), "settings", "设置", View()))
        app.back_stack.pages = [PageState(("show_question", None), "question", "练习", View(),
                                         {"session_id": app.session_id, "question": app.question})]
        app.main_window = SimpleNamespace(dialog=AsyncMock(return_value=True))
        app.service_message = "日语声音已就绪"
        app.model_state, app.platform.tts_ready = "ready", True
        app.platform.chinese_voice_status = Mock(return_value={"detail": "可用"})
        app.mixed_label = lambda *items, **kwargs: View(text="".join(map(str, items)))
        for name in ("install_tts_data", "refresh_services", "open_tts_settings", "show_free_ink",
                     "speak_chinese", "speak_text", "export_learning_backup", "import_learning_backup",
                     "export_previous_backup", "convert_legacy_learning_records", "show_notices"):
            setattr(app, name, Mock())
        app.delete_learning_data = MethodType(KanaMemory.delete_learning_data, app)
        app.show_settings = MethodType(KanaMemory.show_settings, app)
        app._practice_preferences = KanaMemory._clean_preferences({"count": "自定义", "custom_count": "27"})
        store.set_setting("practice_preferences", app._practice_preferences)
        app.alert.reset_mock()
        yield app


def saved_tables(app):
    return decode_current(app.store.export_backup())["tables"]


def retained_state(app):
    return (app.session_id, app.questions, app.question, app.question_index, app.session_right,
            set(app._assisted_attempts), set(app._answered_attempts), list(app.back_stack.pages),
            app.strokes, app._active_stroke, app._preview_pending, app._practice_context,
            app._ink_revision, app._audio_serial, app._practice_start_token)
