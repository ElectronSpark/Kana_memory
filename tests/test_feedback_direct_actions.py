"""Visible per-record management never consumes the batch selection or learning."""
import asyncio
from unittest.mock import Mock
from support.feedback_ui_support import feedback_app, seed, click
from support.app_fixtures import setup_app
from support.fake_ui import descendants


def test_row_archive_reopen_and_delete_are_direct(feedback_app):
    app = feedback_app
    records = seed(app, 2)
    app.show_feedback_manager()
    other = records[0]['id']
    app.select_feedback(other, True)
    target = app._feedback_page_switches[0][0]
    # Choose a distinct record for the pre-existing cross-page selection.
    other = next(r['id'] for r in records if r['id'] != target)
    app._feedback_selected = {other}
    click(app, '归档')
    assert app.feedback_store().get(target)['status'] == 'archived'
    assert app._feedback_selected == {other}
    assert [i for i, _ in app._feedback_page_switches] == [other]
    app.show_feedback_manager(status='archived')
    click(app, '取消归档')
    assert app.feedback_store().get(target)['status'] == 'open'
    app.show_feedback_detail(target)
    app.main_window.dialog.return_value = False
    click(app, '删除')
    assert app.feedback_store().get(target)
    app.main_window.dialog.return_value = True
    click(app, '删除')
    assert app.feedback_store().get(target) is None
    assert app.feedback_store().get(other)
    assert app.page == 'report_list'


def test_archive_detail_saves_edits_and_preserves_capture(feedback_app):
    app = feedback_app
    record = seed(app)[0]
    app.show_feedback_manager()
    app.show_feedback_detail(record['id'])
    app._report_detail['note'].value = '补充说明'
    click(app, '归档')
    saved = app.feedback_store().get(record['id'])
    assert saved['note'] == '补充说明' and saved['status'] == 'archived'
    assert saved['context'] == record['context']
    app.main_window.dialog.assert_not_awaited()


def test_stale_row_and_pending_delete_cannot_change_another_page(feedback_app):
    app = feedback_app
    record = seed(app)[0]
    app.show_feedback_manager()
    old = next(w for w in descendants(app.body) if w.text == '归档')
    app.show_feedback_manager()
    old.on_press(old)
    assert app.feedback_store().get(record['id'])['status'] == 'open'
    app.show_feedback_detail(record['id'])
    tasks = []
    app.loop.create_task = tasks.append
    click(app, '删除')
    click(app, '删除')
    assert len(tasks) == 1
    app.show_feedback_manager()
    asyncio.run(tasks.pop())
    assert app.feedback_store().get(record['id'])
    assert not app._feedback_delete_pending


def test_archive_failure_keeps_dirty_detail(feedback_app, monkeypatch):
    app = feedback_app
    record = seed(app)[0]
    app.show_feedback_manager()
    app.show_feedback_detail(record['id'])
    app._report_detail['note'].value = '未保存'
    monkeypatch.setattr(app.feedback_store(), 'update', Mock(side_effect=OSError('disk')))
    click(app, '归档')
    assert app.page == 'report_detail' and app._report_detail['note'].value == '未保存'
    assert app.feedback_store().get(record['id'])['status'] == 'open'
