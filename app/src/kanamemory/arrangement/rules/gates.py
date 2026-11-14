"""The three gates that no longer require a correct answer under breadth first (design 12 Q1-Q3).

Pure predicates. The existing modules that own each gate call these with the
orientation as an argument; under THOROUGH every answer is the rule the app
had before. Guide and quiz answers stay guided evidence and never reach the
scheduler either way.
"""
from __future__ import annotations

from collections.abc import Collection

from ..model import Orientation


def guide_may_continue(orientation: Orientation, *, answered_wrong: bool) -> bool:
    """Q1: after a recorded wrong guide answer the learner may read the answer and go on."""
    return orientation is Orientation.BREADTH and answered_wrong


def quiz_item_settled(orientation: Orientation, *, correct: bool, question_ids: Collection[str],
                      wrong_ids: Collection[str]) -> bool:
    """Q2: a batch-quiz item is done when answered correctly, or, under breadth
    first, when each of its frozen questions has a recorded wrong answer."""
    if correct:
        return True
    return (orientation is Orientation.BREADTH and bool(question_ids)
            and set(question_ids) <= set(wrong_ids))


def quiz_requeue_after_miss(orientation: Orientation, *, question_ids: Collection[str],
                            wrong_ids: Collection[str]) -> bool:
    """Q2: after a miss the item is asked again only while it is not settled."""
    return not quiz_item_settled(orientation, correct=False, question_ids=question_ids, wrong_ids=wrong_ids)


def prerequisite_met(orientation: Orientation, *, independently_ready: bool, teaching_done: bool) -> bool:
    """Q3: a prerequisite is met by an independent correct answer, or, under
    breadth first, once its teaching is done (a scored first check, right or wrong)."""
    return independently_ready or (orientation is Orientation.BREADTH and teaching_done)
