"""AddContentService: the business of the add-content page 添加学习内容 (design 13 S2, C1, C5).

The service reads through the path service's read operations, plans the
selection, counts its workload and writes through the existing owners
(StudyPathService.add_entries, create_personal_route; practice_round.restart
through the injected ``restart``). AddSession is one showing of the page: the
selection, the last plan and the re-entry flag of 创建路线. Neither imports
Toga or takes the app object; the page's display state (what is expanded,
whether 按顺序选多节 is open and what it says) comes in as arguments.

C5: an operation does no more than the page did before: opening, a tick,
清空选择 and 加入勾选 each open one read operation, plan once and count the
workload once; a rebase also re-reads the route; the section-count preview
asks the path service for the next sections, as each keystroke did.

The first part of the two-line design: each plan also draws the route's two
lines (study_lines.route_lines over the overview the session holds, with the
readiness the path service has in memory and the selection counted as known);
the quick choices take a line; 一并加入 ticks the lessons a line's next section
misses. The orientation is read once per opening or rebase (``breadth``), and
with it the line behind its share of the new-content time (``behind``, design
12 LS8: the suggestion's rule (b) follows it).
"""
from __future__ import annotations

from collections.abc import Callable, Collection
import sqlite3
from typing import Any

from .. import admission_workload
from ..study_lines import readiness_ids, route_lines
from ..study_path_display import active_route, closure_text, has_other_routes, route_key, route_kwargs, route_title
from ..study_path_selection import NodeKey, StudyPathSelection
from ..study_routes import CORE_ROUTE_ID, CORE_ROUTE_TITLE
from .display import (OUTSIDE_ROUTE, PREVIEW_VISIBLE, ROUTE_EMPTY, ROUTE_SAVE_FAILED, SELECTION_SAVE_FAILED,
                      UNTITLED, AddDelta, AddPageModel, LinesModel, NodeModel, PlanModel, Prerequisites,
                      ShortcutPreview, route_switch, selection_feedback, tree_nodes, visible_rows)
from .model import AddResult, section_count

# What 加入并继续 turns into a message (the enrollment, the round restart and what the outer layer does next).
ENROLL_ERRORS = (ValueError, KeyError, sqlite3.Error, OSError)


def enrollment_failure(error: BaseException, model: AddPageModel | None = None) -> AddResult:
    """rejected with the error's own text for a refused enrollment, failed for a storage error."""
    if isinstance(error, (ValueError, KeyError)):
        return AddResult("rejected", str(error), model=model)
    return AddResult("failed", SELECTION_SAVE_FAILED, model=model)


class AddSession:
    """One showing of the add page: kept while the page is on screen or in the back stack.

    ``selection``: the StudyPathSelection over the shown route's overview;
    ``plan``: the last preview_entries result (None after a failed plan);
    ``saving_route``: True while 创建路线 is writing. No database state is
    held: every operation opens its own read operation.
    """

    def __init__(self, selection: StudyPathSelection):
        self.selection = selection
        self.plan: dict | None = None
        self.saving_route = False
        self.breadth = False   # the learner's orientation is 广度优先 (read at opening and at each rebase)
        self.behind: str | None = None   # the line behind its share of new content (read with ``breadth``)
        self._shown: dict[NodeKey, NodeModel] = {}   # the rows last drawn; a delta reports on these

    @property
    def route_id(self) -> str | None:
        """The shown route for the path service (None: the core route)."""
        return self.selection.route_id

    def rows(self, expanded: Collection[NodeKey]) -> tuple[NodeModel, ...]:
        """The visible rows under ``expanded`` (expanding is display only: no read, no plan)."""
        rows = visible_rows(self.selection, expanded)
        self._shown = {row.key: row for row in rows}
        return rows

    def changed_rows(self) -> tuple[NodeModel, ...]:
        """The drawn rows whose selection state changed since they were last handed out."""
        changed = []
        for key, row in self._shown.items():
            status = self.selection.node_status(key)
            if status != row.status:
                row = self._shown[key] = row.with_status(status)
                changed.append(row)
        return tuple(changed)

    def selection_feedback(self) -> dict | None:
        """The selection tree's fragment for the last plan, without display fields; None after a failed plan."""
        return None if self.plan is None else selection_feedback(self.plan)


