"""Shared word interval backup fixtures and assertions; no collected scenarios."""
from kanamemory.domain import Question
from datetime import datetime
from datetime import timedelta
from datetime import timezone


BASE = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


NOW = BASE + timedelta(hours=1)


KA, KI = "kana-304b", "kana-304d"


ROWS = [{"id": KA, "hiragana": "か", "katakana": "カ"},
        {"id": KI, "hiragana": "き", "katakana": "キ"}]


def word(answer="かき", content_id="word:persimmon", *, drill=True):
    return Question(content_id, "word_meaning_kana", "hiragana", "kaki", answer,
        metadata={"word_romaji_drill": drill, "practice_config": {
            "schedule_mode": "reinforce", "kinds": ["word_meaning_kana"]}})


def submit(store, question=None, *, answered_text=None, now=NOW, assisted=False):
    question = question or word()
    sid = store.start_session([question], now=now)
    store.save_session_progress(sid, 0)
    answer = question.answer if answered_text is None else answered_text
    result = store.record_review(question.content_id, question.kind, question.script,
        None, session_id=sid, question_index=0, answered_text=answer,
        outcome="correct" if answer == question.answer and not assisted else "incorrect",
        assisted=assisted, kana_rows=ROWS, now=now)
    return result


def mature(store):
    for cid in (KA, KI):
        store.record_review(cid, "romaji_kana", "hiragana", 4,
                            outcome="correct", now=BASE)
    assert all(store.get_card(cid, "romaji_kana", "hiragana")["scheduler_state"]["state"] == 2
               for cid in (KA, KI))
