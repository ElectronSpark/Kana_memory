"""The teaching steps of the course line (讲解与引导) on the app side: the session the app keeps, and what a
teaching head becomes.

What the line holds while it teaches — the lesson, the guide question, the
familiarity page's state and the course step settled on last — is one
TeachingSession on the app, reached only through this module. It is replaced,
never edited, when a field of it is assigned (the set_* functions); the dicts
inside are changed in place where the line always changed them. A page kept
for Back therefore holds the session of its own moment.

decide_teaching asks the service (course_line.teaching) what a teaching head
becomes — the service makes the writes of that decision — and
perform_teaching makes the one call the answer stands for: a stop page or the
page 开始前 (drawn by course_line.ui.teaching_pages from a display model), the
batch introduction, or one of the two pages the app's own builders still
assemble (the guide page and the introduction page).

This module reads the app object and imports neither Toga nor the control
components nor the app module; every page method and line entry is looked up
on the app object at the moment of the call, and the page module is imported
at call time.
"""
from __future__ import annotations

from dataclasses import replace

from .course_line import teaching_display
from .course_line.teaching import CourseTeaching
from .course_line.teaching_model import (AskFamiliarity, BatchOwns, Guide, GuideBlocked, GuidePaused,
                                         GuideUnavailable, Introduce, MaterialNotReady, OpenAbility,
                                         TeachingActionKind, TeachingSession)
from .new_dispatch import study_path_available


def teaching_session(app) -> TeachingSession:
    """The teaching session of the app (an empty one before the line taught anything)."""
    return getattr(app, "_course_teaching", None) or TeachingSession()


def assign(app, **fields) -> None:
    """Assign fields of the session: the app gets a NEW session with them (the one before is left as it was,
    for whatever page still holds it)."""
    app._course_teaching = replace(teaching_session(app), **fields)


def item(app):
    """The lesson the teaching pages are about (None: none)."""
    return teaching_session(app).item


def set_item(app, value) -> None:
    assign(app, item=value)


def guide(app):
    """The frozen guide question on the guide page (None: none)."""
    return teaching_session(app).guide


def set_guide(app, value) -> None:
    assign(app, guide=value)


def familiarity(app):
    """What the page 开始前 holds (None before it was first shown)."""
    return teaching_session(app).familiarity


def set_familiarity(app, value) -> None:
    assign(app, familiarity=value)


def head(app):
    """The course step the line settled on last (None: it ended the round, or nothing was settled yet)."""
    return teaching_session(app).head


def set_head(app, value) -> None:
    assign(app, head=value)


def _fair_new(app):
    from .new_dispatch import enabled
    return enabled(app)


def _record_error(*args):
    from .diagnostic_events import record_error
    return record_error(*args)


def service(app) -> CourseTeaching:
    """The teaching service over this app's stores, built for one call (nothing is kept)."""
    return CourseTeaching(app.learning_store, app.store,
                          enabled_kinds=lambda: app.learning_preferences()["enabled"],
                          fair_new=lambda: _fair_new(app), record_error=_record_error)


# --- what a teaching head becomes ---------------------------------------------------------------------------------

def decide_teaching(app, lesson):
    """What the teaching head ``lesson`` becomes (a value of course_line.teaching_model); the session holds the
    lesson and the guide question from the moments the decision reaches them. A guide question comes with a
    new, empty result column for its page."""
    def keep(**fields):
        assign(app, **fields)
        if "guide" in fields:
            app._learning_guide_result = app._new_guide_result()
    return service(app).step(lesson, keep)


def _on_action(app):
    def on_action(action, widget=None):
        return run_teaching_action(app, action, widget)
    return on_action


def perform_teaching(app, step) -> None:
    """Make the one call the teaching step stands for."""
    if isinstance(step, OpenAbility):
        app.open_batch_ability(step.item)
        return
    if isinstance(step, BatchOwns):
        app.show_intro_batch()
        return
    if isinstance(step, AskFamiliarity):
        app.show_learning_familiarity(step.item)
        return
    if isinstance(step, Guide):
        app._draw_learning_guide(step)
        return
    if isinstance(step, Introduce):
        app._draw_learning_intro(step)
        return
    from .course_line.ui import teaching_pages
    if isinstance(step, GuideBlocked):
        replacement = app._learning_replacement_controls(item=step.item)
        next_content = app.learning_next_content_controls()
        model = teaching_display.guide_blocked_model(step.item)
        app.screen(model.page, model.title,
                   *teaching_pages.guide_blocked_page(model, replacement_rows=replacement,
                                                      next_content_rows=next_content, on_action=_on_action(app)))
        return
    if isinstance(step, MaterialNotReady):
        model = teaching_display.material_not_ready_model(step.item, missing=step.missing)
        app.screen(model.page, model.title, *teaching_pages.material_not_ready_page(model, on_action=_on_action(app)))
        return
    if isinstance(step, GuidePaused):
        model = teaching_display.guide_paused_model()
        app.screen(model.page, model.title, *teaching_pages.guide_paused_page(model, on_action=_on_action(app)))
        return
    if isinstance(step, GuideUnavailable):
        show_guide_unavailable(app, step.item)
        return
    raise TypeError(f"unknown teaching step {step!r}")


