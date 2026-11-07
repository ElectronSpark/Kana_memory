"""Feedback list freshness, explicit export scope and thread-owned delivery work."""
import asyncio
from datetime import timedelta, timezone
from io import BytesIO
import json
from threading import Event, get_ident
from types import MethodType
from unittest.mock import Mock
from uuid import uuid4
import zipfile

import pytest

from kanamemory import feedback_lifecycle, feedback_ui, personal_content_refresh
from kanamemory.app import KanaMemory
from kanamemory.domain import StudyStore
from kanamemory.feedback_context import MAX_CONTAINER_ITEMS, capture_feedback_context
from kanamemory.feedback_lifecycle import FeedbackLifecycleMixin, REPORT_BINDINGS
from kanamemory.feedback_store import FeedbackStore
from kanamemory.navigation import PageState
from support.app_fixtures import setup_app
from support.fake_ui import View, descendants, text_of
from support.feedback_context_support import fake_app
from support.feedback_ui_support import capture, feedback_app, seed


def packet_ids(payload):
    with zipfile.ZipFile(BytesIO(payload)) as packet:
        return {record['id'] for record in json.loads(packet.read('feedback.json'))['records']}


async def wait_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(wait(), 5)


async def export_and_wait(app, **kwargs):
    app.loop = asyncio.get_running_loop()
    app.export_feedback_records(**kwargs)
    await wait_until(lambda: not app._feedback_export_pending)


def attach_list_return(app, monkeypatch):
    app._view_key = ('show_feedback_manager', None)
    previous = PageState(app._view_key, app.page, app.title, app.body,
        {name: getattr(app, name) for name in REPORT_BINDINGS['report_list'] if hasattr(app, name)})
    app._capture_page = lambda: previous
    app._feedback_return_stack = []
    app._audio_serial = app._ink_revision = 0
    app._question_started = None
    app._checkpoint = Mock()
    app._sync_back_handler = Mock()
    app._review_wait_handle = None
    app.content_slot = View(app.body)
    app.title_label = View(text=app.title)
    app.loop.call_soon = lambda fn, *args: fn(*args)
    for name in ('begin_page_feedback', 'return_from_feedback', '_restore_feedback_page'):
        setattr(app, name, MethodType(getattr(FeedbackLifecycleMixin, name), app))
    monkeypatch.setattr(feedback_lifecycle, 'capture_feedback_context', lambda *args, **kwargs: capture())
    monkeypatch.setattr(KanaMemory, '_update_chrome', lambda self: None)
    monkeypatch.setattr(personal_content_refresh, 'refresh_return', lambda *args: False)


def test_saved_feedback_refreshes_list_and_preserves_unapplied_controls(feedback_app, monkeypatch):
    app = feedback_app
    original = seed(app)[0]
    app.show_feedback_manager(status='open')
    app.select_feedback(original['id'], True)
    app._feedback_manager_controls['query'].value = 'not yet applied'
    app._feedback_manager_controls['status'].value = '已归档'
    app._feedback_export_controls['format'].value = '复制到剪贴板'
    app._feedback_export_controls['archive'].value = False
    app._feedback_export_controls['archive'].on_change(app._feedback_export_controls['archive'])
    attach_list_return(app, monkeypatch)
    app.begin_page_feedback()
    app._report_editor['note'].value = 'Synthetic report about the feedback list'
    app.save_page_feedback()
    assert app.page == 'report_list'
    assert app.feedback_store().count_records() == 2
    assert '共 2 条' in text_of(app.body)
    assert len(app._feedback_page_switches) == 2
    assert app._feedback_selected == {original['id']}
    assert app._feedback_manager_state == {'status': 'open', 'query': '', 'offset': 0}
    assert app._feedback_manager_controls['query'].value == 'not yet applied'
    assert app._feedback_manager_controls['status'].value == '已归档'
    assert app._feedback_export_settings() == {'format': 'text', 'archive': False}
    app.select_feedback_page()
    assert len(app._feedback_selected) == 2


