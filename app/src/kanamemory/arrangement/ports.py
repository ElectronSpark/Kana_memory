"""The three interfaces the arrangement rules and services ask the outside for.

An interface exists only where something must be kept apart: the clock must
be testable without a store, the new-content candidate must be fetched only
when it can be chosen, a focus scope must be turned into content without
the dispatcher reading routes, and what one answer counts as must be
decidable over states held in memory (design 12 E3). Everything else calls the existing store and
query owners directly.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from .model import Candidate, ClockEvent, DeckAnswerMode, DeckTexts, ExtraEntry, FocusScope, StateRef, StateView


class EventSource(Protocol):
    """Timed study actions of the recent past, already attributed to units."""

    def clock_events(self, now: datetime) -> Sequence[ClockEvent]:
        """Events up to ``now``, oldest first, reaching back far enough to fill the window."""


class CandidateSource(Protocol):
    """The heads the lanes could serve at this dispatch."""

    def review_candidates(self) -> Sequence[Candidate]:
        """The due-review head of each lane that has one (cheap: already computed)."""

    def new_candidate(self) -> Candidate | None:
        """The first-teaching head, or None. Probed only when it could be chosen."""


class ScopeCatalog(Protocol):
    """Turns a focus scope into the content it covers."""

    def contents(self, scope: FocusScope) -> frozenset[str] | None:
        """Content ids inside the scope, including enrolled prerequisites; None when
        the route, node or library it names is gone or disabled."""

    def members(self, scope: FocusScope) -> frozenset[str] | None:
        """The scope's own content ids, without prerequisites (暂不复习 pauses these, design 12 H9);
        None when it does not resolve."""


class StateSource(Protocol):
    """The scheduling state behind one answer, as the credit rule sees it (design 12 E3)."""

    def view(self, ref: StateRef, now: datetime) -> StateView:
        """The state at ``now``: the due the lane's queue uses, whether it is inside its
        learning or relearning steps, its last formal rating and the scheduler's
        retrievability. A state that was never formally rated (no card, no unit, a
        word skill) has ``last_rated`` None."""


class ExtraStates(Protocol):
    """The learned scheduling states extra practice chooses from (design 12 E2)."""

    def read(self, now: datetime) -> Sequence[ExtraEntry]:
        """Every learned state of both lanes that extra practice may serve or must watch at ``now``,
        steps included, in one bulk read; each with its focus-scope flag."""

    def reread(self, entry: ExtraEntry, now: datetime) -> ExtraEntry | None:
        """The same state read again at ``now`` (a point read), keeping its scope flag and card;
        None when it is no longer a learned state."""


class ConfirmedMissMove(Protocol):
    """The one schedule change practice can make: a confirmed miss (design 12 E3, 10 (6))."""

    def last_advanced(self, ref: StateRef) -> datetime | None:
        """When the state's due was last brought forward by an auxiliary adjustment, or None."""

    def advance(self, ref: StateRef, question: Mapping[str, object], now: datetime, *,
                answered_text: str = "", assisted: bool = False) -> datetime | None:
        """Record the miss as one auxiliary evidence row and, when the existing bounded
        adjustment applies, its adjustment row; the state's new due, or None when
        nothing moved (not in review, already due, budget used, ...)."""


class DeckQuestions(Protocol):
    """The lanes' existing question builders and judges, as the practice deck needs them (design 12 E5)."""

    def scheduled(self, ref: StateRef) -> bool:
        """Whether ``ref`` is a scheduling state (False for a kana word skill: judged, never recorded)."""

    def build(self, ref: StateRef, now: datetime) -> Mapping[str, object] | None:
        """A new question about ``ref``, built in memory by the lane's builder; None when none can be built."""

    def mode(self, ref: StateRef, question: Mapping[str, object]) -> DeckAnswerMode:
        """How the deck page lets the learner answer this question."""

    def texts(self, ref: StateRef, question: Mapping[str, object]) -> DeckTexts:
        """Prompt, task, options, answer and explanation as the lane shows them."""

    def judge(self, ref: StateRef, question: Mapping[str, object], answer: str) -> tuple[bool | None, str]:
        """(correct, message) from the lane's existing judge; correct None when it cannot be scored."""


class PracticeExposure(Protocol):
    """Registers that a practice answer showed a question's answer (design 12 E5, 10 (6), AC17)."""

    def record(self, ref: StateRef, question: Mapping[str, object], now: datetime, *, correct: bool,
               answered_text: str = "", assisted: bool = False, event_id: str | None = None) -> None:
        """One evidence row "见过" (role exposure, weight 0) and whatever the lane's existing exposure
        guard reads, so that no formal question of that content is asked within the existing interval.
        ``event_id``: a deterministic id (an existing id writes nothing again); None: a new one."""
