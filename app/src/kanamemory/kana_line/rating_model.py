"""Rating a kana practice answer: the arguments of the review record, and what a saved rating does to the
session's position (design 13).

Pure: no Toga, no store, no clock of its own, no app object.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from ..practice_preferences import WORD_PRACTICE_KINDS
from .attempt import KanaAttempt
from .session import KanaSession

ONLY_AGAIN = "已查看学习提示，本题只能选择再学一次。"
UNSCHEDULED = "这次作答未安排复习："
RETRY = "请重试"
BUSY = "数据库正忙，复习记录尚未保存。请稍后再点一次评级。"
NOT_SAVED = "复习记录尚未保存："


@dataclass(frozen=True)
class Refused:
    """Nothing was saved and the position stays; ``message`` says why."""

    message: str


@dataclass(frozen=True)
class Rated:
    """The review was recorded; ``result`` is what the store answered."""

    result: dict


@dataclass(frozen=True)
class ReviewCall:
    """The call that records a review: the positional and the keyword arguments of the store's record_review."""

    args: tuple
    kwargs: dict


def is_word(question) -> bool:
    """A word answer is saved without a rating, and at the time it is saved."""
    return question.kind in WORD_PRACTICE_KINDS


def review_call(question, session: KanaSession, attempt: KanaAttempt, rating, *, finalized_at,
                fair_new: Callable[[], bool], fingerprint: Callable) -> ReviewCall:
    """The arguments of the review record, read in the order the record always took them.

    The time of the answer is the first submit's for a kana answer (None when it was never frozen: the store's
    own clock decides then) and the time of saving for a word answer. ``fair_new`` and ``fingerprint`` are asked
    here, after the values before them were read. A value the record needs that was never set raises
    AttributeError with its name; only a duration that was never set counts as 0, and a time of the answer that
    was never set as never frozen.
    """
    args = (question.content_id, question.kind, question.script, rating)
    kwargs = {
        "session_id": session.need("session_id"),
        "question_index": session.need("question_index"),
        "answered_text": attempt.need("answered_text"),
        "outcome": "correct" if attempt.need("correct") else "incorrect",
        "audio_ok": attempt.need("audio_ok"),
        "content_version": question.metadata["content_version"],
        "assisted": attempt.need("revealed"),
        "duration_ms": attempt.get("duration_ms", 0),
        "now": (finalized_at if is_word(question) else
                datetime.fromisoformat(attempt.need("submitted_at")) if attempt.get("submitted_at") else None),
        "finalized_at": finalized_at,
        "fair_new": fair_new(),
        "question_fingerprint": fingerprint(question),
    }
    return ReviewCall(args, kwargs)


def advanced(session: KanaSession, attempt: KanaAttempt, question) -> KanaSession:
    """The position after a saved rating: one place on, and one more right answer for a right answer that is not
    the correction of an earlier spelling. The count is read only when it grows."""
    counted = {}
    if attempt.need("correct") and not question.metadata.get("spelling_correction_of"):
        counted["session_right"] = session.need("session_right") + 1
    return replace(session, question_index=session.need("question_index") + 1, **counted)


def reread_queue(question, result: dict) -> bool:
    """The queue is read again from the store after a rating in a spaced queue, or when the rating extended it."""
    return (question.metadata.get("practice_config", {}).get("schedule_mode") == "spaced"
            or result.get("queue_extended") is True)