class AddContentService:
    """The operations of the add page, one call each; the session is the caller's.

    ``paths``: the current StudyPathService (asked at each operation, as the
    page did); ``workload``: the admission_workload.WorkloadContext of this
    store; ``restart``: one new pass of the main round (practice_round.restart
    of this store, an owner call); ``breadth()``: whether the learner's
    orientation is 广度优先 (one settings read; None: never, the suggestion
    is the route's next section); ``behind()``: the line behind its share of
    the new-content time (the arrangement's ShareDispatcher.line_behind; None:
    no arrangement, the suggestion's rule (b) keeps the route order).
    """

    def __init__(self, *, paths: Callable[[], Any], workload: admission_workload.WorkloadContext,
                 restart: Callable[[], object], breadth: Callable[[], bool] | None = None,
                 behind: Callable[[], str | None] | None = None):
        self._paths = paths
        self._workload = workload
        self._restart = restart
        self._breadth = breadth
        self._behind = behind

    # ------------------------------------------------------------ opening and rebasing
    def open(self, *, route_id: str | None = None,
             selection_key: NodeKey | None = None) -> tuple[AddSession, AddPageModel]:
        """A new session on ``route_id`` (default: the current route; an old link to a route that is gone shows
        the current route) with ``selection_key`` chosen, or the route's next section; and the whole page."""
        service = self._paths()
        shown = route_id or active_route(service)
        if route_key(shown) is not None and shown not in service.route_set():
            shown = active_route(service)  # an old link to a route that is gone
        route = route_kwargs(shown)
        with service.read_operation() as read:
            overview = read.overview(**route)
            session = AddSession(StudyPathSelection(overview, route_id=route_key(shown)))
            self._orient(session)
            if selection_key is None and overview.get("next_section"):
                selection_key = ("section", overview["next_section"]["id"])
            expanded = _choose(session.selection, overview, selection_key)
            nodes = session.rows(expanded)
            plan = self._plan(session, read)
            switch = _route_switch(read.service, shown)
        return session, AddPageModel(nodes, frozenset(expanded), plan, bool(route), route_switch=switch,
                                     prerequisites=self.prerequisites(session, open=False))

    def refresh(self, session: AddSession, *, expanded: Collection[NodeKey], preview_text: str | None = None,
                prerequisites_open: bool = False) -> AddPageModel:
        """Rebase on what is enrolled now (after Back, around 加入并继续): re-read the route, keep the choices that
        are still new, and redraw everything; ``preview_text``: the section count while 按顺序选多节 is open;
        ``prerequisites_open``: whether the page shows the prerequisite titles (they are read here, with the
        page, so that 加入并继续 reads them before it writes)."""
        with self._paths().read_operation() as read:
            route = route_kwargs(session.route_id)
            session.selection.refresh(read.overview(**route))
            self._orient(session)
            nodes = session.rows(expanded)
            preview = None if preview_text is None else self._preview(session, preview_text, read)
            plan = self._plan(session, read)
        return AddPageModel(nodes, frozenset(expanded), plan, bool(route), preview=preview,
                            prerequisites=self.prerequisites(session, open=prerequisites_open))

    # ------------------------------------------------------------ changing the selection
    def toggle(self, session: AddSession, key: NodeKey) -> AddDelta:
        """Tick or untick one node (all of its entries that are not enrolled yet)."""
        session.selection.toggle(key)
        return self._replan(session)

    def clear(self, session: AddSession, *, replan: bool = True) -> AddDelta | None:
        """清空选择. ``replan`` False: only the selection is emptied and nothing is read (a 清空选择 kept from
        a page that was left; step A pinned that it still empties the selection)."""
        session.selection.clear()
        return self._replan(session) if replan else None

    def select_next(self, session: AddSession, count_text: str, line: str | None = None) -> AddDelta:
        """加入勾选: add the route's next ``count_text`` sections to the selection (nothing is enrolled);
        ``line`` (study_lines.WORD / GRAMMAR, 只勾选词汇 / 只勾选语法): the next sections of that line."""
        try:
            count = section_count(count_text)
        except ValueError as exc:
            return AddDelta(rejection=str(exc))
        route = route_kwargs(session.route_id)
        with self._paths().read_operation() as read:
            sections = read.plan_next(count, **route) if line is None else read.plan_next(count, line=line, **route)
            for section in sections:
                session.selection.select(("section", section if isinstance(section, str) else section["id"]))
            return self._replan(session, read)

    def select_entries(self, session: AddSession, entry_ids) -> AddDelta:
        """一并加入: tick these entries (the lessons a line's next section misses; nothing is enrolled)."""
        for entry_id in entry_ids:
            session.selection.select(("entry", entry_id))
        return self._replan(session)

    def readiness_job(self, session: AddSession):
        """A worker-thread callable that reads the readiness the session's lines lack (the outer layer runs it
        and then rebases the page), or None when nothing is missing or the path service keeps none."""
        make = getattr(self._paths(), "line_readiness_job", None)
        return make(readiness_ids(session.selection.overview)) if callable(make) else None

    def preview_sections(self, session: AddSession, count_text: str) -> ShortcutPreview:
        """What 加入勾选 would select for ``count_text`` (the shortcut opening and every keystroke)."""
        return self._preview(session, count_text, None)

    # ------------------------------------------------------------ queries of the drawn page
    def prerequisites(self, session: AddSession, *, open: bool) -> Prerequisites | None:
        """The prerequisites line of the last plan (None after a failed plan); the titles only while ``open``.

        Titles inside the route come from the tree; on a route, the others come
        from the whole catalogue and are marked （路线外）.
        """
        plan = session.plan
        if plan is None:
            return None
        if not plan["prerequisite_count"] or not open:
            return Prerequisites(plan["prerequisite_count"], open)
        identities, shown, titles = set(plan["prerequisite_entry_ids"]), set(), []
        for key, value, _ in tree_nodes(session.selection.overview):
            if key[0] == "entry" and key[1] in identities:
                shown.add(key[1])
                titles.append(value.get("title", UNTITLED))
        if session.route_id:
            catalogue = self._paths().entries
            titles.extend(catalogue.get(eid, {}).get("title", UNTITLED) + OUTSIDE_ROUTE
                          for eid in plan["prerequisite_entry_ids"] if eid not in shown)
        return Prerequisites(plan["prerequisite_count"], open, tuple(titles))

    # ------------------------------------------------------------ writing
    def submit(self, session: AddSession, *, expanded: Collection[NodeKey], preview_text: str | None = None,
               prerequisites_open: bool = False) -> AddResult:
        """加入并继续: rebase, then enroll what is still new, then restart the main round (the caller continues).

        ignored when the rebase leaves nothing to add; rejected or failed when
        the enrollment or the restart raises, with the page rebased again.
        ``model`` is always the page to draw; it was read before anything was
        written, the prerequisite titles included.
        """
        shown = dict(expanded=expanded, preview_text=preview_text, prerequisites_open=prerequisites_open)
        model = self.refresh(session, **shown)
        if not model.plan.can_submit:
            return AddResult("ignored", model=model)
        try:
            self._paths().add_entries(session.selection.selected_entry_ids, start_learning=True,
                                      **route_kwargs(session.route_id))
            self._restart()
        except ENROLL_ERRORS as exc:
            return enrollment_failure(exc, self.refresh(session, **shown))
        return AddResult("ok", model=model)

    def save_route(self, session: AddSession, title: str) -> AddResult:
        """创建路线: save the selection as a personal route (nothing is enrolled); ok carries its route_id."""
        if session.saving_route:
            return AddResult("ignored")
        selected = list(session.selection.selected_entry_ids)
        if not selected:
            return AddResult("rejected", ROUTE_EMPTY)
        session.saving_route = True
        try:
            route_id = self._paths().create_personal_route(title, selected)
        except ValueError as exc:
            return AddResult("rejected", str(exc))
        except (sqlite3.Error, OSError):
            return AddResult("failed", ROUTE_SAVE_FAILED)
        finally:
            session.saving_route = False
        return AddResult("ok", route_id=route_id)

    # ------------------------------------------------------------ inside
    def _replan(self, session: AddSession, read=None) -> AddDelta:
        if read is None:
            with self._paths().read_operation() as read:
                return self._replan(session, read)
        nodes = session.changed_rows()
        return AddDelta(nodes, self._plan(session, read))

    def _plan(self, session: AddSession, read) -> PlanModel:
        """Plan the selection under ``read``; the workload is counted in the same read context."""
        read.check_active()
        selection = session.selection
        route = route_kwargs(session.route_id)
        can_save_route = bool(selection.selected_entry_ids)
        try:
            plan = read.preview_entries(selection.selected_entry_ids, **route)
        except (ValueError, KeyError) as exc:
            session.plan = None
            return PlanModel.failed(str(exc), selection.selected_entry_ids, can_save_route=can_save_route)
        session.plan = plan
        # Design 10 D4: where the closure's items sit (one progress, different routes).
        routes_line = closure_text(read.service, plan) if route else None
        # A route's closure can reach items outside that route; the core
        # overview holds every catalogue entry with the same item-level states.
        workload = admission_workload.admission_snapshot(
            self._workload, plan, overview=read.overview() if route else selection.overview, read=read)
        return PlanModel.planned(plan, workload, admission_workload.admission_text(workload),
                                 routes_line=routes_line, can_save_route=can_save_route,
                                 lines=self._lines(session, plan, getattr(read, "service", None)))

    def _lines(self, session: AddSession, plan, service) -> LinesModel:
        """The two lines of the shown route: the selection and its prerequisites count as known."""
        overview = session.selection.overview
        known = getattr(service, "line_readiness", None)
        readiness = known(readiness_ids(overview)) if callable(known) else None
        ticked = (*session.selection.selected_entry_ids, *plan.get("prerequisite_entry_ids", ()))
        return LinesModel.of(route_lines(overview, readiness=readiness, ticked=ticked, breadth=session.breadth,
                                         behind=session.behind))

    def _orientation(self) -> bool:
        return bool(self._breadth()) if self._breadth is not None else False

    def _orient(self, session: AddSession) -> None:
        """The orientation and, under breadth, the line behind its share (one clock read)."""
        session.breadth = self._orientation()
        session.behind = self._behind() if session.breadth and self._behind is not None else None

    def _preview(self, session: AddSession, count_text: str, read) -> ShortcutPreview:
        try:
            count = section_count(count_text)
        except ValueError:
            return ShortcutPreview.invalid(count_text)
        route = route_kwargs(session.route_id)
        sections = read.plan_next(count, **route) if read is not None else self._paths().plan_next(count, **route)
        visible = [self._paths().section(section, **route) if isinstance(section, str) else section
                   for section in sections[:PREVIEW_VISIBLE]]
        complete = None
        if not sections:
            complete = route_title(self._paths(), route["route_id"]) if route else CORE_ROUTE_TITLE
        return ShortcutPreview.planned(count_text, count, sections, visible, complete)


