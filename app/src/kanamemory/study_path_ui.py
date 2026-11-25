"""A quiet, resumable course route over the existing learning queues.

Sections select content. They do not own review schedules or duplicate cards.
"""
from __future__ import annotations

import sqlite3
from typing import Any, NamedTuple

from .navigation import page_view
from .study_routes import CORE_ROUTE_ID, CORE_ROUTE_TITLE
from .study_path_display import (SECTION_STATES, active_route, closure_text, has_other_routes,  # noqa: F401
                                 other_route_rows, path_display_snapshot, route_key, route_kwargs, route_title)
from .typography import configure_study_ranges
from . import ui_components as u
from .admission import display as add_text
from .admission.ui.add_page import AddPage
from .content_availability import LIBRARY_DISABLED_TEXT


def progress_text(progress, *, sections=True):
    unit = "节" if sections else "项"
    total = int(progress.get("total", 0))
    added = int(progress.get("added", 0))
    completed = int(progress.get("completed", 0))
    partial = int(progress.get("partial", 0)) if sections else 0
    result = f"初学完成 {completed}/{total} {unit} · 未加入 {max(0, total - added - partial)} {unit}"
    return result + (f" · 部分加入 {partial} {unit}" if partial else "")


# The route helpers (other_route_rows included), SECTION_STATES, path_display_snapshot and closure_text live in
# study_path_display (pure, shared with the add-content and home services); they are imported above under the
# same names.

def route_overview(service, route_id):
    return service.overview(**route_kwargs(route_id))


def external_prerequisite_lines(service, section, route_id):
    """Section-page lines for hard prerequisites outside this route (design 10 D2)."""
    external = list(section.get("external_prerequisites") or ())
    if route_key(route_id) is None or not external:
        return []
    owners, missing = {}, 0
    current = route_key(route_id)
    for identity in external:
        found = [rid for rid in service.entry_owners(identity) if rid != current]
        for rid in found:
            owners[rid] = owners.get(rid, 0) + 1
        if not found:
            missing += 1
    lines = []
    if owners:
        lines.append("前置在：" + "、".join(f"{route_title(service, rid)}（{count} 项）"
                                          for rid, count in owners.items()))
    if missing:
        lines.append(f"前置不在任何路线（添加时自动加入）：{missing} 项")
    return lines


# One wording per library state (content_availability, review B-06); the
# registry's exact state (missing/damaged) is not part of the route row.
ROUTE_REFUSED_TEXT = "路线被拒（前缀不符）"
ROUTE_STATUS = {"library_unavailable": LIBRARY_DISABLED_TEXT, "refused": ROUTE_REFUSED_TEXT,
                "invalid": "路线规格无效，已跳过"}
ROUTE_CREATED_NOTICE = "已创建路线，同一进度，不同路线"
ROUTE_DELETED_NOTICE = "已删除路线，学习进度保留"
ROUTE_DELETE_HINT = "只删路线，不删进度：已学内容、复习安排与其他路线都保留。"


class AddShowing(NamedTuple):
    """One showing of 添加学习内容, kept on the app as _study_add (Back restores it with the page's widgets)."""

    operations: Any   # admission.service.AddOperations: the service bound to this showing's session
    page: AddPage


def focus_models(app, *scopes):
    """display.FocusButtonModel (or None) for each FocusScope a page shows, one settings read (design 12 UI6)."""
    from .study_path_runtime import StudyPathRuntimeMixin
    return StudyPathRuntimeMixin.study_arrangement(app).presenter.focus_buttons(scopes)


def focus_controls(app, model, redraw):
    """设为重点 / 取消重点 of a page's own object (design 12 UI6, H6) from its display model.

    Setting or cancelling goes through the focus service (with_scope: the
    tilts stay; the current round is rearranged once), then ``redraw``
    repaints the page, which now says whether its object is the focus. The
    current route (study_routes_v1) is not touched.
    """
    from .arrangement.ui.focus_button import focus_button
    from .study_path_runtime import StudyPathRuntimeMixin

    def change(scope):
        StudyPathRuntimeMixin.study_arrangement(app).focus.with_scope(scope)
        redraw()
    return focus_button(model, on_set=change, on_cancel=lambda: change(None))


