"""The continue action (继续学习) on the app side: it decides the next step, then performs it.

continue_action prepares the queues (the checkpoint, the main lane, the kana
queue, the kind policy, the next kana question) and may end on one of the two
background waits; decide then reads what is due and returns a NextStep
(next_step.model) without drawing anything; perform makes the one call that
step stands for: a learning line, the take-over of extra practice, one of the
three end pages (next_step.ui.end_pages), the reference page, a waiting page,
nothing, or the error page. The read contexts are opened here and handed to
both, so a line entry gets the same live context as before.

What the action keeps between its moments (the enrollment notice, the waiting
page and its timer, the wait tasks) lives in one ContinueState on the app,
reached only through continue_state(app).

This module reads the app object and imports neither Toga nor the control
components nor the app module: the App class arrives as ``K`` from the
mixin's entry method, and every line entry and page method is looked up on
the app object at the moment of the call. Module globals that tests reach
through tests/support/continue_support.py: summarize_queue_availability,
run_in_background, decide and share_dispatch. Imports made at call time stay
at call time, so their own patch targets keep working.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .next_step.model import (STAY_CHECKPOINT, STAY_KINDS, STAY_LANE, CourseItem, EndActionKind, ExtraOr, Failed,
                              KanaItem, NoKanaKind, Reference, RoundEnd, RouteEnd, Stay, WaitCourseHead)
from .practice_availability import summarize_queue_availability
from .practice_preferences import clean_preferences, enabled_preference_kinds
from .practice_worker import BackgroundTicket, run_in_background
from .study_path_runtime import (_UNREAD, CourseCurrentResume, PreparedKana, StudyPathRuntimeMixin,
                                 cancel_continue_wait, course_current_in_worker, show_continue_error,
                                 skipped_teaching_ids)


@dataclass(eq=False)
class ContinueState:
    """What the continue action keeps on the app between its moments.

    ``notice``: the enrollment notice armed for the next continue action (or
    carried over a waiting page); ``wait_view``: (page name, page token) of the
    last waiting page shown; ``wait_timer``: the handle of that page's "takes
    longer" timer; ``kana_task`` / ``course_task``: the tasks of the two
    background waits (the kana queue assembly and the course head of a sealed
    round). None of them is restored by Back.
    """

    notice: object = None
    wait_view: object = None
    wait_timer: object = None
    kana_task: object = None
    course_task: object = None


def continue_state(app) -> ContinueState:
    """The app's ContinueState, created on first use (anything else in its place is replaced)."""
    state = getattr(app, "_continue_state", None)
    if not isinstance(state, ContinueState):
        state = app._continue_state = ContinueState()
    return state


@dataclass(frozen=True)
class RouteEndView:
    """What the page 本次学习已保存 shows, read after extra practice did not take over.

    ``note``: B10's next section under breadth (None otherwise), added in
    ``note_route_id``; ``overview`` / ``route_id`` / ``others``: the arguments
    of the route's next-section rows when ``keyed`` (a non-core route or other
    routes to list), else the rows are made from the core overview alone.
    """

    note: object
    note_route_id: str | None
    overview: dict
    route_id: str | None
    others: object
    keyed: bool


