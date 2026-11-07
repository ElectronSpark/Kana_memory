"""System Back shares the visible safe-cancel action; no Android is accessed."""
import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from support.backup_background_candidate_support import app
from support.app_fixtures import setup_app
from support.backup_background_candidate_support import tables
from support.app_fixtures import app_module
from kanamemory import backup_ui, backup_worker


async def entered_worker(event):
    async def wait():
        while not event.is_set():
            await asyncio.sleep(.001)
    await asyncio.wait_for(wait(), 3)


@pytest.mark.parametrize('trigger', ['back', 'button', 'exit'])
def test_shared_cancel_updates_controls_and_waits_for_close(app, monkeypatch, trigger):
    before, view = tables(app.store), app.main_window.content
    entered, release, closed = Event(), Event(), Event()
    original = backup_worker.StudyStore
    class Slow(original):
        def export_backup(self):
            entered.set()
            assert release.wait(5)
            return super().export_backup()
        def close(self):
            super().close()
            closed.set()
    monkeypatch.setattr(backup_worker, 'StudyStore', Slow)
    async def run():
        task = asyncio.create_task(backup_ui.export(app))
        await entered_worker(entered)
        notice, _, cancel = app.main_window.content.children
        job = app._backup_job
        original_cancel = job.cancel
        job.cancel = Mock(wraps=original_cancel)
        try:
            if trigger == 'button':
                assert cancel.on_press(None) is True
            elif trigger == 'back':
                assert app_module.KanaMemory.go_back(app) is True
            else:
                assert app_module.KanaMemory.on_exit(app) is False
            assert notice.text == '正在安全取消，请稍候…'
            assert cancel.enabled is False
            assert job.cancelled.is_set()
            # Repeated physical Back and even a stale direct button call are
            # idempotent; neither releases the active worker's write barrier.
            assert app_module.KanaMemory.go_back(app) is True
            assert cancel.on_press(None) is True
            job.cancel.assert_called_once()
            await asyncio.sleep(.01)
            assert app._backup_busy and not task.done() and not closed.is_set()
        finally:
            release.set()
            await task
        assert closed.is_set()
        assert not app._backup_busy and app._backup_job is None
        assert app._backup_cancel_notice is None
        assert app._backup_cancel_requested is False
        assert cancel.on_press(None) is False
    asyncio.run(run())
    app.platform.export_backup.assert_not_awaited()
    assert '已取消' in app.alert.call_args.args[0]
    assert tables(app.store) == before and app.main_window.content is view
    # The exact retained page can start a fresh successful export.
    monkeypatch.setattr(backup_worker, 'StudyStore', original)
    asyncio.run(backup_ui.export(app))
    app.platform.export_backup.assert_awaited_once()


def test_cancel_after_restore_commit_reports_success_and_reopens(app, monkeypatch):
    entered, release = Event(), Event()
    original = backup_worker.BackupJob._did_commit
    def after_commit(job, result):
        original(job, result)
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(backup_worker.BackupJob, '_did_commit', after_commit)
    async def run():
        task = asyncio.create_task(backup_ui.restore(app))
        await entered_worker(entered)
        try:
            assert app_module.KanaMemory.go_back(app) is True
            assert app.main_window.content.children[0].text == '正在安全取消，请稍候…'
            assert app._backup_busy and not task.done()
        finally:
            release.set()
            await task
    asyncio.run(run())
    assert app._backup_restore_committed
    assert app.store.get_setting('replacement') is True
    assert app.store.previous_backup()
    assert app._backup_cancel_notice is None and not app._backup_busy
    assert '已恢复' in app.alert.call_args.args[0]
    assert all('取消恢复' not in call.args[0] and '未能恢复' not in call.args[0]
               for call in app.alert.call_args_list)
    app.show_home.assert_called_once()


@pytest.mark.parametrize('state', ['idle', 'missing_job', 'reopen_failed'])
def test_unavailable_cancel_does_not_touch_ui_or_job(state):
    job, notice = Mock(), Mock()
    app = SimpleNamespace(_backup_busy=state != 'idle', _backup_job=job,
                          _backup_reopen_failed=state == 'reopen_failed',
                          _backup_cancel_notice=notice)
    if state == 'missing_job':
        app._backup_job = None
    assert backup_ui.request_cancel(app) is False
    job.cancel.assert_not_called()
    notice.assert_not_called()
    if state != 'idle':
        assert app_module.KanaMemory.go_back(app) is True
    if state == 'missing_job':
        assert app_module.KanaMemory.on_exit(app) is False


def test_missing_ui_and_stale_button_identity_are_safe():
    job = Mock()
    app = SimpleNamespace(_backup_busy=True, _backup_job=job)
    assert backup_ui.request_cancel(app, expected_job=Mock()) is False
    job.cancel.assert_not_called()
    assert backup_ui.request_cancel(app) is True
    assert app_module.KanaMemory.go_back(app) is True
    job.cancel.assert_called_once()


def test_reopen_failure_clears_retained_cancel_callback(app, monkeypatch):
    monkeypatch.setattr(backup_ui, 'StudyStore', Mock(side_effect=OSError('reopen failed')))
    asyncio.run(backup_ui.export(app))
    assert app._backup_reopen_failed and app._backup_busy
    assert app._backup_cancel_notice is None and not app._backup_cancel_requested
    assert app._backup_job is None
    assert app_module.KanaMemory.go_back(app) is True


@pytest.mark.parametrize('summary, expected', [
    ({'cards': 372, 'reviews': 28532, 'learning_content': 3365, 'learning_reviews': 195},
     '假名记忆卡 372 张\n课程内容 3365 项\n复习记录 28727 次'),
    ({'cards': 12, 'reviews': 25}, '备份含 12 张学习卡、25 次复习。'),
    ({'cards': 12, 'reviews': 25, 'learning_content': 0, 'learning_reviews': 0},
     '备份含 12 张学习卡、25 次复习。'),
])
def test_actual_restore_confirmation_summarizes_both_learning_domains(app, monkeypatch, summary, expected):
    app.main_window.dialog.return_value = False
    stage = AsyncMock(return_value=backup_worker.BackupResult(summary))
    monkeypatch.setattr(backup_ui, '_stage', stage)
    dialog = Mock(return_value=object())
    monkeypatch.setattr(app_module.toga, 'ConfirmDialog', dialog)
    asyncio.run(backup_ui.restore(app))
    title, message = dialog.call_args.args
    assert title == '恢复学习备份'
    assert message == expected + '\n恢复会替换当前学习记录和设置，并先在本机保留恢复前的备份。'
    assert '已学' not in message
    assert stage.await_count == 1
    app.show_home.assert_not_called()
