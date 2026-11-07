"""The home page separates ready questions from saved future repetitions."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from support.joint_practice_ui_support import queued_app
from support.answer_option_ui_support import preview_app
from support.practice_availability_support import question
from support.app_fixtures import question_app, setup_app
from support.fake_ui import text_of
from support.review_wait_ui_support import ControlledLoop
from support.review_wait_ui_support import clock
from support.review_wait_ui_support import waiting_app
from kanamemory.app import KanaMemory
from kanamemory.practice_availability import summarize_queue_availability
from kanamemory.domain import StudyStore


def start(store, questions, now):
    session = store.start_session(questions, now=now)
    store.save_session_progress(session, 0)
    return session


def rate(store, session, rating, now):
    saved = store.load_session_progress()
    item = saved["questions"][saved["question_index"]]
    return store.record_review(item.content_id, item.kind, item.script, rating,
        now=now, session_id=session, question_index=saved["question_index"],
        outcome="incorrect" if rating == 1 else "correct", answered_text=item.answer)


def kana_queue_availability(app, store):
    """The kana-queue counts the study-path home (refresh_study_home) displays."""
    before = tuple(store._db.iterdump())
    pending = store.load_session_progress(lane="main", lazy_questions=True)
    availability = summarize_queue_availability(store, pending,
        allowed_kinds=KanaMemory._queue_kinds(app, pending) if pending else None)
    assert tuple(store._db.iterdump()) == before
    return availability


def policy_app(store):
    return SimpleNamespace(store=store, _practice_preferences=KanaMemory._clean_preferences({}))


def timer_app(due):
    """Only the home refresh timer; the study-path home renders through refresh_home_actions."""
    store = Mock(spec=["stats"])
    store.stats.side_effect = AssertionError("The home timer must not scan review history")
    return SimpleNamespace(page="home", _page_token=17, loop=ControlledLoop(), _review_wait_handle=None,
                           _home_queue_due=due, refresh_home_actions=Mock(), store=store)


def test_completed_round_home_reports_saved_wait_not_two_questions_to_answer(tmp_path, clock):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        session = start(store, [question("a"), question("b")], clock.now)
        rate(store, session, 3, clock.now)
        due = rate(store, session, 1, clock.now)["due_at_utc"]
        saved = store.load_session_progress()
        available = kana_queue_availability(policy_app(store), store)
        assert (available.ready_count, available.waiting_count) == (0, 2)
        assert available.next_due_utc == due
        assert store.load_session_progress() == saved


def test_mixed_queue_home_counts_only_ready_work_and_waiting_separately(tmp_path, clock):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        session = start(store, [question("a"), question("b"), question("c")], clock.now)
        rate(store, session, 1, clock.now)
        available = kana_queue_availability(policy_app(store), store)
        assert (available.ready_count, available.waiting_count) == (2, 1)


def test_home_timer_wakes_at_original_due_boundary_without_history_scan(clock):
    due = clock.now + timedelta(minutes=2)
    app = timer_app(due)
    KanaMemory._arm_home_review_refresh(app)
    initial = app._review_wait_handle
    assert initial.delay is None
    initial.run()
    assert app._review_wait_handle.delay == 60
    clock.now = due - timedelta(microseconds=1)
    app._review_wait_handle.run()
    assert app._review_wait_handle.delay == pytest.approx(0.000001)
    app.refresh_home_actions.assert_not_called()
    clock.now = due
    app._review_wait_handle.run()
    app.refresh_home_actions.assert_called_once_with()
    assert app._review_wait_handle is None
    app.store.stats.assert_not_called()


@pytest.mark.parametrize("stale", ["page", "token"])
def test_home_timer_cannot_refresh_a_different_or_replaced_page(clock, stale):
    due = clock.now + timedelta(seconds=20)
    app = timer_app(due)
    KanaMemory._arm_home_review_refresh(app)
    handle, token, count = app._review_wait_handle, app._page_token, len(app.loop.handles)
    if stale == "page":
        app.page = "kana_detail"
    else:
        app._page_token += 1
    clock.now = due
    KanaMemory._tick_home_review(app, token)
    app.refresh_home_actions.assert_not_called()
    assert len(app.loop.handles) == count and app._review_wait_handle is handle


def test_refresh_replaces_the_old_home_timer_instead_of_accumulating_callbacks(clock):
    app = timer_app(clock.now + timedelta(minutes=2))
    KanaMemory._arm_home_review_refresh(app)
    old = app._review_wait_handle
    KanaMemory._arm_home_review_refresh(app)
    assert old.cancelled and app._review_wait_handle is not old
    count = len(app.loop.handles)
    old.run()
    assert len(app.loop.handles) == count


def test_home_counts_respect_temporary_queue_policy_then_current_preference_change(tmp_path, clock):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        session = start(store, [question("a", "kana_romaji"), question("b")], clock.now)
        app = policy_app(store)
        app._practice_preferences.update(kind="综合自适应练习", mixed_kinds=["kana_choice"])
        store.set_setting("practice_queue_kinds", {"session_id": session,
            "regular_kinds": ["kana_choice"], "kinds": ["kana_romaji"]})
        assert kana_queue_availability(app, store).ready_count == 1
        assert KanaMemory._queue_kinds(app, store.load_session_progress()) == ["kana_romaji"]
        app._practice_preferences["mixed_kinds"] = ["kana_choice", "romaji_kana"]
        assert kana_queue_availability(app, store).ready_count == 1
        assert KanaMemory._queue_kinds(app, store.load_session_progress()) == ["kana_choice", "romaji_kana"]


def test_reenabled_kind_home_previews_original_wait_once_without_changing_queue(tmp_path, clock):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        session = start(store, [question("a", "kana_romaji"), question("keep")], clock.now)
        due = rate(store, session, 1, clock.now)["due_at_utc"]
        store.reconcile_session_kinds(session, {"kana_choice"}, now=clock.now)
        app = policy_app(store)
        app._practice_preferences.update(kind="综合自适应练习", mixed_kinds=["kana_choice"])
        assert kana_queue_availability(app, store).waiting_count == 0
        app._practice_preferences["mixed_kinds"].append("kana_romaji")
        for _ in range(3):
            available = kana_queue_availability(app, store)
            assert (available.ready_count, available.waiting_count) == (1, 1)
            assert available.next_due_utc == due
        assert store.get_card("a", "kana_romaji", "hiragana")["review_count"] == 1
        saved = store.load_session_progress()
        assert len(saved["questions"]) - saved["question_index"] == 1


@pytest.mark.parametrize("state", [
    {"page": "question", "answered_text": "unsubmitted"},
    {"page": "feedback", "feedback_pending": True, "answered_text": "a"},
])
def test_home_preserves_current_ungraded_work_while_reporting_future_repetitions(tmp_path, clock, state):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        due = clock.now + timedelta(minutes=1)
        session = start(store, [question("a", future=due), question("b", future=due)], clock.now)
        store.save_session_progress(session, 0, state=state)
        available = kana_queue_availability(policy_app(store), store)
        assert (available.ready_count, available.waiting_count) == (1, 1)
        assert store.load_session_progress()["state"] == state


def test_question_header_and_wait_screen_report_the_same_ready_waiting_split(waiting_app, clock):
    app = waiting_app
    first = question("a", "kana_romaji")
    first.metadata.update(kana_symbol="あ", romaji="a", content_version="test")
    second = question("b", "kana_romaji")
    second.metadata.update(kana_symbol="い", romaji="i", content_version="test")
    app.questions = [first, second]
    app.session_id = start(app.store, app.questions, clock.now)
    app.question_index, app.session_right = 0, 0
    rate(app.store, app.session_id, 1, clock.now)
    app.show_question()
    assert app.page == "question"
    assert app.title_label.text == "练习 · 现在可答 1 题"
    rate(app.store, app.session_id, 4, clock.now)
    app.show_question()
    assert app.page == "session_wait"
    assert "已保存 1 道后续复习题，到期后自动继续" in text_of(app.body)


def test_entering_another_page_cancels_home_due_callback(waiting_app, clock):
    app = waiting_app
    app.page = "home"
    app._home_queue_due = clock.now + timedelta(minutes=1)
    KanaMemory._arm_home_review_refresh(app)
    handle = app._review_wait_handle
    app.screen("settings", "设置")
    assert handle.cancelled and app._review_wait_handle is None
    handle.run()
    assert app.page == "settings"
