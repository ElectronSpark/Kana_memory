"""Extra practice (加练) inside 继续学习: the app side of StoreArrangement.extra (design 12 E1-E8, UI7, K13).

The one place where the mode meets the pages and the two lanes. The mode and
all its memory (the pool, the loop guard, the due states already reported,
whether a left early batch has been looked for) belong to the store's
ExtraSession, one per learner store and launch. The app object holds only a
counter of steps carried out one inside another (``_extra_steps``, a bound
on the stack) and, as for any batch, its queue in memory.

What the pages call (one line each at their sites):

- extra_button_rows(app): 继续加练 on an end or "only waiting" page of the
  main lane (every 稍后继续 of type waiting too, also when reached from
  课程学习), after its 加入下一小节 / 继续下一节; nothing while the mode is on
  or when no learned state can be practised (one pool read, reused for 30
  seconds).
- take_over(app): at a terminal outcome of the main-lane dispatch. While the
  mode is on, the session's next step is carried out instead of the page
  (True); False lets the page be shown (mode off, or nothing left to
  practise: the mode then ends, and when it had served no extra question an
  alert says so after the page).
- before_next_question(app): after a kana answer on the main lane. Arranged
  work that has newly come due inside an early batch starts one new
  scheduling pass of the main lane (ExtraSession.arranged_news), so the
  dispatch serves it before the next early question.
- early_submit(app, question_id): {"early": True} for exactly the course
  question the session permitted, else {} (the submit call is unchanged);
  early_queue(app, question): after that answer, the next question of that
  unit only is queued also inside a sealed round (a sealed round's frozen
  order does not serve it before the next round), so the unit stays among
  the learned states the next full pool read lists.
- end_extra_practice(app): the home page ends the mode and the early batch
  it left; while the mode is off, the first home page of a store ends a
  batch an earlier launch left. end_left_early_batch(app): the same look,
  once per store, at the start of 继续学习, before anything is served.
- extra_inputs(app): what StoreArrangement.for_store needs to read the pool
  as the main lane works now (kana kinds in force on this device, the
  enabled course kinds, the system Japanese input).

Entering (继续加练, or 开始加练 on the page 加练) switches the mode on and does
exactly what an Arranged step does: one new scheduling pass of the main lane
and continue_study_path. The dispatch serves everything it can first (due
work, steps, new material); the first extra step is carried out by
take_over at its terminal outcome. The steps use the lanes' existing
machinery: Arranged -> practice_round.restart + continue_study_path;
EarlyKana -> kana_early_batch (a new marked main-lane session, started as
the practice-setup page starts one that replaces the standing batch) +
continue_study_path; EarlyCourse -> LearningStore.early_head shown through
continue_learning(_selected_current=head), with the early permit at submit;
Practice -> the practice deck (header 加练, 结束加练, the session's next step
when the deck is used up or arranged work has come due); Nothing -> the mode
ends and the page is shown. A failed read when entering or after a deck
alerts and leaves the page as it is, so pressing again retries.
"""
from . import kana_session_runtime as kana_session
from .arrangement.display import EXTRA_NOTHING
from .arrangement.model import Arranged, EarlyCourse, EarlyKana, Practice

# Steps carried out one inside another without a question page in between. The session's own loop guard
# (states handed back unchanged are set aside) ends such a chain long before; this only bounds the stack.
MAX_STEPS = 24
BUSY = "数据库正忙，请稍后再试。"
# app.PRACTICE_PAGES (app.py is not imported here): the kana practice pages a retained view could bring back.
PRACTICE_PAGES = frozenset({"question", "word_question", "ink_question", "feedback", "session_done", "session_wait",
                            "dependency_wait"})


def _now():
    from .domain import _utc
    return _utc(None)


def _record(exc):
    from .diagnostic_events import record_error
    record_error("practice_rating", "read", exc)


def _say_after_page(app, text):
    """An alert shown after the page the caller is about to show (the event loop runs it once this call ends)."""
    app.loop.call_soon(app.alert, text)


def _existing_session(app):
    """This store's ExtraSession when its arrangement already exists (nothing is built here)."""
    current = getattr(app, "_study_arrangement_value", None)
    return current.extra if current is not None and current.serves(app.store) else None


def _store_session(app):
    """This store's ExtraSession, its arrangement built when needed (the study path's own apps only)."""
    session = _existing_session(app)
    if session is None:
        from .new_dispatch import study_path_available
        if study_path_available(app):
            from .study_path_runtime import StudyPathRuntimeMixin
            session = StudyPathRuntimeMixin.study_arrangement(app).extra
    return session


def _main_lane(app):
    from .new_dispatch import study_path_available
    return study_path_available(app) and getattr(app.store, "practice_lane", "main") == "main"


