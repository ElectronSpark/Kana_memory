"""A quiet, resumable course route over the existing learning queues.

Sections select content. They do not own review schedules or duplicate cards.
"""
from __future__ import annotations

from copy import deepcopy
import sqlite3

from .navigation import page_view
from .typography import configure_study_ranges
from .tree_checkbox import tree_checkbox, update_tree_checkbox


def ui():
    from . import ui_components as app
    return app


def progress_text(progress, *, sections=True):
    unit = "节" if sections else "项"
    total = int(progress.get("total", 0))
    added = int(progress.get("added", 0))
    completed = int(progress.get("completed", 0))
    partial = int(progress.get("partial", 0)) if sections else 0
    result = f"初学完成 {completed}/{total} {unit} · 未加入 {max(0, total - added - partial)} {unit}"
    return result + (f" · 部分加入 {partial} {unit}" if partial else "")


SECTION_STATES = {
    "new": "未加入", "partial": "部分已加入", "added": "已加入",
    "started": "学习中", "completed": "初学完成",
}


def path_display_snapshot(value, kind):
    """Keep the data rendered at this hierarchy level, not its hidden subtree.

    Progress summaries are already derived from the learner's records. A stage
    button does not display every descendant card, so exporting those cards
    both overstates the page's scope and exhausts feedback capture limits.
    Actual entries on a section detail page retain their content identities;
    the ordinary feedback collector still captures their individual progress.
    """
    fields = ("id", "title", "stage_id", "stage_title", "unit_id", "unit_title",
              "state", "progress", "new_count", "optional")
    if kind in {"study_path_entry", "study_path_reference"}:
        fields += ("content_id", "kind", "script", "family", "domain", "label")
    result = {key: deepcopy(value[key]) for key in fields if key in value}
    if kind == "study_path_section":
        entries = value.get("entries", [])
        result["entry_count"] = len(entries)
        result["reference_only"] = bool(entries) and all(entry.get("kind") == "reference" for entry in entries)
    return result


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

    def _path_next_controls(self, overview, *, compact=False):
        u = ui()
        next_section = overview.get("next_section")
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
                lambda w, identity=next_section["id"]: self._add_study_sections((identity,)), primary=not can_continue, multiline=True)
            children.append(self._path_feedback(button, "study_path_section", next_section))
        else:
            children.append(u.label("主线小节都已加入，可继续复习或选择拓展内容。", size=15, color=u.MUTED))
            if not can_continue:
                children.append(u.button("继续已加入的学习", self.continue_study_path, primary=True, multiline=True))
        children.append(u.button("添加内容", self.show_study_path_add, compact=True))
        return children

    @page_view()
    def show_study_path(self, widget=None, **kwargs):
        u = ui()
        overview = self.study_path_service().overview()
        children = self._path_next_controls(overview)
        for stage in overview.get("stages", []):
            entry = u.button(stage["title"] + "\n" + progress_text(stage.get("progress", {})),
                lambda w, identity=stage["id"]: self.show_study_stage(identity), multiline=True)
            children.append(self._path_feedback(entry, "study_path_stage", stage))
        children.append(u.button("自由查阅学习库", self.show_learning_library, compact=True))
        children.append(u.button("课程说明", self.show_study_path_notes, compact=True))
        self.screen("study_path", "学习路线", *children)

    def _path_stage(self, identity):
        return next((stage for stage in self.study_path_service().overview().get("stages", []) if stage["id"] == identity), None)

    @page_view(detail=True)
    def show_study_stage(self, identity):
        u = ui()
        stage = self._path_stage(identity)
        if stage is None:
            self.show_study_path()
            return
        children = [u.label(progress_text(stage.get("progress", {})), size=14, color=u.MUTED)]
        for unit in stage.get("units", []):
            entry = u.button(unit["title"] + "\n" + progress_text(unit.get("progress", {})),
                lambda w, sid=identity, uid=unit["id"]: self.show_study_unit(uid, sid), multiline=True)
            children.append(self._path_feedback(entry, "study_path_unit", unit))
        self.screen("study_stage", stage["title"], *children)

    @page_view(detail=True)
    def show_study_unit(self, unit_id, stage_id):
        u = ui()
        stage = self._path_stage(stage_id)
        unit = next((unit for unit in (stage or {}).get("units", []) if unit["id"] == unit_id), None)
        if unit is None:
            self.show_study_path()
            return
        self._study_path_unit = (stage_id, unit_id)
        children = [u.label(progress_text(unit.get("progress", {})), size=14, color=u.MUTED)]
        sections = unit.get("sections", [])
        remaining = [section for section in sections if section.get("new_count", 0) > 0]
        if remaining:
            children.append(u.button("选择本单元内容", lambda w: self.show_study_path_add(selection_key=("unit", unit_id)), primary=True, multiline=True))
        for section in sections:
            entry = u.button(self._path_section_caption(section),
                lambda w, identity=section["id"]: self.show_study_section(identity), multiline=True)
            children.append(self._path_feedback(entry, "study_path_section", section))
        self.screen("study_unit", unit["title"], *children)

    @page_view(detail=True)
    def show_study_section(self, identity):
        u = ui()
        section = self.study_path_service().section(identity)
        self._study_path_section_id = identity
        references = [entry for entry in section.get("entries", []) if entry.get("kind") == "reference"]
        reference_only = bool(references) and len(references) == len(section.get("entries", []))
        progress = progress_text(section.get("progress", {}), sections=False)
        if reference_only:
            progress = progress.replace("初学完成", "已读")
        children = [u.label(progress, size=14, color=u.MUTED)]
        if section.get("optional"):
            children.append(u.label("拓展小节，可按需加入。", size=13, color=u.MUTED))
        if reference_only:
            children.append(u.label("只作阅读参考，不加入记忆卡。", size=13, color=u.MUTED))
            if section.get("state") == "completed":
                children.append(u.button("继续学习", self.continue_study_path, primary=True))
                following = self.study_path_service().overview().get("next_section")
                if following:
                    children.append(u.button("学习下一小节", lambda w, sid=following["id"]: self._add_study_sections((sid,))))
        elif section.get("new_count", 0):
            children.append(u.button("选择本节内容", lambda w: self.show_study_path_add(selection_key=("section", identity)), primary=True))
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
                                     lambda w: self.mark_study_reference_read(identity), primary=reference_only, multiline=True))
        self.screen("study_section", section["title"], *children)

    def mark_study_reference_read(self, identity):
        self.study_path_service().mark_reference_read(identity)
        # Re-render after a mutation; detail-page same-identity protection is
        # intentionally bypassed here, as in other refreshed detail controls.
        StudyPathUIMixin.show_study_section.__wrapped__(self, identity)

    @page_view(replace=True)
    def show_study_reference(self, entry_id):
        """Present the actual reference inline, then return to the dispatcher."""
        from .kana_audio import kana_audio_plan
        from .kana_diagrams import kana_diagram
        from .lesson_diagram_ui import diagram_widget
        from .presentation import payload
        from .romanization import kana_learning_romaji

        u = ui()
        entry = self.study_path_service().reference_entry(entry_id)
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
        children.append(u.button("已了解，继续", lambda w: self.finish_study_reference(entry_id), primary=True))
        self.screen("study_reference", "认识字形", *children)

    def finish_study_reference(self, entry_id):
        # A delayed or double-tapped old button cannot acknowledge the next item.
        if (self.page != "study_reference"
                or getattr(self, "_study_reference_entry_id", None) != entry_id):
            return
        self.study_path_service().mark_reference_entry_read(entry_id)
        self._study_reference_entry_id = None
        # Normal due work keeps its priority; never drain references recursively.
        self.continue_study_path()

    @page_view()
    def show_study_path_add(self, widget=None, *, selection_key=None, **kwargs):
        with self.study_path_service().read_operation() as read:
            self._show_study_path_add(read, selection_key=selection_key)

    def _show_study_path_add(self, read, *, selection_key=None):
        from .study_path_selection import StudyPathSelection
        u = ui()
        overview = read.overview()
        self._study_selection = StudyPathSelection(overview)
        self._study_expanded = set()
        self._study_shortcut_open = False
        self._study_prerequisites_open = False
        if selection_key is None and overview.get("next_section"):
            selection_key = ("section", overview["next_section"]["id"])
        if selection_key:
            self._study_selection.select(selection_key)
            # Reveal the selected branch while leaving unrelated groups compact.
            for stage in overview["stages"]:
                for unit in stage["units"]:
                    for section in unit["sections"]:
                        keys = (("stage", stage["id"]), ("unit", unit["id"]), ("section", section["id"]))
                        if selection_key in keys:
                            self._study_expanded.update(keys[:keys.index(selection_key)])
            self._study_expanded.add(selection_key)
        self._study_selection_summary = u.label("", size=16, bold=True, color=u.GREEN)
        self._study_selection_workload = u.label("", size=13, color=u.MUTED)
        self._study_selection_action = u.button("加入并继续", self._submit_study_selection, primary=True)
        self._study_selection_tree = u.col()
        self._study_selection_dependencies = u.col()
        self._study_selection_shortcut = u.col()
        self.study_section_count = u.toga.TextInput(value="1", placeholder="小节数量", style=u.Pack(margin_bottom=6))
        self.study_section_preview = u.col()
        self.study_section_count.on_change = lambda w: self.refresh_study_path_add()
        self._study_selection_plan = None
        # Populate the detached tree before screen() attaches it to the native
        # window. Mounted per-row additions trigger whole-window Toga layouts.
        self._render_study_selection_tree()
        self._refresh_study_selection(preparing=True, read=read)
        self.screen("study_path_add", "添加学习内容",
            self._study_selection_summary, self._study_selection_workload, self._study_selection_action,
            u.row(u.button("清空选择", self._clear_study_selection, flex=1, compact=True),
                  u.button("按顺序选多节", self._toggle_study_shortcut, flex=1, compact=True, multiline=True)),
            self._study_selection_shortcut,
            self._study_selection_dependencies,
            self._study_selection_tree)

    def _study_selection_nodes(self):
        for stage in self._study_selection.overview["stages"]:
            yield ("stage", stage["id"]), stage, 0
            for unit in stage["units"]:
                yield ("unit", unit["id"]), unit, 1
                for section in unit["sections"]:
                    yield ("section", section["id"]), section, 2
                    for entry in section["entries"]:
                        yield ("entry", entry["id"]), entry, 3

    def _study_selection_description(self, key, value, counts):
        if key[0] == "entry":
            state = value.get("state", "new")
            if value.get("kind") == "reference":
                return "阅读参考 · " + ("已读" if state == "completed" else "未加入" if state == "new" else "已加入")
            return SECTION_STATES.get(state, "未加入")
        result = f"已加入 {counts['added_count']}/{counts['total_count']} 项"
        if counts["new_count"]:
            result += f" · 可选 {counts['new_count']} 项"
        if counts["selected_count"]:
            result += f"\n本次已选 {counts['selected_count']} 项"
        return result

    def _render_study_selection_tree(self):
        u = ui()
        self._study_node_controls = {}
        blocks = []
        parents = []
        for key, value, depth in self._study_selection_nodes():
            parents = parents[:depth]
            visible = all(parent in self._study_expanded for parent in parents)
            parents.append(key)
            if not visible:
                continue
            title = value.get("title") or value.get("label") or "学习内容"
            check = tree_checkbox(u, lambda w, k=key: self._toggle_study_selection(*k))
            description = u.label("", size=12, color=u.MUTED, margin_bottom=3)
            if key[0] == "entry":
                heading = u.button(title, lambda w, e=deepcopy(value): self._show_study_selection_entry(e),
                    multiline=True, text_role="study" if value.get("kind") in {"kana", "reference"}
                    or value.get("family") == "word" else "ui", margin_bottom=0)
            else:
                heading = u.button(("▾ " if key in self._study_expanded else "▸ ") + title,
                    lambda w, k=key: self._expand_study_selection(*k), multiline=True, margin_bottom=0)
            if u.current_platform == "android":
                from android.view import Gravity
                # Keep native horizontal padding around large-font CJK glyphs.
                # Tree captions read from the same edge on every wrapped line.
                heading._impl.native.setGravity(Gravity.START | Gravity.CENTER_VERTICAL)
            block = u.row(check, u.col(heading, description, flex=1), align_items="center",
                          margin_top=4, margin_right=4, margin_left=4 + depth * 12, margin_bottom=5,
                          background_color=u.ENTRY_BACKGROUNDS[depth % len(u.ENTRY_BACKGROUNDS)])
            self._study_node_controls[key] = (check, description, block, value)
            self._update_study_selection_node(key)
            blocks.append(block)
        self._study_selection_tree.clear()
        self._study_selection_tree.add(*blocks)

    def _update_study_selection_node(self, key):
        """Project one model read into all consumers without redundant setters."""
        u = ui()
        check, description, block, value = self._study_node_controls[key]
        status = self._study_selection.node_status(key)
        counts, state = status.counts(), status.state
        title = value.get("title") or value.get("label") or "学习内容"
        if (getattr(check, "_study_render_status", None) != (status, title)
                or bool(check.value) != (getattr(check, "_study_selection_state", None) in {"all", "added"})
                or check.enabled != bool(status.new_count)):
            update_tree_checkbox(check, u, state, counts, title)
            check._study_render_status = (status, title)
        check._study_selection_key = key
        text = self._study_selection_description(key, value, counts)
        if description.text != text:
            description.text = text
        block._feedback_fragment = {"kind": "study_path_selection_node", "node_key": list(key),
            "path_snapshot": path_display_snapshot(value, "study_path_" + key[0]),
            "selection_state": state, "counts": counts, "expanded": key in self._study_expanded}

    def _refresh_study_selection(self, *, rebase=False, preparing=False, read=None):
        """Revalidate after selection/data changes, not after display-only toggles."""
        if self.page != "study_path_add" and not preparing:
            return
        if read is None:
            with self.study_path_service().read_operation() as read:
                return self._refresh_study_selection(rebase=rebase, preparing=preparing, read=read)
        read.check_active()
        if rebase:
            self._study_selection.refresh(read.overview())
            self._render_study_selection_tree()
            if self._study_shortcut_open:
                self.refresh_study_path_add(read=read)
        if not preparing and not rebase:
            for key in self._study_node_controls:
                self._update_study_selection_node(key)
        try:
            plan = read.preview_entries(self._study_selection.selected_entry_ids)
        except (ValueError, KeyError) as exc:
            self._study_selection_plan = None
            self._study_selection_action.enabled = False
            self._study_selection_summary.text = str(exc)
            self._study_selection_workload.text = ""
            self._study_selection_dependencies.clear()
            self._study_selection_tree._feedback_fragment = {
                "kind": "study_path_selection", "version": 1, "valid": False,
                "selected_entry_ids": self._study_selection.selected_entry_ids,
                "error": str(exc)}
            self._update_study_selection_feedback()
            return
        self._study_selection_plan = plan
        self._study_selection_summary.text = plan["summary"]
        from .admission_workload import admission_snapshot, admission_text
        workload = admission_snapshot(self, plan, overview=self._study_selection.overview, read=read)
        self._study_selection_workload.text = admission_text(workload)
        self._study_selection_workload._feedback_fragment = {
            "kind": "admission_workload", "version": 1, "snapshot": workload}
        self._study_selection_action.enabled = bool(plan["total_count"])
        self._render_study_selection_dependencies()
        self._update_study_selection_feedback()

    def _render_study_selection_dependencies(self):
        u = ui()
        plan = self._study_selection_plan
        blocks = []
        if plan is None:
            self._study_selection_dependencies.clear()
            return
        if plan["prerequisite_count"]:
            blocks.append(u.button(
                ("收起" if self._study_prerequisites_open else "查看") + f"前置内容（{plan['prerequisite_count']} 项）",
                self._toggle_study_prerequisites, compact=True))
            if self._study_prerequisites_open:
                identities = set(plan["prerequisite_entry_ids"])
                for key, value, depth in self._study_selection_nodes():
                    if key[0] == "entry" and key[1] in identities:
                        blocks.append(u.label(value.get("title", "学习内容"), size=14))
        self._study_selection_dependencies.clear()
        self._study_selection_dependencies.add(*blocks)

    def _update_study_selection_feedback(self):
        plan = self._study_selection_plan
        display = {
            "expanded_node_ids": [list(key) for key in sorted(self._study_expanded)],
            "visible_node_ids": [list(key) for key in self._study_node_controls],
            "shortcut_open": self._study_shortcut_open,
            "prerequisites_open": self._study_prerequisites_open,
        }
        if plan is None:
            self._study_selection_tree._feedback_fragment.update(display)
            return
        self._study_selection_tree._feedback_fragment = {"kind": "study_path_selection", "version": 1,
            "valid": True, "snapshot_scope": "selection_and_visible_nodes",
            "selected_entry_ids": list(plan["selected_entry_ids"]),
            "prerequisite_entry_ids": list(plan["prerequisite_entry_ids"]),
            "selected_count": plan["selected_count"], "prerequisite_count": plan["prerequisite_count"],
            "total_count": plan["total_count"], "section_count": plan["section_count"],
            **display}

    def _toggle_study_selection(self, kind, identity):
        if self.page != "study_path_add":
            return
        self._study_selection.toggle((kind, identity))
        self._refresh_study_selection()

    def _clear_study_selection(self, widget=None):
        self._study_selection.clear()
        self._refresh_study_selection()

    def _expand_study_selection(self, kind, identity):
        key = (kind, identity)
        self._study_expanded.symmetric_difference_update({key})
        self._render_study_selection_tree()
        self._update_study_selection_feedback()

    def _show_study_selection_entry(self, entry):
        if entry["kind"] in {"kana", "reference"}:
            self.show_kana_detail(entry["content_id"])
        else:
            self.show_learning_detail(entry["content_id"])

    def _toggle_study_prerequisites(self, widget=None):
        self._study_prerequisites_open = not self._study_prerequisites_open
        self._render_study_selection_dependencies()
        self._update_study_selection_feedback()

    def _toggle_study_shortcut(self, widget=None):
        u = ui()
        self._study_shortcut_open = not self._study_shortcut_open
        self._study_selection_shortcut.clear()
        if self._study_shortcut_open:
            self._study_selection_shortcut.add(self.study_section_count,
                u.button("加入勾选", self.add_study_sections_from_input, compact=True), self.study_section_preview)
            self.refresh_study_path_add()
        self._update_study_selection_feedback()

    def _study_section_count_value(self):
        value = str(self.study_section_count.value or "").strip()
        if not value.isdecimal() or int(value) < 1:
            raise ValueError("请输入大于 0 的小节数量。")
        return int(value)

    def refresh_study_path_add(self, *, read=None):
        u = ui()
        self.study_section_preview.clear()
        self.study_section_preview._feedback_fragment = {
            "kind": "study_path_add_preview", "snapshot_scope": "displayed_batch_preview",
            "requested_text": str(self.study_section_count.value or ""), "valid": False}
        try:
            count = self._study_section_count_value()
        except ValueError:
            self.study_section_preview.add(u.label("请输入大于 0 的小节数量。", size=13, color=u.MUTED))
            return
        sections = read.plan_next(count) if read is not None else self.study_path_service().plan_next(count)
        visible = [self.study_path_service().section(section) if isinstance(section, str) else section
                   for section in sections[:5]]
        self.study_section_preview._feedback_fragment.update({"valid": True,
            "requested_count": count, "planned_count": len(sections),
            "selection_rule": "next_new_main_sections_in_catalogue_order",
            "visible_sections": [path_display_snapshot(section, "study_path_section") for section in visible],
            "additional_count": max(0, len(sections) - len(visible))})
        self.study_section_preview.add(u.label(f"将勾选 {len(sections)} 节", size=14))
        for section in visible:
            self.study_section_preview.add(u.label(section["title"], size=13))
        if len(sections) > 5:
            self.study_section_preview.add(u.label(f"另有 {len(sections) - 5} 节", size=13, color=u.MUTED))
        if not sections:
            self.study_section_preview.add(u.label("主线小节都已加入。", size=14, color=u.MUTED))

    def add_study_sections_from_input(self, widget=None, **kwargs):
        if self.page != "study_path_add":
            return
        try:
            count = self._study_section_count_value()
        except ValueError as exc:
            self.alert(str(exc))
            return
        with self.study_path_service().read_operation() as read:
            for section in read.plan_next(count):
                self._study_selection.select(("section", section if isinstance(section, str) else section["id"]))
            self._refresh_study_selection(read=read)

    def _submit_study_selection(self, widget=None, **kwargs):
        if self.page != "study_path_add" or getattr(self, "_study_path_adding", False):
            return
        self._refresh_study_selection(rebase=True)
        if not self._study_selection_action.enabled:
            return
        self._study_path_adding = True
        try:
            self.study_path_service().add_entries(self._study_selection.selected_entry_ids, start_learning=True)
            self.continue_study_path()
        except (ValueError, KeyError) as exc:
            self.alert(str(exc))
            self._refresh_study_selection(rebase=True)
        except (sqlite3.Error, OSError):
            self.alert("未能保存本次选择，请重试。")
            self._refresh_study_selection(rebase=True)
        finally:
            self._study_path_adding = False

    def advance_study_path(self, widget=None, **kwargs):
        self.add_next_study_sections(count=1)

    def add_next_study_sections(self, widget=None, *, count=1, **kwargs):
        if type(count) is not int or count < 1:
            self.alert("请输入大于 0 的小节数量。")
            return
        sections = self.study_path_service().plan_next(count)
        identities = [section if isinstance(section, str) else section["id"] for section in sections]
        self._add_study_sections(identities)

    def _add_study_sections(self, identities):
        if getattr(self, "_study_path_adding", False):
            return
        self._study_path_adding = True
        try:
            if identities:
                self.study_path_service().add_sections(list(identities), start_learning=True)
            self.continue_study_path()
        except (ValueError, KeyError) as exc:
            self.alert(str(exc))
        except (sqlite3.Error, OSError):
            self.alert("未能保存本次选择，请重试。")
        finally:
            self._study_path_adding = False

    @page_view()
    def show_study_path_notes(self, widget=None, **kwargs):
        u = ui()
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