def test_list_and_selection_do_not_read_full_context_or_image_blobs(feedback_app):
    app = feedback_app
    records = seed(app, 14)
    statements = []
    app.feedback_store()._db.set_trace_callback(statements.append)
    app.show_feedback_manager()
    assert len(statements) == 2
    assert all('context_json' not in sql and 'SELECT *' not in sql for sql in statements)
    statements.clear()
    app.select_feedback_page()
    assert len(app._feedback_selected) == 12
    app.clear_feedback_selection()
    app.select_feedback(records[0]['id'], True)
    assert statements == []


def test_all_filtered_and_selected_exports_have_distinct_explicit_scopes(feedback_app):
    app = feedback_app
    records = seed(app, 14)
    visible, hidden = records[13], records[0]
    app.show_feedback_manager(query='说明 13')
    app.select_feedback(hidden['id'], True)
    app._feedback_export_controls['archive'].value = False
    assert len(app._feedback_page_switches) == 1
    assert any('筛选' in str(item.text) and getattr(item, 'on_press', None)
               for item in descendants(app.body))
    assert '1 条不在本页' in app._feedback_selection_label.text
    asyncio.run(export_and_wait(app, filtered=True))
    assert packet_ids(app.platform.export_feedback.call_args.args[0]) == {visible['id']}
    asyncio.run(export_and_wait(app, selected=True))
    assert packet_ids(app.platform.export_feedback.call_args.args[0]) == {hidden['id']}
    asyncio.run(export_and_wait(app))
    assert packet_ids(app.platform.export_feedback.call_args.args[0]) == {r['id'] for r in records}
    assert app._feedback_selected == {hidden['id']}
    assert app.feedback_store().count_records(status='archived') == 0


def test_local_feedback_time_preserves_original_utc_and_converts_date_boundary():
    stamp = '2026-11-03T03:39:00+00:00'
    displayed = feedback_ui.local_feedback_time(stamp, zone=timezone(timedelta(hours=-5)))
    assert '2026-11-02' in displayed and '22:39' in displayed
    assert stamp == '2026-11-03T03:39:00+00:00'


def test_manager_capture_distinguishes_applied_draft_selected_and_export_state():
    selected, visible = str(uuid4()), str(uuid4())
    app = fake_app(page='report_list', _view_key=('show_feedback_manager', None),
        _feedback_manager_state={'status': 'open', 'query': 'applied', 'offset': 12},
        _feedback_manager_controls={'status': View(value='已归档'), 'query': View(value='draft')},
        _feedback_selected={selected}, _feedback_page_switches=[(visible, View(value=False))],
        _feedback_export_controls={'format': View(value='ZIP 文件（含截图）'), 'archive': View(value=False)},
        _feedback_export_pending=True,
        _feedback_export_state={'stage': 'preparing', 'timings_ms': {'prepare': 25}})
    app._feedback_export_settings = MethodType(feedback_ui.FeedbackUIMixin._feedback_export_settings, app)
    captured = capture_feedback_context(app)
    state = captured['feedback_manager']
    assert state['applied_filter'] == {'status': 'open', 'query': 'applied', 'offset': 12}
    assert state['draft_filter']['query'] == 'draft'
    assert state['draft_filter']['status'] in {'已归档', 'archived'}
    assert state['selected_ids'] == [selected] and state['visible_ids'] == [visible]
    assert state['export']['format'] == 'zip' and state['export']['archive'] is False
    assert state['export']['pending'] is True and state['export']['stage'] == 'preparing'
    assert state['export']['timings_ms'] == {'prepare': 25}
    app._feedback_selected.clear()
    app._feedback_manager_state['query'] = 'later'
    assert state['selected_ids'] == [selected] and state['applied_filter']['query'] == 'applied'
    assert 'api_key' not in app.store.reads


def test_manager_capture_reports_actual_selection_truncation_and_total():
    selected = {str(uuid4()) for _ in range(MAX_CONTAINER_ITEMS + 1)}
    app = fake_app(page='report_list', _feedback_selected=selected,
        _feedback_manager_state={}, _feedback_manager_controls={}, _feedback_page_switches=[],
        _feedback_export_controls={'format': View(value='ZIP 文件（含截图）'), 'archive': View(value=False)})
    app._feedback_export_settings = MethodType(feedback_ui.FeedbackUIMixin._feedback_export_settings, app)
    state = capture_feedback_context(app)['feedback_manager']
    assert state['selected_count'] == MAX_CONTAINER_ITEMS + 1
    assert len(state['selected_ids']) == MAX_CONTAINER_ITEMS
    assert state['selected_ids'] == sorted(selected)[:MAX_CONTAINER_ITEMS]
    assert state['truncated'] is True
    assert len(selected) == MAX_CONTAINER_ITEMS + 1


