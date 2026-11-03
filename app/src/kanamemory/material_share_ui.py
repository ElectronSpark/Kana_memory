"""Explicit handoff from the device share inbox to independent editable drafts."""
from .navigation import page_view
from .material_share import MaterialShareStore


class MaterialShareUIMixin:
    def shared_material_rows(self, *, sync=True):
        service = MaterialShareStore(self.store)
        self._material_share_sync_error = False
        if sync:
            read = getattr(self.platform, "pending_material_shares", None)
            try:
                incoming = read() if callable(read) else []
                if incoming:
                    for identity in service.ingest(incoming):
                        self.platform.acknowledge_material_share(identity)
            except Exception:
                # Native entries remain pending; already committed IDs dedupe on retry.
                self._material_share_sync_error = True
        return [row for row in service.list() if row["status"] != "discarded"]

    def refresh_shared_material_notice(self):
        from . import ui_components as u
        slot = getattr(self, "home_material_slot", None)
        if slot is None:
            return
        rows = self.shared_material_rows()
        count = sum(row["status"] == "pending" for row in rows)
        slot.clear()
        if count:
            slot.add(u.button(f"收到 {count} 份分享文本", self.show_material_share_inbox, multiline=True))

    def activity_visibility(self, visible, at=None):
        self.diagnostic_visibility(visible, at)
        if visible and self.page == "home":
            self.refresh_shared_material_notice()

    @page_view()
    def show_material_share_inbox(self, widget=None, *, offset=0, **kwargs):
        from . import ui_components as u
        rows = self.shared_material_rows()
        children = []
        if self._material_share_sync_error:
            children.append(u.label("部分系统分享尚未取回，可稍后刷新；已有内容仍在。", size=13, color=u.MUTED))
        if not rows:
            children.append(u.label("从其他 App 分享日文纯文本到这里，再选择要解析的部分。", size=15))
        for row in rows[offset:offset + 12]:
            identity = row["id"]
            children.append(u.col(u.label(row["text"][:100], size=17, text_role="study"),
                u.row(u.button("继续编辑" if row["status"] == "opened" else "编辑并解析",
                    lambda w, sid=identity: self.show_ai_form("analyze", input_id=sid), flex=1),
                    u.button("舍弃草稿", lambda w, sid=identity: self.discard_material_share(sid), compact=True)),
                margin_bottom=12, padding=10, background_color=u.ENTRY_BACKGROUNDS[0]))
        if offset:
            children.append(u.button("上一页", lambda w: self.show_material_share_inbox(offset=max(0, offset - 12))))
        if offset + 12 < len(rows):
            children.append(u.button("下一页", lambda w: self.show_material_share_inbox(offset=offset + 12)))
        children.extend([u.button("刷新", self.show_material_share_inbox, compact=True),
                         u.button("返回 AI 中心", self.show_ai_center)])
        self.screen("material_share_inbox", "分享文本", *children)

    def discard_material_share(self, identity):
        if self.page != "material_share_inbox":
            return
        MaterialShareStore(self.store).discard(identity)
        self.show_material_share_inbox()
