"""The page 添加学习内容 (design 13 S2, C4): a display model and the operations of one showing in, widgets out.

AddPage owns every widget of the page and its display-only state: which
nodes are expanded, whether 按顺序选多节 and the prerequisites are open, the
route-save form, the typed section count and route title. A tick, 清空选择,
加入勾选, the section-count preview and the prerequisites line go to the
operations object (the add-content service bound to this showing) and the
page draws what comes back. 加入并继续, 创建路线, an entry's detail page,
改看<路线> and alerts are callbacks to the outer layer, which decides what
follows (C7). ``live()`` (this page is on screen and is the app's current add
page) guards exactly the actions that were guarded before the page moved
here; expanding, the two toggles, typing, the detail and the route switch
have no guard, and a 清空选择 of a page that was left still empties its choice.
The page sees no app object, store or service, and decides nothing the
model does not carry. The two lines (the first part of the two-line design)
are drawn from the plan's LinesModel under the main button: each line's next
section, the mark 建议先加, the readiness line and 一并加入 (a tick, guarded
like one); 只勾选词汇 / 只勾选语法 sit beside 加入勾选.
"""
from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Protocol

import kanamemory.ui_components as u
from kanamemory.tree_checkbox import tree_checkbox, update_tree_checkbox
from ..display import (ADD_LINE, ADD_MISSING, ADD_PREVIEWED, CANCEL, CLEAR, CREATE_ROUTE, ROUTE_SAVE_NOTE,
                       ROUTE_TITLE_PLACEHOLDER, SAVE_ROUTE, SECTION_COUNT_PLACEHOLDER, SHORTCUT, SUBMIT, AddDelta,
                       AddPageModel, LinesModel, NodeModel, PlanModel, Prerequisites, ShortcutPreview)

Key = tuple[str, str]
_ASK = object()   # draw a plan without a prerequisites line: ask the operations for it


class Operations(Protocol):
    """What the page asks of the service bound to its session (admission.service.AddOperations)."""

    def rows(self, expanded: Collection[Key]) -> tuple[NodeModel, ...]: ...
    def toggle(self, key: Key) -> AddDelta: ...
    def clear(self, *, replan: bool = True) -> AddDelta | None: ...
    def select_next(self, count_text: str, line: str | None = None) -> AddDelta: ...
    def select_entries(self, entry_ids) -> AddDelta: ...
    def preview_sections(self, count_text: str) -> ShortcutPreview: ...
    def prerequisites(self, *, open: bool) -> Prerequisites | None: ...
    def selection_feedback(self) -> dict | None: ...
    def refresh(self, *, expanded: Collection[Key], preview_text: str | None = None,
                prerequisites_open: bool = False) -> AddPageModel: ...


