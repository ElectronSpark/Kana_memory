"""Reverse word choices share kana evidence without becoming memory cards."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

from kanamemory.domain import (
    Question, StudyStore, build_questions, check_answer, session_practice_lane,
    word_kana_answer,
)

BASE = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
NOW = BASE + timedelta(hours=1)
KA, KI, KU = "kana-304b", "kana-304d", "kana-304f"
ROWS = [{"id": cid, "hiragana": hira, "katakana": kata}
        for cid, hira, kata in ((KA, "か", "カ"), (KI, "き", "キ"), (KU, "く", "ク"))]


def reverse(content_id="word:kaki"):
    return Question(content_id, "word_romaji", "hiragana", "かき", "kaki",
        ["kaki", "kaka", "kiku", "kika"], metadata={
            "word_romaji_drill": True, "kana_answer": "かき"})


def forward(content_id="word:kaki"):
    return Question(content_id, "word_meaning_kana", "hiragana", "kaki", "かき",
                    metadata={"word_romaji_drill": True})


def submit(store, question, answer=None, *, assisted=False, now=NOW):
    sid = store.start_session([question], now=now)
    store.save_session_progress(sid, 0)
    answer = question.answer if answer is None else answer
    result = store.record_review(question.content_id, question.kind, question.script, None,
        session_id=sid, question_index=0, answered_text=answer,
        outcome="correct" if check_answer(question, answer) and not assisted else "incorrect",
        assisted=assisted, kana_rows=ROWS, now=now)
    return sid, result


def mature(store):
    for cid in (KA, KI, KU):
        store.record_review(cid, "romaji_kana", "hiragana", 4, outcome="correct", now=BASE)


def cards(store):
    return {cid: store.get_card(cid, "romaji_kana", "hiragana") for cid in (KA, KI, KU)}


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", "UTC") as value:
        mature(value)
        yield value


@pytest.mark.parametrize("answer,ki_sign", [("kaki", 1), ("kaka", -1)])
def test_reverse_adjusts_only_related_kana_due_by_quarter(store, answer, ki_sign):
    before = cards(store)
    history = store.review_history()
    _, result = submit(store, reverse(), answer)
    evidence = result["word_spelling_evidence"]
    assert evidence["expected"] == "かき" and evidence["actual"] == answer
    assert evidence["answer_direction"] == "romaji" and evidence["expected_romaji"] == "kaki"
    assert evidence["source_kind"] == "word_romaji"
    assert {unit["content_id"]: unit["result"] for unit in evidence["units"]} == {
        KA: "correct", KI: "correct" if ki_sign == 1 else "incorrect"}
    after = cards(store)
    for cid, sign in ((KA, 1), (KI, ki_sign)):
        old_due = datetime.fromisoformat(before[cid]["due_at_utc"])
        assert datetime.fromisoformat(after[cid]["due_at_utc"]) == old_due + (old_due - BASE) * .25 * sign
        assert after[cid]["review_count"] == before[cid]["review_count"]
        for key in ("stability", "difficulty", "last_review", "state"):
            assert after[cid]["scheduler_state"][key] == before[cid]["scheduler_state"][key]
    assert after[KU] == before[KU]
    assert store.review_history() == history
    assert store.get_card("word:kaki", "word_romaji", "hiragana") is None
    assert store.stats(NOW)["total_cards"] == 3
    assert store.batch_counts([reverse()], now=NOW)["content_count"] == 0


@pytest.mark.parametrize("reverse_first", [True, False])
def test_opposite_directions_cannot_repeat_same_reading_anchor_adjustment(store, reverse_first):
    first, second = (reverse(), forward("word:alias")) if reverse_first else (forward(), reverse("word:alias"))
    submit(store, first)
    adjusted = cards(store)
    _, result = submit(store, second, now=NOW + timedelta(minutes=1))
    assert result["word_spelling_evidence"]["schedule_adjustments"] == []
    assert cards(store) == adjusted
    assert len(store.word_spelling_events(now=NOW + timedelta(minutes=2))) > 0


@pytest.mark.parametrize("answer", ["", "kaki"])
def test_reveal_and_duplicate_submission_do_not_change_due_or_make_cards(store, answer):
    before = cards(store)
    sid, result = submit(store, reverse(), answer, assisted=True)
    assert result["word_spelling_evidence"]["units"] == []
    assert result["word_spelling_evidence"]["schedule_adjustments"] == []
    assert result["word_spelling_evidence"]["correct"] is False
    store.inspect_backup(store.export_backup())
    repeat = store.record_review("word:kaki", "word_romaji", "hiragana", None,
        session_id=sid, question_index=0, answered_text="kaki", outcome="correct",
        kana_rows=ROWS, now=NOW + timedelta(seconds=1))
    assert repeat["already_recorded"] is True
    assert cards(store) == before
    assert store._db.execute("SELECT COUNT(*) FROM word_spelling_attempt").fetchone()[0] == 1


def test_forward_draft_survives_reverse_append_and_reopen(store, tmp_path):
    store.practice_lane = "word_romaji"
    sid = store.start_session([forward()], now=NOW)
    store.save_session_progress(sid, 0, state={"word_answer": "か", "page": "question"})
    old = store.load_session_progress("word_romaji")
    store.append_session(sid, [reverse("word:other")], expected_token=store.session_append_token(sid))
    current = store.load_session_progress("word_romaji")
    assert current["state"] == old["state"]
    assert current["questions"][0] == old["questions"][0]
    assert len(current["questions"]) == 2
    with StudyStore(tmp_path / "learner", "UTC") as reopened:
        assert reopened.load_session_progress("word_romaji") == current
        assert word_kana_answer(current["questions"][1]) == "かき"


@pytest.mark.parametrize("change", [
    {"metadata": {"kana_answer": "かき"}},
    {"metadata": {"word_romaji_drill": True, "kana_answer": "カキ"}},
    {"answer": "kiku"}, {"options": ["kaki", "kaki"]},
])
def test_invalid_reverse_snapshot_cannot_enter_a_queue(store, change):
    with pytest.raises(ValueError):
        store.start_session([replace(reverse(), **change)], now=NOW)


def test_reverse_is_auxiliary_only_and_never_enters_main_builder(store):
    assert session_practice_lane([forward(), reverse("word:second")]) == "word_romaji"
    with pytest.raises(ValueError, match="dedicated"):
        build_questions(None, store, kind="word_romaji")
    with pytest.raises(ValueError, match="saved question"):
        store.record_review("word:bad", "word_romaji", "hiragana", 4,
                            outcome="correct", answered_text="kaki", now=NOW)