def main_lane_kana_kinds(app):
    """The kana kinds the main lane uses now: the practice preferences' kinds that this device can serve."""
    from .practice_preferences import clean_preferences, enabled_preference_kinds
    from .study_path import usable_queue_kinds
    kinds = enabled_preference_kinds(clean_preferences(getattr(app, "_practice_preferences", {})))
    platform = getattr(app, "platform", None)
    return usable_queue_kinds(kinds, bool(getattr(platform, "tts_ready", False)),
                              getattr(app, "model_state", None) == "ready")


def extra_inputs(app):
    """StoreArrangement.for_store's extra-practice inputs, read from ``app`` at each pool read."""
    from .answer_input import system_japanese_enabled
    return {"kana_kinds": lambda: main_lane_kana_kinds(app),
            "course_kinds": lambda: tuple(app.learning_preferences()["enabled"]),
            "japanese_input": lambda: bool(system_japanese_enabled(app.store))}


# --- the app's queue in memory -----------------------------------------------------------------------------

def _hold_batch(app, session_id):
    """The app's queue in memory becomes the new early batch, as after the practice-setup page started one:
    a checkpoint of the replaced session can then never be written over the new one."""
    kana_session.assign(app, questions=app.store.get_session_questions(session_id), session_id=session_id,
                        question_index=0, session_right=0)
    marks = getattr(app, "_kana_marks", None)
    if marks is not None:
        marks.clear()


def _forget_batch(app, session_id):
    """An ended early batch leaves the app's memory too when it is the queue held there, so no later
    checkpoint can write it back."""
    if session_id is None or kana_session.session(app).get("session_id") != session_id:
        return
    kana_session.assign(app, questions=[], session_id=None, question_index=0, session_right=0)
    back_stack = getattr(app, "back_stack", None)
    if back_stack is not None:
        back_stack.discard(PRACTICE_PAGES)


def _end_batch(app, session):
    """End the early batch standing on the main lane, if any (kana_early_batch.end_early_kana_batch); raises
    when the store does, and the session then looks again at the next chance."""
    from .kana_early_batch import end_early_kana_batch
    session.note_batch_checked(False)
    ended = end_early_kana_batch(app.store)
    session.note_batch_checked()
    _forget_batch(app, ended)


def _end_batch_reported(app, session):
    try:
        _end_batch(app, session)
    except Exception as exc:   # a busy database: the page is shown; the batch is looked for again later
        _record(exc)


