"""Learned content reaches its next supported direction before unrelated new work."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from record_test_support import decode_current
from fsrs import Card, Rating

from kanamemory.domain import ALGORITHM_VERSION, Question, StudyStore, build_practice_batch, to_script
from support.domain_support import SmallContent


NOW = datetime(2026, 10, 15, 16, tzinfo=timezone.utc)
KANA = ("kana_choice", "romaji_kana", "kana_romaji", "romaji_kana_full",
        "romaji_handwriting", "kana_handwriting")
ALL = (*KANA, "word_meaning_kana", "word_kana")


class Catalog(SmallContent):
    def __init__(self):
        super().__init__()
        self.words += [{"id": f"word-new-{index}", "reading": "キャット",
                        "gloss": "cat", "practice_approved": True} for index in range(500)]


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as result:
        yield result


def learn(store, content_id="kana-0", script="hiragana", *, days_apart=False, assisted=False):
    assert not content_id.startswith("word-")
    times = (NOW - timedelta(days=2) if days_apart else NOW - timedelta(minutes=30),
             NOW - timedelta(minutes=20))
    for kind, when in zip(KANA[:2], times):
        store.record_review(content_id, kind, script, 4, now=when,
                            outcome="correct", assisted=assisted)


def learn_word_context(store, script="katakana"):
    question = Question("word-reviewed", "word_meaning_kana", script, "cat",
                        to_script("キャット", script))
    for minutes in (30, 20):
        when = NOW - timedelta(minutes=minutes)
        session = store.start_session([question], now=when)
        result = store.record_review(question.content_id, question.kind, script, None,
            now=when, session_id=session, question_index=0, answered_text=question.answer,
            outcome="correct", kana_rows=Catalog().kana_list())
        assert result["card"] is None and result["due_at_utc"] is None


def legacy_word(store, script="hiragana", *, rating=Rating.Easy, minutes=20):
    """Seed a genuine previous-release word card, not a current word attempt."""
    when = NOW - timedelta(minutes=minutes)
    question = Question("word-reviewed", "word_kana", script, "cat", to_script("キャット", script))
    session = store.start_session([question], now=when)
    key = store._card_key(question.content_id, question.kind, script)
    initial = Card(card_id=int(key[:15], 16), due=when)
    updated, log = store.scheduler.review_card(initial, rating, review_datetime=when)
    correct = rating != Rating.Again
    event = str(uuid4())
    with store._db:
        store._db.execute("INSERT INTO study_card VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            key, question.content_id, "fixture", question.kind, script, updated.to_json(),
            ALGORITHM_VERSION, updated.due.isoformat(), when.isoformat(), 1, 0))
        store._db.execute("INSERT INTO review_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            event, session, key, when.isoformat(), int(rating), question.answer if correct else "カット",
            0, "correct" if correct else "incorrect", 100, initial.to_json(), updated.to_json(),
            ALGORITHM_VERSION, log.to_json()))
        store._db.execute("INSERT INTO session_answer VALUES (?,?,?,?)", (session, 0, event, int(correct)))
    return store.get_card(question.content_id, question.kind, script)


def build(store, **options):
    return build_practice_batch(Catalog(), store, **{
        "kinds": KANA, "adaptive": True, "schedule_mode": "spaced",
        "limit": 2, "seed": 0, "now": NOW, "_targets_only": True, **options})


def keys(questions):
    return [(q.content_id, q.kind, q.script) for q in questions]


@pytest.mark.parametrize("learned_script", ["hiragana", "katakana"])
@pytest.mark.parametrize("requested_script", ["same", "both"])
def test_unseen_production_beats_new_content_without_touching_future_cards(
        store, learned_script, requested_script):
    learn(store, script=learned_script)
    assert store.get_due(now=NOW) == []
    original = decode_current(store.export_backup())["tables"]
    script = learned_script if requested_script == "same" else "both"
    for seed in range(20):
        questions = build(store, seed=seed, script=script, limit=1)
        assert {q.content_id for q in questions} == {"kana-0"}
        advanced = [q for q in questions if q.script == learned_script]
        assert {q.kind for q in advanced} == {"kana_romaji", "romaji_kana_full"}
        assert all(q.metadata["adaptive_stage"] == 1 for q in advanced)
    assert decode_current(store.export_backup())["tables"] == original
    assert store.get_card("kana-0", "kana_romaji", learned_script) is None


@pytest.mark.parametrize("script", ["hiragana", "both"])
def test_due_content_stays_ahead_of_advancement_and_fresh_work(store, script):
    learn(store)
    due_script = "katakana" if script == "both" else script
    old_word = legacy_word(store, due_script, rating=Rating.Again, minutes=10)
    store.record_review("kana-16", "kana_romaji", due_script,
                        1, now=NOW - timedelta(minutes=2), outcome="incorrect")
    for seed in range(12):
        questions = build(store, kinds=ALL, script=script, seed=seed, limit=2)
        memory = [q for q in questions if q.kind in KANA]
        assert list(dict.fromkeys(q.content_id for q in memory)) == ["kana-16", "kana-0"]
        assert questions[0].kind == "kana_romaji" and questions[0].script == due_script
        assert {q.kind for q in questions if q.content_id.startswith("word-")} == {"word_meaning_kana"}
        assert store.batch_counts(questions, NOW)["due"] == 1
    assert store.get_card("word-reviewed", "word_kana", due_script) == old_word


@pytest.mark.parametrize("source", ["kana", "word_context"])
def test_measured_kana_advancement_is_not_displaced_by_fresh_items(store, source):
    if source == "kana":
        learned = {"kana-0", "kana-1", "kana-2"}
        for content_id in learned:
            learn(store, content_id, "katakana")
        limit = 2
    else:
        learn_word_context(store)
        learned, limit = {"kana-16"}, 1
        assert store.stats(NOW)["total_cards"] == 0
    for seed in range(20):
        questions = build(store, kinds=ALL, script="both", seed=seed, limit=limit)
        memory = [q for q in questions if q.kind in KANA]
        assert len({q.content_id for q in memory}) == limit
        assert {q.content_id for q in memory} <= learned
        assert all(any(q.content_id == cid and q.script == "katakana"
                       and q.kind in {"kana_romaji", "romaji_kana_full"}
                       for q in memory) for cid in {q.content_id for q in memory})


@pytest.mark.parametrize("script", ["katakana", "hiragana"])
def test_ability_dedupe_keeps_an_earned_input_form_only_when_earned(store, script):
    learn_word_context(store)
    for seed in range(40):
        questions = build(store, script=script, seed=seed, limit=1)
        kinds = {q.kind for q in questions}
        if script == "katakana":
            assert kinds & {"kana_romaji", "romaji_kana_full"}
        else:
            assert kinds == {"kana_choice", "romaji_kana"}
    assert store.stats(NOW)["total_cards"] == 0


def test_context_words_add_to_the_kana_limit_without_displacing_advancement(store):
    for content_id in ("kana-0", "kana-16"):
        learn(store, content_id, "katakana")
    for seed in range(20):
        questions = build(store, kinds=ALL, script="both", seed=seed, limit=2)
        assert {q.content_id for q in questions if q.kind in KANA} == {"kana-0", "kana-16"}
        words = [q for q in questions if q.kind not in KANA]
        assert len({q.content_id for q in words}) == 2
        assert all("kana-16" in q.metadata["context_kana_ids"] for q in words)
        counts = store.batch_counts(questions, NOW)
        assert counts["content_count"] == counts["word_count"] == 2
        assert keys(questions) == keys(build(store, kinds=ALL, script="both", seed=seed, limit=2))


def test_random_ties_vary_across_seeds_but_a_seed_is_repeatable(store):
    learned = {f"kana-{index}" for index in range(6)}
    for content_id in learned:
        learn(store, content_id)
    selections = set()
    for seed in range(20):
        questions = build(store, seed=seed)
        ids = frozenset(q.content_id for q in questions)
        assert len(ids) == 2 and ids <= learned
        selections.add(ids)
        assert keys(questions) == keys(build(store, seed=seed))
    assert len(selections) > 1


@pytest.mark.parametrize("options", [
    {"new_only": True}, {"schedule_mode": "reinforce"}, {"adaptive": False},
    {"kinds": ("kana_romaji",)}, {"kinds": ("kana_romaji",), "script": "both"},
    {"kinds": ("word_kana",), "script": "both"},
    {"kinds": ("kana_romaji", "kana_romaji")},
])
def test_other_modes_keep_the_preexisting_selection_order(store, options):
    learn(store)
    legacy_word(store)
    for seed in range(12):
        expected = build(store, seed=seed, _prioritize_advancement=False, **options)
        assert keys(build(store, seed=seed, **options)) == keys(expected)


def test_new_only_does_not_reintroduce_an_already_learned_item(store):
    for script in ("hiragana", "katakana"):
        learn(store, script=script)
    questions = build(store, kinds=ALL, script="both", new_only=True, limit=100)
    assert "kana-0" not in {q.content_id for q in questions}


def test_no_promotion_from_assistance_or_an_advanced_only_fallback(store):
    learn(store, assisted=True)
    for seed in range(12):
        for kinds in (KANA, ("kana_romaji", "romaji_kana_full")):
            expected = build(store, seed=seed, kinds=kinds, _prioritize_advancement=False)
            assert keys(build(store, seed=seed, kinds=kinds)) == keys(expected)


def test_unavailable_services_cannot_create_advancement_priority(store):
    legacy_word(store)
    options = dict(kinds=ALL, script="both", audio_available=False, handwriting_available=False)
    for seed in range(12):
        expected = build(store, seed=seed, _prioritize_advancement=False, **options)
        actual = build(store, seed=seed, **options)
        assert keys(actual) == keys(expected)
        assert "word_kana" not in {q.kind for q in actual}


def test_delayed_evidence_unlocks_unseen_handwriting_without_repeating_known_cards(store):
    learn(store, days_apart=True)
    for kind in ("kana_romaji", "romaji_kana_full"):
        store.record_review("kana-0", kind, "hiragana", 4,
                            now=NOW - timedelta(minutes=10), outcome="correct")
    questions = build(store, limit=1)
    assert {q.content_id for q in questions} == {"kana-0"}
    assert {q.kind for q in questions} == {"romaji_handwriting", "kana_handwriting"}


@pytest.mark.parametrize("action", ["suspend", "bury"])
def test_unavailable_content_never_enters_advancement_tier(store, action):
    learn(store)
    store.set_content_control("kana-0", action, now=NOW)
    questions = build(store, kinds=ALL, script="both", limit=10)
    assert "kana-0" not in {q.content_id for q in questions}


def test_family_splitting_preserves_the_outer_mixed_policy(store):
    store.record_review("kana-16", "kana_choice", "katakana", 1,
                        now=NOW - timedelta(minutes=2), outcome="incorrect")
    # The word sub-builder receives only one kind, but the outer choice is mixed.
    kinds = ("kana_choice", "word_kana")
    for seed in range(12):
        questions = build(store, kinds=kinds, script="both", limit=1, seed=seed)
        assert {q.content_id for q in questions if q.kind in KANA} == {"kana-16"}
        assert len({q.content_id for q in questions if q.kind == "word_kana"}) == 1
        assert any(q.script == "katakana" and q.kind == "word_kana" for q in questions)
        assert all(q.metadata["practice_config"]["kinds"] == list(kinds) for q in questions)
        assert all(q.metadata["practice_config"]["requested_script"] == "both" for q in questions)
