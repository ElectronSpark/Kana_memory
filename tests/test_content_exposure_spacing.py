"""Feedback delays sibling abilities, without moving their real FSRS schedule."""
from datetime import timedelta
import json

import pytest

from support.learning_store_fixtures import item, question, ready, START, stores
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore


def setup_pair(learning):
    learning.enroll([item(), item("other")], now=START)
    learning.append_questions([
        question(), question(qid="sibling", ability="application", kind="grammar_form"),
        question("other", "other-question")], now=START)
    ready(learning, "other")
    return ready(learning)


@pytest.mark.parametrize("outcome", ["correct", "incorrect", "revealed", "device_failure"])
def test_existing_sibling_waits_other_content_proceeds_and_real_schedule_unchanged(stores, outcome):
    store, learning = stores
    now = setup_pair(learning)
    sibling = learning.get_unit(item()["id"], "application")
    snapshot = learning.get_question("sibling")
    correct = True if outcome == "correct" else False if outcome == "incorrect" else None
    learning.submit("q1", "answer", correct, outcome=outcome, now=now)
    assert learning.current(now=now)["question"]["id"] == "q1"
    learning.finalize("q1", 3, now=now)
    assert learning.current(now=now)["question"]["id"] == "other-question"
    assert learning.counts(now=now)["waiting"] == 1
    assert learning.counts(now=now)["ready"] == 1
    with pytest.raises(ValueError, match="not currently due"):
        learning.submit("sibling", "answer", True, now=now + timedelta(seconds=59))
    assert learning.get_unit(item()["id"], "application") == sibling
    assert learning.get_question("sibling") == snapshot
    assert learning.current(now=now + timedelta(seconds=60))["question"]["id"] == "sibling"


def test_new_sibling_restart_backup_and_idempotency(stores, tmp_path):
    store, learning = stores
    learning.enroll(item(), now=START)
    learning.append_questions(question(), now=START)
    now = ready(learning)
    learning.submit("q1", "answer", True, now=now)
    saved_at = now + timedelta(minutes=3)
    first = learning.finalize("q1", 3, now=saved_at)
    learning.append_questions(question(qid="sibling", ability="application"), now=saved_at)
    due = saved_at + timedelta(seconds=60)
    assert learning.get_question("sibling")["not_before_utc"] == due.isoformat()
    assert learning.finalize("q1", 4, now=saved_at + timedelta(seconds=30)) == first
    data = store.export_backup()
    with StudyStore(tmp_path / "restored", time_zone="UTC") as restored:
        restored.import_backup(data)
        reopened = LearningStore(restored)
        assert reopened.current(now=due-timedelta(seconds=1))["type"] == "waiting"
        assert reopened.current(now=due)["question"]["id"] == "sibling"
        assert reopened.history() == learning.history()
        assert reopened.get_unit(item()["id"], "application")["review_count"] == 0
        assert reopened.content_exposure_times(now=due) == {}


@pytest.mark.parametrize("draft", [{"text": "typed"}, {"parts": ["form"]}])
def test_existing_real_draft_is_not_discarded_or_hidden_by_sibling_feedback(stores, draft):
    _, learning = stores
    now = setup_pair(learning)
    learning.save_draft("sibling", draft)
    learning.submit("q1", "answer", True, now=now)
    learning.finalize("q1", 3, now=now)
    assert learning.current(now=now)["question"]["id"] == "sibling"
    assert learning.get_question("sibling")["draft"] == draft
    assert learning.counts(now=now)["waiting"] == 0


def test_empty_controls_and_audio_flag_do_not_bypass_exposure_or_extend_it(stores):
    store, learning = stores
    now = setup_pair(learning)
    learning.save_draft("sibling", {"text": " ", "parts": [], "audio_ok": True})
    learning.submit("q1", "answer", True, now=now)
    learning.finalize("q1", 3, now=now)
    before = [tuple(row) for row in store._db.execute("SELECT * FROM learning_content")]
    for _ in range(3):
        assert learning.current(enabled_kinds=["grammar_form"], now=now)["type"] == "waiting"
        assert learning.counts(enabled_kinds=["grammar_form"], now=now)["waiting"] == 1
    assert [tuple(row) for row in store._db.execute("SELECT * FROM learning_content")] == before
    assert learning.current(enabled_kinds=["grammar_form"], now=now+timedelta(seconds=60))["question"]["id"] == "sibling"