def continue_action(app, K, widget=None, *, checkpoint_saved=False, **kwargs):
    """Body of continue_study_path; K (the App class) is passed in by its caller."""
    from .domain import _utc
    from .review_dispatch import kana_resume_priority, stored_review_priority
    resume = kwargs.get("_course_resume")
    if resume is not None:
        resume_course(app, K, resume)
        return
    if not checkpoint_saved and app._checkpoint() is False:
        perform(app, Stay(STAY_CHECKPOINT))
        return
    if K._activate_practice_lane(app, "main") is False:
        perform(app, Stay(STAY_LANE))
        return
    # Design 12 E1: an early batch of extra practice left by an earlier launch ends before anything is served.
    from .extra_practice_runtime import end_left_early_batch
    end_left_early_batch(app)
    from .practice_round import begin, sealed, seal, frozen_priority, native_key, mark_finished
    begin(app.store, restart_finished=not checkpoint_saved)
    # Personal-content head/hold reads share one index (WP7a) up to the
    # queued course questions; writes run in transactions (read live) and
    # any local write renews the index before the next lookup.
    with app.learning_store.read_operation():
        app.learning_store.ensure_prerequisites()
        service = app.study_path_service()
        preferences = clean_preferences(getattr(app, "_practice_preferences", {}))
        existing = app.store.load_session_progress(lane="main", lazy_questions=True)
        kinds = (K._queue_kinds(app, existing) if existing
                 else enabled_preference_kinds(preferences))
        if sealed(app.store):
            queued = {"session_id": existing["session_id"] if existing else None}
        else:
            audio, handwriting = bool(app.platform.tts_ready), app.model_state == "ready"
            plan = kwargs.get("_kana_plan")
            # WP5: assemble the kana queue on a worker connection when the app
            # enabled it; this action then ends on a short wait page and resumes
            # here with the plan (KanaMemory._finish_kana_queue_assembly).
            if plan is None and K._plan_kana_queue_in_background(app, service, kinds,
                    audio_available=audio, handwriting_available=handwriting,
                    attempt=kwargs.get("_kana_attempt", 0)):
                return
            queued = service.ensure_kana_queue(kinds, audio_available=audio,
                handwriting_available=handwriting, lazy_questions=True, plan=plan)
        if queued["session_id"]:
            # Do not overwrite the previous policy of an existing legacy queue.
            pending = app.store.load_session_progress(lazy_questions=True)
            if pending and app.store.get_setting("practice_queue_kinds", {}).get("session_id") != queued["session_id"]:
                K._set_queue_kind_policy(app, queued["session_id"], kinds)
        app._queue_missing_learning_questions()
    pending = app.store.load_session_progress(lane="main", lazy_questions=True)
    now = _utc(None)
    prepared_kana = None
    # Design 12 H3: the focus scope orders both lanes when this round is collected.
    scope = StudyPathRuntimeMixin._focus_scope(app)
    if pending and K._queue_kinds(app, pending):
        pending = K._reconcile_practice_kinds(app, pending)
        if pending is False:
            perform(app, Stay(STAY_KINDS))
            return
        # Select the same eligible head that resume will display. This
        # keeps cross-lane ordering consistent with pauses and timed steps.
        kinds = K._queue_kinds(app, pending)
        pending = (None if pending.get("completed") else
                   app.store.prepare_next_question(pending["session_id"], now=now, lazy_questions=True, fair_new=True,
                                                    **({} if scope is None else {"scope_contents": scope})))
        if isinstance(pending, dict):
            prepared_kana = (pending, kinds)
    # WP7a: in a sealed round, course candidate filtering/order runs on a
    # worker with the frozen round snapshot; this action ends on the short
    # wait page and resumes through resume_course.
    if (not kwargs.get("_course_inline")
            and (step := course_wait_request(app, pending, now, prepared_kana)) is not None and perform(app, step)):
        return
    # One read context for the rest of this synchronous action: course
    # dispatch, protected-teaching probes and the course page share it.
    with app.learning_store.read_operation(now=now) as read:
        perform(app, decide(app, K, pending, now, read, service, prepared_kana, scope=scope), read=read)


def course_wait_request(app, pending, now, prepared_kana):
    """The sealed-round course current() can run on a worker: the wait to start, or None.

    Only with startup's background assembly, a running loop, real stores,
    the main lane of an active sealed round, no open transaction and no
    UI-bound example store (material checks would differ on the worker).
    The wait page must not write: otherwise the inline path continues.
    """
    import asyncio
    from .content import ContentRepository
    from .domain import StudyStore
    from .material_qualification import bound_resources
    from .practice_round import RoundSnapshot
    if (not getattr(app, "_background_assembly", False) or not isinstance(app.repo, ContentRepository)
            or skipped_teaching_ids(app)
            or not isinstance(app.store, StudyStore) or app.store.in_transaction
            or getattr(app.store, "practice_lane", "main") != "main"
            or bound_resources(app.store)[1] is not None):
        return None
    snapshot = RoundSnapshot.capture(app.store)
    if snapshot is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return WaitCourseHead(loop, snapshot, pending, now, prepared_kana)


