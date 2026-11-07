"""Production answer/queue/auxiliary/restore paths for an activated ability."""
from datetime import timedelta
import json

import pytest

from kanamemory.domain import Question, StudyStore, prepare_session_snapshot
from kanamemory.kana_ability_bridge import (
    BRIDGE_SETTING, activate_bridge, bridge_after_review, register_legacy_map,
)
from kanamemory.kana_routing import ROUTE_KEY, bind_encoded, unit_for
from kanamemory.learning_evidence import LearningEvidenceStore
from kanamemory.review_dispatch import reviews_today
from record_test_support import decode_current, encode_current
from support.kana_ability_bridge_support import START
from support.kana_ability_bridge_support import CID
from support.kana_ability_bridge_support import UID
from support.kana_ability_bridge_support import legacy_rows
from support.kana_ability_bridge_support import prepare
from support.kana_ability_bridge_support import store


from support.kana_routing_support import activate, answer


def test_real_answer_uses_one_carrier_preserves_outcome_and_duplicate_response(store):
    ready = activate(store)
    sibling = store.get_card(CID, "kana_romaji", "hiragana")
    old_events = legacy_rows(store)["review_event"]
    first, sid, args = answer(store, outcome="incorrect", rating=1)
    second = store.record_review(CID, "kana_romaji", "hiragana", 1, **args)
    assert second["already_recorded"] and second["event_id"] == first["event_id"]
    assert first["card"] == second["card"]
    assert first["card"]["card_id"] == ready["carrier_card_id"]
    assert first["card"]["scheduler_state"]["state"] == 3
    assert store.get_card(CID, "kana_romaji", "hiragana") == sibling
    assert legacy_rows(store)["review_event"][:len(old_events)] == old_events
    event = store.review_history(newest_first=True, limit=1)[0]
    assert event["skill"] == "kana_romaji" and event["outcome"] == "incorrect"
    assert json.loads(event["review_log_json"])["ability_route"] == store._session_snapshot(sid)[0]["metadata"][ROUTE_KEY]
    assert store.review_history(skill="kana_romaji")[-1]["event_id"] == first["event_id"]
    assert store.stats(START + timedelta(days=100))["total_cards"] == 1
    assert unit_for(store, CID, "kana_romaji", "hiragana")["status"] == "active"


def test_auxiliary_live_due_is_used_by_next_formal_answer_and_restore(store, tmp_path):
    activate(store)
    now = START + timedelta(days=6)
    sid = store.start_session([Question("word:aa", "word_meaning_kana", "hiragana", "aa", "ああ",
                                       metadata={"word_romaji_drill": True})], now=now)
    args = dict(session_id=sid, question_index=0, answered_text="ああ", outcome="correct",
                kana_rows=[{"id": CID, "hiragana": "あ", "katakana": "ア"}], now=now)
    before = store.get_scheduling_card(CID, "kana_romaji", "hiragana")
    sibling = store.get_card(CID, "kana_romaji", "hiragana")
    result = store.record_review("word:aa", "word_meaning_kana", "hiragana", None, **args)
    adjustment, = result["word_spelling_evidence"]["schedule_adjustments"]
    assert adjustment["card_id"] == before["card_id"] and adjustment["weight"] == .25
    after = store.get_scheduling_card(CID, "kana_romaji", "hiragana")
    assert after["base_due_at_utc"] != before["base_due_at_utc"]
    assert after["due_at_utc"] == before["due_at_utc"]  # Input deadline is protected.
    assert after["review_count"] == before["review_count"]
    assert store.get_card(CID, "kana_romaji", "hiragana") == sibling
    assert store.record_review("word:aa", "word_meaning_kana", "hiragana", None, **args)["already_recorded"]
    assert store._db.execute("SELECT COUNT(*) FROM learning_aux_adjustment").fetchone()[0] == 1
    with StudyStore(tmp_path / "restored") as restored:
        restored.import_backup(store.export_backup())
        assert restored.get_scheduling_card(CID, "kana_romaji", "hiragana") == after
        repeat = restored.start_session([Question("word:repeat-aa", "word_meaning_kana", "hiragana", "aa", "ああ",
                                                  metadata={"word_romaji_drill": True})], now=now + timedelta(minutes=1))
        repeated = restored.record_review("word:repeat-aa", "word_meaning_kana", "hiragana", None,
            **{**args, "session_id": repeat, "now": now + timedelta(minutes=1)})
        assert repeated["word_spelling_evidence"]["schedule_adjustments"] == []
        assert restored.get_scheduling_card(CID, "kana_romaji", "hiragana") == after
        reviewed, _, _ = answer(restored)
        event = restored.review_history(newest_first=True, limit=1)[0]
        assert event["state_before"]["due"] == after["base_due_at_utc"]
        assert reviewed["card"]["card_id"] == before["card_id"]
    LearningEvidenceStore(store).retract(adjustment["evidence_event_id"], reason="test", now=now + timedelta(seconds=1))
    assert store.get_scheduling_card(CID, "kana_romaji", "hiragana")["due_at_utc"] == before["due_at_utc"]
    with StudyStore(tmp_path / "restored-retracted") as restored:
        restored.import_backup(store.export_backup())
        assert restored.get_scheduling_card(CID, "kana_romaji", "hiragana")["due_at_utc"] == before["due_at_utc"]


