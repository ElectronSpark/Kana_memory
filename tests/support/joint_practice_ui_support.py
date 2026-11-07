"""Shared joint practice ui fixtures and assertions; no collected scenarios."""
from kanamemory.app import KanaMemory
from types import MethodType
from unittest.mock import Mock
from kanamemory.domain import Question
from kanamemory.domain import StudyStore
from datetime import datetime
from support.fake_ui import descendants
import pytest
from datetime import timedelta
from datetime import timezone
from support.app_fixtures import kana_route_handoff


def control(app, caption):
    return next(item for item in descendants(app.body) if item.text == caption and item.on_press)


def bind(app, *methods):
    for name in methods:
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))


@pytest.fixture
def queued_app(question_app, tmp_path):
    app = question_app
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        app.store = store
        app._practice_preferences = KanaMemory._clean_preferences({})
        app._practice_preferences["mixed_kinds"].append("kana_handwriting")
        app.show_home, app.show_progress, app.show_new_batch = Mock(), Mock(), Mock()
        app._sync_back_handler = Mock()
        bind(app, "_checkpoint", "continue_spaced_practice", "save_rating")
        kana_route_handoff(app)
        yield app


def queue_question(ident, kind="kana_romaji", *, future=False):
    metadata = {"content_version": "test", "kana_symbol": "あ", "romaji": "a",
                "practice_config": {"schedule_mode": "spaced"}}
    if future:
        metadata["not_before_utc"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    return Question(ident, kind, "hiragana", "", "a", options=["a", "i"], metadata=metadata)
