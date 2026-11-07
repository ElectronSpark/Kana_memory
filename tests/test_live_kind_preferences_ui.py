"""Current question choices apply to saved queues without losing usable drafts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import MethodType
from unittest.mock import Mock

import pytest

from support.session_snapshots import unrouted

from support.app_fixtures import app_module, question_app, setup_app
from support.append_queue_ui_support import question
from kanamemory.app import KanaMemory
from kanamemory.domain import StudyStore


from support.live_kind_preferences_ui_support import preferences, configure, evidence


@pytest.mark.parametrize("feedback", [False, True])
def test_resume_discards_disabled_current_handwriting_state_only(question_app, tmp_path, feedback):
    app = question_app
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        configure(app, store, "kana_romaji", "romaji_handwriting")
        disabled = question("audio", "kana_handwriting")
        survivor = question("visual", "kana_romaji")
        sid = store.start_session([disabled, survivor])
        store.save_session_progress(sid, 0, assisted_indices=[0], state={
            "page": "feedback" if feedback else "ink_question", "feedback_pending": feedback,
            "answered_text": "あ", "answer_revealed": True, "question_audio_ok": True,
            "strokes": [[[10, 20, 100], [20, 40, 120]]], "romaji_answer": "stale"})
        before = evidence(store)
        app.resume_practice()
        assert unrouted(app.question) == survivor and app.page == "question"
        assert app.romaji_answer == app.romaji_input.value == ""
        assert not app.answer_revealed and app.answered_text is None
        assert app.question_index == 1 and app.session_right == 0
        assert app._attempt_key() not in app._assisted_attempts
        assert store.load_session_progress()["state"]["page"] == "question"
        assert evidence(store) == before
        app.show_ink_page.assert_not_called()
        app._redraw_ink.assert_not_called()
        app.alert.assert_not_called()


@pytest.mark.parametrize("feedback", [False, True])
def test_resume_preserves_allowed_current_draft_after_disabled_tail_moves(question_app, tmp_path, feedback):
    app = question_app
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        configure(app, store, "kana_romaji", "romaji_handwriting")
        survivor = question("current", "kana_romaji")
        disabled = question("audio", "kana_handwriting")
        future = question("later", "romaji_handwriting")
        due = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        future.metadata["not_before_utc"] = due
        sid = store.start_session([survivor, disabled, future])
        saved = {"page": "feedback" if feedback else "question", "feedback_pending": feedback,
                 "answered_text": "a" if feedback else "", "question_audio_ok": True,
                 "answer_revealed": False, "romaji_answer": "a" if feedback else "partial"}
        store.save_session_progress(sid, 0, state=saved)
        before = evidence(store)
        app.resume_practice()
        assert unrouted(app.question) == survivor and app.question_index == 1
        assert app.romaji_answer == saved["romaji_answer"]
        assert app.page == ("feedback" if feedback else "question")
        assert app.answered_text == ("a" if feedback else None)
        pending = store.load_session_progress()
        assert unrouted(pending["questions"][pending["question_index"]:]) == [survivor, future]
        assert pending["questions"][-1].metadata["not_before_utc"] == due
        assert evidence(store) == before
        app.alert.assert_not_called()


def test_all_disabled_finishes_without_restoring_or_grading_old_feedback(question_app, tmp_path):
    app = question_app
    with StudyStore(tmp_path / "learner") as store:
        configure(app, store, "kana_choice")
        sid = store.start_session([question("audio", "kana_handwriting")])
        store.save_session_progress(sid, 0, state={"page": "feedback", "feedback_pending": True,
            "answered_text": "あ", "question_audio_ok": True})
        before = evidence(store)
        app.resume_practice()
        assert app.page == "session_done"
        assert store.load_session_progress() is None
        assert store.get_session_summary(sid)["answered"] == 0
        assert evidence(store) == before
        app.show_ink_page.assert_not_called()
        app.alert.assert_not_called()


def test_empty_selection_pauses_saved_queue_until_user_chooses_a_type(question_app, tmp_path):
    app = question_app
    with StudyStore(tmp_path / "learner") as store:
        configure(app, store)
        sid = store.start_session([question("current", "kana_romaji")])
        store.save_session_progress(sid, 0, state={"page": "question", "romaji_answer": "partial"})
        before = deepcopy(store.load_session_progress())
        app.resume_practice()
        assert app.page == "home" and store.load_session_progress() == before
        assert store.stats()["total_reviews"] == 0
        app.alert.assert_called_once()
        app._practice_preferences = preferences("kana_romaji")
        store.set_setting("practice_preferences", app._practice_preferences)
        app.resume_practice()
        assert app.page == "question" and app.romaji_answer == "partial"


def test_temporary_queue_policy_survives_restart_but_regular_change_overrides_it(question_app, tmp_path):
    app = question_app
    data = tmp_path / "learner"
    store = StudyStore(data)
    try:
        configure(app, store, "kana_choice")
        original = question("typed", "kana_romaji")
        sid = store.start_session([original, question("choice", "kana_choice")])
        store.save_session_progress(sid, 0, state={"page": "question", "romaji_answer": "partial"})
        KanaMemory._set_queue_kind_policy(app, sid, ("kana_romaji", "kana_choice"))
        store.close()
        store = StudyStore(data)
        configure(app, store, "kana_choice")
        app.resume_practice()
        assert unrouted(app.question) == original and app.romaji_answer == "partial"
        # A later ordinary setting change invalidates the old temporary plan.
        app._practice_preferences = preferences("romaji_kana", "kana_choice")
        store.set_setting("practice_preferences", app._practice_preferences)
        app.page = "home"
        app.resume_practice()
        assert app.question.kind == "kana_choice" and app.question_index == 1
        assert store.stats()["total_reviews"] == 0
    finally:
        store.close()


def test_opening_temporary_due_form_does_not_replace_existing_queue_policy(setup_app, tmp_path):
    app = setup_app
    with StudyStore(tmp_path / "learner") as store:
        configure(app, store, "kana_choice", "kana_romaji")
        sid = store.start_session([question()])
        store.save_session_progress(sid, 0, state={"page": "question", "romaji_answer": "partial"})
        KanaMemory._set_queue_kind_policy(app, sid, ("kana_romaji",))
        before = deepcopy(store.load_session_progress())
        policy = deepcopy(store.get_setting("practice_queue_kinds"))
        temporary = preferences("kana_choice")
        KanaMemory.show_practice_setup.__wrapped__(app, temporary_preferences=temporary)
        assert store.load_session_progress() == before
        assert store.get_setting("practice_queue_kinds") == policy
        assert app._practice_preferences["mixed_kinds"] == ["kana_choice", "kana_romaji"]


def test_explicit_temporary_type_change_updates_policy_without_rewriting_queue(setup_app, tmp_path):
    app = setup_app
    with StudyStore(tmp_path / "learner") as store:
        configure(app, store, "kana_choice")
        sid = store.start_session([question("audio", "kana_handwriting"), question("visual", "kana_romaji")])
        store.save_session_progress(sid, 0)
        KanaMemory._set_queue_kind_policy(app, sid, ("kana_handwriting", "kana_romaji"))
        before = deepcopy(store.load_session_progress())
        KanaMemory.show_practice_setup.__wrapped__(app,
            temporary_preferences=preferences("kana_handwriting", "kana_romaji"))
        app.mixed_kind_switches["kana_handwriting"].value = False
        app.refresh_practice_setup(app.mixed_kind_switches["kana_handwriting"])
        policy = store.get_setting("practice_queue_kinds")
        assert policy["session_id"] == sid and policy["kinds"] == ["kana_romaji"]
        assert store.load_session_progress() == before
        assert app._practice_preferences["mixed_kinds"] == ["kana_choice"]


def test_completed_batch_cannot_reenable_any_disabled_question_type(setup_app):
    app = setup_app
    app._clean_preferences = KanaMemory._clean_preferences
    app._practice_preferences = preferences("kana_romaji", "romaji_handwriting")
    old = question("old", "kana_choice")
    old.metadata["practice_config"].update(kinds=list(app_module.ALL_MIXED_KINDS), requested_count=7,
        requested_script="katakana", kana_categories=["yoon"])
    app.questions, app.page = [old], "session_done"
    app.show_practice_setup = lambda **kwargs: KanaMemory.show_practice_setup.__wrapped__(app, **kwargs)
    KanaMemory.show_new_batch(app)
    assert app._practice_kinds() == ("kana_romaji", "romaji_handwriting")
    assert app.script_picker.value == "片假名"
    assert app.custom_count_input.value == "7"
    assert app.scope_switches["拗音"].value