def test_worker_connections_are_created_used_and_closed_on_worker_thread(tmp_path, monkeypatch):
    from kanamemory import feedback_export_worker as worker
    path, data_dir = tmp_path/'feedback.sqlite3', tmp_path/'study'
    with FeedbackStore(path) as feedback:
        saved = feedback.create('Synthetic export', capture())
    with StudyStore(data_dir, time_zone='UTC') as study:
        study.set_setting('preserved', {'value': 1})
    ui_thread, actions = get_ident(), []
    class TrackedFeedback(FeedbackStore):
        def __init__(self, *args, **kwargs):
            actions.append(('feedback-open', get_ident()))
            super().__init__(*args, **kwargs)
        def prepare_export(self, *args, **kwargs):
            actions.append(('prepare', get_ident()))
            return super().prepare_export(*args, **kwargs)
        def close(self):
            actions.append(('feedback-close', get_ident()))
            super().close()
    class TrackedStudy(StudyStore):
        def __init__(self, *args, **kwargs):
            actions.append(('study-open', get_ident()))
            super().__init__(*args, **kwargs)
        def close(self):
            actions.append(('study-close', get_ident()))
            super().close()
    monkeypatch.setattr(worker, 'FeedbackStore', TrackedFeedback)
    from kanamemory import domain
    monkeypatch.setattr(domain, 'StudyStore', TrackedStudy)
    requested = [saved['id']]
    job = worker.FeedbackExportJob(path, data_dir=data_dir, zone='UTC', identities=requested)
    requested.clear()
    result = asyncio.run(job.run())
    assert packet_ids(result.prepared.payload) == {saved['id']}
    assert result.content_stamp is not None
    assert result.timings_ms and all(value >= 0 for value in result.timings_ms.values())
    assert {'feedback-open', 'prepare', 'feedback-close', 'study-open', 'study-close'} <= {a for a, _ in actions}
    assert len({thread for _, thread in actions}) == 1 and actions[0][1] != ui_thread
    with StudyStore(data_dir, time_zone='UTC') as study:
        assert study.get_setting('preserved') == {'value': 1}


def test_cancelling_worker_waiter_waits_for_connection_close(tmp_path, monkeypatch):
    from kanamemory import feedback_export_worker as worker
    entered, release, closed = Event(), Event(), Event()
    path = tmp_path/'feedback.sqlite3'
    with FeedbackStore(path) as feedback:
        feedback.create('Synthetic export', capture())
    class SlowFeedback(FeedbackStore):
        def prepare_export(self, *args, **kwargs):
            entered.set()
            assert release.wait(5)
            return super().prepare_export(*args, **kwargs)
        def close(self):
            super().close()
            closed.set()
    monkeypatch.setattr(worker, 'FeedbackStore', SlowFeedback)
    async def run():
        task = asyncio.create_task(worker.FeedbackExportJob(path).run())
        try:
            await wait_until(entered.is_set)
            task.cancel()
            await asyncio.sleep(.02)
            assert not task.done() and not closed.is_set()
        finally:
            release.set()
        with pytest.raises(worker.FeedbackExportCancelled):
            await task
        assert closed.is_set()
    asyncio.run(run())
    with FeedbackStore(path) as feedback:
        assert feedback.count_records(status='open') == 1


