"""Kana feedback blocks sibling directions briefly, keeping scripts separate."""
from datetime import timedelta
import pytest

from support.learning_store_fixtures import START, stores
from kanamemory.domain import Question, StudyStore
from kanamemory.practice_availability import summarize_queue_availability


def q(cid="kana-a", kind="kana_romaji", script="hiragana"):
    return Question(cid, kind, script, "あ" if script == "hiragana" else "ア", "a", ["a", "i"],
                    metadata={"practice_config": {"schedule_mode": "spaced"}})


def exposed(store):
    return store.record_review("kana-a", "kana_choice", "hiragana", 3,
        now=START-timedelta(minutes=2), finalized_at=START, outcome="correct")


def test_sibling_kind_waits_but_other_script_and_content_do_not(stores):
    store, _ = stores
    original = exposed(store)
    questions = [q(), q(script="katakana"), q("kana-i")]
    sid = store.start_session(questions, now=START)
    store.save_session_progress(sid, 0, state={})
    before = list(store._db.iterdump())
    identities = [(x.content_id, x.kind, x.script) for x in questions]
    assert store.feedback_wait_times(identities, START) == {identities[0]: START+timedelta(seconds=60)}
    counts = summarize_queue_availability(store, now=START)
    assert (counts.ready_count, counts.waiting_count) == (2, 1)
    assert list(store._db.iterdump()) == before
    pending = store.prepare_next_question(sid, now=START)
    assert pending["questions"][pending["question_index"]].script == "katakana"
    assert store.get_card("kana-a", "kana_choice", "hiragana")["due_at_utc"] == original["due_at_utc"]
    assert store.feedback_wait_times(identities, START+timedelta(seconds=60)) == {}


def test_sibling_wait_survives_restart_and_ends_at_exact_boundary(stores, tmp_path):
    store, _ = stores
    exposed(store)
    sid = store.start_session([q()], now=START)
    store.save_session_progress(sid, 0, state={})
    saved = store.export_backup()
    with StudyStore(tmp_path / "restored") as restored:
        restored.import_backup(saved)
        snapshot = restored.get_session_questions(sid)
        pending = restored.prepare_next_question(sid, now=START+timedelta(seconds=59))
        assert pending["waiting_until_utc"] == (START+timedelta(seconds=60)).isoformat()
        with pytest.raises(ValueError, match="feedback"):
            restored.record_review("kana-a", "kana_romaji", "hiragana", 3, session_id=sid,
                question_index=0, now=START+timedelta(seconds=59), finalized_at=START+timedelta(seconds=59))
        assert restored.get_card("kana-a", "kana_romaji", "hiragana") is None
        pending = restored.prepare_next_question(sid, now=START+timedelta(seconds=60))
        assert pending["waiting_until_utc"] is None
        assert pending["questions"] == snapshot
        restored.record_review("kana-a", "kana_romaji", "hiragana", 3, session_id=sid,
            question_index=0, now=START+timedelta(seconds=60), finalized_at=START+timedelta(seconds=60))
        assert restored.get_card("kana-a", "kana_romaji", "hiragana")["review_count"] == 1


def test_existing_real_draft_can_finish_once_despite_sibling_feedback(stores):
    store, _ = stores
    sid = store.start_session([q()], now=START-timedelta(minutes=3))
    state = {"page": "question", "romaji_answer": "a"}
    store.save_session_progress(sid, 0, state=state)
    exposed(store)
    assert summarize_queue_availability(store, now=START).ready_count == 1
    assert store.prepare_next_question(sid, now=START)["state"] == state
    kwargs = dict(session_id=sid, question_index=0, now=START, finalized_at=START,
                  answered_text="a", outcome="correct")
    store.record_review("kana-a", "kana_romaji", "hiragana", 3, **kwargs)
    assert store.record_review("kana-a", "kana_romaji", "hiragana", 3, **kwargs)["already_recorded"]
    # exposed() and this one draft answer share the planned reading carrier (2c44345).
    assert store.get_card("kana-a", "kana_romaji", "hiragana")["review_count"] == 2


def test_unsaved_feedback_and_legacy_observation_do_not_invent_a_finalization(stores):
    store, _ = stores
    sid = store.start_session([q(kind="kana_choice")], now=START)
    store.save_session_progress(sid, 0, state={"page": "feedback", "answered_text": "a", "feedback_pending": True})
    key = ("kana-a", "kana_romaji", "hiragana")
    before = list(store._db.iterdump())
    assert store.feedback_wait_times([key], START) == {}
    assert list(store._db.iterdump()) == before
    store.record_review("kana-a", "kana_choice", "hiragana", 3, now=START)
    assert store.feedback_wait_times([key], START) == {}
