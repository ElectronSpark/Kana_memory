"""The evidence of one kana practice attempt, and the marks of a session's attempts (design 13).

An attempt is one question of one session, named ``(session id, question index)``. Its evidence says whether
the answer or a hint was seen, what was submitted and with which verdict, whether a heard question was played
to its end, when the page was drawn and when the answer was first submitted. The marks say which attempts of
the session saw help and which were already submitted.

Pure: no Toga, no store, no clock of its own (times are handed in), no app object.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, fields, replace


class _Unset:
    """The value of an evidence field that was never set since the app started."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


UNSET = _Unset()


@dataclass(eq=False)
class KanaAttempt:
    """What happened in the attempt on the page.

    The app side keeps one of these and REPLACES it whenever a field is assigned; it never edits one in
    place. So a practice page kept for Back holds the attempt of its own moment.
    """

    revealed: object = UNSET        # bool: the answer or a hint was seen, so the attempt counts as assisted
    answered_text: object = UNSET   # str | None: the submitted answer, None before a submit
    correct: object = UNSET         # bool: the verdict of the submitted answer
    audio_ok: object = UNSET        # bool: a heard question was played to its end (true for other kinds)
    started: object = UNSET         # float: the monotonic time at which the question page was drawn
    submitted_at: object = UNSET    # str | None: the UTC time of the first submit, ISO text
    duration_ms: object = UNSET     # int: from the drawing of the page to the first submit

    def get(self, name: str, default=None):
        """A field, or ``default`` when it was never set."""
        value = getattr(self, name)
        return default if value is UNSET else value

    def set_fields(self) -> dict:
        """The fields that were set, by name."""
        return {item.name: getattr(self, item.name) for item in fields(self) if getattr(self, item.name) is not UNSET}


@dataclass(eq=False)
class AttemptMarks:
    """The session's attempts that saw help and those already submitted.

    Facts of the attempts: a page kept for Back does not roll them back. The sets are changed in place.
    """

    assisted: set = field(default_factory=set)
    answered: set = field(default_factory=set)

    def clear(self) -> None:
        self.assisted.clear()
        self.answered.clear()


def saved_assisted(session_id, indices: Iterable) -> set:
    """The assisted marks a saved session brings back: one for each of its saved question indices."""
    return {(session_id, index) for index in indices}


def fresh(attempt: KanaAttempt, key, marks: AttemptMarks, *, heard: bool, started: float) -> KanaAttempt:
    """The evidence when a question page is about to be drawn: seen help carries over from the marks, nothing is
    submitted, a question that is not heard needs no playback. The verdict is left as it was."""
    return replace(attempt, revealed=key in marks.assisted, answered_text=None, audio_ok=not heard,
                   started=started, submitted_at=None, duration_ms=0)


def cleared(attempt: KanaAttempt) -> KanaAttempt:
    """The evidence on a page that stops instead of asking (content updated, kinds paused, a prerequisite
    missing, the review wait): nothing seen, nothing submitted, no verdict."""
    return replace(attempt, revealed=False, answered_text=None, correct=False)


def submitted(attempt: KanaAttempt, answer: str, key, marks: AttemptMarks, *,
              utc_iso: Callable[[], str], monotonic: Callable[[], float]) -> KanaAttempt:
    """The evidence of a submit: the text; the time of the answer and how long it took, frozen at the FIRST
    submit only (reading the result, or restoring it days later, adds no retrieval time); the attempt marked
    as answered, and as assisted when help was seen. The two clocks are read only when the time is frozen."""
    attempt = replace(attempt, answered_text=answer)
    if not attempt.get("submitted_at"):
        at = utc_iso()
        attempt = replace(attempt, submitted_at=at,
                          duration_ms=max(0, int((monotonic() - _required(attempt, "started")) * 1000)))
    marks.answered.add(key)
    if _required(attempt, "revealed"):
        marks.assisted.add(key)
    return attempt


def judged(attempt: KanaAttempt, matches) -> KanaAttempt:
    """The verdict: right only for a matching answer given without help."""
    return replace(attempt, correct=matches and not _required(attempt, "revealed"))


def restored_result(attempt: KanaAttempt, state: dict, *, heard: bool) -> KanaAttempt:
    """The evidence a saved result page brings back before its submit is replayed: whether the sound had been
    played, and the saved time and duration of the answer (so the replay freezes nothing new)."""
    return replace(attempt, audio_ok=not heard or state.get("question_audio_ok", False),
                   submitted_at=state.get("submitted_at_utc"), duration_ms=state.get("answer_duration_ms", 0))


def returned_from_result(marks: AttemptMarks, key) -> None:
    """Back to a question after its result was seen: the attempt saw the answer, and is open again."""
    marks.assisted.add(key)
    marks.answered.discard(key)


def moved_start(started, now: float, entered: float):
    """The start time after a report page that was entered at ``entered``: the time spent there does not count
    as answering time."""
    return started + now - entered


def _required(attempt: KanaAttempt, name: str):
    value = getattr(attempt, name)
    if value is UNSET:
        raise AttributeError(name)
    return value
