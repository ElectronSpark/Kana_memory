"""The one way tests reach the home page 今日学习 (show_home and its refresh).

Design 13 stage S3. The home's reading and computing is a service
(kanamemory.home.service.HomeOverviewService: the overview model,
can_continue, the records page's course due and the two unqueued previews),
built per refresh by kanamemory.home_runtime (the app side: the service, the
inputs, what a home control does) and drawn by kanamemory.home.ui.home_page.
The page state is one HomeShowing kept on the app as _study_home
(PAGE_BINDINGS["home"]): the four widgets show_home makes and the next change
moment, from which the home timer (KanaMemory._arm_home_review_refresh /
_tick_home_review) is armed on the handle the wait pages share
(app._review_wait_handle). The route and end pages read
app.study_path_can_continue, the records page record_course_due; the
arrangement and its launch notes are kept in app._study_arrangement_value;
three names are looked up when they are used: home_counts and
summarize_queue_availability in kanamemory.home.service (the latter also for
继续学习's dispatch, through support.continue_support), and other_route_rows in
kanamemory.home.service, study_path_ui and study_path_display. That knowledge
lives only here: when step BC moved the home into the service and the page
module, the inside of this file changed and the tests that use it did not.

home_page(app) returns a live view: every property is read at the moment it
is used, so a test may keep a HomePage across a refresh, a re-open or a Back.
Operations are named by what the learner does (press) or by what the outer
layer does (refresh: the timer, Back, the startup check and 取消重点; the
timer itself). Public (tools/public_source_files.txt): it imports only
support.fake_ui and kanamemory modules.
"""
from types import MethodType

from kanamemory import study_path_display, study_path_ui
from kanamemory import ui_components as component_ui
from kanamemory.app import KanaMemory
from kanamemory.home import service as overview
from kanamemory.home_runtime import HomeShowing, home_inputs, home_service
from kanamemory.study_path_runtime import StudyPathRuntimeMixin
from support.fake_ui import descendants


class HomePage:
    """Read the home page's state and act on it like the learner or the outer layer."""

    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------ widgets
    @property
    def action_slot(self):
        """The primary card: the arrangement's rows, the focus row, 接着学, the hint, the primary button."""
        return self.app._study_home.action_slot

    @property
    def secondary_slot(self):
        """The column under the card: invite, backlog, 加入下一小节, other routes, 学习路线/添加内容, word drill."""
        return self.app._study_home.secondary_slot

    @property
    def stats_label(self):
        """The label under 学习概况 ("主线初学完成 C/T 节 · 已加入 A 节")."""
        return self.app._study_home.stats_label

    @property
    def material_slot(self):
        """The column of the shared-material notice ("收到 N 份分享文本")."""
        return self.app._study_home.material_slot

    # ------------------------------------------------------------ texts
    @property
    def stats_text(self):
        return self.stats_label.text

    def action_texts(self):
        """Every non-empty text of the primary card, in drawing order."""
        return [str(view.text) for view in descendants(self.action_slot) if view.text]

    def secondary_texts(self):
        """Every non-empty text of the column under the card, in drawing order."""
        return [str(view.text) for view in descendants(self.secondary_slot) if view.text]

    def focus_lines(self):
        """The focus row's texts: "重点：…" and "重点之外到期 N 题" (design 12 UI6)."""
        return [text for text in self.action_texts() if text.startswith("重点：") or text.startswith("重点之外到期")]

    # ------------------------------------------------------------ controls
    def action_buttons(self):
        """The pressable controls of the primary card, in drawing order (the primary button is the last)."""
        return [view for view in descendants(self.action_slot) if view.on_press]

    def secondary_buttons(self):
        return [view for view in descendants(self.secondary_slot) if view.on_press]

    def action_captions(self):
        return [view.text for view in self.action_buttons()]

    def secondary_captions(self):
        return [view.text for view in self.secondary_buttons()]

    @property
    def primary(self):
        """The card's primary button (继续学习 / 开始下一小节 / 查看学习路线): it follows the arrangement's rows."""
        return self.action_buttons()[-1]

    def control(self, caption):
        """The pressable control with this caption, in the primary card first, then in the column under it."""
        for view in (*self.action_buttons(), *self.secondary_buttons()):
            if view.text == caption:
                return view
        raise AssertionError(f"no control {caption!r} on the home page: "
                             f"{self.action_captions()} {self.secondary_captions()}")

    def word_buttons(self):
        """The word-drill entries ("单词拼写" / "继续单词拼写（剩 N 词）") under the card."""
        return [item for item in descendants(self.secondary_slot)
                if item.on_press and "单词拼写" in item.text]

    # ------------------------------------------------------------ the learner
    def press_primary(self):
        control = self.primary
        control.on_press(control)
        return control

    def press(self, caption):
        control = self.control(caption)
        control.on_press(control)
        return control

    def press_word(self):
        (control,) = self.word_buttons()
        control.on_press(control)
        return control

    # ------------------------------------------------------------ the outer layer
    def refresh(self):
        """What the timer, Back, the startup service check and 取消重点 do: redraw the home in place."""
        self.app.refresh_home_actions()

    @property
    def next_change(self):
        """The moment the home's numbers next change by themselves (the home timer's due), None without one."""
        return self.app._study_home.next_change_at

    def set_next_change(self, moment):
        showing = getattr(self.app, "_study_home", None)
        if showing is None:   # an app that never drew the home (a timer fake): a showing with the moment only
            showing = self.app._study_home = HomeShowing(None, None, None, None)
        showing.next_change_at = moment

    def arm_timer(self):
        """Arm the home timer as show_home, a refresh, a backup job or a feedback return does."""
        KanaMemory._arm_home_review_refresh(self.app)

    def tick(self, token=None):
        """Run the home timer's callback with ``token`` (default: the current page token)."""
        KanaMemory._tick_home_review(self.app, self.app._page_token if token is None else token)

    @property
    def timer(self):
        """The handle of the timer the home and the wait pages share (None when none is armed)."""
        return self.app._review_wait_handle

    def home_timers(self):
        """Every home timer callback scheduled so far, cancelled or not, as (handle, page token) pairs.

        Needs a loop that keeps its handles (support.review_wait_ui_support.ControlledLoop).
        """
        return [(handle, handle.args[-1]) for handle in self.app.loop.handles
                if handle.callback is KanaMemory._tick_home_review]

    def live_timers(self):
        """The home timer callbacks scheduled and not cancelled, as (handle, page token) pairs."""
        return [(handle, token) for handle, token in self.home_timers() if not handle.cancelled]

    # ------------------------------------------------------------ the numbers behind the page
    def unqueued_reviews(self, *args, **kwargs):
        """The kana reviews without a queued question: {"ready", "waiting", "next_due"}.

        pending as the home passes it: left out, the main checkpoint is read; None means known empty.
        """
        app = self.app
        service = home_service(app, queue_kinds=lambda pending: KanaMemory._queue_kinds(app, pending))
        return service.unqueued_reviews(home_inputs(app), *args, **kwargs)

    def unqueued_courses(self, *args, **kwargs):
        """The course abilities without a queued question: {"ready", "waiting", "instruction", "blocked",
        "blocked_content", "blocked_material", "next_due"} (projection: a LearningStore.read_operation)."""
        return home_service(self.app).unqueued_courses(*args, **kwargs)

    def can_continue(self):
        """What the route page and the end pages ask: is there anything 继续学习 can reach now."""
        return self.app.study_path_can_continue()

    def course_due(self):
        """The records page's "课程到期 N 题": course questions due now, queued or not (rounds do not limit it)."""
        return StudyPathRuntimeMixin.record_course_due(self.app)


