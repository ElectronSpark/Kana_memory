"""Data of the home overview: what it reads from the device at every refresh, and what a home button does
(design 13 S3, C1, C2).

Pure: no Toga, no store, no app object.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..arrangement.model import RouteFacts


@dataclass(frozen=True)
class HomeInputs:
    """The device and the preferences in memory, read again at every refresh (C1: data, not the app).

    The settings the home reads (the enabled course kinds, a pending queue's
    kind policy, the word-drill direction) are not here: the service reads
    them where and as often as the home did before the move, through its
    collaborators (HomeOverviewService).
    """

    practice_preferences: dict          # the practice preferences in memory, as the app holds them (not copied): the
                                        # kana preview takes its kinds from them while nothing is pending in the main
                                        # lane, and works them out only then (as before the move)
    speech_ready: bool                  # Japanese speech is ready (heard kinds and audio context words)
    handwriting_ready: bool             # the handwriting model is ready
    libraries_page: bool                # the app can open the content libraries (the disabled-library line needs it)


class HomeActionKind(Enum):
    """What a home control does; HomeAction's docstring names the call of each kind."""

    PRIMARY = "primary"
    CONTINUE = "continue"
    ADD_SECTIONS = "add_sections"
    ROUTE_PAGE = "route_page"
    OTHER_ROUTE = "other_route"
    ADD_CONTENT = "add_content"
    LIBRARIES = "libraries"
    WORD_DRILL = "word_drill"
    ORIENTATION = "orientation"
    CANCEL_FOCUS = "cancel_focus"
    CHOOSE_ORIENTATION = "choose_orientation"


@dataclass(frozen=True)
class HomeAction:
    """What one home control does, with the values of the refresh that drew it (never re-read when pressed).

    The outer layer (kanamemory.home_runtime.run_action) makes the call; "button" is the pressed control:
    PRIMARY       the card's primary button: the arrangement's two first-time questions (the orientation
                  screen on a fresh install, B10's prompt) asked from ``route`` and ``no_new_content``, then
                  ``then``; the prompt's 加入下一小节并开始 adds in the prompt's route, else in ``route_id``
    CONTINUE      continue_study_path(button)
    ADD_SECTIONS  _add_study_sections([section_id], **route_kwargs(route_id))
    ROUTE_PAGE    show_study_path(button)
    OTHER_ROUTE   show_study_path(route_id=route_id)
    ADD_CONTENT   show_study_path_add(button)
    LIBRARIES     show_content_libraries(button)
    WORD_DRILL    open_home_word_drill() (the only one that checks the page shown)
    ORIENTATION   show_study_orientation()
    CANCEL_FOCUS  the focus service's clear(), then the home redraws
    CHOOSE_ORIENTATION  store ``orientation`` (the never-chosen card's two buttons) and re-arrange the
                  main round once (StoreArrangement.choose_orientation), then the home redraws
    """

    kind: HomeActionKind
    section_id: str | None = None      # ADD_SECTIONS: the section to add
    route_id: str | None = None        # ADD_SECTIONS: the route to add it in (the core route: CORE_ROUTE_ID or None);
                                       # OTHER_ROUTE: the route page to open; PRIMARY: the current route of the refresh
    then: HomeAction | None = None     # PRIMARY: CONTINUE, ADD_SECTIONS or ROUTE_PAGE
    route: RouteFacts | None = None    # PRIMARY: the current route as the refresh read it
    no_new_content: bool = False       # PRIMARY: B10's "nothing new to start" of the refresh
    orientation: str | None = None     # CHOOSE_ORIENTATION: the Orientation value to store


OPEN_ORIENTATION = HomeAction(HomeActionKind.ORIENTATION)
CANCEL_FOCUS = HomeAction(HomeActionKind.CANCEL_FOCUS)


def choose_orientation(mode) -> HomeAction:
    """The never-chosen card's button for ``mode`` (an arrangement Orientation)."""
    return HomeAction(HomeActionKind.CHOOSE_ORIENTATION, orientation=getattr(mode, "value", mode))
