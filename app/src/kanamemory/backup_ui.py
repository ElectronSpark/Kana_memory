"""Exclusive, cancellable backup stages; SAF and confirmation stay on the UI."""
from __future__ import annotations
import asyncio
from datetime import datetime

from .backup_worker import BackupJob, BackupCancelled
from .domain import StudyStore
from .learning_store import LearningStore
from . import progress_backup


def _other_app_work():
    current = asyncio.current_task()
    for task in asyncio.all_tasks():
        if task is current or task.done():
            continue
        awaited = task.get_coro()
        seen = set()
        while awaited is not None and id(awaited) not in seen:
            seen.add(id(awaited))
            frame = getattr(awaited, 'cr_frame', None) or getattr(awaited, 'gi_frame', None)
            if frame is not None and str(frame.f_globals.get('__name__', '')).startswith('kanamemory.'):
                return True
            awaited = getattr(awaited, 'cr_await', None) or getattr(awaited, 'gi_yieldfrom', None)
    return False


def _ready(app):
    if getattr(app, '_backup_closing', False):
        raise BackupCancelled()
    if (any(getattr(app, name, False) for name in ('_ink_busy', '_starting_practice',
             '_model_checking', '_model_downloading', '_ai_request_operation')) or _other_app_work()):
        raise RuntimeError('请等当前的生成、识别或其他操作结束后，再进行备份或恢复；当前操作和输入会保留。')


def request_cancel(app, *, expected_job=None):
    """Request cancellation without releasing the worker's write barrier."""
    job = getattr(app, '_backup_job', None)
    if (not getattr(app, '_backup_busy', False)
            or getattr(app, '_backup_reopen_failed', False) or job is None
            or (expected_job is not None and job is not expected_job)):
        return False
    if not getattr(app, '_backup_cancel_requested', False):
        job.cancel()
        app._backup_cancel_requested = True
        update = getattr(app, '_backup_cancel_notice', None)
        if update is not None:
            update()
    return True


def _restore_summary(summary):
    courses = summary.get('learning_content', 0)
    reviews = summary['reviews'] + summary.get('learning_reviews', 0)
    if courses:
        return f"假名记忆卡 {summary['cards']} 张\n课程内容 {courses} 项\n复习记录 {reviews} 次"
    return f"备份含 {summary['cards']} 张学习卡、{reviews} 次复习。"


async def _stage(app, action, title, data=None):
    from . import ui_components as u
    from .app import KanaMemory
    _ready(app)
    if app._checkpoint() is False:
        raise RuntimeError('当前进度尚未保存，请稍后重试。')
    old = app.store
    data_dir, zone, lane = old.data_dir, None if old._device_local else old.zone, old.practice_lane
    previous = app.main_window.content
    job = BackupJob(action, data_dir, zone, data)
    notice = u.label(title, size=20)
    cancel = u.button('取消', lambda w, **kw: request_cancel(app, expected_job=job))
    def cancelled_notice():
        notice.text = '正在安全取消，请稍候…'
        cancel.enabled = False
    KanaMemory._cancel_review_wait(app)
    app._page_token += 1
    app._backup_busy, app._backup_job = True, job
    app._backup_cancel_requested = False
    app._backup_cancel_notice = cancelled_notice
    app._backup_restore_committed = False
    app.main_window.content = u.col(notice, u.label('记录较多时需要一些时间。', size=14), cancel, padding=24)
    closed = False
    try:
        # This is a hard UI write barrier, in addition to the visible busy
        # layer and navigation/token guards. No connection crosses a thread.
        old.close()
        closed = True
        return await job.run()
    finally:
        # Release retained widget callbacks even when reopening fails. A stale
        # Cancel control also checks its job identity before touching a new job.
        app._backup_cancel_notice = None
        app._backup_cancel_requested = False
        app._backup_restore_committed = job.committed is not None
        if closed:
            reopened = None
            try:
                reopened = StudyStore(data_dir, time_zone=zone)
                reopened.practice_lane = lane
                learning = LearningStore(reopened)
            except Exception:
                if reopened is not None:
                    reopened.close()
                app._backup_reopen_failed = True
                app._backup_job = None
                message = ('学习记录已恢复，但界面无法重新载入，请退出后重新打开应用。'
                           if app._backup_restore_committed else '无法重新打开学习记录，请退出后重新打开应用。')
                app.main_window.content = u.col(u.label(message),
                    u.button('退出应用', lambda w, **kw: app.exit()), padding=24)
                raise
            app.store, app.learning_store = reopened, learning
            app._study_path_service_key = None
            app._study_path_service_value = None
        app._backup_busy, app._backup_job = False, None
        app._page_token += 1
        app.main_window.content = previous
        # Keep the exact retained view and unsaved controls on export, inspect,
        # cancellation and failure. Only a committed import clears old views.
        if getattr(app, 'page', None) == 'home':
            KanaMemory._arm_home_review_refresh(app)


