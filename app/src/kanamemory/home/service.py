"""HomeOverviewService: what the home page 今日学习 reads and computes, without the page (design 13 S3, C1).

Its reads are the home's from before the move, in the same order and as
often (rule C5; reads are not merged here): the current route's overview and
the other routes, the main checkpoint and its kana summary, one learning read
operation for the counts, the unqueued course preview and the focus row, the
unqueued kana preview, the arrangement's rows, the word-drill checkpoint. The
four moments stay as they were: the read operation's, the kana preview's own,
the kana summary's own and one for the arrangement's rows. The result is a
home.display.HomeModel. Collaborators come in through the constructor: the
two stores, and callables for what belongs to the app's assembly or settings:
``paths()`` the current StudyPathService, ``presenter()`` the arrangement's
presenter (the StoreArrangement of the store, launch notes included),
``costs()`` the add page's AdmissionCosts (its plan preview is shared with
the add page), ``repository()``, ``course_kinds()`` the enabled course kinds,
``queue_kinds(pending)`` a pending main queue's kinds and ``word_count(pending)``
the word-drill count. No app object, no Toga.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from .. import app_clock
from ..arrangement.model import RouteFacts
from ..arrangement.rules.review_filter import ends_in_scope_done, waits_within
from ..arrangement.services.presenters import no_new_content
from ..home_counts import home_counts
from ..practice_availability import summarize_queue_availability
from ..study_lines import LINE_TITLES, SUGGEST_MARK, RouteLines, SectionNames, readiness_ids, route_lines
from ..study_path_display import active_route, other_route_rows, route_kwargs, route_title
from .display import HomeModel, OtherRoute, compose
from .model import HomeInputs
from .previews import UNREAD, course_preview, kana_preview


def suggestion_mark(suggestion) -> str:
    """建议先加 · 词汇 / 建议先加 · 语法 of a study_lines.Suggestion; "" without one."""
    return "" if suggestion is None else SUGGEST_MARK + " · " + LINE_TITLES[suggestion.line]


class HomeOverviewService:
    """The home's reads and rules for one pair of learner stores; built per refresh, it keeps nothing but
    drawn_lines of its last overview (what of the drawing the line readiness decides: lines_changed)."""

    def __init__(self, store, learning_store, *, repository: Callable[[], Any], paths: Callable[[], Any],
                 presenter: Callable[[], Any], costs: Callable[[], Any], course_kinds: Callable[[], Sequence[str]],
                 queue_kinds: Callable[[dict], Sequence[str]], word_count: Callable[[Any], int],
                 arrangement: Callable[[], Any] | None = None):
        self._store, self._learning = store, learning_store
        self._repository, self._paths, self._presenter, self._costs = repository, paths, presenter, costs
        self._course_kinds, self._queue_kinds, self._word_count = course_kinds, queue_kinds, word_count
        self._arrangement = arrangement   # the StoreArrangement (its review filter and extra practice), or None

    def overview(self, inputs: HomeInputs) -> HomeModel:
        """The whole home page as the learner records are now; reads only.

        Design 12 H10: the reads run under the learner's review filter of this moment (only what 继续学习
        will serve is in the main numbers); what it leaves out is counted apart (the focus row's 范围外到期 and
        the 暂不复习 line), and with 只做范围内 acting and nothing inside left the primary button says so.
        """
        arrangement = self._arrangement() if self._arrangement is not None else None
        if arrangement is None:
            return self._overview(inputs, None, None)
        from ..domain import _utc
        serve = arrangement.review_filter.current(_utc(None))
        with arrangement.serving(serve):
            return self._overview(inputs, arrangement, serve)

    def _overview(self, inputs: HomeInputs, arrangement, serve) -> HomeModel:
        store, learning = self._store, self._learning
        service = self._paths()
        # Design 10 section 8.3: the current route's next section and counts;
        # in-progress work of every enabled route keeps "继续学习".
        current = active_route(service)
        route = route_kwargs(current)
        path = service.overview(**route)
        others = other_route_rows(service, current)
        short = route_title(service, current)
        pending = store.load_session_progress(lane="main", lazy_questions=True)
        kana_kinds = self._queue_kinds(pending) if pending else None
        availability = summarize_queue_availability(store, pending, allowed_kinds=kana_kinds)
        presenter = self._presenter()
        # One read context for this home refresh: counts and the unqueued
        # preview share the moment, prerequisite evidence and exposure times.
        with learning.read_operation() as read:
            enabled = self._course_kinds()
            counts = learning.counts(enabled, projection=read)
            courses, teaching = self._course_preview(projection=read)
            # The same AvailabilityView that filtered the counts above.
            view = learning.availability(projection=read)
            orphans = len(view.orphaned_ids)
            # UI6 focus row: one settings read without a focus; only a resolving scope reads the due
            # reviews of "重点之外到期 N 题", under the kinds and the availability this refresh uses.
            apart = (presenter.apart(read.moment(), course_kinds=enabled, kana_kinds=kana_kinds,
                                     course_exclude=view.unavailable_ids, unqueued_course=teaching["planned"])
                     if serve is not None else None)
            focus = presenter.home_focus(read.moment(), course_kinds=enabled, kana_kinds=kana_kinds,
                                         course_exclude=view.unavailable_ids, unqueued_course=teaching["planned"],
                                         apart=apart)
        for name in ("blocked", "blocked_content", "blocked_material"):
            counts[name] += courses[name]
        unqueued = self.unqueued_reviews(inputs, pending)
        # Review 2026-11-08 sections 4b/4c/6: one rule for "可答"/"待到期"/"可开始初学".
        summary = home_counts(kana_ready=availability.ready_count, course_ready=counts["ready"],
            unqueued_course_ready=courses["ready"], unqueued_kana_ready=unqueued["ready"],
            waiting=(availability.waiting_count, counts["waiting"], courses["waiting"], unqueued["waiting"]),
            queued_teaching=counts["instruction"], queued_teaching_pairs=teaching["queued"],
            unqueued_teaching=teaching["unqueued"])
        counts["instruction"] = summary.instruction
        ready, waiting = summary.ready_total, summary.waiting
        # The home timer's moment: every source holds future instants only.
        dues = [date for date in (availability.next_due_utc, counts.get("next_due_at_utc"),
                                 unqueued["next_due"], courses["next_due"]) if date]
        next_change_at = min(app_clock.localize(datetime.fromisoformat(d)) for d in dues) if dues else None
        next_section = path.get("next_section")
        others_in_progress = any(row["in_progress_sections"] for row in others)
        inside = bool(ready or counts["instruction"] or waiting or availability.blocked_count or counts["disabled"]
                      or counts["blocked"] or counts.get('blocked_material') or counts.get('blocked_input'))
        has_work = bool(inside or path["in_progress_sections"] or others_in_progress)
        if serve is not None:
            from ..domain import _utc
            # Design 12 H12: under the learner's filter the home follows the dispatch's one decision on the
            # filtered candidates (rules/review_filter.ends_in_scope_done): a question, the wait page (short steps
            # waiting inside), the offer of a next section the filter allows, or 范围内的都做完了. Sections in
            # progress outside the filter are no work it serves; a next section it does not allow is not offered.
            # The disabled count is the record's, not the scope's: it does not keep the button.
            offer = arrangement.review_filter.next_offer(serve, current, path, others)
            next_section = offer[1] if offer is not None and offer[0] == current else None
            serveable = bool(ready or counts["instruction"] or availability.blocked_count or counts["blocked"]
                             or counts.get('blocked_material') or counts.get('blocked_input'))
            if serve.limits_to_scope and not serveable:
                # 继续学习 serves a ready consolidation deck of the allowed work before its end page (N5).
                serveable = bool(arrangement.consolidation.due_deck(_utc(None), nothing_else=True))
            has_work = serveable or bool(waiting)
            scope_done = ends_in_scope_done(serve, can_serve=serveable,
                                            waiting=waits_within(next_change_at, app_clock.localize(_utc(None))),
                                            next_section=offer is not None)
        else:
            scope_done = False
        # The first part of the two-line design: each line's next section (a next section the filter does not
        # allow is not offered) and the suggestion B10's note names; the note exists under breadth only.
        from ..domain import _utc
        # Design 12 LS8: rule (b) of the suggestion follows the line behind its share (the note is breadth only).
        behind = None if arrangement is None else arrangement.dispatcher.line_behind(_utc(None), breadth=True)
        # Sections that share a title are named apart wherever the home offers one (study_lines.section_name).
        names = SectionNames(path)
        lines = self._lines(service, path, behind, names=names)
        self.drawn_lines = (path, behind, self.line_view(lines))
        listed_lines = [(item.line_title, item.section_id, item.title) for item in lines.lines
                        if serve is None or arrangement.review_filter.offers_section(serve, current, item.section_id)]
        suggestion = lines.suggestion if lines.suggestion is not None and lines.suggestion.line is not None else None
        # Design 12 UI2, UI3 (B10), UI5: the arrangement's rows, from its presenter.
        facts = RouteFacts.from_overview(path, suggestion, suggestion_mark(suggestion), name=names)
        nothing_new = no_new_content(has_work=has_work, teaching_ready=summary.instruction, overview=path,
                                     others_in_progress=others_in_progress)
        from ..domain import _utc
        rows = presenter.home_model(_utc(None), facts, no_new_content=nothing_new)
        # The line button the add page's suggestion names carries 建议先加 while both are drawn: under the
        # orientation home_model read (rows.breadth, no read of its own), as the add page suggests.
        marked = lines.suggestion if rows.breadth else lines.route_next
        marked = marked if marked is not None and marked.line is not None else None
        word_count = self._word_count(store.load_session_progress(lane="word_romaji", lazy_questions=True))
        # Every other route with work counts as work above; the column lists two of them.
        listed = [OtherRoute(row["route_id"], row["title"], row["in_progress_sections"],
                             row["next_section"]["title"] if row["next_section"] else None) for row in others[:2]]
        return compose(
            rows=rows, focus=focus, route=facts, no_new_content=nothing_new, route_id=current, route_title=short,
            core=not route, has_work=has_work, ready=ready, waiting=waiting, instruction=counts["instruction"],
            blocked_content=counts["blocked_content"], blocked_material=counts.get('blocked_material'),
            blocked_input=counts.get('blocked_input'), blocked_kana=availability.blocked_count,
            next_section=(next_section["id"], names(next_section)) if next_section else None, others=listed,
            orphans=orphans, libraries_page=inputs.libraries_page, word_count=word_count,
            completed=path['required_completed_sections'], total=path['required_total_sections'],
            added=path['required_added_sections'], partial=path.get("required_partial_sections"),
            next_change_at=next_change_at, scope_done=scope_done,
            paused_line=presenter.paused_line(apart) if apart is not None else None,
            extra_entry=self._extra_entry(arrangement), line_sections=listed_lines,
            suggested_section=(marked.section_id, SUGGEST_MARK) if marked is not None else None)

    @staticmethod
    def line_view(lines: RouteLines) -> tuple:
        """What the home draws of lines: each line's (title, section id, title) and B10's suggestion with
        its mark. The line readiness changes the drawn home only through these."""
        suggestion = lines.suggestion if lines.suggestion is not None and lines.suggestion.line is not None else None
        return (tuple((item.line_title, item.section_id, item.title) for item in lines.lines), suggestion,
                suggestion_mark(suggestion))

    @staticmethod
    def lines_changed(service, drawn) -> bool:
        """Whether the home drawn with drawn (an overview's drawn_lines) would now draw its lines or the
        suggestion differently with the readiness service holds: the same computation, nothing read."""
        path, behind, view = drawn
        # the same names the drawing used (SectionNames of the drawn path), so like is compared with like
        lines = HomeOverviewService._lines(service, path, behind, names=SectionNames(path))
        return HomeOverviewService.line_view(lines) != view

    @staticmethod
    def _lines(service, path, behind: str | None = None, *, names=None) -> RouteLines:
        """study_lines.route_lines of the current route under breadth (B10's note, breadth only, and the mark on a
        line button under breadth; RouteLines.route_next is the mark under 深度优先), with the readiness the path
        service holds in memory (never computed for this); ``behind``: the line behind its share of the
        new-content time (ShareDispatcher.line_behind, None unless the orientation is breadth first), design 12
        LS8; ``names``: the refresh's SectionNames of ``path``."""
        known = getattr(service, "line_readiness", None)
        readiness = known(readiness_ids(path)) if callable(known) else None
        return route_lines(path, readiness=readiness, breadth=True, behind=behind, names=names)

    def next_review_at(self, inputs: HomeInputs) -> datetime | None:
        """The moment of the home's 下次复习 line for a page that ends a round, or None (design 02, round end).

        The overview's sources for its numbers, read the same way and under the same review filter (the
        main checkpoint's kana summary, the course counts and the unqueued course preview in one read
        operation, the unqueued kana preview; planned course dues come from LearningStore.planned_question_due
        through the preview): the earliest future due when nothing can be answered now (no ready question and
        no teaching that can start) and something waits; None otherwise. Reads only.
        """
        arrangement = self._arrangement() if self._arrangement is not None else None
        if arrangement is None:
            return self._next_review_at(inputs)
        from ..domain import _utc
        with arrangement.serving(arrangement.review_filter.current(_utc(None))):
            return self._next_review_at(inputs)

    def _next_review_at(self, inputs: HomeInputs) -> datetime | None:
        from ..home_counts import show_next_review
        summary, dues = self._counted(inputs)
        moment = min(app_clock.localize(datetime.fromisoformat(d)) for d in dues) if dues else None
        return moment if show_next_review(summary.ready_total + summary.instruction, summary.waiting, moment) else None

    def ready_total(self, inputs: HomeInputs) -> int:
        """The home's "现在可答 N 题" now: kana and course questions alike, with the overview's own reads (the main
        checkpoint's kana summary, the course counts and the unqueued course preview in one read operation, the
        unqueued kana preview), under the learner's review filter of this moment. Entering 加练 serves all of
        them first through the main dispatch, and the page 加练 says how many (design 12 E9)."""
        arrangement = self._arrangement() if self._arrangement is not None else None
        if arrangement is None:
            return self._counted(inputs)[0].ready_total
        from ..domain import _utc
        with arrangement.serving(arrangement.review_filter.current(_utc(None))):
            return self._counted(inputs)[0].ready_total

    def _counted(self, inputs: HomeInputs):
        """(home_counts.HomeCounts, the future due instants of every source) as the overview reads them."""
        store, learning = self._store, self._learning
        pending = store.load_session_progress(lane="main", lazy_questions=True)
        kana_kinds = self._queue_kinds(pending) if pending else None
        availability = summarize_queue_availability(store, pending, allowed_kinds=kana_kinds)
        with learning.read_operation() as read:
            counts = learning.counts(self._course_kinds(), projection=read)
            courses, teaching = self._course_preview(projection=read)
        unqueued = self.unqueued_reviews(inputs, pending)
        summary = home_counts(kana_ready=availability.ready_count, course_ready=counts["ready"],
            unqueued_course_ready=courses["ready"], unqueued_kana_ready=unqueued["ready"],
            waiting=(availability.waiting_count, counts["waiting"], courses["waiting"], unqueued["waiting"]),
            queued_teaching=counts["instruction"], queued_teaching_pairs=teaching["queued"],
            unqueued_teaching=teaching["unqueued"])
        dues = [date for date in (availability.next_due_utc, counts.get("next_due_at_utc"),
                                 unqueued["next_due"], courses["next_due"]) if date]
        return summary, dues

    @staticmethod
    def _extra_entry(arrangement) -> bool:
        """加练… (design 12 E9): some learned state exists (ExtraSession.learned: the pool reader stops at the first
        state, the counts are left to the page 加练; a failed read: no entry)."""
        if arrangement is None:
            return False
        from ..domain import _utc
        try:
            return arrangement.extra.learned(_utc(None))
        except Exception as exc:   # a busy database: the home is drawn without the entry
            from ..diagnostic_events import record_error
            record_error("practice_rating", "read", exc)
            return False

    def apart(self):
        """Design 12 H10's apart counts now (rules/review_filter.Apart), under the enabled course kinds, the kana
        kinds in force and this refresh's availability; H8's page 范围内的都做完了 reads its line from them."""
        learning = self._learning
        with learning.read_operation() as read:
            view = learning.availability(projection=read)
            return self._presenter().apart(read.moment(), course_kinds=self._course_kinds(),
                                           course_exclude=view.unavailable_ids)

    def can_continue(self, inputs: HomeInputs) -> bool:
        """Whether 继续学习 can reach anything now (the route page and the end pages ask; reads only).

        The main queue and the counts answer first; only without an answer
        there are the two unqueued previews and the reference entries read.
        """
        store, learning = self._store, self._learning
        pending = store.load_session_progress(lane="main", lazy_questions=True)
        available = summarize_queue_availability(store, pending,
            allowed_kinds=self._queue_kinds(pending) if pending else None)
        with learning.read_operation() as read:
            counts = learning.counts(self._course_kinds(), projection=read)
            if available.ready_count or available.blocked_count or counts["ready"] or counts["instruction"]:
                return True
            courses = self.unqueued_courses(projection=read)
        return bool(courses["ready"] or courses["instruction"]
                    or self.unqueued_reviews(inputs, pending)["ready"]
                    or self._paths().next_reference_entry() is not None)

    def course_due(self) -> int:
        """The records page's "课程到期 N 题": course questions due now, queued or not yet queued, long-term
        (a running planned round does not limit it)."""
        learning = self._learning
        with learning.read_operation() as read:
            counts = learning.counts(self._course_kinds(), projection=read)
            courses, _ = self._course_preview(projection=read, round_limited=False)
        return home_counts(course_ready=counts["ready"], unqueued_course_ready=courses["ready"]).course_due

    def course_ready(self) -> int:
        """The course part of the home's "现在可答 N 题": the overview's own reads (the counts and the unqueued
        preview in one read operation, under the learner's review filter of this moment, a running planned
        round limiting the unqueued side as there)."""
        arrangement = self._arrangement() if self._arrangement is not None else None
        if arrangement is None:
            return self._course_ready()
        from ..domain import _utc
        with arrangement.serving(arrangement.review_filter.current(_utc(None))):
            return self._course_ready()

    def _course_ready(self) -> int:
        learning = self._learning
        with learning.read_operation() as read:
            counts = learning.counts(self._course_kinds(), projection=read)
            courses, _ = self._course_preview(projection=read)
        return home_counts(course_ready=counts["ready"], unqueued_course_ready=courses["ready"]).course_due

    def unqueued_courses(self, projection=None, *, round_limited: bool = True) -> dict:
        """Course abilities without a queued question: {"ready", "waiting", "instruction", "blocked",
        "blocked_content", "blocked_material", "next_due"} (previews.course_preview)."""
        return self._course_preview(projection=projection, round_limited=round_limited)[0]

    def unqueued_teaching(self, projection=None) -> frozenset[str]:
        """Content ids of untaught lessons (or added abilities) the current settings could teach but that
        have no queued question yet, so a running planned round cannot show them (design 12 B5)."""
        return frozenset(self._course_preview(projection=projection, round_limited=False)[1]["unqueued"])

    def unqueued_teaching_states(self, projection=None) -> frozenset[tuple]:
        """unqueued_teaching, each lesson with its teaching state: ("teaching", content id, introduction id, stage,
        the probe profile's diagnostic state). Design 12 N9 ends a frozen round once per such state."""
        import json
        states = set()
        for content_id in self.unqueued_teaching(projection=projection):
            row = self._learning.instruction_row(content_id)
            checkpoint = json.loads(row["checkpoint_json"] or "{}") if row else {}
            profile = checkpoint.get("initial_familiarity") or {}
            states.add(("teaching", content_id, row["introduction_id"] if row else None,
                        row["stage"] if row else None, profile.get("diagnostic_state")))
        return frozenset(states)

    def untaught_lessons(self, now, projection=None) -> int:
        """Added lessons (and added abilities) with a queued question still to be taught (counts' "instruction")."""
        return int(self._learning.counts(self._course_kinds(), now, projection=projection).get("instruction", 0))

    def unqueued_due(self, projection=None) -> frozenset[tuple[str, str]]:
        """(content id, ability) of course abilities that are answerable now but have no queued question
        (their question is created at the next collection), so a running planned round cannot serve them
        (design 12 B5)."""
        return frozenset(self._course_preview(projection=projection, round_limited=False)[1]["due"])

    def unqueued_reviews(self, inputs: HomeInputs, pending=UNREAD) -> dict:
        """Due kana reviews without a queued question: {"ready", "waiting", "next_due"} (previews.kana_preview).

        ``pending``: the main checkpoint as the caller read it; left out, it is read here; None means known empty.
        """
        return kana_preview(self._store, inputs, repository=self._repository, paths=self._paths,
                            queue_kinds=self._queue_kinds, pending=pending)

    def _course_preview(self, *, projection=None, round_limited=True):
        return course_preview(self._store, self._learning, course_kinds=self._course_kinds, costs=self._costs,
                              projection=projection, round_limited=round_limited)
