"""The next step of the continue action and what an end-page button does (design 13 S4, C3).

The continue dispatch decides one of these values and a single function
performs it: a learning line, extra practice before a fallback, one of the
three end pages, the reference page, a wait, nothing, or the error page.
Pure: no Toga, no store, no app object; store-side values are typed ``object``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TypeAlias


@dataclass(frozen=True)
class CourseItem:
    """The course line (continue_learning) serves its next item."""

    selected_current: dict | None = None   # the protected or share-promoted head; None: the line chooses


@dataclass(frozen=True)
class KanaItem:
    """The kana line (resume_practice) serves its next question."""

    first_teaching_targets: object = None  # the protected first-teaching head's targets (a set); None otherwise
    prepared: object = None                # the question prepared while deciding; None when none or protected
    preserve_audio: bool = False           # True: a reopened question keeps its "audio was played" mark


@dataclass(frozen=True)
class ExtraOr:
    """Extra practice takes over if it can; otherwise ``fallback`` is performed."""

    fallback: NextStep


@dataclass(frozen=True)
class RoundEnd:
    """The page 本轮已保存: the round is finished and saved."""


@dataclass(frozen=True)
class Reference:
    """The reference page of a reference-only entry."""

    entry_id: str


@dataclass(frozen=True)
class NoKanaKind:
    """The page 暂时没有可进行的假名题型: added kana remain, but no enabled kind can ask them."""


@dataclass(frozen=True)
class RouteEnd:
    """The page 本次学习已保存: nothing is left to serve on the route."""

    service: object   # the study path service the dispatch read
    path: dict        # the core overview the dispatch read


@dataclass(frozen=True)
class WaitKanaPlan:
    """The wait while the kana queue is assembled in the background; the resume ticket is made after drawing."""

    loop: object
    options: dict
    attempt: int


@dataclass(frozen=True)
class WaitCourseHead:
    """The wait while the course head of a sealed round is computed in the background."""

    loop: object
    snapshot: object
    pending: object
    now: datetime
    prepared_kana: object


STAY_CHECKPOINT = "checkpoint"   # the checkpoint could not be saved
STAY_LANE = "lane"               # the main lane could not be activated
STAY_KINDS = "kinds"             # the kana kinds were reconciled and refused (the alert is already shown)


@dataclass(frozen=True)
class Stay:
    """Nothing is drawn; the current page stays."""

    reason: str   # STAY_CHECKPOINT, STAY_LANE or STAY_KINDS


@dataclass(frozen=True)
class Failed:
    """The page 无法准备下一题 for ``error``."""

    error: BaseException     # the error page shows its reason and marks it as shown
    title: str | None = None   # the course title line; None from the background failures


NextStep: TypeAlias = (CourseItem | KanaItem | ExtraOr | RoundEnd | Reference | NoKanaKind | RouteEnd
                       | WaitKanaPlan | WaitCourseHead | Stay | Failed)


class EndActionKind(Enum):
    """What an end-page button does; EndAction's docstring names the call of each kind."""

    NEW_ROUND = "new_round"
    ADD_SECTIONS = "add_sections"
    PRACTICE_HUB = "practice_hub"
    SETTINGS = "settings"
    ROUTE_PAGE = "route_page"
    HOME = "home"


@dataclass(frozen=True)
class EndAction:
    """What one end-page button does, with the values of the drawing (never re-read when pressed).

    The outer layer makes the call when the button is pressed; "button" is the pressed control:
    NEW_ROUND     continue_study_path(button)
    ADD_SECTIONS  _add_study_sections([section_id], **route_kwargs(route_id))
    PRACTICE_HUB  show_practice_hub(button)
    SETTINGS      show_settings(button)
    ROUTE_PAGE    show_study_path(button)
    HOME          leave_learning(button)
    """

    kind: EndActionKind
    section_id: str | None = None   # ADD_SECTIONS: the section to add
    route_id: str | None = None     # ADD_SECTIONS: the route to add it in (None: the current route)
