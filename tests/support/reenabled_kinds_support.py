"""Shared reenabled kinds fixtures and assertions; no collected scenarios."""
from support.domain_support import START
from support.domain_support import SmallContent
from kanamemory.domain import build_practice_batch


EVIDENCE = ("study_card", "review_event", "session_answer", "practice_attempt", "word_spelling_attempt")


def evidence(store):
    return {table: [tuple(row) for row in store._db.execute(f"SELECT * FROM {table}")]
            for table in EVIDENCE}


def plan(store, kind, script, now, *, new_only=False):
    return build_practice_batch(SmallContent(), store, kinds=(kind,), script=script,
        limit=100, seed=23, schedule_mode="spaced", adaptive=True,
        new_only=new_only, now=now)


def start(store, questions, now=START):
    session = store.start_session(questions, now=now)
    store.save_session_progress(session, 0)
    return session


def rate(store, session, index, rating, now=START):
    question = store.get_session_questions(session)[index]
    return store.record_review(question.content_id, question.kind, question.script,
        rating, session_id=session, question_index=index,
        outcome="incorrect" if rating == 1 else "correct", now=now)


def filtered_learning_queue(store, kind, script):
    learned = next(q for q in plan(store, kind, script, START) if q.content_id == "kana-0")
    keep_kind = "romaji_kana" if kind == "kana_choice" else "kana_choice"
    keep = next(q for q in plan(store, keep_kind, script, START) if q.content_id == "kana-1")
    session = start(store, [learned, keep])
    result = rate(store, session, 0, 1)
    assert result["queue_extended"]
    original_card = result["card"]
    original_evidence = evidence(store)
    before = store.get_session_questions(session)
    assert before[-1].metadata["not_before_utc"] == original_card["due_at_utc"]

    filtered = store.reconcile_session_kinds(session, (keep_kind,))
    assert filtered["question_index"] == 2
    assert filtered["questions"] == [before[0], before[2], before[1]]
    assert evidence(store) == original_evidence
    # Compare the frozen enrollment payload, including its ability binding.
    return session, before[0], before[1], original_card
