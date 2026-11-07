"""Coordinated content queues retain per-direction evidence and timed learning."""
from record_test_support import decode_current, encode_current
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from kanamemory.domain import Question, StudyStore, build_practice_batch
from kanamemory.progress_backup import validate_content_controls
from support.domain_support import SmallContent


START = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
KINDS = ("kana_choice", "kana_romaji", "romaji_kana")


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as value:
        yield value


def question(content_id="a", kind="kana_choice", mode="spaced"):
    return Question(content_id, kind, "hiragana", "あ", "a", ["a", "i", "u", "e"],
                    metadata={"audio_only": False, "practice_config": {"schedule_mode": mode}})


def begin(store, questions):
    sid = store.start_session(questions, now=START)
    store.save_session_progress(sid, 0)
    return sid


def rate(store, sid, index, rating, when=START):
    item = store.get_session_questions(sid)[index]
    return store.record_review(item.content_id, item.kind, item.script, rating,
        session_id=sid, question_index=index, now=when,
        outcome="incorrect" if rating == 1 else "correct", answered_text=item.answer)


def test_spaced_only_due_or_unseen_directions_without_promoting_other_fsrs(store):
    repo = SmallContent()
    store.record_review("kana-0", "kana_choice", "hiragana", 4, now=START)
    store.record_review("kana-0", "kana_romaji", "hiragana", 1, now=START)
    before = store.get_card("kana-0", "kana_choice", "hiragana")
    questions = build_practice_batch(repo, store, kinds=KINDS, limit=1,
        schedule_mode="spaced", seed=3, now=START + timedelta(minutes=2))
    assert {q.content_id for q in questions} == {"kana-0"}
    assert [q.kind for q in questions] == ["kana_romaji", "romaji_kana"]
    assert store.get_card("kana-0", "kana_choice", "hiragana") == before
    reinforced = build_practice_batch(repo, store, kinds=KINDS, limit=1,
        seed=3, now=START + timedelta(minutes=2))
    assert {q.kind for q in reinforced} == set(KINDS)


def test_spaced_new_items_are_interleaved_and_have_more_questions_than_items(store):
    questions = build_practice_batch(SmallContent(), store, kinds=KINDS, limit=10,
        schedule_mode="spaced", seed=9, now=START)
    # KINDS span two shared abilities per kana: choice/typed reading and spelling (2c44345).
    assert len(questions) == 20
    assert len({q.content_id for q in questions}) == 10
    assert all(a.content_id != b.content_id for a, b in zip(questions, questions[1:]))
    assert questions == build_practice_batch(SmallContent(), store, kinds=KINDS, limit=10,
        schedule_mode="spaced", seed=9, now=START)
    with pytest.raises(ValueError):
        build_practice_batch(SmallContent(), store, kinds=KINDS, schedule_mode="spaced", rounds=2)


@pytest.mark.parametrize("rating,minutes,extended", [(1, 1, True), (2, 5.5, True), (3, 10, True), (4, None, False)])
def test_learning_steps_append_once_and_keep_original_options(store, rating, minutes, extended):
    sid = begin(store, [question()])
    result = rate(store, sid, 0, rating)
    assert result["queue_extended"] is extended
    questions = store.get_session_questions(sid)
    assert len(questions) == (2 if extended else 1)
    rate(store, sid, 0, rating)
    assert len(store.get_session_questions(sid)) == len(questions)
    assert store.stats(START)["total_reviews"] == 1
    if extended:
        repeat = questions[1]
        assert repeat.options == questions[0].options
        assert repeat.metadata["not_before_utc"] == (START + timedelta(minutes=minutes)).isoformat()
        assert repeat.metadata["relearning_of_index"] == 0
        assert repeat.metadata["relearning_count"] == 1
        pending = store.load_session_progress()
        assert pending["question_index"] == 1
        with pytest.raises(ValueError, match="not due"):
            rate(store, sid, 1, 3)
        assert store.stats(START)["total_reviews"] == 1
    else:
        assert store.load_session_progress() is None


