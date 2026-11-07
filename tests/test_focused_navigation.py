"""A practice attempt is a flow, not a stack of reversible question pages."""
import asyncio
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from support.answer_option_ui_support import control
from support.answer_option_ui_support import preview_app
from support.answer_option_ui_support import render
from support.fake_ui import View, text_of
from support.app_fixtures import question_app, setup_app
from kanamemory import app as app_module
from kanamemory.app import FOCUSED_PAGES, KanaMemory
from kanamemory.domain import StudyStore
from kanamemory.learning_ui import LEARNING_PAGES, LearningUIMixin
from kanamemory.navigation import PageState, page_view
from kanamemory.retention_session_ui import DIAGNOSTIC_PAGES
from kanamemory.tutor_ui import TutorUIMixin

SAVED_FOCUSED_PAGES = {"session_wait", "session_done", "kana_intro_wait", "dependency_wait", "learning_wait", "learning_done", "ai_practice_done", "retention_done", "retention_wait", "tutor_center", "tutor_wait", "tutor_profile"}


from support.focused_navigation_support import focused_app, resolve_confirmation


def test_feedback_system_back_confirms_exit_without_restoring_unanswered_question(focused_app):
    app = focused_app
    question, values = render(app)
    question_view = app.content_slot.children[0]
    app.choose_practice_answer(values[0], question)
    feedback = app.content_slot.children[0]
    assert feedback is not question_view
    assert all(state.name != "question" for state in app.back_stack.pages)
    assert app.go_back() is True
    assert app.page == "feedback" and app.content_slot.children[0] is feedback
    assert app.question is question and app.answer_correct
    resolve_confirmation(app, False)
    assert app.page == "feedback" and app.content_slot.children[0] is feedback
    assert app.answered_text == "ka" and not app.answer_revealed
    assert "记住了" in text_of(feedback)
    app.store.record_review.assert_not_called()


@pytest.mark.parametrize("kind,draft_field,draft", [("kana_romaji", "romaji_answer", "k"),
                                                    ("word_meaning_kana", "word_answer", "か")])
def test_cancel_then_confirm_preserves_real_saved_draft_and_does_not_grade(focused_app, tmp_path, kind, draft_field, draft):
    app = focused_app
    question, _ = render(app, kind)
    setattr(app, draft_field, draft)
    if kind == "kana_romaji":
        app.romaji_input.value = draft
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        app.store = store
        app.session_id = store.start_session([question])
        # The frozen snapshot owns the queue, as it does for a real fresh session.
        app.question = store.get_session_questions(app.session_id)[0]
        app.questions = [app.question]
        app._checkpoint = MethodType(KanaMemory._checkpoint, app)
        app._checkpoint()
        before = store.load_session_progress()
        current_view = app.content_slot.children[0]
        app.back_stack.pages.append(PageState(("show_words", None), "words", "词库", View()))
        app.go_back()
        resolve_confirmation(app, False)
        assert app.content_slot.children[0] is current_view
        assert getattr(app, draft_field) == draft
        assert store.load_session_progress()["state"] == before["state"]
        assert store.stats()["total_reviews"] == 0
        app.go_back()
        resolve_confirmation(app, True)
        saved = store.load_session_progress()
        assert app.page == "home" and not app.back_stack.pages
        assert saved["session_id"] == before["session_id"] and saved["question_index"] == 0
        assert saved["state"][draft_field] == draft
        assert store.stats()["total_reviews"] == 0
        assert app.navigation in app.root.children and not app.header_back_slot.children


def test_confirmed_feedback_exit_clears_history_without_changing_answer_or_review(focused_app):
    app = focused_app
    question, values = render(app)
    app.choose_practice_answer(values[1], question)
    app.back_stack.pages.append(PageState(("show_words", None), "words", "词库", View()))
    answer = app.answered_text
    app.go_back()
    resolve_confirmation(app, True)
    assert app.page == "home" and app._view_key == ("show_home", None)
    assert not app.back_stack.pages and not app._practice_context
    assert app.answered_text == answer and not app.answer_correct
    assert app.question_index == 0 and app.session_right == 0
    assert app._attempt_key() in app._answered_attempts
    app.store.record_review.assert_not_called()
    assert not app._leave_confirmation_active and not app._returning_home


def test_repeated_back_while_dialog_pending_creates_only_one_confirmation(focused_app):
    app = focused_app
    render(app)
    for _ in range(4):
        app.go_back()
        app.header_back_button.on_press(None)
    assert len(app.pending_confirmation) == 1 and app._leave_confirmation_active
    resolve_confirmation(app, False)
    app.main_window.dialog.assert_awaited_once()
    assert not app._leave_confirmation_active
    app.go_back()
    assert len(app.pending_confirmation) == 1
    resolve_confirmation(app, False)
    assert app.main_window.dialog.await_count == 2


