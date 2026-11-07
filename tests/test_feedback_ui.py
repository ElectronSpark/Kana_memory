"""Feedback controllers preserve immutable evidence, drafts and learning state."""
import asyncio
from copy import deepcopy
from io import BytesIO
import json
import struct
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
import zipfile
import zlib

import pytest

from support.app_fixtures import app_module, setup_app
from support.fake_ui import descendants, text_of, View
from kanamemory.feedback_store import FeedbackStore
from kanamemory.feedback_ui import FeedbackUIMixin, PAGE_SIZE, feedback_preview


from support.feedback_ui_support import capture, png, click, feedback_app, seed


def packet_records(data):
    with zipfile.ZipFile(BytesIO(data)) as packet:
        return json.loads(packet.read("feedback.json"))["records"]


def test_editor_saves_frozen_context_and_screenshot_then_restores_origin(feedback_app):
    app = feedback_app
    context = capture()
    original = deepcopy(context)
    app.render_feedback_editor(context, png())
    assert app.page == "report_editor"
    assert "维护答案" not in text_of(app.body)
    context["page"]["title"] = "之后变化的页面"
    context["widget_tree"]["children"][1]["value"] = "之后的草稿"
    app._report_editor["note"].value = "  这里的例子不清楚。\n能否补充对比？  "
    app._report_editor["category"].value = "看不懂／不清楚"
    click(app, "保存并返回")
    assert app.page == "learning_question"
    app.return_from_feedback.assert_called_once()
    records = app.feedback_store().list_records()
    assert len(records) == 1
    saved = app.feedback_store().get(records[0]["id"])
    assert saved["context"] == original
    assert saved["note"] == "这里的例子不清楚。\n能否补充对比？"
    assert saved["category"] == "unclear"
    assert app.feedback_store().screenshot_bytes(saved["id"]) == png()
    # A queued second native tap cannot create another record.
    app.save_page_feedback()
    assert app.feedback_store().count_records() == 1


def test_empty_editor_and_failed_save_keep_draft_and_retry_once(feedback_app, monkeypatch):
    app = feedback_app
    app.render_feedback_editor(capture())
    click(app, "保存并返回")
    assert "先写下" in app._report_editor["message"].text
    app.return_from_feedback.assert_not_called()
    app._report_editor["note"].value = "未保存草稿"
    service = app.feedback_store()
    actual = service.create
    monkeypatch.setattr(service, "create", Mock(side_effect=OSError("磁盘暂不可用")))
    click(app, "保存并返回")
    assert app.page == "report_editor"
    assert app._report_editor["note"].value == "未保存草稿"
    assert "内容仍保留" in app._report_editor["message"].text
    monkeypatch.setattr(service, "create", actual)
    click(app, "保存并返回")
    assert service.count_records() == 1


def test_saved_record_not_duplicated_if_navigation_fails_once(feedback_app):
    app = feedback_app
    app.render_feedback_editor(capture())
    app._report_editor["note"].value = "反馈"
    app.return_from_feedback.side_effect = RuntimeError("view not ready")
    with pytest.raises(RuntimeError):
        app.save_page_feedback()
    assert app.feedback_store().count_records() == 1
    app.return_from_feedback.side_effect = None
    app.save_page_feedback()
    assert app.feedback_store().count_records() == 1


@pytest.mark.parametrize("edit", ["note", "category"])
def test_cancel_and_system_back_keep_draft_unless_confirmed(feedback_app, edit):
    app = feedback_app
    app.render_feedback_editor(capture())
    if edit == "note":
        app._report_editor["note"].value = "还没写完"
    else:
        app._report_editor["category"].value = "读音或播放"
    state = app._report_editor
    app.main_window.dialog.return_value = False
    assert app.request_report_back() is True
    assert app._report_editor is state
    assert app.page == "report_editor"
    app.return_from_feedback.assert_not_called()
    assert app.feedback_store().count_records() == 0
    app.main_window.dialog.return_value = True
    click(app, "取消")
    app.return_from_feedback.assert_called_once()
    assert app.feedback_store().count_records() == 0


