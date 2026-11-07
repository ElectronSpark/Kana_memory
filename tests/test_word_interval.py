"""Auxiliary word evidence changes due dates without inventing FSRS reviews."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from record_test_support import decode_current

from kanamemory.domain import Question, StudyStore, WORD_ROMAJI_POLICY
from kanamemory.word_interval import (
    VISUAL_INTERVAL_SKILLS, WORD_INTERVAL_POLICY, WORD_INTERVAL_WEIGHT,
    apply_word_interval_adjustments, calculate_interval_due,
)

from support.word_interval_support import BASE, NOW, KA, KI, store, mature, evidence, apply, due, unchanged_except_due


@pytest.mark.parametrize("result,factor", [("correct", 1.25), ("incorrect", .75)])
def test_pure_calculation_moves_real_interval_by_quarter(result, factor):
    answer = calculate_interval_due(base_due=BASE + timedelta(days=8),
        base_last_review=BASE, before_due=BASE + timedelta(days=8), result=result, now=NOW)
    assert answer == BASE + timedelta(days=8 * factor)


@pytest.mark.parametrize("offset,result", [(10, "correct"), (6, "incorrect")])
def test_pure_calculation_saturates_instead_of_compounding(offset, result):
    assert calculate_interval_due(base_due=BASE + timedelta(days=8),
        base_last_review=BASE, before_due=BASE + timedelta(days=offset), result=result, now=NOW) is None


@pytest.mark.parametrize("result", ["correct", "incorrect"])
def test_due_or_overdue_card_is_never_rescheduled(result):
    assert calculate_interval_due(base_due=NOW, base_last_review=BASE,
        before_due=NOW, result=result, now=NOW) is None


def test_wrong_answer_can_make_upcoming_card_due_now_but_never_in_past():
    later = BASE + timedelta(days=7)
    assert calculate_interval_due(base_due=BASE + timedelta(days=8),
        base_last_review=BASE, before_due=BASE + timedelta(days=8),
        result="incorrect", now=later) == later


@pytest.mark.parametrize("changes", [
    {"before_due": BASE + timedelta(days=20)},
    {"base_due": BASE}, {"now": BASE - timedelta(seconds=1)},
])
def test_unknown_schedule_or_clock_reversal_is_not_overwritten(changes):
    args = dict(base_due=BASE + timedelta(days=8), base_last_review=BASE,
        before_due=BASE + timedelta(days=8), result="correct", now=NOW)
    args.update(changes)
    assert calculate_interval_due(**args) is None


def test_invalid_result_or_naive_time_is_rejected():
    args = dict(base_due=BASE + timedelta(days=8), base_last_review=BASE,
        before_due=BASE + timedelta(days=8), result="correct", now=NOW)
    with pytest.raises(ValueError): calculate_interval_due(**{**args, "result": "easy"})
    with pytest.raises(ValueError): calculate_interval_due(**{**args, "now": NOW.replace(tzinfo=None)})


@pytest.mark.parametrize("result,sign", [("correct", 1), ("incorrect", -1)])
def test_real_mature_card_changes_due_only_and_audits_real_anchor(store, result, sign):
    before = mature(store)
    history = store.review_history()
    adjustment, = apply(store, evidence(result=result))
    after = store.get_card(KA, "romaji_kana", "hiragana")
    interval = due(before) - BASE
    assert due(after) == due(before) + interval * .25 * sign
    unchanged_except_due(before, after)
    assert store.review_history() == history
    assert store.stats(NOW)["total_cards"] == 1
    assert store._db.execute("SELECT event_id FROM learning_evidence WHERE event_id=?", (adjustment["evidence_event_id"],)).fetchone()
    assert {k: v for k, v in adjustment.items() if k != "evidence_event_id"} == {"policy": WORD_INTERVAL_POLICY, "weight": WORD_INTERVAL_WEIGHT,
        "card_id": before["card_id"], "content_id": KA, "skill": "romaji_kana",
        "script": "hiragana", "anchor_event_id": history[-1]["event_id"],
        "result": result, "before_due_at_utc": before["due_at_utc"],
        "after_due_at_utc": after["due_at_utc"], "base_due_at_utc": before["due_at_utc"],
        "base_interval_seconds": interval.total_seconds()}


def test_four_visual_skills_share_only_same_script_existing_kana_evidence(store):
    cards = [mature(store, skill=skill) for skill in VISUAL_INTERVAL_SKILLS]
    protected = [mature(store, skill="kana_handwriting"), mature(store, skill="romaji_handwriting"),
                 mature(store, script="katakana"), mature(store, cid=KI)]
    history = store.review_history()
    adjustments = apply(store)
    assert {a["card_id"] for a in adjustments} == {c["card_id"] for c in cards}
    for card in protected:
        assert store.get_card(card["content_id"], card["skill"], card["script"]) == card
    assert store.review_history() == history
    assert store.get_card("word:one", "word_meaning_kana", "hiragana") is None


@pytest.mark.parametrize("symbol", ["ゃ", "ャ", "っ", "ッ", "ん", "ン", "ー"])
def test_shared_rule_sign_never_reschedules_its_historical_glyph_card(store, symbol):
    script = "katakana" if symbol in "ャッンー" else "hiragana"
    cid = "kana-" + format(ord(symbol), "04x")
    before = mature(store, cid=cid, script=script)
    history = store.review_history()
    spelling = evidence(symbol, cid=cid, script=script)
    assert apply(store, spelling) == []
    assert store.get_card(cid, "romaji_kana", script) == before
    assert store.review_history() == history
    assert store._db.execute("SELECT COUNT(*) FROM learning_aux_adjustment").fetchone()[0] == 0


def test_retired_sign_old_history_and_pending_word_survive_backup_and_duplicate_submit(store, tmp_path):
    cid = "kana-3063"
    mature(store, cid=cid)
    q = Question("word:short-stop", "word_meaning_kana", "hiragana", "romaji", "かっか",
                 metadata={"word_romaji_drill": True})
    rows = [{"id": KA, "hiragana": "か", "katakana": "カ"},
            {"id": cid, "hiragana": "っ", "katakana": "ッ"}]
    sid = store.start_session([q], now=NOW)
    store.save_session_progress(sid, 0, state={"romaji_answer": "か"})
    before = store.get_card(cid, "romaji_kana", "hiragana")
    args = dict(session_id=sid, question_index=0, answered_text=q.answer, outcome="correct",
                kana_rows=rows, now=NOW)
    result = store.record_review(q.content_id, q.kind, q.script, None, **args)
    assert result["word_spelling_evidence"]["schedule_adjustments"] == []
    frozen = decode_current(store.export_backup())["tables"]
    replay = store.record_review(q.content_id, q.kind, q.script, None, **args)
    assert replay["already_recorded"]
    assert decode_current(store.export_backup())["tables"] == frozen
    with StudyStore(tmp_path / "restored-retired", "UTC") as restored:
        restored.import_backup(store.export_backup())
        assert restored.get_card(cid, "romaji_kana", "hiragana") == before
        assert decode_current(restored.export_backup())["tables"] == frozen


@pytest.mark.parametrize("control", ["suspended", "buried", "direction"])
def test_paused_or_deferred_items_are_untouched(store, control):
    card = mature(store)
    if control == "direction":
        with store._db:
            store._db.execute("UPDATE study_card SET suspended=1 WHERE card_id=?", (card["card_id"],))
    elif control == "suspended":
        store.set_content_control(KA, suspended=True)
    else:
        store.set_content_control(KA, buried_until_utc=NOW + timedelta(days=2))
    before = store.get_card(KA, "romaji_kana", "hiragana")
    assert apply(store) == []
    assert store.get_card(KA, "romaji_kana", "hiragana") == before


@pytest.mark.parametrize("relearning", [False, True])
def test_learning_and_relearning_steps_and_pending_drafts_are_unchanged(store, relearning):
    if relearning: mature(store)
    store.record_review(KA, "romaji_kana", "hiragana", 1, now=NOW)
    before = store.get_card(KA, "romaji_kana", "hiragana")
    q = Question(KA, "romaji_kana", "hiragana", "ka", "か",
        metadata={"not_before_utc": before["due_at_utc"]})
    sid = store.start_session([q], now=NOW)
    store.save_session_progress(sid, 0, state={"word_answer": "か"})
    checkpoint = store.load_session_progress("main")
    assert apply(store, now=NOW + timedelta(seconds=1)) == []
    assert store.get_card(KA, "romaji_kana", "hiragana") == before
    assert store.load_session_progress("main") == checkpoint


def test_no_prior_card_or_missing_anchor_does_not_create_or_manufacture_state(store):
    assert apply(store) == []
    assert store.stats(NOW)["total_cards"] == 0
    card = mature(store)
    with store._db: store._db.execute("DELETE FROM review_event WHERE card_id=?", (card["card_id"],))
    assert apply(store) == []
    assert store.get_card(KA, "romaji_kana", "hiragana") == card


def test_same_normalized_spelling_different_word_id_cannot_reverse_first_evidence(store):
    before = mature(store)
    assert apply(store, evidence(result="incorrect"), word_id="word:one")
    lower = store.get_card(KA, "romaji_kana", "hiragana")
    assert apply(store, evidence(" か "), word_id="word:another", now=NOW + timedelta(seconds=1)) == []
    assert store.get_card(KA, "romaji_kana", "hiragana") == lower
    assert due(lower) < due(before)


def test_different_words_cannot_recycle_absolute_quarter_budget_by_reversing_direction(store):
    before = mature(store)
    for i in range(20):
        result = apply(store, evidence("か" * (i + 1)), word_id=f"word:{i}",
            now=NOW + timedelta(seconds=i))
        assert bool(result) is (i == 0)
    upper = store.get_card(KA, "romaji_kana", "hiragana")
    assert due(upper) == due(before) + (due(before) - BASE) * .25
    assert apply(store, evidence("かき", result="incorrect"), now=NOW + timedelta(minutes=1)) == []
    assert due(store.get_card(KA, "romaji_kana", "hiragana")) == due(upper)
    # Capped attempts remain observable without further auxiliary movement.
    assert apply(store, evidence("かか"), now=NOW + timedelta(minutes=2)) == []
    assert due(store.get_card(KA, "romaji_kana", "hiragana")) == due(upper)
    assert store._db.execute("SELECT COUNT(*) FROM learning_evidence WHERE source='word' AND outcome='incorrect'").fetchone()[0] == 1


def test_new_independent_review_resets_anchor_and_allows_same_reading_again(store):
    mature(store)
    first, = apply(store)
    store.record_review(KA, "romaji_kana", "hiragana", 3, now=NOW, outcome="correct")
    before = store.get_card(KA, "romaji_kana", "hiragana")
    second, = apply(store, now=NOW)
    assert first["anchor_event_id"] != second["anchor_event_id"]
    assert second["base_due_at_utc"] == before["due_at_utc"]
    assert second["before_due_at_utc"] == before["due_at_utc"]
    assert store.stats(NOW)["total_reviews"] == 2


@pytest.mark.parametrize("change", [
    {"assisted": True}, {"accepted_alternative": True}, {"units": []},
    {"practice_policy": "legacy"}, {"script": "katakana"},
])
def test_nonindependent_or_unattributable_evidence_is_ignored(store, change):
    before = mature(store)
    spelling = {**evidence(), **change}
    assert apply(store, spelling) == []
    assert store.get_card(KA, "romaji_kana", "hiragana") == before
    assert apply(store, now=NOW + timedelta(seconds=1))


def test_mixed_word_updates_only_definite_units_and_repeated_kana_once(store):
    left, right = mature(store), mature(store, cid=KI)
    spelling = evidence("かきか")
    spelling["units"] = [spelling["units"][0],
        {"content_id": KI, "symbol": "き", "result": "incorrect", "error_types": ["missing"]},
        spelling["units"][0]]
    spelling["ambiguous"] = True  # Remaining definite units still have evidence.
    assert len(apply(store, spelling)) == 2
    assert due(store.get_card(KA, "romaji_kana", "hiragana")) > due(left)
    assert due(store.get_card(KI, "romaji_kana", "hiragana")) < due(right)


def test_word_save_failure_rolls_back_all_due_changes_and_dedup_reservation(store):
    before = mature(store)
    with pytest.raises(ValueError, match="deliberate"):
        apply(store, rollback=True)
    assert store.get_card(KA, "romaji_kana", "hiragana") == before
    assert store._db.execute("SELECT COUNT(*) FROM word_spelling_attempt").fetchone()[0] == 0
    assert apply(store)


def test_helper_requires_callers_transaction_and_does_not_commit_it(store):
    mature(store)
    with pytest.raises(RuntimeError, match="transaction"):
        apply_word_interval_adjustments(store, evidence(), "hiragana", NOW)
    store._db.execute("BEGIN IMMEDIATE")
    assert apply_word_interval_adjustments(store, evidence(), "hiragana", NOW)
    assert store._db.in_transaction
    store._db.rollback()


def _single_kana_repository():
    from support.domain_support import SmallContent
    repository = SmallContent()
    repository.kana = [row for row in repository.kana if row["hiragana"] == "か"]
    repository.kana[0]["id"] = KA
    return repository


def _submit_actual_word(store, repository, answer):
    question = Question("word:ka", "word_meaning_kana", "hiragana", "ka", "か",
        metadata={"word_romaji_drill": True})
    sid = store.start_session([question], now=NOW)
    store.save_session_progress(sid, 0)
    return store.record_review(question.content_id, question.kind, question.script,
        None, session_id=sid, question_index=0, answered_text=answer,
        outcome="correct" if answer == "か" else "incorrect",
        kana_rows=repository.kana_list(), now=NOW)


def _subsequent_spaced_batch(store, repository, now):
    from kanamemory.domain import build_practice_batch
    return build_practice_batch(repository, store, kinds=["romaji_kana"],
        script="hiragana", limit=1, schedule_mode="spaced", now=now)


def test_subsequent_spaced_batch_uses_extended_due_and_preserves_pending_queue(store):
    repository = _single_kana_repository()
    original = mature(store)
    waiting_until = BASE + timedelta(days=100)
    main_question = Question(KA, "romaji_kana", "hiragana", "ka", "か",
        metadata={"not_before_utc": waiting_until.isoformat()})
    main_sid = store.start_session([main_question], now=BASE)
    store.save_session_progress(main_sid, 0, state={"page": "session_wait"})
    pending_before = store.load_session_progress("main")

    result = _submit_actual_word(store, repository, "か")
    extended_due = due(store.get_card(KA, "romaji_kana", "hiragana"))
    assert result["word_spelling_evidence"]["schedule_adjustments"]
    assert extended_due > due(original)
    assert _subsequent_spaced_batch(store, repository, due(original)) == []
    assert _subsequent_spaced_batch(store, repository, extended_due - timedelta(microseconds=1)) == []
    assert [q.content_id for q in _subsequent_spaced_batch(store, repository, extended_due)] == [KA]
    assert store.load_session_progress("main") == pending_before
    resumed = store.prepare_next_question(main_sid, now=NOW)
    assert resumed["waiting_until_utc"] == waiting_until.isoformat()
    assert resumed["questions"] == pending_before["questions"]
    assert resumed["question_index"] == pending_before["question_index"]


def test_subsequent_spaced_batch_uses_shortened_due_without_word_memory_card(store):
    repository = _single_kana_repository()
    original = mature(store)
    result = _submit_actual_word(store, repository, "き")
    shortened_due = due(store.get_card(KA, "romaji_kana", "hiragana"))
    assert result["word_spelling_evidence"]["schedule_adjustments"]
    assert NOW < shortened_due < due(original)
    assert _subsequent_spaced_batch(store, repository, shortened_due - timedelta(microseconds=1)) == []
    questions = _subsequent_spaced_batch(store, repository, shortened_due)
    assert [(q.content_id, q.kind, q.script) for q in questions] == [(KA, "romaji_kana", "hiragana")]
    assert store.get_card("word:ka", "word_meaning_kana", "hiragana") is None
    assert store.stats(shortened_due)["total_reviews"] == 1
