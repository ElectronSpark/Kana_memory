"""The kana early batch of extra practice (design 12 E1, K10): learned kana states asked before their due.

Owner side of the main kana lane, next to study_path's queue assembly. Each
chosen state becomes one question built exactly as plan_kana_queue builds a
due question (study_path.card_questions), but WITHOUT not_before_utc and
without study_path_due_v1, with practice_config {kinds, rounds: 1} and no
schedule_mode. That is an ordinary one-round reinforce batch: the queue
serves it without a due filter, and each answer is judged at the instant of
the answer by the committed rule (rules/reinforce.credit_applies ->
PracticeCredit): not due and 24 hours or more since its last formal rating
is rated (early), rated meanwhile is practice only, come due meanwhile is
rated. Each question carries the mark EXTRA_PRACTICE_MARK (a question
metadata mark like study_path_due_v1; no table, column, setting key or
enumerated value), so the batch is known again after a restart.

An early batch lives only while the mode is on: standing_early_batch finds
the main lane's unfinished marked session, end_early_kana_batch ends it
(the lane's checkpoint goes, and one new scheduling pass of the main lane
plans again the due questions it held and the waiting steps).

The batch starts as the main lane's new session, the way the practice-setup
page replaces a standing batch: start_prepared_session, the first checkpoint
save (which removes the lane's other checkpoint) and one new scheduling pass
of the main lane (practice_round.restart). Appending to the standing queue
is refused by append_prepared_session for questions that are not due.
Cards keep their due, so the replaced queue's waiting relearning copies are
planned again by the queue assembly once due.
"""
from __future__ import annotations

import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from .arrangement.model import Lane, StateRef
from .arrangement.services.extra_session import EARLY_KANA_BATCH
from .domain import PreparedSession, Question, _utc, any_question_metadata, prepare_session_snapshot
from .study_path import card_questions

EXTRA_PRACTICE_MARK = "extra_practice_v1"   # question metadata: asked by an early batch of extra practice


@dataclass(frozen=True)
class EarlyKanaBatch:
    """The built questions of an early batch, their snapshot, and the states that got no question."""

    questions: tuple[Question, ...]
    prepared: PreparedSession | None
    skipped: tuple[StateRef, ...] = ()

    @property
    def built(self) -> tuple[StateRef, ...]:
        return tuple(StateRef.kana(q.content_id, q.kind, q.script) for q in self.questions)


def plan_early_kana_batch(repo, store, states: Iterable[StateRef], *, kinds: Sequence[str],
                          now: datetime | None = None, seed: int | None = None) -> EarlyKanaBatch:
    """One question per kana state (at most EARLY_KANA_BATCH), in the given order; nothing is written.

    ``kinds``: the main lane's kinds in force, stored in each question's
    practice_config like a queue question. A state the builder leaves out
    (its own eligibility) is in ``skipped``.
    """
    refs = list(states)
    if len(refs) > EARLY_KANA_BATCH:
        raise ValueError("An early batch holds at most %d states" % EARLY_KANA_BATCH)
    if any(ref.lane is not Lane.KANA for ref in refs):
        raise ValueError("An early kana batch holds kana states only")
    now = _utc(now)
    seed = secrets.randbits(32) if seed is None else seed
    questions, skipped = [], []
    for ref in refs:
        made = card_questions(repo, store, ref.content_id, ref.ability, ref.script, seed=seed, now=now)
        for question in made:
            question.metadata["practice_config"].update(kinds=list(kinds), rounds=1)
            question.metadata[EXTRA_PRACTICE_MARK] = True
        questions.extend(made)
        if not made:
            skipped.append(ref)
    prepared = prepare_session_snapshot(questions, seed) if questions else None
    return EarlyKanaBatch(tuple(questions), prepared, tuple(skipped))


def start_early_kana_batch(store, batch: EarlyKanaBatch, *, now: datetime | None = None) -> str:
    """Start ``batch`` as the main lane's new session, replacing a standing one; its session id.

    The same three writes as a batch from the practice-setup page that ends
    the standing batch, on the main lane: the snapshot, the first checkpoint
    (the lane's other checkpoint goes) and a new scheduling pass.
    """
    if batch.prepared is None:
        raise ValueError("The early batch has no question")
    previous = store.practice_lane
    store.practice_lane = "main"
    try:
        session_id = store.start_prepared_session(batch.prepared, now=now)
        store.save_session_progress(session_id, 0, 0, state={})
    finally:
        store.practice_lane = previous
    from .practice_round import restart
    restart(store, lane="main")
    return session_id


def standing_early_batch(store) -> str | None:
    """The main lane's unfinished session when it is an early batch (a question carries the mark), else None.
    Owner reads only: the lane's checkpoint and its snapshot, without building the questions."""
    progress = store.load_session_progress("main", lazy_questions=True)
    if progress and any_question_metadata(progress["questions"], EXTRA_PRACTICE_MARK):
        return progress["session_id"]
    return None


def end_early_kana_batch(store) -> str | None:
    """End the standing early batch, if there is one; its session id.

    The main lane's checkpoint goes (StudyStore.clear_session_progress) and
    one new scheduling pass of the main lane starts: cards keep their due,
    so due questions the planner had appended to the batch, and waiting
    steps, are planned again. Answers already given stay recorded.
    """
    session_id = standing_early_batch(store)
    if session_id is None:
        return None
    store.clear_session_progress("main")
    from .practice_round import restart
    restart(store, lane="main")
    return session_id
