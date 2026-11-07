"""V6 round-trips reverse auxiliary words while preserving legacy backups."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest
from record_test_support import decode_current, encode_current, as_legacy

from kanamemory.domain import Question, StudyStore

BASE = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
NOW = BASE + timedelta(hours=1)
KA, KI = "kana-304b", "kana-304d"
ROWS = [{"id": KA, "hiragana": "か", "katakana": "カ"},
        {"id": KI, "hiragana": "き", "katakana": "キ"}]


def question(content_id="word:kaki"):
    return Question(content_id, "word_romaji", "hiragana", "かき", "kaki",
        ["kaki", "kaka", "kiku", "kika"], metadata={
            "word_romaji_drill": True, "kana_answer": "かき",
            "practice_config": {"word_direction": "mixed"}})


def payload(store):
    return decode_current(store.export_backup())


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner", "UTC") as value:
        for cid in (KA, KI):
            value.record_review(cid, "romaji_kana", "hiragana", 4, outcome="correct", now=BASE)
        sid = value.start_session([question(), question("word:next")], now=NOW)
        value.save_session_progress(sid, 0)
        value.record_review("word:kaki", "word_romaji", "hiragana", None,
            session_id=sid, question_index=0, answered_text="kaka", outcome="incorrect",
            kana_rows=ROWS, now=NOW)
        yield value


def test_v6_restores_reverse_history_due_and_pending_choices_without_replaying(store, tmp_path):
    document = payload(store)
    assert document["version"] == 1
    before_due = store.get_card(KA, "romaji_kana", "hiragana")["due_at_utc"]
    with StudyStore(tmp_path / "restored", "UTC") as restored:
        result = restored.import_backup(store.export_backup())
        assert result["cards"] == result["reviews"] == 2
        assert result["word_exercises"] == 1
        assert payload(restored)["tables"] == document["tables"]
        assert restored.load_session_progress("word_romaji") == store.load_session_progress("word_romaji")
        restored.import_backup(restored.export_backup())
        assert restored.get_card(KA, "romaji_kana", "hiragana")["due_at_utc"] == before_due
        assert restored.get_card("word:kaki", "word_romaji", "hiragana") is None


@pytest.mark.parametrize("mutation", [
    "downgrade", "kana_identity", "romaji_answer", "wrong_script", "main_lane",
    "evidence_expected", "evidence_direction", "evidence_romaji", "missing_direction",
    "wrong_correct", "duplicate_options", "independent_card", "direction_config",
])
def test_malformed_reverse_backup_rejected_without_mutating_live_data(store, mutation):
    original = payload(store)
    document = deepcopy(original)
    sid = document["tables"]["study_session"][-1]['session_id']
    question_row = next(r for r in document['tables']['session_question'] if r['session_id']==sid and r['question_index']==0)
    q = json.loads(question_row['snapshot_json'])
    row = document["tables"]["word_spelling_attempt"][0]
    evidence = json.loads(row["evidence_json"])
    if mutation == "downgrade": document["version"] = 5
    elif mutation == "kana_identity": q["metadata"]["kana_answer"] = "きく"
    elif mutation == "romaji_answer": q["answer"] = "kaka"
    elif mutation == "wrong_script": q["metadata"]["kana_answer"] = "カキ"
    elif mutation == "main_lane": q["metadata"]["word_romaji_drill"] = False
    elif mutation == "evidence_expected": evidence["expected"] = "kaki"
    elif mutation == "evidence_direction": evidence["answer_direction"] = "kana"
    elif mutation == "evidence_romaji": evidence["expected_romaji"] = "kaka"
    elif mutation == "missing_direction": del evidence["answer_direction"]
    elif mutation == "wrong_correct": evidence["correct"] = True
    elif mutation == "duplicate_options": q["options"] = ["kaki", "kaki"]
    elif mutation == "independent_card": document["tables"]["study_card"][0]["skill"] = "word_romaji"
    elif mutation == "direction_config": q["metadata"]["practice_config"]["word_direction"] = "bad"
    row["evidence_json"] = json.dumps(evidence)
    question_row['snapshot_json'] = json.dumps(q)
    rollbacks = list(store.data_dir.glob("learning-before-restore-*.json"))
    with pytest.raises(ValueError):
        store.import_backup(encode_current(document))
    assert payload(store)["tables"] == original["tables"]
    assert list(store.data_dir.glob("learning-before-restore-*.json")) == rollbacks


def test_pending_reverse_and_direction_only_preferences_use_current_schema(tmp_path):
    with StudyStore(tmp_path / "store", "UTC") as store:
        assert payload(store)["version"] == 1
        store.set_setting("word_drill_preferences", {"direction": "罗马音 → 假名"})
        document = payload(store)
        assert document["version"] == 1
        store.inspect_backup(store.export_backup())
        document["version"] = 5
        with pytest.raises(ValueError): store.inspect_backup(encode_current(document))
        store.set_setting("word_drill_preferences", {"direction": "bad"})
        with pytest.raises(ValueError): store.inspect_backup(store.export_backup())
        store.set_setting("word_drill_preferences", {})
        sid = store.start_session([question()], now=NOW)
        store.save_session_progress(sid, 0)
        assert payload(store)["version"] == 1
        store.inspect_backup(store.export_backup())


def test_forward_without_direction_uses_current_schema_and_restores_draft(tmp_path):
    with StudyStore(tmp_path / "store", "UTC") as store:
        old = Question("word:old", "word_meaning_kana", "hiragana", "kaki", "かき",
                       metadata={"word_romaji_drill": True})
        sid = store.start_session([old], now=NOW)
        store.save_session_progress(sid, 0, state={"word_answer": "か"})
        document = payload(store)
        assert document["version"] == 1
        with StudyStore(tmp_path / "restored", "UTC") as restored:
            restored.import_backup(store.export_backup())
            assert payload(restored)["tables"] == document["tables"]
            assert restored.load_session_progress("word_romaji")["state"] == {"word_answer": "か"}
