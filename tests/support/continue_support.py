"""The one way tests reach the state and the seams of 继续学习 (the continue action and its dispatch).

Design 13 stage S4. The continue action (app.continue_study_path) prepares the
queues, may end on one of two background waits (the kana queue assembly and
the course head of a sealed round, both on the waiting page
KANA_ASSEMBLY_PAGE) and then decides the next step: an item of the kana or the
course line (app.resume_practice / app.continue_learning), the reference page
(app.show_study_reference), an extra-practice step (the take-over), or one of
its own pages (本轮已保存, 暂时没有可进行的假名题型, 本次学习已保存, the waiting
page and the error page "无法准备下一题"). The decision and the course-head wait
live in kanamemory.continue_runtime (decide returns the next step, perform
makes its call); the action's own state between its moments (the enrollment
notice, the waiting page and its timer, the tasks of both waits) is one
ContinueState object on the app. How the action keeps its state (also the
background switch, the teaching passed over this run, the extra-practice
step counter, the mark on an error already
shown) and where it looks names up (the decision itself, the share dispatch,
the wait delay, the two background runners, the queue summary, the
promotion, the take-over) is known only here. Tests never name the module or
the attributes behind these functions, so the decision and its state can move
without a test changing.

What stays outside, because it is the outer contract of every page: the fake
screen's app.page / app.title / app.body, and the app's own methods (the line
entries, screen, the end pages' callbacks), which tests may replace on the
instance and which the action must keep calling through the app object.

Public (tools/public_source_files.txt): it imports only support modules that
are public and kanamemory modules.
"""
import asyncio
from dataclasses import dataclass, field

from kanamemory.app import KANA_ASSEMBLY_PAGE
from kanamemory.continue_runtime import KANA_ASSEMBLY_ATTEMPTS
from support.fake_ui import descendants
from support.review_wait_ui_support import ControlledLoop
from support.study_home_support import made_with, record_controls

__all__ = ["KANA_ASSEMBLY_ATTEMPTS", "KANA_ASSEMBLY_PAGE", "WAIT_PAGE", "made_with", "record_controls"]

# The waiting page both background waits draw (its page name).
WAIT_PAGE = KANA_ASSEMBLY_PAGE


def _runtime():
    from kanamemory import study_path_runtime
    return study_path_runtime


def _continue():
    from kanamemory import continue_runtime
    return continue_runtime


def _state(app):
    from kanamemory.continue_runtime import continue_state
    return continue_state(app)


# --- the action's own state ------------------------------------------------------------------------------

def background_on(app):
    """The started app's switch for the two background waits (startup sets it; fixtures and tools do not)."""
    app._background_assembly = True


def background_off(app):
    """The app was not started by startup: no background switch at all (both waits stay inline)."""
    try:
        del app._background_assembly
    except AttributeError:
        pass


def background_enabled(app):
    """Whether the two background waits may start (and the startup route-mix warm-up may run)."""
    return bool(getattr(app, "_background_assembly", False))


def kana_wait_task(app):
    """The task of the kana queue assembly wait (W1) started by the last continue that waited, or None."""
    return _state(app).kana_task


def course_wait_task(app):
    """The task of the course-head wait of a sealed round (W2) started by the last continue that waited, or None."""
    return _state(app).course_task


def wait_timer(app):
    """The handle of the waiting page's 20-second "takes longer" timer, None when none is armed."""
    return _state(app).wait_timer


def wait_view(app):
    """(page name, page token) of the last waiting page shown, None before any."""
    return _state(app).wait_view


def set_wait_view(app, view):
    """As if the waiting page (page name, page token) ``view`` had been shown last."""
    _state(app).wait_view = view


def enrollment_notice(app):
    """The armed enrollment notice ("已加入「…」…"), None when none is armed."""
    return _state(app).notice


def set_enrollment_notice(app, notice):
    """Arm ``notice`` (a dict with text, new_ids and anchor) for the next continue action."""
    _state(app).notice = notice


def skipped_teaching(app):
    """The course items whose teaching the learner passed over in this run (跳到下一项), as kept."""
    return getattr(app, "_guide_skipped_ids", None)


def set_skipped_teaching(app, ids):
    """The learner passed over the teaching of these course items in this run."""
    app._guide_skipped_ids = ids


def error_shown(error):
    """Whether the continue action already drew the error page for this exception."""
    return bool(getattr(error, "_continue_error_shown", False))


def extra_steps(app):
    """How many extra-practice steps are being carried out one inside another right now."""
    return getattr(app, "_extra_steps", 0)


def set_extra_steps(app, count):
    """Pretend ``count`` extra-practice steps are being carried out one inside another."""
    app._extra_steps = count


def wait_seconds():
    """The delay after which the waiting page offers 重试."""
    return _runtime().CONTINUE_WAIT_SECONDS


# --- seams -----------------------------------------------------------------------------------------------

