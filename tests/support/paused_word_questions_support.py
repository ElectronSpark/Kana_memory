"""Shared paused word questions fixtures and assertions; no collected scenarios."""
from support.word_context_memory_support import NOW
from copy import deepcopy
from support.word_context_memory_support import word_question


KINDS = ("kana_choice", "word_meaning_kana")


DRAFT = {"page": "word_question", "word_answer": "か", "question_audio_ok": True}


FEEDBACK = {"page": "feedback", "feedback_pending": True, "answered_text": "かき",
            "answer_correct": True, "answer_revealed": False,
            "submitted_at_utc": NOW.isoformat(), "answer_duration_ms": 2500}


def start(store, *, state=DRAFT, tail=()):
    q = word_question()
    sid = store.start_session([q, *tail], now=NOW)
    store.save_session_progress(sid, 0, assisted_indices=[0], state=deepcopy(state))
    # Session creation freezes answer tokens; compare the actual saved/displayed
    # question, not the pre-freezing builder input.
    return sid, store.get_session_questions(sid)[0]


def evidence(store):
    names = ("study_card", "review_event", "session_answer", "word_spelling_attempt", "learning_evidence")
    return {name: [tuple(row) for row in store._db.execute("SELECT * FROM " + name)] for name in names}


def reconcile(store, sid, allowed, *, lazy=False):
    return store.reconcile_session_kinds(sid, allowed, now=NOW, lazy_questions=lazy)
