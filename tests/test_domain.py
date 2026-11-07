"""Memory scheduling, fair grading, and reproducible practice contracts."""
from datetime import datetime, timedelta, timezone
import json
import random
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fsrs import Card, Rating, Scheduler

from kanamemory.domain import (
    ALGORITHM_VERSION, Question, StudyStore, build_questions,
    check_answer, normalize_answer, to_script,
)

from support.domain_support import UTC, START, SmallContent


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as result:
        yield result


def test_real_fsrs_state_and_rating_sequence_survive_reopen(tmp_path):
    directory = tmp_path / "learner"
    oracle = Scheduler(enable_fuzzing=False)
    with StudyStore(directory, time_zone="UTC") as store:
        key = store._card_key("a", "kana_choice", "hiragana")
        expected = Card(card_id=int(key[:15], 16), due=START)
        for rating, minutes in [(3, 0), (3, 10), (1, 1440), (4, 1450)]:
            when = START + timedelta(minutes=minutes)
            expected, _ = oracle.review_card(expected, Rating(rating), review_datetime=when, review_duration=1200)
            result = store.record_review("a", "kana_choice", "hiragana", rating, now=when,
                                         duration_ms=1200, answered_text="a", content_version="fixture-v7")
            assert result["scheduled"] is True
            assert result["card"]["scheduler_state"] == expected.to_dict()
            assert result["card"]["scheduler_version"] == ALGORITHM_VERSION
        saved = store.get_card("a", "kana_choice", "hiragana")
    with StudyStore(directory, time_zone="UTC") as reopened:
        assert reopened.get_card("a", "kana_choice", "hiragana") == saved
        events = reopened.review_history("a", "kana_choice", "hiragana")
        assert [e["rating"] for e in events] == [3, 3, 1, 4]
        assert events[-1]["state_after"] == saved["scheduler_state"]
        assert all(a["state_after"] == b["state_before"] for a, b in zip(events, events[1:]))
        assert saved["content_version"] == "fixture-v7"


def test_skill_and_script_have_independent_memory_states(store):
    first = store.record_review("a", "kana_choice", "hiragana", 3, now=START)["card"]
    store.record_review("a", "kana_handwriting", "hiragana", 1, now=START)
    store.record_review("a", "kana_choice", "katakana", 4, now=START)
    assert store.get_card("a", "kana_choice", "hiragana") == first
    assert len({r["card_id"] for r in store.review_history()}) == 3
    assert store.stats(START)["new_today"] == 3


@pytest.mark.parametrize("failure", [
    {"audio_ok": False}, {"recognition_ok": False}, {"recognition_confirmed": False},
    {"outcome": "cancelled"}, {"outcome": "technical_failure"},
])
def test_technical_or_unconfirmed_attempt_never_records_forgetting(store, failure):
    before = store.record_review("a", "kana_handwriting", "hiragana", 3, now=START)["card"]
    for cid in ("a", "never-reviewed"):
        result = store.record_review(cid, "kana_handwriting", "hiragana", None,
                                     now=START + timedelta(minutes=10), **failure)
        assert result["scheduled"] is False
    assert store.get_card("a", "kana_handwriting", "hiragana") == before
    assert store.get_card("never-reviewed", "kana_handwriting", "hiragana") is None
    assert store.stats(START)["total_reviews"] == 1
    assert store.stats(START)["new_today"] == 1
    assert store.stats(START)["skipped_attempts"] == 2


def test_due_first_fills_requested_count_without_daily_new_limit(store):
    for n in range(10):
        store.record_review(str(n), "kana_choice", "hiragana", 3, now=START)
    assert store.record_review("other", "romaji_kana", "katakana", 3, now=START)["scheduled"]
    assert store.stats(START)["new_today"] == 11
    assert set(store.select_content_ids([str(n) for n in range(20)], "kana_choice", "hiragana", 20, 7, START)) == {str(n) for n in range(10, 20)}
    due_time = START + timedelta(minutes=11)
    assert set(store.select_content_ids([str(n) for n in range(20)], "kana_choice", "hiragana", 20, 7, due_time)) == {str(n) for n in range(20)}
    next_day = START + timedelta(days=1)
    chosen = store.select_content_ids([str(n) for n in range(20)], "kana_choice", "hiragana", 20, 7, next_day)
    assert set(chosen[:10]) == {str(n) for n in range(10)}
    assert set(chosen[10:]) == {str(n) for n in range(10, 20)}


