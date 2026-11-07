"""Separate auxiliary queues and quarter-strength kana evidence survive restore."""
from runtime_record_test_support import snapshot_json, write_snapshot
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest
from record_test_support import decode_current, encode_current

from kanamemory.domain import (
    Question, StudyStore, WORD_ROMAJI_POLICY, _adaptive_stages,
    prepare_session_snapshot,
)

from support.word_romaji_lanes_support import NOW, KA, KI, ROWS, question, main_question, begin, submit


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", "UTC") as value:
        yield value


def checkpoints(store):
    return [dict(row) for row in store._db.execute("SELECT * FROM session_checkpoint ORDER BY session_id")]


def test_lanes_keep_drafts_wait_times_and_original_pending_reservations(store):
    main = begin(store, [main_question(due=NOW + timedelta(minutes=5))],
                 {"page": "session_wait"})
    original = checkpoints(store)
    drill = begin(store, [question(), question("word:two")], {"word_answer": "か"})
    assert store.practice_lane == "main"
    assert store.load_session_progress()["session_id"] == main
    assert store.load_session_progress("word_romaji")["session_id"] == drill
    assert [r for r in checkpoints(store) if r["session_id"] == main] == original
    assert store.queued_content_ids() == {KA}
    assert store.queued_content_ids("word_romaji") == {"word:one", "word:two"}
    assert store.prepare_next_question(drill, now=NOW) is None
    store.practice_lane = "word_romaji"
    assert store.prepare_next_question(drill, now=NOW)["state"]["word_answer"] == "か"
    assert store.load_session_progress("main")["questions"][0].metadata["not_before_utc"] == (NOW + timedelta(minutes=5)).isoformat()


def test_save_completion_replace_and_clear_only_the_matching_lane(store):
    main = begin(store, [main_question()])
    first = begin(store, [question()])
    second = begin(store, [question("word:two")])
    assert {r["session_id"] for r in checkpoints(store)} == {main, second}
    assert first not in {r["session_id"] for r in checkpoints(store)}
    store.save_session_progress(second, 1)
    assert [r["session_id"] for r in checkpoints(store)] == [main]
    begin(store, [question()])
    store.clear_session_progress("main")
    assert store.load_session_progress() is None
    assert store.load_session_progress("word_romaji") is not None
    store.practice_lane = "word_romaji"
    store.clear_session_progress()
    assert checkpoints(store) == []


def test_other_lane_cannot_invalidate_append_token_and_cross_lane_append_rejected(store):
    main = begin(store, [main_question()])
    token = store.session_append_token(main)
    begin(store, [question()])
    assert store.session_append_token(main) == token
    store.append_session(main, [main_question(KI)], expected_token=token)
    before = store._session_snapshot(main)
    with pytest.raises(ValueError, match="mix"):
        store.append_session(main, [question("word:two")],
                             expected_token=store.session_append_token(main))
    assert store._session_snapshot(main) == before
    assert len(checkpoints(store)) == 2


@pytest.mark.parametrize("questions", [
    [main_question(), question()],
    [replace(question(), kind="word_kana")],
    [replace(question(), metadata={"word_romaji_drill": "yes"})],
    [replace(question(), metadata={"word_romaji_drill": True, "not_before_utc": NOW.isoformat()})],
])
def test_invalid_lane_batches_rejected_before_any_write(store, questions):
    with pytest.raises(ValueError):
        prepare_session_snapshot(questions)
    assert store._db.execute("SELECT COUNT(*) FROM study_session").fetchone()[0] == 0


def test_drill_reconcile_and_completion_do_not_touch_main_checkpoint(store):
    main = begin(store, [main_question()], {"romaji_answer": "k"})
    original = checkpoints(store)
    drill = begin(store, [question()])
    store.practice_lane = "word_romaji"
    assert not store.reconcile_session_kinds(drill, ["kana_choice"], now=NOW)["completed"]
    assert store.prepare_next_question(drill, now=NOW)["settings_blocked"]
    assert store.load_session_progress("main")["session_id"] == main
    enabled = store.reconcile_session_kinds(drill, ["word_meaning_kana"], now=NOW)
    q = enabled["questions"][enabled["question_index"]]
    store.record_review(q.content_id, q.kind, q.script, None, session_id=drill,
        question_index=enabled["question_index"], answered_text=q.answer,
        outcome="correct", kana_rows=ROWS, now=NOW)
    assert checkpoints(store) == original
    assert store.load_session_progress("main")["session_id"] == main


