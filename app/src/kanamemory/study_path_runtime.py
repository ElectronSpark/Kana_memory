"""One continue action routes to the existing kana and course queues."""
import json
from dataclasses import dataclass
from datetime import datetime
from .practice_preferences import clean_preferences, enabled_preference_kinds

from .navigation import page_view
from .study_path import StudyPathService
from .practice_availability import summarize_queue_availability


_UNREAD = object()
# A continue wait page that outlives this offers retry (review 2026-11-08 section 2).
CONTINUE_WAIT_SECONDS = 20
RECOMPUTE_QUEUE_TEXT = "重新计算今日队列"
RECOMPUTED_NOTICE = "已按当前进度重新计算；原题、草稿和复习时间不变。"
# Review 2026-11-08 section 2 item 4: when due reviews or an open question come
# before the newly added section, the first page reached says so once.
ENROLL_NOTICE = "已加入「{title}」（{count} 项）。先完成当前到期复习，新课会接着开始。"
ENROLL_REVIEW_PAGES = frozenset({"question", "word_question", "ink_question", "feedback",
                                 "learning_question", "learning_feedback"})


def skipped_teaching_ids(app):
    """Course items the learner chose to pass over this run (see learning_ui)."""
    return frozenset(getattr(app, "_guide_skipped_ids", None) or ())


def show_continue_wait(app, page, loop):
    """The short "正在准备题目…" page; after CONTINUE_WAIT_SECONDS it offers retry.

    The page itself (and so a late worker result) stays valid; only controls
    are added. Returns False when the page could not be shown.
    """
    from . import ui_components as u
    late = u.col()
    app.screen(page, "继续学习", u.label("正在准备题目…", size=17, color=u.MUTED), late,
               u.button("回到今日", app.leave_learning))
    if app.page != page:
        return False
    token = app._page_token
    # Which page is the wait page (the enrollment notice waits over it).
    app._continue_wait_view = (page, token)

    def overdue():
        if getattr(app, "page", None) != page or app._page_token != token or late.children:
            return
        late.add(u.label("准备时间比预期长。", size=16))
        late.add(u.label("已保存的进度不受影响。可以重试，或先回到今日。", size=13, color=u.MUTED))
        late.add(u.button("重试", app.continue_study_path, primary=True))
    cancel_continue_wait(app)
    try:
        app._continue_wait_timer = loop.call_later(CONTINUE_WAIT_SECONDS, overdue)
    except (AttributeError, RuntimeError):
        app._continue_wait_timer = None
    return True


def enrollment_notice(result, identities):
    """One-shot notice for an add_sections result; None when nothing new was added.

    Only presentation: the text, and the content ids of the added sections
    (with their newly enrolled prerequisites), so a page that already shows
    the new lesson does not get the notice.
    """
    if not isinstance(result, dict) or not result.get("added_sections"):
        return None
    added = set(result["added_sections"])
    sections = [s for s in result.get("sections") or () if isinstance(s, dict) and s.get("id") in added]
    if not sections:
        return None
    requested = set(identities)
    titles = [s.get("title") for s in sections if s.get("id") in requested] or [s.get("title") for s in sections]
    entries = [e for s in sections for e in s.get("entries") or ()]
    new_ids = {e.get("content_id") for e in entries} | set(result.get("added_learning") or ())
    return {"text": ENROLL_NOTICE.format(title="、".join(t for t in titles if t), count=len(entries)),
            "new_ids": frozenset(new_ids), "anchor": None}


def take_enrollment_notice(app):
    """The armed notice for this continue action (cleared either way).

    A notice armed by the add itself has no anchor; one carried over a
    continue wait page is valid only while that same wait page is current.
    """
    notice, app._enroll_notice = getattr(app, "_enroll_notice", None), None
    if not notice:
        return None
    anchor = notice.get("anchor")
    if anchor is not None and anchor != (getattr(app, "page", None), getattr(app, "_page_token", None)):
        return None
    return notice


def _landed_content_id(app, page):
    if page in {"learning_question", "learning_feedback"}:
        question = getattr(app, "_learning_question", None)
        return question.get("content_id") if isinstance(question, dict) else None
    return getattr(getattr(app, "question", None), "content_id", None)


def place_enrollment_notice(app, notice):
    """Show the notice on the review page this continue reached.

    A continue wait page (show_continue_wait) keeps it for the resumed
    action; the new lesson's own pages (or any other page) drop it: the
    lesson starts right away.
    """
    if not notice:
        return
    page = getattr(app, "page", None)
    view = (page, getattr(app, "_page_token", None))
    if getattr(app, "_continue_wait_view", None) == view:
        app._enroll_notice = {**notice, "anchor": view}
        return
    if page not in ENROLL_REVIEW_PAGES or _landed_content_id(app, page) in notice["new_ids"]:
        return
    slot = getattr(app, "content_slot", None)
    children = list(getattr(slot, "children", None) or ())
    if not children:
        return
    body = getattr(children[0], "content", None) or children[0]
    from . import ui_components as u
    label = u.label(notice["text"], size=13, bold=True)
    if callable(getattr(body, "insert", None)):
        body.insert(0, label)
    else:
        rest = list(body.children)
        body.clear()
        body.add(label, *rest)


def cancel_continue_wait(app):
    timer, app._continue_wait_timer = getattr(app, "_continue_wait_timer", None), None
    if timer is not None:
        try:
            timer.cancel()
        except (AttributeError, RuntimeError):
            pass


def show_continue_error(app, error, *, title=None):
    """Actionable page instead of a stuck wait page; the caller records the error."""
    from . import ui_components as u
    cancel_continue_wait(app)
    reason = (str(error).strip().splitlines() or [""])[0][:80] or type(error).__name__
    feedback = getattr(app, "begin_page_feedback", None)
    app.screen("learning_wait", "无法准备下一题",
        *([u.label(title, size=19, bold=True)] if title else []),
        u.label("准备下一题时出错，学习记录与草稿已保留。", size=16),
        u.label("原因：" + reason, size=13, color=u.MUTED),
        u.button("重试", app.continue_study_path, primary=True),
        u.button("回到今日", app.leave_learning),
        *([u.button("反馈", feedback)] if callable(feedback) else []))
    try:
        error._continue_error_shown = True
    except AttributeError:
        pass


def _course_title(app, before):
    """Title of the course item this continue action reached, when known."""
    for name in ("_learning_item", "_learning_current"):
        value = getattr(app, name, None)
        if value is None or value is before.get(name):
            continue
        content = value.get("content", value) if isinstance(value, dict) else None
        if isinstance(content, dict) and isinstance(content.get("title"), str):
            return content["title"]
    return None


def continue_failed(app, error, *, source):
    """A background continue step failed: record it and leave an actionable page.

    Without this the asyncio task error only reached the loop's log and the
    "正在准备题目…" page stayed up. An error the inline continue already showed
    is not rendered twice.
    """
    from .diagnostic_events import record_error
    from .crash_events import record_unhandled
    record_unhandled(error, source)
    if getattr(error, "_continue_error_shown", False):
        return
    record_error("kana_queue_assembly", "callback", error)
    show_continue_error(app, error)


def routes_service_key(value):
    """The part of study_routes_v1 a StudyPathService is built from.

    Personal route specs and enabled flags change the RouteSet, so they
    rebuild the service; active_route and added_sections are read live and
    leave the (costly) core tree in place (design 10 WP-R3).
    """
    import json
    rows = value.get("routes") if isinstance(value, dict) else None
    if not isinstance(rows, dict):
        return ()
    return tuple(sorted(
        (str(rid), row.get("enabled") if isinstance(row, dict) else None,
         json.dumps(row.get("spec"), ensure_ascii=False, sort_keys=True, default=str)
         if isinstance(row, dict) and "spec" in row else None)
        for rid, row in rows.items()))