async def export(app, *, previous=False):
    from . import ui_components as u
    from .app import error_message
    if getattr(app, '_backup_request', False):
        return
    app._backup_request = True
    try:
        result = await _stage(app, 'previous' if previous else 'export', '正在准备学习备份…')
        if result.value is None:
            app.alert('还没有恢复前的自动备份。')
            return
        prefix = 'KanaMemory-before-restore' if previous else 'KanaMemory-records'
        if await app.platform.export_backup(result.value, f'{prefix}-{datetime.now():%Y%m%d-%H%M%S}.json'):
            app.alert('学习备份已导出。')
    except BackupCancelled:
        app.alert('已取消备份，学习记录和当前输入已保留。')
    except Exception as error:
        app.alert('无法导出备份：' + error_message(error))
    finally:
        app._backup_request = False


async def convert_legacy(app):
    """Save a converted copy; never restore or replace the active learner data."""
    from . import ui_components as u
    from .app import error_message
    if getattr(app, '_backup_request', False):
        return
    app._backup_request = True
    try:
        _ready(app)
        data = await app.platform.import_backup()
        if data is None:
            return
        if not progress_backup.is_legacy_backup(data):
            # An arbitrary unrecognized file must not be labelled current.
            await _stage(app, 'inspect', '正在检查学习记录…', data)
            app.alert('这个文件已是当前记录格式，可直接恢复。')
            return
        converted = (await _stage(app, 'convert', '正在转换旧版学习记录…', data)).value
        summary = (await _stage(app, 'inspect', '正在核验转换结果…', converted)).value
        filename = f'KanaMemory-records-converted-{datetime.now():%Y%m%d-%H%M%S}.json'
        if await app.platform.export_backup(converted, filename):
            app.alert('已转换并导出。\n' + _restore_summary(summary)
                      + '\n当前学习记录未替换，原文件仍可保留。')
    except BackupCancelled:
        app.alert('已取消转换，当前学习记录和原文件未改动。')
    except Exception as error:
        app.alert('未能转换学习记录：' + error_message(error))
    finally:
        app._backup_request = False


async def restore(app):
    from . import ui_components as u
    from .app import error_message
    if getattr(app, '_backup_request', False):
        return
    app._backup_request = True
    # This flag describes this request, including failures before a worker
    # starts. A previous successful restore must not label a picker error as
    # another committed replacement.
    app._backup_restore_committed = False
    try:
        _ready(app)
        data = await app.platform.import_backup()
        if data is None:
            return
        converted = progress_backup.is_legacy_backup(data)
        if converted:
            data = (await _stage(app, 'convert', '正在转换旧版学习记录…', data)).value
        summary = (await _stage(app, 'inspect', '正在检查学习备份…', data)).value
        confirmed = await app.main_window.dialog(u.toga.ConfirmDialog('恢复学习备份',
            ('旧格式已转换。\n' if converted else '') + _restore_summary(summary)
            + '\n恢复会替换当前学习记录和设置，并先在本机保留恢复前的备份。'))
        if not confirmed:
            return
        result = await _stage(app, 'restore', '正在恢复学习记录…', data)
        if not result.committed:
            raise RuntimeError('学习记录未替换。')
        app.session_id, app.questions, app.question = None, [], None
        app.question_index = app.session_right = 0
        app._assisted_attempts.clear()
        app._answered_attempts.clear()
        app.back_stack.pages.clear()
        app._practice_preferences = app._clean_preferences(app.store.get_setting('practice_preferences', {}))
        app.show_home()
        app.back_stack.pages.clear()
        app.alert('学习记录已恢复。未完成的练习可从首页继续。')
    except BackupCancelled:
        app.alert('已取消恢复，原学习记录和当前输入已保留。')
    except Exception as error:
        app.alert(('学习记录已恢复，请重新打开应用：' if getattr(app, '_backup_restore_committed', False)
                   else '未能恢复备份：') + error_message(error))
    finally:
        app._backup_request = False
