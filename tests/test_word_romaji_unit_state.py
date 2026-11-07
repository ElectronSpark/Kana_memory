"""Per-kana Roman selections survive queues/backups without independent cards."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest
from record_test_support import decode_current, encode_current, as_legacy

from kanamemory.domain import Question, StudyStore, check_answer, word_romaji_unit_plan
from kanamemory.progress_backup import validate_session_state
from kanamemory.progress_backup import convert_legacy_backup
from kanamemory.romanization import romanize_kana
from kanamemory.word_romaji_units import compose_romaji_parts

BASE = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
NOW = BASE + timedelta(hours=1)
KA, KI, KU = "kana-304b", "kana-304d", "kana-304f"
ROWS = [{"id": cid, "hiragana": hira, "katakana": kata}
        for cid, hira, kata in ((KA, "か", "カ"), (KI, "き", "キ"), (KU, "く", "ク"))]


def question(content_id="word:kaki", *, kana="かき", saved_plan=True):
    answer = romanize_kana(kana)
    q = Question(content_id, "word_romaji", "hiragana", kana, answer,
        list(dict.fromkeys([answer, "kaka", "kiku", "kika"]))[:4], metadata={
            "word_romaji_drill": True, "kana_answer": kana, "session_seed": 728,
            "practice_config": {"word_direction": "mixed"}})
    if saved_plan:
        q.metadata["romaji_unit_plan"] = word_romaji_unit_plan(q)
    return q


def parts_for(q):
    plan = word_romaji_unit_plan(q)
    return plan, [unit["answer"] for unit in plan["units"]]


def draft(q, parts=None, **kwargs):
    plan, correct = parts_for(q)
    return {"page": "question", "word_romaji_parts": correct[:1] if parts is None else parts,
            "word_romaji_plan": plan, **kwargs}


def backup(store):
    return decode_current(store.export_backup())


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", "UTC") as value:
        value.practice_lane = "word_romaji"
        yield value


def seed_cards(store):
    for cid in (KA, KI, KU):
        store.record_review(cid, "romaji_kana", "hiragana", 4, outcome="correct", now=BASE)


def cards(store):
    return {cid: store.get_card(cid, "romaji_kana", "hiragana") for cid in (KA, KI, KU)}


def submit(store, q, parts, *, assisted=False, plan=None, now=NOW):
    plan = plan or word_romaji_unit_plan(q)
    actual = compose_romaji_parts(plan, parts)
    sid = store.start_session([q], now=now)
    store.save_session_progress(sid, 0, state=draft(q, parts))
    result = store.record_review(q.content_id, q.kind, q.script, None, session_id=sid,
        question_index=0, answered_text=actual, word_romaji_parts=parts, word_romaji_plan=plan,
        outcome="correct" if check_answer(q, actual, word_romaji_parts=parts, word_romaji_plan=plan)
            and not assisted else "incorrect", assisted=assisted, kana_rows=ROWS, now=now)
    return sid, result


def test_legacy_plan_derivation_is_stable_and_never_rewrites_snapshot(store):
    q = question(saved_plan=False)
    sid = store.start_session([q], now=NOW)
    before = store._session_snapshot(sid)
    plan = word_romaji_unit_plan(q)
    assert plan == word_romaji_unit_plan(q) == word_romaji_unit_plan(before[0])
    assert "romaji_unit_plan" not in q.metadata
    store.save_session_progress(sid, 0, state=draft(q))
    assert store._session_snapshot(sid) == before
    assert store.load_session_progress()["state"]["word_romaji_plan"] == plan
    assert backup(store)["version"] == 1


def test_append_and_reopen_keep_parts_choices_main_queue_and_history(store):
    store.practice_lane = "main"
    main = Question("kana-304b", "kana_romaji", "hiragana", "か", "ka")
    main_sid = store.start_session([main], now=NOW)
    store.save_session_progress(main_sid, 0, state={"romaji_answer": "k"})
    before_main = store.load_session_progress("main")
    store.practice_lane = "word_romaji"
    q = question()
    sid = store.start_session([q], now=NOW)
    state = draft(q)
    store.save_session_progress(sid, 0, state=state)
    initial = store._session_snapshot(sid)
    for cid in ("word:added-one", "word:added-two"):
        store.append_session(sid, [question(cid)], expected_token=store.session_append_token(sid))
    with StudyStore(store.data_dir, "UTC") as reopened:
        pending = reopened.load_session_progress("word_romaji")
        assert len(pending["questions"]) == 3
        assert pending["question_index"] == 0 and pending["state"] == state
        assert reopened._session_snapshot(sid)[:1] == initial
        assert reopened.load_session_progress("main") == before_main
        assert reopened.stats(NOW)["total_cards"] == 0


def test_current_plan_and_partial_draft_roundtrip_without_rescheduling(store, tmp_path):
    seed_cards(store)
    q = question()
    sid = store.start_session([q], now=NOW)
    store.save_session_progress(sid, 0, state=draft(q))
    document = backup(store)
    assert document["version"] == 1
    with StudyStore(tmp_path / "restored", "UTC") as restored:
        restored.import_backup(store.export_backup())
        assert backup(restored)["tables"] == document["tables"]
        assert cards(restored) == cards(store)
        assert restored.load_session_progress("word_romaji") == store.load_session_progress("word_romaji")


def test_legacy_v6_feedback_keeps_whole_answer_without_new_parts(store, tmp_path):
    q = question(saved_plan=False)
    sid = store.start_session([q], now=NOW)
    state = {"page": "feedback", "feedback_pending": True, "answered_text": "kaka",
             "answer_revealed": False}
    store.save_session_progress(sid, 0, state=state)
    document = backup(store)
    old = json.dumps(as_legacy(document, version=6)).encode()
    with StudyStore(tmp_path / "old-feedback", "UTC") as restored:
        restored.import_backup(convert_legacy_backup(restored, old))
        assert restored.load_session_progress("word_romaji")["state"] == state
        restored.record_review(q.content_id, q.kind, q.script, None, session_id=sid,
            question_index=0, answered_text="kaka", outcome="incorrect", kana_rows=ROWS, now=NOW)
        assert backup(restored)["version"] == 1


@pytest.mark.parametrize("assisted", [False, True])
def test_parts_adjust_only_target_cards_once_and_keep_same_kana_key(store, assisted):
    seed_cards(store)
    q = question()
    plan, parts = parts_for(q)
    parts[1] = next(value for value in plan["units"][1]["options"] if value != parts[1])
    before = cards(store)
    sid, result = submit(store, q, parts, assisted=assisted)
    evidence = result["word_spelling_evidence"]
    assert evidence["expected"] == "かき" and evidence["romaji_parts"] == parts
    assert evidence["romaji_unit_plan"] == plan and evidence["romaji_unit_version"] == 1
    after = cards(store)
    if assisted:
        assert after == before and not evidence["units"]
    else:
        for cid, sign in ((KA, 1), (KI, -1)):
            due = datetime.fromisoformat(before[cid]["due_at_utc"])
            assert datetime.fromisoformat(after[cid]["due_at_utc"]) == due + sign * (due - BASE) * .25
        assert after[KU] == before[KU]
    repeated = store.record_review(q.content_id, q.kind, q.script, None, session_id=sid,
        question_index=0, answered_text=compose_romaji_parts(plan, parts), outcome="incorrect",
        word_romaji_parts=parts, word_romaji_plan=plan, kana_rows=ROWS, now=NOW)
    assert repeated["already_recorded"] and cards(store) == after
    assert store.get_card(q.content_id, q.kind, q.script) is None
    assert store.stats(NOW)["total_reviews"] == 3
    store.inspect_backup(store.export_backup())


def test_unit_attempt_and_legacy_opposite_direction_share_interval_dedup(store):
    seed_cards(store)
    q = question()
    plan, parts = parts_for(q)
    submit(store, q, parts)
    before = cards(store)
    forward = Question("word:other-id", "word_meaning_kana", "hiragana", "kaki", "かき",
        metadata={"word_romaji_drill": True})
    sid = store.start_session([forward], now=NOW)
    store.save_session_progress(sid, 0)
    result = store.record_review(forward.content_id, forward.kind, forward.script, None,
        session_id=sid, question_index=0, answered_text="かき", outcome="correct", kana_rows=ROWS, now=NOW)
    assert not result["word_spelling_evidence"]["schedule_adjustments"]
    assert cards(store) == before


def test_hint_can_keep_partial_draft_while_feedback_answer_is_empty(store):
    q = question()
    sid = store.start_session([q], now=NOW)
    state = draft(q, page="feedback", feedback_pending=True, answer_revealed=True, answered_text="")
    store.save_session_progress(sid, 0, state=state)
    assert store.load_session_progress()["state"] == state
    store.inspect_backup(store.export_backup())
    result = store.record_review(q.content_id, q.kind, q.script, None, session_id=sid,
        question_index=0, answered_text="", outcome="incorrect", assisted=True, kana_rows=ROWS, now=NOW)
    assert not result["word_spelling_evidence"]["units"]


@pytest.mark.parametrize("mutation", ["missing_plan", "wrong_parts_type", "too_many", "foreign_part", "wrong_question", "feedback_mismatch"])
def test_invalid_draft_does_not_replace_saved_progress(store, mutation):
    q = question()
    sid = store.start_session([q], now=NOW)
    good = draft(q)
    store.save_session_progress(sid, 0, state=good)
    bad = deepcopy(good)
    if mutation == "missing_plan": bad.pop("word_romaji_plan")
    elif mutation == "wrong_parts_type": bad["word_romaji_parts"] = "ka"
    elif mutation == "too_many": bad["word_romaji_parts"] *= 3
    elif mutation == "foreign_part": bad["word_romaji_parts"] = ["wholeword"]
    elif mutation == "wrong_question": bad["word_romaji_plan"] = word_romaji_unit_plan(question(kana="かく"))
    elif mutation == "feedback_mismatch": bad.update(page="feedback", feedback_pending=True, answered_text="wrong")
    with pytest.raises((ValueError, TypeError)):
        store.save_session_progress(sid, 0, state=bad)
    assert store.load_session_progress()["state"] == good


@pytest.mark.parametrize("mutation", ["downgrade", "draft_plan", "draft_parts", "snapshot_plan", "evidence_parts", "evidence_plan", "evidence_actual"])
def test_malformed_current_backup_rejected_before_live_mutation(store, mutation):
    q = question()
    plan, parts = parts_for(q)
    submit(store, q, parts)
    pending = question("word:pending")
    sid = store.start_session([pending], now=NOW)
    store.save_session_progress(sid, 0, state=draft(pending))
    original = backup(store)
    damaged = deepcopy(original)
    if mutation == "downgrade": damaged["version"] = 6
    elif mutation == "snapshot_plan":
        row = next(row for row in damaged["tables"]["session_question"] if row["session_id"] == sid)
        snapshot = json.loads(row["snapshot_json"])
        snapshot["metadata"]["romaji_unit_plan"]["units"][0]["answer"] = "wrong"
        row["snapshot_json"] = json.dumps(snapshot)
    elif mutation.startswith("draft"):
        row = damaged["tables"]["session_checkpoint"][0]
        state = json.loads(row["state_json"])
        if mutation == "draft_plan": state["word_romaji_plan"]["units"][0]["kana"] = "く"
        else: state["word_romaji_parts"] = ["wholeword"]
        row["state_json"] = json.dumps(state)
    else:
        row = damaged["tables"]["word_spelling_attempt"][0]
        evidence = json.loads(row["evidence_json"])
        if mutation == "evidence_parts": evidence["romaji_parts"][0] = "wrong"
        elif mutation == "evidence_plan": evidence["romaji_unit_plan"]["version"] = 100
        else: evidence["actual"] = "wrong"
        row["evidence_json"] = json.dumps(evidence)
    before_rollbacks = list(store.data_dir.glob("learning-before-restore-*.json"))
    with pytest.raises(ValueError): store.import_backup(encode_current(damaged))
    assert backup(store)["tables"] == original["tables"]
    assert list(store.data_dir.glob("learning-before-restore-*.json")) == before_rollbacks


def test_submit_requires_composed_text_and_stable_plan(store):
    q = question()
    plan, parts = parts_for(q)
    with pytest.raises(ValueError): check_answer(q, "kaka", word_romaji_parts=parts, word_romaji_plan=plan)
    modified = deepcopy(plan)
    modified["units"][0]["options"].reverse()
    with pytest.raises(ValueError): check_answer(q, q.answer, word_romaji_parts=parts, word_romaji_plan=modified)
    with pytest.raises(ValueError): check_answer(q, q.answer, word_romaji_plan=plan)


def test_same_composed_word_with_wrong_kana_boundaries_is_not_correct(store, tmp_path):
    q = question(kana="きゃ")
    plan, expected = parts_for(q)
    assert expected == ["k", "ya"]
    wrong = ["ky", "a"]
    for unit, value in zip(plan["units"], wrong):
        if value not in unit["options"]:
            index = next(i for i, option in enumerate(unit["options"]) if option != unit["answer"])
            unit["options"][index] = value
    q.metadata["romaji_unit_plan"] = deepcopy(plan)
    assert all(value in unit["options"] for value, unit in zip(wrong, plan["units"]))
    assert compose_romaji_parts(plan, wrong) == q.answer == "kya"
    assert check_answer(q, q.answer)  # Legacy whole-word records remain valid.
    assert not check_answer(q, q.answer, word_romaji_parts=wrong, word_romaji_plan=plan)
    sid, result = submit(store, q, wrong)
    row = store._db.execute("SELECT * FROM word_spelling_attempt WHERE session_id=?", (sid,)).fetchone()
    evidence = result["word_spelling_evidence"]
    assert row["correct"] == 0 and evidence["correct"] is False
    assert evidence["actual"] == evidence["expected_romaji"] == "kya"
    with StudyStore(tmp_path / "collision-restored", "UTC") as restored:
        restored.import_backup(store.export_backup())
        assert backup(restored)["tables"] == backup(store)["tables"]