def show_guide_unavailable(app, lesson) -> None:
    """No guided example can be built: say why and offer a way forward. Its two ways forward count only on
    this very drawing of the page."""
    from .course_line.ui import teaching_pages
    content_id, scope = lesson["id"], lesson.get("instruction_scope")
    page = [None]

    def here():
        return app.page == teaching_display.WAIT_PAGE and app._page_token == page[0]

    def on_action(action, widget=None):
        if action.kind is TeachingActionKind.START_PRACTICE:
            if not here():
                return None
            try:
                service(app).bridge_without_guide(content_id, scope)
            except (ValueError, KeyError) as exc:
                view.status.text = teaching_display.NOT_SAVED + str(exc)
                return None
            return app.continue_added_learning()
        if action.kind is TeachingActionKind.SKIP_ITEM:
            if not here():
                return None
            skipped = getattr(app, "_guide_skipped_ids", None)
            if not isinstance(skipped, set):
                skipped = app._guide_skipped_ids = set()
            # Process-local: after a restart this item is offered again.
            skipped.add(content_id)
            return app.continue_added_learning()
        return run_teaching_action(app, action, widget)

    model = teaching_display.guide_unavailable_model(lesson)
    view = teaching_pages.guide_unavailable_page(model, on_action=on_action)
    app.screen(model.page, model.title, *view.children)
    page[0] = app._page_token


# --- the page 开始前 --------------------------------------------------------------------------------------------

def show_familiarity(app, lesson):
    """The page 开始前 for ``lesson``; a lesson that may not be taught goes to the teaching step instead (which
    says why)."""
    from .course_line.ui import teaching_pages
    opened = service(app).open_familiarity(lesson, head(app), lambda **fields: assign(app, **fields))
    if opened is None:
        return app.show_learning_instruction(lesson)
    set_familiarity(app, opened.state)
    state = opened.state

    def on_action(action, widget=None):
        if action.kind is not TeachingActionKind.FAMILIARITY:
            return run_teaching_action(app, action, widget)
        if (app.page != teaching_display.FAMILIARITY_PAGE or familiarity(app) is not state
                or state['busy'] or item(app)['introduction_id'] != state['introduction_id']
                or item(app).get('instruction_scope') != state['instruction_scope']):
            return None
        state['busy'] = True
        chosen = {**opened.levels, opened.ability: action.level}
        if set(chosen) != set(opened.abilities):
            try:
                saved = service(app).save_familiarity_draft(opened, chosen)
                app.show_learning_familiarity(saved)
            finally:
                state['busy'] = False
            return None
        try:
            saved = service(app).set_familiarity(opened, chosen)
        finally:
            state['busy'] = False
        if saved["stage"] == "intro":
            app.show_learning_instruction(saved)
        elif study_path_available(app):
            app.continue_study_path(_checkpoint_saved=True)
        else:
            app.continue_learning()
        return None

    model = teaching_display.familiarity_model(opened.item, opened.ability)
    app.screen(model.page, model.title, *teaching_pages.familiarity_page(model, on_action=on_action))
    return None


def run_teaching_action(app, action, widget=None):
    """Make the call a button of a stop page stands for (TeachingAction's docstring)."""
    kind = action.kind
    if kind is TeachingActionKind.LEAVE:
        return app.leave_learning(widget)
    if kind is TeachingActionKind.HOME:
        return app.show_home(widget)
    if kind is TeachingActionKind.DETAIL:
        return app.show_learning_detail(action.item)
    if kind is TeachingActionKind.CONTINUE_OTHER:
        return app.continue_added_learning(widget)
    if kind is TeachingActionKind.LEARNING_SETTINGS:
        return app.show_learning_settings(widget)
    raise ValueError(f"unknown teaching action {action!r}")
