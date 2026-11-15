"""The course line's next step and what a button of its own pages does (design 13).

The course decision returns one of these values and a single function performs
it: one of the line's own pages, the batch introduction, the diagnostic line,
a teaching step or a question. Pure: no Toga, no store, no app object;
store-side values are typed ``object``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias


@dataclass(frozen=True)
class LibraryOff:
    """The page 所属内容库已停用: the head's content library is off, missing or superseded."""

    count: int   # how many course items wait for their library (the head's library_unavailable count)


@dataclass(frozen=True)
class MaterialBlocked:
    """The page 材料待核: the head's material changed and its old questions wait for a check."""

    question_ids: object   # the head's question ids as stored, handed unchanged to the replacement rows


@dataclass(frozen=True)
class Batch:
    """The batch introduction opened or resumed a batch for this head."""


PAUSED_NO_HEAD = "no_head"      # nothing to serve and some kinds are closed
PAUSED_DISABLED = "disabled"    # the head itself is of a closed kind
PAUSED_QUIZ = "batch_quiz"      # the batch quiz's frozen head is of a closed kind


@dataclass(frozen=True)
class Paused:
    """The paused page; whether it asks for the device's voice is read when the page is drawn."""

    reason: str   # PAUSED_NO_HEAD, PAUSED_DISABLED or PAUSED_QUIZ
    text: str     # the first line of the page that names the closed kinds


@dataclass(frozen=True)
class Done:
    """The page 课程学习 (本次学习已保存): nothing is left; the round is already marked finished."""


@dataclass(frozen=True)
class Diagnostic:
    """The diagnostic line takes over."""

    episode_id: object


@dataclass(frozen=True)
class Wait:
    """The page 稍后继续."""

    due: object = None   # the head's due time: an ISO string or None


@dataclass(frozen=True)
class Prerequisite:
    """The page 先巩固前置知识."""

    content: dict        # the item waiting (its title, id and instruction scope)
    due: object = None   # the head's due time: an ISO string or None
    taught: tuple = ()   # titles of the dependencies already taught, in their order


@dataclass(frozen=True)
class Teach:
    """A teaching step of ``content``."""

    content: dict


@dataclass(frozen=True)
class Ask:
    """The question of ``current`` (the whole head, as the question page takes it)."""

    current: dict


CourseStep: TypeAlias = (LibraryOff | MaterialBlocked | Batch | Paused | Done | Diagnostic | Wait | Prerequisite
                         | Teach | Ask)


class CourseActionKind(Enum):
    """What a button of the course line's own pages does; CourseAction's docstring names the call of each kind."""

    CHECK_WAIT = "check_wait"
    NEW_ROUND = "new_round"
    ACKNOWLEDGE_PREREQUISITES = "acknowledge_prerequisites"
    CONTENT_LIBRARIES = "content_libraries"
    LEARNING_SETTINGS = "learning_settings"
    DEVICE_SETTINGS = "device_settings"
    HOME = "home"


@dataclass(frozen=True)
class CourseAction:
    """What one button of the course line's own pages does, with the values of the drawing.

    The outer layer makes the call when the button is pressed; "button" is the pressed control:
    CHECK_WAIT                 check_learning_wait(button)
    NEW_ROUND                  continue_learning(button)            (a press restarts a finished round)
    ACKNOWLEDGE_PREREQUISITES  learning_store.acknowledge_prerequisites(content_id, instruction_scope=instruction_scope);
                               then continue_added_learning()       (no button, no page guard)
    CONTENT_LIBRARIES          show_content_libraries(button)
    LEARNING_SETTINGS          show_learning_settings(button)
    DEVICE_SETTINGS            show_settings(button)
    HOME                       leave_learning(button)
    """

    kind: CourseActionKind
    content_id: str | None = None      # ACKNOWLEDGE_PREREQUISITES: the waiting item
    instruction_scope: object = None   # ACKNOWLEDGE_PREREQUISITES: its instruction scope (None: none)