def test_relearning_a_mature_card_appends_real_ten_minute_step(store):
    store.record_review("a", "kana_choice", "hiragana", 4, now=START - timedelta(days=20))
    sid = begin(store, [question()])
    result = rate(store, sid, 0, 1)
    assert result["queue_extended"]
    assert result["card"]["scheduler_state"]["state"] == 3
    assert store.get_session_questions(sid)[1].metadata["not_before_utc"] == (START + timedelta(minutes=10)).isoformat()


def test_reinforcement_and_technical_failure_do_not_append(store):
    sid = begin(store, [question(mode="reinforce")])
    assert not rate(store, sid, 0, 1)["queue_extended"]
    assert len(store.get_session_questions(sid)) == 1
    sid2 = begin(store, [question("b")])
    result = store.record_review("b", "kana_choice", "hiragana", None, session_id=sid2,
                                question_index=0, audio_ok=False, now=START)
    assert not result["scheduled"]
    assert len(store.get_session_questions(sid2)) == 1


def test_append_and_grade_rollback_together_on_snapshot_failure(store):
    sid = begin(store, [question()])
    store._db.execute("CREATE TRIGGER reject_queue BEFORE INSERT ON session_question BEGIN SELECT RAISE(ABORT,'test queue failure'); END")
    with pytest.raises(Exception, match="test queue failure"):
        rate(store, sid, 0, 1)
    assert store.get_card("a", "kana_choice", "hiragana") is None
    assert store.stats(START)["total_reviews"] == 0
    assert len(store.get_session_questions(sid)) == 1
    assert store.load_session_progress()["question_index"] == 0


def test_waiting_queue_selects_earlier_repetition_and_survives_backup(store, tmp_path):
    sid = begin(store, [question("a"), question("b")])
    rate(store, sid, 0, 3)
    rate(store, sid, 1, 1)
    pending = store.prepare_next_question(sid, now=START)
    assert [q.content_id for q in pending["questions"]] == ["a", "b", "b", "a"]
    assert pending["question_index"] == 2
    assert pending["waiting_until_utc"] == (START + timedelta(minutes=1)).isoformat()
    assert pending["state"] == {"page": "session_wait"}
    with StudyStore(tmp_path / "restored") as restored:
        restored.import_backup(store.export_backup())
        assert restored.get_session_questions(sid) == pending["questions"]
        ready = restored.prepare_next_question(sid, now=START + timedelta(minutes=1))
        assert ready["waiting_until_utc"] is None
        rate(restored, sid, 2, 4, START + timedelta(minutes=1))
        waiting = restored.prepare_next_question(sid, now=START + timedelta(minutes=2))
        assert waiting["question_index"] == 3
        assert waiting["waiting_until_utc"] == (START + timedelta(minutes=10)).isoformat()
        rate(restored, sid, 3, 3, START + timedelta(minutes=10))
        assert restored.prepare_next_question(sid, now=START + timedelta(minutes=10)) is None
        assert restored.stats(START)["total_reviews"] == 4


def test_visible_partial_answer_is_not_reordered(store):
    sid = begin(store, [question("a"), question("b")])
    store.save_session_progress(sid, 0, state={"page": "question", "romaji_answer": "partial"})
    pending = store.prepare_next_question(sid, now=START)
    assert pending["state"]["romaji_answer"] == "partial"
    assert pending["questions"][0].content_id == "a"
    store.set_content_control("a", "suspend", now=START)
    pending = store.prepare_next_question(sid, now=START)
    assert pending["question_index"] == 1
    assert pending["questions"][1].content_id == "b"
    assert pending["state"] == {}
    assert store.stats(START)["total_reviews"] == 0