def registry_service_key(registry):
    """The part of the library registry a StudyPathService is built from.

    The service filters library routes and catalogue items by the registry
    it saw at construction; disabling a library that no learned content
    belongs to leaves the unavailable ids unchanged, so enabled/state are
    keyed explicitly (F3 open item 3). None (no registry written yet) is ().
    """
    rows = registry.get("libraries") if isinstance(registry, dict) else None
    if not isinstance(rows, (list, tuple)):
        return ()
    return tuple(sorted((str(row.get("library_id")), bool(row.get("enabled")), str(row.get("state")))
                        for row in rows if isinstance(row, dict)))


async def read_line_readiness(source, overview=None) -> bool:
    """The readiness of the lessons of each line's next section on the current route (``overview``: that
    route's, read here when None), read on a worker thread when this process has not read it yet
    (StudyPathService.line_readiness_job); True when something was read. Any failure leaves the readiness
    hint out (the add page and the home are drawn as without it)."""
    import asyncio
    from .study_lines import readiness_ids
    service = None
    try:
        service = source() if callable(source) else source
        if overview is None:
            overview = current_route_next(service)[0]
        make = getattr(service, "line_readiness_job", None)
        job = make(readiness_ids(overview)) if callable(make) else None
        if job is None:
            return False
        return bool(await asyncio.to_thread(job))
    except Exception as error:  # noqa: BLE001 - an optional hint: the pages stay as without it
        mix = getattr(service, "route_mix", None)
        if mix is not None:
            mix.last_error = type(error).__name__   # as the plan's warm-up keeps it
        return False


def current_route_next(service):
    """Positional _path_next_controls arguments for the current route.

    (overview,) for the core route, exactly the pre-route call; otherwise
    (that route's overview, route_id) (design 10 section 8.2).
    """
    from .study_path_display import active_route, route_key
    current = active_route(service)
    if route_key(current) is None:
        return (service.overview(),)
    return (service.overview(route_id=current), current)


@dataclass(frozen=True)
class Rearrange:
    """A dispatch's request to re-arrange the frozen round once (design 12 B5); nothing was shown or sealed.

    ``identities``: the new content (lesson content ids, kana members) the
    round could not show; the probe memo remembers them so that no second
    re-arrangement is made for them.
    """
    identities: frozenset


@dataclass(frozen=True)
class Consolidate:
    """A dispatch's choice of a consolidation deck (design 12 N5): its items; nothing else was shown."""
    items: tuple


@dataclass(frozen=True)
class PreparedKana:
    """Proof that continue already reconciled and prepared the stored main queue.

    Lifetime: the rest of one synchronous continue action. resume_practice and
    show_question reuse ``prepared`` instead of reconciling/preparing again only
    while the database revision, practice-round plan, lane, session and queue
    kinds are all unchanged; any write or setting change makes them redo the
    original work. It grants no write or grading permission.
    """
    session_id: str
    prepared: dict
    kinds: tuple
    revision: tuple
    plan: tuple

    @staticmethod
    def _plan(store):
        from .practice_round import course_order_token, finished, promoted, sealed
        # promoted: a promotion reorders the kana lane too (design 12 B5).
        return (course_order_token(store), sealed(store), finished(store), promoted(store))

    @classmethod
    def capture(cls, store, prepared, kinds):
        return cls(prepared["session_id"], prepared, tuple(kinds), store.read_revision(), cls._plan(store))

    def current(self, store, session_id, kinds) -> bool:
        return (getattr(store, "practice_lane", "main") == "main" and not store.in_transaction
                and session_id == self.session_id and tuple(kinds) == self.kinds
                and store.read_revision() == self.revision and self._plan(store) == self.plan)


def course_current_in_worker(content, progress, *, snapshot, enabled_kinds, now, fair_new=True):
    """Worker half of a sealed-round continue: course candidate filtering and order.

    Runs on practice_worker.run_in_background's own connections. The frozen
    round order arrives as a practice_round.RoundSnapshot (pure data) and is
    installed on this worker connection only; LearningStore.reader never
    writes. Returns the current() result for the UI thread to seed (WP7a).
    """
    from .learning_store import LearningStore
    from .material_qualification import bind_resources
    bind_resources(progress, repo=content)
    snapshot.install(progress)
    learning = LearningStore.reader(progress)
    moment = datetime.fromisoformat(now)
    with learning.read_operation(now=moment) as read:
        value = learning.current(enabled_kinds, now=moment, fair_new=fair_new, projection=read)
    progress._build_cancel_check()
    return value


@dataclass(frozen=True)
class CourseCurrentResume:
    """UI-thread state of one sealed-round continue waiting for its worker.

    Lifetime: until the worker returns. ``value`` is used only after the
    BackgroundTicket (page, page token, UI revision) and the RoundSnapshot
    both still hold; any mismatch makes continue recompute inline.
    """
    ticket: object
    snapshot: object
    pending: object
    now: datetime
    prepared_kana: object
    enabled_kinds: tuple
    value: object = _UNREAD


