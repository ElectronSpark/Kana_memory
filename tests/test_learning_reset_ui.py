"""Deleting progress requires a current confirmation and retires old views."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from record_test_support import decode_current, encode_current, as_legacy

from support.learning_reset_support import NOW
from support.learning_reset_support import populate
from support.fake_ui import View, descendants
from support.app_fixtures import app_module, setup_app
from kanamemory.app import KanaMemory
from kanamemory.content import ContentRepository
from kanamemory.domain import Question, StudyStore, prepare_session_snapshot
from kanamemory.navigation import PageState


from support.learning_reset_ui_support import CONTENT, reset_app, saved_tables, retained_state


def test_settings_exposes_delete_action_without_deleting_until_confirmed(reset_app):
    app = reset_app
    before = saved_tables(app)
    app.main_window.dialog.return_value = False
    app.show_settings()
    buttons = [node for node in descendants(app.body) if node.text == "删除学习数据"]
    assert len(buttons) == 1 and callable(buttons[0].on_press)
    assert saved_tables(app) == before
    retained = retained_state(app)
    asyncio.run(buttons[0].on_press(None))
    app.main_window.dialog.assert_awaited_once()
    assert saved_tables(app) == before and retained_state(app) == retained
    assert app._deleting_learning_data is False
    app.alert.assert_not_called()


@pytest.mark.parametrize("change", ["leave", "token", "leave_and_return"])
def test_confirmation_cannot_delete_after_navigation_or_settings_refresh(reset_app, change):
    app = reset_app
    before, retained = saved_tables(app), retained_state(app)

    async def confirm(dialog):
        if change in {"leave", "leave_and_return"}:
            app.page = "home"
        if change in {"token", "leave_and_return"}:
            app._page_token += 1
        if change == "leave_and_return":
            app.page = "settings"
        return True

    app.main_window.dialog.side_effect = confirm
    asyncio.run(app.delete_learning_data())
    assert saved_tables(app) == before and retained_state(app) == retained
    assert app._deleting_learning_data is False
    app.alert.assert_not_called()


def test_repeat_click_while_dialog_pending_has_only_one_confirmation_and_one_reset(reset_app, monkeypatch):
    app = reset_app
    clear = Mock(wraps=app.store.clear_learning_data)
    monkeypatch.setattr(app.store, "clear_learning_data", clear)

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def confirm(dialog):
            entered.set()
            await release.wait()
            return True

        app.main_window.dialog.side_effect = confirm
        first = asyncio.create_task(app.delete_learning_data())
        await entered.wait()
        try:
            await app.delete_learning_data()
            app.main_window.dialog.assert_awaited_once()
            clear.assert_not_called()
        finally:
            release.set()
            await first
        clear.assert_called_once()
        assert app._deleting_learning_data is False

    asyncio.run(exercise())


def test_success_retires_session_views_and_audio_but_retains_preferences_and_resources(reset_app):
    app = reset_app
    preferences = deepcopy(app._practice_preferences)
    stored_preferences = app.store.get_setting("practice_preferences")
    content_metadata = app.repo.metadata()
    backup = app.store.export_backup()
    model_path, backup_path = app.store.data_dir / "model.bin", app.store.data_dir / "saved-backup.json"
    model_path.write_bytes(b"existing model")
    backup_path.write_bytes(backup)
    previous_token, ink_revision, audio_serial = app._page_token, app._ink_revision, app._audio_serial
    asyncio.run(app.delete_learning_data())
    assert app.session_id is None and app.questions == [] and app.question is None
    assert app.question_index == app.session_right == 0
    assert app._assisted_attempts == app._answered_attempts == set()
    assert app.strokes == [] and app._active_stroke is None
    assert app._preview_pending is None and app._practice_context is False
    assert app._practice_start_token is app._practice_start_task is None
    assert not app._starting_practice and app.back_stack.pages == []
    assert app._ink_revision > ink_revision and app._audio_serial > audio_serial
    assert app._page_token > previous_token and app.page == "settings"
    app.platform.stop.assert_called()
    assert app.store.load_session_progress() is None
    assert app.store.stats()["total_reviews"] == 0
    assert app._practice_preferences == preferences
    assert app.store.get_setting("practice_preferences") == stored_preferences
    assert app.repo.metadata() == content_metadata
    assert app.model_state == "ready" and app.platform.tts_ready
    assert model_path.read_bytes() == b"existing model" and backup_path.read_bytes() == backup
    assert "已删除" in app.alert.call_args.args[0]


def test_failed_deletion_keeps_old_database_and_in_memory_attempt(reset_app):
    app = reset_app
    before, retained = saved_tables(app), retained_state(app)
    app.store._db.execute("CREATE TEMP TRIGGER reset_failure BEFORE DELETE ON study_session "
                          "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
    asyncio.run(app.delete_learning_data())
    assert saved_tables(app) == before and retained_state(app) == retained
    assert app._deleting_learning_data is False
    app.platform.stop.assert_not_called()
    assert "未能删除" in app.alert.call_args.args[0]


def test_cancelled_confirmation_preserves_progress_and_releases_busy_guard(reset_app):
    app = reset_app
    before, retained = saved_tables(app), retained_state(app)

    async def exercise():
        entered = asyncio.Event()

        async def confirm(dialog):
            entered.set()
            await asyncio.Event().wait()

        app.main_window.dialog.side_effect = confirm
        task = asyncio.create_task(app.delete_learning_data())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert saved_tables(app) == before and retained_state(app) == retained
    assert app._deleting_learning_data is False


def test_worker_returning_after_reset_cannot_restore_deleted_batch(reset_app, monkeypatch):
    app = reset_app
    old_session = app.session_id
    KanaMemory.show_practice_setup.__wrapped__(app)
    old_question = Question("kana-3042", "kana_choice", "hiragana", "あ", "a", ["a", "i"])

    async def exercise():
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def worker(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                return [old_question], prepare_session_snapshot([old_question], seed=7)

        monkeypatch.setattr(app_module, "build_in_background", worker)
        task = asyncio.create_task(app.start_practice(replace_session_id=old_session))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            app.show_settings()
            await app.delete_learning_data()
            await asyncio.wait_for(cancelled.wait(), 2)
            # A late result remains stale even after returning to a new form.
            KanaMemory.show_practice_setup.__wrapped__(app)
            release.set()
            await task
            assert app.store._db.execute("SELECT COUNT(*) FROM study_session").fetchone()[0] == 0
            assert app.store.load_session_progress() is None
            assert app.session_id is None and app.questions == [] and app.question is None
            app.show_question.assert_not_called()
            assert not app._starting_practice and app.practice_start_button.enabled
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())
