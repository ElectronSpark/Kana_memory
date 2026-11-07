"""Shared word romaji lanes fixtures and assertions; no collected scenarios."""
from kanamemory.domain import Question
from datetime import datetime
from datetime import timezone


NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


KA, KI = "kana-304b", "kana-304d"


ROWS = [{"id": KA, "hiragana": "か", "katakana": "カ"},
        {"id": KI, "hiragana": "き", "katakana": "キ"}]


def question(cid="word:one", *, drill=True, answer="かき", script="hiragana"):
    return Question(cid, "word_meaning_kana", script, "kaki", answer,
        metadata={"content_version": "1", "word_romaji_drill": drill,
                  "practice_config": {"kinds": ["word_meaning_kana"], "schedule_mode": "reinforce"}})


def main_question(cid=KA, *, due=None):
    metadata = {"not_before_utc": due.isoformat()} if due else {}
    return Question(cid, "kana_choice", "hiragana", "か", "ka", ["ka"], metadata=metadata)


def begin(store, questions, state=None):
    sid = store.start_session(questions, now=NOW)
    store.save_session_progress(sid, 0, state=state)
    return sid


def submit(store, q=None, *, answer=None, now=NOW, assisted=False, corrections=None):
    q = q or question()
    sid = begin(store, [q])
    answer = q.answer if answer is None else answer
    return store.record_review(q.content_id, q.kind, q.script, None,
        session_id=sid, question_index=0, answered_text=answer,
        outcome="correct" if answer == q.answer and not assisted else "incorrect",
        assisted=assisted, kana_rows=ROWS, spelling_corrections=corrections, now=now)