async def finish_course_wait(app, resume):
    """Back on the UI thread: use the worker value only if page, data and round still match."""
    from dataclasses import replace
    value, error = _UNREAD, None
    try:
        value = await run_in_background(app.repo, app.store, course_current_in_worker, {
            "snapshot": resume.snapshot, "enabled_kinds": list(resume.enabled_kinds),
            "now": resume.now.isoformat(), "fair_new": True})
    except Exception as exc:
        error = exc
        from .diagnostic_events import record_error
        record_error("kana_queue_assembly", "read", exc)
    if not resume.ticket.page_current(getattr(app, "page", None), app._page_token):
        return
    cancel_continue_wait(app)
    try:
        if (error is None and resume.ticket.data_current(app.store) and resume.snapshot.current(app.store)
                and tuple(app.learning_preferences()["enabled"]) == resume.enabled_kinds):
            app.continue_study_path(_checkpoint_saved=True, _course_resume=replace(resume, value=value))
        else:
            # Changed data/plan or a worker error: recompute the whole action inline.
            app.continue_study_path(_checkpoint_saved=True, _course_inline=True)
    except Exception as exc:
        continue_failed(app, exc, source="async_task")


def resume_course(app, K, resume):
    """Finish the paused continue with the seeded course head (inline if refused)."""
    enabled = list(resume.enabled_kinds)
    with app.learning_store.read_operation(now=resume.now) as read:
        seeded = (resume.snapshot.current(app.store)
                  and read.seed(app.learning_store.current_key(enabled, resume.now, fair_new=True),
                                resume.value, resume.ticket.revision))
        if seeded:
            perform(app, decide(app, K, resume.pending, resume.now, read, app.study_path_service(),
                                resume.prepared_kana), read=read)
            return
    app.continue_study_path(_checkpoint_saved=True, _course_inline=True)


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
    perform(app, Failed(error))


def share_dispatch(app, K, pending, current, normal_kana, kana_priority, course_priority, now, read, skipped,
                   kana_kinds, scope=None):
    """Breadth-first dispatch (design 12 B2-B5): (protected (lane, head) or None, lane or None).

    A protected head is already promoted and is shown like the protected
    first-teaching block; a lane is the review side the share rule chose;
    (None, None) leaves the existing lane choice. An explicit start keeps
    its rule; heads below tier 3 (feedback, drafts, short steps, context
    words, a promoted block) and new work without a due review keep the
    existing order. The study clock reads every answer from the store.
    ``scope``: the focus scope's content (design 12 H2), None for none:
    the kinds with a candidate inside it share the time first.
    """
    from .arrangement.model import Unit
    from .arrangement.sources.candidates import DispatchCandidates, LaneHead
    from .first_teaching_dispatch import explicit_start_head
    from .practice_round import promote, promote_initial, sealed
    enabled = app.learning_preferences()["enabled"]
    if not sealed(app.store):
        explicit = explicit_start_head(app.store, app.learning_store, normal_kana, current,
            enabled_kinds=enabled, kana_kinds=kana_kinds, now=now, projection=read)
        if explicit and not (explicit[0] == "course" and explicit[1].get("type") == "instruction"
                             and explicit[1].get("content", {}).get("id") in skipped):
            lane, head = explicit
            if lane == "course":
                promote_initial(app.store, course_id=head["question"]["id"])
            elif pending:
                promote_initial(app.store, session_id=pending["session_id"], question=head["question"])
            return explicit, None
    normal = app.store.teaching_queries.choose_lane(kana_priority, course_priority,
                 course_started=bool(current and current.get("new_dispatch_continue")))
    priority = kana_priority if normal == "kana" else course_priority if normal == "course" else None
    if priority is None or priority[0] != 3:
        return None, None
    arrangement = StudyPathRuntimeMixin.study_arrangement(app)
    source = DispatchCandidates(app.store, app.learning_store, pending=pending,
        kana=LaneHead(normal_kana, kana_priority) if normal_kana else None,
        course=LaneHead(current, course_priority) if course_priority is not None else None,
        enabled_kinds=enabled, kana_kinds=kana_kinds, now=now, projection=read, skipped=skipped,
        memo=arrangement.probe_memo, scope_contents=scope)
    dispatcher = arrangement.dispatcher
    decision = dispatcher.decide(source, now)
    if decision.unit is Unit.NEW:
        lane, head = decision.candidate.ref
        # A kana candidate needs a session: without one kana_kinds is () and
        # the kana teaching probes return None, so "pending is None" below is
        # never reached with a kana candidate.
        if (promote(app.store, course_id=head["question"]["id"]) if lane == "course" else
                pending is not None and promote(app.store, session_id=pending["session_id"],
                                                question=head["question"])):
            return (lane, head), None
        # Not registered in this round: new content has no candidate now.
        source.promotion_refused()
        decision = dispatcher.decide(source.without_new(), now)
    return None, {Unit.COURSE: "course", Unit.KANA: "kana"}.get(decision.unit)