def test_due_alias_and_counts_do_not_resurrect_earlier_sibling(store):
    ready = activate(store)
    now = START + timedelta(days=100)
    assert len(store.get_due(now=now)) == 1
    assert store.get_due("kana_romaji", "hiragana", now=now)[0]["card_id"] == ready["carrier_card_id"]
    result, _, _ = answer(store, now=now)
    assert store.get_due(now=now) == []
    assert store.next_due_at(now=now) == result["due_at_utc"]
    q = Question(CID, "kana_romaji", "hiragana", "あ", "a")
    assert store.practice_counts([q], now) == {"due": 0, "new": 0}
    assert store.batch_counts([q], now)["new"] == 0


def test_other_abilities_and_script_are_not_modified_and_device_failure_is_not_evidence(store):
    activate(store)
    protected = []
    for kind, script in [("romaji_kana", "hiragana"), ("kana_romaji", "katakana"), ("romaji_handwriting", "hiragana")]:
        store.record_review(CID, kind, script, 4, now=START)
        protected.append(store.get_card(CID, kind, script))
    before = store.get_scheduling_card(CID, "kana_romaji", "hiragana")
    result = store.record_review(CID, "kana_romaji", "hiragana", 3, audio_ok=False, now=START + timedelta(days=100))
    assert not result["scheduled"] and result["card"] == before
    answer(store)
    for card in protected:
        assert store.get_card(CID, card["skill"], card["script"]) == card


def test_unrelated_short_step_does_not_block_reading_preparation(store):
    store.record_review(CID, "romaji_handwriting", "hiragana", 1, now=START)
    _, ready = prepare(store)
    assert ready["status"] == "ready"


def test_active_draft_timed_repetition_survive_restore(store, tmp_path):
    activate(store)
    result, sid, _ = answer(store, rating=1, outcome="incorrect")
    assert result["queue_extended"]
    snapshot = store._session_snapshot(sid)
    assert snapshot[1]["metadata"][ROUTE_KEY] == snapshot[0]["metadata"][ROUTE_KEY]
    with StudyStore(tmp_path / "restore-steps") as restored:
        restored.import_backup(store.export_backup())
        assert restored._session_snapshot(sid) == snapshot
        due = restored.get_scheduling_card(CID, "kana_romaji", "hiragana")["due_at_utc"]
        assert snapshot[1]["metadata"]["not_before_utc"] == due
        now = START + timedelta(days=100, minutes=30)
        store.save_session_progress(sid, 1, state={"romaji_answer": "a"})
        restored.save_session_progress(sid, 1, state={"romaji_answer": "a"})
        assert restored.load_session_progress()["state"]["romaji_answer"] == "a"
        out = restored.record_review(CID, "kana_romaji", "hiragana", 3, session_id=sid,
                                     question_index=1, answered_text="a", outcome="correct", now=now)
        assert out["card"]["card_id"] == result["card"]["card_id"]


def test_disabled_and_reenabled_repetition_keeps_original_carrier_and_due(store):
    activate(store)
    result, sid, _ = answer(store, rating=1, outcome="incorrect", tail=True)
    before = legacy_rows(store)
    now = START + timedelta(days=100, seconds=2)
    disabled = store.reconcile_session_kinds(sid, {"kana_choice"}, now=now)
    assert disabled is not None
    enabled = store.reconcile_session_kinds(sid, {"kana_choice", "kana_romaji"}, now=now)
    assert enabled["restored_count"] == 1
    assert enabled["questions"][-1].metadata["not_before_utc"] == result["due_at_utc"]
    assert legacy_rows(store)["study_card"] == before["study_card"]
    assert legacy_rows(store)["review_event"] == before["review_event"]