def test_repeated_draft_saves_do_not_decode_the_other_queue(store, monkeypatch):
    main = begin(store, [main_question()])
    drill = begin(store, [question()])
    store.load_session_progress("main")
    store.load_session_progress("word_romaji")
    original = store._session_snapshot
    decoded = []
    def snapshot(sid):
        decoded.append(sid)
        return original(sid)
    monkeypatch.setattr(store, "_session_snapshot", snapshot)
    for answer in ("か", "かき", "か"):
        store.save_session_progress(drill, 0, state={"word_answer": answer})
    assert main not in decoded
    assert store.load_session_progress("word_romaji")["state"]["word_answer"] == "か"


def test_snapshot_lane_cache_invalidates_when_snapshot_changes(store):
    q = question(drill=False)
    sid = begin(store, [q])
    assert store.load_session_progress()["session_id"] == sid
    with store._db:
        write_snapshot(store._db, sid, json.dumps([question().to_dict()]))
    assert store.load_session_progress() is None
    assert store.load_session_progress("word_romaji")["session_id"] == sid


def test_quarter_credit_requires_eight_successes_and_cross_day_for_writing(store):
    for index in range(7):
        submit(store, question(answer="かか"), now=NOW + timedelta(minutes=index))
    assert _adaptive_stages(store, "hiragana", NOW + timedelta(hours=1))[KA] == 0
    submit(store, question(answer="かか"), now=NOW + timedelta(minutes=7))
    assert _adaptive_stages(store, "hiragana", NOW + timedelta(hours=1))[KA] == 1
    submit(store, question(answer="かか"), now=NOW + timedelta(days=1))
    assert _adaptive_stages(store, "hiragana", NOW + timedelta(days=1))[KA] == 2
    assert _adaptive_stages(store, "katakana", NOW + timedelta(days=1)) == {}
    events = store.word_spelling_events(now=NOW + timedelta(days=1))
    assert len(events) == 9 and {e["weight"] for e in events} == {0.25}
    assert store.get_card(KA, "kana_choice", "hiragana") is None


def test_single_context_error_subtracts_quarter_then_one_success_restores_credit(store):
    for day in (0, 1):
        store.record_review(KA, "kana_choice", "hiragana", 3,
                            outcome="correct", now=NOW + timedelta(days=day))
    before = store.get_card(KA, "kana_choice", "hiragana")
    submit(store, question(answer="か"), answer="き", now=NOW + timedelta(days=2))
    assert _adaptive_stages(store, "hiragana", NOW + timedelta(days=2))[KA] == 0
    shortened = store.get_card(KA, "kana_choice", "hiragana")
    assert shortened["due_at_utc"] < before["due_at_utc"]
    submit(store, question(answer="か"), now=NOW + timedelta(days=2, minutes=1))
    assert _adaptive_stages(store, "hiragana", NOW + timedelta(days=2, minutes=1))[KA] == 2
    # Repeating this spelling may improve the lightweight skill evidence, but
    # it cannot apply another interval change before a real independent review.
    assert store.get_card(KA, "kana_choice", "hiragana") == shortened


def test_legacy_word_evidence_retains_full_weight_and_error_reset(store):
    for day in (0, 1):
        submit(store, question(drill=False), now=NOW + timedelta(days=day))
    assert _adaptive_stages(store, "hiragana", NOW + timedelta(days=1)) == {KA: 2, KI: 2}
    submit(store, question(drill=False), answer="かか", now=NOW + timedelta(days=2))
    assert _adaptive_stages(store, "hiragana", NOW + timedelta(days=2))[KI] == 0
    assert {e["weight"] for e in store.word_spelling_events(now=NOW + timedelta(days=2))} == {1}


def test_weighted_weakness_accumulates_and_recovers_without_changing_raw_counts(store):
    for index in range(4):
        submit(store, question(answer="か"), answer="き", now=NOW + timedelta(minutes=index))
        summary = store.word_spelling_summary(KA, now=NOW + timedelta(minutes=index))[0]
        assert summary["weak_weight"] == (index + 1) * 0.25
    submit(store, question(answer="か"), now=NOW + timedelta(minutes=4))
    summary = store.word_spelling_summary(KA, now=NOW + timedelta(minutes=4))[0]
    assert summary["weighted_success"] == 0.25
    assert summary["weighted_failure"] == 1
    assert summary["weak_weight"] == 0.75
    assert summary["success_count"] == 1 and summary["failure_count"] == 4
    assert summary["event_count"] == 5 and summary["successes_since_error"] == 1
    assert summary["weak"] is False  # Existing UI still sees the actual last result.
    submit(store, question(answer="か", drill=False), now=NOW + timedelta(minutes=5))
    summary = store.word_spelling_summary(KA, now=NOW + timedelta(minutes=5))[0]
    assert summary["weak_weight"] == 0 and summary["weighted_success"] == 1.25