def home_page(app):
    """The home page the app currently holds (showing, or kept in the back stack)."""
    return HomePage(app)


def open_home(app):
    """The learner opens 今日 (show_home with the page set to home first)."""
    app.page = "home"
    app.show_home()


def stub_can_continue(app, value):
    """The route and end pages' "can 继续学习 reach anything" answers ``value`` without reading."""
    app.study_path_can_continue = lambda: value


def new_launch(app):
    """What a restart forgets: the arrangement (and its launch notes) is rebuilt for the store."""
    app._study_arrangement_value = None


def use_arrangement(app, arrangement):
    """The home and continue use ``arrangement`` (a StoreArrangement) until the store object changes."""
    app._study_arrangement_value = arrangement
    return arrangement


def orientation_seen(app):
    """The fresh-install orientation screen counts as shown in this launch (it does not open by itself)."""
    StudyPathRuntimeMixin.study_arrangement(app).presenter.showed_orientation()


def bind_home_reads(app):
    """Bind the app's real home reads onto a fake app that has none (the records, route and end pages call them)."""
    for name in ("study_path_service", "study_path_can_continue"):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))


def patch_home_counts(monkeypatch, replacement):
    """The count rule becomes ``replacement`` (home_counts.home_counts, called with keywords only) for every
    caller it has: the home and the records page's course_due (both in the home service)."""
    monkeypatch.setattr(overview, "home_counts", replacement)


def patch_kana_availability(monkeypatch, value):
    """The main kana queue summary (summarize_queue_availability) answers ``value`` for every caller it has:
    the home and can_continue (the home service) and 继续学习's dispatch (study_path_runtime)."""
    from support.continue_support import patch_dispatch_availability

    def answer(*args, **kwargs):
        return value
    monkeypatch.setattr(overview, "summarize_queue_availability", answer)
    patch_dispatch_availability(monkeypatch, answer)


def patch_other_routes(monkeypatch, rows):
    """The other routes (routes_overview rows: route_id, title, in_progress_sections, next_section) are
    ``rows`` for every caller: the home (the home service), the route page (study_path_ui), the 本次学习已保存
    page and the learning pages' next-content controls (both look the name up in study_path_display)."""
    rows = list(rows)

    def answer(service, current, **kwargs):
        return list(rows)
    for module in (overview, study_path_ui, study_path_display):
        monkeypatch.setattr(module, "other_route_rows", answer)


def record_controls(monkeypatch):
    """Keep on every label, button, row and col the keyword arguments it was made with.

    fake_ui.View drops sizes, colours, flex and the primary/multiline flags;
    after this, made_with(node) lists them. Call it after the fixture that
    installs the fake controls (setup_app), before the page is drawn.
    """
    for kind in ("label", "button", "row", "col"):
        make = getattr(component_ui, kind)

        def recording(*args, _kind=kind, _make=make, **kwargs):
            view = _make(*args, **kwargs)
            view._made = (_kind, dict(kwargs))
            return view
        monkeypatch.setattr(component_ui, kind, recording)


def made_with(node):
    """(kind, text, keyword arguments) of node and its descendants that record_controls saw made, in order."""
    return [(view._made[0], view.text, view._made[1]) for view in descendants(node) if hasattr(view, "_made")]