class StudyPathRuntimeMixin:
    def study_path_can_continue(self):
        """Whether 继续学习 can reach anything now; the route page and the end pages ask (design 13 S3: the
        home service answers, home_runtime builds it). KanaMemory._queue_kinds is the App's, as before."""
        from .app import KanaMemory
        from .home_runtime import home_inputs, home_service
        service = home_service(self, queue_kinds=lambda pending: KanaMemory._queue_kinds(self, pending))
        return service.can_continue(home_inputs(self))

    def study_path_service(self):
        # Learner IDs, not the number of rows, detect restored/custom courses.
        store = self.store
        ids = store.queries.course_ids()
        # A registry change (library disabled/enabled) rebuilds the route too,
        # as does a route enable/disable or personal route change.
        from .study_routes import ROUTES_SETTING
        from .route_mix.service import main_route_mix
        from .learning_questions import stored_enabled_kinds
        from .learning_ui import LEARNING_PREFS
        view = self.learning_store.availability()
        previous = getattr(self, "_study_path_service_value", None)
        # Design 11 section 16 (M10): the main route mix of this data directory, kept by the service it
        # replaces; a newly saved plan raises its generation, so the next call builds the mixed tree. Its M5
        # floor follows the kinds the learner has switched on, read here (plain data for route_mix) only
        # when a new mix is made: a switch changed later applies from the plan the next start-up computes
        # (the stored plan stays in use until then).
        mix = main_route_mix(previous, getattr(store, "data_dir", None),
                             lambda: stored_enabled_kinds(store.get_setting(LEARNING_PREFS, {})))
        key = (id(self.store), ids, view.unavailable_ids, registry_service_key(getattr(view, "registry", None)),
               routes_service_key(self.store.get_setting(ROUTES_SETTING, None)), getattr(mix, "generation", 0))
        if getattr(self, "_study_path_service_key", None) != key:
            # The replaced service hands over its core route tree when the catalogue it was built from is unchanged.
            self._study_path_service_value = StudyPathService(
                self.repo, self.store, self.learning_store, previous=previous, route_mix=mix)
            self._study_path_service_key = key
        return self._study_path_service_value

    def _warm_up_allowed(self) -> bool:
        """The start-up warm-up's guard: only the started app (_background_assembly), not fixtures or tools,
        never while a backup runs or the app closes, and only with a path service accessor."""
        return not (not getattr(self, "_background_assembly", False) or getattr(self, "_backup_closing", False)
                    or getattr(self, "_backup_busy", False) or not callable(getattr(self, "study_path_service", None)))

    async def prepare_line_readiness(self):
        """Startup warm-up, first step: the readiness of each line's next section on the current route (the
        two-line design: the add page's hint, the suggestion of B10's note; memory only), read on a worker
        thread before the slower warm-up steps, so the add page opened right after the start has it. Home is
        redrawn only when the drawn home shows something the read changes (warm_up_refresh). True when
        something was read."""
        if not StudyPathRuntimeMixin._warm_up_allowed(self):
            return False
        read = await read_line_readiness(self.study_path_service)
        if read:
            await StudyPathRuntimeMixin.warm_up_refresh(self, plan_saved=False)
        return read

    async def prepare_main_route_mix(self):
        """Startup warm-up, last step (design 11 section 16, M10): the main route mix plan of the current
        derivation tree on a worker thread when it has none (route_mix.service); home is refreshed once
        when one was saved. Only the started app (_background_assembly) runs it, not fixtures or tools, and
        never while a backup runs or the app closes; a failure, getting the service included, returns False."""
        if not StudyPathRuntimeMixin._warm_up_allowed(self):
            return False
        from .route_mix.service import prepare_in_background
        saved = await prepare_in_background(self.study_path_service)
        # The two-line design: the readiness of each line's next section (read at the start of the warm-up;
        # here only what a new plan's next sections still lack).
        read = await read_line_readiness(self.study_path_service)
        if saved or read:
            await StudyPathRuntimeMixin.warm_up_refresh(self, plan_saved=saved)
        return saved

    async def warm_up_refresh(self, *, plan_saved):
        """Redraw home after a warm-up step when it changes what home shows: a saved route mix plan (the
        route order) always; a readiness read only when the drawn home's lines or B10's suggestion would be
        drawn differently (HomeOverviewService.lines_changed on the drawing's HomeShowing.lines; a drawing
        without them is redrawn). Like the home timer, the redraw runs as its own event-loop callback, after
        any tap already waiting, and only if home is still the same showing (page and page token)."""
        if self.page != "home" or getattr(self, "_backup_closing", False):
            return False
        if not plan_saved:
            drawn = getattr(getattr(self, "_study_home", None), "lines", None)
            if drawn is not None:
                from .home.service import HomeOverviewService
                try:
                    if not HomeOverviewService.lines_changed(self.study_path_service(), drawn):
                        return False
                except Exception:  # noqa: BLE001 - the comparison is only a saving: redraw as without it
                    pass
        import asyncio
        token = getattr(self, "_page_token", None)

        def redraw():
            if (self.page == "home" and getattr(self, "_page_token", None) == token
                    and not getattr(self, "_backup_closing", False)):
                self.refresh_home_actions()
        asyncio.get_running_loop().call_soon(redraw)
        await asyncio.sleep(0)   # the callback runs on the next loop turn, before this step goes on
        return True

    def cancel_main_route_mix(self):
        """App exit (on_exit): a running main route mix computation stops and saves nothing."""
        mix = getattr(getattr(self, "_study_path_service_value", None), "route_mix", None)
        if mix is not None:
            mix.cancel()

    def study_arrangement(self):
        """Preferences, study clock and share dispatcher of the current store (design 12 WP-B3).

        Created on first use and replaced when the store object is replaced,
        like study_path_service: the clock keeps memory of one connection.
        The focus scope catalogue reaches the routes through this app's
        study_path_service (asked only when a route or node scope is resolved).
        """
        from .arrangement.assembly import StoreArrangement
        from .extra_practice_runtime import extra_inputs
        current = getattr(self, "_study_arrangement_value", None)
        if current is None or not current.serves(self.store):
            current = self._study_arrangement_value = StoreArrangement.for_store(
                self.store, routes=lambda: self.study_path_service(), repository=lambda: self.repo,
                **extra_inputs(self))
        return current

    def _focus_scope(self):
        """The focus scope's content for this dispatch (design 12 H3), None without one: one settings read.

        The scope acts through "继续学习" (lead decision, design 12 section 12 WP-H):
        the single-lane entries (课程学习, a kana practice opened by itself) keep
        their own order, as N2 says for 课程学习; so a kana practice resumed
        directly while a round is still being collected may freeze that round's
        kana order without the scope (known, accepted).
        """
        return StudyPathRuntimeMixin.study_arrangement(self).scope_contents()

    def refresh_study_home(self):
        """Redraw the home page in place: show_home, Back, the startup check, 取消重点, the timer (design 13 S3).

        The home service reads and computes everything first (home_runtime
        builds it for the stores of now); only then does the page clear and
        draw the two slots and the stats label (F1: a read that fails leaves
        the drawn home as it was). The showing keeps the moment it drew, and
        the timer is armed exactly as before: only while home is shown, with
        the current page token. The App's own methods (the queue kinds, the
        word-drill count and entry, the timer) are called on its class, as
        before the move.
        """
        from .app import KanaMemory
        from .home.ui.home_page import draw_home
        from .home_runtime import home_inputs, home_service, record_drawing, run_action
        home = home_service(self, queue_kinds=lambda pending: KanaMemory._queue_kinds(self, pending),
                            word_count=lambda pending: KanaMemory._word_drill_remaining(self, pending))
        model = home.overview(home_inputs(self))
        showing = self._study_home

        def on_action(action, widget=None):
            return run_action(self, action, widget, open_word_drill=lambda: KanaMemory.open_home_word_drill(self))
        draw_home(model, action_slot=showing.action_slot, secondary_slot=showing.secondary_slot,
                  stats_label=showing.stats_label, on_action=on_action)
        record_drawing(self, showing, model.next_change_at)
        showing.lines = getattr(home, "drawn_lines", None)
        KanaMemory._arm_home_review_refresh(self)

    def press_home_primary(self, widget, action, *, route, no_new_content, add_next):
        """The home page's primary button: the arrangement's two first-time questions, then ``action``.

        Only this button asks them (continue_study_path and the other entry
        points do not): a fresh install first sees the orientation screen
        (design 12 UI1), which goes on to ``action`` after 确定; under breadth
        the first press of this launch with nothing new to start shows the
        B10 prompt once. The presenter decides; this only navigates.
        ``add_next(section_id, route_id)``: the prompt's section may lie in the
        focus scope's route (SectionNote.route_id).
        """
        presenter = StudyPathRuntimeMixin.study_arrangement(self).presenter
        if presenter.orientation_due():
            presenter.showed_orientation()
            return self.show_study_orientation(then=lambda: action(widget))
        note = presenter.continue_prompt(route, no_new_content=no_new_content)
        if note is not None:
            presenter.prompted_continue()
            return self.show_section_prompt(note=note, on_add_and_start=lambda: add_next(note.section_id, note.route_id),
                                            on_review_first=lambda: action(widget))
        return action(widget)

    @page_view()
    def show_section_prompt(self, widget=None, *, note, on_add_and_start, on_review_first, **kwargs):
        """B10's once-per-launch prompt before the first "继续学习" (page: arrangement/ui/section_note)."""
        from .arrangement.ui.section_note import section_prompt
        self.screen("study_section_prompt", "继续学习",
                    *section_prompt(note, on_add_and_start=on_add_and_start, on_review_first=on_review_first))

    def progress_rows(self, course_due):
        """The records page's "时间与进度" block (design 12 UI4); ``course_due`` as the page already counted it."""
        from .arrangement.model import RouteFacts
        from .arrangement.ui.progress_block import progress_block
        from .domain import _utc
        from .study_path_display import active_route, route_kwargs
        service = self.study_path_service()
        facts = RouteFacts.from_overview(service.overview(**route_kwargs(active_route(service))))
        model = StudyPathRuntimeMixin.study_arrangement(self).presenter.progress_model(
            _utc(None), facts, course_due=course_due,
            course_exclude=self.learning_store.availability().unavailable_ids)
        return progress_block(model)

    def recompute_study_queue(self, widget=None, **kwargs):
        """Drop only this run's frozen main-lane order and rebuild the view.

        This is the same explicit scheduling request as adding a batch:
        practice_round.restart forgets in-memory order and priorities. Stored
        questions, drafts, feedback, due times and FSRS state are untouched;
        the next continue re-captures order from current long-term state.
        """
        from .practice_round import restart
        # Offered on the route page (review 2026-11-08 section 7 item 7: an
        # internal action off the home page). The lane is named explicitly,
        # so a word-drill plan is never replaced from here.
        if getattr(self, "page", None) != "study_path":
            return
        restart(self.store, lane="main")
        self._study_queue_recomputed = True
        self.show_study_path()

    def record_course_due(self):
        """Course questions due now for the records page: queued plus not yet
        queued, long-term (a running planned round does not limit it); the home
        service counts them (design 13 S3)."""
        from .home_runtime import home_service
        return home_service(self).course_due()

    def study_queue_controls(self):
        """Route-page controls of recompute_study_queue: the button while a
        planned round runs, and the one-shot notice after a recompute."""
        from . import ui_components as u
        from .practice_round import planned_round_running
        controls = []
        if planned_round_running(self.store, lane="main"):
            controls.append(u.button(RECOMPUTE_QUEUE_TEXT, self.recompute_study_queue, multiline=True))
        if getattr(self, "_study_queue_recomputed", False):
            self._study_queue_recomputed = False
            controls.append(u.label(RECOMPUTED_NOTICE, size=12, color=u.MUTED))
        return controls

    @page_view(replace=True)
    def continue_study_path(self, widget=None, *, _checkpoint_saved=False, **kwargs):
        """One continue action; a failure ends on an actionable page, then re-raises."""
        from .app import KanaMemory
        before = {name: getattr(self, name, None) for name in ("_learning_item", "_learning_current")}
        notice = take_enrollment_notice(self)
        try:
            result = StudyPathRuntimeMixin._continue_study_path(self, KanaMemory, widget,
                                                                _checkpoint_saved=_checkpoint_saved, **kwargs)
            place_enrollment_notice(self, notice)
            return result
        except Exception as exc:
            if not getattr(exc, "_continue_error_shown", False):
                from .diagnostic_events import record_error
                record_error("kana_queue_assembly", "read", exc)
                show_continue_error(self, exc, title=_course_title(self, before))
            # Still raised: the handler/crash log keeps the code location.
            raise

    def _continue_study_path(self, KanaMemory, widget=None, *, _checkpoint_saved=False, **kwargs):
        """Body of continue_study_path; KanaMemory is passed in by its caller.

        Design 12 H8-H10: the learner's review filter of this moment (只做范围内 unless lifted, 暂不复习, an
        extra-practice part) is read once and is in force for the whole action: both lanes, the probes, the
        consolidation deck and the pages shown from here read it (StoreArrangement.serving).
        """
        from .domain import _utc
        arrangement = StudyPathRuntimeMixin.study_arrangement(self)
        with arrangement.serving(arrangement.serve_filter(_utc(None))):
            return StudyPathRuntimeMixin._continue_study_path_served(self, KanaMemory, widget,
                                                                    _checkpoint_saved=_checkpoint_saved, **kwargs)

    def _continue_study_path_served(self, KanaMemory, widget=None, *, _checkpoint_saved=False, **kwargs):
        """_continue_study_path under the review filter it put in force."""
        from . import ui_components as u
        from .domain import _utc
        from .review_dispatch import kana_resume_priority, stored_review_priority
        resume = kwargs.get("_course_resume")
        if resume is not None:
            StudyPathRuntimeMixin._resume_course_dispatch(self, resume)
            return
        if not _checkpoint_saved and self._checkpoint() is False:
            return
        if KanaMemory._activate_practice_lane(self, "main") is False:
            return
        # Design 12 E1: an early batch of extra practice left by an earlier launch ends before anything is served.
        from .extra_practice_runtime import end_left_early_batch
        end_left_early_batch(self)
        StudyPathRuntimeMixin._renew_round_after_break(self)
        from .practice_round import begin, sealed, seal, frozen_priority, native_key, mark_finished
        begin(self.store, restart_finished=not _checkpoint_saved)
        StudyPathRuntimeMixin.study_arrangement(self).dispatched(_utc(None))
        # Personal-content head/hold reads share one index (WP7a) up to the
        # queued course questions; writes run in transactions (read live) and
        # any local write renews the index before the next lookup.
        with self.learning_store.read_operation():
            self.learning_store.ensure_prerequisites()
            service = self.study_path_service()
            preferences = clean_preferences(getattr(self, "_practice_preferences", {}))
            existing = self.store.load_session_progress(lane="main", lazy_questions=True)
            kinds = (KanaMemory._queue_kinds(self, existing) if existing
                     else enabled_preference_kinds(preferences))
            if sealed(self.store):
                queued = {"session_id": existing["session_id"] if existing else None}
            else:
                audio, handwriting = bool(self.platform.tts_ready), self.model_state == "ready"
                plan = kwargs.get("_kana_plan")
                # WP5: assemble the kana queue on a worker connection when the app
                # enabled it; this action then ends on a short wait page and resumes
                # here with the plan (KanaMemory._finish_kana_queue_assembly).
                if plan is None and KanaMemory._plan_kana_queue_in_background(self, service, kinds,
                        audio_available=audio, handwriting_available=handwriting,
                        attempt=kwargs.get("_kana_attempt", 0)):
                    return
                queued = service.ensure_kana_queue(kinds, audio_available=audio,
                    handwriting_available=handwriting, lazy_questions=True, plan=plan)
            if queued["session_id"]:
                # Do not overwrite the previous policy of an existing legacy queue.
                pending = self.store.load_session_progress(lazy_questions=True)
                if pending and self.store.get_setting("practice_queue_kinds", {}).get("session_id") != queued["session_id"]:
                    KanaMemory._set_queue_kind_policy(self, queued["session_id"], kinds)
            self._queue_missing_learning_questions()
        pending = self.store.load_session_progress(lane="main", lazy_questions=True)
        now = _utc(None)
        prepared_kana = None
        # Design 12 H3: the focus scope orders both lanes when this round is collected.
        scope = StudyPathRuntimeMixin._focus_scope(self)
        if pending and KanaMemory._queue_kinds(self, pending):
            pending = KanaMemory._reconcile_practice_kinds(self, pending)
            if pending is False:
                return
            # Select the same eligible head that resume will display. This
            # keeps cross-lane ordering consistent with pauses and timed steps.
            kinds = KanaMemory._queue_kinds(self, pending)
            pending = (None if pending.get("completed") else
                       self.store.prepare_next_question(pending["session_id"], now=now, lazy_questions=True, fair_new=True,
                                                        **({} if scope is None else {"scope_contents": scope})))
            if isinstance(pending, dict):
                prepared_kana = (pending, kinds)
        # WP7a: in a sealed round, course candidate filtering/order runs on a
        # worker with the frozen round snapshot; this action ends on the short
        # wait page and resumes through _resume_course_dispatch.
        if not kwargs.get("_course_inline") and StudyPathRuntimeMixin._course_current_in_background(
                self, pending, now, prepared_kana):
            return
        # One read context for the rest of this synchronous action: course
        # dispatch, protected-teaching probes and the course page share it.
        with self.learning_store.read_operation(now=now) as read:
            outcome = StudyPathRuntimeMixin._continue_dispatch(self, pending, now, read, service, prepared_kana,
                                                               scope=scope, rearrange=not kwargs.get("_rearranged"))
        StudyPathRuntimeMixin._rearrange_round(self, outcome)

    def _wait_or_scope_done(self, wait, serve, due):
        """A wait outcome of 继续学习: the lane's own wait page (``wait()``), as without a filter; under 只做范围内
        only when the inside work it waits for (``due``, ISO) falls within the wait page's horizon or other work
        is left (design 12 H12, _scope_done_instead), else 范围内的都做完了."""
        from datetime import datetime as _datetime
        from .arrangement.rules.review_filter import waits_within
        from .domain import _utc
        if (serve is not None and serve.limits_to_scope
                and not (due and waits_within(_datetime.fromisoformat(due), _utc(None)))
                and StudyPathRuntimeMixin._scope_done_instead(self, serve)):
            return StudyPathRuntimeMixin.show_scope_done(self)
        return wait()

    def _scope_done_instead(self, serve):
        """Whether the end page of 继续学习 is 范围内的都做完了 instead (design 12 H12): the one rule the home
        follows too (rules/review_filter.ends_in_scope_done), asked where the lanes of this round serve nothing
        and nothing of the round waits. The work the home counts beyond the round is asked as the home asks it
        (the unqueued course and kana previews under the same filter: a lesson's next review is created at the
        next collection), and the offer of a next section the filter allows (next_offer); with any of them the
        lanes' own end page is shown, as without a filter. Waiting counts within the wait page's horizon
        (rules/review_filter.waits_within)."""
        from .arrangement.rules.review_filter import ends_in_scope_done
        if serve is None or not serve.limits_to_scope:
            return False
        from .home_runtime import home_inputs, home_service
        home = home_service(self, queue_kinds=lambda pending: self._queue_kinds(pending))
        courses = home.unqueued_courses(round_limited=False)
        kana = home.unqueued_reviews(home_inputs(self))
        from datetime import datetime as _datetime
        from .arrangement.rules.review_filter import waits_within
        from .domain import _utc
        dues = [_datetime.fromisoformat(value) for value in (courses["next_due"], kana["next_due"]) if value]
        return ends_in_scope_done(serve, can_serve=bool(courses["ready"] or courses["instruction"] or kana["ready"]),
                                  waiting=waits_within(min(dues) if dues else None, _utc(None)),
                                  next_section=StudyPathRuntimeMixin.next_offer(self, serve) is not None)

    def next_offer(self, serve):
        """(route id, section) of the next section 继续学习's pages offer under ``serve`` (None: none), as the home
        decides it (ReviewFilterService.next_offer over the current route's overview and the other routes)."""
        from .study_path_display import active_route, other_route_rows, route_kwargs
        service = self.study_path_service()
        current = active_route(service)
        return StudyPathRuntimeMixin.study_arrangement(self).review_filter.next_offer(
            serve, current, service.overview(**route_kwargs(current)), other_route_rows(service, current))

    def offered_section(self, route_id, section):
        """``section`` (an overview's next section, or None) when 继续学习's pages may offer it now, else None: the
        review filter in force during a "继续学习" leaves out a section it does not allow (design 12 H12); outside
        one (the route page) every section is offered."""
        if not section:
            return None
        from .study_routes import CORE_ROUTE_ID
        arrangement = StudyPathRuntimeMixin.study_arrangement(self)
        serve = arrangement.serving_now()
        return (section if arrangement.review_filter.offers_section(serve, route_id or CORE_ROUTE_ID,
                                                                    str(section["id"])) else None)

    def show_scope_done(self):
        """Design 12 H8: 范围内的都做完了 -- nothing inside the focus scope can be served now.

        The line 范围外到期 N 题 (none at 0), 继续加练（范围内） (only when something inside can be practised),
        做范围外的 (lifts 只做范围内 in memory until the next sitting break or app start, then 继续学习) and
        回到今日. Models from the presenter, the page from arrangement/ui/scope_done.
        """
        from .arrangement.ui.scope_done import scope_done_page
        from .extra_practice_runtime import extra_available, start_extra
        from .home_runtime import home_service
        arrangement = StudyPathRuntimeMixin.study_arrangement(self)
        model = arrangement.presenter.scope_done(home_service(self).apart(), extra_available=extra_available(self))

        def lift():
            from .domain import _utc as utc
            arrangement.review_filter.lift(utc(None))
            self.continue_study_path()   # as 开始新一轮: a finished round starts afresh, now without the scope limit
        self.screen("learning_done", model.title, *scope_done_page(
            model, on_extra=lambda: start_extra(self), on_lift=lift, on_back=self.leave_learning))

    def _renew_round_after_break(self):
        """Design 12 B11: after a sitting break the main round is re-arranged once before anything is served.

        Not while extra practice owns the round (its mode, or an early kana
        batch). What is in progress (a draft, feedback, an opened block, a
        short step) comes first in the fresh round, as after any restart.
        """
        from .domain import _utc
        from .extra_practice_runtime import manages_round
        return StudyPathRuntimeMixin.study_arrangement(self).renew_after_break(
            _utc(None), leave_alone=lambda: manages_round(self))

    def _rearrange_round(self, outcome):
        """Design 12 B5: re-arrange the frozen round once for new content it could not show, and continue.

        The same restart as a focus change or added sections: stored
        questions, drafts, feedback and due times stay; what is in progress
        comes first again. The memo records the content first, so the new
        round never asks again for it; ``_rearranged`` keeps this one
        "继续学习" from asking twice.
        """
        if not isinstance(outcome, Rearrange):
            return
        StudyPathRuntimeMixin.study_arrangement(self).rearrange(outcome.identities)
        self.continue_study_path(_checkpoint_saved=True, _rearranged=True)

    def _course_current_in_background(self, pending, now, prepared_kana):
        """Start the sealed-round course current() on a worker; True when this action waits.

        Only with startup's background assembly, a running loop, real stores,
        the main lane of an active sealed round, no open transaction and no
        UI-bound example store (material checks would differ on the worker).
        The wait page must not write: otherwise the inline path continues.
        """
        import asyncio
        from .content import ContentRepository
        from .domain import StudyStore
        from .practice_round import RoundSnapshot
        from .practice_worker import BackgroundTicket
        if (not getattr(self, "_background_assembly", False) or not isinstance(self.repo, ContentRepository)
                or skipped_teaching_ids(self) or StudyPathRuntimeMixin.study_arrangement(self).serving_now() is not None
                or not isinstance(self.store, StudyStore) or self.store.in_transaction
                or getattr(self.store, "practice_lane", "main") != "main"
                or getattr(self.store, "_material_resources", (None, None))[1] is not None):
            return False
        snapshot = RoundSnapshot.capture(self.store)
        if snapshot is None:
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        from .app import KANA_ASSEMBLY_PAGE
        if (not show_continue_wait(self, KANA_ASSEMBLY_PAGE, loop)
                or tuple(self.store.read_revision()) != snapshot.revision):
            return False
        ticket = BackgroundTicket.capture(self.page, self._page_token, self.store)
        resume = CourseCurrentResume(ticket, snapshot, pending, now, prepared_kana,
                                     tuple(self.learning_preferences()["enabled"]))
        from .draft_debounce import create_task_awake
        self._course_dispatch_task = create_task_awake(
            loop, StudyPathRuntimeMixin._finish_course_current(self, resume))
        return True

    async def _finish_course_current(self, resume):
        """Back on the UI thread: use the worker value only if page, data and round still match."""
        from dataclasses import replace
        from .practice_worker import run_in_background
        value, error = _UNREAD, None
        try:
            value = await run_in_background(self.repo, self.store, course_current_in_worker, {
                "snapshot": resume.snapshot, "enabled_kinds": list(resume.enabled_kinds),
                "now": resume.now.isoformat(), "fair_new": True})
        except Exception as exc:
            error = exc
            from .diagnostic_events import record_error
            record_error("kana_queue_assembly", "read", exc)
        if not resume.ticket.page_current(getattr(self, "page", None), self._page_token):
            return
        cancel_continue_wait(self)
        try:
            if (error is None and resume.ticket.data_current(self.store) and resume.snapshot.current(self.store)
                    and tuple(self.learning_preferences()["enabled"]) == resume.enabled_kinds):
                self.continue_study_path(_checkpoint_saved=True, _course_resume=replace(resume, value=value))
            else:
                # Changed data/plan or a worker error: recompute the whole action inline.
                self.continue_study_path(_checkpoint_saved=True, _course_inline=True)
        except Exception as exc:
            continue_failed(self, exc, source="async_task")

    def _resume_course_dispatch(self, resume):
        """Finish the paused continue with the seeded course head (inline if refused)."""
        enabled = list(resume.enabled_kinds)
        with self.learning_store.read_operation(now=resume.now) as read:
            seeded = (resume.snapshot.current(self.store)
                      and read.seed(self.learning_store.current_key(enabled, resume.now, fair_new=True),
                                    resume.value, resume.ticket.revision))
            outcome = (StudyPathRuntimeMixin._continue_dispatch(self, resume.pending, resume.now, read,
                                                                self.study_path_service(), resume.prepared_kana)
                       if seeded else None)
        if seeded:
            StudyPathRuntimeMixin._rearrange_round(self, outcome)
            return
        self.continue_study_path(_checkpoint_saved=True, _course_inline=True)

    def _sealed_round_left_out(self, current, read):
        """The course state "disabled" only means the sealed round could not queue that work.

        A sealed round queues no course question, so content still in teaching
        without a pending question reads as "disabled" although its kinds are
        open (after startup's archive, in a round a practice batch started:
        native note N12). The unqueued preview without the round limit tells
        the two apart: work the current settings can offer is left to the
        next round; only really closed kinds keep the "题型已关闭" page.
        """
        if not current or current.get("type") != "disabled":
            return False
        from .home_runtime import home_service
        courses = home_service(self).unqueued_courses(projection=read, round_limited=False)
        return bool(courses["ready"] or courses["instruction"] or courses["waiting"])

    def _thorough_hold_notice(self, priority, now, read):
        """Design 12 G3 under 深度优先 (chosen or never chosen): once per round, on the review page reached,
        the rule that holds the added, untaught lessons back -- or None.

        Only when the orientation in force is not breadth first, the head is an
        ordinary due review (tier 3) and lessons are added and not taught yet.
        The number is the reviews a teaching block needs now (8/16/32/64 by the
        backlog's age, first_teaching_dispatch.teaching_review_threshold).
        """
        arrangement = StudyPathRuntimeMixin.study_arrangement(self)
        if arrangement.dispatcher.active() or priority is None or priority[0] != 3:
            return None
        from .home_runtime import home_service
        lessons = home_service(self).untaught_lessons(now, projection=read)
        if not lessons or not arrangement.hold_note_once():
            return None
        from .arrangement.display import THOROUGH_HOLD_TEXT, UNCHOSEN_TEXT
        from .first_teaching_dispatch import teaching_review_threshold
        text = THOROUGH_HOLD_TEXT.format(count=teaching_review_threshold(now.timestamp() - priority[1]),
                                         lessons=lessons)
        if arrangement.preferences.chosen_orientation() is None:
            text = UNCHOSEN_TEXT + " " + text
        return {"text": text, "new_ids": frozenset(), "anchor": None}

    def _share_dispatch(self, pending, current, normal_kana, kana_priority, course_priority, now, read, skipped,
                        kana_kinds, scope=None, rearrange=True):
        """Breadth-first dispatch (design 12 B2-B5): (protected (lane, head) or None, lane or None).

        A protected head is already promoted and is shown like the protected
        first-teaching block; a lane is the review side the share rule chose;
        (None, None) leaves the existing lane choice. (Rearrange, None): new
        content this frozen round cannot show (a refused promotion, or added
        lessons without a queued question while the round has nothing to
        teach) asks for one re-arrangement of the round (B5); never twice for
        the same content (the probe memo) and only while ``rearrange``, so at
        most once per "继续学习". An explicit start keeps
        its rule; heads below tier 3 (feedback, drafts, short steps, context
        words, a promoted block) and new work without a due review keep the
        existing order. The study clock reads every answer from the store.
        ``scope``: the focus scope's content (design 12 H2), None for none:
        the kinds with a candidate inside it share the time first.
        """
        from .arrangement.model import Unit
        from .arrangement.rules.recent import REVIEW_TIER
        from .arrangement.sources.candidates import DispatchCandidates, LaneHead, head_member, review_head
        from .first_teaching_dispatch import explicit_start_head
        from .practice_round import promote, promote_initial, sealed
        enabled = self.learning_preferences()["enabled"]
        was_sealed = sealed(self.store)
        # Design 12 E11: inside 加练 an explicit start (new content) is never served.
        learned_only = getattr(StudyPathRuntimeMixin.study_arrangement(self).serving_now(), "learned_only", False)
        if not was_sealed and not learned_only:
            explicit = explicit_start_head(self.store, self.learning_store, normal_kana, current,
                enabled_kinds=enabled, kana_kinds=kana_kinds, now=now, projection=read)
            if explicit and not (explicit[0] == "course" and explicit[1].get("type") == "instruction"
                                 and explicit[1].get("content", {}).get("id") in skipped):
                lane, head = explicit
                if lane == "course":
                    promote_initial(self.store, course_id=head["question"]["id"])
                elif pending:
                    promote_initial(self.store, session_id=pending["session_id"], question=head["question"])
                return explicit, None
        normal = self.store.teaching_queries.choose_lane(kana_priority, course_priority,
                     course_started=bool(current and current.get("new_dispatch_continue")))
        priority = kana_priority if normal == "kana" else course_priority if normal == "course" else None
        arrangement = StudyPathRuntimeMixin.study_arrangement(self)
        source = DispatchCandidates(self.store, self.learning_store, pending=pending,
            kana=LaneHead(normal_kana, kana_priority) if normal_kana else None,
            course=LaneHead(current, course_priority) if course_priority is not None else None,
            enabled_kinds=enabled, kana_kinds=kana_kinds, now=now, projection=read, skipped=skipped,
            memo=arrangement.probe_memo, scope_contents=scope)
        if priority is None or priority[0] >= REVIEW_TIER:
            # Design 12 N4 (revised): after tiers 0-2, before the share rule, a recent state's due review,
            # then a ready consolidation deck; nobody's candidate, charged to new content (N3).
            before = StudyPathRuntimeMixin._before_share(self, source, now)
            if before is not None:
                return before
        # The share rule decides once the normal head is review of its lane (tier 3, or a first check of
        # something already learned: candidates.review_head, 2026-11-18); tiers 0-2 and new work keep their order.
        if priority is None or not review_head(normal, normal_kana if normal == "kana" else current, priority):
            return None, None
        dispatcher = arrangement.dispatcher
        decision = dispatcher.decide(source, now)
        memo = arrangement.probe_memo
        if decision.unit is Unit.NEW:
            lane, head = decision.candidate.ref
            # A kana candidate needs a session: without one kana_kinds is () and
            # the kana teaching probes return None, so "pending is None" below is
            # never reached with a kana candidate.
            if (promote(self.store, course_id=head["question"]["id"]) if lane == "course" else
                    pending is not None and promote(self.store, session_id=pending["session_id"],
                                                    question=head["question"])):
                return (lane, head), None
            # Not registered in this round: re-arrange it once (B5), then a limited wait.
            identity = frozenset({head_member(lane, head) or lane})
            if rearrange and memo.may_rearrange(identity):
                return Rearrange(identity), None
            source.promotion_refused()
            decision = dispatcher.decide(source.without_new(), now)
        elif rearrange and source.found_nothing_in_round() and was_sealed:
            # B5: nothing to teach in this frozen round, while added lessons it never queued wait outside it.
            from .home_runtime import home_service
            outside = home_service(self).unqueued_teaching(projection=read)
            if outside and memo.may_rearrange(outside):
                return Rearrange(outside), None
        if (decision.unit is Unit.KANA and rearrange and was_sealed
                and not any(candidate.unit is Unit.COURSE for candidate in source.review_candidates())):
            # B5: the review side went to kana because the frozen round holds no course review, while
            # course reviews came due outside it (their question is created at the next collection).
            identity = source.round_key()
            if not memo.reviews_checked_recently(identity, now):
                memo.reviews_checked(identity, now)
                from .home_runtime import home_service
                due = home_service(self).unqueued_due(projection=read)
                if due and memo.may_rearrange(due):
                    return Rearrange(due), None
        return None, {Unit.COURSE: "course", Unit.KANA: "kana"}.get(decision.unit)

    def _before_share(self, source, now):
        """Design 12 N4 (revised) (ii) and (iii) under breadth first, once no head is in tiers 0-2: a protected
        (lane, head) / lane pair as _share_dispatch returns it, a Consolidate, or None (the share rule decides).

        (ii) The first due review of a recent state in the course order (N1: the scope first, then the due;
        N2 decides "due"): the course head itself when it is one (rules/recent via candidates.recent_head), else
        the first one behind it (LearningStore.current(only_recent=True): a scope, or the round's frozen order,
        can put other rows first). (iii) A consolidation deck that is ready by N5's rule (three states ready,
        or one for READY_WAIT; "nothing else can be served" is the end branch of _continue_dispatch), outside
        the leave pause.
        """
        head = source.first_recent_review()
        if head is not None:
            # The course head itself is served as the course lane; one behind it is shown as that head.
            return (None, "course") if source.course_head() is head else (("course", head), None)
        items = StudyPathRuntimeMixin.study_arrangement(self).consolidation.due_deck(now)
        if items:
            return Consolidate(items), None
        return None

    def _ready_teaching_outside(self, read, serve):
        """Design 12 N9: the identities of ready teaching the sealed round cannot show (None: none, or a round
        was already ended for each of them).

        Reached where the lanes of this round serve nothing now (a wait page or an end page would follow):
        lessons whose teaching has no queued question (home_service.unqueued_teaching, under the filter in
        force), as after a probe that failed or was answered 还不会 in this round. Not inside extra practice
        (E11: it never serves new content). An identity carries the lesson's teaching state (introduction,
        stage, the probe profile's state) and is kept with the probe memo's re-arrangements (B5), so a lesson
        that already had one for its probe gets one more for the teaching that probe sent it to, never two for
        the same state.
        """
        if getattr(serve, "learned_only", False):
            return None
        from .home_runtime import home_service
        identities = home_service(self).unqueued_teaching_states(projection=read)
        if identities and StudyPathRuntimeMixin.study_arrangement(self).probe_memo.may_rearrange(identities):
            return identities
        return None

    def _show_consolidation(self, items, show):
        """The consolidation deck (design 12 N5): header 巩固, leave 先到这里; afterwards 继续学习 goes on.

        Leaving starts the pause before the next deck (its unanswered items stay
        unpractised); a deck that could serve nothing pauses too, so that the
        dispatch never offers it again at once. False: nothing was shown.
        ``show``: the app's show_practice_deck (KanaMemory's, passed by the dispatch).
        """
        from .arrangement.services.consolidation import Consolidation
        from .domain import utc_now
        consolidation = StudyPathRuntimeMixin.study_arrangement(self).consolidation
        deck = consolidation.deck(items)

        def leave():
            consolidation.left(utc_now())
            self.continue_study_path(_checkpoint_saved=True)

        def finish():
            if not deck.served:
                consolidation.left(utc_now())
            self.continue_study_path(_checkpoint_saved=True)

        shown = show(self, deck, presenter=Consolidation.presenter(), leave=leave, finish=finish)
        if not shown:
            consolidation.left(utc_now())
        return bool(shown)

    def _continue_dispatch(self, pending, now, read, service, prepared_kana, scope=_UNREAD, rearrange=True):
        """``scope``: the focus scope's content the caller read for this action (None: no scope); read here when not given.

        Returns a Rearrange when the share rule asks to re-arrange the round
        (design 12 B5; only while ``rearrange``): nothing is sealed or shown
        then, and the caller restarts the round and continues; else None.
        """
        from . import ui_components as u
        from .app import KanaMemory
        from .review_dispatch import kana_resume_priority, stored_review_priority
        from .practice_round import sealed, seal, frozen_priority, native_key, mark_finished
        if scope is _UNREAD:
            scope = StudyPathRuntimeMixin._focus_scope(self)
        scoped = {} if scope is None else {"scope_content_ids": scope}
        # Sealed before this action: its queueing step could add nothing.
        sealed_before = sealed(self.store)
        availability = summarize_queue_availability(self.store, pending,
            allowed_kinds=KanaMemory._queue_kinds(self, pending) if pending else None, now=now)
        if isinstance(pending, dict) and pending.get("serve_counts") is not None:
            # Design 12 H8-H10: the kana items the filter holds back are neither ready nor waiting.
            from dataclasses import replace as _replace
            held_ready, held_waiting, held_next = pending["serve_counts"]
            availability = _replace(availability, ready_count=held_ready, waiting_count=held_waiting,
                                    next_due_utc=held_next)
        serve = StudyPathRuntimeMixin.study_arrangement(self).serving_now()
        skipped = skipped_teaching_ids(self)
        current = self.learning_store.current(self.learning_preferences()["enabled"], now=now, fair_new=True,
                                              projection=read, **({"skip_teaching_ids": skipped} if skipped else {}),
                                              **scoped)
        kana_priority = None
        if availability.ready_count and pending:
            q = pending["questions"][pending["question_index"]]
            kana_priority = kana_resume_priority(pending.get("state"))
            if kana_priority is None:
                kana_priority = stored_review_priority(self.store,
                    self.store.get_scheduling_card(q.content_id, q.kind, q.script), now=now)
                from .kana_context_words import context_priority
                # A short contextual word can interrupt ordinary review, but
                # never an already opened course teaching block.
                if not (current and current.get("new_dispatch_continue")):
                    kana_priority = context_priority(q, self.store) or kana_priority
                if kana_priority[0] == 4:
                    from .kana_learning_intro import should_introduce
                    if should_introduce(self, q):
                        kana_priority = (5, 0)
        course_priority = (current.get("dispatch_priority", (4 if current["type"] == "question" else 5, 0))
                           if current and current["type"] in {"question", "instruction"} else None)
        if kana_priority is not None:
            kana_priority = frozen_priority(self.store, "cross-kana",
                (pending["session_id"], native_key(q)), kana_priority)
        if course_priority is not None:
            course_priority = frozen_priority(self.store, "cross-course",
                current["question"]["id"], course_priority)
        from .first_teaching_dispatch import choose_protected
        normal_kana = ({"type": "feedback" if pending.get("state", {}).get("feedback_pending") else
                        "introduction" if kana_priority[0] == 5 else "question",
                        "question": pending["questions"][pending["question_index"]],
                        "pending": pending, "state": pending.get("state", {}),
                        "session_id": pending["session_id"], "question_index": pending["question_index"],
                        "dispatch_priority": kana_priority} if pending and kana_priority else None)
        share_lane = None
        if StudyPathRuntimeMixin.study_arrangement(self).dispatcher.active():
            # Breadth first (design 12 B2-B5): new content and review share time.
            protected, share_lane = StudyPathRuntimeMixin._share_dispatch(
                self, pending, current, normal_kana, kana_priority, course_priority, now, read, skipped,
                KanaMemory._queue_kinds(self, pending) if pending else (), scope, rearrange)
            if isinstance(protected, Rearrange):
                return protected
        else:
            protected = None
            if not any(priority is not None and priority[0] <= 3 for priority in (kana_priority, course_priority)):
                # Design 12 N5 under 深度优先: after the due reviews, before new lessons.
                items = StudyPathRuntimeMixin.study_arrangement(self).consolidation.due_deck(now)
                if items:
                    protected = Consolidate(items)
        if isinstance(protected, Consolidate):
            seal(self.store)
            if StudyPathRuntimeMixin._show_consolidation(self, protected.items, KanaMemory.show_practice_deck):
                return
            protected, share_lane = None, None   # nothing could be shown: the lanes as usual
        if not StudyPathRuntimeMixin.study_arrangement(self).dispatcher.active():
            # Design 12 E11: inside 加练 no protected first teaching (new content) is chosen.
            protected = (None if sealed(self.store) or getattr(serve, "learned_only", False)
                         else choose_protected(self.store, self.learning_store, normal_kana, current,
                enabled_kinds=self.learning_preferences()["enabled"],
                kana_kinds=KanaMemory._queue_kinds(self, pending) if pending else (), now=now, projection=read,
                **({} if scope is None else {"scope": scope})))
            if (protected and protected[0] == "course" and protected[1].get("type") == "instruction"
                    and protected[1].get("content", {}).get("id") in skipped):
                protected = None
            if protected:
                from .practice_round import promote_initial
                protected_lane, protected_head = protected
                if protected_lane == "course":
                    promote_initial(self.store, course_id=protected_head["question"]["id"])
                elif pending:
                    promote_initial(self.store, session_id=pending["session_id"], question=protected_head["question"])
        seal(self.store)
        # Proof that the kana head prepared above is still the stored queue:
        # taken after seal (an in-memory step), checked again before reuse.
        token = (PreparedKana.capture(self.store, *prepared_kana) if prepared_kana and not protected else None)
        reuse = {} if token is None else {"_prepared": token}
        if protected:
            protected_lane, protected_head = protected
            if protected_lane == "course":
                self.continue_learning(_selected_current=protected_head, _projection=read)
            else:
                selected_question = protected_head["question"]
                self.resume_practice(preserve_audio=True, _first_teaching=True,
                    _first_teaching_targets=protected_head.get('teaching_targets', {(selected_question.content_id,selected_question.script)}))
            return
        # Design 12 H2: between the two lane heads the one inside the focus scope first (no scope: unchanged).
        from .arrangement.sources.candidates import head_in_scope
        inside = ({} if scope is None else {"inside": (head_in_scope("kana", normal_kana, scope),
                                                       head_in_scope("course", current, scope))})
        if inside.get("inside") == (False, True) and normal_kana:
            # H4: an opened kana teaching block outside the scope goes on, as an opened course block does.
            from .first_teaching_kana import opened_block
            inside["kana_started"] = opened_block(self.store, normal_kana["question"], now)
        lane = share_lane or self.store.teaching_queries.choose_lane(kana_priority, course_priority,
                           course_started=bool(current and current.get("new_dispatch_continue")), **inside)
        hold = (StudyPathRuntimeMixin._thorough_hold_notice(
                    self, kana_priority if lane == "kana" else course_priority, now, read)
                if lane in {"kana", "course"} and share_lane is None else None)
        if lane == "kana":
            self.resume_practice(preserve_audio=True, **reuse)
            place_enrollment_notice(self, hold)
        elif lane == "course":
            self.continue_learning(_projection=read)
            place_enrollment_notice(self, hold)
        else:
            # Design 12 E1: while extra practice is on, an end or "only waiting" outcome is its next step.
            from .extra_practice_runtime import extra_button_rows, take_over

            def round_saved():
                from .home_runtime import round_end_review_line
                due = round_end_review_line(self)   # the wait page's 下次复习 line, when nothing is answerable now
                self.screen("learning_done", "本轮已保存",
                    u.label("作答记录和复习安排已保存。", size=17),
                    *([u.label(due, size=14, color=u.MUTED)] if due else []),
                    u.button("开始新一轮", self.continue_study_path, primary=True),
                    *self.learning_next_content_controls(),
                    *extra_button_rows(self),
                    u.button("回到今日", self.leave_learning))
            # Design 12 N9: teaching that is ready but outside this frozen round (a probe that sent its lesson
            # to teaching) ends the round as a round with nothing left does (N12 of the native notes): 本轮已保存,
            # whose 开始新一轮 (and the home's 继续学习) collects a round that teaches it; never a wait page alone.
            outside = (StudyPathRuntimeMixin._ready_teaching_outside(self, read, serve)
                       if rearrange and sealed_before else None)
            if outside is not None:
                StudyPathRuntimeMixin.study_arrangement(self).probe_memo.rearranged(outside)
                mark_finished(self.store)
                round_saved()
                return
            # Design 12 N5: with nothing else to serve, one ready consolidation repeat is enough for a deck.
            items = StudyPathRuntimeMixin.study_arrangement(self).consolidation.due_deck(now, nothing_else=True)
            if items and StudyPathRuntimeMixin._show_consolidation(self, items, KanaMemory.show_practice_deck):
                return
            # A round with nothing left ends, also when all that remains is
            # course work it could not admit (never a dead "题型已关闭" page).
            left_out = sealed_before and StudyPathRuntimeMixin._sealed_round_left_out(self, current, read)
            if not availability.ready_count and not availability.waiting_count and (not current or left_out):
                mark_finished(self.store)
                from .practice_round import finished
                if finished(self.store):
                    if take_over(self):
                        return
                    if StudyPathRuntimeMixin._scope_done_instead(self, serve):
                        StudyPathRuntimeMixin.show_scope_done(self)
                        return
                    round_saved()
                    return
            # Reference-only symbols have no made-up independent audio cards.
            path = service.overview()
            reference = service.next_reference_entry()
            if reference:
                self.show_study_reference(reference["id"])
            elif (availability.waiting_count and availability.next_due_utc
                  and (not current or not current.get("due_at_utc")
                       or datetime.fromisoformat(availability.next_due_utc)
                       < datetime.fromisoformat(current["due_at_utc"]))):
                if not take_over(self):
                    StudyPathRuntimeMixin._wait_or_scope_done(self, lambda: self.resume_practice(**reuse), serve,
                                                              availability.next_due_utc)
            elif current:
                if not (current.get("type") == "waiting" and take_over(self)):
                    if current.get("type") == "waiting":
                        StudyPathRuntimeMixin._wait_or_scope_done(
                            self, lambda: self.continue_learning(_projection=read), serve, current.get("due_at_utc"))
                    else:
                        self.continue_learning(_projection=read)
            elif availability.waiting_count:
                if not take_over(self):
                    StudyPathRuntimeMixin._wait_or_scope_done(self, lambda: self.resume_practice(**reuse), serve,
                                                              availability.next_due_utc)
            elif availability.blocked_count:
                self.resume_practice(**reuse)
            elif not (isinstance(pending, dict) and pending.get("serve_held")) and any(
                    e["kind"] == "kana" and e["state"] in {"added", "started"}
                     for stage in path["stages"] for unit in stage["units"] for section in unit["sections"] for e in section["entries"]):
                self.screen("learning_wait", "暂时没有可进行的假名题型",
                    u.label("已加入的小节仍保留。请检查题型设置、语音或手写模型，以及内容是否已暂停。", size=17),
                    u.button("题型与练习设置", self.show_practice_hub, primary=True),
                    u.button("语音与手写模型", self.show_settings),
                    u.button("学习路线", self.show_study_path), u.button("回到今日", self.leave_learning))
            elif take_over(self):
                return
            elif StudyPathRuntimeMixin._scope_done_instead(self, serve):
                StudyPathRuntimeMixin.show_scope_done(self)
            else:
                # Design 10 section 8.2: the current route's next section;
                # without one, the other routes' next sections (at most 3).
                from .study_path_display import active_route, other_route_rows, route_key, route_kwargs
                from .arrangement.model import RouteFacts
                from .arrangement.ui.section_note import section_note_rows
                current = active_route(service)
                shown = path if route_key(current) is None else service.overview(route_id=current)
                others = other_route_rows(service, current) if not shown.get("next_section") else ()
                # B10 under breadth: the next section first (presenter.end_note is None otherwise), named apart
                # from sections of the same title (study_lines.section_name).
                from .study_lines import SectionNames
                note = StudyPathRuntimeMixin.study_arrangement(self).presenter.end_note(
                    RouteFacts.from_overview(shown, name=SectionNames(shown)))
                if note is not None and not StudyPathRuntimeMixin.offered_section(
                        self, note.route_id or current, {"id": note.section_id}):
                    note = None   # design 12 H12: not a section the learner's filter leaves out
                self.screen("learning_done", "本次学习已保存",
                    *(section_note_rows(note, on_add_next=lambda identity: self._add_study_sections(
                        [identity], **route_kwargs(note.route_id or current))) if note is not None else ()),
                    u.label("可以继续下一小节，或稍后按复习安排回来。", size=17),
                    *(self._path_next_controls(shown, route_id=current, others=others) if route_key(current) or others
                      else self._path_next_controls(path)),
                    *extra_button_rows(self),
                    u.button("题型与练习设置", self.show_practice_hub),
                    u.button("回到今日", self.leave_learning))