def test_one_legacy_failure_equals_four_drill_failures_and_weight_never_exceeds_one(store):
    submit(store, question(answer="か", drill=False), answer="き")
    summary = store.word_spelling_summary(KA, now=NOW)[0]
    assert summary["weak_weight"] == summary["weighted_failure"] == 1
    for index in range(6):
        submit(store, question(answer="か"), answer="き", now=NOW + timedelta(minutes=index + 1))
    summary = store.word_spelling_summary(KA, now=NOW + timedelta(hours=1))[0]
    assert summary["weak_weight"] == 1 and summary["weighted_failure"] == 2.5
    assert summary["failure_count"] == 7


def test_ordinary_context_selector_uses_quarter_weight_without_displacing_due_priority(store):
    submit(store, question(answer="か"), answer="き")
    submit(store, question(answer="き", drill=False), answer="か", now=NOW + timedelta(minutes=1))
    contexts = {"low": [KA], "full": [KI]}
    for seed in range(5):
        assert store.select_word_context_ids(["low", "full"], contexts, "hiragana", 2,
            seed, NOW + timedelta(minutes=2)) == ["full", "low"]
    store.record_review(KA, "kana_choice", "hiragana", 1, outcome="incorrect", now=NOW)
    assert store.select_word_context_ids(["low", "full"], contexts, "hiragana", 2,
        4, NOW + timedelta(hours=1)) == ["low", "full"]


def test_quarter_success_offsets_one_quarter_error_and_other_script_stays_clear(store):
    submit(store, question(answer="か"), answer="き")
    submit(store, question(answer="カ", script="katakana"), now=NOW + timedelta(minutes=1))
    summary = {s["script"]: s for s in store.word_spelling_summary(KA, now=NOW + timedelta(minutes=1))}
    assert summary["hiragana"]["weak_weight"] == 0.25
    assert summary["katakana"]["weak_weight"] == 0
    submit(store, question(answer="か"), now=NOW + timedelta(minutes=2))
    summary = {s["script"]: s for s in store.word_spelling_summary(KA, now=NOW + timedelta(minutes=2))}
    assert summary["hiragana"]["weak_weight"] == 0


def test_hint_empty_and_wrong_then_hint_do_not_invent_credit(store):
    submit(store, assisted=True)
    submit(store, answer="", now=NOW + timedelta(minutes=1))
    assert store.word_spelling_events(now=NOW + timedelta(minutes=2)) == []
    submit(store, answer="きき", now=NOW + timedelta(minutes=2))
    assert _adaptive_stages(store, "hiragana", NOW + timedelta(minutes=2)) == {KA: 0, KI: 0}


def test_drill_save_ignores_guided_again_and_only_adjusts_mature_due(store):
    store.record_review(KI, "romaji_kana", "hiragana", 4,
                        outcome="correct", now=NOW - timedelta(days=1))
    before = store.get_card(KI, "romaji_kana", "hiragana")
    correction = replace(main_question(KI), kind="romaji_kana", answer="き",
        metadata={"spelling_correction_of": "word:one", "spelling_correction_index": 0})
    result = submit(store, answer="かか", corrections=[correction])
    assert result["correction_count"] == 0 and not result["queue_extended"]
    assert result["word_spelling_evidence"]["practice_policy"] == WORD_ROMAJI_POLICY
    after = store.get_card(KI, "romaji_kana", "hiragana")
    assert after["due_at_utc"] < before["due_at_utc"]
    assert after["review_count"] == before["review_count"]
    assert {key: value for key, value in after["scheduler_state"].items() if key != "due"} == {
        key: value for key, value in before["scheduler_state"].items() if key != "due"}
    assert store.get_card("word:one", "word_meaning_kana", "hiragana") is None
    assert store._db.execute("SELECT COUNT(*) FROM review_event").fetchone()[0] == 1


