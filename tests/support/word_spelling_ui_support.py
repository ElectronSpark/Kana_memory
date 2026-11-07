"""Shared word spelling ui fixtures and assertions; no collected scenarios."""
from support.paths import ROOT as _TEST_ROOT
from kanamemory.content import ContentRepository
from kanamemory.app import KanaMemory
from types import MethodType
from unittest.mock import Mock
from pathlib import Path
from kanamemory.domain import Question
from kanamemory.domain import StudyStore
from support.live_kind_preferences_ui_support import configure
from support.fake_ui import descendants
import pytest
from support.app_fixtures import kana_route_handoff


CONTENT = _TEST_ROOT / "app/src/kanamemory/resources/content.sqlite3"


def buttons(app):
    return {item.text: item for item in descendants(app.body) if item.on_press}


def word_question(answer="がっこう", *, script="hiragana", kind="word_meaning_kana"):
    return Question("jmdict:1206730", kind, script, "拼出学校", answer,
        options=list(dict.fromkeys(answer + "かきようモタ")), audio_text=answer,
        metadata={"content_version": "test", "audio_only": kind == "word_kana",
                  "romaji": "gakkou", "meaning_hint": "学校", "word_table": "partial",
                  "practice_config": {"schedule_mode": "spaced",
                                      "kinds": ["word_meaning_kana", "romaji_kana"]}})


@pytest.fixture
def spelling_app(question_app, tmp_path):
    app = question_app
    with ContentRepository(CONTENT) as repo, StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        configure(app, store, "word_meaning_kana", "romaji_kana")
        app.repo = repo
        app._sync_back_handler = Mock()
        app.save_rating = MethodType(KanaMemory.save_rating, app)
        app.continue_spaced_practice = MethodType(KanaMemory.continue_spaced_practice, app)
        kana_route_handoff(app)
        yield app


def start(app, question=None, *, extra=()):
    app.questions = [question or word_question(), *extra]
    app.session_id = app.store.start_session(app.questions)
    app.store.save_session_progress(app.session_id, 0)
    app.show_question()
    assert app.page == "word_question"
