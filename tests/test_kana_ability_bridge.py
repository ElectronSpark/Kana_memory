"""Capability bridge preparation preserves old data; activation is explicit."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory.domain import Question, StudyStore
from kanamemory.kana_ability_bridge import (
    BRIDGE_SETTING, ability_unit_id, activate_bridge, auxiliary_anchor, bridge_after_review,
    effective_carrier, record_ability_review, register_legacy_map,
)

from support.kana_ability_bridge_support import START, CID, UID, store, legacy_rows, prepare


def test_registration_is_audited_identity_only_and_combined_is_not_split(store):
    for kind in ("kana_choice", "kana_romaji", "romaji_kana", "romaji_kana_full", "kana_handwriting"):
        store.record_review(CID, kind, "hiragana", 4, now=START)
    before = legacy_rows(store)
    mapping = register_legacy_map(store)
    assert legacy_rows(store) == before
    units = list(mapping["units"].values())
    assert len(units) == 3
    assert {u["ability"] for u in units} == {"reading", "spelling", "legacy_combined"}
    combined = next(u for u in units if u["ability"] == "legacy_combined")
    assert combined["deferred_reason"] == "combined_evidence_needs_diagnosis"
    assert all(u["status"] == "deferred" for u in units)


def test_independent_earliest_carrier_prepares_without_any_second_score(store):
    store.record_review(CID, "kana_choice", "hiragana", 4, now=START)
    store.record_review(CID, "kana_romaji", "hiragana", 4, now=START + timedelta(hours=1))
    register_legacy_map(store)
    review = store.record_review(CID, "kana_choice", "hiragana", 4, now=START + timedelta(days=5))
    before = legacy_rows(store)
    ready = bridge_after_review(store, review["event_id"])
    assert ready["status"] == "ready" and ready["carrier_skill"] == "kana_choice"
    assert ready["scheduler_state"] == review["card"]["scheduler_state"]
    assert legacy_rows(store) == before
    assert bridge_after_review(store, review["event_id"]) == ready
    assert effective_carrier(store, CID, "hiragana", "kana_romaji") is None
    with pytest.raises(ValueError):
        activate_bridge(store, UID)
    with pytest.raises(ValueError):
        record_ability_review(store, UID, 4, attempt_id="not-connected")


@pytest.mark.parametrize("reason", ["pending", "short", "assisted", "not-earliest"])
def test_unsafe_bridge_is_deferred_and_old_states_unchanged(store, reason):
    store.record_review(CID, "kana_choice", "hiragana", 4, now=START)
    store.record_review(CID, "kana_romaji", "hiragana", 4, now=START + timedelta(hours=1))
    register_legacy_map(store)
    skill = "kana_romaji" if reason == "not-earliest" else "kana_choice"
    review = store.record_review(CID, skill, "hiragana", 1 if reason == "short" else 4,
                                now=START + timedelta(days=5), assisted=reason == "assisted")
    if reason == "pending":
        sid = store.start_session([Question(CID, "romaji_kana", "hiragana", "a", "あ")])
        store.save_session_progress(sid, 0, state={"draft": "あ"})
    before = legacy_rows(store)
    result = bridge_after_review(store, review["event_id"])
    assert result["status"] == "deferred"
    assert legacy_rows(store) == before


def test_explicit_activation_aliases_only_same_script_and_ability(store):
    _, ready = prepare(store)
    before = legacy_rows(store)
    active = activate_bridge(store, UID, routing_ready=True)
    assert legacy_rows(store) == before
    assert active["status"] == "active"
    assert effective_carrier(store, CID, "hiragana", "kana_romaji")["carrier_card_id"] == ready["carrier_card_id"]
    assert effective_carrier(store, CID, "katakana", "kana_romaji") is None
    assert effective_carrier(store, CID, "hiragana", "romaji_kana") is None
    assert len({auxiliary_anchor(store, cid) for cid in active["legacy_card_ids"]}) == 1


def test_real_shared_review_is_atomic_idempotent_and_preserves_old_history(store):
    _, ready = prepare(store)
    activate_bridge(store, UID, routing_ready=True)
    old_events = legacy_rows(store)["review_event"]
    sibling = store.get_card(CID, "kana_romaji", "hiragana")
    now = START + timedelta(days=20)
    result = record_ability_review(store, UID, 3, attempt_id="attempt-1", answered_text="a", now=now)
    assert legacy_rows(store)["review_event"][:len(old_events)] == old_events
    assert len(legacy_rows(store)["review_event"]) == len(old_events) + 1
    assert store.get_card(CID, "kana_romaji", "hiragana") == sibling
    active = effective_carrier(store, CID, "hiragana", "kana_choice")
    assert active["anchor_event_id"] == result["event_id"]
    assert active["scheduler_state"] == store.get_card(CID, "kana_choice", "hiragana")["scheduler_state"]
    after = legacy_rows(store)
    replay = record_ability_review(store, UID, 3, attempt_id="attempt-1", now=now + timedelta(seconds=10))
    assert replay["already_recorded"] and replay["event_id"] == result["event_id"]
    assert legacy_rows(store) == after
    assert {auxiliary_anchor(store, cid)[1] for cid in active["legacy_card_ids"]} == {result["event_id"]}


def test_active_bridge_refuses_new_pending_legacy_queue(store):
    prepare(store)
    activate_bridge(store, UID, routing_ready=True)
    sid = store.start_session([Question(CID, "kana_choice", "hiragana", "あ", "a")])
    store.save_session_progress(sid, 0, state={"draft": "a"})
    before = legacy_rows(store)
    with pytest.raises(ValueError):
        record_ability_review(store, UID, 3, attempt_id="attempt-1", now=START + timedelta(days=20))
    assert legacy_rows(store) == before


def test_mapping_and_preparation_survive_backup_restore_without_activation(store, tmp_path):
    prepare(store)
    backup = store.export_backup()
    with StudyStore(tmp_path / "restored") as restored:
        restored.import_backup(backup)
        assert restored.get_setting(BRIDGE_SETTING) == store.get_setting(BRIDGE_SETTING)
        assert effective_carrier(restored, CID, "hiragana", "kana_choice") is None
        assert legacy_rows(restored) == legacy_rows(store)