class AddOperations:
    """The service bound to one session: what one showing of the add page can ask (design 13 S2, C4, C7).

    The page (admission.ui.add_page) uses the selection and preview
    operations and draws what they return; ``submit`` and ``save_route`` are
    the outer layer's, because what follows them is navigation. No app object
    and no Toga: the outer layer builds one per showing and keeps it with the page.
    """

    def __init__(self, service: AddContentService, session: AddSession):
        self._service = service
        self.session = session

    # ------------------------------------------------------------ the page's
    def rows(self, expanded: Collection[NodeKey]) -> tuple[NodeModel, ...]:
        return self.session.rows(expanded)

    def toggle(self, key: NodeKey) -> AddDelta:
        return self._service.toggle(self.session, key)

    def clear(self, *, replan: bool = True) -> AddDelta | None:
        return self._service.clear(self.session, replan=replan)

    def select_next(self, count_text: str, line: str | None = None) -> AddDelta:
        return self._service.select_next(self.session, count_text, line)

    def select_entries(self, entry_ids) -> AddDelta:
        return self._service.select_entries(self.session, entry_ids)

    def readiness_job(self):
        return self._service.readiness_job(self.session)

    def preview_sections(self, count_text: str) -> ShortcutPreview:
        return self._service.preview_sections(self.session, count_text)

    def prerequisites(self, *, open: bool) -> Prerequisites | None:
        return self._service.prerequisites(self.session, open=open)

    def selection_feedback(self) -> dict | None:
        return self.session.selection_feedback()

    def refresh(self, *, expanded: Collection[NodeKey], preview_text: str | None = None,
                prerequisites_open: bool = False) -> AddPageModel:
        return self._service.refresh(self.session, expanded=expanded, preview_text=preview_text,
                                     prerequisites_open=prerequisites_open)

    # ------------------------------------------------------------ the outer layer's
    def submit(self, *, expanded: Collection[NodeKey], preview_text: str | None = None,
               prerequisites_open: bool = False) -> AddResult:
        return self._service.submit(self.session, expanded=expanded, preview_text=preview_text,
                                    prerequisites_open=prerequisites_open)

    def save_route(self, title: str) -> AddResult:
        return self._service.save_route(self.session, title)


def _choose(selection: StudyPathSelection, overview, key: NodeKey | None) -> set[NodeKey]:
    """Select ``key`` and return the expanded set that reveals it in ``overview``: its ancestors and itself."""
    expanded: set[NodeKey] = set()
    if not key:
        return expanded
    selection.select(key)
    # Reveal the selected branch while leaving unrelated groups compact.
    for stage in overview["stages"]:
        for unit in stage["units"]:
            for section in unit["sections"]:
                keys = (("stage", stage["id"]), ("unit", unit["id"]), ("section", section["id"]))
                if key in keys:
                    expanded.update(keys[:keys.index(key)])
    expanded.add(key)
    return expanded


def _route_switch(service, shown):
    """The route line of the page; None for the core route alone."""
    if not has_other_routes(service):
        return None
    current = route_key(shown) or CORE_ROUTE_ID
    others = [(rid, route_title(service, rid)) for rid in service.route_set().route_ids()
              if rid != current and service.route_state(rid)["enabled"]]
    return route_switch(route_title(service, current), others)