def decide(app, K, pending, now, read, service, prepared_kana, *, scope=_UNREAD):
    """The next step of this continue action, with the round's writes made in order; nothing is drawn.

    ``scope``: the focus scope's content the caller read for this action (None: no scope); read here when not given.
    """
    from .review_dispatch import kana_resume_priority, stored_review_priority
    from .practice_round import sealed, seal, frozen_priority, native_key, mark_finished
    if scope is _UNREAD:
        scope = StudyPathRuntimeMixin._focus_scope(app)
    scoped = {} if scope is None else {"scope_content_ids": scope}
    # Sealed before this action: its queueing step could add nothing.
    sealed_before = sealed(app.store)
    availability = summarize_queue_availability(app.store, pending,
        allowed_kinds=K._queue_kinds(app, pending) if pending else None, now=now)
    skipped = skipped_teaching_ids(app)
    current = app.learning_store.current(app.learning_preferences()["enabled"], now=now, fair_new=True,
                                         projection=read, **({"skip_teaching_ids": skipped} if skipped else {}),
                                         **scoped)
    kana_priority = None
    if availability.ready_count and pending:
        q = pending["questions"][pending["question_index"]]
        kana_priority = kana_resume_priority(pending.get("state"))
        if kana_priority is None:
            kana_priority = stored_review_priority(app.store,
                app.store.get_scheduling_card(q.content_id, q.kind, q.script), now=now)
            from .kana_context_words import context_priority
            # A short contextual word can interrupt ordinary review, but
            # never an already opened course teaching block.
            if not (current and current.get("new_dispatch_continue")):
                kana_priority = context_priority(q, app.store) or kana_priority
            if kana_priority[0] == 4:
                from .kana_learning_intro import should_introduce
                if should_introduce(app, q):
                    kana_priority = (5, 0)
    course_priority = (current.get("dispatch_priority", (4 if current["type"] == "question" else 5, 0))
                       if current and current["type"] in {"question", "instruction"} else None)
    if kana_priority is not None:
        kana_priority = frozen_priority(app.store, "cross-kana",
            (pending["session_id"], native_key(q)), kana_priority)
    if course_priority is not None:
        course_priority = frozen_priority(app.store, "cross-course",
            current["question"]["id"], course_priority)
    from .first_teaching_dispatch import choose_protected
    normal_kana = ({"type": "feedback" if pending.get("state", {}).get("feedback_pending") else
                    "introduction" if kana_priority[0] == 5 else "question",
                    "question": pending["questions"][pending["question_index"]],
                    "pending": pending, "state": pending.get("state", {}),
                    "session_id": pending["session_id"], "question_index": pending["question_index"],
                    "dispatch_priority": kana_priority} if pending and kana_priority else None)
    share_lane = None
    if StudyPathRuntimeMixin.study_arrangement(app).dispatcher.active():
        # Breadth first (design 12 B2-B5): new content and review share time.
        protected, share_lane = share_dispatch(
            app, K, pending, current, normal_kana, kana_priority, course_priority, now, read, skipped,
            K._queue_kinds(app, pending) if pending else (), scope)
    else:
        protected = (None if sealed(app.store) else choose_protected(app.store, app.learning_store, normal_kana, current,
            enabled_kinds=app.learning_preferences()["enabled"],
            kana_kinds=K._queue_kinds(app, pending) if pending else (), now=now, projection=read,
            **({} if scope is None else {"scope": scope})))
        if (protected and protected[0] == "course" and protected[1].get("type") == "instruction"
                and protected[1].get("content", {}).get("id") in skipped):
            protected = None
        if protected:
            from .practice_round import promote_initial
            protected_lane, protected_head = protected
            if protected_lane == "course":
                promote_initial(app.store, course_id=protected_head["question"]["id"])
            elif pending:
                promote_initial(app.store, session_id=pending["session_id"], question=protected_head["question"])
    seal(app.store)
    # Proof that the kana head prepared above is still the stored queue:
    # taken after seal (an in-memory step), checked again before reuse.
    token = (PreparedKana.capture(app.store, *prepared_kana) if prepared_kana and not protected else None)
    if protected:
        protected_lane, protected_head = protected
        if protected_lane == "course":
            return CourseItem(selected_current=protected_head)
        else:
            selected_question = protected_head["question"]
            return KanaItem(preserve_audio=True,
                first_teaching_targets=protected_head.get('teaching_targets', {(selected_question.content_id,selected_question.script)}))
    # Design 12 H2: between the two lane heads the one inside the focus scope first (no scope: unchanged).
    from .arrangement.sources.candidates import head_in_scope
    inside = ({} if scope is None else {"inside": (head_in_scope("kana", normal_kana, scope),
                                                   head_in_scope("course", current, scope))})
    if inside.get("inside") == (False, True) and normal_kana:
        # H4: an opened kana teaching block outside the scope goes on, as an opened course block does.
        from .first_teaching_kana import opened_block
        inside["kana_started"] = opened_block(app.store, normal_kana["question"], now)
    lane = share_lane or app.store.teaching_queries.choose_lane(kana_priority, course_priority,
                       course_started=bool(current and current.get("new_dispatch_continue")), **inside)
    if lane == "kana":
        return KanaItem(preserve_audio=True, prepared=token)
    elif lane == "course":
        return CourseItem()
    else:
        # Design 12 E1: while extra practice is on, an end or "only waiting" outcome is its next step.
        # A round with nothing left ends, also when all that remains is
        # course work it could not admit (never a dead "题型已关闭" page).
        left_out = sealed_before and StudyPathRuntimeMixin._sealed_round_left_out(app, current, read)
        if not availability.ready_count and not availability.waiting_count and (not current or left_out):
            mark_finished(app.store)
            from .practice_round import finished
            if finished(app.store):
                return ExtraOr(RoundEnd())
        # Reference-only symbols have no made-up independent audio cards.
        path = service.overview()
        reference = service.next_reference_entry()
        if reference:
            return Reference(reference["id"])
        elif (availability.waiting_count and availability.next_due_utc
              and (not current or not current.get("due_at_utc")
                   or datetime.fromisoformat(availability.next_due_utc)
                   < datetime.fromisoformat(current["due_at_utc"]))):
            return ExtraOr(KanaItem(prepared=token))
        elif current:
            return ExtraOr(CourseItem()) if current.get("type") == "waiting" else CourseItem()
        elif availability.waiting_count:
            return ExtraOr(KanaItem(prepared=token))
        elif availability.blocked_count:
            return KanaItem(prepared=token)
        elif any(e["kind"] == "kana" and e["state"] in {"added", "started"}
                 for stage in path["stages"] for unit in stage["units"] for section in unit["sections"] for e in section["entries"]):
            return NoKanaKind()
        else:
            # Design 10 section 8.2: the current route's next section;
            # without one, the other routes' next sections (at most 3).
            return ExtraOr(RouteEnd(service, path))


