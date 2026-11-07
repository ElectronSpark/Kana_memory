from record_test_support import decode_current, encode_current
"""Real thread-owned DB, exclusive UI stage, cancellation and retained drafts."""
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from kanamemory import backup_worker, backup_ui, progress_backup
from kanamemory.backup_worker import BackupJob, BackupCancelled
from kanamemory.domain import StudyStore, Question
from kanamemory.learning_store import LearningStore
from kanamemory.navigation import page_view
from support.app_fixtures import setup_app, app_module
from support.fake_ui import View


from support.backup_background_candidate_support import backup, tables, app


def test_worker_constructs_uses_and_closes_own_connection(tmp_path, monkeypatch):
    ui_thread, threads = get_ident(), []
    class Tracked(StudyStore):
        def __init__(self, *args, **kw): threads.append(get_ident()); super().__init__(*args, **kw)
        def export_backup(self): threads.append(get_ident()); return super().export_backup()
        def close(self): threads.append(get_ident()); super().close()
    monkeypatch.setattr(backup_worker, 'StudyStore', Tracked)
    result = asyncio.run(BackupJob('export', tmp_path/'live', 'UTC').run())
    assert result.value and not result.committed
    assert len(threads) == 3 and len(set(threads)) == 1 and threads[0] != ui_thread


def test_cancelled_await_waits_for_worker_to_close(tmp_path, monkeypatch):
    entered, release, closed = Event(), Event(), Event()
    class Slow(StudyStore):
        def export_backup(self):
            entered.set(); assert release.wait(5)
            return super().export_backup()
        def close(self): super().close(); closed.set()
    monkeypatch.setattr(backup_worker, 'StudyStore', Slow)
    async def run():
        task = asyncio.create_task(BackupJob('export', tmp_path/'live', 'UTC').run())
        while not entered.is_set(): await asyncio.sleep(.001)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done() and not closed.is_set()
        release.set()
        with pytest.raises(BackupCancelled): await task
        assert closed.is_set()
    asyncio.run(run())


@pytest.mark.parametrize('phase', ['before', 'after_file', 'during_insert', 'after_commit'])
def test_restore_cancellation_is_atomic_or_reports_real_commit(tmp_path, monkeypatch, phase):
    data = backup(tmp_path)
    with StudyStore(tmp_path/'live') as store:
        LearningStore(store)
        store.set_setting('original', 'keep')
        before = tables(store)
    job = BackupJob('restore', tmp_path/'live', 'UTC', data)
    if phase == 'before': job.cancel()
    elif phase == 'after_file':
        write = progress_backup._write_recovery_file
        def cancel(path, data): write(path, data); job.cancel()
        monkeypatch.setattr(progress_backup, '_write_recovery_file', cancel)
    elif phase == 'during_insert':
        calls = 0
        check = job.check
        def cancel():
            nonlocal calls
            calls += 1
            if calls == 7: job.cancel()
            check()
        job.check = cancel
    else:
        committed = job._did_commit
        def after(value): committed(value); job.cancel()
        job._did_commit = after
    if phase == 'after_commit':
        result = asyncio.run(job.run())
        assert result.committed
    else:
        with pytest.raises(BackupCancelled): asyncio.run(job.run())
    with StudyStore(tmp_path/'live') as store:
        assert (store.get_setting('replacement') is True) if phase == 'after_commit' else tables(store) == before


def test_export_preserves_exact_view_draft_and_options_after_reopen(app):
    before, view, original = tables(app.store), app.main_window.content, app.store
    options, question, sid = app.questions, app.question, app.session_id
    asyncio.run(app_module.KanaMemory.export_learning_backup(app))
    assert app.store is not original and tables(app.store) == before
    assert app.main_window.content is view and app.questions is options and app.question is question
    assert app.session_id == sid and app.store.load_session_progress()['state']['romaji_answer'] == 'a'
    assert app._backup_busy is False and app._backup_request is False
    app.platform.export_backup.assert_awaited_once()


def test_mutation_and_navigation_blocked_while_ui_loop_remains_live(app, monkeypatch):
    entered, release = Event(), Event()
    original = backup_worker.StudyStore.export_backup
    def slow(store): entered.set(); assert release.wait(5); return original(store)
    monkeypatch.setattr(backup_worker.StudyStore, 'export_backup', slow)
    old = app.store
    visited = []
    @page_view()
    def navigate(self): visited.append(True)
    async def run():
        task = asyncio.create_task(backup_ui.export(app))
        while not entered.is_set(): await asyncio.sleep(.001)
        assert app._backup_busy
        with pytest.raises(sqlite3.ProgrammingError): old.set_setting('stale_callback', True)
        assert app_module.KanaMemory._checkpoint(app) is False
        assert app_module.KanaMemory.go_back(app) is True
        navigate(app)
        assert not visited
        await backup_ui.export(app)
        assert not task.done()
        release.set()
        await task
        assert app.store.get_setting('stale_callback') is None
    asyncio.run(run())
    app.platform.export_backup.assert_not_awaited()
    assert '已取消' in str(app.alert.call_args)


@pytest.mark.parametrize('problem', ['cancel', 'error'])
def test_export_cancellation_or_failure_reopens_without_losing_view(app, monkeypatch, problem):
    before, view = tables(app.store), app.main_window.content
    original = backup_worker.BackupJob._run
    def action(job):
        if problem == 'cancel': job.cancel(); return original(job)
        raise OSError('private fixture error')
    monkeypatch.setattr(backup_worker.BackupJob, '_run', action)
    asyncio.run(backup_ui.export(app))
    assert tables(app.store) == before and app.main_window.content is view
    assert not app._backup_busy and not app._backup_request
    app.platform.export_backup.assert_not_awaited()


