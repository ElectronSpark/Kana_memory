"""The app side of the home overview (design 13 S3): where the app's state is handed to the home package.

The package takes no app object. This module reads what it needs from the
app when it is asked: the inputs of one refresh (HomeInputs), the stores and
the mixin accessors a HomeOverviewService is built from (per refresh; no app
attribute keeps it), and what a home control does (run_action: each
HomeAction kind to the app method the home called before the move, with the
same arguments). HomeShowing is the page state of one showing, kept on the
app as _study_home. It creates no controls: show_home makes the four widgets,
StudyPathRuntimeMixin.refresh_study_home has the page draw.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .home.model import HomeAction, HomeActionKind, HomeInputs
from .home.service import HomeOverviewService
from .study_path_display import route_kwargs


@dataclass(eq=False)
class HomeShowing:
    """One showing of 今日学习, kept on the app as _study_home (PAGE_BINDINGS["home"]: Back restores it).

    The four widgets show_home made (the stats label, the primary card, the
    column under it, the shared-material notice) and ``next_change_at``, the
    moment the shown numbers next change by themselves: each refresh stores
    the one it drew; the home timer, a backup job on home and a feedback
    return arm from it.
    """

    stats_label: Any
    action_slot: Any
    secondary_slot: Any
    material_slot: Any
    next_change_at: datetime | None = None


def home_inputs(app) -> HomeInputs:
    """The app's device state and preferences in memory, read for one refresh (or one count); nothing is computed."""
    return HomeInputs(
        practice_preferences=getattr(app, "_practice_preferences", {}),
        speech_ready=bool(getattr(getattr(app, "platform", None), "tts_ready", False)),
        handwriting_ready=getattr(app, "model_state", None) == "ready",
        libraries_page=callable(getattr(app, "show_content_libraries", None)))


def home_service(app, *, queue_kinds: Callable[[dict], Sequence[str]] | None = None,
                 word_count: Callable[[Any], int] | None = None) -> HomeOverviewService:
    """The home service for the app's stores as they are now, from the mixin accessors.

    ``queue_kinds(pending)``: a pending main queue's kinds and ``word_count(pending)``:
    the word-drill count, the App's own methods (KanaMemory._queue_kinds,
    _word_drill_remaining), handed in by the caller; an operation that needs
    one the caller left out raises LookupError (the course counts need neither).
    """
    from .study_path_runtime import StudyPathRuntimeMixin
    from .study_path_ui import StudyPathUIMixin
    return HomeOverviewService(
        app.store, app.learning_store, repository=lambda: app.repo, paths=lambda: app.study_path_service(),
        presenter=lambda: StudyPathRuntimeMixin.study_arrangement(app).presenter,
        costs=lambda: StudyPathUIMixin.study_admission(app).costs,
        course_kinds=lambda: app.learning_preferences()["enabled"],
        queue_kinds=queue_kinds or _missing("queue_kinds"), word_count=word_count or _missing("word_count"))


def run_action(app, action: HomeAction, widget=None, *, open_word_drill: Callable[[], object]):
    """Do what a home control carries, as the home did before the move (design 13 S3).

    The values are the action's, that is the refresh's that drew the control
    (the current route then, the section, B10's facts); the app's methods are
    looked up when pressed. ``widget``: the pressed control, handed to the
    methods that took it before. No page check is added: only the word drill
    checks the page (open_home_word_drill), as before.
    """
    kind = action.kind
    if kind is HomeActionKind.PRIMARY:
        from .study_path_runtime import StudyPathRuntimeMixin
        then = action.then

        def add_next(identity, route_id=None):
            # A B10 prompt inside the focus scope names its own route (SectionNote.route_id, H6).
            return app._add_study_sections([identity], **route_kwargs(route_id or action.route_id))
        return StudyPathRuntimeMixin.press_home_primary(
            app, widget, lambda pressed: run_action(app, then, pressed, open_word_drill=open_word_drill),
            route=action.route, no_new_content=action.no_new_content, add_next=add_next)
    if kind is HomeActionKind.CONTINUE:
        return app.continue_study_path(widget)
    if kind is HomeActionKind.ADD_SECTIONS:
        return app._add_study_sections([action.section_id], **route_kwargs(action.route_id))
    if kind is HomeActionKind.ROUTE_PAGE:
        return app.show_study_path(widget)
    if kind is HomeActionKind.OTHER_ROUTE:
        return app.show_study_path(route_id=action.route_id)
    if kind is HomeActionKind.ADD_CONTENT:
        return app.show_study_path_add(widget)
    if kind is HomeActionKind.LIBRARIES:
        return app.show_content_libraries(widget)
    if kind is HomeActionKind.WORD_DRILL:
        return open_word_drill()
    if kind is HomeActionKind.ORIENTATION:
        return app.show_study_orientation()
    if kind is HomeActionKind.CANCEL_FOCUS:
        # 取消重点 (UI6): the focus service clears the focus (one rearrangement), then the home refreshes.
        from .study_path_runtime import StudyPathRuntimeMixin
        StudyPathRuntimeMixin.study_arrangement(app).focus.clear()
        return app.refresh_home_actions()
    if kind is HomeActionKind.CHOOSE_ORIENTATION:
        # The never-chosen card (design 12 UI5): store the choice at once, one re-arrangement, then the home.
        from .arrangement.model import Orientation
        from .study_path_runtime import StudyPathRuntimeMixin
        StudyPathRuntimeMixin.study_arrangement(app).choose_orientation(Orientation(action.orientation))
        return app.refresh_home_actions()
    raise ValueError(f"Unknown home action: {kind}")


def _missing(name: str) -> Callable[..., Any]:
    def ask(*args):
        raise LookupError(f"The home service was built without {name}")
    return ask