def test_clean_cancel_has_no_confirmation_and_other_pages_are_not_intercepted(feedback_app):
    app = feedback_app
    app.render_feedback_editor(capture())
    click(app, "取消")
    app.main_window.dialog.assert_not_awaited()
    assert app.request_report_back() is False


def test_stale_cancel_result_cannot_dismiss_nested_feedback(feedback_app):
    app = feedback_app
    tasks = []
    app.loop.create_task = tasks.append
    app.render_feedback_editor(capture())
    app._report_editor["note"].value = "第一条草稿"
    first = app._report_editor
    app.request_report_back()
    app.request_report_back()
    assert len(tasks) == 1
    app.render_feedback_editor({**capture(), "page": {"name": "report_editor", "title": "记录反馈"}})
    app._report_editor["note"].value = "嵌套草稿"
    asyncio.run(tasks.pop())
    app.return_from_feedback.assert_not_called()
    assert app._report_editor["note"].value == "嵌套草稿"
    assert first["confirming"] is False


def test_preview_is_optional_selectable_and_never_reveals_unseen_answers(feedback_app):
    app = feedback_app
    context = capture()
    app.render_feedback_editor(context)
    assert not any(getattr(item, "readonly", False) for item in descendants(app.body))
    click(app, "查看捕获信息")
    preview = next(item for item in descendants(app.body) if getattr(item, "readonly", False))
    assert "我的未提交草稿" in preview.value
    assert "这里应该用哪个助词" in preview.value
    assert "未附截图" in text_of(app.body)
    for hidden in ("隐藏标准答案", "维护答案", "片段隐藏答案", "fragment_index", "recent_events"):
        assert hidden not in preview.value
    assert feedback_preview(context)["widget_tree"]["children"] == [
        {"type": "Label", "text": "这里应该用哪个助词？"},
        {"type": "TextInput", "value": "我的未提交草稿"}]
    assert context["practice"]["maintainer_only"]["answer"] == "维护答案"
    click(app, "收起捕获信息")
    assert not any(getattr(item, "readonly", False) for item in descendants(app.body))


def test_manager_paginates_and_preserves_selection_across_filters(feedback_app):
    app = feedback_app
    records = seed(app, PAGE_SIZE + 2)
    app.show_feedback_manager()
    assert len(app._feedback_page_switches) == PAGE_SIZE
    assert not next(item for item in descendants(app.body) if item.text == "导出已选").enabled
    click(app, "全选本页")
    first_page = set(app._feedback_selected)
    assert len(first_page) == PAGE_SIZE
    click(app, "下一页")
    click(app, "全选本页")
    assert app._feedback_selected == {record["id"] for record in records}
    app._feedback_manager_controls["query"].value = "说明 1"
    app._feedback_manager_controls["query"].on_confirm(None)
    assert app._feedback_manager_state["offset"] == 0
    assert all("说明 1" in app.feedback_store().get(identity)["note"] for identity, _ in app._feedback_page_switches)
    assert len(app._feedback_selected) == PAGE_SIZE + 2
    click(app, "清空选择")
    assert not app._feedback_selected
    assert not any(control.value for _, control in app._feedback_page_switches)
    assert first_page


def test_batch_status_management_and_search_are_persisted(feedback_app):
    app = feedback_app
    records = seed(app, 3)
    app.show_feedback_manager()
    app.select_feedback(records[0]["id"], True)
    app.select_feedback(records[1]["id"], True)
    click(app, "批量操作")
    click(app, "标记已解决")
    assert not app._feedback_selected
    assert app.feedback_store().count_records(status="resolved") == 2
    app._feedback_manager_controls["status"].value = "已解决"
    click(app, "搜索／筛选")
    assert len(app._feedback_page_switches) == 2
    click(app, "全选本页")
    app.change_feedback_batch("archived")
    assert "没有符合条件" in text_of(app.body)
    assert app.feedback_store().count_records(status="archived") == 2
    app.show_feedback_manager(status="archived")
    click(app, "全选本页")
    app.change_feedback_batch("open")
    assert app.feedback_store().count_records(status="open") == 3


