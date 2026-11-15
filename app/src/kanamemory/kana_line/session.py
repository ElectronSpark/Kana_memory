"""The position of the kana practice session: which queue is held and where the learner is in it (design 13).

Pure: no Toga, no store, no app object; the questions are whatever the store handed out.
"""
from __future__ import annotations

from dataclasses import dataclass

from .fields import UNSET, PartlySet


@dataclass(eq=False)
class KanaSession(PartlySet):
    """Where the practice is.

    The app side keeps one of these and REPLACES it whenever a field is assigned; it never edits one in
    place. The queue inside is the line's own list and is changed in place where the line always changed it.
    So a practice page kept for Back holds the position of its own moment.
    """

    session_id: object = UNSET       # str | None: the session whose queue is held, None when none is
    questions: object = UNSET        # list: the queue, the session's questions in their order
    question_index: object = UNSET   # int: the place of the question on the page; the queue's length once done
    session_right: object = UNSET    # int: how many answers of the session were right so far
    question: object = UNSET         # the question on the page, None when the practice holds none
    script: object = UNSET           # "hiragana" | "katakana": the script of the question on the page
