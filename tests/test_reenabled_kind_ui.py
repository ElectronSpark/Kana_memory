"""Re-enabling a learned direction resumes its saved step, without new reviews."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import MethodType

import pytest

from support.append_queue_ui_support import question
from support.live_kind_preferences_ui_support import configure
from support.live_kind_preferences_ui_support import evidence
from support.live_kind_preferences_ui_support import preferences
from support.app_fixtures import question_app, setup_app, kana_route_handoff
from kanamemory import domain
from kanamemory.app import KanaMemory
from kanamemory.domain import StudyStore


START = datetime(2035, 1, 2, 12, tzinfo=timezone.utc)
AUDIO = "kana_handwriting"
TYPED = "kana_romaji"


def seed_learned_repeat(store):
    """Keep an ungraded sibling distinct from the already learned audio card."""
    sid = store.start_session([
        question("learned-audio", AUDIO), question("ungraded-audio", AUDIO),
        question("survivor", TYPED)], now=START)
    store.save_session_progress(sid, 0)
    review = store.record_review("learned-audio", AUDIO, "hiragana", 1,
        session_id=sid, question_index=0, outcome="correct", now=START)
    assert review["queue_extended"]
    store.save_session_progress(sid, 1, 1)
    return sid, deepcopy(store.get_session_questions(sid)[-1])


def set_regular(app, *kinds):
    app._practice_preferences = preferences(*kinds)
    app.store.set_setting("practice_preferences", app._practice_preferences)


def pending_questions(store):
    pending = store.load_session_progress()
    return pending["questions"][pending["question_index"]:]


def assert_one_saved_repeat(store, original):
    remaining = pending_questions(store)
    repeats = [item for item in remaining if item.kind == AUDIO]
    assert repeats == [original]
    assert all(item.content_id != "ungraded-audio" for item in remaining)
    card = store.get_card("learned-audio", AUDIO, "hiragana")
    assert card["review_count"] == 1
    assert card["due_at_utc"] == original.metadata["not_before_utc"]


@pytest.mark.parametrize("feedback", [False, True])
def test_reenable_preserves_survivor_draft_or_feedback_and_restores_only_saved_step(
        question_app, tmp_path, monkeypatch, feedback):
    app = question_app
    monkeypatch.setattr(domain, "utc_now", lambda: START)
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        configure(app, store, TYPED)
        sid, repeat = seed_learned_repeat(store)
        app.resume_practice()  # Applying the disabled kind skips its old rows.
        assert app.question.content_id == "survivor"
        state = {"page": "feedback" if feedback else "question",
            "feedback_pending": feedback, "answer_revealed": False,
            "question_audio_ok": True, "answered_text": "a" if feedback else "",
            "romaji_answer": "a" if feedback else "unfinished"}
        store.save_session_progress(sid, app.question_index, 1, state=state)
        unchanged = evidence(store)
        survivor_index = app.question_index
        set_regular(app, TYPED, AUDIO)
        for _ in range(3):
            app.page = "home"
            app.resume_practice()
            assert app.question.content_id == "survivor"
            assert app.question_index == survivor_index
            assert app.page == ("feedback" if feedback else "question")
            assert app.romaji_answer == state["romaji_answer"]
            assert app.answered_text == ("a" if feedback else None)
            assert_one_saved_repeat(store, repeat)
            assert evidence(store) == unchanged
        app.show_ink_page.assert_not_called()
        app.alert.assert_not_called()


def test_reenabled_step_waits_until_original_due_then_uses_same_review_record(
        question_app, tmp_path, monkeypatch):
    app = question_app
    kana_route_handoff(app)
    clock = [START]
    monkeypatch.setattr(domain, "utc_now", lambda: clock[0])
    app.continue_spaced_practice = MethodType(KanaMemory.continue_spaced_practice, app)

    def ink_page(**kwargs):
        app.page, app.strokes = "ink_question", []
    app.show_ink_page = ink_page

    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        configure(app, store, TYPED)
        sid, repeat = seed_learned_repeat(store)
        app.resume_practice()
        set_regular(app, TYPED, AUDIO)
        app.page = "home"
        app.resume_practice()
        assert_one_saved_repeat(store, repeat)
        store.record_review("survivor", TYPED, "hiragana", 4,
            session_id=sid, question_index=app.question_index, outcome="correct", now=START)
        unchanged = evidence(store)
        app.resume_practice()
        assert app.page == "session_wait"
        assert app._review_wait_until == datetime.fromisoformat(repeat.metadata["not_before_utc"])
        for seconds_before in (30, 1):
            clock[0] = datetime.fromisoformat(repeat.metadata["not_before_utc"]) - timedelta(seconds=seconds_before)
            app.continue_spaced_practice()
            assert app.page == "session_wait"
            assert_one_saved_repeat(store, repeat)
            assert evidence(store) == unchanged
        clock[0] = datetime.fromisoformat(repeat.metadata["not_before_utc"])
        app.continue_spaced_practice()
        assert app.page == "ink_question" and app.question == repeat
        assert evidence(store) == unchanged
        result = store.record_review("learned-audio", AUDIO, "hiragana", 4,
            session_id=sid, question_index=app.question_index, outcome="correct", now=clock[0])
        assert result["card"]["review_count"] == 2
        assert result["card"]["introduced_at_utc"] == START.isoformat()
        assert store.load_session_progress() is None
        assert len(store.review_history("learned-audio", AUDIO, "hiragana")) == 2
        app.alert.assert_not_called()


def test_repeated_off_on_cycles_never_duplicate_the_saved_repeat(question_app, tmp_path, monkeypatch):
    app = question_app
    monkeypatch.setattr(domain, "utc_now", lambda: START)
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        configure(app, store, TYPED)
        sid, repeat = seed_learned_repeat(store)
        unchanged = evidence(store)
        for _ in range(3):
            set_regular(app, TYPED)
            app.page = "home"
            app.resume_practice()
            assert all(item.kind != AUDIO for item in pending_questions(store))
            assert app.question.content_id == "survivor"
            store.save_session_progress(sid, app.question_index, 1,
                state={"page": "question", "romaji_answer": "draft"})
            set_regular(app, TYPED, AUDIO)
            app.page = "home"
            app.resume_practice()
            assert app.question.content_id == "survivor" and app.romaji_answer == "draft"
            assert_one_saved_repeat(store, repeat)
            assert evidence(store) == unchanged
        app.alert.assert_not_called()


def test_reenable_while_waiting_uses_earliest_original_due_without_changing_other_steps(
        question_app, tmp_path, monkeypatch):
    app = question_app
    app.continue_spaced_practice = MethodType(KanaMemory.continue_spaced_practice, app)
    clock = [START]
    monkeypatch.setattr(domain, "utc_now", lambda: clock[0])
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        configure(app, store, TYPED)
        sid, repeat = seed_learned_repeat(store)
        app.resume_practice()
        clock[0] = START + timedelta(seconds=20)
        typed_review = store.record_review("survivor", TYPED, "hiragana", 1,
            session_id=sid, question_index=app.question_index, outcome="correct", now=clock[0])
        app.resume_practice()
        assert app.page == "session_wait"
        assert app._review_wait_until == datetime.fromisoformat(typed_review["due_at_utc"])
        typed_repeat = deepcopy(pending_questions(store)[0])
        unchanged = evidence(store)
        set_regular(app, TYPED, AUDIO)
        for _ in range(2):
            app.resume_practice()
            assert app.page == "session_wait"
            assert app._review_wait_until == datetime.fromisoformat(repeat.metadata["not_before_utc"])
            assert pending_questions(store) == [repeat, typed_repeat]
            assert evidence(store) == unchanged
        app.show_ink_page.assert_not_called()
        app.alert.assert_not_called()


def test_temporary_reenable_policy_restores_once_after_reopening_store(question_app, tmp_path, monkeypatch):
    app = question_app
    monkeypatch.setattr(domain, "utc_now", lambda: START)
    data = tmp_path / "learner"
    with StudyStore(data, time_zone="UTC") as store:
        configure(app, store, TYPED)
        sid, repeat = seed_learned_repeat(store)
        app.resume_practice()
        store.save_session_progress(sid, app.question_index, 1,
            state={"page": "question", "romaji_answer": "before restart"})
        # Equivalent to explicitly enabling the type in a temporary due form.
        KanaMemory._set_queue_kind_policy(app, sid, (TYPED, AUDIO))
        unchanged = evidence(store)
    for _ in range(2):
        with StudyStore(data, time_zone="UTC") as reopened:
            configure(app, reopened, TYPED)
            app.resume_practice()
            assert app.question.content_id == "survivor"
            assert app.romaji_answer == "before restart"
            assert_one_saved_repeat(reopened, repeat)
            assert evidence(reopened) == unchanged
            assert app._practice_preferences["mixed_kinds"] == [TYPED]
    app.alert.assert_not_called()


def test_off_then_on_without_continuing_keeps_original_queue_exactly(setup_app, tmp_path):
    app = setup_app
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        configure(app, store, TYPED, AUDIO)
        seed_learned_repeat(store)
        before = deepcopy(store.load_session_progress())
        unchanged = evidence(store)
        KanaMemory.show_practice_setup.__wrapped__(app)
        for enabled in (False, True):
            app.mixed_kind_switches[AUDIO].value = enabled
            app.refresh_practice_setup(app.mixed_kind_switches[AUDIO])
            assert store.load_session_progress() == before
            assert evidence(store) == unchanged
        assert AUDIO in store.get_setting("practice_queue_kinds")["kinds"]
        app.alert.assert_not_called()