def node_focus_scope(route_id, node_id):
    """The focus scope of a stage, unit or section page (route_id None is the core route)."""
    from .arrangement.model import FocusScope
    return FocusScope.node(route_id or CORE_ROUTE_ID, node_id)


class StudyPathUIMixin:
    def _path_feedback(self, widget, kind, value):
        widget._feedback_fragment = {"kind": kind, "snapshot_scope": "displayed_hierarchy_level",
                                     "path_snapshot": path_display_snapshot(value, kind)}
        return widget

    def _path_section_caption(self, section):
        status = SECTION_STATES.get(section.get("state"), "未加入")
        progress = progress_text(section.get("progress", {}), sections=False)
        if section.get("entries") and all(entry.get("kind") == "reference" for entry in section["entries"]):
            status = "已读" if section.get("state") == "completed" else "参考"
            progress = progress.replace("初学完成", "已读")
        return section["title"] + "\n" + status + " · " + progress

    def _path_next_controls(self, overview, route_id=None, *, compact=False, others=()):
        """Next-step controls of one route's overview (default: the core route).

        others: routes_overview rows listed as "其他路线的下一小节" when this
        route has no next section (design 10 section 8.2, at most 3).
        """
        route = route_kwargs(route_id)
        # Design 12 H12: during a "继续学习" under the learner's filter, only a section it allows is offered.
        offered = getattr(self, "offered_section", lambda route_id, section: section)
        next_section = offered(route.get("route_id"), overview.get("next_section"))
        children = []
        available = getattr(self, "study_path_can_continue", None)
        can_continue = callable(available) and available()
        if can_continue:
            children.append(u.button("继续已加入的学习", self.continue_study_path, primary=True, multiline=True))
        if next_section:
            children.append(u.label("接下来：" + next_section["title"], size=17, bold=True, color=u.GREEN))
            if not compact:
                location = " · ".join(str(next_section.get(key, "")) for key in ("stage_title", "unit_title") if next_section.get(key))
                if location:
                    children.append(u.label(location, size=12, color=u.MUTED))
            button = u.button("学习下一小节",
                lambda w, identity=next_section["id"]: self._add_study_sections((identity,), **route), primary=not can_continue, multiline=True)
            children.append(self._path_feedback(button, "study_path_section", next_section))
        else:
            title = CORE_ROUTE_TITLE if not route else route_title(self.study_path_service(), route_id)
            children.append(u.label(title + "小节都已加入，可继续复习或选择拓展内容。", size=15, color=u.MUTED))
            if not can_continue:
                children.append(u.button("继续已加入的学习", self.continue_study_path, primary=True, multiline=True))
            others = [row for row in others if offered(row["route_id"], row.get("next_section"))][:3]
            if others:
                children.append(u.label("其他路线的下一小节", size=15, bold=True))
            for row in others:
                section = row["next_section"]
                children.append(u.button(row["title"] + " · " + section["title"],
                    lambda w, sid=section["id"], rid=row["route_id"]: self._add_study_sections((sid,), **route_kwargs(rid)),
                    multiline=True))
        # route_id None: the legacy caller's add page (the current route).
        children.append(u.button("添加内容", self.show_study_path_add if route_id is None
                                 else (lambda w: self.show_study_path_add(route_id=route_id)), compact=True))
        return children

    def _path_route_list(self, service, shown):
        """Route list rows of the route page; [] for the core route alone (design 10 section 9)."""
        reader = getattr(service, "routes_overview", None)
        if not callable(reader):
            return []
        rows = reader()["routes"]
        if len(rows) < 2:
            return []
        from .content_library_service import library_titles
        libraries = library_titles(self.store)
        children = [u.label("同一进度，不同路线", size=17, bold=True, color=u.GREEN),
                    u.label("各路线共用同一份学习记录；切换路线只改变浏览与下一小节的顺序。", size=12, color=u.MUTED)]
        for row in rows:
            rid = row["route_id"]
            heading = row["title"] + (" · 当前" if row["active"] else "") + ("（正在查看）" if rid == shown else "")
            lines = [heading, "来源：" + libraries.get(row["library_id"], row["library_id"])]
            if row["progress"] is not None:
                summary = row["progress"]
                lines.append(progress_text({"total": summary["required_total_sections"],
                                            "added": summary["required_added_sections"],
                                            "completed": summary["required_completed_sections"],
                                            "partial": summary["required_partial_sections"]}))
            if not row["available"]:
                lines.append(ROUTE_STATUS.get(row["status"], "暂不可用"))
            elif not row["enabled"]:
                lines.append("已停用")
            block = [u.label("\n".join(lines), size=14)]
            if row["available"]:
                actions = []
                if rid != shown:
                    actions.append(u.button("查看", lambda w, r=rid: self.show_study_path(route_id=r), flex=1,
                                            compact=True, margin_right=8))
                if not row["active"] and row["enabled"]:
                    actions.append(u.button("设为当前", lambda w, r=rid: self.set_study_route_active(r), flex=1,
                                            compact=True))
                if actions:
                    block.append(u.row(*actions))
                if rid != CORE_ROUTE_ID:
                    toggle = u.switch("启用此路线", value=bool(row["enabled"]))
                    toggle.on_change = lambda w, r=rid: self.set_study_route_enabled(r, bool(w.value))
                    block.append(toggle)
            if row["source"] == "local":
                confirm = u.col()
                block += [u.button("删除路线", lambda w, r=rid, c=confirm: self._ask_delete_study_route(r, c, shown),
                                   compact=True), confirm]
            entry = u.col(*block, padding=8, margin_bottom=6, background_color=u.WHITE)
            entry._feedback_fragment = {"kind": "study_route", "route_id": rid, "status": row["status"],
                                        "active": row["active"], "enabled": row["enabled"]}
            children.append(entry)
        return children

    def _ask_delete_study_route(self, route_id, box, shown):
        """Second confirmation of "删除路线", inline under that route's row."""
        box.clear()
        box.add(u.label(ROUTE_DELETE_HINT, size=13, color=u.MUTED),
                u.row(u.button("确认删除路线", lambda w: self.delete_study_route(route_id, shown=shown), flex=1,
                               compact=True, margin_right=8),
                      u.button("取消", lambda w: box.clear(), flex=1, compact=True)))

    def delete_study_route(self, route_id, *, shown=None):
        """Delete a personal route (spec and route state only; progress stays)."""
        try:
            self.study_path_service().delete_personal_route(route_id)
        except ValueError as exc:
            self.alert(str(exc))
            return
        except (sqlite3.Error, OSError):
            self.alert("未能删除路线，请重试。")
            return
        self._study_path_notice = ROUTE_DELETED_NOTICE
        self.show_study_path(route_id=None if shown in {None, route_id} else shown)

    def set_study_route_active(self, route_id):
        """"设为当前": writes study_routes_v1.active_route only; queues are untouched."""
        try:
            self.study_path_service().set_active_route(route_id)
        except ValueError as exc:
            self.alert(str(exc))
        self.show_study_path(route_id=route_id)

    def set_study_route_enabled(self, route_id, enabled):
        """Enable/disable a non-core route (12 Q2: the core route stays enabled)."""
        try:
            self.study_path_service().set_route_enabled(route_id, enabled)
        except ValueError as exc:
            self.alert(str(exc))
        self.show_study_path(route_id=route_id if enabled else None)

    @page_view()
    def show_study_path(self, widget=None, *, route_id=None, **kwargs):
        """Route list (only with other routes) and one route's stage tree (default: the current route)."""
        service = self.study_path_service()
        shown = route_id or active_route(service)
        if route_key(shown) is not None and shown not in service.route_set():
            shown = active_route(service)  # an old link to a route that is gone
        route = route_kwargs(shown)
        overview = service.overview(**route)
        notice, self._study_path_notice = getattr(self, "_study_path_notice", None), None
        children = [u.label(notice, size=15, bold=True, color=u.GREEN)] if notice else []
        children += self._path_route_list(service, shown)
        others = other_route_rows(service, shown) if not overview.get("next_section") else ()
        children += self._path_next_controls(overview, route_id=shown, others=others)
        from .arrangement.model import FocusScope
        children += focus_controls(self, focus_models(self, FocusScope("route", shown))[0],
                                   lambda: self.show_study_path(route_id=shown))
        # Moved here from the home page (review 2026-11-08 section 7 item 7).
        if callable(getattr(self, "study_queue_controls", None)):
            children += self.study_queue_controls()
        for stage in overview.get("stages", []):
            entry = u.button(stage["title"] + "\n" + progress_text(stage.get("progress", {})),
                lambda w, identity=stage["id"]: self.show_study_stage(identity, **route), multiline=True)
            children.append(self._path_feedback(entry, "study_path_stage", stage))
        children.append(u.button("自由查阅学习库", self.show_learning_library, compact=True))
        children.append(u.button("课程说明", self.show_study_path_notes, compact=True))
        self.screen("study_path", "学习路线", *children)

    def _path_stage(self, identity, route_id=None):
        overview = self.study_path_service().overview(**route_kwargs(route_id))
        return next((stage for stage in overview.get("stages", []) if stage["id"] == identity), None)

    @page_view(detail=True)
    def show_study_stage(self, identity, route_id=None):
        route = route_kwargs(route_id)
        stage = self._path_stage(identity, **route)
        if stage is None:
            self.show_study_path(**route)
            return
        children = [u.label(progress_text(stage.get("progress", {})), size=14, color=u.MUTED)]
        children += focus_controls(self, focus_models(self, node_focus_scope(route_id, identity))[0],
                                   lambda: StudyPathUIMixin.show_study_stage.__wrapped__(self, identity, route_id))
        for unit in stage.get("units", []):
            entry = u.button(unit["title"] + "\n" + progress_text(unit.get("progress", {})),
                lambda w, sid=identity, uid=unit["id"]: self.show_study_unit(uid, sid, **route), multiline=True)
            children.append(self._path_feedback(entry, "study_path_unit", unit))
        self.screen("study_stage", stage["title"], *children)

    @page_view(detail=True)
    def show_study_unit(self, unit_id, stage_id, route_id=None):
        route = route_kwargs(route_id)
        stage = self._path_stage(stage_id, **route)
        unit = next((unit for unit in (stage or {}).get("units", []) if unit["id"] == unit_id), None)
        if unit is None:
            self.show_study_path(**route)
            return
        self._study_path_unit = (stage_id, unit_id)
        children = [u.label(progress_text(unit.get("progress", {})), size=14, color=u.MUTED)]
        children += focus_controls(self, focus_models(self, node_focus_scope(route_id, unit_id))[0],
                                   lambda: StudyPathUIMixin.show_study_unit.__wrapped__(self, unit_id, stage_id, route_id))
        sections = unit.get("sections", [])
        remaining = [section for section in sections if section.get("new_count", 0) > 0]
        if remaining:
            children.append(u.button("选择本单元内容", lambda w: self.show_study_path_add(selection_key=("unit", unit_id), route_id=route_id or CORE_ROUTE_ID), primary=True, multiline=True))
        for section in sections:
            entry = u.button(self._path_section_caption(section),
                lambda w, identity=section["id"]: self.show_study_section(identity, **route), multiline=True)
            children.append(self._path_feedback(entry, "study_path_section", section))
        self.screen("study_unit", unit["title"], *children)

    @page_view(detail=True)
    def show_study_section(self, identity, route_id=None):
        route = route_kwargs(route_id)
        service = self.study_path_service()
        section = service.section(identity, **route)
        self._study_path_section_id = identity
        references = [entry for entry in section.get("entries", []) if entry.get("kind") == "reference"]
        reference_only = bool(references) and len(references) == len(section.get("entries", []))
        progress = progress_text(section.get("progress", {}), sections=False)
        if reference_only:
            progress = progress.replace("初学完成", "已读")
        children = [u.label(progress, size=14, color=u.MUTED)]
        if section.get("optional"):
            children.append(u.label("拓展小节，可按需加入。", size=13, color=u.MUTED))
        for line in external_prerequisite_lines(service, section, route_id):
            children.append(u.label(line, size=13, color=u.MUTED))
        children += focus_controls(self, focus_models(self, node_focus_scope(route_id, identity))[0],
                                   lambda: StudyPathUIMixin.show_study_section.__wrapped__(self, identity, route_id))
        if reference_only:
            children.append(u.label("只作阅读参考，不加入记忆卡。", size=13, color=u.MUTED))
            if section.get("state") == "completed":
                children.append(u.button("继续学习", self.continue_study_path, primary=True))
                following = service.overview(**route).get("next_section")
                if following:
                    children.append(u.button("学习下一小节", lambda w, sid=following["id"]: self._add_study_sections((sid,), **route)))
        elif section.get("new_count", 0):
            children.append(u.button("选择本节内容", lambda w: self.show_study_path_add(selection_key=("section", identity), route_id=route_id or CORE_ROUTE_ID), primary=True))
        else:
            children.append(u.button("继续学习", self.continue_study_path, primary=True))
        for index, entry in enumerate(section.get("entries", [])):
            title = entry.get("title") or entry.get("label") or "学习内容"
            status = SECTION_STATES.get(entry.get("state"), "未加入")
            if entry.get("kind") == "reference":
                status = "已读" if entry.get("state") == "completed" else "未读"
                item = u.button(title + " · " + status,
                    lambda w, cid=entry["content_id"]: self.show_kana_detail(cid), multiline=True)
                configure_study_ranges(item, [(0, len(title))])
                children.append(self._path_feedback(item, "study_path_reference", entry))
                continue
            item = u.col(u.label(title, size=17, text_role="study" if entry.get("kind") == "kana" or entry.get("family") == "word" else "ui"), u.label(status, size=12, color=u.MUTED),
                         padding=10, margin_bottom=6, background_color=u.ENTRY_BACKGROUNDS[index % len(u.ENTRY_BACKGROUNDS)])
            children.append(self._path_feedback(item, "study_path_entry", entry))
        if references and any(entry.get("state") != "completed" for entry in references):
            partial = any(e.get("state") != "new" for e in references) and any(e.get("state") == "new" for e in references)
            children.append(u.button("已读已加入的参考内容" if partial else "已读本节参考内容",
                                     lambda w: self.mark_study_reference_read(identity, **route), primary=reference_only, multiline=True))
        self.screen("study_section", section["title"], *children)

    def mark_study_reference_read(self, identity, route_id=None):
        route = route_kwargs(route_id)
        self.study_path_service().mark_reference_read(identity, **route)
        # Re-render after a mutation; detail-page same-identity protection is
        # intentionally bypassed here, as in other refreshed detail controls.
        StudyPathUIMixin.show_study_section.__wrapped__(self, identity, **route)

    @page_view(replace=True)
    def show_study_reference(self, entry_id, route_id=None):
        """Present the actual reference inline, then return to the dispatcher.

        References are item-level (reference_read is shared by every route);
        route_id only selects which route's entry index resolves entry_id.
        """
        from .kana_audio import kana_audio_plan
        from .kana_diagrams import kana_diagram
        from .lesson_diagram_ui import diagram_widget
        from .presentation import payload
        from .romanization import kana_learning_romaji

        route = route_kwargs(route_id)
        entry = self.study_path_service().reference_entry(entry_id, **route)
        if entry["state"] != "added":
            self.continue_study_path()
            return
        self._study_reference_entry_id = entry_id
        item = self.repo.get_kana(entry["content_id"]) or {}
        script = entry.get("script", "hiragana")
        symbol = item.get(script) or entry.get("title") or entry["content_id"]
        children = [self._path_feedback(u.col(
            u.label("平假名" if script == "hiragana" else "片假名", size=12, color=u.MUTED),
            u.label(symbol, size=42, bold=True, text_role="study"),
            padding=12, margin_bottom=8, background_color=u.WHITE), "study_path_reference", entry)]
        plan = kana_audio_plan(item)
        romaji = kana_learning_romaji(item)
        if plan.playable:
            children.append(u.row(u.label(plan.label or romaji or "读音示范", size=16, flex=1),
                u.speaker_button(lambda w, text=plan.text: self.speak_text(text), "播放读音示范")))
        elif romaji:
            children.append(u.label(romaji, size=18, color=u.GREEN))
        diagram = kana_diagram(item, script)
        if diagram:
            children.append(diagram_widget(u, diagram))
        for note in dict.fromkeys(payload(item).get("special_context", item.get("special_context", []))):
            children.append(u.label(note, size=16))
        children.append(u.button("已了解，继续", lambda w: self.finish_study_reference(entry_id, **route), primary=True))
        self.screen("study_reference", "认识字形", *children)

    def finish_study_reference(self, entry_id, route_id=None):
        # A delayed or double-tapped old button cannot acknowledge the next item.
        if (self.page != "study_reference"
                or getattr(self, "_study_reference_entry_id", None) != entry_id):
            return
        self.study_path_service().mark_reference_entry_read(entry_id, **route_kwargs(route_id))
        self._study_reference_entry_id = None
        # Normal due work keeps its priority; never drain references recursively.
        self.continue_study_path()

    # ------------------------------------------------------------ 添加学习内容 (design 13 S2)
    # The page is admission.ui.add_page.AddPage and its business admission.service.AddContentService;
    # what is left here is the outer layer (C7): open a showing, mount it, and what follows 加入并继续,
    # 创建路线, an entry's detail and 改看<路线>. The app keeps only _study_add (the showing) and
    # _study_admission_value.

    def study_admission(self):
        """The add-content service and its caches for the current store, built on first use (design 13 S2).

        Replaced together with the store object, like study_arrangement; the
        home counts reach its question-plan preview through here too.
        """
        from .admission_runtime import current_admission
        self._study_admission_value = current_admission(self, getattr(self, "_study_admission_value", None))
        return self._study_admission_value

    @page_view()
    def show_study_path_add(self, widget=None, *, selection_key=None, route_id=None, **kwargs):
        """Selection tree of one route (default: the current route)."""
        from .admission.service import AddOperations
        service = self.study_admission().service
        session, model = service.open(route_id=route_id, selection_key=selection_key)
        operations = AddOperations(service, session)
        page = AddPage(model, operations,
                       live=lambda: self.page == "study_path_add" and self._study_add.page is page,
                       on_submit=self._submit_study_selection, on_save_route=self._save_study_route,
                       on_detail=self._show_study_selection_entry,
                       on_switch_route=lambda rid: self.show_study_path_add(route_id=rid),
                       alert=lambda message: self.alert(message))
        self._study_add = AddShowing(operations, page)
        self.screen("study_path_add", add_text.PAGE_TITLE, *page.widgets())

    def _rebase_study_selection(self):
        """Back (app.go_back), and a failed continue after 加入并继续: the page re-reads and redraws."""
        self._study_add.page.rebase()

    def _submit_study_selection(self, shown):
        """加入并继续 (``shown``: the page's display state); the shared _study_path_adding guards re-entry."""
        if getattr(self, "_study_path_adding", False):
            return
        from .admission.service import ENROLL_ERRORS, enrollment_failure
        self._study_path_adding = True
        try:
            showing = self._study_add
            result = showing.operations.submit(**shown)
            if result.message:
                self.alert(result.message)
            showing.page.redraw(result.model)
            if result.ok:
                try:
                    self.continue_study_path()
                except ENROLL_ERRORS as exc:
                    # As before, what follows the enrollment fails like the enrollment.
                    self.alert(enrollment_failure(exc).message)
                    self._rebase_study_selection()
        finally:
            self._study_path_adding = False

    def _save_study_route(self, title):
        """创建路线: the personal route from the selection, then its route page."""
        result = self._study_add.operations.save_route(title)
        if result.message:
            self.alert(result.message)
        if not result.ok:
            return
        self._study_path_notice = ROUTE_CREATED_NOTICE
        self.show_study_path(route_id=result.route_id)

    def _show_study_selection_entry(self, detail):
        kind, content_id = detail
        if kind == "kana":
            self.show_kana_detail(content_id)
        else:
            self.show_learning_detail(content_id)

    def advance_study_path(self, widget=None, **kwargs):
        self.add_next_study_sections(count=1)

    def add_next_study_sections(self, widget=None, *, count=1, route_id=None, **kwargs):
        if type(count) is not int or count < 1:
            self.alert("请输入大于 0 的小节数量。")
            return
        route = route_kwargs(route_id)
        sections = self.study_path_service().plan_next(count, **route)
        identities = [section if isinstance(section, str) else section["id"] for section in sections]
        self._add_study_sections(identities, **route)

    def _add_study_sections(self, identities, route_id=None):
        """Enroll whole sections of one route (default: the core route), then continue.

        When the continue reaches a due review instead of the new section,
        that page carries a one-shot "已加入「X」" notice (study_path_runtime).
        """
        if getattr(self, "_study_path_adding", False):
            return
        self._study_path_adding = True
        try:
            self._enroll_notice = None
            if identities:
                result = self.study_path_service().add_sections(list(identities), start_learning=True,
                                                                **route_kwargs(route_id))
                from .practice_round import restart
                restart(self.store, lane="main")
                from .study_path_runtime import enrollment_notice
                self._enroll_notice = enrollment_notice(result, identities)
            self.continue_study_path()
        except (ValueError, KeyError) as exc:
            self.alert(str(exc))
        except (sqlite3.Error, OSError):
            self.alert("未能保存本次选择，请重试。")
        finally:
            self._study_path_adding = False

    @page_view()
    def show_study_path_notes(self, widget=None, **kwargs):
        sources = (
            ("GENKI：词汇与语法并进", "https://genki3.japantimes.co.jp/en/intro/index.html", "23课、约1700基本词汇的初级教材实例，不是考试门槛。"),
            ("Marugoto：课程阶段", "https://marugoto.jpf.go.jp/en/about/", "国际交流基金的课程系列与阶段介绍。"),
            ("Irodori：生活场景与小课", "https://www.irodori.jpf.go.jp/en/about.html", "按生活场景和交际目标组织课程的官方示例。"),
            ("JLPT：各级能力要求", "https://www.jlpt.jp/e/about/levelsummary.html", "N5 至 N1 的阅读与听力能力描述。"),
        )
        self.screen("study_path_notes", "课程说明",
            u.label("先会用，再逐步扩展", size=20, bold=True, color=u.GREEN),
            u.label("阶段组织整体进度，单元围绕相关表达，小节分配一次可以加入的内容。可以一天加入多节，也可以随时暂停。"),
            u.label("词汇与语法一起推进", size=17, bold=True),
            u.label("相关词汇小节穿插在语法附近；共享例句把词义和用法联系起来。同一词义再次出现时沿用原学习记录。词典里可查的词不等于都已有正式教案。"),
            u.label("词数不是语法的通关条件", size=17, bold=True),
            u.label("初级内容建设参考约1500—2000词的教材规模，逐步补充完整教案；这不是已收录数量或考试官方要求。更高阶段按阅读、交流任务继续补词。"),
            u.label("加入不等于掌握", size=17, bold=True),
            u.label("新内容先看介绍、跟着做，再独立练习。初学完成后，仍按各项原有的复习时间巩固。"),
            u.label("保留已有学习", size=17, bold=True),
            u.label("加入小节只追加尚未加入的内容。已学记录、未完成题目、草稿和到期时间继续保留；必要的前置内容会先安排。"),
            u.label("考试级别是组织参考", size=17, bold=True),
            u.label("路线结合常见课程顺序、使用场景与考试能力描述。考试名称不表示官方完整词表或语法表。"),
            u.label("编排参考", size=17, bold=True),
            *[u.button(title, lambda w, url=url, title=title, summary=summary:
                       self.open_url(url, title=title, summary=summary), compact=True, multiline=True)
              for title, url, summary in sources])
