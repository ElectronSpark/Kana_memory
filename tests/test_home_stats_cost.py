"""Home shows current path progress without computing discarded legacy totals."""
from unittest.mock import Mock

from support.study_path_runtime_support import path_app
from support.study_path_runtime_support import home
from support.study_path_runtime_support import seed_main
from support.study_path_runtime_support import history
from support.study_path_runtime_support import buttons
from support.learning_fixtures import learning_app
from support.app_fixtures import question_app, setup_app


def test_path_home_does_not_scan_legacy_statistics(path_app, monkeypatch):
    app = path_app
    seed_main(app, draft={"page": "question", "romaji_answer": "draft"})
    before = history(app)
    pending = app.store.load_session_progress()
    forbidden = Mock(side_effect=AssertionError("Unused legacy history scan"))
    monkeypatch.setattr(app.store, "stats", forbidden)

    home(app)
    assert "主线初学完成" in app.home_stats_label.text
    assert "继续学习" in [button.text for button in buttons(app)]
    forbidden.assert_not_called()
    assert history(app) == before
    assert app.store.load_session_progress() == pending
