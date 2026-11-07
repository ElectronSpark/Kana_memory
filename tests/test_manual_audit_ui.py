"""Regression cases found while manually using the Android preview."""
import asyncio
from unittest.mock import Mock

from support.app_fixtures import setup_app, app_module
from support.fake_ui import descendants, text_of
from kanamemory.domain import Question


def action(app, title):
    return next(w for w in descendants(app.practice_replace_slot) if w.text == title).on_press


def saved_batch(session_id="old", *, questions=None, state=None):
    return {"session_id": session_id, "question_index": 0, "session_right": 0,
            "assisted_indices": [], "state": state or {"page": "question"},
            "questions": questions if questions is not None else [
                Question("kana-3042", "kana_choice", "hiragana", "あ", "a", ["a", "i"])]}


def test_new_batch_preserves_pending_answer_until_explicit_replacement(setup_app, monkeypatch):
    app = setup_app
    draft = saved_batch(state={"page": "feedback", "feedback_pending": True, "answered_text": "a"})
    app.store.load_session_progress.return_value = draft
    build = Mock(return_value=[object()])
    monkeypatch.setattr(app_module, "build_questions", build)
    app.resume_practice = Mock()
    asyncio.run(app.start_practice())
    build.assert_not_called()
    app.store.start_session.assert_not_called()
    assert draft["state"] == {"page": "feedback", "feedback_pending": True, "answered_text": "a"}
    assert "未提交答案" in text_of(app.practice_replace_slot)
    # Start stays at the top. The replacement warning expands immediately
    # below it, before the summary and configuration controls.
    children = list(app.body.children)
    assert children[:2] == [app.practice_start_button, app.practice_replace_slot]
    action(app, "继续原练习")(None)
    app.resume_practice.assert_called_once()
    asyncio.run(action(app, "结束原批次并开始")())
    app.store.start_session.assert_called_once()
    app.show_question.assert_called_once()


def test_repeated_start_clicks_do_not_replace_pending_batch(setup_app, monkeypatch):
    app = setup_app
    app.store.load_session_progress.return_value = saved_batch()
    monkeypatch.setattr(app_module, "build_questions", Mock())
    for _ in range(3):
        asyncio.run(app.start_practice())
    app.store.start_session.assert_not_called()
    assert text_of(app.practice_replace_slot).count("还有未完成的练习") == 1


def test_changed_settings_invalidate_old_replace_action(setup_app, monkeypatch):
    app = setup_app
    app.store.load_session_progress.return_value = saved_batch()
    monkeypatch.setattr(app_module, "build_questions", Mock())
    asyncio.run(app.start_practice())
    stale = action(app, "结束原批次并开始")
    app.count_picker.value = "20 项"
    app.refresh_practice_setup(app.count_picker)
    assert not app.practice_replace_slot.children
    asyncio.run(stale())
    app.store.start_session.assert_not_called()


def test_replacement_permission_cannot_apply_to_a_different_pending_batch(setup_app, monkeypatch):
    app = setup_app
    app.store.load_session_progress.return_value = saved_batch()
    monkeypatch.setattr(app_module, "build_questions", Mock())
    asyncio.run(app.start_practice())
    replace = action(app, "结束原批次并开始")
    app.store.load_session_progress.return_value = saved_batch("newer")
    asyncio.run(replace())
    app.store.start_session.assert_not_called()


def test_failed_new_batch_does_not_replace_in_memory_old_questions(setup_app, monkeypatch):
    app = setup_app
    pending = saved_batch()
    original = pending["questions"]
    app.questions, app.session_id = original, "old"
    app.store.load_session_progress.return_value = pending
    app.store.start_session.side_effect = RuntimeError("disk full")
    monkeypatch.setattr(app_module, "build_questions", Mock(return_value=[object()]))
    asyncio.run(app.start_practice())
    asyncio.run(action(app, "结束原批次并开始")())
    assert app.questions is original and app.session_id == "old"
    app.show_question.assert_not_called()


def test_empty_unreviewed_collection_explains_how_to_enable_it(setup_app):
    app = setup_app
    app.kind_picker.value = "看词义和罗马音选假名"
    app.practice_word_collection_picker.value = "英语借词"
    app.repo.approved_word_count.return_value = 121
    app.repo.practice_word_candidates.return_value = []
    app.practice_plan_summary.return_value = {"total": 0, "due": 0, "new": 0}
    app.refresh_practice_setup()
    assert not app.practice_start_button.enabled
    text = text_of(app.practice_services)
    assert "所选词汇组" in text and "使用待审核词汇" in text and "基础词" in text
    assert app.research_switch.value is False
