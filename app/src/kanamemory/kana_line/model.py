"""The kana practice's next step and what a button of its own pages does (design 13).

The practice's decision returns one of these values and a single function performs it: nothing at all, the end
of the queue, one of the line's own pages that stop instead of asking, the first introduction of a kana or its
wait, the restore of a parked word, or a question. Pure: no Toga, no store, no app object; store-side values
are typed ``object``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

# The pages on which a question of the practice is answered.
QUESTION_PAGES = {"question", "word_question", "ink_question"}
# The page that shows the result of an answer.
RESULT_PAGE = "feedback"


def answer_started(prepared) -> bool:
    """Whether the head the store prepared was already being answered or shows its result: its saved state
    names a question page or the result page. A queue without a prepared head has no started answer."""
    return isinstance(prepared, dict) and prepared["state"].get("page") in QUESTION_PAGES | {RESULT_PAGE}


@dataclass(frozen=True)
class Stopped:
    """Nothing is shown: the queue's kinds could not be brought up to date and the learner was told why."""


@dataclass(frozen=True)
class Finished:
    """The queue is through: the route goes on, a practice deck opens, extra practice takes over, or one of
    the two end pages (本组单词完成, 本批完成) is shown."""


@dataclass(frozen=True)
class ContentUpdated:
    """The page 题目内容已更新: the content of the head changed and its old question is kept."""


@dataclass(frozen=True)
class KindsPaused:
    """The page 题型已暂停: the head is of a kind that is switched off."""


@dataclass(frozen=True)
class PrerequisiteMissing:
    """The page 先完成前置假名 / 先学所需规则."""

    blocked: object   # the identities the head waits for, as the store names them ("kana:<id>:<script>",
                      # "pronunciation:<rule>")


@dataclass(frozen=True)
class Introduce:
    """The first introduction of the kana of ``question``."""

    question: object


@dataclass(frozen=True)
class IntroWait:
    """The wait after a first introduction, until ``due``."""

    due: object   # an aware datetime


@dataclass(frozen=True)
class ReviewWait:
    """The page 本轮先歇一会儿: the head is not due before ``until``."""

    until: str         # the head's due time, ISO text in UTC
    prepared: object   # the head as the store prepared it (the page tells how much of the queue waits)


@dataclass(frozen=True)
class ResumeParked:
    """A word whose answer was already started becomes current again: it is restored, not drawn empty."""


@dataclass(frozen=True)
class Ask:
    """The question at the place is asked."""

    prepared: object   # the head as the store prepared it, None for a queue without a prepared head


KanaStep: TypeAlias = (Stopped | Finished | ContentUpdated | KindsPaused | PrerequisiteMissing | Introduce | IntroWait
                       | ReviewWait | ResumeParked | Ask)


class KanaActionKind(Enum):
    """What a button of the practice line's own pages does; KanaAction's docstring names the call of each kind."""

    WORD_SETUP = "word_setup"
    PRACTICE_SETUP = "practice_setup"
    PRACTICE_HUB = "practice_hub"
    RECORDS = "records"
    HOME = "home"
    USE_NEW_CONTENT = "use_new_content"
    LEARN_RULE = "learn_rule"
    CHECK_PREREQUISITES = "check_prerequisites"
    CHECK_WAIT = "check_wait"


@dataclass(frozen=True)
class KanaAction:
    """What one button of the practice line's own pages does, with the values of the drawing.

    The outer layer makes the call when the button is pressed; "button" is the pressed control:
    WORD_SETUP           show_word_drill_setup(button)
    PRACTICE_SETUP       show_practice_setup(button)
    PRACTICE_HUB         show_practice_hub(button)
    RECORDS              show_progress(button)
    HOME                 show_home(button)
    USE_NEW_CONTENT      replace_content_question(session_id, retired_id)   (no button)
    LEARN_RULE           enroll_learning(rule)                              (no button)
    CHECK_PREREQUISITES  resume_practice(button)
    CHECK_WAIT           continue_spaced_practice(button)
    """

    kind: KanaActionKind
    session_id: object = None   # USE_NEW_CONTENT: the session whose question is replaced
    retired_id: object = None   # USE_NEW_CONTENT: the name of the retired question
    rule: object = None         # LEARN_RULE: the lesson of the rule, as the content names it


def setup_kind(word_lane: bool) -> KanaActionKind:
    """The settings a stopped practice leads to: the word drill's in the word lane, else the kana practice's."""
    return KanaActionKind.WORD_SETUP if word_lane else KanaActionKind.PRACTICE_SETUP