def test_failed_batch_retains_selection_and_no_partial_changes(feedback_app):
    app = feedback_app
    records = seed(app, 2)
    app.show_feedback_manager()
    click(app, "全选本页")
    app._feedback_selected.add(str(uuid4()))
    selection = set(app._feedback_selected)
    app.change_feedback_batch("resolved")
    assert app._feedback_selected == selection
    assert app.feedback_store().count_records(status="open") == 2
    assert "未能更新" in app.alert.call_args.args[0]


def test_delete_cancel_then_confirm_removes_only_selected(feedback_app):
    app = feedback_app
    records = seed(app, 3)
    app.show_feedback_manager()
    app.select_feedback(records[0]["id"], True)
    app.main_window.dialog.return_value = False
    app.delete_feedback_batch()
    assert app.feedback_store().count_records() == 3
    assert app._feedback_selected == {records[0]["id"]}
    app.main_window.dialog.return_value = True
    app.delete_feedback_batch()
    assert app.feedback_store().count_records() == 2
    assert not app._feedback_selected
    assert app.feedback_store().get(records[0]["id"]) is None


def test_delete_commit_survives_list_refresh_failure_and_releases_busy(feedback_app, monkeypatch):
    app = feedback_app
    records = seed(app, 2)
    app.show_feedback_manager()
    app.select_feedback(records[0]["id"], True)
    app.main_window.dialog.return_value = True
    monkeypatch.setattr(app, "_render_feedback_manager", Mock(side_effect=OSError("refresh")))
    app.delete_feedback_batch()
    assert app.feedback_store().get(records[0]["id"]) is None
    assert app.feedback_store().get(records[1]["id"]) is not None
    assert "反馈已删除" in app.alert.call_args.args[0]
    assert app._feedback_delete_pending is False


def test_export_freezes_selection_archives_success_and_preserves_cancelled_records(feedback_app):
    app = feedback_app
    records = seed(app, 3)
    app.feedback_store().update(records[2]["id"], status="archived")
    app.show_feedback_manager()
    app.select_feedback(records[0]["id"], True)
    tasks = []
    app.loop.create_task = tasks.append
    app.export_feedback_records(selected=True)
    app.export_feedback_records(selected=True)
    assert len(tasks) == 1
    app.select_feedback(records[1]["id"], True)
    asyncio.run(tasks.pop())
    exported, filename = app.platform.export_feedback.call_args.args
    assert filename.startswith("KanaMemory-feedback-v1-") and filename.endswith(".zip")
    assert {record["id"] for record in packet_records(exported)} == {records[0]["id"]}
    assert app._feedback_selected == {records[1]["id"]}
    assert app.feedback_store().get(records[0]["id"])["status"] == "archived"
    app.platform.export_feedback.return_value = False
    app.alert.reset_mock()
    app.export_feedback_records(selected=False)
    asyncio.run(tasks.pop())
    assert {record["id"] for record in packet_records(app.platform.export_feedback.call_args.args[0])} == {record["id"] for record in records}
    app.alert.assert_not_called()
    assert app.feedback_store().count_records(status="open") == 1
    assert app.feedback_store().count_records(status="archived") == 2
    assert app._feedback_export_pending is False


def test_export_error_and_empty_selection_leave_records_untouched(feedback_app):
    app = feedback_app
    record = seed(app)[0]
    app.show_feedback_manager()
    app.export_feedback_records(selected=True)
    app.platform.export_feedback.assert_not_awaited()
    assert "请先选择" in app.alert.call_args.args[0]
    app.platform.export_feedback.side_effect = OSError("写入失败")
    app.export_feedback_records()
    assert "未能导出" in app.alert.call_args.args[0]
    assert app.feedback_store().get(record["id"])["status"] == "open"
    assert app._feedback_export_pending is False


def test_edit_detail_preserves_capture_and_refuses_to_export_unsaved_note(feedback_app):
    app = feedback_app
    record = seed(app)[0]
    app.show_feedback_manager()
    app.show_feedback_detail(record["id"])
    state = app._report_detail
    state["note"].value = "  更明确的说明  "
    state["category"].value = "布局显示"
    state["status"].value = "已解决"
    click(app, "导出这一条")
    app.platform.export_feedback.assert_not_awaited()
    assert "请先保存" in state["message"].text
    click(app, "保存修改")
    saved = app.feedback_store().get(record["id"])
    assert saved["note"] == "更明确的说明"
    assert saved["category"] == "layout" and saved["status"] == "resolved"
    assert saved["context"] == record["context"]
    assert saved["capture_sha256"] == record["capture_sha256"]
    click(app, "导出这一条")
    assert packet_records(app.platform.export_feedback.call_args.args[0])[0]["note"] == "更明确的说明"
    click(app, "查看捕获信息")
    assert "未附截图" in text_of(app.body)
    click(app, "返回列表")
    app.main_window.dialog.assert_not_awaited()
    app.return_from_feedback_detail.assert_called_once()


