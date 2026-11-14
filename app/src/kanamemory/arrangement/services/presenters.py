"""ArrangementPresenter: display models of the orientation screen, the home rows and "时间与进度" (design 12 UI1-UI6).

Application layer without SQL and without writes: it reads through the
preferences service, the study clock's event source, the dispatcher and the
store's public read methods, and composes display.* models. Instants are
passed in; the route arrives as model.RouteFacts that the caller already
read for the home page (StudyPathService.overview()), so the route is never
read again here (design 13 C5).

Cost: home_model under thorough or a missing orientation is one settings
read. Under breadth it reads the clock window the dispatcher keeps
(StoreEventSource.clock_events, incremental) up to three times at the same
revision (window_cut, today, learned: the later ones are memory hits), plus
a cached lesson lookup of newly seen course units and the indexed origin
lookups of the few new-learning candidates not yet known to be old.
progress_model reads its 7 days in full (StoreEventSource.period) and the
pass rates from a bounded tail of each log, only when the records page opens.
The focus (WP-H): home_focus is one settings read without a focus; only
with a focus scope does it resolve the scope (cached per store revision by
the catalogue), and only when it resolves does it read, through the
injected sources.due_reviews.DueReviews, the due reviews 继续学习 can serve
for "重点之外到期 N 题". home_model reads the focus only under breadth, and only
when it would show the B10 note (to point it at the scope's next section).
focus_buttons (设为重点 on the route, node and library pages) is one settings
read for a whole page plus the catalogue's titles (cached per revision).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta

from ..display import (BREADTH_TEXT, BREADTH_TITLE, CANCEL_FOCUS, COURSE_REVIEW_TITLE, DEPTH_TITLES,
                       DIRECTION_TITLES, FOCUS_SET_TEXT, INVITE_TEXT, KANA_TILT_TITLES, KEEP_TEXT, KEEP_TITLE,
                       NO_REVIEWS_TEXT, NO_SCOPE_TITLE, PASS_RATE_REVIEWS, PLAN_UNCHANGED_TEXT, SCOPE_GONE_TEXT,
                       SCOPE_GONE_TITLE, SCOPE_KIND_TITLES, SCOPE_TEXT, SET_FOCUS, SINGLE_KIND_UNCHANGED_TEXT,
                       THOROUGH_TEXT, THOROUGH_TITLE, TILT_TITLES, DayMinutes, DepthOption, FocusButtonModel,
                       FocusModel, FocusRowModel, HomeRowsModel, KanaTiltOption, OrientationModel, OrientationOption,
                       ProgressModel, ReviewFigures, ScopeOption, SectionNote, TiltOption)
from ..model import (KANA_DIRECTION_KINDS, Focus, FocusScope, KanaDirection, KanaTilt, Orientation, RouteFacts, Tilt,
                     Unit)
from ..rules.clock import charges
from ..rules.depth import GROUP_TITLES, LEVELS, KanaDepth, switched_groups
from ..rules.shares import KANA_WEIGHTS, TILT_WEIGHTS

DAYS = 7
# A read from this far back covers the 7 study days (each at most 25 hours) and the time of today.
_PERIOD_REACH = timedelta(days=DAYS + 1)
_DAY = 86400.0
# The pass rates look only at this many of the newest rows of each answer log (design 12 UI4): the read is
# bounded, and a direction with fewer than PASS_RATE_REVIEWS reviews among them shows the rate of those it has.
PASS_RATE_REACH = 2000


def minutes(seconds: float) -> int:
    """Whole minutes of charged seconds, rounded half up."""
    return int((seconds + 30.0) // 60.0)


def _joined(groups: Iterable[str]) -> str:
    return "与".join(GROUP_TITLES[group] for group in groups)


def consequence(off: tuple[str, ...], on: tuple[str, ...], stopping: int, *,
                in_force: Iterable[str] = (), after: Iterable[str] | None = None, single_kind: bool = False) -> str:
    """The D2 sentence for a depth choice: groups switched off (with the due count) and on.

    ``in_force`` / ``after``: the kinds the current practice plan uses now
    and would use after the choice (practice_preferences.
    enabled_preference_kinds). When the mixed switches change but those kinds
    stay the same (a single-kind plan, or a word-only mixed plan), the
    choice changes nothing the learner practises now, and the sentence says
    so instead of naming groups and due counts; ``single_kind`` (the plan is
    one kind, not a mixed practice) picks the wording.
    """
    if (off or on) and after is not None and set(after) == set(in_force):
        return SINGLE_KIND_UNCHANGED_TEXT if single_kind else PLAN_UNCHANGED_TEXT
    parts = []
    if off:
        parts.append(f"会停掉{_joined(off)}的练习：现有 {stopping} 道到期题不再出现，到期时间不变；"
                     "以后改回时它们会一次回来。")
    if on:
        parts.append(f"会打开{_joined(on)}的练习。")
    return "".join(parts) or "与现在的题型开关相同。"


def no_new_content(*, has_work: bool, teaching_ready: int, overview: Mapping, others_in_progress: bool) -> bool:
    """B10's "nothing new to start" (已加入的内容都学过了), from facts the home page already read.

    The whole rule; the caller passes facts only. True only when all hold:
    ``has_work``: the home page has work (with none, the home offers the
    next section anyway and says nothing here); ``teaching_ready``: the
    home page's "可开始初学" count (home_counts' ``instruction``: queued and
    not yet queued lesson teaching, of every route) is 0; ``overview``:
    StudyPathService.overview() of the current route has no entry of any kind
    (lesson, kana, reference) in state "added" or "started" (added but not
    yet learned: still new, whether or not it can start now);
    ``others_in_progress``: no other enabled route has a section in progress
    (its added content may still be unlearned) is False.

    It errs towards False: the sentence must not be shown while added content
    is still unlearned somewhere. Known gaps (design 12 section 12): kana
    added outside any route are not seen (the note can then show); added
    content that is blocked keeps the note away although it cannot start; two
    unscored first checks today (B10's third case) are not known on the home
    page.
    """
    if not has_work or teaching_ready > 0 or others_in_progress:
        return False
    return not any(entry.get("state") in {"added", "started"}
                   for stage in overview.get("stages", ()) for unit in stage.get("units", ())
                   for section in unit.get("sections", ()) for entry in section.get("entries", ()))


def scope_line(title: str | None, resolves: bool) -> str:
    """How a focus scope is named: its title, or that it no longer resolves (then no scope acts)."""
    return (title or SCOPE_GONE_TITLE) if resolves else f"{title or SCOPE_GONE_TITLE}（{SCOPE_GONE_TEXT}）"


def focus_line(focus: Focus, scope_title: str | None, *, breadth: bool) -> str | None:
    """UI6's home row "重点：<scope> · <tilt>": only the parts that are set and act; None without any.

    ``scope_title`` is scope_line() of the focus scope (None without a
    scope). The tilts act only under breadth (``breadth``); the even tilt
    and the normal kana tilt are not a focus.
    """
    parts = [scope_title] if focus.scope is not None and scope_title else []
    if breadth and focus.tilt is not Tilt.EVEN:
        parts.append(TILT_TITLES[focus.tilt])
    if breadth and focus.kana is not KanaTilt.NORMAL:
        parts.append(KANA_TILT_TITLES[focus.kana])
    return "重点：" + " · ".join(parts) if parts else None


def tilt_options() -> tuple[TiltOption, ...]:
    """The five tilts with the share each means (new:review), e.g. 多学新的 70:30."""
    return tuple(TiltOption(tilt, TILT_TITLES[tilt], f"{new}:{review}", f"{TILT_TITLES[tilt]} {new}:{review}")
                 for tilt, (new, review) in TILT_WEIGHTS.items())


def kana_tilt_options() -> tuple[KanaTiltOption, ...]:
    """The three kana tilts with the share each means inside review (course:kana), e.g. 假名少 85:15."""
    return tuple(KanaTiltOption(kana, KANA_TILT_TITLES[kana], f"{course}:{share}",
                                f"{KANA_TILT_TITLES[kana]} {course}:{share}")
                 for kana, (course, share) in KANA_WEIGHTS.items())


def _overdue_days(now: datetime, dues: Iterable[datetime]) -> int | None:
    earliest = min(dues, default=None)
    return None if earliest is None else max(0, int((now - earliest).total_seconds() // _DAY))


def _pass(ratings: list[int]) -> tuple[int | None, int]:
    if not ratings:
        return None, 0
    return int(100 * sum(rating > 1 for rating in ratings) / len(ratings) + 0.5), len(ratings)


class ArrangementPresenter:
    """Display models for one learner store; every call is read-only."""

    def __init__(self, store, preferences, events, dispatcher, launch, *, focus=None, due=None):
        self._store = store
        self._preferences = preferences   # services.preferences.ArrangementPreferences
        self._events = events             # sources.events.StoreEventSource
        self._dispatcher = dispatcher     # services.dispatch.ShareDispatcher
        self._launch = launch             # services.launch_notes.LaunchNotes
        self._focus = focus               # services.focus.FocusService, or None (then no focus is shown)
        self._due = due                   # sources.due_reviews.DueReviews, or None (then no "重点之外到期")

    # Study days -----------------------------------------------------------

    def study_days(self, now: datetime) -> tuple[str, ...]:
        """The last 7 study days including today, oldest first (keys as ClockEvent.day)."""
        today = self._store.study_day(now)
        return tuple((today - timedelta(days=back)).isoformat() for back in range(DAYS - 1, -1, -1))

    # UI1 -------------------------------------------------------------------

    def orientation_model(self, now: datetime) -> OrientationModel:
        """The orientation screen with the kana depth choice; the D2 numbers are the cards due at ``now``."""
        chosen = self._preferences.chosen_orientation()
        depth = self._preferences.depth
        preferences = depth.preferences()
        current = depth.level(preferences)
        stopping = depth.stopping_due(now, preferences)
        in_force = depth.kinds_in_force(preferences)
        single = depth.single_kind(preferences)
        options = []
        for level in LEVELS:
            preview = depth.preview(level, preferences)
            off, on = switched_groups(preferences["mixed_kinds"], preview["mixed_kinds"])
            text = consequence(off, on, stopping[level], in_force=in_force, after=depth.kinds_in_force(preview),
                               single_kind=single)
            options.append(DepthOption(level, DEPTH_TITLES[level], text, stopping[level]))
        if current is None:
            options.append(DepthOption(None, KEEP_TITLE, KEEP_TEXT, 0))
        return OrientationModel(
            chosen=chosen,
            orientations=(OrientationOption(Orientation.BREADTH, BREADTH_TITLE, BREADTH_TEXT),
                          OrientationOption(Orientation.THOROUGH, THOROUGH_TITLE, THOROUGH_TEXT)),
            depths=tuple(options), current_depth=current,
            preselected=chosen or Orientation.BREADTH,
            breadth_depth=KanaDepth.RECOGNITION if chosen is None else current,
            thorough_depth=current)

    def orientation_due(self) -> bool:
        """Whether the orientation screen should open by itself (UI1: a fresh install's first study).

        True when the orientation was never chosen, the store has no answer
        in any of the three logs and no teaching mark, and the screen was not
        shown in this launch. One settings read, then tail reads.
        """
        if not self._launch.may_show_orientation() or self._preferences.chosen_orientation() is not None:
            return False
        return not self._events.has_history()

    def showed_orientation(self) -> None:
        """Record that the fresh-install orientation screen was shown in this launch."""
        self._launch.showed_orientation()

    # UI2, UI3, UI5 ---------------------------------------------------------

    def home_model(self, now: datetime, route: RouteFacts, *, no_new_content: bool = False) -> HomeRowsModel:
        """The home rows; ``no_new_content``: no_new_content() of the facts the home page read (B10).

        The lines come from the clock window the dispatcher keeps; a number
        is never shown wrong: when a source hit its row limit inside the
        window (StoreEventSource.window_cut) and the window does not hold
        every event of the 7 study days, the "近 7 天新学" part is left out
        (the route part stays), and when it does not hold all of today, the
        usage line is left out. The records page reads in full (period()).
        The B10 note points at the focus scope's next section when the scope
        still has a section not added (H6), else at the route's.
        """
        chosen = self._preferences.chosen_orientation()
        if chosen is not Orientation.BREADTH:
            return HomeRowsModel(invite=chosen is None, invite_text=INVITE_TEXT if chosen is None else None)
        days = self.study_days(now)
        cut = self._events.window_cut(now)
        # The window drops every event at or before its cut: it holds all of a study day after the cut's day.
        held_from = None if cut is None else self._store.study_day(cut).isoformat()
        usage_line = None
        if held_from is None or held_from < days[-1]:
            today = self._dispatcher.today(now, days[-1])
            spent = (minutes(today.new), minutes(today.course), minutes(today.kana))
            if any(spent):
                usage_line = f"今日 新内容 {spent[0]} 分 · 课程复习 {spent[1]} 分 · 假名练习 {spent[2]} 分"
        lessons = len(self._events.learned(now, days).lessons) if held_from is None or held_from < days[0] else None
        return HomeRowsModel(progress_line=self._progress_line(route, lessons), usage_line=usage_line,
                             note=self._note(route, no_new_content))

    # UI6 -------------------------------------------------------------------

    def home_focus(self, now: datetime, *, course_kinds: Iterable[str] = (), kana_kinds: Iterable[str] | None = None,
                   course_exclude: Iterable[str] = (), unqueued_course=None) -> FocusRowModel | None:
        """The home page's focus row (UI6) under every orientation, None without a focus.

        One settings read without a focus (the even tilt, the normal kana
        tilt and no scope); with one, also the orientation; only when the
        scope resolves (cached per revision) are the due reviews read for
        "重点之外到期 N 题" (outside_due). What the home page already has is
        handed in: ``course_kinds`` the enabled course question kinds,
        ``kana_kinds`` the kana practice kinds in force (the pending queue's
        kinds; None: the kinds in force of the stored practice preferences,
        KanaDepthSetting.kinds_in_force), ``course_exclude`` LearningStore.
        availability().unavailable_ids, ``unqueued_course`` the (content id,
        ability) pairs without a question for which the home's unqueued
        preview planned one under the enabled kinds (None: not known, see
        DueReviews.course).
        """
        if self._focus is None:
            return None
        focus = self._focus.focus()
        if focus.is_default:
            return None
        breadth = self._preferences.chosen_orientation() is Orientation.BREADTH
        state = self._focus.scope_state(focus) if focus.scope is not None else None
        line = focus_line(focus, None if state is None else scope_line(state.title, state.resolves), breadth=breadth)
        if line is None:
            return None
        contents = self._focus.scope_contents(focus) if state is not None and state.resolves else None
        if contents is None or self._due is None:
            return FocusRowModel(line)
        if kana_kinds is None:
            kana_kinds = self._preferences.depth.kinds_in_force(self._preferences.depth.preferences())
        outside = self.outside_due(now, contents, course_kinds=course_kinds, kana_kinds=kana_kinds,
                                   course_exclude=course_exclude, unqueued_course=unqueued_course)
        return FocusRowModel(line, outside_due=outside, outside_due_line=f"重点之外到期 {outside} 题")

    def outside_due(self, now: datetime, scope_contents, *, course_kinds: Iterable[str], kana_kinds: Iterable[str],
                    course_exclude: Iterable[str] = (), unqueued_course=None) -> int:
        """N of "重点之外到期 N 题" (design 12 UI6, AC13): the due reviews outside the scope that 继续学习 can serve.

        As far as the kinds go (sources.due_reviews.DueReviews): course
        abilities due at ``now`` that an enabled question kind
        (``course_kinds``) serves, kana cards due at ``now`` whose direction a
        kana practice kind in force (``kana_kinds``) serves; never suspended
        or paused items, never unavailable content (``course_exclude`` and
        the kana due list's own filter). Inside the scope means a member of
        ``scope_contents`` (the lesson, or the kana entry of the card). An
        ability without a pending question counts when the enabled kinds plan
        a question for its lesson (``unqueued_course``, the home preview's
        planning). It is a count of due work, not of questions answerable
        this minute: an anti-leak wait of a few minutes or a queue not yet
        built does not remove an item, and a due review held back for another
        reason (an unmet prerequisite, material under review, no Japanese
        input) is still counted.
        """
        course = self._due.course(now, course_kinds, unavailable=course_exclude, plannable=unqueued_course)
        kana = self._due.kana(now, kana_kinds)
        return sum(member not in scope_contents for member in (*course, *kana))

    def focus_model(self) -> FocusModel:
        """The orientation screen's third part (UI6): tilts, kana tilts and the scope choices.

        Read-only: one settings read for the focus, and the scope catalogue's
        choices (each enabled route, each usable library, each family). The
        stored scope is offered (second, after none) also when the choices
        do not hold it, so confirming the screen untouched keeps it.
        """
        focus = self._focus.focus() if self._focus is not None else Focus()
        state = self._focus.scope_state(focus) if self._focus is not None and focus.scope is not None else None
        choices = [(choice.scope, choice.title) for choice in self._focus.catalog.choices()] \
            if self._focus is not None else []
        if state is None:
            line, resolves = f"现在：{NO_SCOPE_TITLE}", True
        else:
            line, resolves = f"现在：{scope_line(state.title, state.resolves)}", state.resolves
            if all(scope != state.scope for scope, _ in choices):
                choices.insert(0, (state.scope, scope_line(state.title, state.resolves)))
        return FocusModel(focus=focus, tilts=tilt_options(), kana_tilts=kana_tilt_options(),
                          scopes=(ScopeOption(None, NO_SCOPE_TITLE), *_unique_titles(choices)),
                          scope_line=line, scope_resolves=resolves)

    def focus_buttons(self, scopes: Iterable[FocusScope]) -> tuple[FocusButtonModel | None, ...]:
        """设为重点 / 取消重点 for pages that show these objects (UI6, H6), one settings read for all.

        One model per scope, in order: ``current`` when it is the stored
        focus scope. None (no button) when it is not the focus and does not
        resolve (a disabled route or library: setting it would act as no
        scope); the titles are read from the catalogue, cached per revision.
        """
        if self._focus is None:
            return tuple(None for _ in scopes)
        stored = self._focus.focus().scope
        models = []
        for scope in scopes:
            if scope == stored:
                models.append(FocusButtonModel(scope, True, CANCEL_FOCUS, (FOCUS_SET_TEXT, SCOPE_TEXT)))
            elif self._focus.catalog.title(scope) is not None:
                models.append(FocusButtonModel(scope, False, SET_FOCUS))
            else:
                models.append(None)
        return tuple(models)

    def continue_prompt(self, route: RouteFacts, *, no_new_content: bool) -> SectionNote | None:
        """The B10 prompt before the first "继续学习" of this launch, or None.

        None once it was shown (prompted_continue), unless the orientation is
        breadth, or when there is new content or no next section. Reads the
        orientation only while the prompt is still possible. The caller
        passes the facts of its last home refresh (the press is on that page).
        """
        if not self._launch.may_prompt_continue():
            return None
        if self._preferences.chosen_orientation() is not Orientation.BREADTH:
            return None
        return self._note(route, no_new_content)

    def prompted_continue(self) -> None:
        """Record that the B10 first-continue prompt was shown in this launch."""
        self._launch.prompted_continue()

    def end_note(self, route: RouteFacts) -> SectionNote | None:
        """B10 on the end page ("本次学习已保存"): the route's next section first, under breadth only.

        The caller shows that page only when nothing at all could be
        dispatched, so there is no new content by construction. One settings
        read; no launch memory (the end page may say it every time).
        """
        if self._preferences.chosen_orientation() is not Orientation.BREADTH:
            return None
        return self._note(route, True)

    def _note(self, route: RouteFacts, no_new_content: bool) -> SectionNote | None:
        """B10's note: the next section inside the focus scope when it has one (H6), else the route's."""
        if not no_new_content:
            return None
        scoped = self._scope_section()
        if scoped is not None:
            return SectionNote(scoped.section_id, scoped.title, route_id=scoped.route_id)
        if route.next_section_id is None:
            return None
        return SectionNote(route.next_section_id, route.next_section_title or "")

    def _scope_section(self):
        """The focus scope's next section not yet added (a route or node scope that resolves), else None."""
        if self._focus is None:
            return None
        scope = self._focus.focus().scope
        if scope is None or scope.kind not in {"route", "node"}:
            return None
        return self._focus.catalog.next_section(scope)

    @staticmethod
    def _route_part(route: RouteFacts) -> str:
        return f"路线：第 {route.position}／{route.total} 小节" if route.total > 0 else "路线：没有小节"

    def _progress_line(self, route: RouteFacts, lessons: int | None) -> str | None:
        """The UI2 progress line; ``lessons`` None leaves out the 7-day part (the window cannot say it)."""
        parts = ([self._route_part(route)] if route.total > 0 else []) + (
            [f"近 {DAYS} 天新学 {lessons} 课"] if lessons is not None else [])
        return " · ".join(parts) or None

    # UI4 -------------------------------------------------------------------

    def progress_model(self, now: datetime, route: RouteFacts, *, course_due: int,
                       course_exclude: Iterable[str] = ()) -> ProgressModel:
        """"时间与进度" for the records page (any orientation); every shown text composed here.

        ``course_due``: the course questions due now and unanswered, as the
        records page already counts them (StudyPathRuntimeMixin.
        record_course_due); ``course_exclude``: content ids whose library is
        unavailable (LearningStore.availability().unavailable_ids), left out
        of the longest course overdue. The 7 days are read in full
        (StoreEventSource.period), exact however many answers they hold.
        The pass rates look at the latest PASS_RATE_REVIEWS reviews among the
        latest PASS_RATE_REACH rows of each log (a bounded read).
        """
        days = self.study_days(now)
        activity = self._events.period(now, now - _PERIOD_REACH, days)
        spent = {day: {Unit.NEW: 0.0, Unit.COURSE: 0.0, Unit.KANA: 0.0} for day in days}
        # The same charges rules/clock.day_usage sums, computed once for the 7 days.
        for event, seconds in charges(activity.events):
            if event.day in spent:
                spent[event.day][event.unit] += seconds
        day_rows = tuple(_day_minutes(day, *(minutes(spent[day][unit]) for unit in (Unit.NEW, Unit.COURSE, Unit.KANA)))
                         for day in days)
        lessons, kana = len(activity.learned.lessons), len(activity.learned.kana)
        return ProgressModel(
            days=day_rows, new_lessons=lessons, new_kana=kana,
            route_position=route.position, route_total=route.total, route_line=self._route_part(route),
            learned_line=f"近 {DAYS} 天新学 {lessons} 课 · 新假名 {kana} 个",
            reviews=(self._course_figures(now, course_due, course_exclude), *self._kana_figures(now)),
            days_heading=f"近 {len(days)} 天每天用时（分钟）")

    def _course_figures(self, now: datetime, due: int, exclude: Iterable[str]) -> ReviewFigures:
        overdue = None
        if due > 0:
            dues = [datetime.fromisoformat(row["due_at_utc"])
                    for row in self._store.queries.active_review_units(exclude=exclude) if row["due_at_utc"]]
            overdue = _overdue_days(now, [value for value in dues if value <= now])
        rate, reviews = _pass(self._store.queries.latest_review_ratings(True, PASS_RATE_REVIEWS,
                                                                        reach=PASS_RATE_REACH))
        return _figures(COURSE_REVIEW_TITLE, None, max(0, int(due)), overdue, rate, reviews)

    def _kana_figures(self, now: datetime) -> list[ReviewFigures]:
        direction_of = {kind: direction for direction, kinds in KANA_DIRECTION_KINDS.items() for kind in kinds}
        dues: dict[KanaDirection, list[datetime]] = {direction: [] for direction in KanaDirection}
        for card in self._store.get_due(now=now):
            direction = direction_of.get(card["skill"])
            if direction is not None:
                dues[direction].append(datetime.fromisoformat(card["due_at_utc"]))
        figures = []
        for direction in KanaDirection:
            rate, reviews = _pass(self._store.queries.latest_review_ratings(
                False, PASS_RATE_REVIEWS, KANA_DIRECTION_KINDS[direction], reach=PASS_RATE_REACH))
            figures.append(_figures(DIRECTION_TITLES[direction], direction, len(dues[direction]),
                                    _overdue_days(now, dues[direction]), rate, reviews))
        return figures


def _unique_titles(choices: Iterable[tuple[FocusScope, str]]) -> tuple[ScopeOption, ...]:
    """Scope options with unique titles, so a page may map a shown title back to its scope.

    A title shown more than once (or equal to 不设范围) carries its kind; one
    still repeated after that gets a running number from the second on, e.g.
    （内容库）, （内容库 2）. The titles are never equal to 不设范围 or to each other.
    """
    choices = list(choices)
    counts: dict[str, int] = {}
    for _, title in choices:
        counts[title] = counts.get(title, 0) + 1
    kept = {title for title, count in counts.items() if count == 1 and title != NO_SCOPE_TITLE}
    taken, options = {NO_SCOPE_TITLE}, []
    for scope, title in choices:
        if title in kept:
            shown = title
        else:
            kind, number = SCOPE_KIND_TITLES[scope.kind], 1
            shown = f"{title}（{kind}）"
            while shown in taken or shown in kept:
                number += 1
                shown = f"{title}（{kind} {number}）"
        taken.add(shown)
        options.append(ScopeOption(scope, shown))
    return tuple(options)


def _day_minutes(day: str, new: int, course: int, kana: int) -> DayMinutes:
    """One day's minutes with its shown line ("MM-DD  新内容 … · 课程复习 … · 假名练习 …")."""
    return DayMinutes(day, new, course, kana, f"{day[5:]}  新内容 {new} · 课程复习 {course} · 假名练习 {kana}")


def review_line(title: str, due: int, overdue: int | None, pass_percent: int | None, reviews: int) -> str:
    """The UI4 review line: due count, longest overdue, and the pass rate with its basis."""
    parts = [f"{title}：到期未答 {due} 题"]
    if overdue is not None:
        parts.append(f"最久逾期 {overdue} 天")
    if pass_percent is None:
        parts.append(NO_REVIEWS_TEXT)
    elif reviews >= PASS_RATE_REVIEWS:
        parts.append(f"最近 {PASS_RATE_REVIEWS} 次通过 {pass_percent}%")
    else:
        parts.append(f"通过 {pass_percent}%（共 {reviews} 次）")
    return " · ".join(parts)


def _figures(title: str, direction: KanaDirection | None, due: int, overdue: int | None,
             pass_percent: int | None, reviews: int) -> ReviewFigures:
    return ReviewFigures(title, direction, due, overdue, pass_percent, reviews,
                         review_line(title, due, overdue, pass_percent, reviews))
