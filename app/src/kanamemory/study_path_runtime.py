"""One continue action routes to the existing kana and course queues."""
import json
from dataclasses import dataclass
from datetime import datetime

from .navigation import page_view
from .study_path import StudyPathService


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
    from .continue_runtime import continue_state
    late = u.col()
    app.screen(page, "继续学习", u.label("正在准备题目…", size=17, color=u.MUTED), late,
               u.button("回到今日", app.leave_learning))
    if app.page != page:
        return False
    token = app._page_token
    # Which page is the wait page (the enrollment notice waits over it).
    continue_state(app).wait_view = (page, token)

    def overdue():
        if getattr(app, "page", None) != page or app._page_token != token or late.children:
            return
        late.add(u.label("准备时间比预期长。", size=16))
        late.add(u.label("已保存的进度不受影响。可以重试，或先回到今日。", size=13, color=u.MUTED))
        late.add(u.button("重试", app.continue_study_path, primary=True))
    cancel_continue_wait(app)
    try:
        continue_state(app).wait_timer = loop.call_later(CONTINUE_WAIT_SECONDS, overdue)
    except (AttributeError, RuntimeError):
        continue_state(app).wait_timer = None
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
    from .continue_runtime import continue_state
    state = continue_state(app)
    notice, state.notice = state.notice, None
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
    from .continue_runtime import continue_state
    page = getattr(app, "page", None)
    view = (page, getattr(app, "_page_token", None))
    if continue_state(app).wait_view == view:
        continue_state(app).notice = {**notice, "anchor": view}
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
    from .continue_runtime import continue_state
    state = continue_state(app)
    timer, state.wait_timer = state.wait_timer, None
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

    async def prepare_main_route_mix(self):
        """Startup warm-up, last step (design 11 section 16, M10): the main route mix plan of the current
        derivation tree on a worker thread when it has none (route_mix.service); home is refreshed once
        when one was saved. Only the started app (_background_assembly) runs it, not fixtures or tools, and
        never while a backup runs or the app closes; a failure, getting the service included, returns False."""
        if (not getattr(self, "_background_assembly", False) or getattr(self, "_backup_closing", False)
                or getattr(self, "_backup_busy", False) or not callable(getattr(self, "study_path_service", None))):
            return False
        from .route_mix.service import prepare_in_background
        saved = await prepare_in_background(self.study_path_service)
        if saved and self.page == "home" and not getattr(self, "_backup_closing", False):
            self.refresh_home_actions()
        return saved

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
        from .home_runtime import home_inputs, home_service, run_action
        model = home_service(self, queue_kinds=lambda pending: KanaMemory._queue_kinds(self, pending),
                             word_count=lambda pending: KanaMemory._word_drill_remaining(self, pending)
                             ).overview(home_inputs(self))
        showing = self._study_home

        def on_action(action, widget=None):
            return run_action(self, action, widget, open_word_drill=lambda: KanaMemory.open_home_word_drill(self))
        draw_home(model, action_slot=showing.action_slot, secondary_slot=showing.secondary_slot,
                  stats_label=showing.stats_label, on_action=on_action)
        showing.next_change_at = model.next_change_at
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
        from .continue_runtime import continue_action, perform
        from .next_step.model import Failed
        before = {name: getattr(self, name, None) for name in ("_learning_item", "_learning_current")}
        notice = take_enrollment_notice(self)
        try:
            result = continue_action(self, KanaMemory, widget, checkpoint_saved=_checkpoint_saved, **kwargs)
            place_enrollment_notice(self, notice)
            return result
        except Exception as exc:
            if not getattr(exc, "_continue_error_shown", False):
                from .diagnostic_events import record_error
                record_error("kana_queue_assembly", "read", exc)
                perform(self, Failed(exc, _course_title(self, before)))
            # Still raised: the handler/crash log keeps the code location.
            raise

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
