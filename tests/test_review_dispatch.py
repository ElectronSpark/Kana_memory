"""Actual due observations outrank untouched new work without rewriting FSRS."""
from datetime import timedelta

from support.learning_store_fixtures import START, item, question, ready, stores
from kanamemory.domain import Question
from kanamemory.review_dispatch import kana_resume_priority, review_priority


def scored(learning, cid, at, rating=3):
    learning.enroll(item(cid, abilities=["understanding"]), now=at)
    when = ready(learning, cid, at)
    q = question(cid, cid + ":first")
    learning.append_questions(q, now=at)
    learning.submit(q["id"], q["answer"], True, now=when)
    return learning.finalize(q["id"], rating, now=when)


def test_old_new_intro_cannot_hide_later_due_review(stores):
    store, learning = stores
    result = scored(learning, "grammar:old", START - timedelta(days=30), 4)
    learning.enroll(item("grammar:new"), now=START)
    learning.append_questions(question("grammar:new", "new"), now=START)
    learning.append_questions(question("grammar:old", "due"), now=START)
    before = list(store._db.iterdump())
    current = learning.current(now=START)
    assert current["question"]["id"] == "due"
    assert current["dispatch_priority"][0] == 3
    assert learning.get_unit("grammar:old", "understanding")["due_at_utc"] == result["due_at_utc"]
    assert list(store._db.iterdump()) == before


def test_due_short_step_precedes_older_mature_review_but_not_actual_draft(stores):
    _, learning = stores
    scored(learning, "grammar:mature", START - timedelta(days=30), 4)
    learning.append_questions(question("grammar:mature", "mature"), now=START)
    scored(learning, "grammar:short", START - timedelta(minutes=4), 1)
    learning.append_questions(question("grammar:short", "short"), now=START)
    assert learning.current(now=START)["question"]["id"] == "short"
    learning.save_draft("mature", {"text": "my unfinished answer"})
    assert learning.current(now=START)["question"]["id"] == "mature"
    learning.set_suspended("grammar:mature", "understanding", True)
    assert learning.current(now=START)["question"]["id"] == "short"


def test_real_kana_due_without_snapshot_timer_precedes_new_queue_entry(stores):
    store, _ = stores
    result = store.record_review("kana-old", "kana_choice", "hiragana", 4,
                                 now=START-timedelta(days=30))
    config = {"practice_config": {"schedule_mode": "spaced"}}
    questions = [Question("kana-new", "kana_choice", "hiragana", "い", "i", ["i", "a"], metadata=config),
                 Question("kana-old", "kana_choice", "hiragana", "あ", "a", ["i", "a"], metadata=config)]
    sid = store.start_session(questions, now=START)
    questions = store.get_session_questions(sid)
    store.save_session_progress(sid, 0, state={})
    before = store.get_card("kana-old", "kana_choice", "hiragana")
    pending = store.prepare_next_question(sid, now=START)
    assert pending["questions"][pending["question_index"]].content_id == "kana-old"
    assert store.get_card("kana-old", "kana_choice", "hiragana") == before
    assert before["due_at_utc"] == result["due_at_utc"]
    assert sorted((vars(q) for q in questions), key=lambda q: q["content_id"]) == sorted(
        (vars(q) for q in pending["questions"]), key=lambda q: q["content_id"])


def test_attempt_priority_does_not_treat_empty_widgets_as_answers():
    assert kana_resume_priority({"page": "question", "romaji_answer": "", "question_audio_ok": True}) is None
    assert kana_resume_priority({"page": "word_question", "word_romaji_parts": ["", ""]}) is None
    assert kana_resume_priority({"page": "ink_question", "strokes": [[(1, 2, 3)]]}) == (1, 0)
    assert kana_resume_priority({"page": "feedback", "answered_text": ""}) == (0, 0)


def test_long_learning_step_is_regular_review_and_future_due_is_not_accelerated():
    card = {"review_count": 2, "due_at_utc": START.isoformat(),
            "scheduler_state": {"state": 1, "last_review": (START-timedelta(days=2)).isoformat()}}
    assert review_priority(card, now=START)[0] == 3
    assert review_priority(card, now=START-timedelta(seconds=1))[0] == 4
