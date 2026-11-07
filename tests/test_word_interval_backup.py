"""Bounded auxiliary due changes survive backup without replay or fake reviews."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest
from record_test_support import decode_current, encode_current, as_legacy

from kanamemory.domain import Question, StudyStore


from support.word_interval_backup_support import BASE, NOW, KA, KI, ROWS, word, submit, mature


def payload(store):
    return decode_current(store.export_backup())


def audit(document):
    row = document["tables"]["word_spelling_attempt"][0]
    evidence = json.loads(row["evidence_json"])
    return row, evidence, evidence["schedule_adjustments"][0]


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", "UTC") as value:
        mature(value)
        submit(value, answered_text="かか")
        yield value


def test_v9_restores_actual_due_and_audit_without_replaying_adjustments(store, tmp_path):
    before = payload(store)
    assert before["version"] == 1
    _, evidence, _ = audit(before)
    assert len(evidence["schedule_adjustments"]) == 2
    assert len(store.review_history()) == 2
    with StudyStore(tmp_path / "restored", "UTC") as restored:
        summary = restored.import_backup(store.export_backup())
        assert summary["cards"] == summary["reviews"] == 2
        assert summary["word_exercises"] == 1
        assert payload(restored)["tables"] == before["tables"]
        assert restored.get_card("word:persimmon", "word_meaning_kana", "hiragana") is None
        copied = restored.export_backup()
        restored.import_backup(copied)
        assert payload(restored)["tables"] == before["tables"]
    with StudyStore(tmp_path / "restored", "UTC") as reopened:
        assert payload(reopened)["tables"] == before["tables"]
        cards = [dict(row) for row in reopened._db.execute("SELECT * FROM study_card")]
        submit(reopened, word(content_id="word:same-reading"), answered_text="かか",
               now=NOW + timedelta(minutes=1))
        assert [dict(row) for row in reopened._db.execute("SELECT * FROM study_card")] == cards


def test_historical_adjustments_remain_valid_after_later_independent_review(store, tmp_path):
    old = audit(payload(store))[2]["after_due_at_utc"]
    store.record_review(KA, "romaji_kana", "hiragana", 3,
                        outcome="correct", now=NOW + timedelta(days=10))
    assert store.get_card(KA, "romaji_kana", "hiragana")["due_at_utc"] != old
    with StudyStore(tmp_path / "restored", "UTC") as restored:
        restored.import_backup(store.export_backup())
        assert payload(restored)["tables"] == payload(store)["tables"]


def test_v5_restore_preserves_both_drafts_and_pre_restore_rollback(store, tmp_path):
    main = Question(KA, "kana_choice", "hiragana", "か", "ka",
        metadata={"not_before_utc": (NOW + timedelta(minutes=5)).isoformat()})
    main_sid = store.start_session([main], now=NOW)
    store.save_session_progress(main_sid, 0, state={"page": "session_wait"})
    drill_sid = store.start_session([word()], now=NOW)
    store.save_session_progress(drill_sid, 0, state={"word_answer": "か"})
    with StudyStore(tmp_path / "target", "UTC") as target:
        mature(target)
        submit(target, answered_text="きき")
        original = payload(target)["tables"]
        imported = target.import_backup(store.export_backup())
        assert Path(imported["rollback_path"]).is_file()
        assert decode_current(target.previous_backup())["tables"] == original
        assert target.load_session_progress("main")["session_id"] == main_sid
        assert target.load_session_progress("word_romaji")["session_id"] == drill_sid
        assert target.load_session_progress("word_romaji")["state"] == {"word_answer": "か"}
        assert payload(target)["tables"] == payload(store)["tables"]
        target.import_backup(target.previous_backup())
        assert payload(target)["tables"] == original


def test_same_current_format_for_empty_forward_auxiliary_and_assisted_history(tmp_path):
    with StudyStore(tmp_path / "store", "UTC") as store:
        assert payload(store)["version"] == 1
        submit(store, word(drill=False))
        assert payload(store)["version"] == 1
        sid = store.start_session([word()], now=NOW)
        store.save_session_progress(sid, 0)
        assert payload(store)["version"] == 1
        submit(store, assisted=True)
        document = payload(store)
        assert document["version"] == 1
        last = json.loads(document["tables"]["word_spelling_attempt"][-1]["evidence_json"])
        assert last["schedule_adjustments"] == []
        store.inspect_backup(store.export_backup())


def test_old_v4_word_history_without_adjustment_marker_still_imports(store, tmp_path):
    document = as_legacy(payload(store), version=4)
    for row in document["tables"]["word_spelling_attempt"]:
        evidence = json.loads(row["evidence_json"])
        del evidence["schedule_adjustments"]
        row["evidence_json"] = json.dumps(evidence)
    with StudyStore(tmp_path / "target", "UTC") as target:
        from kanamemory.progress_backup import convert_legacy_backup
        converted = convert_legacy_backup(target, json.dumps(document).encode())
        target.import_backup(converted)
        assert payload(target)["version"] == 1
        assert payload(target)['tables']['study_card'] == document['tables']['study_card']
        assert payload(target)['tables']['word_spelling_attempt'] == document['tables']['word_spelling_attempt']


@pytest.mark.parametrize("mutation", [
    "downgrade", "non_drill", "not_array", "extra_field", "missing_field",
    "policy", "weight", "bool_weight", "missing_card", "missing_anchor", "foreign_anchor",
    "skill", "script", "content", "result", "duplicate", "before_past",
    "after_unchanged", "after_too_far", "base_due", "base_interval", "bool_interval",
    "before_out_of_bounds", "time_without_zone", "time_after_anchor", "base_state",
    "accepted_alternative",
])
def test_invalid_interval_audit_is_rejected_before_any_live_write_or_rollback(store, mutation):
    original = payload(store)
    document = deepcopy(original)
    row, evidence, adjustment = audit(document)
    if mutation == "downgrade":
        document["version"] = 4
    elif mutation == "non_drill":
        for saved in document["tables"]["session_question"]:
            question = json.loads(saved['snapshot_json'])
            question["metadata"]["word_romaji_drill"] = False
            saved['snapshot_json'] = json.dumps(question)
        del evidence["practice_policy"]
    elif mutation == "not_array":
        evidence["schedule_adjustments"] = {}
    elif mutation == "extra_field":
        adjustment["extra"] = 1
    elif mutation == "missing_field":
        del adjustment["base_due_at_utc"]
    elif mutation == "policy":
        adjustment["policy"] = "different"
    elif mutation == "weight":
        adjustment["weight"] = 1
    elif mutation == "bool_weight":
        adjustment["weight"] = True
    elif mutation == "missing_card":
        adjustment["card_id"] = "missing"
    elif mutation == "missing_anchor":
        adjustment["anchor_event_id"] = "missing"
    elif mutation == "foreign_anchor":
        adjustment["anchor_event_id"] = next(event["event_id"] for event in document["tables"]["review_event"]
            if event["card_id"] != adjustment["card_id"])
    elif mutation == "skill":
        adjustment["skill"] = "romaji_handwriting"
    elif mutation == "script":
        adjustment["script"] = "katakana"
    elif mutation == "content":
        adjustment["content_id"] = "kana-other"
    elif mutation == "result":
        adjustment["result"] = "incorrect" if adjustment["result"] == "correct" else "correct"
    elif mutation == "duplicate":
        evidence["schedule_adjustments"].append(dict(adjustment))
    elif mutation == "before_past":
        adjustment["before_due_at_utc"] = BASE.isoformat()
    elif mutation == "after_unchanged":
        adjustment["after_due_at_utc"] = adjustment["before_due_at_utc"]
    elif mutation == "after_too_far":
        adjustment["after_due_at_utc"] = (NOW + timedelta(days=3000)).isoformat()
    elif mutation == "base_due":
        adjustment["base_due_at_utc"] = (NOW + timedelta(days=300)).isoformat()
    elif mutation == "base_interval":
        adjustment["base_interval_seconds"] += 1
    elif mutation == "bool_interval":
        adjustment["base_interval_seconds"] = True
    elif mutation == "before_out_of_bounds":
        adjustment["before_due_at_utc"] = (NOW + timedelta(days=300)).isoformat()
    elif mutation == "time_without_zone":
        adjustment["after_due_at_utc"] = "2026-10-30T12:00:00"
    elif mutation == "time_after_anchor":
        row["occurred_at_utc"] = (BASE - timedelta(minutes=1)).isoformat()
    elif mutation == "base_state":
        anchor = next(event for event in document["tables"]["review_event"]
            if event["event_id"] == adjustment["anchor_event_id"])
        state = json.loads(anchor["state_after_json"])
        state["state"] = 1
        state["step"] = 0
        anchor["state_after_json"] = json.dumps(state)
    elif mutation == "accepted_alternative":
        evidence["accepted_alternative"] = True
    row["evidence_json"] = json.dumps(evidence)
    existing_rollbacks = list(store.data_dir.glob("learning-before-restore-*.json"))
    with pytest.raises(ValueError, match="备份.*无效"):
        store.import_backup(encode_current(document))
    assert payload(store)["tables"] == original["tables"]
    assert list(store.data_dir.glob("learning-before-restore-*.json")) == existing_rollbacks


def test_reset_removes_adjustment_history_and_keeps_current_schema(store):
    store.clear_learning_data()
    document = payload(store)
    assert document["version"] == 1
    assert not document["tables"]["study_card"]
    assert not document["tables"]["review_event"]


def test_same_reading_cannot_claim_another_interval_adjustment_with_another_word_id(store):
    submit(store, word(content_id="word:duplicate-reading"), now=NOW + timedelta(minutes=1))
    document = payload(store)
    first = json.loads(document["tables"]["word_spelling_attempt"][0]["evidence_json"])
    row = document["tables"]["word_spelling_attempt"][-1]
    evidence = json.loads(row["evidence_json"])
    assert evidence["schedule_adjustments"] == []
    # A second alias of the same reading cannot pretend to be a new interval
    # observation, even with an otherwise valid-looking quarter-step formula.
    evidence["schedule_adjustments"] = [deepcopy(first["schedule_adjustments"][0])]
    row["evidence_json"] = json.dumps(evidence)
    with pytest.raises(ValueError, match="重复"):
        store.inspect_backup(encode_current(document))
