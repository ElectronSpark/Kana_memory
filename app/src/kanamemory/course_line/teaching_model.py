"""The teaching steps of the course line (讲解与引导): their session, what a teaching head becomes and what a
button of the stop pages does (design 13).

Pure: no Toga, no store, no app object; store-side values are typed ``dict`` or ``object``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(eq=False)
class TeachingSession:
    """What the course line holds while it teaches.

    The app side keeps one of these and REPLACES it whenever a field is assigned; it never edits one in
    place. The dicts inside are the line's own and are changed in place exactly where the line always
    changed them (the familiarity state's ``busy`` mark). So a page kept for Back holds the session of its
    own moment.
    """

    item: dict | None = None          # the lesson the teaching pages are about (also: whose card or batch quiz is shown)
    guide: dict | None = None         # the frozen guide question on the guide page
    familiarity: dict | None = None   # what the page 开始前 holds: the lesson, the ability asked, the levels so far, busy
    head: dict | None = None          # the course step the line settled on last


# --- what a teaching head becomes (CourseTeaching.step) --------------------------------------------------------

@dataclass(frozen=True)
class GuideBlocked:
    """The page 引导题待核: the lesson's material changed and its frozen guide question waits for a check."""

    item: dict


@dataclass(frozen=True)
class MaterialNotReady:
    """The page 学习材料尚不完整 / 材料待核验: the lesson may not be taught yet."""

    item: dict
    missing: bool   # the material is missing (else: it waits to be verified)


@dataclass(frozen=True)
class OpenAbility:
    """A later ability of a batch item: opened without an introduction page or a familiarity question."""

    item: dict


@dataclass(frozen=True)
class BatchOwns:
    """The open batch owns this lesson: its card or its quiz is shown."""


@dataclass(frozen=True)
class AskFamiliarity:
    """The page 开始前 asks how familiar the lesson is."""

    item: dict


@dataclass(frozen=True)
class GuidePaused:
    """The page 引导已保留: no enabled kind can guide this lesson, or its frozen guide question is of a closed
    kind."""


@dataclass(frozen=True)
class GuideUnavailable:
    """The page 引导例题暂不可用: no guide question can be built for the lesson."""

    item: dict


@dataclass(frozen=True)
class Guide:
    """The guide page: the lesson, its frozen guide question and what the page is built from."""

    item: dict           # the lesson as stored after the decision's writes
    question: dict       # the frozen guide question
    checkpoint: dict     # the lesson's checkpoint (the saved draft and answer, the sequence)
    known: object        # the ids a sentence of this lesson may use (the one read of this page)
    shared_intro: bool   # another ability of the lesson was introduced before: the overview is not repeated


@dataclass(frozen=True)
class Introduce:
    """The introduction page (初次学习 / 快速回顾)."""

    item: dict            # the lesson as the head gave it
    familiarity: object   # its saved familiarity profile, or None
    enrolled: bool        # it has units and an introduction (an unenrolled lesson is only shown)


# --- the page 开始前 (CourseTeaching.open_familiarity) ----------------------------------------------------------

@dataclass(frozen=True)
class FamiliarityOpened:
    """The familiarity question is ready to be asked."""

    item: dict         # the lesson as stored after the question was prepared
    state: dict        # what the page holds (the line's own dict: its ``busy`` mark is changed in place)
    abilities: tuple   # the lesson's abilities, in order
    levels: dict       # the levels chosen so far (saved as a draft)
    ability: str       # the ability asked now


# --- the guide (CourseTeaching.answer_guide, CourseTeaching.finish_guide) --------------------------------------

@dataclass(frozen=True)
class GuideEmpty:
    """The guide answer is empty or cannot be read: nothing was written."""

    message: str   # what the learner is told beside the controls


@dataclass(frozen=True)
class GuideSaveFailed:
    """The guide answer could not be saved (the error is noted for diagnostics)."""


@dataclass(frozen=True)
class GuideAnswered:
    """The guide answer is judged and saved."""

    correct: bool
    draft: object   # the draft saved with it (a pitch or practice question), else None


@dataclass(frozen=True)
class GuideNext:
    """The guide's sequence goes on with its next question."""

    item: dict   # the lesson as stored now


@dataclass(frozen=True)
class GuideFinished:
    """The guide is over: the lesson moved on and the line goes on."""


# --- the stop pages' buttons ------------------------------------------------------------------------------------

class TeachingActionKind(Enum):
    """What a button of the teaching steps' stop pages does; TeachingAction's docstring names each call."""

    LEAVE = "leave"
    HOME = "home"
    DETAIL = "detail"
    CONTINUE_OTHER = "continue_other"
    LEARNING_SETTINGS = "learning_settings"
    START_PRACTICE = "start_practice"
    SKIP_ITEM = "skip_item"
    FAMILIARITY = "familiarity"


@dataclass(frozen=True)
class TeachingAction:
    """What one button of a stop page or of the page 开始前 does, with the values of its drawing.

    The outer layer makes the call when the button is pressed; "button" is the pressed control:
    LEAVE              leave_learning(button)
    HOME               show_home(button)
    DETAIL             show_learning_detail(item)
    CONTINUE_OTHER     continue_added_learning(button)
    LEARNING_SETTINGS  show_learning_settings(button)
    START_PRACTICE     go on to independent practice without a guide (only on the page it was drawn on)
    SKIP_ITEM          pass this lesson over for now (only on the page it was drawn on)
    FAMILIARITY        the learner's answer ``level`` ("new", "some" or "familiar") to the question of this drawing
    """

    kind: TeachingActionKind
    item: dict | None = None    # DETAIL: the lesson
    level: str | None = None    # FAMILIARITY: the answer
