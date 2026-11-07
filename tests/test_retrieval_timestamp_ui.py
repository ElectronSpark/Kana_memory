"""Feedback/restart time must not masquerade as delayed recall evidence."""
from datetime import datetime, timedelta, timezone
from types import MethodType
from unittest.mock import Mock

from support.app_fixtures import app_module, question_app, setup_app, kana_route_handoff
from support.live_kind_preferences_ui_support import configure
from kanamemory import domain
from kanamemory.app import KanaMemory
from kanamemory.domain import Question, StudyStore


def test_delayed_rating_after_restart_keeps_first_answer_time(question_app, tmp_path, monkeypatch):
    app = question_app
    kana_route_handoff(app)
    submitted = datetime(2035, 1, 2, 12, tzinfo=timezone.utc)
    clock = [submitted]
    monkeypatch.setattr(domain, "utc_now", lambda: clock[0])
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        configure(app, store, "kana_choice")
        app._sync_back_handler = Mock()
        q = Question("kana-3042", "kana_choice", "hiragana", "あ", "a", ["a", "i"],
                     metadata={"content_version": "test", "kana_symbol": "あ", "romaji": "a"})
        sid = store.start_session([q], now=submitted)
        store.save_session_progress(sid, 0)
        app.resume_practice()
        app.check_answer("a")
        app._checkpoint()
        saved = store.load_session_progress()["state"]
        assert saved["submitted_at_utc"] == submitted.isoformat()
        duration = saved["answer_duration_ms"]
        clock[0] += timedelta(days=7)
        app.resume_practice()
        assert store.load_session_progress()["state"]["submitted_at_utc"] == submitted.isoformat()
        app.save_rating = MethodType(KanaMemory.save_rating, app)
        app.save_rating(4)
        card = store.get_card(q.content_id, q.kind, q.script)
        assert card["scheduler_state"]["last_review"] == submitted.isoformat()
        assert datetime.fromisoformat(card["due_at_utc"]) == submitted + timedelta(days=8)
        event = dict(store._db.execute("SELECT * FROM review_event").fetchone())
        assert event["occurred_at_utc"] == submitted.isoformat()
        assert event["duration_ms"] == duration
        app.save_rating(4)
        assert store._db.execute("SELECT COUNT(*) FROM review_event").fetchone()[0] == 1
