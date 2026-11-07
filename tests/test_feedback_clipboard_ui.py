"""Deliver the captured feedback version before archiving; retain newer UI edits."""
import json
from unittest.mock import Mock

import pytest

from kanamemory.feedback_store import FeedbackStore

from support.feedback_ui_support import capture
from support.feedback_ui_support import click
from support.feedback_ui_support import feedback_app
from support.feedback_ui_support import png
from support.feedback_ui_support import seed
from support.app_fixtures import setup_app


from support.feedback_clipboard_ui_support import clipboard


def test_selected_clipboard_keeps_complete_context_and_archives_only_delivered_record(feedback_app):
    app = feedback_app
    service = app.feedback_store()
    chosen = service.create("中文・日本語 🙂\n字样有问题", capture(), screenshot=png())
    other = seed(app)[0]
    app.show_feedback_manager()
    assert app._feedback_export_controls["archive"].value is True
    clipboard(app)
    app.select_feedback(chosen["id"], True)
    click(app, "导出已选")
    app.platform.export_feedback.assert_not_awaited()
    packet = json.loads(app.platform.copy_text.call_args.args[0])
    assert packet["record_count"] == 1
    record = packet["records"][0]
    assert record["id"] == chosen["id"]
    assert record["note"] == chosen["note"] and record["context"] == capture()
    assert record["status"] == "open" and record["capture_sha256"] == chosen["capture_sha256"]
    assert packet["attachments"]["images_included"] is False
    assert record["screenshot"]["sha256"] == chosen["screenshot"]["sha256"]
    assert service.get(chosen["id"])["status"] == "archived"
    assert service.get(other["id"])["status"] == "open"
    assert not app._feedback_selected
    assert "已复制 1 条" in app.alert.call_args.args[0]


def test_all_clipboard_archives_snapshot_not_records_added_or_edited_while_copying(feedback_app):
    app = feedback_app
    service = app.feedback_store()
    first, edited, removed = seed(app, 3)
    app.show_feedback_manager()
    clipboard(app)
    added = []

    async def copy(text):
        assert {r["id"] for r in json.loads(text)["records"]} == {first["id"], edited["id"], removed["id"]}
        service.update(edited["id"], note="复制期间刚刚补充的新问题")
        service.delete_many([removed["id"]])
        added.append(service.create("复制期间新增", capture()))
        app._feedback_selected.add(added[0]["id"])
        return True

    app.platform.copy_text.side_effect = copy
    click(app, "导出全部记录（不受筛选限制）")
    assert service.get(first["id"])["status"] == "archived"
    assert service.get(edited["id"])["status"] == "open"
    assert service.get(added[0]["id"])["status"] == "open"
    assert service.get(removed["id"]) is None
    assert app._feedback_selected == {added[0]["id"]}
    assert "被修改" in app.alert.call_args.args[0]


@pytest.mark.parametrize("failed", [False, True], ids=["cancelled", "native-error"])
def test_copy_failure_does_not_archive_or_clear_selection(feedback_app, failed):
    app = feedback_app
    record = seed(app)[0]
    app.show_feedback_manager()
    clipboard(app)
    app.select_feedback(record["id"], True)
    if failed:
        app.platform.copy_text.side_effect = RuntimeError("剪贴板暂不可用")
    else:
        app.platform.copy_text.return_value = False
    click(app, "导出已选")
    assert app.feedback_store().get(record["id"]) == record
    assert app._feedback_selected == {record["id"]}
    assert app._feedback_export_pending is False
    if failed:
        assert "未能导出" in app.alert.call_args.args[0]
    else:
        app.alert.assert_not_called()


@pytest.mark.parametrize("as_text", [False, True], ids=["zip", "clipboard"])
def test_archive_opt_out_preserves_saved_status_and_remains_selected_on_refresh(feedback_app, as_text):
    app = feedback_app
    record = seed(app)[0]
    app.show_feedback_manager()
    if as_text:
        clipboard(app)
    control = app._feedback_export_controls["archive"]
    control.value = False
    control.on_change(control)
    click(app, "导出全部记录（不受筛选限制）")
    assert app.feedback_store().get(record["id"]) == record
    app._render_feedback_manager(query="说明")
    assert app._feedback_export_controls["archive"].value is False