def route_end_view(app, service, path) -> RouteEndView:
    """What 本次学习已保存 shows: the current route's next section; without one, the other routes' (at most 3)."""
    from .study_path_display import active_route, other_route_rows, route_key
    from .arrangement.model import RouteFacts
    current = active_route(service)
    shown = path if route_key(current) is None else service.overview(route_id=current)
    others = other_route_rows(service, current) if not shown.get("next_section") else ()
    # B10 under breadth: the next section first (presenter.end_note is None otherwise).
    note = StudyPathRuntimeMixin.study_arrangement(app).presenter.end_note(RouteFacts.from_overview(shown))
    keyed = bool(route_key(current) or others)
    return RouteEndView(note, (note.route_id or current) if note is not None else None,
                        shown if keyed else path, current, others, keyed)


def run_end_action(app, action, widget=None):
    """What an end-page button does when pressed; the method is looked up on the app at that moment."""
    kind = action.kind
    if kind is EndActionKind.NEW_ROUND:
        return app.continue_study_path(widget)
    if kind is EndActionKind.ADD_SECTIONS:
        from .study_path_display import route_kwargs
        return app._add_study_sections([action.section_id], **route_kwargs(action.route_id))
    if kind is EndActionKind.PRACTICE_HUB:
        return app.show_practice_hub(widget)
    if kind is EndActionKind.SETTINGS:
        return app.show_settings(widget)
    if kind is EndActionKind.ROUTE_PAGE:
        return app.show_study_path(widget)
    if kind is EndActionKind.HOME:
        return app.leave_learning(widget)
    raise ValueError(f"unknown end-page action {action!r}")


