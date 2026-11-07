"""Shared learning reset fixtures and assertions; no collected scenarios."""
from kanamemory.domain import Question
from datetime import datetime
from datetime import timezone


NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def populate(store):
    questions = [
        Question("a", "kana_choice", "hiragana", "あ", "a", ["a", "i", "u", "e"]),
        Question("a", "romaji_kana", "katakana", "a", "ア", ["ア", "イ", "ウ", "エ"]),
        Question("ka", "kana_choice", "hiragana", "か", "ka", ["ka", "ki", "ku", "ke"]),
    ]
    session = store.start_session(questions, seed=42, now=NOW)
    store.save_session_progress(session, 0)
    for index, question in enumerate(questions[:2]):
        store.record_review(question.content_id, question.kind, question.script, 4,
                            session_id=session, question_index=index, outcome="correct", now=NOW)
    store.record_review("ka", "kana_handwriting", "hiragana", None,
                        recognition_ok=False, now=NOW)
    store.set_content_control("a", "suspend", now=NOW)
    store.set_content_control("ka", "bury", now=NOW)
    store.set_content_control("never-studied", "suspend", now=NOW)
    with store._db:
        store._db.execute("UPDATE study_card SET suspended=1")
    store.set_setting("practice_preferences", {"kind": "mixed", "script": "both", "count": 27})
    store.set_daily_new_limit(37)
    store.set_setting("unrelated_future_setting", {"keep": True})
    return session, questions
