"""Shared study path runtime fixtures and assertions; no collected scenarios."""
from support.paths import ROOT as _TEST_ROOT
from kanamemory.content import ContentRepository
from kanamemory.app import KanaMemory
from types import MethodType
from unittest.mock import Mock
from pathlib import Path
from kanamemory.study_path_runtime import StudyPathRuntimeMixin
from kanamemory.study_path_ui import StudyPathUIMixin
from kanamemory.learning_store import LearningStore
from kanamemory.learning_ui import LearningUIMixin
from kanamemory.material_share_ui import MaterialShareUIMixin
from kanamemory.domain import _BatchQuestionStore
from kanamemory.domain import build_questions
from datetime import datetime
from support.fake_ui import descendants
from kanamemory import domain
from support.learning_fixtures import learning_app
from support.learning_fixtures import legacy_lesson_snapshot
import pytest
from support.app_fixtures import question_app
from support.app_fixtures import setup_app
from support.fake_ui import text_of
from datetime import timedelta
from datetime import timezone


CONTENT = _TEST_ROOT / "app/src/kanamemory/resources/content.sqlite3"


def bind(app, cls):
    for name, descriptor in vars(cls).items():
        if name.startswith("__"):
            continue
        method = getattr(cls, name)
        if isinstance(descriptor, staticmethod):
            setattr(app, name, method)
        elif callable(method):
            setattr(app, name, MethodType(getattr(method, "__wrapped__", method), app))


class _CurrentLearningStore:
    """LearningStore of whichever StudyStore the app currently holds.

    Production swaps app.store and app.learning_store together (backup_ui);
    the word-drill tests swap only app.store when they restore a backup.
    """
    def __init__(self, app):
        self._app = app
        # Opening a LearningStore also prepares its tables, as at app start.
        self._pair = (app.store, LearningStore(app.store))

    def __getattr__(self, name):
        store = self._app.store
        if self._pair[0] is not store:
            self._pair = (store, LearningStore(store))
        return getattr(self._pair[1], name)


def bind_study_home(app):
    """Wire a question_app-based fake to the study-path home.

    Since B10 that is the only home: show_home -> refresh_home_actions ->
    refresh_study_home, which reads the learning store and also refreshes the
    shared-material notice.
    """
    for cls in (*reversed(LearningUIMixin.__mro__[:-1]), MaterialShareUIMixin,
                StudyPathUIMixin, StudyPathRuntimeMixin):
        bind(app, cls)
    app.learning_store = _CurrentLearningStore(app)


@pytest.fixture
def path_app(learning_app, monkeypatch):
    app = learning_app
    with ContentRepository(CONTENT) as repo:
        app.repo = repo
        app._test_clock[0] = datetime.now(timezone.utc)
        monkeypatch.setattr(domain, "_utc", lambda value=None: value or app._test_clock[0])
        bind(app, StudyPathUIMixin)
        bind(app, StudyPathRuntimeMixin)
        for name in ("show_home", "refresh_home_actions", "_stats_summary", "resume_practice",
                     "_set_queue_kind_policy", "_queue_kinds", "continue_spaced_practice"):
            method = getattr(KanaMemory, name)
            setattr(app, name, MethodType(getattr(method, "__wrapped__", method), app))
        app._clean_preferences = KanaMemory._clean_preferences
        app._practice_preferences = KanaMemory._clean_preferences({
            "kind": "看假名选读音", "script": "平假名", "schedule": "间隔复习",
            "mixed_kinds": ["kana_choice"], "question_plan_version": 2,
            "listening_handwriting_opt_in_version": 1})
        app._checkpoint = Mock(return_value=True)
        app.loop = Mock()
        app.page = "home"
        app._review_wait_handle = None
        app._sync_back_handler = Mock()
        app.show_learning_settings = Mock()
        yield app


def buttons(app):
    return [view for view in descendants(app.body) if view.on_press]


def press(app, text):
    control = next(view for view in buttons(app) if view.text == text)
    control.on_press(control)
    return control


def home(app):
    app.page = "home"
    app.show_home()


def kana_id(app, symbol):
    return next(row["id"] for row in app.repo.kana_list() if row["hiragana"] == symbol)


def source_question(app, symbol="あ", kind="kana_choice"):
    q = build_questions(app.repo, _BatchQuestionStore(app.store, [kana_id(app, symbol)]),
                        kind=kind, script="hiragana", limit=1, seed=19,
                        include_extended=True, now=app._test_clock[0])[0]
    q.metadata["practice_config"]["schedule_mode"] = "spaced"
    return q


def seed_main(app, *, symbol="あ", kind="kana_choice", draft=None, waiting=False):
    q = source_question(app, symbol, kind)
    if waiting:
        q.metadata["not_before_utc"] = (app._test_clock[0] + timedelta(hours=2)).isoformat()
    sid = app.store.start_session([q], now=app._test_clock[0])
    app.store.save_session_progress(sid, 0, state=draft or {})
    return sid, app.store.get_session_questions(sid)[0]


def history(app):
    return {name: [tuple(row) for row in app.store._db.execute("SELECT * FROM " + name + " ORDER BY rowid")]
            for name in ("study_card", "review_event", "session_answer", "word_spelling_attempt", "learning_event")}