@pytest.mark.parametrize('confirmed', [False, True])
def test_restore_keeps_confirmation_and_reloads_only_after_commit(app, confirmed):
    before, view, original = tables(app.store), app.main_window.content, app.store
    app.main_window.dialog.return_value = confirmed
    asyncio.run(app_module.KanaMemory.import_learning_backup(app))
    app.main_window.dialog.assert_awaited_once()
    assert app.store is not original and not app._backup_busy
    if confirmed:
        assert app.store.get_setting('replacement') is True
        assert app.session_id is None and app.questions == []
        app.show_home.assert_called_once()
        assert app.store.previous_backup()
    else:
        assert tables(app.store) == before and app.main_window.content is view
        assert app.store.load_session_progress()['state'] == {'romaji_answer': 'a'}
        app.show_home.assert_not_called()


def test_invalid_import_keeps_draft_without_confirmation(app):
    app.platform.import_backup.return_value = b'not json'
    before, view = tables(app.store), app.main_window.content
    asyncio.run(backup_ui.restore(app))
    assert tables(app.store) == before and app.main_window.content is view
    app.main_window.dialog.assert_not_awaited()


@pytest.mark.parametrize('flag', ['_ink_busy', '_starting_practice', '_ai_request_operation', '_model_downloading'])
def test_active_user_operation_is_not_cancelled_or_discarded(app, flag):
    marker = object()
    setattr(app, flag, marker)
    old = app.store
    asyncio.run(backup_ui.export(app))
    assert app.store is old and getattr(app, flag) is marker
    assert '结束后' in str(app.alert.call_args)
    app.platform.export_backup.assert_not_awaited()


def test_untracked_active_app_coroutine_prevents_closing_connection(app):
    namespace = {'__name__':'kanamemory.fake_pending', 'asyncio':asyncio}
    exec('async def waiting(event):\n    await event.wait()', namespace)
    async def run():
        event = asyncio.Event()
        task = asyncio.create_task(namespace['waiting'](event))
        await asyncio.sleep(0)
        old = app.store
        await backup_ui.export(app)
        assert app.store is old and not task.cancelled()
        event.set(); await task
    asyncio.run(run())


def test_busy_exit_requests_safe_cancel_instead_of_closing_worker_connection(app):
    app._backup_busy = True
    app._backup_job = Mock()
    assert app_module.KanaMemory.on_exit(app) is False
    app._backup_job.cancel.assert_called_once()
    assert app.store.get_setting('original') == 'keep'


def test_reopen_failure_keeps_actions_blocked_with_restart_instruction(app, monkeypatch):
    monkeypatch.setattr(backup_ui, 'StudyStore', Mock(side_effect=OSError('reopen failed')))
    asyncio.run(backup_ui.export(app))
    assert app._backup_reopen_failed and app._backup_busy
    assert '重新打开' in app.main_window.content.children[0].text
    app.platform.export_backup.assert_not_awaited()


def test_committed_restore_with_ui_reopen_failure_never_claims_rollback(app, monkeypatch):
    factory = backup_ui.StudyStore
    directory = app.store.data_dir
    opens = 0
    def opening(*args, **kwargs):
        nonlocal opens
        opens += 1
        if opens == 2:
            raise OSError('UI reopen failed after commit')
        return factory(*args, **kwargs)
    monkeypatch.setattr(backup_ui, 'StudyStore', opening)
    asyncio.run(backup_ui.restore(app))
    assert app._backup_restore_committed and app._backup_reopen_failed
    assert '已恢复' in str(app.alert.call_args)
    with StudyStore(directory) as check:
        assert check.get_setting('replacement') is True
        assert check.previous_backup()


def test_restore_sql_error_reopens_original_view_and_draft(app, monkeypatch):
    before, view = tables(app.store), app.main_window.content
    def fail(path, value): raise OSError('private recovery write failure')
    monkeypatch.setattr(progress_backup, '_write_recovery_file', fail)
    asyncio.run(backup_ui.restore(app))
    assert tables(app.store) == before and app.main_window.content is view
    assert not app._backup_restore_committed and not app._backup_busy
    app.main_window.dialog.assert_awaited_once()
    app.show_home.assert_not_called()


def test_cancel_restore_after_confirmation_preserves_original_and_reopens(app, monkeypatch):
    before, view = tables(app.store), app.main_window.content
    original = progress_backup._write_recovery_file
    def cancel(path, value):
        original(path, value)
        app._backup_job.cancel()
    monkeypatch.setattr(progress_backup, '_write_recovery_file', cancel)
    asyncio.run(backup_ui.restore(app))
    assert tables(app.store) == before and app.main_window.content is view
    assert not app._backup_restore_committed and not app._backup_busy
    app.show_home.assert_not_called()
    assert '已取消' in str(app.alert.call_args)


def test_confirmation_binds_exact_previously_inspected_bytes(app):
    async def confirm(_):
        app.platform.import_backup.return_value = b'changed external file'
        return True
    app.main_window.dialog.side_effect = confirm
    asyncio.run(backup_ui.restore(app))
    app.platform.import_backup.assert_awaited_once()
    assert app.store.get_setting('replacement') is True
