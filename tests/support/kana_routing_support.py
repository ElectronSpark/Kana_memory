"""Shared kana routing fixtures and assertions; no collected scenarios."""
from support.kana_ability_bridge_support import CID
from kanamemory.domain import Question
from support.kana_ability_bridge_support import START
from support.kana_ability_bridge_support import UID
from kanamemory.kana_ability_bridge import activate_bridge
from support.kana_ability_bridge_support import prepare
from datetime import timedelta


def activate(store):
    _, ready = prepare(store)
    activate_bridge(store, UID, routing_ready=True)
    return ready


def answer(store, *, kind="kana_romaji", rating=3, outcome="correct", assisted=False, now=None, tail=False):
    now = now or START + timedelta(days=100)
    q = Question(CID, kind, "hiragana", "あ", "a",
                 metadata={"practice_config": {"schedule_mode": "spaced"}})
    items = [q, Question("kana-3044", "kana_choice", "hiragana", "い", "i")] if tail else [q]
    sid = store.start_session(items, now=now)
    store.save_session_progress(sid, 0, state={"romaji_answer": "a"})
    args = dict(session_id=sid, question_index=0, answered_text="a", outcome=outcome,
                assisted=assisted, now=now)
    return store.record_review(CID, kind, "hiragana", rating, **args), sid, args