def test_worker_failure_closes_connection_without_archiving(tmp_path, monkeypatch):
    from kanamemory import feedback_export_worker as worker
    path, closed = tmp_path/'feedback.sqlite3', Event()
    with FeedbackStore(path) as feedback:
        feedback.create('Synthetic export', capture())
    class FailedFeedback(FeedbackStore):
        def prepare_export(self, *args, **kwargs):
            raise OSError('Synthetic read failure')
        def close(self):
            super().close()
            closed.set()
    monkeypatch.setattr(worker, 'FeedbackStore', FailedFeedback)
    with pytest.raises(OSError, match='Synthetic read failure'):
        asyncio.run(worker.FeedbackExportJob(path).run())
    assert closed.is_set()
    with FeedbackStore(path) as feedback:
        assert feedback.count_records(status='open') == 1


def test_worker_archive_uses_delivered_snapshot_receipt_and_retains_later_edits(tmp_path):
    from kanamemory import feedback_export_worker as worker
    path = tmp_path/'feedback.sqlite3'
    with FeedbackStore(path) as feedback:
        unchanged = feedback.create('Unchanged synthetic record', capture())
        edited = feedback.create('Edited synthetic record', capture())
    job = worker.FeedbackExportJob(path)
    result = asyncio.run(job.run())
    with FeedbackStore(path) as feedback:
        feedback.update(edited['id'], note='Updated after export snapshot')
        added = feedback.create('Added after export snapshot', capture())
    outcome = asyncio.run(job.archive(result.prepared.receipt, result.content_stamp))
    assert outcome['archived_ids'] == [unchanged['id']] and outcome['changed'] == 1
    with FeedbackStore(path) as feedback:
        assert feedback.get(unchanged['id'])['status'] == 'archived'
        assert feedback.get(edited['id'])['status'] == feedback.get(added['id'])['status'] == 'open'


@pytest.mark.parametrize('abandon', ['cancel', 'navigate'])
def test_preparing_ui_remains_responsive_and_abandoned_results_are_not_delivered(feedback_app, monkeypatch, abandon):
    from kanamemory import feedback_export_worker as worker
    app = feedback_app
    saved = seed(app)[0]
    app.show_feedback_manager()
    app.select_feedback(saved['id'], True)
    entered, release = Event(), Event()
    calls = []
    class SlowFeedback(FeedbackStore):
        def prepare_export(self, *args, **kwargs):
            calls.append(get_ident())
            entered.set()
            assert release.wait(5)
            return super().prepare_export(*args, **kwargs)
    monkeypatch.setattr(worker, 'FeedbackStore', SlowFeedback)
    async def run():
        app.loop = asyncio.get_running_loop()
        app.export_feedback_records(selected=True)
        try:
            await wait_until(entered.is_set)
            assert app._feedback_export_pending
            assert app._feedback_export_state['stage'] == 'preparing'
            app.export_feedback_records(selected=True)
            assert len(calls) == 1
            if abandon == 'cancel':
                control = next(item for item in descendants(app.body) if item.text == '取消准备')
                control.on_press(control)
            else:
                app.render_feedback_editor(capture())
                app._report_editor['note'].value = 'Retained newer draft'
        finally:
            release.set()
        await wait_until(lambda: not app._feedback_export_pending)
    asyncio.run(run())
    app.platform.export_feedback.assert_not_awaited()
    app.platform.copy_text.assert_not_awaited()
    assert app.feedback_store().get(saved['id']) == saved
    if abandon == 'navigate':
        assert app.page == 'report_editor'
        assert app._report_editor['note'].value == 'Retained newer draft'
    else:
        assert '已取消' in app._feedback_export_status.text


@pytest.mark.parametrize('delivery', ['cancelled', 'failed'])
def test_unsuccessful_platform_delivery_keeps_status_selection_and_clears_busy(feedback_app, delivery):
    app = feedback_app
    saved = seed(app)[0]
    app.show_feedback_manager()
    app.select_feedback(saved['id'], True)
    if delivery == 'failed':
        app.platform.export_feedback.side_effect = OSError('Synthetic target failure')
    else:
        app.platform.export_feedback.return_value = False
    asyncio.run(export_and_wait(app, selected=True))
    assert app.feedback_store().get(saved['id']) == saved
    assert app._feedback_selected == {saved['id']} and app._feedback_export_pending is False