def test_copy_uses_original_store_and_does_not_replace_new_feedback_draft(feedback_app):
    app = feedback_app
    service = app.feedback_store()
    record = seed(app)[0]
    app.show_feedback_manager()
    clipboard(app)

    async def copy(text):
        app.render_feedback_editor(capture())
        app._report_editor["note"].value = "后来打开页面的未保存反馈"
        app.feedback_store = Mock(side_effect=AssertionError("must use captured store"))
        return True

    app.platform.copy_text.side_effect = copy
    click(app, "导出全部记录（不受筛选限制）")
    app.feedback_store.assert_not_called()
    app.feedback_store = lambda: service
    assert service.get(record["id"])["status"] == "archived"
    assert app.page == "report_editor"
    assert app._report_editor["note"].value == "后来打开页面的未保存反馈"


def test_detail_copy_refuses_unsaved_note_then_keeps_edits_made_during_delivery(feedback_app):
    app = feedback_app
    record = seed(app)[0]
    app.show_feedback_manager()
    app.show_feedback_detail(record["id"])
    clipboard(app)
    app._report_detail["note"].value = "保存后的反馈"
    click(app, "导出这一条")
    app.platform.copy_text.assert_not_awaited()
    click(app, "保存修改")
    state = app._report_detail

    async def copy(text):
        assert json.loads(text)["records"][0]["note"] == "保存后的反馈"
        state["note"].value = "复制期间尚未保存的新修改"
        return True

    app.platform.copy_text.side_effect = copy
    click(app, "导出这一条")
    assert app._report_detail is state
    assert state["note"].value == "复制期间尚未保存的新修改"
    assert app.feedback_store().get(record["id"])["note"] == "保存后的反馈"
    assert app.feedback_store().get(record["id"])["status"] == "archived"


def test_double_tap_cannot_copy_or_archive_twice(feedback_app):
    app = feedback_app
    seed(app)
    app.show_feedback_manager()
    clipboard(app)

    async def copy(text):
        app.export_feedback_records()
        return True

    app.platform.copy_text.side_effect = copy
    click(app, "导出全部记录（不受筛选限制）")
    assert app.platform.copy_text.await_count == 1
    assert app.feedback_store().count_records(status="archived") == 1
    assert app._feedback_export_pending is False


def test_archive_failure_reports_delivery_success_separately(feedback_app, monkeypatch):
    app = feedback_app
    record = seed(app)[0]
    app.show_feedback_manager()
    clipboard(app)
    monkeypatch.setattr(FeedbackStore, "archive_exported", Mock(side_effect=OSError("归档写入失败")))
    click(app, "导出全部记录（不受筛选限制）")
    app.platform.copy_text.assert_awaited_once()
    message = app.alert.call_args.args[0]
    assert "已复制" in message and "归档未完成" in message and "未能导出" not in message
    assert app.feedback_store().get(record["id"]) == record
    assert app._feedback_export_pending is False


def test_refresh_failure_does_not_misreport_delivery_or_archive_failure(feedback_app, monkeypatch):
    app = feedback_app
    record = seed(app)[0]
    app.show_feedback_manager()
    clipboard(app)
    monkeypatch.setattr(app, "_feedback_export_refresh", Mock(side_effect=RuntimeError("UI刷新失败")))
    click(app, "导出全部记录（不受筛选限制）")
    message = app.alert.call_args.args[0]
    assert "已复制" in message and "已归档" in message and "列表暂未刷新" in message
    assert "未能导出" not in message
    assert app.feedback_store().get(record["id"])["status"] == "archived"


def test_oversized_clipboard_keeps_every_record_without_truncation(feedback_app):
    app = feedback_app
    context = capture()
    context["large_section"] = "汉" * 100_000
    record = app.feedback_store().create("较大的页面", context)
    app.show_feedback_manager()
    clipboard(app)
    click(app, "导出全部记录（不受筛选限制）")
    app.platform.copy_text.assert_not_awaited()
    assert app.feedback_store().get(record["id"]) == record
    assert "ZIP" in app._feedback_export_status.text
    assert any(control.text == "改用 ZIP 导出" for control in app._feedback_export_actions.children)


def test_empty_export_does_not_replace_clipboard(feedback_app):
    app = feedback_app
    app.show_feedback_manager()
    clipboard(app)
    click(app, "导出全部记录（不受筛选限制）")
    app.platform.copy_text.assert_not_awaited()
    assert "没有可导出" in app.alert.call_args.args[0]