def test_disabling_only_remaining_kind_keeps_due_ability_available_when_enabled(store):
    activate(store)
    result, sid, _ = answer(store, rating=1, outcome="incorrect")
    before = legacy_rows(store)
    disabled = store.reconcile_session_kinds(sid, {"kana_choice"}, now=START + timedelta(days=100, seconds=2))
    assert disabled["completed"] and store.load_session_progress() is None
    due, = store.get_due("kana_romaji", "hiragana", now=START + timedelta(days=101))
    assert due["card_id"] == result["card"]["card_id"] and due["due_at_utc"] == result["due_at_utc"]
    assert legacy_rows(store)["study_card"] == before["study_card"]
    assert legacy_rows(store)["review_event"] == before["review_event"]


def test_unseen_question_mode_does_not_create_second_card(store):
    store.record_review(CID, "kana_choice", "hiragana", 4, now=START)
    register_legacy_map(store)
    review = store.record_review(CID, "kana_choice", "hiragana", 4, now=START + timedelta(days=5))
    bridge_after_review(store, review["event_id"])
    activate_bridge(store, UID, routing_ready=True)
    answer(store)
    assert store.get_card(CID, "kana_romaji", "hiragana") is None
    assert store._db.execute("SELECT COUNT(*) FROM study_card").fetchone()[0] == 1


def test_ready_mapping_can_remain_stale_until_explicit_repreparation(store, tmp_path):
    prepare(store)
    store.record_review(CID, "kana_choice", "hiragana", 3, now=START + timedelta(days=100))
    with StudyStore(tmp_path / "stale-ready") as restored:
        restored.import_backup(store.export_backup())
        assert restored.get_setting(BRIDGE_SETTING) == store.get_setting(BRIDGE_SETTING)
        with pytest.raises(ValueError): activate_bridge(restored, UID, routing_ready=True)


def test_paused_carrier_cannot_receive_auxiliary_credit_through_active_sibling(store):
    active = activate(store)
    with store._db:
        store._db.execute("UPDATE study_card SET suspended=1 WHERE card_id=?", (active["carrier_card_id"],))
    from support.word_interval_support import evidence
    from kanamemory.word_interval import apply_word_interval_adjustments
    before = legacy_rows(store)
    with store._db:
        store._db.execute("BEGIN IMMEDIATE")
        assert apply_word_interval_adjustments(store, evidence("あ", cid=CID), "hiragana",
                                               START + timedelta(days=6)) == []
    assert legacy_rows(store) == before
    assert store._db.execute("SELECT COUNT(*) FROM learning_aux_adjustment").fetchone()[0] == 0


def test_daily_budget_sums_real_alias_history(store):
    activate(store)
    # The oldest rows on one calendar day remain real distinct ratings.
    day, counts = reviews_today(store, START + timedelta(hours=2))
    assert counts[unit_for(store, CID, "kana_choice", "hiragana")["carrier_card_id"]] == 2


@pytest.mark.parametrize("change", ["carrier", "anchor", "script", "route_kind", "pending_route"])
def test_corrupt_routing_backup_is_rejected_atomically(store, tmp_path, change):
    activate(store)
    _, sid, _ = answer(store)
    if change == "pending_route":
        sid = store.start_session([Question(CID, "kana_romaji", "hiragana", "あ", "a")])
        store.save_session_progress(sid, 0, state={"romaji_answer": "a"})
    document = decode_current(store.export_backup())
    tables = document["tables"]
    setting = next(r for r in tables["app_setting"] if r["key"] == BRIDGE_SETTING)
    mapping = json.loads(setting["value_json"])
    unit = mapping["units"][UID]
    if change == "carrier": unit["carrier_card_id"] = "missing"
    if change == "anchor": unit["anchor_event_id"] = unit["bridge_event_id"]
    if change == "script": unit["script"] = "katakana"
    setting["value_json"] = json.dumps(mapping)
    if change in {"route_kind", "pending_route"}:
        row = next(r for r in tables["session_question"] if r["session_id"] == sid)
        question = json.loads(row["snapshot_json"])
        question["metadata"][ROUTE_KEY]["skill"] = "romaji_handwriting"
        row["snapshot_json"] = json.dumps(question)
    with StudyStore(tmp_path / "reject") as restored:
        before = legacy_rows(restored)
        with pytest.raises(ValueError): restored.import_backup(encode_current(document))
        assert legacy_rows(restored) == before