def test_due_minute_repetition_is_not_starved_behind_new_items(store):
    sid = begin(store, [question("a"), question("b"), question("c"), question("d")])
    rate(store, sid, 0, 1)
    rate(store, sid, 1, 4, START + timedelta(seconds=30))
    pending = store.prepare_next_question(sid, now=START + timedelta(minutes=1))
    assert pending["question_index"] == 2
    assert pending["questions"][2].content_id == "a"
    assert [q.content_id for q in pending["questions"][:2]] == ["a", "b"]
    assert pending["waiting_until_utc"] is None
    assert store.get_session_summary(sid)["answered"] == 2


def test_due_repetition_spaced_from_same_content_when_another_item_is_ready(store):
    # The later sibling is a separate ability; kana_romaji would be this same
    # due reading ability under another mode since 2c44345.
    sid = begin(store, [question("a"), question("b"), question("a", "romaji_kana")])
    rate(store, sid, 0, 1)
    pending = store.prepare_next_question(sid, now=START + timedelta(minutes=1))
    assert [q.content_id for q in pending["questions"][:3]] == ["a", "b", "a"]
    assert pending["questions"][2].kind == "kana_choice"


def test_all_paused_pending_items_complete_without_grading(store):
    sid = begin(store, [question("a"), question("a", "kana_romaji")])
    store.set_content_control("a", "suspend", now=START)
    assert store.prepare_next_question(sid, now=START) is None
    assert store.load_session_progress() is None
    assert store.stats(START)["total_reviews"] == 0
    assert store.get_session_summary(sid) == {"answered": 0, "correct": 0,
        "content_count": 0, "total": 2, "skipped": 2, "auxiliary_count": 0}


def test_completed_session_summary_counts_only_actual_answers(store):
    sid = begin(store, [question("a"), question("b")])
    rate(store, sid, 0, 4)
    store.set_content_control("b", "bury", now=START)
    assert store.prepare_next_question(sid, now=START) is None
    assert store.get_session_summary(sid) == {"answered": 1, "correct": 1,
        "content_count": 1, "total": 2, "skipped": 1, "auxiliary_count": 0}


def test_content_pause_applies_to_all_scripts_and_new_directions(store):
    for kind, script in [("kana_choice", "hiragana"), ("kana_romaji", "katakana")]:
        store.record_review("kana-0", kind, script, 1, now=START)
    original = store.review_history()
    store.set_content_control("kana-0", "suspend", now=START)
    later = START + timedelta(minutes=2)
    assert store.get_due(now=later) == []
    assert store.due_groups(now=later) == []
    assert store.select_content_ids(["kana-0", "new"], "romaji_kana", "hiragana", now=later) == ["new"]
    for mode in ("reinforce", "spaced"):
        assert "kana-0" not in {q.content_id for q in build_practice_batch(SmallContent(), store,
            kinds=KINDS, schedule_mode=mode, limit=100, now=later)}
    with pytest.raises(ValueError, match="paused"):
        store.record_review("kana-0", "kana_choice", "hiragana", 1, now=later)
    assert store.review_history() == original
    store.set_content_control("kana-0", "resume", now=later)
    assert len(store.get_due(now=later)) == 2


def test_bury_expires_at_local_midnight_without_changing_fsrs(tmp_path):
    japan = timezone(timedelta(hours=9))
    with StudyStore(tmp_path / "japan", time_zone=japan) as store:
        original = store.record_review("a", "kana_choice", "hiragana", 1, now=START)["card"]
        control = store.set_content_control("a", "bury", now=START)
        midnight = START.replace(hour=15)
        assert control["buried_until_utc"] == midnight.isoformat()
        assert not store.is_content_available("a", START + timedelta(hours=2))
        assert store.is_content_available("a", midnight)
        assert store.next_due_at("a", now=START) == midnight.isoformat()
        assert store.get_card("a", "kana_choice", "hiragana") == original
        store.set_content_control("a", "unbury", now=START)
        assert store.is_content_available("a", START)