def replace_dispatch(monkeypatch, replacement):
    """The decision after the queues are prepared becomes ``replacement(app, *args, **kwargs)`` (both the
    inline path and the resumed course wait call it)."""
    monkeypatch.setattr(_continue(), "decide", replacement)


def make_dispatch_raise(monkeypatch, error):
    """The decision raises ``error``; returns the list of (app,) it was called with."""
    calls = []

    def broken(app, *args, **kwargs):
        calls.append(app)
        raise error
    replace_dispatch(monkeypatch, broken)
    return calls


def replace_share_dispatch(monkeypatch, replacement):
    """The breadth-first share decision becomes ``replacement(app, *args, **kwargs)``."""
    monkeypatch.setattr(_continue(), "share_dispatch", replacement)


def set_wait_seconds(monkeypatch, seconds):
    """The waiting page offers 重试 after ``seconds`` instead of 20."""
    monkeypatch.setattr(_runtime(), "CONTINUE_WAIT_SECONDS", seconds)


def kana_runner():
    """The background runner the kana queue assembly wait (W1) awaits, as installed now."""
    return _continue().run_in_background


def replace_kana_runner(monkeypatch, runner):
    """The kana queue assembly wait (W1) awaits ``runner(repo, store, work, options)`` instead (the
    course-head wait W2 awaits the same runner)."""
    monkeypatch.setattr(_continue(), "run_in_background", runner)


def course_runner():
    """The background runner the course-head wait (W2) awaits, as installed now."""
    return _continue().run_in_background


def replace_course_runner(monkeypatch, runner):
    """The course-head wait (W2) awaits ``runner(repo, store, work, options)`` instead (the kana queue
    assembly wait W1 awaits the same runner)."""
    monkeypatch.setattr(_continue(), "run_in_background", runner)


def patch_dispatch_availability(monkeypatch, answer):
    """The decision's kana queue summary (summarize_queue_availability) is ``answer(*args, **kwargs)``."""
    monkeypatch.setattr(_continue(), "summarize_queue_availability", answer)


def patch_promotion(monkeypatch, replacement):
    """The round's promotion of a share-chosen new head (practice_round.promote, as the share decision
    looks it up) becomes ``replacement(store, **kwargs)``."""
    from kanamemory import practice_round
    monkeypatch.setattr(practice_round, "promote", replacement)


def wrap_protected_choice(monkeypatch, wrapper):
    """The thorough decision's choice of a protected first-teaching head (first_teaching_dispatch's
    choose_protected, as the decision looks it up) becomes ``wrapper(real, *args, **kwargs)``; ``real`` is the
    choice as it was."""
    from kanamemory import first_teaching_dispatch
    real = first_teaching_dispatch.choose_protected

    def choose(*args, **kwargs):
        return wrapper(real, *args, **kwargs)
    monkeypatch.setattr(first_teaching_dispatch, "choose_protected", choose)


@dataclass
class Call:
    """One call the continue action made: a line entry, the reference page, the take-over or screen."""
    name: str
    args: tuple
    kwargs: dict
    # For a line entry with _projection: "action read" when it is the read context this action holds open
    # at the moment of the call (attached to the store and still active), else "other"; None without one.
    projection: object = None
    result: object = None
    page: object = None           # the page shown when the call was made
    read: object = field(default=None, repr=False)
    # _projection at the moment of the call: it IS the read context attached to the learner store, and that
    # context is still active (None without a _projection).
    projection_attached: object = None
    projection_active: object = None
    children: object = None       # screen: the number of children handed to the page

    @property
    def read_ended(self):
        """The _projection passed is no longer usable (its action has ended)."""
        return self.read is not None and not getattr(self.read, "active", True)


ENTRIES = ("continue_learning", "resume_practice", "show_study_reference")


def _projection(app, kwargs):
    read = kwargs.get("_projection")
    if read is None:
        return None, None, None, None
    attached = read is getattr(getattr(app, "store", None), "_learning_read", None)
    active = bool(getattr(read, "active", False))
    return ("action read" if attached and active else "other"), read, attached, active


def record_entries(app, monkeypatch, *, real=True, log=None, names=ENTRIES):
    """Record every call to the two line entries and to show_study_reference with its exact arguments.

    ``real``: the app's own methods still run (False: nothing is drawn; a callable is asked at each call).
    ``names``: other app methods to record the same way (the end pages' targets). Returns the log (a list of
    Call), shared with spy_take_over / record_screens when ``log`` is passed. Install it before the page is
    drawn: a control may hold the method it was drawn with.
    """
    log = [] if log is None else log
    for name in names:
        original = getattr(app, name)

        def entry(*args, _name=name, _original=original, **kwargs):
            projection, read, attached, active = _projection(app, kwargs)
            log.append(Call(_name, args, dict(kwargs), projection, page=getattr(app, "page", None), read=read,
                            projection_attached=attached, projection_active=active))
            if real() if callable(real) else real:
                return _original(*args, **kwargs)
            return None
        monkeypatch.setattr(app, name, entry)
    return log