def test_late_confirmation_cannot_exit_a_new_page_or_discard_its_history(focused_app):
    app = focused_app
    render(app)

    async def run():
        ready = asyncio.get_running_loop().create_future()
        app.main_window.dialog.side_effect = lambda dialog: ready
        # AsyncMock would return the future as a value, so explicitly await it.
        async def dialog(_):
            return await ready
        app.main_window.dialog.side_effect = dialog
        app.go_back()
        task = asyncio.create_task(app.pending_confirmation.pop())
        await asyncio.sleep(0)
        assert app._leave_confirmation_active
        app.show_kana_detail("ka")
        new_view, history = app.content_slot.children[0], list(app.back_stack.pages)
        ready.set_result(True)
        await task
        assert app.page == "kana_detail" and app.content_slot.children[0] is new_view
        assert app.back_stack.pages == history and not app._leave_confirmation_active
    asyncio.run(run())
    app.store.record_review.assert_not_called()


# Chat returns to its originating question; loading cancels a request. Those
# are tested with real persisted tutor state in test_tutor_ui, not this fixture.
# Back on a retention diagnostic page goes to diagnostic_back ("暂停检查？",
# tested in test_retention_session_ui); routing is checked below.
INTERACTIVE_ENTRIES = [(page, entry)
    for page in sorted(FOCUSED_PAGES - SAVED_FOCUSED_PAGES - {"tutor_chat", "tutor_loading"})
    for entry in ("system", "header", "home_action")
    if not (page in DIAGNOSTIC_PAGES and entry != "home_action")]


@pytest.mark.parametrize("page,entry", INTERACTIVE_ENTRIES)
def test_interactive_focused_pages_hide_bar_and_share_confirmation_policy(focused_app, page, entry):
    app = focused_app
    render(app)
    app.screen(page, "特殊页面", View(text="当前内容"))
    assert app.navigation not in app.root.children
    assert app.header_back_slot.children == [app.header_back_button]
    if entry == "system":
        assert app.go_back() is True
    elif entry == "header":
        app.header_back_button.on_press(None)
    else:
        KanaMemory.show_home(app)
    assert app.page == page and len(app.pending_confirmation) == 1
    resolve_confirmation(app, False)
    dialog = app.main_window.dialog.await_args.args[0]
    assert dialog.title == ("返回今日？" if page in LEARNING_PAGES else "返回首页？")
    assert app.page == page and app.navigation not in app.root.children
    app.store.record_review.assert_not_called()


@pytest.mark.parametrize("page", sorted(DIAGNOSTIC_PAGES))
@pytest.mark.parametrize("entry", ["system", "header"])
def test_diagnostic_back_uses_the_check_pause_policy(focused_app, page, entry):
    app = focused_app
    render(app)
    app.diagnostic_back = Mock(return_value=True)
    app.screen(page, "特殊页面", View(text="当前内容"))
    assert app.navigation not in app.root.children
    if entry == "system":
        assert app.go_back() is True
    else:
        app.header_back_button.on_press(None)
    app.diagnostic_back.assert_called_once_with()
    assert app.page == page and not app.pending_confirmation
    app.store.record_review.assert_not_called()


@pytest.mark.parametrize("page", ["retention_done", "retention_wait"])
@pytest.mark.parametrize("entry", ["system", "header", "home_action"])
def test_finished_or_waiting_retention_check_returns_home_without_a_dialog(focused_app, page, entry):
    app = focused_app
    render(app)
    app.screen(page, "检查结束", View(text="当前内容"))
    if entry == "system":
        assert app.go_back() is True
    elif entry == "header":
        app.header_back_button.on_press(None)
    else:
        KanaMemory.show_home(app)
    assert app.page == "home" and not app.pending_confirmation
    app.main_window.dialog.assert_not_awaited()
    app.store.record_review.assert_not_called()


