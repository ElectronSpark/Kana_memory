"""QueuePractice: the practice side of a kana queue (design 12 E3, E4, K11; WP-E part 1).

Memory only, one per learner store (StoreArrangement), with a small state per
kana session (the sitting of that session, its held misses and the memo of
its practice-only answers). At most LIMIT sessions are remembered (the two
lanes' unfinished sessions); touching one never resets another. A session's
state is forgotten when its deck is taken or forget() is called.

- answer(): is this kana answer practice only? Only a question the credit
  rule judges (rules/reinforce.credit_applies: a reinforce batch, not a word
  skill, not a spelling correction) is asked of PracticeCredit, at the
  instant of the answer; everything else is scheduled work and returns None
  without any read. A formal answer returns None and the page rates it
  exactly as before. A practice-only answer is not rated: the sitting folds
  it in (a confirmed miss moves the next formal review forward through the
  bounded auxiliary adjustment), a miss is held for the deck at the end of
  the batch, and the effect is returned for the result page (UI8). Nothing
  else is written here. The decision is remembered per (session, position),
  so showing the result again settles nothing twice; should the confirmed-
  miss write fail after the decision was made, the answer stays practice only
  with effect RETRY (the error still reaches the caller, which keeps the memo).
  The memo is memory only. After a restart a result page shown again is
  settled again; the one durable effect, a confirmed miss that moved the due,
  is recognised by its instant (``advanced_at``: a move is recorded at the
  instant of its answer) and stays the move it was, without being judged or
  recorded again.
- go_on(): the learner leaves the result page. First the exposure row "见过",
  stamped at that instant with the deterministic event id
  "practice:<session>:<position>" (a retry or a re-settled answer writes it
  once), then the session's checkpoint moves past the position through the
  store's own checkpoint save; no answer row, review event or snapshot
  change is written. The practised positions are read back from those rows
  (StudyQueries.practised_positions), never counted here.

take_deck() gives the session's held misses that still wait for a retry in
the sitting, each state once, as a PracticeDeck sharing the sitting.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from ..model import DeckItem, PracticeEffect, StateRef
from ..ports import PracticeExposure
from ..rules.reinforce import credit_applies
from .practice import PracticeCredit
from .practice_deck import PracticeDeck
from .practice_sitting import PracticeSitting

LIMIT = 2              # sessions remembered at once (the two lanes' unfinished sessions)
EVENT_PREFIX = "practice:"

# checkpoint(session_id, next_index, session_right, assisted_indices): the store's checkpoint save
Checkpoint = Callable[[str, int, int, Sequence[int]], object]


def exposure_event_id(session_id: str, index: int) -> str:
    """The evidence event id of the practice-only answer at ``index`` of ``session_id``."""
    return f"{EVENT_PREFIX}{session_id}:{int(index)}"


@dataclass
class _Answer:
    """A settled practice-only answer: what go_on records."""

    ref: StateRef
    question: Mapping[str, object]
    correct: bool
    answered_text: str
    assisted: bool
    effect: PracticeEffect


@dataclass
class _Session:
    sitting: PracticeSitting
    held: list[DeckItem] = field(default_factory=list)
    settled: dict[int, _Answer] = field(default_factory=dict)


class QueuePractice:
    def __init__(self, credit: PracticeCredit, *, new_sitting: Callable[[], PracticeSitting],
                 new_deck: Callable[[Sequence[DeckItem], PracticeSitting], PracticeDeck],
                 exposure: PracticeExposure, checkpoint: Checkpoint,
                 advanced_at: Callable[[StateRef], datetime | None] | None = None):
        """``advanced_at``: ports.ConfirmedMissMove.last_advanced of the store (None: nothing is durable)."""
        self._credit = credit
        self._new_sitting, self._new_deck = new_sitting, new_deck
        self._exposure, self._checkpoint = exposure, checkpoint
        self._advanced_at = advanced_at
        self._sessions: OrderedDict[str, _Session] = OrderedDict()

    def _session(self, session_id: str) -> _Session:
        state = self._sessions.get(session_id)
        if state is None:
            state = self._sessions[session_id] = _Session(self._new_sitting())
            while len(self._sessions) > LIMIT:
                self._sessions.popitem(last=False)
        self._sessions.move_to_end(session_id)
        return state

    def answer(self, session_id: str, index: int, question: object, correct: bool, now: datetime, *,
               answered_text: str = "", assisted: bool = False) -> PracticeEffect | None:
        """None: a formal answer (rate it as before). Otherwise the practice-only answer's effect.

        Reading the state may raise (a missing ability carrier, a busy
        database): nothing was decided then, and the caller treats the
        answer as formal. A failing confirmed-miss write raises after the
        decision: settled() then reports RETRY.
        """
        if not credit_applies(question):
            return None
        state = self._sessions.get(session_id)
        if state is not None and index in state.settled:
            return state.settled[index].effect
        payload = _as_mapping(question)
        ref = StateRef.kana(str(payload["content_id"]), str(payload["kind"]), str(payload["script"]))
        if not correct and self._moved_by_this_answer(state, ref, now):
            # Its result page is shown again after a restart: the move stands, nothing is judged or
            # recorded again (the moved due may even read as due at this instant).
            self._session(session_id).settled[index] = _Answer(ref, payload, False, answered_text, bool(assisted),
                                                               PracticeEffect.ADVANCE)
            return PracticeEffect.ADVANCE
        if PracticeCredit.is_formal(self._credit.kana_answer(payload, now)):
            if state is not None and ref.key in state.sitting.marks:
                state.sitting.note_formal(ref)   # a formal answer ends the state's miss in this sitting
            return None
        state = self._session(session_id)
        settled = state.settled[index] = _Answer(ref, payload, bool(correct), answered_text, bool(assisted),
                                                 PracticeEffect.NONE if correct else PracticeEffect.RETRY)
        if not correct:
            state.held.append(DeckItem(ref, payload))
        # The decision stands before anything is written: a failed write leaves it practice only (RETRY).
        settled.effect = state.sitting.record(ref, bool(correct), now, question=payload,
                                              answered_text=answered_text, assisted=assisted)
        return settled.effect

    def _moved_by_this_answer(self, state: _Session | None, ref: StateRef, now: datetime) -> bool:
        """Whether the miss given at ``now`` is the confirmed miss that already moved this state's due.

        Asked only while the session's sitting knows nothing of the state (a
        new sitting, as after a restart): one read of the state's latest move.
        """
        if self._advanced_at is None or (state is not None and ref.key in state.sitting.marks):
            return False
        return self._advanced_at(ref) == now

    def settled(self, session_id: str, index: int) -> PracticeEffect | None:
        """The effect of the practice-only answer at this position, None when it was not one."""
        state = self._sessions.get(session_id)
        answer = state.settled.get(index) if state is not None else None
        return answer.effect if answer is not None else None

    def go_on(self, session_id: str, index: int, session_right: int, assisted: Sequence[int],
              now: datetime) -> None:
        """The learner leaves the result of the practice-only answer at ``index`` at ``now``.

        The exposure row first (its 60-second wait counts from ``now``), then
        the checkpoint past ``index``. Either may fail (a busy database); a
        retry writes the exposure row once.
        """
        state = self._sessions.get(session_id)
        answer = state.settled.get(index) if state is not None else None
        if answer is None:
            raise ValueError("Only a practice-only answer moves the queue this way")
        self._exposure.record(answer.ref, answer.question, now, correct=answer.correct,
                              answered_text=answer.answered_text, assisted=answer.assisted,
                              event_id=exposure_event_id(session_id, index))
        self._checkpoint(session_id, index + 1, session_right, assisted)

    def held(self, session_id: str) -> tuple[DeckItem, ...]:
        """The session's misses that still carry a miss in its sitting, each state once, in order.

        "Still carries a miss": the state's PracticeMark waits for a retry
        (needs_retry; a confirmed miss resets the streak but keeps it). A miss
        answered right later, or answered formally since, is no longer held.
        """
        state = self._sessions.get(session_id)
        if state is None:
            return ()
        result, seen = [], set()
        for item in state.held:
            if item.ref.key in seen or not state.sitting.mark(item.ref).needs_retry:
                continue
            seen.add(item.ref.key)
            result.append(item)
        return tuple(result)

    def take_deck(self, session_id: str) -> PracticeDeck | None:
        """The held misses of this session as a deck (None when none); the session's state is then forgotten."""
        state = self._sessions.get(session_id)
        items = self.held(session_id)
        self.forget(session_id)
        if not items:
            return None
        return self._new_deck(items, state.sitting)

    def forget(self, session_id: str | None = None) -> None:
        """Drop one session's state, or every session's (a new learner store, a reset)."""
        if session_id is None:
            self._sessions.clear()
        else:
            self._sessions.pop(session_id, None)


def _as_mapping(question: object) -> Mapping[str, object]:
    if isinstance(question, Mapping):
        return dict(question)
    to_dict = getattr(question, "to_dict", None)
    if not callable(to_dict):
        raise ValueError("A kana question or its dict is needed")
    return to_dict()
