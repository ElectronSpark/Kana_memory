"""Appending retains the visible attempt and rejects abandoned background work."""
from runtime_record_test_support import snapshot_json, write_snapshot
import asyncio
from copy import deepcopy
import json
from types import MethodType
from unittest.mock import Mock

import pytest
from support.session_snapshots import unrouted

from support.app_fixtures import CHOICE, app_module, question_app, setup_app, set_mode
from support.fake_ui import View, text_of
from support.background_practice_review_support import real_setup
from support.domain_support import SmallContent
from kanamemory.app import KanaMemory, PRACTICE_PAGES
from kanamemory.domain import Question, StudyStore, prepare_session_snapshot, to_script
from kanamemory.navigation import PageState
from kanamemory.word_keyboard import normalize_word_question_snapshot


from support.append_queue_ui_support import question


@pytest.mark.parametrize("kind,feedback", [
    ("kana_romaji", False), ("word_kana", False),
    ("kana_handwriting", False), ("kana_choice", True),
])
def test_append_restores_original_draft_feedback_audio_and_question_settings(question_app, tmp_path, monkeypatch, kind, feedback):
    app = question_app
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        app.store, app.repo = store, SmallContent()
        app.repo.approved_word_count = lambda: 1
        original, addition = question(kind=kind), question("new", "kana_choice", "katakana")
        app.questions = [original]
        app.session_id = store.start_session(app.questions)
        draft = {"page": "feedback" if feedback else "ink_question" if kind == "kana_handwriting" else
                 "word_question" if kind == "word_kana" else "question",
            "feedback_pending": feedback, "answered_text": original.answer if feedback else "",
            "question_audio_ok": True, "answer_revealed": False, "use_full_keyboard": False,
            "romaji_answer": "partial", "word_answer": "あ", "strokes": [[[10, 20, 100], [20, 40, 120]]],
            "ink_width": 360, "ink_height": 210}
        store.save_session_progress(app.session_id, 0, state=draft)
        app.back_stack.pages.append(PageState(("old",), "question", "old", View(), {"questions": [original]}))
        for method in ("resume_practice", "_checkpoint"):
            setattr(app, method, MethodType(getattr(KanaMemory, method), app))
        app._redraw_ink = Mock()
        def ink_page(**kwargs):
            app.page, app.strokes = "ink_question", []
        app.show_ink_page = ink_page
        # Retain this direction in current settings while checking the append
        # contract. A disabled old direction is intentionally skipped now.
        app.mixed_kind_switches[kind].value = True
        set_mode(app, "综合自适应练习", script="片假名", batch="只学新内容")
        monkeypatch.setattr(app_module, "build_practice_batch", Mock(return_value=[addition]))
        asyncio.run(app.start_practice())
        assert len(app.questions) == 2
        assert unrouted(app.question).to_dict() == normalize_word_question_snapshot(original.to_dict())
        # Legacy word keyboard metadata is migrated only for rendering. The
        # appended queue retains the authoritative original snapshot and draft.
        saved = json.loads(snapshot_json(store._db, app.session_id))
        route = saved[0]["metadata"].pop("kana_ability_route_v1", None)
        assert (route is None) if kind in {"word_kana", "kana_handwriting"} else route["skill"] == kind
        assert saved[0] == json.loads(json.dumps(original.to_dict(), ensure_ascii=False))
        assert app.question_index == 0 and app.session_right == 0
        assert app.script == "hiragana" and not app.research_enabled and not app.use_full_keyboard
        assert app.question_audio_ok
        assert all(view.name not in PRACTICE_PAGES for view in app.back_stack.pages)
        assert store.stats()["total_reviews"] == 0
        if feedback:
            assert app.page == "feedback" and app.answered_text == original.answer
        elif kind == "kana_romaji":
            assert app.romaji_input.value == app.romaji_answer == "partial"
        elif kind == "word_kana":
            assert app.word_answer == app.word_answer_label.text == "あ"
        else:
            assert [[point[:2] for point in stroke] for stroke in app.strokes] == [[(10, 20), (20, 40)]]
            assert app.strokes[0][1][2] - app.strokes[0][0][2] == 20
            app._redraw_ink.assert_called_once()
        assert not app.practice_replace_slot.children
        app.alert.assert_not_called()


