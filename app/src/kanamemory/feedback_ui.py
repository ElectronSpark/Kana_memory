"""Local feedback editing and batch management, separate from learning state."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json

from .navigation import page_view
from .feedback_analysis_ui import FeedbackAnalysisUIMixin


CATEGORIES = {
    "看不懂／不清楚": "unclear", "语料或解释有误": "content", "读音或播放": "audio",
    "布局显示": "layout", "操作异常": "bug", "其他": "other",
}
STATUSES = {"待处理": "open", "已解决": "resolved", "已归档": "archived"}
FILTERS = {"全部状态": None, **STATUSES}
PAGE_SIZE = 12
EXPORT_FORMATS = {"ZIP 文件（含截图）": "zip", "复制到剪贴板": "text"}


def ui():
    from . import ui_components as app
    return app


def _caption(mapping, value, default):
    return next((caption for caption, key in mapping.items() if key == value), default)


def local_feedback_time(value, zone=None):
    """Show device-local time; legacy naive timestamps are interpreted as UTC."""
    try:
        stamp = datetime.fromisoformat(str(value))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(zone).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value or "")


def _page_title(context):
    page = context.get("page", {})
    return str(page.get("title") or page.get("name") or "当前页面") if isinstance(page, dict) else str(page)


def _visible_tree(value):
    """Preview actual rendered content, never maintainer-only answer material."""
    if isinstance(value, dict):
        if value.get("visible") is False:
            return None
        return {key: _visible_tree(child) for key, child in value.items()
                if key != "fragment_index" and not (isinstance(child, dict) and child.get("visible") is False)}
    if isinstance(value, list):
        return [result for child in value if (result := _visible_tree(child)) is not None]
    return deepcopy(value)


def feedback_preview(context):
    """The full immutable context is exported, but not exposed during a quiz.

    content/practice/fragments may include expected answers needed by maintainers.
    Only public page content and capture metadata belong in an on-device preview.
    """
    return {key: _visible_tree(context[key]) if key == "widget_tree" else deepcopy(context[key])
            for key in ("format", "version", "captured_at_utc", "page", "environment",
                        "completeness", "widget_tree", "sha256") if key in context}


class FeedbackUIMixin(FeedbackAnalysisUIMixin):
    def _feedback_context_panel(self, context, has_screenshot=False):
        u = ui()
        body = u.col()
        opened = [False]

        def toggle(widget=None, **kwargs):
            opened[0] = not opened[0]
            body.clear()
            trigger.text = "收起捕获信息" if opened[0] else "查看捕获信息"
            if opened[0]:
                body.add(u.label("已附当前画面（PNG）。" if has_screenshot else "未附截图；已记录页面文字与状态。", size=13, color=u.MUTED),
                    u.label("此处显示页面文字与捕获信息；供维护使用的完整上下文保存在导出包中。", size=13, color=u.MUTED),
                    u.toga.MultilineTextInput(value=json.dumps(feedback_preview(context), ensure_ascii=False, indent=2),
                        readonly=True, style=u.Pack(height=240, margin_bottom=8, font_size=12)))

        trigger = u.button("查看捕获信息", toggle, compact=True, font_size=12)
        return u.col(trigger, body)

    def render_feedback_editor(self, context, screenshot=None):
        u = ui()
        context = deepcopy(context)
        state = {"context": context, "screenshot": screenshot, "saved_id": None,
                 "initial_note": "", "initial_category": "other", "confirming": False}
        state["note"] = u.toga.MultilineTextInput(placeholder="哪里有问题，或者哪里没看懂？",
            style=u.Pack(height=170, margin_bottom=8, font_size=16))
        state["category"] = u.selection(items=list(CATEGORIES), value="其他", style=u.Pack(margin_bottom=8))
        state["message"] = u.label("", size=13, color=u.MUTED)
        self._report_editor = state
        children = [u.label(_page_title(context), size=18, bold=True),
                    u.label("页面信息已附上，写下问题即可。", size=14, color=u.MUTED),
                    state["note"], state["category"], state["message"],
                    u.button("保存并返回", self.save_page_feedback, primary=True),
                    u.button("保存并让 AI 检查", lambda w: self.save_page_feedback(analyze=True), compact=True),
                    u.button("取消", self.request_report_back),
                    self._feedback_context_panel(context, bool(screenshot))]
        if context.get("completeness", {}).get("complete") is False:
            children.insert(2, u.label("部分页面信息未能捕获，缺失项已记入记录。", size=13, color=u.MUTED))
        self.screen("report_editor", "记录反馈", *children)

    def save_page_feedback(self, widget=None, *, analyze=False, **kwargs):
        if self.page != "report_editor":
            return
        state = self._report_editor
        if state["saved_id"] is not None:
            if analyze: self.show_feedback_analysis(state['saved_id'])
            else: self.return_from_feedback()
            return
        note = str(state["note"].value or "").strip()
        if not note:
            state["message"].text = "请先写下问题或不清楚的地方。"
            return
        try:
            record = self.feedback_store().create(note, state["context"],
                category=CATEGORIES[state["category"].value], screenshot=state["screenshot"])
        except Exception as exc:
            state["message"].text = "未能保存，内容仍保留：" + str(exc)
            return
        state["saved_id"] = record["id"]
        if analyze: self.show_feedback_analysis(record['id'])
        else: self.return_from_feedback()

    def _feedback_dirty(self, state, *, detail=False):
        dirty = (str(state["note"].value or "") != state["initial_note"]
                 or CATEGORIES.get(state["category"].value) != state["initial_category"])
        return dirty or bool(detail and STATUSES.get(state["status"].value) != state["initial_status"])

    def _return_feedback_list(self):
        restore = getattr(self, "return_from_feedback_detail", None)
        if restore is not None:
            restore()
        else:
            self._render_feedback_manager(**getattr(self, "_feedback_manager_state", {}))

    def request_report_back(self, widget=None, **kwargs):
        if self.page not in {"report_editor", "report_detail"}:
            return False
        detail = self.page == "report_detail"
        state = self._report_detail if detail else self._report_editor
        leave = self._return_feedback_list if detail else self.return_from_feedback
        if state.get("saved_id") or not self._feedback_dirty(state, detail=detail):
            leave()
            return True
        if state.get("confirming"):
            return True
        state["confirming"] = True
        token = self._page_token

        def current_editor():
            return (getattr(self, "_report_detail" if detail else "_report_editor", None) is state
                    and self.page == ("report_detail" if detail else "report_editor")
                    and self._page_token == token)

        async def confirm():
            try:
                if not current_editor():
                    return
                accepted = await self.main_window.dialog(ui().toga.ConfirmDialog("放弃未保存的修改？", "尚未保存的反馈文字和分类修改将不会保留。"))
                if accepted and current_editor():
                    leave()
            except Exception as exc:
                state["message"].text = "暂时无法确认退出，未保存的内容仍保留：" + str(exc)
            finally:
                state["confirming"] = False
        self.loop.create_task(confirm())
        return True

    @page_view()
    def show_feedback_manager(self, widget=None, *, status="open", query="", offset=0):
        self._render_feedback_manager(status=status, query=query, offset=offset)

    def _render_feedback_manager(self, *, status="open", query="", offset=0):
        u = ui()
        try:
            service = self.feedback_store()
            total = service.count_records(status=status, query=query)
            offset = max(0, min(offset, ((total - 1) // PAGE_SIZE) * PAGE_SIZE)) if total else 0
            records = service.list_records(status=status, query=query, limit=PAGE_SIZE, offset=offset)
        except Exception as exc:
            self.alert("未能读取反馈记录，当前页面已保留：" + str(exc))
            return
        selected = getattr(self, "_feedback_selected", None)
        if selected is None:
            selected = self._feedback_selected = set()
        state = {"status": status, "query": query, "offset": offset}
        self._feedback_manager_state = state
        search = u.toga.TextInput(value=query, placeholder="搜索反馈文字或页面", style=u.Pack(margin_bottom=6))
        status_picker = u.selection(items=list(FILTERS), value=_caption(FILTERS, status, "全部状态"), style=u.Pack(margin_bottom=6))
        self._feedback_manager_controls = {"query": search, "status": status_picker}

        def apply(widget=None, **kwargs):
            if self.page == "report_list" and self._feedback_manager_state is state:
                self._render_feedback_manager(status=FILTERS[status_picker.value], query=str(search.value or "").strip())
        search.on_confirm = apply
        self._feedback_selection_label = u.label("", size=13, color=u.MUTED)
        self._feedback_bulk_buttons = []
        self._feedback_page_switches = []
        bulk = u.col()

        def toggle_bulk(widget=None, **kwargs):
            if bulk.children:
                bulk.clear()
            else:
                for text, status_code in (("标记已解决", "resolved"), ("归档已选", "archived"), ("重新打开已选", "open")):
                    bulk.add(u.button(text, lambda w, value=status_code: self.change_feedback_batch(value)))
                bulk.add(u.button("删除已选", self.delete_feedback_batch))

        export_selected = u.button("导出已选", lambda w: self.export_feedback_records(selected=True), flex=1)
        analyze_selected = u.button('AI 检查已选', lambda w: self.show_feedback_batch())
        bulk_button = u.button("批量操作", toggle_bulk, compact=True)
        self._feedback_bulk_buttons.extend([export_selected, bulk_button, analyze_selected])
        children = [u.label("反馈保存在本机。ZIP 保留截图；剪贴板包含完整文字上下文（含题目和答案）。", size=13, color=u.MUTED), search, status_picker,
                    u.button("搜索／筛选", apply, compact=True),
                    u.label(f"共 {total} 条", size=14),
                    u.row(u.button("全选本页", self.select_feedback_page, flex=1),
                          u.button("清空选择", self.clear_feedback_selection, flex=1)), self._feedback_selection_label,
                    self._feedback_export_options(),
                    u.row(export_selected, u.button("导出筛选结果", lambda w: self.export_feedback_records(filtered=True), flex=1)),
                    u.button("导出全部记录（不受筛选限制）", lambda w: self.export_feedback_records(selected=False), compact=True),
                    analyze_selected, bulk_button, bulk]
        try:
            from .feedback_batch import FeedbackBatchStore
            batch = FeedbackBatchStore(self.feedback_analysis()).latest()
            if batch: children.append(u.button('继续上次批量检查', lambda w: self.show_feedback_batch(identity=batch['id']), compact=True))
        except (AttributeError, ValueError): pass
        for index, record in enumerate(records):
            identity = record["id"]
            page = record.get("page_title") or record.get("page_name") or "页面反馈"
            checkbox = u.switch("选择这条反馈", value=identity in selected, style=u.Pack(margin_bottom=2))
            checkbox.on_change = lambda w, key=identity: self.select_feedback(key, bool(w.value))
            self._feedback_page_switches.append((identity, checkbox))
            note = str(record.get("note", ""))
            preview = note if len(note) <= 100 else note[:100] + "…"
            caption = _caption(STATUSES, record.get("status"), "待处理") + " · " + _caption(CATEGORIES, record.get("category"), "其他")
            children.append(u.col(checkbox, u.label(page, size=16, bold=True),
                u.label(preview, size=15), u.label(caption + " · " + local_feedback_time(record.get("created_at", "")), size=12, color=u.MUTED),
                u.row(u.button("查看", lambda w, key=identity: self.show_feedback_detail(key), compact=True, flex=1),
                    u.button("取消归档" if record['status'] == 'archived' else "归档",
                        lambda w, key=identity, action='open' if record['status'] == 'archived' else 'archived':
                            self.change_feedback_record(key, action, state), compact=True, flex=1),
                    u.button("删除", lambda w, key=identity: self.change_feedback_record(key, 'delete', state), compact=True, flex=1)),
                padding=10, margin_bottom=8, background_color=u.ENTRY_BACKGROUNDS[index % 3]))
        if not records:
            children.append(u.label("没有符合条件的反馈。", color=u.MUTED))
        if offset:
            children.append(u.button("上一页", lambda w: self._render_feedback_manager(status=status, query=query, offset=offset - PAGE_SIZE)))
        if offset + PAGE_SIZE < total:
            children.append(u.button("下一页", lambda w: self._render_feedback_manager(status=status, query=query, offset=offset + PAGE_SIZE)))
        self._refresh_feedback_selection()
        self.screen("report_list", "反馈记录", *children)

    def _refresh_feedback_selection(self):
        selected = self._feedback_selected
        hidden = len(selected - {identity for identity, _ in self._feedback_page_switches})
        self._feedback_selection_label.text = f"已选 {len(selected)} 条" + (f"（其中 {hidden} 条不在本页）" if hidden else "")
        for button in self._feedback_bulk_buttons:
            button.enabled = bool(selected)

    def select_feedback(self, identity, selected):
        if selected:
            self._feedback_selected.add(identity)
        else:
            self._feedback_selected.discard(identity)
        self._refresh_feedback_selection()

    def select_feedback_page(self, widget=None, **kwargs):
        for identity, control in self._feedback_page_switches:
            self._feedback_selected.add(identity)
            control.value = True
        self._refresh_feedback_selection()

    def clear_feedback_selection(self, widget=None, **kwargs):
        self._feedback_selected.clear()
        for _, control in self._feedback_page_switches:
            control.value = False
        self._refresh_feedback_selection()

    def change_feedback_batch(self, status):
        if self.page != "report_list":
            return
        identities = sorted(self._feedback_selected)
        if not identities:
            return
        try:
            self.feedback_store().update_many(identities, status=status)
        except Exception as exc:
            self.alert("未能更新反馈：" + str(exc))
            return
        self.clear_feedback_selection()
        self._render_feedback_manager(**self._feedback_manager_state)

    def change_feedback_record(self, identity, action, state, *, detail=False):
        """Direct single-record actions never reuse the bulk selection."""
        if action not in {'archived', 'open', 'delete'}:
            raise ValueError('Unknown feedback action')
        page = 'report_detail' if detail else 'report_list'
        attribute = '_report_detail' if detail else '_feedback_manager_state'
        token = self._page_token

        def current():
            return (self.page == page and self._page_token == token
                    and getattr(self, attribute, None) is state
                    and (not detail or state['id'] == identity))

        if not current() or getattr(self, '_feedback_delete_pending', False):
            return

        def commit():
            if action == 'delete':
                self.feedback_store().delete_many([identity])
            elif detail:
                self.feedback_store().update(identity, status=action,
                    note=str(state['note'].value or '').strip(), category=CATEGORIES[state['category'].value])
            else:
                self.feedback_store().update_many([identity], status=action)
            getattr(self, '_feedback_selected', set()).discard(identity)

        def refresh():
            if detail:
                self._return_feedback_list()
            else:
                self._render_feedback_manager(**state)

        if action != 'delete':
            committed = False
            try:
                commit()
                committed = True
                refresh()
            except Exception as exc:
                self.alert(('反馈已更新，请重新打开列表：' if committed else '未能更新反馈，修改仍保留：') + str(exc))
            return
        self._feedback_delete_pending = True

        async def confirm():
            committed = False
            try:
                if not current():
                    return
                accepted = await self.main_window.dialog(ui().toga.ConfirmDialog('删除这条反馈？',
                    '将删除此反馈及其页面快照，无法撤销。' + ('未保存的修改也会丢弃。' if detail and self._feedback_dirty(state, detail=True) else '')))
                if not accepted or not current():
                    return
                commit()
                committed = True
                refresh()
            except Exception as exc:
                self.alert(('反馈已删除，请重新打开列表：' if committed else '未能删除反馈：') + str(exc))
            finally:
                self._feedback_delete_pending = False
        self.loop.create_task(confirm())

    def delete_feedback_batch(self, widget=None, **kwargs):
        if self.page != "report_list":
            return
        identities = sorted(self._feedback_selected)
        if not identities or getattr(self, "_feedback_delete_pending", False):
            return
        self._feedback_delete_pending = True
        state, token = self._feedback_manager_state, self._page_token
        selected = self._feedback_selected

        def current_selection():
            return (self.page == "report_list" and self._page_token == token
                    and self._feedback_manager_state is state
                    and self._feedback_selected is selected and sorted(selected) == identities)

        async def confirm():
            deleted = False
            try:
                if not current_selection():
                    return
                accepted = await self.main_window.dialog(ui().toga.ConfirmDialog("删除这些反馈？", f"将删除所选 {len(identities)} 条反馈及其页面快照。此操作无法撤销。"))
                if not accepted or not current_selection():
                    return
                self.feedback_store().delete_many(identities)
                deleted = True
                self.clear_feedback_selection()
                self._render_feedback_manager(**self._feedback_manager_state)
            except Exception as exc:
                from .diagnostic_events import record_error
                record_error('feedback_delete', 'refresh' if deleted else 'commit', exc)
                self.alert("反馈已删除，但列表未刷新。请重新打开反馈页。" if deleted else "未能删除反馈：" + str(exc))
            finally:
                self._feedback_delete_pending = False
        self.loop.create_task(confirm())

    def _feedback_export_options(self):
        u = ui()
        defaults = getattr(self, "_feedback_export_defaults", {"format": "zip", "archive": True})
        controls = {
            "format": u.selection(items=list(EXPORT_FORMATS),
                value=_caption(EXPORT_FORMATS, defaults["format"], "ZIP 文件（含截图）"),
                style=u.Pack(margin_bottom=4)),
            "archive": u.switch("导出成功后归档", value=defaults["archive"],
                style=u.Pack(margin_bottom=6)),
        }
        self._feedback_export_controls = controls

        def changed(widget=None, **kwargs):
            if getattr(self, "_feedback_export_controls", None) is controls:
                self._feedback_export_defaults = self._feedback_export_settings()
        for control in controls.values():
            control.on_change = changed
        self._feedback_export_status = u.label("", size=13, color=u.MUTED)
        self._feedback_export_actions = u.col()
        return u.col(controls["format"], controls["archive"], self._feedback_export_status, self._feedback_export_actions)

    def _feedback_export_settings(self):
        controls = getattr(self, "_feedback_export_controls", None)
        if controls is None:
            return dict(getattr(self, "_feedback_export_defaults", {"format": "zip", "archive": True}))
        return {"format": EXPORT_FORMATS[controls["format"].value],
                "archive": bool(controls["archive"].value)}

    def _feedback_export_refresh(self, page, token, state, *, archived_ids):
        # A completed file picker must not replace a newly opened screen or edit.
        if self.page != page or self._page_token != token:
            return
        if page == "report_list" and self._feedback_manager_state is state:
            draft = {key: control.value for key, control in self._feedback_manager_controls.items()}
            getattr(self, "_feedback_selected", set()).difference_update(archived_ids)
            for identity, control in self._feedback_page_switches:
                control.value = identity in self._feedback_selected
            self._refresh_feedback_selection()
            self._render_feedback_manager(**state)
            for key, value in draft.items():
                self._feedback_manager_controls[key].value = value
        elif (page == "report_detail" and self._report_detail is state
              and not self._feedback_dirty(state, detail=True)):
            getattr(self, "_feedback_selected", set()).difference_update(archived_ids)
            self._render_feedback_detail(state["id"])

    def export_feedback_records(self, widget=None, *, selected=False, identities=None, filtered=False, format=None):
        if getattr(self, "_feedback_export_pending", False):
            return
        if filtered:
            state = self._feedback_manager_state
            try:
                identities = self.feedback_store().matching_ids(status=state["status"], query=state["query"])
            except Exception as exc:
                self.alert("未能读取筛选结果：" + str(exc))
                return
        identities = list(identities) if identities is not None else sorted(self._feedback_selected) if selected else None
        if identities == []:
            self.alert("请先选择需要导出的反馈。")
            return
        options = self._feedback_export_settings()
        if format is not None:
            options["format"] = format
            controls = getattr(self, "_feedback_export_controls", None)
            if controls is not None:
                controls["format"].value = _caption(EXPORT_FORMATS, format, "ZIP 文件（含截图）")
        self._feedback_export_defaults = dict(options)
        page, token = self.page, self._page_token
        state = (getattr(self, "_feedback_manager_state", None) if page == "report_list"
                 else getattr(self, "_report_detail", None))
        self._feedback_export_pending = True
        from .feedback_export_worker import FeedbackExportJob, FeedbackExportCancelled
        from .feedback_store import FeedbackTextTooLarge
        job = FeedbackExportJob(self.feedback_store().path, getattr(self.store, "data_dir", None),
            getattr(self.store, "zone", None), identities, options["format"])
        self._feedback_export_job = job
        self._feedback_export_state = {"stage": "preparing", "timings_ms": {}}
        status = getattr(self, "_feedback_export_status", None)
        actions = getattr(self, "_feedback_export_actions", None)
        if status is not None:
            status.text = "正在准备导出…"
        if actions is not None:
            actions.clear()
            actions.add(ui().button("取消准备", lambda w: cancel_prepare(), compact=True))

        def current_page():
            return self.page == page and self._page_token == token

        def cancel_prepare():
            if (current_page() and self._feedback_export_job is job
                    and self._feedback_export_state["stage"] == "preparing"):
                job.cancel()

        async def export():
            nonlocal token, status, actions
            try:
                result = await job.run()
                if not current_page() or self._feedback_export_job is not job:
                    return
                prepared = result.prepared
                self._feedback_export_state.update(stage="delivery", timings_ms=result.timings_ms)
                if actions is not None:
                    actions.clear()
                if status is not None:
                    status.text = f"已准备 {prepared.record_count} 条，等待保存。"
                if not prepared.record_count:
                    self.alert("没有可导出的反馈。")
                    return
                if options["format"] == "text":
                    succeeded = await self.platform.copy_text(prepared.payload)
                    message = f"已复制 {prepared.record_count} 条反馈及完整文字上下文（不含截图）。"
                else:
                    succeeded = await self.platform.export_feedback(prepared.payload,
                        f"KanaMemory-feedback-v1-{datetime.now():%Y%m%d-%H%M%S}.zip")
                    message = f"已导出 {prepared.record_count} 条反馈，包含完整上下文和可用截图。"
                if not succeeded:
                    if current_page() and status is not None:
                        status.text = "未导出，反馈仍保留。"
                    return
                if options["archive"]:
                    try:
                        self._feedback_export_state["stage"] = "archiving"
                        outcome = await job.archive(prepared.receipt, result.content_stamp)
                        if outcome is None:
                            if current_page():
                                self.alert(message + ' 导出期间内容修订有变化，未自动归档；可再次导出。')
                            return
                    except Exception as exc:
                        if current_page():
                            self.alert(message + " 自动归档未完成，原反馈仍保留：" + str(exc))
                        return
                    if outcome["archived"]:
                        message += f" 已归档 {outcome['archived']} 条。"
                    if outcome["changed"]:
                        message += f" {outcome['changed']} 条在导出期间被修改，保留原状态。"
                    if outcome["missing"]:
                        message += f" {outcome['missing']} 条已被删除。"
                    try:
                        own_refresh = current_page()
                        self._feedback_export_refresh(page, token, state, archived_ids=outcome["archived_ids"])
                        if own_refresh:
                            token = self._page_token
                            status = getattr(self, "_feedback_export_status", None)
                            actions = getattr(self, "_feedback_export_actions", None)
                    except Exception:
                        message += " 列表暂未刷新，可重新打开查看。"
                if current_page():
                    if status is not None:
                        status.text = message
                    self.alert(message)
            except FeedbackExportCancelled:
                if current_page() and status is not None:
                    status.text = "已取消准备，反馈仍保留。"
            except FeedbackTextTooLarge:
                if current_page():
                    message = "完整文字超过剪贴板容量。可改用 ZIP，保留全部内容。"
                    if status is not None:
                        status.text = message
                    if actions is not None:
                        actions.clear()
                        actions.add(ui().button("改用 ZIP 导出", lambda w: self.export_feedback_records(
                            identities=identities, format="zip"), compact=True))
                    else:
                        self.alert(message)
            except Exception as exc:
                from .diagnostic_events import record_error
                record_error("feedback_export", self._feedback_export_state["stage"], exc)
                if current_page():
                    self.alert("未能导出反馈：" + str(exc))
            finally:
                self._feedback_export_pending = False
                self._feedback_export_state["stage"] = "idle"
                if current_page() and status is not None and status.text.startswith("正在准备"):
                    status.text = "准备已结束。"
                if current_page() and actions is not None:
                    for control in list(actions.children):
                        if getattr(control, "text", "") == "取消准备":
                            actions.remove(control)
        self.loop.create_task(export())

    @page_view(detail=True)
    def show_feedback_detail(self, identity):
        self._render_feedback_detail(identity)

    def _render_feedback_detail(self, identity):
        u = ui()
        try:
            record = self.feedback_store().get(identity)
        except Exception as exc:
            self.alert("未能读取这条反馈，当前页面已保留：" + str(exc))
            return
        if record is None:
            self.alert("这条反馈已不存在。")
            return
        state = {"id": identity, "record": record, "initial_note": record["note"],
                 "initial_category": record["category"], "initial_status": record["status"], "confirming": False}
        state["note"] = u.toga.MultilineTextInput(value=record["note"], style=u.Pack(height=170, margin_bottom=8, font_size=16))
        state["category"] = u.selection(items=list(CATEGORIES), value=_caption(CATEGORIES, record["category"], "其他"), style=u.Pack(margin_bottom=8))
        state["status"] = u.selection(items=list(STATUSES), value=_caption(STATUSES, record["status"], "待处理"), style=u.Pack(margin_bottom=8))
        state["message"] = u.label("", size=13, color=u.MUTED)
        self._report_detail = state
        context = record["context"]
        self.screen("report_detail", "反馈详情", u.label(_page_title(context), size=18, bold=True),
            u.row(u.button('取消归档' if record['status'] == 'archived' else '归档',
                lambda w: self.change_feedback_record(identity, 'open' if record['status'] == 'archived' else 'archived', state, detail=True), compact=True, flex=1),
                u.button('删除', lambda w: self.change_feedback_record(identity, 'delete', state, detail=True), compact=True, flex=1)),
            state["note"], state["category"], state["status"], state["message"],
            u.button("保存修改", self.save_feedback_changes, primary=True),
            u.button('AI 检查', lambda w: self.open_feedback_analysis_detail(), compact=True),
            self._feedback_export_options(),
            u.button("导出这一条", self.export_feedback_detail),
            u.button("返回列表", self.request_report_back),
            self._feedback_context_panel(context, bool(record.get("screenshot", {}).get("available"))))

    def export_feedback_detail(self, widget=None, **kwargs):
        if self.page != "report_detail":
            return
        state = self._report_detail
        if self._feedback_dirty(state, detail=True):
            state["message"].text = "请先保存修改，再导出这条反馈。"
            return
        self.export_feedback_records(identities=[state["id"]])

    def save_feedback_changes(self, widget=None, **kwargs):
        if self.page != "report_detail":
            return
        state = self._report_detail
        note = str(state["note"].value or "").strip()
        if not note:
            state["message"].text = "反馈内容不能为空。"
            return
        try:
            record = self.feedback_store().update(state["id"], note=note,
                category=CATEGORIES[state["category"].value], status=STATUSES[state["status"].value])
        except Exception as exc:
            state["message"].text = "未能保存，修改仍保留：" + str(exc)
            return
        state.update(record=record, initial_note=record["note"], initial_category=record["category"], initial_status=record["status"])
        state["note"].value = record["note"]
        state["message"].text = "修改已保存。"
