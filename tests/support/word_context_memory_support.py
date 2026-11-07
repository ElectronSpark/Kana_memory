"""Shared word context memory fixtures and assertions; no collected scenarios."""
from kanamemory.domain import Question
from datetime import datetime
from datetime import timezone


NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


A, KA, KI, KU = "kana-3042", "kana-304b", "kana-304d", "kana-304f"


class ContextContent:
    rows = [{"id": cid, "hiragana": hira, "katakana": kata, "romaji": roman,
             "category": category, "tier": "modern_core"}
        for cid, hira, kata, roman, category in [
            (A, "あ", "ア", "a", "basic"), (KA, "か", "カ", "ka", "basic"),
            (KI, "き", "キ", "ki", "basic"), (KU, "く", "ク", "ku", "basic"),
            ("kana-304d-3083", "きゃ", "キャ", "kya", "yoon"),
            ("kana-3063", "っ", "ッ", "", "small")]]
    words = [{"id": cid, "reading": reading, "gloss": "example", "practice_approved": True}
        for cid, reading in [("word-a", "あ"), ("word-ka", "かき"),
                             ("word-ku", "く"), ("word-group", "きゃっ")]]

    def metadata(self): return {"content_version": "fixture"}
    def kana_list(self): return self.rows
    def all_kana_symbols(self, script): return [r[script] for r in self.rows]
    def practice_word_candidates(self, **kwargs): return self.words


def word_question():
    return Question("word-ka", "word_meaning_kana", "hiragana", "example", "かき",
        ["か", "き", "く"], metadata={"content_version": "fixture", "practice_config": {
            "kinds": ["word_meaning_kana", "kana_choice"], "schedule_mode": "spaced"}})


def begin(store, q=None, tail=()):
    q = q or word_question()
    sid = store.start_session([q, *tail], now=NOW)
    store.save_session_progress(sid, 0)
    return sid, q


def answer_word(store, repo, sid, q, answer="かき", now=NOW, **kwargs):
    return store.record_review(q.content_id, q.kind, q.script, None,
        session_id=sid, question_index=0, answered_text=answer,
        outcome="correct" if answer == q.answer else "incorrect", kana_rows=repo.kana_list(),
        now=now, **kwargs)
