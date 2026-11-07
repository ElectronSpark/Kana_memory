"""Adaptive scaffolding changes cues, never another direction's FSRS state."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from kanamemory.domain import StudyStore, build_practice_batch, build_questions, check_answer
from record_test_support import decode_current
from support.domain_support import SmallContent


START = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
KANA = ("kana_choice", "romaji_kana", "kana_romaji", "romaji_kana_full",
        "romaji_handwriting", "kana_handwriting")
ALL = (*KANA, "word_meaning_kana", "word_kana")


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as value:
        yield value


def build(store, **kwargs):
    return build_practice_batch(SmallContent(), store, **{"kinds": KANA,
        "adaptive": True, "seed": 29, "now": START, "limit": 10, "schedule_mode": "spaced", **kwargs})


def success(store, kind="kana_choice", script="hiragana", when=START, content_id="kana-0"):
    return store.record_review(content_id, kind, script, 4, now=when,
                               outcome="correct", answered_text="a")


def test_first_batch_has_two_supported_directions_and_no_premature_production(store):
    before = decode_current(store.export_backup())["tables"]
    questions = build(store)
    assert len(questions) == 20
    assert len({question.content_id for question in questions}) == 10
    assert {question.kind for question in questions} == {"kana_choice", "romaji_kana"}
    assert all(question.metadata["adaptive_stage"] == 0 for question in questions)
    assert all(left.content_id != right.content_id for left, right in zip(questions, questions[1:]))
    assert questions == build(store)
    assert decode_current(store.export_backup())["tables"] == before


def test_two_successes_introduce_production_without_inventing_other_card_states(store):
    success(store, when=START - timedelta(hours=1))
    success(store, kind="romaji_kana", when=START - timedelta(minutes=20))
    before = store.get_card("kana-0", "kana_choice", "hiragana")
    questions = build(store, limit=100)
    target = [question for question in questions if question.content_id == "kana-0"]
    assert {q.kind for q in target} == {"kana_romaji", "romaji_kana_full"}
    assert all(q.metadata["adaptive_stage"] == 1 for q in target)
    assert store.get_card("kana-0", "kana_romaji", "hiragana") is None
    assert store.get_card("kana-0", "kana_choice", "hiragana") == before


def test_delayed_success_introduces_handwriting_and_script_evidence_stays_separate(store):
    success(store, when=START - timedelta(days=2))
    success(store, kind="romaji_kana", when=START - timedelta(minutes=20))
    questions = build(store, script="both", limit=100)
    target = [q for q in questions if q.content_id == "kana-0"]
    by_script = {script: {q.kind for q in target if q.script == script}
                 for script in ("hiragana", "katakana")}
    assert {"romaji_handwriting", "kana_handwriting"} <= by_script["hiragana"]
    assert by_script["katakana"] == {"kana_choice", "romaji_kana"}
    assert {q.metadata["adaptive_stage"] for q in target if q.script == "katakana"} == {0}


def test_failure_restores_support_but_never_hides_existing_due_advanced_direction(store):
    success(store, when=START - timedelta(days=2))
    success(store, kind="romaji_kana", when=START - timedelta(minutes=20))
    store.record_review("kana-0", "kana_romaji", "hiragana", 1,
                        now=START - timedelta(minutes=2), outcome="incorrect")
    target = [q for q in build(store, limit=100) if q.content_id == "kana-0"]
    assert {q.kind for q in target} == {"kana_romaji"}
    assert target[0].metadata["adaptive_stage"] == 0
    assert "romaji_handwriting" not in {q.kind for q in target}


def test_assisted_or_failed_answers_never_unlock_production(store):
    for minute in (10, 5):
        store.record_review("kana-0", "kana_choice", "hiragana", 3,
                            now=START - timedelta(minutes=minute), assisted=True, outcome="correct")
    target = [q for q in build(store, limit=100) if q.content_id == "kana-0"]
    assert {q.kind for q in target} <= {"kana_choice", "romaji_kana"}


def test_missing_services_skip_only_dependent_tasks_and_visual_writing_needs_no_audio(store):
    questions = build(store, kinds=ALL, script="both", limit=10,
                      handwriting_available=False, audio_available=False)
    assert {q.kind for q in questions} == {"kana_choice", "romaji_kana", "word_meaning_kana"}
    assert {q.script for q in questions} == {"hiragana", "katakana"}
    assert len({q.content_id for q in questions if q.kind in KANA}) == 10
    assert {q.content_id for q in questions if q.kind == "word_meaning_kana"} == {"word-reviewed"}
    counts = store.batch_counts(questions, START)
    assert counts["content_count"] == counts["new"] == 10
    assert counts["word_count"] == 1 and counts["auxiliary_count"] == 2
    writing = build(store, kinds=("romaji_handwriting",), audio_available=False)
    assert writing and all(q.kind == "romaji_handwriting" and not q.metadata["audio_only"] for q in writing)
    assert build(store, kinds=("romaji_handwriting",), handwriting_available=False) == []


@pytest.mark.parametrize("script", ["hiragana", "katakana"])
def test_full_table_uses_modern_single_script_characters_and_fair_homophones(store, script):
    questions = build_questions(SmallContent(), store, kind="romaji_kana_full",
                                script=script, limit=100, now=START, seed=1)
    assert questions
    for question in questions:
        assert question.options and all(len(option) == 1 for option in question.options)
        assert set(question.answer) <= set(question.options)
        assert question.metadata["answer_tokens"] == list(question.answer)
        assert question.metadata["word_table"] == "full"
        other_start, other_end = ("ァ", "ヺ") if script == "hiragana" else ("ぁ", "ゖ")
        assert all(not other_start <= option <= other_end for option in question.options)
        assert check_answer(question, question.answer)
        for alternative in question.accepted_answers:
            assert check_answer(question, alternative)


def test_all_forms_both_scripts_keep_single_script_cues_and_backup_roundtrip(store, tmp_path):
    # Explicit advanced-only selection bypasses scaffolding without conflating
    # production and handwriting review histories.
    questions = build(store, kinds=("romaji_kana_full", "romaji_handwriting", "word_meaning_kana"),
                      script="both", limit=10)
    for question in questions:
        assert question.script in {"hiragana", "katakana"}
        assert question.metadata["practice_config"]["requested_script"] == "both"
        assert question.metadata["practice_config"]["adaptive"]
        assert question.metadata["batch_content_count"] == 10
    session = store.start_session(questions, now=START)
    questions = store.get_session_questions(session)
    store.save_session_progress(session, 0, state={"word_answer": "あ", "page": "word_question"})
    with StudyStore(tmp_path / "restored") as restored:
        restored.import_backup(store.export_backup())
        assert restored.load_session_progress()["questions"] == questions
        assert restored.load_session_progress()["state"]["word_answer"] == "あ"


def test_due_advanced_card_has_priority_across_families_and_scripts(store):
    store.record_review("kana-16", "kana_romaji", "katakana", 1,
                        now=START - timedelta(minutes=2), outcome="incorrect")
    questions = build(store, kinds=ALL, script="both", limit=1)
    assert {q.content_id for q in questions if q.kind in KANA} == {"kana-16"}
    assert questions[0].kind == "kana_romaji" and questions[0].script == "katakana"
    assert {q.content_id for q in questions if q.kind not in KANA} == {"word-reviewed"}
    assert {q.kind for q in questions if q.kind not in KANA} == {"word_meaning_kana"}
    assert store.batch_counts(questions, START)["due"] == 1
    assert store.stats(START)["total_reviews"] == 1


def test_two_new_kana_keep_their_slots_with_context_words_and_both_scripts_enabled(store):
    questions = build(store, kinds=ALL, script="both", limit=2)
    kana_ids = {q.content_id for q in questions if q.kind in KANA}
    words = [q for q in questions if q.kind not in KANA]
    assert len(kana_ids) == 2
    assert {q.content_id for q in words} == {"word-reviewed"}
    assert all(set(q.metadata["context_kana_ids"]) & kana_ids for q in words)
    counts = store.batch_counts(questions, START)
    assert counts["content_count"] == counts["new"] == 2
    assert counts["word_count"] == 1
    for content_id in {q.content_id for q in questions}:
        assert {q.script for q in questions if q.content_id == content_id} == {"hiragana", "katakana"}


def test_explicit_single_advanced_form_respected_and_old_nonadaptive_behavior_kept(store):
    assert {q.kind for q in build(store, kinds=("kana_romaji",))} == {"kana_romaji"}
    legacy = build_practice_batch(SmallContent(), store, kinds=KANA[:4], now=START, limit=2)
    assert len(legacy) == 8
    with pytest.raises(ValueError):
        build_practice_batch(SmallContent(), store, kinds=ALL, now=START)
    with pytest.raises(ValueError):
        build_practice_batch(SmallContent(), store, kinds=KANA, script="both", now=START)


@pytest.mark.parametrize("kwargs", [{"script": "unknown"}, {"kinds": ["invalid"]},
    {"rounds": 0}, {"schedule_mode": "bad"}, {"adaptive": 1}, {"audio_available": "yes"}])
def test_invalid_adaptive_configuration_rejected(store, kwargs):
    with pytest.raises(ValueError):
        build(store, **kwargs)