def perform(app, step, *, read=None) -> bool:
    """Make the one call ``step`` stands for, with ``read`` (this action's read context) for the line entries.

    True, except for a wait whose page could not be shown or whose data moved
    while it was drawn: False, and the action goes on inline.
    """
    if isinstance(step, ExtraOr):
        from .extra_practice_runtime import take_over
        if take_over(app):
            return True
        step = step.fallback
    if isinstance(step, Stay):
        return True
    if isinstance(step, CourseItem):
        app.continue_learning(**({} if step.selected_current is None else {"_selected_current": step.selected_current}),
                              _projection=read)
        return True
    if isinstance(step, KanaItem):
        app.resume_practice(**({"preserve_audio": True} if step.preserve_audio else {}),
                            **({} if step.first_teaching_targets is None else
                               {"_first_teaching": True, "_first_teaching_targets": step.first_teaching_targets}),
                            **({} if step.prepared is None else {"_prepared": step.prepared}))
        return True
    if isinstance(step, Reference):
        app.show_study_reference(step.entry_id)
        return True
    if isinstance(step, Failed):
        show_continue_error(app, step.error, title=step.title)
        return True
    if isinstance(step, WaitCourseHead):
        return _start_course_wait(app, step)

    def on_action(action, widget=None):
        return run_end_action(app, action, widget)
    if isinstance(step, RoundEnd):
        from .extra_practice_runtime import extra_button_rows
        from .next_step.display import round_end_model
        from .next_step.ui.end_pages import round_end_page
        model = round_end_model()
        next_content = app.learning_next_content_controls()
        extra = extra_button_rows(app)
        app.screen(model.page, model.title,
                   *round_end_page(model, next_content_rows=next_content, extra_rows=extra, on_action=on_action))
        return True
    if isinstance(step, NoKanaKind):
        from .next_step.display import no_kana_kind_model
        from .next_step.ui.end_pages import no_kana_kind_page
        model = no_kana_kind_model()
        app.screen(model.page, model.title, *no_kana_kind_page(model, on_action=on_action))
        return True
    if isinstance(step, RouteEnd):
        from .extra_practice_runtime import extra_button_rows
        from .next_step.display import study_end_model
        from .next_step.ui.end_pages import study_end_page
        view = route_end_view(app, step.service, step.path)
        next_rows = (app._path_next_controls(view.overview, route_id=view.route_id, others=view.others)
                     if view.keyed else app._path_next_controls(step.path))
        extra = extra_button_rows(app)
        model = study_end_model(view.note, view.note_route_id)
        app.screen(model.page, model.title,
                   *study_end_page(model, next_rows=next_rows, extra_rows=extra, on_action=on_action))
        return True
    raise TypeError(f"unknown next step {step!r}")


def _start_course_wait(app, step) -> bool:
    """Draw the waiting page and start the course-head worker; False when the inline path must go on."""
    from .next_step.display import KANA_ASSEMBLY_PAGE
    from .study_path_runtime import show_continue_wait
    if (not show_continue_wait(app, KANA_ASSEMBLY_PAGE, step.loop)
            or tuple(app.store.read_revision()) != step.snapshot.revision):
        return False
    ticket = BackgroundTicket.capture(app.page, app._page_token, app.store)
    resume = CourseCurrentResume(ticket, step.snapshot, step.pending, step.now, step.prepared_kana,
                                 tuple(app.learning_preferences()["enabled"]))
    from .draft_debounce import create_task_awake
    continue_state(app).course_task = create_task_awake(
        step.loop, finish_course_wait(app, resume))
    return True