def test_duplicate_drill_save_and_stale_checkpoint_cannot_duplicate_credit(store):
    main = begin(store, [main_question()])
    q = question()
    sid = begin(store, [q])
    arguments = dict(session_id=sid, question_index=0, answered_text=q.answer,
                     outcome="correct", kana_rows=ROWS, now=NOW)
    store.record_review(q.content_id, q.kind, q.script, None, **arguments)
    again = store.record_review(q.content_id, q.kind, q.script, None, **arguments)
    store.save_session_progress(sid, 0, state={"word_answer": "か"})
    assert again["already_recorded"]
    assert store.load_session_progress("main")["session_id"] == main
    assert store.load_session_progress("word_romaji") is None
    assert len(store.word_spelling_events(now=NOW)) == 2


def test_independent_failure_still_resets_quarter_credit(store):
    for index in range(8):
        submit(store, now=NOW + timedelta(minutes=index))
    store.record_review(KA, "kana_choice", "hiragana", 1,
                        outcome="incorrect", now=NOW + timedelta(hours=1))
    assert _adaptive_stages(store, "hiragana", NOW + timedelta(hours=1)) == {KA: 0, KI: 1}


def test_current_backup_restores_both_lanes_weights_and_drafts_then_reset_clears_all(store, tmp_path):
    for index in range(8):
        submit(store, now=NOW + timedelta(minutes=index))
    main = begin(store, [main_question(due=NOW + timedelta(hours=1))], {"page": "session_wait"})
    drill = begin(store, [question()], {"word_answer": "か"})
    backup = store.export_backup()
    assert decode_current(backup)["version"] == 1
    with StudyStore(tmp_path / "restored", "UTC") as restored:
        restored.import_backup(backup)
        assert restored.load_session_progress()["session_id"] == main
        assert restored.load_session_progress("word_romaji")["session_id"] == drill
        assert restored.load_session_progress("word_romaji")["state"] == {"word_answer": "か"}
        assert _adaptive_stages(restored, "hiragana", NOW + timedelta(hours=1)) == {KA: 1, KI: 1}
        assert restored.word_spelling_events(now=NOW + timedelta(hours=1)) == store.word_spelling_events(now=NOW + timedelta(hours=1))
        restored.clear_learning_data()
        assert checkpoints(restored) == []
        assert restored.word_spelling_events(now=NOW + timedelta(hours=1)) == []


def test_unanswered_drill_keeps_same_current_record_format(store):
    assert decode_current(store.export_backup())["version"] == 1
    begin(store, [question()])
    assert decode_current(store.export_backup())["version"] == 1


@pytest.mark.parametrize("mutation", ["duplicate_lane", "mixed_session", "marker_type", "unknown_policy", "missing_policy", "downgrade"])
def test_corrupt_current_import_is_atomic(store, mutation):
    submit(store)
    begin(store, [main_question()])
    begin(store, [question()])
    before = store.export_backup()
    payload = decode_current(before)
    tables = payload["tables"]
    drill_question = next(q for q in tables["session_question"]
        if json.loads(q["snapshot_json"])["metadata"].get("word_romaji_drill"))
    drill_id = drill_question["session_id"]
    if mutation == "duplicate_lane":
        checkpoint = dict(tables["session_checkpoint"][-1])
        checkpoint["session_id"] = drill_id
        tables["session_checkpoint"].append(checkpoint)
    elif mutation == "mixed_session":
        ordinal = max(q["question_index"] for q in tables["session_question"] if q["session_id"] == drill_id) + 1
        tables["session_question"].append({"session_id": drill_id, "question_index": ordinal,
            "snapshot_json": json.dumps(main_question().to_dict())})
    elif mutation == "marker_type":
        snapshot = json.loads(drill_question["snapshot_json"])
        snapshot["metadata"]["word_romaji_drill"] = 1
        drill_question["snapshot_json"] = json.dumps(snapshot)
    elif mutation in {"unknown_policy", "missing_policy"}:
        evidence = json.loads(tables["word_spelling_attempt"][0]["evidence_json"])
        if mutation == "unknown_policy":
            evidence["practice_policy"] = "arbitrary-full-credit"
        else:
            del evidence["practice_policy"]
        tables["word_spelling_attempt"][0]["evidence_json"] = json.dumps(evidence)
    else:
        payload["version"] = 3
    with pytest.raises(ValueError):
        store.import_backup(encode_current(payload))
    assert decode_current(store.export_backup())["tables"] == decode_current(before)["tables"]


def test_unknown_lane_rejected_without_switching(store):
    with pytest.raises(ValueError):
        store.practice_lane = "other"
    with pytest.raises(ValueError):
        store.load_session_progress("other")
    assert store.practice_lane == "main"
