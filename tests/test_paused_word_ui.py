"""Dispatching a parked word restores its real widgets before checkpointing."""
from copy import deepcopy
from types import MethodType
from unittest.mock import Mock

import pytest

from support.app_fixtures import app_module, question_app, setup_app
from support.fake_ui import text_of, descendants
from support.live_kind_preferences_ui_support import configure
from support.live_kind_preferences_ui_support import preferences
from support.paused_word_questions_support import DRAFT
from support.paused_word_questions_support import FEEDBACK
from support.paused_word_questions_support import KINDS
from support.word_context_memory_support import NOW
from support.paused_word_questions_support import evidence
from support.word_context_memory_support import word_question
from kanamemory.app import KanaMemory
from kanamemory.domain import Question, StudyStore, word_romaji_unit_plan


@pytest.fixture
def paused_app(question_app, tmp_path):
    app = question_app
    with StudyStore(tmp_path / "learner", "UTC") as store:
        configure(app, store, *KINDS)
        app._ink_revision = app._audio_serial = 0
        app.repo.kana_list.return_value = []
        app.repo.get_word.return_value = None
        app.repo.get_kana.return_value = None
        app.show_practice_setup = Mock()
        app.show_word_drill_setup = Mock()
        original = app.screen

        def screen(*args, **kwargs):
            value = original(*args, **kwargs)
            app._checkpoint()  # production screen writes the rendered state
            return value

        app.screen = screen
        yield app


def begin(app, state, *, tail=()):
    word = word_question()
    word.metadata.update(romaji="kaki", meaning_hint="柿子")
    sid = app.store.start_session([word, *tail], now=NOW)
    app.store.save_session_progress(sid, 0, state=deepcopy(state))
    return sid


def set_kinds(app, kinds):
    app._practice_preferences = preferences(*kinds)
    app.store.set_setting("practice_preferences", app._practice_preferences)


@pytest.mark.parametrize("state", [DRAFT, FEEDBACK])
def test_sole_disabled_word_shows_pause_then_restores_widgets_without_new_review(paused_app, state):
    app = paused_app
    begin(app, state)
    before = evidence(app.store)
    set_kinds(app, ["kana_choice"])
    app.resume_practice()
    assert app.page == "session_wait"
    assert "题型已暂停" == app.title
    assert "未完成的题目和草稿已保留" in text_of(app.body)
    assert app.store.load_session_progress() is not None
    set_kinds(app, KINDS)
    app.resume_practice()
    assert app.page == state["page"]
    if state is DRAFT:
        assert app.word_answer == app.word_answer_label.text == "か"
    else:
        assert app.answered_text == "かき"
        assert app._answer_submitted_at == state["submitted_at_utc"]
        assert app._answer_duration_ms == 2500
    assert evidence(app.store) == before
    app.alert.assert_not_called()


@pytest.mark.parametrize("lane", ["main", "word_romaji"])
def test_pause_settings_button_opens_the_same_lanes_settings(paused_app, lane):
    app = paused_app
    word = word_question()
    if lane == "word_romaji":
        word.metadata["word_romaji_drill"] = True
        app.store.set_setting("word_drill_preferences", {"direction": "假名 → 选罗马音"})
    else:
        set_kinds(app, ["kana_choice"])
    app.store.practice_lane = lane
    sid = app.store.start_session([word], now=NOW)
    app.store.save_session_progress(sid, 0, state=DRAFT)
    app.resume_practice()
    assert app.page == "session_wait"
    next(view for view in descendants(app.body) if view.text == "练习设置").on_press(None)
    selected = app.show_word_drill_setup if lane == "word_romaji" else app.show_practice_setup
    other = app.show_practice_setup if lane == "word_romaji" else app.show_word_drill_setup
    selected.assert_called_once()
    other.assert_not_called()


@pytest.mark.parametrize("feedback", [False, True])
def test_reverse_word_lane_recovers_exact_parts_and_plan_after_other_word(paused_app, feedback):
    app = paused_app
    reverse = Question("word-ka", "word_romaji", "hiragana", "かき", "kaki",
        metadata={"kana_answer": "かき", "word_romaji_drill": True})
    reverse.metadata["romaji_unit_plan"] = word_romaji_unit_plan(reverse)
    other = word_question()
    other.metadata.update(word_romaji_drill=True, romaji="kaki", meaning_hint="柿子")
    app.store.practice_lane = "word_romaji"
    sid = app.store.start_session([reverse, other], now=NOW)
    plan = word_romaji_unit_plan(app.store.get_session_questions(sid)[0])
    parts = [unit["answer"] for unit in plan["units"]][:2 if feedback else 1]
    state = {"page": "question", "word_romaji_parts": parts, "word_romaji_plan": plan}
    if feedback:
        state.update(FEEDBACK, answered_text="kaki")
    app.store.save_session_progress(sid, 0, state=state)
    app.store.set_setting("word_drill_preferences", {"direction": "罗马音 → 假名"})
    app.resume_practice()
    assert app.question.kind == "word_meaning_kana"
    app.store.set_setting("word_drill_preferences", {"direction": "双向混合"})
    app.resume_practice()
    app.store.record_review(other.content_id, other.kind, other.script, None,
        session_id=sid, question_index=app.question_index, answered_text="かき",
        outcome="correct", now=NOW)
    before = evidence(app.store)
    app.show_question()
    assert app.page == ("feedback" if feedback else "question")
    assert app.word_romaji_parts == parts and app.word_romaji_plan == plan
    assert app.store.load_session_progress()["state"]["word_romaji_parts"] == parts
    if feedback:
        assert app.answered_text == "kaki"
        assert app._answer_submitted_at == FEEDBACK["submitted_at_utc"]
    else:
        assert app.word_romaji_answer_label.text == "ka"
    assert evidence(app.store) == before
    app.alert.assert_not_called()


@pytest.mark.parametrize("state", [DRAFT, FEEDBACK])
@pytest.mark.parametrize("reopen", [False, True])
def test_rating_other_question_restores_parked_word_with_screen_checkpoint(paused_app, state, reopen):
    app = paused_app
    other = Question("test-other", "kana_romaji", "hiragana", "く", "ku",
        metadata={"kana_symbol": "く", "romaji": "ku"})
    sid = begin(app, state, tail=[other])
    set_kinds(app, ["kana_romaji"])
    app.resume_practice()
    assert app.page == "question" and app.question.content_id == other.content_id
    app.romaji_answer = "k"
    app._checkpoint()
    set_kinds(app, [*KINDS, "kana_romaji"])
    app.resume_practice()
    assert app.romaji_answer == "k"
    app.store.record_review(other.content_id, other.kind, other.script, 4,
        session_id=sid, question_index=app.question_index, outcome="correct", now=NOW)
    before = evidence(app.store)
    # The ordinary rating path directly dispatches the next head. The alternate
    # path reopens while dispatch still has to activate the parked state.
    if reopen:
        app.resume_practice()
    else:
        app.show_question()
    assert app.page == state["page"]
    saved = app.store.load_session_progress()["state"]
    if state is DRAFT:
        assert app.word_answer == app.word_answer_label.text == saved["word_answer"] == "か"
    else:
        assert app.answered_text == saved["answered_text"] == "かき"
        assert saved["feedback_pending"]
        assert saved["submitted_at_utc"] == state["submitted_at_utc"]
    assert evidence(app.store) == before
    app.alert.assert_not_called()