def test_successful_archive_updates_rebuilt_visible_status_and_actions(feedback_app):
    app = feedback_app
    saved = seed(app)[0]
    app.show_feedback_manager()
    old_status, old_actions = app._feedback_export_status, app._feedback_export_actions
    app.select_feedback(saved['id'], True)
    asyncio.run(export_and_wait(app, selected=True))
    visible = list(descendants(app.body))
    assert app._feedback_export_status is not old_status
    assert app._feedback_export_actions is not old_actions
    assert app._feedback_export_status in visible and app._feedback_export_actions in visible
    assert '已导出 1 条' in app._feedback_export_status.text
    assert '已归档 1 条' in app._feedback_export_status.text
    assert app._feedback_export_actions.children == []
    assert app.alert.call_args.args[0] == app._feedback_export_status.text


def test_stale_prepare_cancel_callback_cannot_cancel_delivered_export_archive(feedback_app):
    app = feedback_app
    saved = seed(app)[0]
    app.show_feedback_manager()
    app.select_feedback(saved['id'], True)
    captured = {}
    async def deliver(payload, filename):
        assert app._feedback_export_state['stage'] == 'delivery'
        assert captured['cancel'] not in list(descendants(app.body))
        captured['cancel'].on_press(captured['cancel'])
        assert app._feedback_export_job.cancelled.is_set() is False
        return True
    app.platform.export_feedback.side_effect = deliver
    async def run():
        app.loop = asyncio.get_running_loop()
        app.export_feedback_records(selected=True)
        captured['cancel'] = next(item for item in descendants(app.body) if item.text == '取消准备')
        assert app._feedback_export_state['stage'] == 'preparing'
        await wait_until(lambda: not app._feedback_export_pending)
    asyncio.run(run())
    app.platform.export_feedback.assert_awaited_once()
    assert app.feedback_store().get(saved['id'])['status'] == 'archived'
    assert app._feedback_selected == set()
    assert '已归档 1 条' in app._feedback_export_status.text


@pytest.mark.parametrize('outcome', ['content_changed', 'archive_failed'])
def test_archive_warning_cannot_interrupt_page_opened_after_delivery(feedback_app, monkeypatch, outcome):
    from kanamemory import feedback_export_worker as worker
    app = feedback_app
    saved = seed(app)[0]
    app.show_feedback_manager()
    app.select_feedback(saved['id'], True)
    async def interrupted_archive(job, receipt, stamp):
        app.render_feedback_editor(capture())
        app._report_editor['note'].value = 'Newer unsaved feedback'
        if outcome == 'archive_failed':
            raise OSError('Synthetic archive failure')
        return None
    monkeypatch.setattr(worker.FeedbackExportJob, 'archive', interrupted_archive)
    asyncio.run(export_and_wait(app, selected=True))
    app.platform.export_feedback.assert_awaited_once()
    app.alert.assert_not_called()
    assert app.page == 'report_editor'
    assert app._report_editor['note'].value == 'Newer unsaved feedback'
    assert app.feedback_store().get(saved['id']) == saved


def test_clipboard_overflow_offers_zip_without_copying_or_archiving(feedback_app):
    app = feedback_app
    context = capture()
    context['synthetic_text'] = 'large ' * 60000
    saved = app.feedback_store().create('Synthetic oversized record', context)
    app.show_feedback_manager()
    app.select_feedback(saved['id'], True)
    app._feedback_export_controls['format'].value = '复制到剪贴板'
    asyncio.run(export_and_wait(app, selected=True))
    app.platform.copy_text.assert_not_awaited()
    assert app.feedback_store().get(saved['id']) == saved
    fallback = [item for item in descendants(app.body)
                if 'ZIP' in str(item.text) and getattr(item, 'on_press', None)]
    assert fallback, 'Clipboard overflow must offer an actionable ZIP fallback'
    async def retry():
        app.loop = asyncio.get_running_loop()
        fallback[0].on_press(fallback[0])
        assert app._feedback_export_controls['format'].value == 'ZIP 文件（含截图）'
        assert app._feedback_export_settings()['format'] == 'zip'
        assert app._feedback_export_job.format == 'zip'
        await wait_until(lambda: not app._feedback_export_pending)
    asyncio.run(retry())
    assert packet_ids(app.platform.export_feedback.call_args.args[0]) == {saved['id']}
