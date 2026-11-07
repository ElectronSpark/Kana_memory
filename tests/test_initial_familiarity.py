"""Self-reported prior knowledge changes teaching, not measured memory."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest
from record_test_support import decode_current, encode_current

from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore, validate_backup_rows

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def lesson(cid="word:test:meaning-one", family="word", **extra):
    abilities = ["understanding", "reading"] if family == "word" else ["understanding", "application"]
    return {"id": cid, "family": family, "revision": "1", "abilities": abilities,
            "instruction": {"meaning": "正在做", "rule": "て形＋います"}, **extra}


def question(cid="word:test:meaning-one", qid="meaning", ability="understanding"):
    return {"id": qid, "target_id": cid, "ability": ability,
            "kind": "vocab_meaning" if ability == "understanding" else "vocab_reading",
            "prompt": "意味", "answer": "含义", "input_mode": "choice", "revision": "1"}


@pytest.fixture
def stores(tmp_path):
    with StudyStore(tmp_path / "learner", time_zone="UTC") as store:
        learning = LearningStore(store)
        learning.enroll(lesson(), now=NOW)
        learning.append_questions([question(), question(qid="reading", ability="reading")], now=NOW)
        yield store, learning


def levels(understanding="familiar", reading="familiar"):
    return {"understanding": understanding, "reading": reading}


def scheduler_rows(learning):
    return [tuple(row) for row in learning._db.execute("""SELECT unit_id,scheduler_state_json,
        due_at_utc,review_count,probe_due_at_utc,last_unassisted_success_at_utc FROM learning_unit ORDER BY rowid""")]


def frozen_questions(learning):
    return [tuple(row) for row in learning._db.execute("""SELECT question_id,snapshot_json,draft_json,
        result_json,status,not_before_utc FROM learning_question ORDER BY rowid""")]


@pytest.mark.parametrize("selected,route", [(levels("new", "familiar"), "full"),
    (levels("familiar", "new"), "full"), (levels("some", "familiar"), "compact"), (levels(), "diagnostic")])
def test_routes_keep_distinct_abilities_and_never_create_memory(stores, selected, route):
    store, learning = stores
    before, questions = scheduler_rows(learning), frozen_questions(learning)
    original = learning.get_item(lesson()["id"])
    item = learning.set_initial_familiarity(original["id"], selected,
        expected_introduction_id=original["introduction_id"], now=NOW)
    profile = item["checkpoint"]["initial_familiarity"]
    assert profile["levels"] == selected and profile["route"] == route
    assert profile["evidence"] == "self_report"
    assert scheduler_rows(learning) == before and frozen_questions(learning) == questions
    assert learning.history() == []
    assert learning.current(now=NOW)["type"] == ("question" if route == "diagnostic" else "instruction")
    assert item["stage"] != "done"
    assert not store._db.execute("SELECT 1 FROM study_card").fetchone()


def test_replayed_selection_and_exposure_never_reset_saved_due_or_extend_wait(stores):
    _, learning = stores
    cid = lesson()["id"]
    selected = levels("new", "familiar")
    learning.set_initial_familiarity(cid, selected, now=NOW)
    learning.mark_instruction_exposed(cid, now=NOW)
    learning.advance_instruction(cid, "intro_done", now=NOW)
    learning.advance_instruction(cid, "guide_correct", now=NOW)
    snapshot = learning.get_item(cid)
    learning.mark_instruction_exposed(cid, now=NOW + timedelta(hours=1))
    assert learning.set_initial_familiarity(cid, selected, now=NOW + timedelta(hours=1)) == snapshot
    assert learning.current(now=NOW)["type"] == "waiting"
    assert learning.current(now=NOW + timedelta(seconds=60))["type"] == "question"
    with pytest.raises(ValueError, match="already been saved"):
        learning.set_initial_familiarity(cid, levels(), now=NOW)


@pytest.mark.parametrize("condition", ["legacy", "exposed"])
def test_old_or_previously_exposed_intro_cannot_be_an_unassisted_diagnostic(stores, condition):
    _, learning = stores
    cid = lesson()["id"]
    if condition == "legacy":
        with learning._db:
            learning._db.execute("UPDATE learning_content SET checkpoint_json='{}' WHERE content_id=?", (cid,))
    else:
        learning.mark_instruction_exposed(cid, now=NOW)
    result = learning.set_initial_familiarity(cid, levels(), now=NOW)
    assert result["checkpoint"]["initial_familiarity"]["route"] == "compact"
    assert learning.current(now=NOW)["type"] == "instruction"


@pytest.mark.parametrize("selected", [{}, {"understanding": "familiar"},
    {"understanding": "familiar", "reading": "familiar", "listening": "familiar"},
    {"understanding": "familiar", "reading": 3}, {"understanding": "familiar", "reading": []}])
def test_invalid_or_incomplete_ability_report_is_atomic(stores, selected):
    _, learning = stores
    before = learning.get_item(lesson()["id"])
    with pytest.raises(ValueError):
        learning.set_initial_familiarity(lesson()["id"], selected, now=NOW)
    assert learning.get_item(lesson()["id"]) == before


@pytest.mark.parametrize("existing", ["draft", "guide", "stale"])
def test_existing_progress_and_old_callback_are_preserved(stores, existing):
    _, learning = stores
    cid = lesson()["id"]
    if existing == "draft":
        learning.save_draft("meaning", {"answer": "saved"})
    elif existing == "guide":
        learning.advance_instruction(cid, "intro_done", now=NOW)
    before, pending = learning.get_item(cid), frozen_questions(learning)
    with pytest.raises(ValueError):
        learning.set_initial_familiarity(cid, levels(), now=NOW,
            expected_introduction_id="stale" if existing == "stale" else None)
    assert learning.get_item(cid) == before and frozen_questions(learning) == pending


def test_success_only_schedules_tested_ability_and_same_report_cannot_postpone_it(stores):
    _, learning = stores
    cid = lesson()["id"]
    learning.set_initial_familiarity(cid, levels(), now=NOW)
    answer = learning.submit("meaning", "含义", True, now=NOW)
    assert answer["initial_diagnostic"] and not answer.get("diagnostic_needs_teaching")
    assert learning.get_unit(cid, "understanding")["review_count"] == 0
    result = learning.finalize("meaning", 3, now=NOW)
    before = scheduler_rows(learning)
    learning.set_initial_familiarity(cid, levels(), now=NOW + timedelta(days=1))
    assert scheduler_rows(learning) == before
    assert learning.finalize("meaning", 4, now=NOW + timedelta(days=1)) == result
    assert learning.get_unit(cid, "reading")["review_count"] == 0
    assert learning.current(now=NOW)["type"] == "waiting"
    after_feedback = NOW + timedelta(seconds=60)
    assert learning.current(now=after_feedback)["question"]["id"] == "reading"
    assert learning.submit("reading", "含义", True, now=after_feedback)["initial_diagnostic"]


@pytest.mark.parametrize("failure", ["incorrect", "revealed", "hint"])
def test_failed_diagnostic_returns_to_teaching_without_fake_lapse(stores, failure):
    _, learning = stores
    cid = lesson()["id"]
    learning.set_initial_familiarity(cid, levels(), now=NOW)
    if failure == "hint":
        learning.save_draft("meaning", {"hint_used": True})
    answer = learning.submit("meaning", "wrong", failure == "hint", now=NOW,
        outcome="revealed" if failure == "revealed" else None)
    assert answer["outcome"] == "unresolved" and answer["diagnostic_needs_teaching"]
    assert learning.get_item(cid)["stage"] == "intro"
    assert learning.get_item(cid)["checkpoint"]["initial_familiarity"]["route"] == "full"
    assert learning.current(now=NOW)["result"] == answer
    assert learning.counts(now=NOW)["ready"] == 1
    learning.finalize("meaning", 1, now=NOW)
    assert learning.get_unit(cid, "understanding")["scheduler_state"] is None
    assert learning.get_unit(cid, "understanding")["review_count"] == 0
    assert learning.current(now=NOW)["type"] == "instruction"
    learning.append_questions(question(qid="meaning-retry"), now=NOW)
    learning.mark_instruction_exposed(cid, now=NOW)
    learning.advance_instruction(cid, "intro_done", now=NOW)
    learning.advance_instruction(cid, "guide_correct", now=NOW)
    assert learning.current(now=NOW)["type"] == "waiting"
    after = NOW + timedelta(seconds=61)
    retry = learning.submit("meaning-retry", "含义", True, now=after)
    assert not retry.get("initial_diagnostic")
    assert learning.finalize("meaning-retry", 3, now=after)["review_count"] == 1


def test_one_diagnostic_failure_cannot_erase_another_ability_success(stores):
    _, learning = stores
    cid = lesson()["id"]
    learning.set_initial_familiarity(cid, levels(), now=NOW)
    learning.submit("meaning", "含义", True, now=NOW)
    result = learning.finalize("meaning", 3, now=NOW)
    known = learning.get_unit(cid, "understanding")
    after_feedback = NOW + timedelta(seconds=60)
    learning.submit("reading", "wrong", False, now=after_feedback)
    learning.finalize("reading", 1, now=after_feedback)
    assert learning.get_unit(cid, "understanding") == known
    assert learning.finalize("meaning", 4, now=NOW + timedelta(days=1)) == result
    assert learning.get_unit(cid, "reading")["review_count"] == 0
    assert learning.get_item(cid)["stage"] == "intro"
    validate_backup_rows(learning._db)


def test_diagnostic_does_not_bypass_disabled_question_or_prerequisites(stores):
    store, learning = stores
    cid = lesson()["id"]
    learning.set_initial_familiarity(cid, levels(), now=NOW)
    store.set_setting("learning_preferences_v1", {"enabled": []})
    with pytest.raises(ValueError, match="disabled"):
        learning.submit("meaning", "含义", True, now=NOW)
    store.set_setting("learning_preferences_v1", {"enabled": ["vocab_meaning"]})
    item = lesson("dependent", depends_on=[cid])
    learning.enroll(item, now=NOW)
    learning.append_questions(question("dependent", "dependent-q"), now=NOW)
    learning.set_initial_familiarity("dependent", levels(), now=NOW)
    with pytest.raises(ValueError, match="前置"):
        learning.submit("dependent-q", "含义", True, now=NOW)


@pytest.mark.parametrize("family,domain", [("grammar", None), ("grammar", "pronunciation")])
def test_grammar_and_reading_rules_have_separate_understanding_and_application(stores, family, domain):
    _, learning = stores
    item = lesson("grammar-rule", family=family, **({"domain": domain} if domain else {}))
    learning.enroll(item, now=NOW)
    result = learning.set_initial_familiarity(item["id"], {"understanding": "familiar", "application": "new"}, now=NOW)
    assert result["checkpoint"]["initial_familiarity"]["route"] == "full"
    assert all(unit["scheduler_state"] is None for unit in result["units"])


def test_frozen_failed_feedback_and_report_survive_reopen_and_backup(stores, tmp_path):
    store, learning = stores
    cid = lesson()["id"]
    learning.set_initial_familiarity(cid, levels(), now=NOW)
    answer = learning.submit("meaning", "wrong", False, now=NOW)
    item, questions = learning.get_item(cid), frozen_questions(learning)
    with StudyStore(tmp_path / "restored", time_zone="UTC") as restored:
        restored.import_backup(store.export_backup())
        other = LearningStore(restored)
        assert other.get_item(cid) == item and frozen_questions(other) == questions
        assert other.current(now=NOW)["result"] == answer
        other.finalize("meaning", 1, now=NOW)
        assert other.current(now=NOW)["type"] == "instruction"


def test_malformed_self_report_backup_rejected_without_touching_state(stores):
    store, learning = stores
    cid = lesson()["id"]
    learning.set_initial_familiarity(cid, levels(), now=NOW)
    before = store.export_backup()
    payload = decode_current(before)
    row = payload["tables"]["learning_content"][0]
    checkpoint = json.loads(row["checkpoint_json"])
    checkpoint["initial_familiarity"]["evidence"] = "successful_review"
    row["checkpoint_json"] = json.dumps(checkpoint)
    with pytest.raises(ValueError):
        store.import_backup(encode_current(payload))
    assert decode_current(store.export_backup())["tables"] == decode_current(before)["tables"]


def title_rule(learning, when=NOW):
    item = lesson("pronunciation:title-discloses-wa", family="grammar", domain="pronunciation",
                  title="助词は：读wa，仍写は")
    learning.enroll(item, now=when)
    q = {**question(item["id"], "title-rule", "understanding"), "kind": "pronunciation_rule"}
    learning.append_questions(q, now=when)
    return item["id"], {"understanding": "familiar", "application": "familiar"}


def test_answer_bearing_rule_title_skips_guide_but_defers_diagnostic_once(stores):
    _, learning = stores
    cid, selected = title_rule(learning)
    before, questions = scheduler_rows(learning), frozen_questions(learning)
    item = learning.set_initial_familiarity(cid, selected, title_answer_exposed=True, now=NOW)
    profile = item["checkpoint"]["initial_familiarity"]
    assert item["stage"] == "bridge" and profile["route"] == "diagnostic"
    assert profile["title_answer_exposed"] is True and profile["diagnostic_state"] == "active"
    assert item["retrieval_at_utc"] == (NOW + timedelta(seconds=60)).isoformat()
    assert "guide_answer" not in item["checkpoint"]
    assert scheduler_rows(learning) == before and frozen_questions(learning) == questions
    assert learning.history() == []
    assert learning.current({"pronunciation_rule"}, now=NOW)["type"] == "waiting"
    assert learning.counts({"pronunciation_rule"}, now=NOW)["waiting"] == 1
    with pytest.raises(ValueError, match="Complete teaching"):
        learning.submit("title-rule", "含义", True, now=NOW)
    assert learning.set_initial_familiarity(cid, selected, title_answer_exposed=True,
                                            now=NOW + timedelta(seconds=50)) == item
    assert learning.current({"pronunciation_rule"}, now=NOW + timedelta(seconds=59))["type"] == "waiting"
    assert learning.current({"pronunciation_rule"}, now=NOW + timedelta(seconds=60))["type"] == "question"
    answer = learning.submit("title-rule", "含义", True, now=NOW + timedelta(seconds=60))
    assert answer["initial_diagnostic"] and learning.get_unit(cid, "understanding")["review_count"] == 0


def test_other_independent_retrieval_can_interleave_before_title_diagnostic(stores):
    _, learning = stores
    other = lesson()["id"]
    learning.advance_instruction(other, "intro_done", now=NOW)
    learning.advance_instruction(other, "guide_correct", now=NOW)
    selected_at = NOW + timedelta(minutes=5)
    cid, selected = title_rule(learning, when=selected_at)
    learning.set_initial_familiarity(cid, selected, title_answer_exposed=True, now=selected_at)
    assert learning.current({"pronunciation_rule"}, now=selected_at)["type"] == "waiting"
    # A self-report alone is not an intervening retrieval event.
    learning.acknowledge_prerequisites(other, now=selected_at + timedelta(seconds=1))
    assert learning.current({"pronunciation_rule"}, now=selected_at + timedelta(seconds=1))["type"] == "waiting"
    learning.submit("meaning", "含义", True, now=selected_at + timedelta(seconds=2))
    learning.finalize("meaning", 3, now=selected_at + timedelta(seconds=3))
    assert learning.current({"pronunciation_rule"}, now=selected_at + timedelta(seconds=4))["type"] == "question"


def test_failed_deferred_diagnostic_returns_to_intro_without_bridge_overwriting_it(stores):
    _, learning = stores
    cid, selected = title_rule(learning)
    learning.set_initial_familiarity(cid, selected, title_answer_exposed=True, now=NOW)
    at = NOW + timedelta(seconds=61)
    result = learning.submit("title-rule", "wrong", False, now=at)
    assert result["diagnostic_needs_teaching"]
    assert learning.get_item(cid)["stage"] == "intro"
    assert learning.current({"pronunciation_rule"}, now=at)["result"] == result
    learning.finalize("title-rule", 1, now=at)
    assert learning.get_unit(cid, "understanding")["scheduler_state"] is None
    assert learning.get_unit(cid, "understanding")["review_count"] == 0
    learning.append_questions({**question(cid, "title-retry"), "kind": "pronunciation_rule"}, now=at)
    assert learning.current({"pronunciation_rule"}, now=at)["type"] == "instruction"


def test_title_delay_survives_backup_and_full_exposure_still_requires_teaching(stores, tmp_path):
    store, learning = stores
    cid, selected = title_rule(learning)
    original = learning.set_initial_familiarity(cid, selected, title_answer_exposed=True, now=NOW)
    with StudyStore(tmp_path / "restored-title", time_zone="UTC") as restored:
        restored.import_backup(store.export_backup())
        other = LearningStore(restored)
        assert other.get_item(cid) == original
        assert other.current({"pronunciation_rule"}, now=NOW + timedelta(seconds=30))["type"] == "waiting"
    # Full teaching exposure retains the older conservative compact route.
    word = lesson()["id"]
    learning.mark_instruction_exposed(word, now=NOW)
    item = learning.set_initial_familiarity(word, levels(), title_answer_exposed=True, now=NOW)
    assert item["stage"] == "intro" and item["checkpoint"]["initial_familiarity"]["route"] == "compact"


def test_title_delay_is_explicit_and_rejects_nonboolean_flags(stores):
    _, learning = stores
    cid = lesson()["id"]
    before = learning.get_item(cid)
    with pytest.raises(ValueError, match="boolean"):
        learning.set_initial_familiarity(cid, levels(), title_answer_exposed="yes", now=NOW)
    assert learning.get_item(cid) == before
    assert learning.set_initial_familiarity(cid, levels(), now=NOW)["stage"] == "check"