class AddPage:
    """One showing of 添加学习内容; ``widgets()`` are the children the outer layer mounts."""

    def __init__(self, model: AddPageModel, operations: Operations, *, live: Callable[[], bool],
                 on_submit: Callable[[dict], object], on_save_route: Callable[[str], object],
                 on_detail: Callable[[tuple[str, str]], object], on_switch_route: Callable[[str], object],
                 alert: Callable[[str], object]):
        """``on_submit(shown)``: 加入并继续 with the page's display state (keyword arguments of the service's
        submit); ``on_save_route(title)``: 创建路线; ``on_detail(detail)``: an entry's NodeModel.detail;
        ``on_switch_route(route_id)``: 改看<路线>."""
        self._ops, self._live, self._alert = operations, live, alert
        self._on_submit, self._on_save_route = on_submit, on_save_route
        self._on_detail, self._on_switch_route = on_detail, on_switch_route
        self._route_switch, self._on_route = model.route_switch, model.on_route
        # Display state.
        self.expanded = set(model.expanded)
        self.shortcut_open = self.prerequisites_open = self.route_save_open = False
        # Widgets, in the page's order.
        self.summary = u.label("", size=16, bold=True, color=u.GREEN)
        self.workload = u.label("", size=13, color=u.MUTED)
        self.routes = u.label("", size=13, color=u.MUTED)   # the closure line, mounted on a non-core route only
        self.action = u.button(SUBMIT, self.submit, primary=True)
        self.tree = u.col()
        self.lines = u.col()     # each line's next section (two-line design)
        self.dependencies = u.col()
        self.shortcut = u.col()
        self.section_count = u.toga.TextInput(value="1", placeholder=SECTION_COUNT_PLACEHOLDER,
                                              style=u.Pack(margin_bottom=6))
        self.section_preview = u.col()
        self.section_count.on_change = lambda w: self.preview_sections()
        self.route_save = u.button(SAVE_ROUTE, self.toggle_route_save, compact=True)
        self.route_save_form = u.col()
        self.route_title = None              # the title field, once the form was opened
        self.node_controls: dict = {}        # key -> (check, description, block, NodeModel), in display order
        # The tree is filled while detached: mounted per-row additions trigger whole-window layouts.
        self._draw_tree(model.nodes)
        self._draw_plan(model.plan, model.prerequisites)

    def widgets(self) -> list:
        switch = self._switch_widgets()
        routes = [self.routes] if self._on_route else []
        return [*switch, self.summary, *routes, self.workload, self.action, self.lines,
                u.row(u.button(CLEAR, self.clear, flex=1, compact=True),
                      u.button(SHORTCUT, self.toggle_shortcut, flex=1, compact=True, multiline=True)),
                self.shortcut, self.dependencies, self.tree, self.route_save, self.route_save_form]

    def shown(self) -> dict:
        """The display state the service needs for a rebase or 加入并继续."""
        return dict(expanded=self.expanded, preview_text=self._count_text() if self.shortcut_open else None,
                    prerequisites_open=self.prerequisites_open)

    # ------------------------------------------------------------ what the learner does
    def tick(self, key: Key) -> None:
        if not self._live():
            return
        delta = self._ops.toggle(key)
        self._draw_delta(delta)
        if key in self.node_controls and all(row.key != key for row in delta.nodes):
            self._draw_row(key)   # the pressed box flipped natively although its state stayed: paint it back

    def expand(self, key: Key) -> None:
        self.expanded.symmetric_difference_update({key})
        self._draw_tree(self._ops.rows(self.expanded))
        self._draw_feedback()

    def clear(self, widget=None) -> None:
        if not self._live():
            self._ops.clear(replan=False)   # a 清空选择 of a page that was left empties the choice only (step A)
            return
        self._draw_delta(self._ops.clear())

    def toggle_shortcut(self, widget=None) -> None:
        self.shortcut_open = not self.shortcut_open
        self.shortcut.clear()
        if self.shortcut_open:
            self.shortcut.add(self.section_count, u.button(ADD_PREVIEWED, self.add_previewed_sections, compact=True),
                              u.row(*(u.button(ADD_LINE[line], lambda w, l=line: self.add_line_sections(l), flex=1,
                                               compact=True, margin_right=8 if line == "word" else 0)
                                      for line in ADD_LINE)),
                              self.section_preview)
            self.preview_sections()
        self._draw_feedback()

    def preview_sections(self) -> None:
        """The section count changed (or the shortcut opened)."""
        self._draw_preview(self._ops.preview_sections(self._count_text()))

    def add_previewed_sections(self, widget=None) -> None:
        if not self._live():
            return
        delta = self._ops.select_next(self._count_text())
        if delta.rejection:
            self._alert(delta.rejection)
            return
        self._draw_delta(delta)

    def add_line_sections(self, line: str) -> None:
        """只勾选词汇 / 只勾选语法: the next N sections of one line (N: the section count field)."""
        if not self._live():
            return
        delta = self._ops.select_next(self._count_text(), line)
        if delta.rejection:
            self._alert(delta.rejection)
            return
        self._draw_delta(delta)

    def add_missing(self, entry_ids) -> None:
        """一并加入: tick the lessons a line's next section misses."""
        if not self._live():
            return
        self._draw_delta(self._ops.select_entries(tuple(entry_ids)))

    def toggle_prerequisites(self, widget=None) -> None:
        self.prerequisites_open = not self.prerequisites_open
        self._draw_prerequisites()
        self._draw_feedback()

    def submit(self, widget=None) -> None:
        if self._live():
            self._on_submit(self.shown())

    def toggle_route_save(self, widget=None) -> None:
        if not self._live():
            return
        self.route_save_form.clear()
        self.route_save_open = not self.route_save_open
        if not self.route_save_open:
            return
        self.route_title = u.toga.TextInput(placeholder=ROUTE_TITLE_PLACEHOLDER, style=u.Pack(margin_bottom=6))
        u.use_done_key(self.route_title)
        self.route_save_form.add(
            u.label(ROUTE_SAVE_NOTE, size=12, color=u.MUTED),
            self.route_title,
            u.row(u.button(CREATE_ROUTE, self.save_route, primary=True, flex=1, compact=True, margin_right=8),
                  u.button(CANCEL, self.toggle_route_save, flex=1, compact=True)))

    def save_route(self, widget=None) -> None:
        if self._live():
            self._on_save_route(str(getattr(self.route_title, "value", "") or ""))

    def open_detail(self, detail: tuple[str, str]) -> None:
        self._on_detail(detail)

    def switch_route(self, route_id: str) -> None:
        self._on_switch_route(route_id)

    # ------------------------------------------------------------ what the outer layer asks
    def rebase(self) -> None:
        """Back, and a failed continue after 加入并继续: re-read and redraw the whole page."""
        if self._live():
            self.redraw(self._ops.refresh(**self.shown()))

    def redraw(self, model: AddPageModel) -> None:
        """A whole page model (a rebase): the tree, the open preview and the plan."""
        self._draw_tree(model.nodes)
        if model.preview is not None:
            self._draw_preview(model.preview)
        self._draw_plan(model.plan, model.prerequisites)

    # ------------------------------------------------------------ drawing
    def _switch_widgets(self) -> list:
        """The route line at the top; [] for the core route alone."""
        switch = self._route_switch
        if switch is None:
            return []
        return [u.label(switch.label, size=14, bold=True),
                *(u.button(choice.caption, lambda w, r=choice.route_id: self.switch_route(r), compact=True,
                           multiline=True) for choice in switch.choices)]

    def _draw_tree(self, rows: tuple[NodeModel, ...]) -> None:
        self.node_controls = {}
        blocks = [self._row_block(row) for row in rows]
        self.tree.clear()
        self.tree.add(*blocks)

    def _row_block(self, row: NodeModel):
        check = tree_checkbox(u, lambda w, k=row.key: self.tick(k))
        description = u.label("", size=12, color=u.MUTED, margin_bottom=3)
        if row.detail is not None:
            heading = u.button(row.title, lambda w, d=row.detail: self.open_detail(d), multiline=True,
                               text_role="study" if row.study_text else "ui", margin_bottom=0, start_aligned=True)
        else:
            heading = u.button(("▾ " if row.key in self.expanded else "▸ ") + row.title,
                               lambda w, k=row.key: self.expand(k), multiline=True, margin_bottom=0, start_aligned=True)
        block = u.row(check, u.col(heading, description, flex=1), align_items="center",
                      margin_top=4, margin_right=4, margin_left=4 + row.depth * 12, margin_bottom=5,
                      background_color=u.ENTRY_BACKGROUNDS[row.depth % len(u.ENTRY_BACKGROUNDS)])
        self.node_controls[row.key] = (check, description, block, row)
        self._draw_row(row.key)
        return block

    def _draw_row(self, key: Key, row: NodeModel | None = None) -> None:
        """Project one row model into its checkbox, description and feedback without redundant setters."""
        check, description, block, shown = self.node_controls[key]
        if row is None:
            row = shown
        else:
            self.node_controls[key] = (check, description, block, row)
        if (getattr(check, "_study_render_status", None) != (row.status, row.title)
                or bool(check.value) != (getattr(check, "_study_selection_state", None) in {"all", "added"})
                or check.enabled != row.enabled):
            update_tree_checkbox(check, u, row.state, row.counts(), row.title)
            check._study_render_status = (row.status, row.title)
        check._study_selection_key = key
        if description.text != row.description:
            description.text = row.description
        block._feedback_fragment = row.feedback(key in self.expanded)

    def _draw_delta(self, delta: AddDelta) -> None:
        """Repaint only the drawn rows the delta names, then the plan."""
        for row in delta.nodes:
            if row.key in self.node_controls:
                self._draw_row(row.key, row)
        self._draw_plan(delta.plan)

    def _draw_plan(self, plan: PlanModel, prerequisites=_ASK) -> None:
        """``prerequisites``: the line a whole page model brought; a delta asks for it."""
        self.route_save.enabled = plan.can_save_route
        if not plan.valid:
            self.action.enabled = False
            self.summary.text = plan.summary
            self.workload.text = ""
            self.dependencies.clear()
            self.lines.clear()
            self.tree._feedback_fragment = dict(plan.feedback)
            self._draw_feedback()
            return
        self.summary.text = plan.summary
        if plan.routes_line is not None:
            self.routes.text = plan.routes_line
        self.workload.text = plan.workload_text
        self.workload._feedback_fragment = plan.workload_feedback
        self.action.enabled = plan.can_submit
        self._draw_lines(plan.lines)
        self._draw_prerequisites(prerequisites)
        self._draw_feedback()

    def _draw_lines(self, lines: LinesModel | None) -> None:
        blocks = []
        for row in (lines.rows if lines is not None else ()):
            blocks.append(u.label(row.text, size=14, bold=row.suggested, color=u.GREEN if row.suggested else u.INK))
            if row.readiness:
                blocks.append(u.label(row.readiness, size=12, color=u.MUTED))
            if row.missing:
                blocks.append(u.label(row.missing, size=12, color=u.MUTED))
                blocks.append(u.button(ADD_MISSING, lambda w, ids=row.missing_ids: self.add_missing(ids), compact=True))
        if lines is not None and lines.suggestion:
            blocks.append(u.label(lines.suggestion, size=14, bold=True, color=u.GREEN))
        if lines is not None and lines.reason:
            blocks.append(u.label(lines.reason, size=12, color=u.MUTED))
        self.lines.clear()
        if blocks:
            self.lines.add(*blocks)

    def _draw_prerequisites(self, prerequisites=_ASK) -> None:
        if prerequisites is _ASK:
            prerequisites = self._ops.prerequisites(open=self.prerequisites_open)
        self.dependencies.clear()
        if prerequisites is None:
            return
        blocks = []
        if prerequisites.count:
            blocks.append(u.button(prerequisites.caption, self.toggle_prerequisites, compact=True))
            blocks.extend(u.label(title, size=14) for title in prerequisites.titles)
        self.dependencies.add(*blocks)

    def _draw_preview(self, preview: ShortcutPreview) -> None:
        self.section_preview.clear()
        self.section_preview._feedback_fragment = dict(preview.feedback)
        if preview.error:
            self.section_preview.add(u.label(preview.error, size=13, color=u.MUTED))
            return
        self.section_preview.add(u.label(preview.heading, size=14))
        for title in preview.titles:
            self.section_preview.add(u.label(title, size=13))
        if preview.remainder:
            self.section_preview.add(u.label(preview.remainder, size=13, color=u.MUTED))
        if preview.complete:
            self.section_preview.add(u.label(preview.complete, size=14, color=u.MUTED))
        for line in preview.lines:   # what 只勾选词汇 / 只勾选语法 would tick
            self.section_preview.add(u.label(line.text, size=13))

    def _draw_feedback(self) -> None:
        """The tree's fragment: the selection's (from the last plan) and the page's display fields."""
        display = {
            "expanded_node_ids": [list(key) for key in sorted(self.expanded)],
            "visible_node_ids": [list(key) for key in self.node_controls],
            "shortcut_open": self.shortcut_open,
            "prerequisites_open": self.prerequisites_open,
        }
        selection = self._ops.selection_feedback()
        if selection is None:
            self.tree._feedback_fragment.update(display)
            return
        self.tree._feedback_fragment = {**selection, **display}

    def _count_text(self) -> str:
        return str(self.section_count.value or "")