def test_resume_revives_legacy_suspended_cards_but_keeps_fsrs(store):
    original = store.record_review("a", "kana_choice", "hiragana", 1, now=START)["card"]
    with store._db:
        store._db.execute("UPDATE study_card SET suspended=1")
    store.set_content_control("a", "resume", now=START)
    assert store.get_card("a", "kana_choice", "hiragana") == original


def test_legacy_direction_pause_skips_only_that_unfinished_direction(store):
    store.record_review("a", "kana_choice", "hiragana", 1, now=START)
    with store._db:
        store._db.execute("UPDATE study_card SET suspended=1")
    sid = begin(store, [question("a"), question("a", "kana_romaji")])
    store.save_session_progress(sid, 0, state={"page": "feedback", "feedback_pending": True})
    pending = store.prepare_next_question(sid, now=START)
    assert pending["question_index"] == 1
    assert pending["questions"][1].kind == "kana_romaji"
    assert pending["state"] == {}
    with pytest.raises(ValueError, match="paused"):
        rate(store, sid, 0, 1)
    assert store.stats(START)["total_reviews"] == 1


@pytest.mark.parametrize("controls", [[], {"": {"suspended": False, "buried_until_utc": None}},
    {"a": {"suspended": 1, "buried_until_utc": None}}, {"a": {"suspended": False}},
    {"a": {"suspended": False, "buried_until_utc": "2026-10-13T00:00:00"}},
    {"a": {"suspended": False, "buried_until_utc": 5}}])
def test_invalid_controls_are_rejected_before_live_mutation(store, controls):
    with pytest.raises(ValueError):
        validate_content_controls(controls)
    with pytest.raises(ValueError):
        store.set_setting("study_item_controls_v1", controls)
    assert store.get_content_control("a") == {"suspended": False, "buried_until_utc": None}


def test_controls_v1_backup_round_trip_and_missing_setting_compatibility(store, tmp_path):
    store.set_content_control("a", "suspend", now=START)
    store.set_content_control("b", "bury", now=START)
    with StudyStore(tmp_path / "restored") as restored:
        restored.import_backup(store.export_backup())
        assert restored.get_content_control("a") == store.get_content_control("a")
        assert restored.get_content_control("b") == store.get_content_control("b")
        old = decode_current(store.export_backup())
        old["tables"]["app_setting"] = [r for r in old["tables"]["app_setting"] if r["key"] != "study_item_controls_v1"]
        restored.import_backup(encode_current(old))
        assert restored.is_content_available("a", START)
        assert restored.is_content_available("b", START)


def test_bad_controls_in_backup_rejected_without_overwrite(store):
    store.set_content_control("a", "suspend", now=START)
    payload = decode_current(store.export_backup())
    row = next(row for row in payload["tables"]["app_setting"] if row["key"] == "study_item_controls_v1")
    row["value_json"] = json.dumps({"a": {"suspended": "no", "buried_until_utc": None}})
    with pytest.raises(ValueError):
        store.import_backup(encode_current(payload))
    assert store.get_content_control("a")["suspended"] is True


def test_content_statistics_count_three_directions_once_and_preserve_legacy_keys(store):
    for kind in KINDS:
        store.record_review("a", kind, "hiragana", 1, now=START)
    stats = store.stats(START + timedelta(minutes=2))
    assert stats["total_cards"] == stats["new_today"] == stats["due"] == 3
    assert stats["total_items"] == stats["new_items_today"] == stats["due_items"] == 1
    tomorrow = START + timedelta(days=1)
    store.record_review("a", "kana_handwriting", "katakana", 1, now=tomorrow)
    stats = store.stats(tomorrow + timedelta(minutes=2))
    assert stats["new_today"] == 1
    assert stats["new_items_today"] == 0
    assert stats["due_items"] == stats["total_items"] == 1
    store.set_content_control("a", "suspend", now=tomorrow)
    assert store.stats(tomorrow + timedelta(minutes=2))["due_items"] == 0