@pytest.mark.parametrize("failure", ["empty", "generation", "storage"])
def test_failed_append_keeps_original_queue_and_current_draft(setup_app, tmp_path, monkeypatch, failure):
    app = setup_app
    with StudyStore(tmp_path / "learner") as store:
        app.store, app.repo = store, SmallContent()
        app.repo.approved_word_count = lambda: 1
        app.questions = [question()]
        app.session_id = store.start_session(app.questions)
        store.save_session_progress(app.session_id, 0, state={"page": "question", "romaji_answer": "draft"})
        pending = deepcopy(store.load_session_progress())
        app.resume_practice = Mock()
        set_mode(app, "综合自适应练习", batch="只学新内容")
        build = Mock(return_value=[] if failure == "empty" else [question("new")])
        if failure == "generation":
            build.side_effect = RuntimeError("generation failed")
        if failure == "storage":
            monkeypatch.setattr(store, "append_session", Mock(side_effect=RuntimeError("disk full")))
        monkeypatch.setattr(app_module, "build_practice_batch", build)
        asyncio.run(app.start_practice())
        assert store.load_session_progress() == pending
        assert app.questions == unrouted(pending["questions"]) and app.session_id == pending["session_id"]
        assert store.stats()["total_reviews"] == 0
        assert app.practice_start_button.enabled
        assert app.practice_start_button.text == "追加到当前练习"
        app.resume_practice.assert_not_called()
        app.show_question.assert_not_called()
        app.alert.assert_called_once()


@pytest.mark.parametrize("change", ["leave", "settings", "external_learning", "confirmation", "cancel"])
def test_stale_background_append_cannot_change_pending_queue(real_setup, monkeypatch, change):
    app = real_setup
    original, addition = question(), question("new")
    sid = app.store.start_session([original])
    app.store.save_session_progress(sid, 0, state={"page": "question", "romaji_answer": "draft"})
    pending = deepcopy(app.store.load_session_progress())
    set_mode(app, "综合自适应练习", batch="只学新内容")
    app.resume_practice = Mock()
    async def worker(*args, **kwargs):
        if change == "leave":
            app.page, app._page_token = "home", app._page_token + 1
        elif change == "settings":
            app.count_picker.value = "20 项"
        elif change == "external_learning":
            with StudyStore(app.store.data_dir) as other:
                other.record_review("other", "kana_choice", "hiragana", 4)
        elif change == "confirmation":
            app._leave_confirmation_active = True
        else:
            raise asyncio.CancelledError()
        return [addition], prepare_session_snapshot([addition], seed=2)
    monkeypatch.setattr(app_module, "build_in_background", worker)
    if change == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(app.start_practice())
    else:
        asyncio.run(app.start_practice())
    assert app.store.load_session_progress() == pending
    app.resume_practice.assert_not_called()
    app.show_question.assert_not_called()
    app.alert.assert_not_called()
    assert not app._starting_practice


def test_new_question_uses_its_own_word_settings_after_original_finishes(question_app):
    app = question_app
    first, second = question(kind="word_meaning_kana"), question("new", "word_meaning_kana", "katakana")
    second.metadata.update(research_content=True, word_table="full")
    app.questions = [first, second]
    app.research_enabled, app.use_full_keyboard = True, True
    app.show_question()
    assert not app.research_enabled and not app.use_full_keyboard
    app.question_index = 1
    app.show_question()
    assert app.research_enabled and app.use_full_keyboard and app.script == "katakana"
    assert "含待审核词汇" in text_of(app.body)
