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
from ..arrangement.services.presenters import no_new_content
from ..home_counts import home_counts
from ..practice_availability import summarize_queue_availability
from ..study_path_display import active_route, other_route_rows, route_kwargs, route_title
from .display import HomeModel, OtherRoute, compose
from .model import HomeInputs
from .previews import UNREAD, course_preview, kana_preview


class HomeOverviewService:
    """The home's reads and rules for one pair of learner stores; built per refresh, it keeps nothing."""

    def __init__(self, store, learning_store, *, repository: Callable[[], Any], paths: Callable[[], Any],
                 presenter: Callable[[], Any], costs: Callable[[], Any], course_kinds: Callable[[], Sequence[str]],
                 queue_kinds: Callable[[dict], Sequence[str]], word_count: Callable[[Any], int]):
        self._store, self._learning = store, learning_store
        self._repository, self._paths, self._presenter, self._costs = repository, paths, presenter, costs
        self._course_kinds, self._queue_kinds, self._word_count = course_kinds, queue_kinds, word_count

    def overview(self, inputs: HomeInputs) -> HomeModel:
        """The whole home page as the learner records are now; reads only."""
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
            focus = presenter.home_focus(read.moment(), course_kinds=enabled, kana_kinds=kana_kinds,
                                         course_exclude=view.unavailable_ids, unqueued_course=teaching["planned"])
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
        has_work = bool(ready or counts["instruction"] or waiting or availability.blocked_count or counts["disabled"]
                        or counts["blocked"] or counts.get('blocked_material') or counts.get('blocked_input')
                        or path["in_progress_sections"] or others_in_progress)
        # Design 12 UI2, UI3 (B10), UI5: the arrangement's rows, from its presenter.
        facts = RouteFacts.from_overview(path)
        nothing_new = no_new_content(has_work=has_work, teaching_ready=summary.instruction, overview=path,
                                     others_in_progress=others_in_progress)
        from ..domain import _utc
        rows = presenter.home_model(_utc(None), facts, no_new_content=nothing_new)
        word_count = self._word_count(store.load_session_progress(lane="word_romaji", lazy_questions=True))
        # Every other route with work counts as work above; the column lists two of them.
        listed = [OtherRoute(row["route_id"], row["title"], row["in_progress_sections"],
                             row["next_section"]["title"] if row["next_section"] else None) for row in others[:2]]
        return compose(
            rows=rows, focus=focus, route=facts, no_new_content=nothing_new, route_id=current, route_title=short,
            core=not route, has_work=has_work, ready=ready, waiting=waiting, instruction=counts["instruction"],
            blocked_content=counts["blocked_content"], blocked_material=counts.get('blocked_material'),
            blocked_input=counts.get('blocked_input'), blocked_kana=availability.blocked_count,
            next_section=(next_section["id"], next_section["title"]) if next_section else None, others=listed,
            orphans=orphans, libraries_page=inputs.libraries_page, word_count=word_count,
            completed=path['required_completed_sections'], total=path['required_total_sections'],
            added=path['required_added_sections'], partial=path.get("required_partial_sections"),
            next_change_at=next_change_at)

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

    def unqueued_courses(self, projection=None, *, round_limited: bool = True) -> dict:
        """Course abilities without a queued question: {"ready", "waiting", "instruction", "blocked",
        "blocked_content", "blocked_material", "next_due"} (previews.course_preview)."""
        return self._course_preview(projection=projection, round_limited=round_limited)[0]

    def unqueued_teaching(self, projection=None) -> frozenset[str]:
        """Content ids of untaught lessons (or added abilities) the current settings could teach but that
        have no queued question yet, so a running planned round cannot show them (design 12 B5)."""
        return frozenset(self._course_preview(projection=projection, round_limited=False)[1]["unqueued"])

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
