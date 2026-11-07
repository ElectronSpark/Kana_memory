"""Independent UI lifecycle probes; no native clipboard or learner records."""
import asyncio

import pytest

from support.feedback_ui_support import click
from support.feedback_ui_support import feedback_app
from support.feedback_ui_support import seed
from support.app_fixtures import setup_app
from support.feedback_clipboard_ui_support import clipboard


def test_old_export_does_not_mutate_new_list_selection_or_widgets(feedback_app):
    app = feedback_app
    first, second = seed(app, 2)
    app.show_feedback_manager()
    clipboard(app)
    app.select_feedback(first["id"], True)
    later = {}

    async def copy(text):
        app.show_feedback_manager(query="说明")
        app.clear_feedback_selection()
        app.select_feedback_page()
        later.update(body=app.body, state=app._feedback_manager_state,
                     selected=set(app._feedback_selected), label=app._feedback_selection_label.text)
        return True

    app.platform.copy_text.side_effect = copy
    click(app, "导出已选")
    assert app.feedback_store().get(first["id"])["status"] == "archived"
    assert app.feedback_store().get(second["id"])["status"] == "open"
    assert app.body is later["body"] and app._feedback_manager_state is later["state"]
    assert app._feedback_selected == later["selected"]
    assert app._feedback_selection_label.text == later["label"]
    assert {key for key, control in app._feedback_page_switches if control.value} == app._feedback_selected


def test_success_does_not_discard_unapplied_list_filter_input(feedback_app):
    app = feedback_app
    record = seed(app)[0]
    app.show_feedback_manager()
    clipboard(app)
    controls = app._feedback_manager_controls
    controls["query"].value = "尚未点击筛选的检索文字"
    controls["status"].value = "已解决"
    click(app, "导出全部记录（不受筛选限制）")
    assert app.feedback_store().get(record["id"])["status"] == "archived"
    assert app._feedback_manager_controls["query"].value == "尚未点击筛选的检索文字"
    assert app._feedback_manager_controls["status"].value == "已解决"


def test_task_cancellation_before_delivery_clears_busy_without_archiving(feedback_app):
    app = feedback_app
    record = seed(app)[0]
    app.show_feedback_manager()
    clipboard(app)
    app.select_feedback(record["id"], True)
    tasks = []
    app.loop.create_task = tasks.append

    async def copy(text):
        raise asyncio.CancelledError()

    app.platform.copy_text.side_effect = copy
    click(app, "导出已选")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(tasks.pop())
    assert app._feedback_export_pending is False
    assert app.feedback_store().get(record["id"]) == record
    assert app._feedback_selected == {record["id"]}