def test_prepared_unaffected_rows_are_not_reserialized(store):
    q = Question(CID, "kana_romaji", "hiragana", "あ", "a")
    prepared = prepare_session_snapshot([q])
    items = json.loads(prepared.snapshot_json)
    bound, encoded = bind_encoded(store, items, prepared.question_json)
    assert bound is items and encoded is prepared.question_json
    activate(store)
    other = Question("kana-3044", "kana_romaji", "hiragana", "い", "i")
    prepared = prepare_session_snapshot([q, other])
    items = json.loads(prepared.snapshot_json)
    bound, encoded = bind_encoded(store, items, prepared.question_json)
    assert encoded[1] is prepared.question_json[1]
    assert ROUTE_KEY in bound[0]["metadata"] and ROUTE_KEY not in bound[1]["metadata"]


def test_route_cache_observes_rollback_and_import(store, tmp_path):
    from kanamemory.kana_routing import active_units
    activate(store)
    saved = active_units(store)[UID]["anchor_event_id"]
    store._db.execute("BEGIN IMMEDIATE")
    value = store.get_setting(BRIDGE_SETTING)
    value["units"][UID]["anchor_event_id"] = "temporary"
    store._db.execute("UPDATE app_setting SET value_json=? WHERE key=?", (json.dumps(value), BRIDGE_SETTING))
    assert active_units(store)[UID]["anchor_event_id"] == "temporary"
    store._db.rollback()
    assert active_units(store)[UID]["anchor_event_id"] == saved
    with StudyStore(tmp_path / "empty") as empty:
        store.import_backup(empty.export_backup())
    assert active_units(store) == {}


def test_queue_failure_rolls_back_carrier_anchor_evidence_and_answer(store, monkeypatch):
    from kanamemory import session_questions
    activate(store)
    q = Question(CID, "kana_romaji", "hiragana", "あ", "a",
                 metadata={"practice_config": {"schedule_mode": "spaced"}})
    sid = store.start_session([q], now=START + timedelta(days=100))
    store.save_session_progress(sid, 0, state={"romaji_answer": "a"})
    before = decode_current(store.export_backup())["tables"]
    def fail(*args, **kwargs):
        raise RuntimeError("injected queue failure after scoring")
    with monkeypatch.context() as patch:
        patch.setattr(session_questions, "replace", fail)
        with pytest.raises(RuntimeError, match="injected queue failure"):
            store.record_review(CID, "kana_romaji", "hiragana", 1, session_id=sid, question_index=0,
                                answered_text="i", outcome="incorrect", now=START + timedelta(days=100))
    assert decode_current(store.export_backup())["tables"] == before
    assert store.load_session_progress()["state"]["romaji_answer"] == "a"


def test_spelling_route_preserves_actual_accepted_homophone(store, tmp_path):
    from kanamemory.domain import check_answer
    from kanamemory.kana_ability_bridge import ability_unit_id
    cid = "kana-3058"
    for index, kind in enumerate(("romaji_kana", "romaji_kana_full")):
        store.record_review(cid, kind, "hiragana", 4, now=START + timedelta(hours=index))
    register_legacy_map(store)
    reviewed = store.record_review(cid, "romaji_kana", "hiragana", 4, now=START + timedelta(days=5))
    bridge_after_review(store, reviewed["event_id"])
    activate_bridge(store, ability_unit_id(cid, "hiragana", "spelling"), routing_ready=True)
    q = Question(cid, "romaji_kana_full", "hiragana", "ji", "じ", metadata={"answer_scope": "isolated_sound"})
    assert check_answer(q, "ぢ")
    sid = store.start_session([q], now=START + timedelta(days=100))
    result = store.record_review(cid, q.kind, q.script, 3, session_id=sid, question_index=0,
                                answered_text="ぢ", outcome="correct", now=START + timedelta(days=100))
    event = store.review_history(newest_first=True, limit=1)[0]
    evidence = json.loads(event["review_log_json"])["kana_answer_evidence"]
    assert evidence["accepted_homophone"] and evidence["actual_answer"] == "ぢ"
    from kanamemory.kana_dependency_runtime import kana_dependency_state
    assert f"kana:{cid}:hiragana" not in kana_dependency_state(store).ready
    assert result["card"]["card_id"] == reviewed["card"]["card_id"]
    assert event["skill"] == "romaji_kana_full"
    with StudyStore(tmp_path / "restore-spelling") as restored:
        restored.import_backup(store.export_backup())
        assert restored.review_history(newest_first=True, limit=1) == [event]
