"""In-memory dispatch protocol. These types do not migrate stored records."""
from typing import Any, Literal, NotRequired, TypedDict


class CourseHead(TypedDict):
    content: dict[str, Any]
    question: dict[str, Any]
    draft: dict[str, Any]
    result: dict[str, Any] | None
    dispatch_priority: tuple[float, float]
    new_dispatch_continue: NotRequired[bool]
    protected_unassessed_ability: NotRequired[bool]
    service_reason: NotRequired[str]
    selected_course_pool: NotRequired[str]
    service_pool_candidates: NotRequired[dict[str, dict[str, str]]]


class InstructionCurrent(CourseHead):
    type: Literal["instruction"]
    stage: Literal["intro", "guide"]


class QuestionCurrent(CourseHead):
    type: Literal["question"]
    stage: Literal["check"]


class PrerequisiteCurrent(CourseHead):
    type: Literal["prerequisite"]
    dependency_status: dict[str, Any]
    prerequisites: list[Any]
    due_at_utc: str | None
    counts: dict[str, Any]


class ContentBlockedCurrent(TypedDict):
    type: Literal["content_blocked"]
    question_ids: list[str]
    counts: dict[str, Any]


class WaitingCurrent(TypedDict):
    type: Literal["waiting"]
    due_at_utc: str
    counts: dict[str, Any]


class DiagnosticCurrent(TypedDict):
    type: Literal["diagnostic"]
    episode_id: str
    counts: dict[str, Any]


class DisabledCurrent(TypedDict):
    type: Literal["disabled"]
    counts: dict[str, Any]


LearningCurrent = (InstructionCurrent | QuestionCurrent | PrerequisiteCurrent |
                   ContentBlockedCurrent | WaitingCurrent | DiagnosticCurrent | DisabledCurrent)