def test_detail_cancel_and_failed_save_keep_changes_until_explicit_discard(feedback_app, monkeypatch):
    app = feedback_app
    record = seed(app)[0]
    app.show_feedback_manager()
    app.show_feedback_detail(record["id"])
    state = app._report_detail
    state["note"].value = ""
    app.save_feedback_changes()
    assert "不能为空" in state["message"].text
    state["note"].value = "新的说明"
    monkeypatch.setattr(app.feedback_store(), "update", Mock(side_effect=OSError("存储失败")))
    app.save_feedback_changes()
    assert "修改仍保留" in state["message"].text
    assert state["note"].value == "新的说明"
    app.main_window.dialog.return_value = False
    click(app, "返回列表")
    assert app.page == "report_detail"
    assert app.feedback_store().get(record["id"])["note"] == record["note"]
    app.main_window.dialog.return_value = True
    app.request_report_back()
    assert app.page == "report_list"


def test_missing_detail_returns_to_manager_without_crashing(feedback_app):
    app = feedback_app
    app.show_feedback_manager()
    app.show_feedback_detail(str(uuid4()))
    assert app.page == "report_list"
    assert "已不存在" in app.alert.call_args.args[0]
    app.return_from_feedback_detail.assert_not_called()


@pytest.mark.parametrize("failure", ["open", "count_records", "list_records"])
def test_manager_read_error_preserves_existing_page_and_filter_draft(feedback_app, monkeypatch, failure):
    app = feedback_app
    service = app.feedback_store()
    seed(app, 2)
    app.show_feedback_manager()
    app.select_feedback_page()
    app._feedback_manager_controls["query"].value = "未应用的搜索文字"
    body, state = app.body, app._feedback_manager_state
    controls, selected = app._feedback_manager_controls, set(app._feedback_selected)
    token = app._page_token
    if failure == "open":
        app.feedback_store = Mock(side_effect=OSError("无法打开数据库"))
    else:
        monkeypatch.setattr(service, failure, Mock(side_effect=OSError("反馈数据库损坏")))
    app.show_feedback_manager(status="resolved", query="新筛选")
    assert app.body is body and app._page_token == token
    assert app._feedback_manager_state is state
    assert app._feedback_manager_controls is controls
    assert controls["query"].value == "未应用的搜索文字"
    assert app._feedback_selected == selected
    assert "当前页面已保留" in app.alert.call_args.args[0]
    app.feedback_store = lambda: service


def test_first_manager_open_error_keeps_origin_and_does_not_create_manager_bindings(feedback_app):
    app = feedback_app
    service = app.feedback_store()
    page, body, token = app.page, app.body, app._page_token
    app.feedback_store = Mock(side_effect=PermissionError("不能写入反馈目录"))
    app.show_feedback_manager()
    assert app.page == page and app.body is body and app._page_token == token
    assert not hasattr(app, "_feedback_manager_state")
    assert not hasattr(app, "_feedback_selected")
    assert "未能读取" in app.alert.call_args.args[0]
    app.feedback_store = lambda: service


@pytest.mark.parametrize("failure", ["open", "get"])
def test_detail_read_error_keeps_current_detail_and_unsaved_note(feedback_app, monkeypatch, failure):
    app = feedback_app
    service = app.feedback_store()
    records = seed(app, 2)
    app.show_feedback_manager()
    app.show_feedback_detail(records[0]["id"])
    app._report_detail["note"].value = "当前反馈的未保存修改"
    state, body, token = app._report_detail, app.body, app._page_token
    if failure == "open":
        app.feedback_store = Mock(side_effect=OSError("不能打开数据库"))
    else:
        monkeypatch.setattr(service, "get", Mock(side_effect=ValueError("上下文已损坏")))
    app.show_feedback_detail(records[1]["id"])
    assert app._report_detail is state and app.body is body and app._page_token == token
    assert state["note"].value == "当前反馈的未保存修改"
    assert "当前页面已保留" in app.alert.call_args.args[0]
    app.feedback_store = lambda: service