def spy_take_over(monkeypatch, answer=None, *, log=None):
    """Record each time the extra-practice take-over is asked (by the dispatch or by a page it reaches).

    ``answer``: None asks the real take-over; True/False answers that without acting; a callable is
    called with the app. Each Call records the page shown when asked and the answer given.
    """
    from kanamemory import extra_practice_runtime
    real = extra_practice_runtime.take_over
    log = [] if log is None else log

    def take_over(app):
        call = Call("take_over", (), {}, page=getattr(app, "page", None))
        log.append(call)
        call.result = (real(app) if answer is None else answer(app) if callable(answer) else bool(answer))
        return call.result
    monkeypatch.setattr(extra_practice_runtime, "take_over", take_over)
    return log


def record_screens(app, monkeypatch, *, log=None):
    """Record every page drawn (screen's name and title as args) without changing what it draws."""
    log = [] if log is None else log
    original = app.screen

    def screen(*args, **kwargs):
        log.append(Call("screen", tuple(args[:2]), dict(kwargs), page=getattr(app, "page", None),
                        children=len(args[2:])))
        return original(*args, **kwargs)
    monkeypatch.setattr(app, "screen", screen)
    return log


def screens(log):
    """(page name, title) of every page drawn in a log, in order."""
    return [call.args for call in log if call.name == "screen"]


def round_plan(app):
    """The in-memory round plan as one tuple, read through practice_round's public functions:
    (sealed, finished, promoted, course_order_token)."""
    from kanamemory import practice_round
    store = app.store
    return (practice_round.sealed(store), practice_round.finished(store), practice_round.promoted(store),
            practice_round.course_order_token(store))


def watch(app, monkeypatch, *, real=True, take_over=False):
    """One ordered log of the line entries, the reference page, the take-over and screen."""
    log = []
    record_entries(app, monkeypatch, real=real, log=log)
    spy_take_over(monkeypatch, take_over, log=log)
    record_screens(app, monkeypatch, log=log)
    return log


def names(log):
    """The names of the calls in a log, in order."""
    return [call.name for call in log]


# --- reading the page -------------------------------------------------------------------------------------

def page(app):
    """(page name, title) of the page shown."""
    return app.page, app.title


def texts(app):
    """Every non-empty text of the page shown, in drawing order (labels and captions)."""
    return [str(view.text) for view in descendants(app.body) if view.text]


def controls(app):
    """The pressable controls of the page shown, in drawing order."""
    return [view for view in descendants(app.body) if view.on_press]


def captions(app):
    """The captions of the pressable controls of the page shown, in drawing order."""
    return [view.text for view in controls(app)]


def control(app, caption):
    """The pressable control with this caption on the page shown."""
    for view in controls(app):
        if view.text == caption:
            return view
    raise AssertionError(f"no control {caption!r}: {captions(app)}")


def press(app, caption):
    """Press the control with this caption (with itself as the widget, as Toga does)."""
    view = control(app, caption)
    view.on_press(view)
    return view


def page_made_with(app):
    """(kind, text, keyword arguments) of every control of the page shown (record_controls first)."""
    return made_with(app.body)


# --- the asyncio pattern and the timer ----------------------------------------------------------------------

def pending_waits(app):
    """The wait tasks not finished yet."""
    return [task for task in (kana_wait_task(app), course_wait_task(app)) if task is not None and not task.done()]


async def settle(app, limit=8):
    """Await the waits the action started, and those their resumes started, until none is pending."""
    for _ in range(limit):
        pending = pending_waits(app)
        if not pending:
            return
        for task in pending:
            await task
    raise AssertionError("the waits did not settle")


def in_loop(app, action, *, while_waiting=None, settle_waits=True):
    """Run ``action()`` under a running asyncio loop; then ``while_waiting()`` (while a wait is shown), then
    await the waits. Returns (the page seen right after the action, its waiting tasks)."""
    async def scenario():
        action()
        seen = (app.page, getattr(app, "title", None))
        tasks = pending_waits(app)
        if while_waiting is not None:
            while_waiting()
        if settle_waits:
            await settle(app)
        return seen, tasks
    return asyncio.run(scenario())


def controlled_wait_clock(monkeypatch):
    """The waiting page's timer is armed on a ControlledLoop instead of the running loop; returns it."""
    runtime = _runtime()
    clock = ControlledLoop()
    real = runtime.show_continue_wait

    def show(app, page_name, loop):
        return real(app, page_name, clock)
    monkeypatch.setattr(runtime, "show_continue_wait", show)
    return clock


def wait_timers(clock):
    """The waiting-page timers armed on ``clock``, in order."""
    return [handle for handle in clock.handles if handle.delay == wait_seconds()]


def fire_wait_timers(clock):
    """Let the waiting page's time run out: run every armed, not cancelled timer."""
    for handle in wait_timers(clock):
        handle.run()


def show_wait_page(app, loop):
    """Draw the waiting page as both waits do, its timer on ``loop``; True when it was shown."""
    return _runtime().show_continue_wait(app, WAIT_PAGE, loop)