def test_daily_counts_use_local_midnight_without_limiting_practice(tmp_path):
    tokyo = timezone(timedelta(hours=9), name="Japan test zone")
    before_midnight = datetime(2026, 10, 10, 14, 59, tzinfo=UTC)
    after_midnight = before_midnight + timedelta(minutes=2)
    with StudyStore(tmp_path / "tokyo", time_zone=tokyo, daily_new_limit=1) as store:
        store.record_review("a", "kana_choice", "hiragana", 3, now=before_midnight)
        assert store.stats(before_midnight)["new_today"] == 1
        assert store.stats(after_midnight)["new_today"] == 0
        assert store.stats(before_midnight)["new_remaining"] is None
        assert store.stats(after_midnight)["new_remaining"] is None
        assert store.stats(after_midnight)["reviews_today"] == 0
        sid = store.start_session([], seed=11, now=after_midnight)
        assert store._db.execute("SELECT timezone FROM study_session WHERE session_id=?", (sid,)).fetchone()[0] == "Japan test zone"


def test_seeded_sessions_and_unique_romaji_choices(store):
    repo = SmallContent()
    a = build_questions(repo, store, seed=123, now=START)
    b = build_questions(repo, store, seed=123, now=START)
    c = build_questions(repo, store, seed=124, now=START)
    assert a == b
    assert a != c
    assert len(a) == 10
    for q in a:
        assert len(q.options) == len(set(q.options))
        assert q.options.count(q.answer) == 1
        assert q.metadata["audio_only"] is False
        assert q.metadata["content_version"] == "fixture-v7"
        assert q.metadata["kana_symbol"] in q.prompt
        assert check_answer(q, q.answer)
        assert all(not check_answer(q, wrong) for wrong in q.options if wrong != q.answer)
    sid = store.start_session(a, now=START)
    saved = store._db.execute("SELECT random_seed FROM study_session WHERE session_id=?", (sid,)).fetchone()
    assert store.get_session_questions(sid)[0].answer == a[0].answer
    assert saved[0] == "123"
    assert store.stats(START)["new_today"] == 0  # Seeing a screen is not a review.


def test_handwriting_excludes_uncontextual_homophones(store):
    questions = build_questions(SmallContent(), store, kind="kana_handwriting", limit=100, seed=1, now=START)
    assert questions
    for q in questions:
        assert q.answer not in {"じ", "ぢ", "ず", "づ", "お", "を", "っ"}
        assert q.script == "hiragana"
        assert q.audio_text == to_script(q.answer, "katakana")
        assert not q.options
    isolated = Question("j", "kana_handwriting", "hiragana", "listen", "じ")
    assert check_answer(isolated, "ぢ")
    contextual = Question("j", "kana_handwriting", "hiragana", "context", "じ", metadata={"context_given": True})
    assert not check_answer(contextual, "ぢ")


@pytest.mark.parametrize("category,form", [
    ("unknown", "あ"), ("basic", "あい"), ("yoon", "かゃ"),
    ("foreign_combination", "ティー"),
])
def test_unreviewed_or_malformed_kana_cannot_create_a_silent_listening_question(store, category, form):
    repo = SmallContent()
    repo.kana = [{"id": "unreviewed", "hiragana": to_script(form, "hiragana"),
                  "katakana": to_script(form, "katakana"), "category": category,
                  "tier": "modern_core", "romaji": "unreviewed"}]
    assert build_questions(repo, store, kind="kana_handwriting", limit=10,
                           include_extended=True, seed=1, now=START) == []


@pytest.mark.parametrize("answer,typed,expected", [
    ("が", "か\u3099", True), ("が", "か", False), ("ゃ", "や", False),
    ("コーヒー", "コヒー", False), ("カ", "か", False), ("カ", "ｶ", False),
    ("パ", "バ", False), ("きゃ", "きや", False),
])
def test_grading_preserves_pronunciation_and_script_distinctions(answer, typed, expected):
    q = Question("w", "word_kana", "katakana", "listen", answer)
    assert check_answer(q, typed) is expected
    assert normalize_answer("か\u3099") == "が"


def test_word_research_opt_in_and_both_keyboards_preserve_small_and_long_kana(store):
    repo = SmallContent()
    questions = build_questions(repo, store, kind="word_kana", script="katakana", seed=2, now=START)
    assert {q.content_id for q in questions} == {"word-reviewed"}
    questions = build_questions(repo, store, kind="word_kana", script="katakana", seed=2, now=START, include_research=True)
    assert {q.content_id for q in questions} == {"word-reviewed", "word-research"}
    for q in questions:
        assert "".join(q.metadata["answer_tokens"]) == q.answer
        assert set(q.metadata["answer_tokens"]) <= set(q.options)
        assert len(q.options) == len(set(q.options))
        assert q.metadata["research_content"] == (q.content_id == "word-research")
    full = build_questions(repo, store, kind="word_kana", script="hiragana", seed=2, now=START, word_table="full", include_research=True)
    assert {q.answer for q in full} == {"きゃっと", "こーひー"}
    assert all(set("あいうえおゃゅょっー") <= set(q.options) for q in full)
    assert all(set(q.answer) <= set(q.options) for q in full)
    assert all(all(len(option) == 1 for option in q.options) for q in full)
    with pytest.raises(ValueError):
        build_questions(repo, store, kind="word_kana", word_table="bad")