@pytest.mark.parametrize("entry", ["system", "header", "home_action"])
@pytest.mark.parametrize("page", ["session_done", "learning_done"])
def test_completed_session_returns_home_directly_without_altering_saved_reviews(focused_app, tmp_path, entry, page):
    app = focused_app
    question, _ = render(app)
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        app.store = store
        app.session_id = store.start_session([question])
        store.save_session_progress(app.session_id, 0)
        store.record_review(question.content_id, question.kind, question.script, 4,
                            session_id=app.session_id, question_index=0,
                            outcome="correct", answered_text=question.answer)
        app.question_index, app.session_right = 1, 1
        app._checkpoint = MethodType(KanaMemory._checkpoint, app)
        app.screen(page, "本批完成", View(text="完成 1 题"))
        assert store.load_session_progress() is None
        assert store.get_session_summary(app.session_id)["answered"] == 1
        before = tuple(store._db.iterdump())
        assert app.navigation not in app.root.children
        assert app.header_back_slot.children == [app.header_back_button]
        app.back_stack.pages.append(PageState(("show_words", None), "words", "词库", View()))
        if entry == "system":
            assert app.go_back() is True
        elif entry == "header":
            app.header_back_button.on_press(None)
        else:
            KanaMemory.show_home(app)
        assert app.page == "home" and not app.pending_confirmation
        app.main_window.dialog.assert_not_awaited()
        assert not app._leave_confirmation_active and not app._returning_home
        assert not app.back_stack.pages and not app._practice_context
        assert app.navigation in app.root.children and not app.header_back_slot.children
        assert store.load_session_progress() is None
        assert tuple(store._db.iterdump()) == before


@pytest.mark.parametrize("entry", ["system", "header", "home_action"])
@pytest.mark.parametrize("page", ["session_wait", "learning_wait", "kana_intro_wait", "dependency_wait",
                                  "tutor_center", "tutor_wait", "tutor_profile"])
def test_saved_waiting_pages_return_directly_and_retain_pending_queue(focused_app, tmp_path, entry, page):
    app = focused_app
    question, _ = render(app)
    with StudyStore(tmp_path / "waiting-learner", time_zone="UTC") as store:
        app.store = store
        app.session_id = store.start_session([question])
        store.save_session_progress(app.session_id, 0, state={"romaji_answer": "saved-draft"})
        app.question_index, app.session_right = 0, 0
        app.screen(page, "稍后继续", View(text="进度已经保存"))
        before = tuple(store._db.iterdump())
        app.back_stack.pages.append(PageState(("show_words", None), "words", "词库", View()))
        assert app.navigation not in app.root.children
        assert app.header_back_slot.children == [app.header_back_button]
        if entry == "system":
            assert app.go_back() is True
        elif entry == "header":
            app.header_back_button.on_press(None)
        else:
            KanaMemory.show_home(app)
        assert app.page == "home" and not app.pending_confirmation
        app.main_window.dialog.assert_not_awaited()
        assert not app.back_stack.pages and not app._practice_context
        assert app.navigation in app.root.children and not app.header_back_slot.children
        assert tuple(store._db.iterdump()) == before
        assert store.load_session_progress()["state"]["romaji_answer"] == "saved-draft"
        assert store.stats()["total_reviews"] == 0


@pytest.mark.parametrize("entry", ["system", "header"])
def test_detail_back_restores_current_feedback_and_keeps_navigation_hidden(focused_app, entry):
    app = focused_app
    question, values = render(app)
    app.choose_practice_answer(values[0], question)
    feedback, preview = app.content_slot.children[0], app.practice_preview_slot
    control(app, "详细").on_press(None)
    assert app.page == "kana_detail" and app.navigation not in app.root.children
    assert app.header_back_slot.children == [app.header_back_button]
    assert app.back_stack.pages[-1].name == "feedback"
    if entry == "system":
        app.go_back()
    else:
        app.header_back_button.on_press(None)
    assert app.page == "feedback" and app.content_slot.children[0] is feedback
    assert app.practice_preview_slot is preview
    assert app.question is question and app.answer_correct and app.answered_text == "ka"
    assert app.navigation not in app.root.children
    assert app.header_back_slot.children == [app.header_back_button]
    assert not app.pending_confirmation
    assert all(state.name not in {"question", "feedback"} for state in app.back_stack.pages)
    app.store.record_review.assert_not_called()


def test_save_failure_keeps_feedback_and_history_even_after_exit_confirmation(focused_app):
    app = focused_app
    question, values = render(app)
    app.choose_practice_answer(values[0], question)
    current, history = app.content_slot.children[0], list(app.back_stack.pages)
    app._checkpoint.return_value = False
    app.go_back()
    resolve_confirmation(app, True)
    assert app.page == "feedback" and app.content_slot.children[0] is current
    assert app.back_stack.pages == history and not app._leave_confirmation_active
    assert app.navigation not in app.root.children
    app.store.record_review.assert_not_called()


def test_checkpoint_returns_failure_when_actual_storage_write_fails(focused_app):
    app = focused_app
    render(app)
    app.store.save_session_progress.side_effect = OSError("disk full")
    assert KanaMemory._checkpoint(app) is False
    assert "未完成练习暂未保存" in app.alert.call_args.args[0]
    app.store.record_review.assert_not_called()