@pytest.mark.parametrize("accepted", [False, True])
def test_delete_dialog_feedback_action_never_deletes_or_changes_new_page(feedback_app, accepted):
    app = feedback_app
    seed(app, 2)
    app.show_feedback_manager()
    app.select_feedback_page()
    selected = set(app._feedback_selected)

    async def dialog_feedback(dialog):
        app.render_feedback_editor(capture())
        app._report_editor["note"].value = "针对删除弹窗的新反馈"
        return accepted

    app.main_window.dialog.side_effect = dialog_feedback
    app.delete_feedback_batch()
    assert app.feedback_store().count_records() == 2
    assert app.page == "report_editor"
    assert app._report_editor["note"].value == "针对删除弹窗的新反馈"
    assert app._feedback_selected == selected
    assert app._feedback_delete_pending is False


@pytest.mark.parametrize("change", ["view", "selection", "token"])
def test_delete_confirmation_is_bound_to_original_view_and_selection(feedback_app, change):
    app = feedback_app
    records = seed(app, 2)
    app.show_feedback_manager()
    app.select_feedback(records[0]["id"], True)

    async def stale_accept(dialog):
        if change == "view":
            app._render_feedback_manager(query="说明")
        elif change == "selection":
            app.select_feedback(records[1]["id"], True)
        else:
            app._page_token += 1  # A retained screen returned from nested feedback.
        return True

    app.main_window.dialog.side_effect = stale_accept
    app.delete_feedback_batch()
    assert app.feedback_store().count_records() == 2
    assert records[0]["id"] in app._feedback_selected
    assert app._feedback_delete_pending is False


def test_delete_queued_after_leaving_manager_and_stale_batch_buttons_do_nothing(feedback_app):
    app = feedback_app
    seed(app, 2)
    app.show_feedback_manager()
    app.select_feedback_page()
    tasks = []
    app.loop.create_task = tasks.append
    app.delete_feedback_batch()
    app.render_feedback_editor(capture())
    asyncio.run(tasks.pop())
    app.main_window.dialog.assert_not_awaited()
    app.change_feedback_batch("resolved")
    app.delete_feedback_batch()
    assert not tasks
    assert app.feedback_store().count_records(status="open") == 2
    assert app._feedback_delete_pending is False


def test_failed_exit_confirmation_keeps_draft_and_allows_retry(feedback_app):
    app = feedback_app
    app.render_feedback_editor(capture())
    app._report_editor["note"].value = "不能丢的草稿"
    app.main_window.dialog.side_effect = RuntimeError("系统弹窗不可用")
    app.request_report_back()
    assert app.page == "report_editor"
    assert app._report_editor["note"].value == "不能丢的草稿"
    assert app._report_editor["confirming"] is False
    assert "未保存的内容仍保留" in app._report_editor["message"].text
    app.main_window.dialog.side_effect = None
    app.request_report_back()
    app.return_from_feedback.assert_called_once()


def test_stale_positive_exit_result_after_retained_view_restore_does_not_discard(feedback_app):
    app = feedback_app
    app.render_feedback_editor(capture())
    app._report_editor["note"].value = "反馈草稿"

    async def stale_accept(dialog):
        app._page_token += 2
        return True

    app.main_window.dialog.side_effect = stale_accept
    app.request_report_back()
    app.return_from_feedback.assert_not_called()
    assert app._report_editor["note"].value == "反馈草稿"


def test_incomplete_capture_notice_and_screenshot_preview_are_accurate(feedback_app):
    app = feedback_app
    context = capture()
    context["completeness"] = {"complete": False, "warnings": [{"reason": "no native hierarchy"}]}
    app.render_feedback_editor(context, png())
    assert "部分页面信息未能捕获" in text_of(app.body)
    click(app, "查看捕获信息")
    assert "已附当前画面" in text_of(app.body)
