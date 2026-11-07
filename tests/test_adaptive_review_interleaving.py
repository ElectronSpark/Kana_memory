"""Actual Good histories reveal mixed review forms early, without editing FSRS."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from kanamemory.domain import StudyStore, build_practice_batch
from kanamemory import domain as domain_module
from record_test_support import decode_current
from support.domain_support import SmallContent


START = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
KANA = ("kana_choice", "romaji_kana", "kana_romaji", "romaji_kana_full",
        "romaji_handwriting", "kana_handwriting")
HISTORIES = [
    ([(0, 3)], 0),
    ([(0, 3), (10, 3)], 1),
    ([(0, 3), (1440, 3)], 2),
    ([(0, 3), (10, 3), (1440, 1), (1450, 3)], 0),
    ([(0, 3), (10, 3), (1440, 1), (1450, 3), (1460, 3)], 1),
]


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as value:
        yield value


def choice_history(store, events, count=10):
    for index in range(count):
        for minutes, rating in events:
            store.record_review(f"kana-{index}", "kana_choice", "hiragana", rating,
                now=START + timedelta(minutes=minutes),
                outcome="correct" if rating > 1 else "incorrect")
    return max(datetime.fromisoformat(store.get_card(f"kana-{index}", "kana_choice", "hiragana")["due_at_utc"])
               for index in range(count))


def build(store, now, **options):
    return build_practice_batch(SmallContent(), store, **{
        "kinds": KANA, "adaptive": True, "schedule_mode": "spaced",
        "limit": 10, "seed": 31, "now": now, **options})


@pytest.mark.parametrize("events,stage", HISTORIES)
@pytest.mark.parametrize("configuration", [
    {}, {"script": "both"},
    {"script": "both", "kinds": (*KANA, "word_kana", "word_meaning_kana")},
])
def test_ten_due_choice_cards_show_another_form_within_first_three_questions(
        store, events, stage, configuration):
    now = choice_history(store, events)
    before = decode_current(store.export_backup())["tables"]
    questions = build(store, now, **configuration)
    assert questions[0].kind == "kana_choice"  # The first overdue cue still leads.
    assert len({q.kind for q in questions[:3]}) >= 2
    assert len({q.content_id for q in questions}) == 10
    assert Counter(q.content_id for q in questions if q.kind == "kana_choice" and q.script == "hiragana") == {
        f"kana-{index}": 1 for index in range(10)}
    hira = [q for q in questions if q.script == "hiragana"]
    assert {q.metadata["adaptive_stage"] for q in hira} == {stage}
    if stage:
        assert {"kana_romaji", "romaji_kana_full"} <= {q.kind for q in hira}
    else:
        assert {q.kind for q in hira} == {"kana_choice", "romaji_kana"}
    if stage == 2:
        assert "romaji_handwriting" in {q.kind for q in hira}
    assert all(left.content_id != right.content_id for left, right in zip(questions, questions[1:]))
    assert questions == build(store, now, **configuration)
    assert decode_current(store.export_backup())["tables"] == before


def test_answering_good_and_preparing_each_question_does_not_restore_the_choice_block(store, tmp_path):
    now = choice_history(store, [(0, 3), (10, 3)])
    questions = build(store, now)
    sid = store.start_session(questions, now=now)
    questions = store.get_session_questions(sid)
    store.save_session_progress(sid, 0)
    shown = []
    for index in range(10):
        when = now + timedelta(seconds=index * 2)
        pending = store.prepare_next_question(sid, now=when)
        assert pending["waiting_until_utc"] is None
        question = pending["questions"][pending["question_index"]]
        shown.append(question)
        assert question == questions[index]
        store.record_review(question.content_id, question.kind, question.script, 3,
            session_id=sid, question_index=pending["question_index"], now=when,
            outcome="correct", answered_text=question.answer)
    assert len({q.kind for q in shown[:3]}) >= 2
    assert {q.kind for q in shown[:9]} == set(KANA[:4])
    # Newly introduced directions may append their own timed Good repetition;
    # preparing the queue still retains the original unanswered mixed prefix.
    pending = store.prepare_next_question(sid, now=now + timedelta(seconds=30))
    assert pending["questions"][:10] == shown
    store.save_session_progress(sid, 10, state={"page": "question", "romaji_answer": "partial"})
    with StudyStore(tmp_path / "restored") as restored:
        restored.import_backup(store.export_backup())
        resumed = restored.prepare_next_question(sid, now=now + timedelta(seconds=40))
        assert resumed["state"]["romaji_answer"] == "partial"
        assert resumed["questions"] == store.get_session_questions(sid)


@pytest.mark.parametrize("script", ["hiragana", "both"])
def test_all_four_unlocked_forms_appear_within_first_nine_after_good_choice_history(store, script, monkeypatch):
    now = choice_history(store, [(0, 3), (10, 3)])
    before = decode_current(store.export_backup())["tables"]
    for seed in range(12):
        questions = build(store, now, script=script, seed=seed)
        assert {q.kind for q in questions[:9]} == set(KANA[:4])
        assert questions[0].kind == "kana_choice"
        assert all(left.content_id != right.content_id for left, right in zip(questions, questions[1:]))
        assert questions == build(store, now, script=script, seed=seed)
        interleave = domain_module._interleave_spaced_batch
        with monkeypatch.context() as original_order:
            original_order.setattr(domain_module, "_interleave_spaced_batch",
                lambda by_content, order, **kwargs: interleave(by_content, order))
            baseline = build(store, now, script=script, seed=seed)
        due_order = lambda values: [q.content_id for q in values if q.kind == "kana_choice" and q.script == "hiragana"]
        # All these cards have equal deadlines. Removing duplicate abilities
        # may reorder ties to keep the same content apart, never drop a review.
        assert Counter(due_order(questions)) == Counter(due_order(baseline))
        deadlines = [store.get_card(cid, "kana_choice", "hiragana")["due_at_utc"] for cid in due_order(questions)]
        assert deadlines == sorted(deadlines)
    assert decode_current(store.export_backup())["tables"] == before


def test_timed_failure_repetition_still_takes_priority_without_changing_another_skill(store):
    now = choice_history(store, [(0, 3), (10, 3)])
    questions = build(store, now)
    sid = store.start_session(questions, now=now)
    questions = store.get_session_questions(sid)
    store.save_session_progress(sid, 0)
    failed = questions[0]
    result = store.record_review(failed.content_id, failed.kind, failed.script, 1,
        session_id=sid, question_index=0, now=now, outcome="incorrect")
    next_question = questions[1]
    store.record_review(next_question.content_id, next_question.kind, next_question.script, 3,
        session_id=sid, question_index=1, now=now + timedelta(seconds=10), outcome="correct")
    pending = store.prepare_next_question(sid, now=datetime.fromisoformat(result["due_at_utc"]))
    repetition = pending["questions"][pending["question_index"]]
    assert (repetition.content_id, repetition.kind, repetition.script) == (
        failed.content_id, failed.kind, failed.script)
    assert repetition.metadata["relearning_of_index"] == 0
    assert store.get_card(failed.content_id, "kana_romaji", failed.script) is None


def test_explicit_single_form_stays_single_and_missing_services_do_not_block_mixing(store):
    now = choice_history(store, [(0, 3), (1440, 3)])
    single = build(store, now, kinds=("kana_choice",))
    assert len(single) == 10 and {q.kind for q in single} == {"kana_choice"}
    visual = build(store, now, handwriting_available=False, audio_available=False)
    assert len({q.kind for q in visual[:3]}) >= 2
    assert {q.kind for q in visual} == {"kana_choice", "romaji_kana", "kana_romaji", "romaji_kana_full"}