def test_empty_projected_reading_still_uses_display_reading(store):
    repo = SmallContent()
    repo.words = [dict(id="fallback", reading="", display_reading="コーヒー", chinese_gloss="咖啡", practice_approved=True)]
    question, = build_questions(repo, store, kind="word_kana", script="hiragana", seed=2, now=START)
    assert question.answer == "こーひー" and question.audio_text == "コーヒー"
    assert question.metadata["original_reading"] == "コーヒー"
    assert question.metadata["meaning_hint"] == "咖啡"
    assert question.metadata["research_content"] is False


def test_batched_existing_ids_preserve_due_order_seed_and_suspended_cards(store):
    candidates = ["due-a", "due-b", "future", "suspended", "unreviewed", "other-script", "new-a", "new-b", "new-a"]
    for cid in candidates[:5]:
        store.record_review(cid, "kana_romaji", "hiragana", 3, now=START)
    store.record_review("other-script", "kana_romaji", "katakana", 3, now=START)
    now = START + timedelta(days=100)
    with store._db:
        store._db.execute("UPDATE study_card SET due_at_utc=? WHERE content_id='future'", ((now + timedelta(days=1)).isoformat(),))
        store._db.execute("UPDATE study_card SET suspended=1 WHERE content_id='suspended'")
        store._db.execute("UPDATE study_card SET review_count=0 WHERE content_id='unreviewed'")
    def legacy_selection(seed):
        rng = random.Random(seed)
        distinct = list(dict.fromkeys(candidates))
        due = [r for r in store.get_due("kana_romaji", "hiragana", now=now) if r["content_id"] in distinct]
        rng.shuffle(due)
        due.sort(key=lambda r: r["due_at_utc"])
        selected = [r["content_id"] for r in due[:10]]
        new = [cid for cid in distinct if store.get_card(cid, "kana_romaji", "hiragana") is None]
        rng.shuffle(new)
        selected.extend(new[:10-len(selected)])
        return selected
    for seed in (1, 4, 99):
        expected = legacy_selection(seed)
        assert store.select_content_ids(candidates, "kana_romaji", "hiragana", 10, seed, now) == expected
        assert not {"future", "suspended", "unreviewed"} & set(expected)
        assert "other-script" in expected
    queries = []
    store._db.set_trace_callback(queries.append)
    try:
        store.select_content_ids([f"new-{i}" for i in range(3000)], "kana_romaji", "hiragana", seed=1, now=now)
    finally:
        store._db.set_trace_callback(None)
    assert len(queries) <= 8  # Independent of collection size; previously 3,007.


@pytest.mark.parametrize("content_id", [None, 3, "", "  "])
def test_batched_selection_still_rejects_invalid_content_ids(store, content_id):
    with pytest.raises(ValueError, match="content_id is required"):
        store.select_content_ids([content_id], "word_kana", "hiragana", seed=1, now=START)


def test_projected_content_keeps_seeded_questions_identical_to_full_payloads(store):
    from kanamemory.content import ContentRepository
    path = Path(__file__).resolve().parents[1] / "app/src/kanamemory/resources/content.sqlite3"
    with ContentRepository(path) as repo:
        full_words = repo.practice_words(include_research=True)
        full_kana = repo.kana_list()
        legacy = SimpleNamespace(
            metadata=repo.metadata,
            kana_list=lambda: full_kana,
            practice_word_candidates=lambda include_research=False: [w for w in full_words if include_research or w.get("practice_approved") is True],
            all_kana_symbols=lambda script: list(dict.fromkeys(w[script] for w in full_kana if w.get(script))),
        )
        for script in ("hiragana", "katakana"):
            for table in ("partial", "full"):
                args = dict(kind="word_kana", script=script, word_table=table, seed=731, now=START, include_research=True)
                assert build_questions(repo, store, **args) == build_questions(legacy, store, **args)


def test_invalid_review_cannot_mutate_existing_state(store):
    before = store.record_review("a", "kana_choice", "hiragana", 3, now=START)["card"]
    for bad in (True, 0, 5, None, 3.0, "3"):
        with pytest.raises(ValueError):
            store.record_review("a", "kana_choice", "hiragana", bad, now=START)
    with pytest.raises(ValueError):
        store.record_review("a", "kana_choice", "hiragana", 3, now=START - timedelta(seconds=1))
    assert store.get_card("a", "kana_choice", "hiragana") == before
    assert len(store.review_history()) == 1
    with pytest.raises(ValueError):
        store.stats(datetime(2026, 10, 10))

