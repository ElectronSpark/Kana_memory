"""Words supply spelling contexts, never independently scheduled memory items."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from record_test_support import decode_current, encode_current, as_legacy
from fsrs import Card, Rating

from kanamemory.domain import (ALGORITHM_VERSION, Question, StudyStore, WORD_SKILLS,
    _adaptive_stages, build_questions, build_practice_batch, practice_plan_counts)
from kanamemory.word_spelling import analyze_spelling
from kanamemory.progress_backup import convert_legacy_backup


from support.word_context_memory_support import NOW, A, KA, KI, KU, ContextContent, word_question, begin, answer_word


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", "UTC") as value:
        yield value


@pytest.fixture
def repo(): return ContextContent()


def raw_tables(store):
    names = ("study_card", "review_event", "study_session", "session_question", "session_answer", "session_checkpoint", "word_spelling_attempt")
    return {name: [dict(row) for row in store._db.execute("SELECT * FROM " + name)] for name in names}


def seed_legacy_word(store, repo, *, q=None, when=NOW - timedelta(days=1)):
    """Build a faithful previous-release record without calling the new API."""
    q = q or word_question()
    sid = store.start_session([q], now=when)
    key = store._card_key(q.content_id, q.kind, q.script)
    initial = Card(card_id=int(key[:15], 16), due=when)
    updated, log = store.scheduler.review_card(initial, Rating.Again, review_datetime=when)
    proof = analyze_spelling(q.answer, "かく", q.script, repo.kana_list())
    proof.update(source_content_id=q.content_id, source_kind=q.kind, source_question_index=0)
    record = json.loads(log.to_json())
    record["word_spelling_evidence"] = proof
    event_id = str(uuid4())
    with store._db:
        store._db.execute("INSERT INTO study_card VALUES (?,?,?,?,?,?,?,?,?,?,?)", (key,
            q.content_id, "fixture", q.kind, q.script, updated.to_json(), ALGORITHM_VERSION,
            updated.due.isoformat(), when.isoformat(), 1, 0))
        store._db.execute("INSERT INTO review_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            event_id, sid, key, when.isoformat(), 1, "かく", 0, "incorrect", 100,
            initial.to_json(), updated.to_json(), ALGORITHM_VERSION, json.dumps(record)))
        store._db.execute("INSERT INTO session_answer VALUES (?,?,?,?)", (sid, 0, event_id, 0))
    return sid, q, store.get_card(q.content_id, q.kind, q.script)


def test_word_submission_preserves_legacy_cards_events_and_does_not_schedule_words(store, repo):
    _, old, old_card = seed_legacy_word(store, repo)
    store.record_review(KA, "kana_choice", "hiragana", 4, now=NOW, outcome="correct")
    sid, q = begin(store)
    before = raw_tables(store)
    result = answer_word(store, repo, sid, q, answer="かく")
    after = raw_tables(store)
    assert result["scheduled"] and result["card"] is None and result["due_at_utc"] is None
    assert before["study_card"] == after["study_card"]
    assert before["review_event"] == after["review_event"]
    assert len(after["word_spelling_attempt"]) == 1
    assert store.get_card(old.content_id, old.kind, old.script) == old_card
    assert store.get_due(skill=old.kind, now=NOW + timedelta(days=100)) == []
    assert all(card["skill"] not in WORD_SKILLS for card in store.get_due(now=NOW + timedelta(days=100)))
    assert store.next_due_at(old.content_id, now=NOW) is None
    stats = store.stats(NOW)
    assert stats["total_cards"] == stats["total_items"] == stats["total_reviews"] == 1
    assert stats["word_exercises_today"] == 1
    assert old.content_id not in _adaptive_stages(store, "hiragana", NOW)


def test_auxiliary_completion_idempotency_checkpoint_and_backup_union(store, repo, tmp_path):
    tail = Question(A, "kana_choice", "hiragana", "あ", "a", ["a", "ka"])
    sid, q = begin(store, tail=[tail])
    first = answer_word(store, repo, sid, q)
    before = raw_tables(store)
    assert answer_word(store, repo, sid, q)["already_recorded"] is True
    assert raw_tables(store) == before
    store.save_session_progress(sid, 0)
    pending = store.load_session_progress()
    assert pending["question_index"] == pending["session_right"] == 1
    summary = store.get_session_summary(sid)
    assert (summary["answered"], summary["correct"], summary["content_count"], summary["auxiliary_count"]) == (1, 1, 0, 1)
    backup = store.export_backup()
    assert decode_current(backup)["version"] == 1
    with StudyStore(tmp_path / "restored", "UTC") as restored:
        restored.import_backup(backup)
        assert restored.load_session_progress()["question_index"] == 1
        assert restored.word_spelling_events(now=NOW) == store.word_spelling_events(now=NOW)
        restored.record_review(A, tail.kind, tail.script, 3, session_id=sid, question_index=1,
            answered_text="a", outcome="correct", now=NOW + timedelta(seconds=2))
        final = restored.get_session_summary(sid)
        assert (final["answered"], final["correct"], final["content_count"]) == (2, 2, 1)
        assert restored.load_session_progress() is None
        assert first["attempt_id"] not in {event["event_id"] for event in restored.review_history()}


@pytest.mark.parametrize("mutation", [
    lambda p: p["tables"].pop("word_spelling_attempt"),
    lambda p: p["tables"]["word_spelling_attempt"][0].update(content_id="different"),
    lambda p: p["tables"]["word_spelling_attempt"][0].update(skill="kana_choice"),
    lambda p: p["tables"]["word_spelling_attempt"][0].update(script="katakana"),
    lambda p: p["tables"]["word_spelling_attempt"][0].update(question_index=99),
    lambda p: p["tables"]["word_spelling_attempt"][0].update(correct=0),
    lambda p: p["tables"]["word_spelling_attempt"][0].update(assisted=1),
    lambda p: p["tables"]["word_spelling_attempt"][0].update(evidence_json="{}"),
    lambda p: p["tables"]["word_spelling_attempt"].append(dict(p["tables"]["word_spelling_attempt"][0])),
])
def test_invalid_current_attempt_backup_is_rejected_without_mutating_learner(store, repo, mutation):
    sid, q = begin(store)
    answer_word(store, repo, sid, q)
    payload = decode_current(store.export_backup())
    before = raw_tables(store)
    mutation(payload)
    with pytest.raises(ValueError):
        store.import_backup(encode_current(payload))
    assert raw_tables(store) == before


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_backup_restores_unchanged_word_history_but_only_kana_evidence(store, repo, tmp_path, version):
    _, q, old_card = seed_legacy_word(store, repo)
    payload = as_legacy(decode_current(store.export_backup()), version=version)
    assert payload["version"] == version and "word_spelling_attempt" not in payload["tables"]
    with StudyStore(tmp_path / ("old-" + str(version)), "UTC") as restored:
        converted = convert_legacy_backup(restored, json.dumps(payload).encode())
        info = restored.import_backup(converted)
        assert info["cards"] == info["reviews"] == 0
        assert restored.get_card(q.content_id, q.kind, q.script) == old_card
        assert restored._db.execute("SELECT COUNT(*) FROM word_spelling_attempt").fetchone()[0] == 0
        assert restored.get_due(now=NOW) == []
        assert _adaptive_stages(restored, "hiragana", NOW) == {KA: 0, KI: 0}
        assert len(restored.word_spelling_events(now=NOW)) == 2


def test_legacy_timed_word_repeats_stay_retired_while_ungraded_word_is_kept(store, repo):
    _, q, old_card = seed_legacy_word(store, repo)
    timed = replace(q, metadata={**q.metadata, "not_before_utc": old_card["due_at_utc"], "relearning_of_index": 0})
    ordinary = replace(q, content_id="word-a", answer="あ")
    kana = Question(KU, "kana_choice", "hiragana", "く", "ku", ["ku"])
    sid = store.start_session([timed, ordinary, kana], now=NOW)
    store.save_session_progress(sid, 0)
    revised = store.reconcile_session_kinds(sid, [q.kind, kana.kind], now=NOW)
    assert revised["question_index"] == 1
    assert revised["questions"][1].content_id == "word-a"
    reopened = store.reconcile_session_kinds(sid, [q.kind, kana.kind], now=NOW)
    assert reopened["restored_count"] == 0
    assert store.get_card(q.content_id, q.kind, q.script) == old_card
    assert store.prepare_next_question(sid, now=NOW)["waiting_until_utc"] is None


def test_context_selection_uses_due_kana_and_reserves_queued_words(store, repo):
    store.record_review(KI, "kana_choice", "hiragana", 1, now=NOW - timedelta(minutes=5))
    selected = build_questions(repo, store, kind="word_meaning_kana", limit=1, now=NOW, seed=5)
    assert selected[0].content_id == "word-ka"
    sid = store.start_session(selected, now=NOW)
    store.save_session_progress(sid, 0)
    following = build_questions(repo, store, kind="word_meaning_kana", limit=4, now=NOW, seed=5)
    assert "word-ka" not in {q.content_id for q in following}
    assert store.get_card("word-ka", "word_meaning_kana", "hiragana") is None


def test_old_word_card_never_prevents_reusing_a_word_example(store, repo):
    _, q, old = seed_legacy_word(store, repo)
    with store._db:
        store._db.execute("UPDATE study_card SET suspended=1")
    for new_only in (False, True):
        words = build_questions(repo, store, kind=q.kind, limit=4, seed=3, now=NOW, new_only=new_only)
        assert q.content_id in {word.content_id for word in words}


@pytest.mark.parametrize("script", ["hiragana", "katakana", "both"])
def test_mixed_batch_word_contexts_are_extra_and_cover_selected_kana(store, repo, script):
    options = dict(kinds=("kana_choice", "word_meaning_kana"), script=script, limit=2,
        adaptive=True, schedule_mode="spaced", seed=27, now=NOW)
    questions = build_practice_batch(repo, store, **options)
    memory = {q.content_id for q in questions if q.kind not in WORD_SKILLS}
    contexts = [q for q in questions if q.kind in WORD_SKILLS]
    assert len(memory) == 2 and contexts
    assert all(set(q.metadata["context_kana_ids"]) & memory for q in contexts)
    counts = store.batch_counts(questions, now=NOW)
    assert counts["content_count"] == counts["new"] == 2
    assert counts["auxiliary_count"] == len(contexts)
    assert counts["total"] > counts["content_count"]
    preview = practice_plan_counts(repo, store, **options)
    assert all(preview[key] == value for key, value in counts.items())


def test_word_only_batch_has_no_memory_item_count_and_never_forces_other_kinds(store, repo):
    options = dict(kinds=("word_meaning_kana",), limit=3, seed=2, now=NOW,
                   adaptive=True, schedule_mode="spaced")
    questions = build_practice_batch(repo, store, **options)
    assert len(questions) == 3 and {q.kind for q in questions} == {"word_meaning_kana"}
    counts = store.batch_counts(questions, now=NOW)
    assert counts == {"content_count": 0, "total": 3, "due": 0, "new": 0,
                      "auxiliary_count": 3, "word_count": 3}
    assert all(len(option) == 1 for q in questions for option in q.options)


def test_clear_learning_data_deletes_auxiliary_attempts_but_keeps_preferences(store, repo):
    sid, q = begin(store)
    answer_word(store, repo, sid, q)
    store.set_setting("practice_preferences", {"kind": "words"})
    summary = store.clear_learning_data()
    assert summary["deleted_rows"]["word_spelling_attempt"] == 1
    assert store.word_spelling_events(now=NOW) == []
    assert store.get_setting("practice_preferences") == {"kind": "words"}
    assert store._db.execute("PRAGMA foreign_key_check").fetchall() == []