class ExtraPracticeFlow:
    """Extra practice of the app's current store: the button, entering, the steps and leaving."""

    def __init__(self, app):
        from .arrangement.services import ExtraPresenter
        from .study_path_runtime import StudyPathRuntimeMixin
        self.app = app
        arrangement = StudyPathRuntimeMixin.study_arrangement(app)
        self.session = arrangement.extra
        self.presenter = ExtraPresenter(arrangement.launch_notes)
        self.empty = False   # the mode ended before any extra question was answered

    # --- the button and entering ----------------------------------------------------------------------

    def button_rows(self):
        if self.session.active:
            return []
        try:
            available = self.session.available(_now())
        except Exception as exc:   # a busy database: the page is shown without the button
            _record(exc)
            return []
        from .arrangement.ui.extra_practice import extra_button_rows
        return extra_button_rows(self.presenter.button(available), on_start=self.enter)

    def enter(self):
        """继续加练: the page 加练 first once per launch, then the mode."""
        model = self.presenter.intro()
        if model is None:
            return self.start()
        self.presenter.showed_intro()
        from .arrangement.ui.extra_practice import extra_intro_page
        self.app.screen("extra_intro", model.header, *extra_intro_page(model, on_start=self.start,
                                                                        on_back=self.app.leave_learning))

    def start(self):
        """The mode is on, then exactly what an Arranged step does: the dispatch serves what it can first."""
        from .practice_round import restart
        try:
            self.session.start(_now())
            restart(self.app.store, lane="main")
        except Exception as exc:   # a busy database: the mode stays off and the page stays
            self.session.stop()
            _record(exc)
            self.app.alert(BUSY)
            return
        self.app.continue_study_path(_checkpoint_saved=True)

    def end(self):
        """结束加练: the mode and its early batch end and the home page is shown."""
        self.session.stop()
        _end_batch_reported(self.app, self.session)
        self.app.leave_learning()

    # --- the steps ------------------------------------------------------------------------------------

    def carry_on(self, step=None):
        """Carry out the session's next step (``step``: already asked); False when nothing can be practised
        (the mode is then off)."""
        app = self.app
        depth = getattr(app, "_extra_steps", 0)
        if depth >= MAX_STEPS:
            self._stop()
            return False
        app._extra_steps = depth + 1
        try:
            step = self.session.next(_now()) if step is None else step
            if isinstance(step, Arranged):
                return self._arranged()
            if isinstance(step, EarlyKana):
                return self._early_kana(step)
            if isinstance(step, EarlyCourse):
                return self._early_course(step)
            if isinstance(step, Practice):
                return self._practice(step)
            self._stop()
            return False
        finally:
            app._extra_steps = depth

    def _stop(self):
        """The mode ends by itself (nothing to practise, the step cap). An early batch still standing is
        not ended here, in the middle of a dispatch: the look for a left batch is armed again, so the next
        home page or the next 继续学习 ends it before anything of it is served."""
        sitting = self.session.sitting
        self.empty = sitting is not None and sitting.position == 0
        self.session.stop()
        self.session.note_batch_checked(False)

    def _arranged(self):
        from .practice_round import restart
        restart(self.app.store, lane="main")
        self.app.continue_study_path(_checkpoint_saved=True)
        return True

    def _early_kana(self, step):
        from .kana_early_batch import plan_early_kana_batch, start_early_kana_batch
        app, now = self.app, _now()
        batch = plan_early_kana_batch(app.repo, app.store, step.refs, kinds=main_lane_kana_kinds(app), now=now)
        if batch.prepared is None:
            return self.carry_on()   # no question could be built: the session sets these states aside
        _hold_batch(app, start_early_kana_batch(app.store, batch, now=now))
        app.continue_study_path(_checkpoint_saved=True)
        return True

    def _early_course(self, step):
        app = self.app
        head = app.learning_store.early_head(step.ref.content_id, step.ref.ability, _now())
        if head is None:
            return self.carry_on()   # not answerable early now: the session sets it aside
        self.session.permit(step.ref, head["question"]["id"])
        app.continue_learning(_selected_current=head)
        return True

    def _practice(self, step):
        deck = self.session.deck(step)
        shown = self.app.show_practice_deck(deck, presenter=self.presenter.deck(), leave=self.end,
                                            finish=self._after_deck, before_next=self._arranged_due)
        return bool(shown) or self.carry_on()

    def _after_deck(self):
        try:
            step = self.session.next(_now())
        except Exception as exc:   # a busy database: the mode stays on and the deck's page stays (retry)
            _record(exc)
            self.app.alert(BUSY)
            return
        if not self.carry_on(step):
            self.app.continue_study_path(_checkpoint_saved=True)
            if self.empty:
                _say_after_page(self.app, EXTRA_NOTHING)

    def _arranged_due(self):
        try:
            return self.session.arranged_due(_now())
        except Exception as exc:
            _record(exc)
            return False


# --- what the pages call ----------------------------------------------------------------------------------

def extra_button_rows(app):
    """继续加练 for an end or wait page of 继续学习 ([] off the main lane, in the mode, or with nothing to practise)."""
    if not _main_lane(app):
        return []
    return ExtraPracticeFlow(app).button_rows()


def take_over(app):
    """At a terminal outcome of the main-lane dispatch: True when the mode carried out a step instead."""
    session = _existing_session(app)
    if session is None or not session.active or not _main_lane(app):
        return False
    flow = ExtraPracticeFlow(app)
    if flow.carry_on():
        return True
    if flow.empty:
        _say_after_page(app, EXTRA_NOTHING)
    return False


def before_next_question(app):
    """After a main-lane kana answer in the mode: newly due arranged work starts one new scheduling pass."""
    session = _existing_session(app)
    if session is None or not session.active or getattr(app.store, "practice_lane", "main") != "main":
        return
    try:
        news = session.arranged_news(_now())
    except Exception as exc:
        _record(exc)
        return
    if news:
        from .practice_round import restart
        restart(app.store, lane="main")


def early_submit(app, question_id):
    """The submit option of a course answer: early=True only for the question the mode permitted."""
    session = _existing_session(app)
    return {"early": True} if session is not None and session.early_permitted(question_id) else {}


def early_queue(app, question):
    """After the early course answer: that unit's next question is queued also inside a sealed round
    (early_ability), so the unit stays among the states the next pool read lists; else {}."""
    return {"early_ability": question["ability"]} if early_submit(app, question["id"]) else {}


def end_extra_practice(app):
    """The home page: the mode ends with the early batch it left; while the mode is off, a batch an earlier
    launch left is ended once per store (the session remembers that it looked)."""
    try:
        session = _store_session(app)
    except Exception as exc:
        _record(exc)
        return
    if session is None:
        return
    if session.active:
        session.stop()
        _end_batch_reported(app, session)
    elif session.batch_unchecked:
        _end_batch_reported(app, session)


def end_left_early_batch(app):
    """At the start of 继续学习, while the mode is off: an early batch an earlier launch left (or a backup
    brought back) is ended before anything is served; once per store, with the first home page."""
    session = _store_session(app)
    if session is not None and not session.active and session.batch_unchecked:
        _end_batch(app, session)
