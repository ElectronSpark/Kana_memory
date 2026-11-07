"""Shared backup background candidate fixtures and assertions; no collected scenarios."""
from unittest.mock import AsyncMock
from kanamemory.learning_store import LearningStore
from unittest.mock import Mock
from kanamemory.domain import Question
from types import SimpleNamespace
from kanamemory.domain import StudyStore
from support.fake_ui import View
from kanamemory import backup_ui
from kanamemory import backup_worker
from record_test_support import decode_current
import pytest
from support.app_fixtures import setup_app


def backup(tmp_path):
    with StudyStore(tmp_path/'replacement') as store:
        store.set_setting('replacement', True)
        return store.export_backup()


def tables(store):
    return decode_current(store.export_backup())['tables']


@pytest.fixture
def app(setup_app, tmp_path):
    app = setup_app
    app.store = StudyStore(tmp_path/'live', time_zone='UTC')
    app.learning_store = LearningStore(app.store)
    q = Question('kana-a', 'kana_romaji', 'hiragana', 'あ', 'a')
    sid = app.store.start_session([q])
    app.store.save_session_progress(sid, 0, state={'romaji_answer':'a'})
    app.store.set_setting('original', 'keep')
    app.main_window = SimpleNamespace(content=View(text='retained'), dialog=AsyncMock(return_value=True))
    app.platform.export_backup = AsyncMock(return_value=True)
    app.platform.import_backup = AsyncMock(return_value=backup(tmp_path))
    app.page, app._checkpoint = 'settings', Mock(return_value=None)
    app.session_id, app.questions, app.question = sid, [q], q
    app.question_index, app.session_right = 0, 0
    app._clean_preferences = lambda value: value
    app.show_home = Mock()
    yield app
    app.store.close()
