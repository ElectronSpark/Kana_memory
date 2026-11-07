"""Exercise state and historical settings survive backups without changing scores."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest
from support.session_snapshots import unrouted
from record_test_support import decode_current, encode_current, as_legacy

from kanamemory.domain import Question, StudyStore, check_answer
from kanamemory.progress_backup import validate_session_state
from kanamemory.word_keyboard import normalize_word_question_snapshot


@pytest.mark.parametrize("kind,answer,state", [
    ("kana_romaji", "shi", {"romaji_answer": "SH", "answer_revealed": False}),
    ("romaji_kana", "し", {"answer_revealed": True}),
    ("word_meaning_kana", "てれび", {"word_answer": "てれ", "question_audio_ok": True}),
])
def test_new_mode_checkpoint_and_budget_restore_and_reopen(tmp_path, kind, answer, state):
    question = Question("test-item", kind, "hiragana", "prompt", answer,
                        metadata={"audio_only": False, "romaji": "terebi"})
    with StudyStore(tmp_path / "source") as source, StudyStore(tmp_path / "target") as target:
        source.set_daily_new_limit(27)
        session = source.start_session([question])
        source.save_session_progress(session, 0, state=state)
        target.set_daily_new_limit(3)
        target.import_backup(source.export_backup())
        assert target.daily_new_limit == 27
        pending = target.load_session_progress()
        assert pending["state"] == state
        assert [item.to_dict() for item in unrouted(pending["questions"])] == [normalize_word_question_snapshot(question.to_dict())]
        assert list(map(tuple,target._db.execute("SELECT * FROM session_question ORDER BY rowid"))) == list(map(tuple,source._db.execute("SELECT * FROM session_question ORDER BY rowid")))
        target.record_review(question.content_id, kind, "hiragana", 1, session_id=session,
                             question_index=0, outcome="incorrect", assisted=True,
                             answered_text="", now=datetime.now(timezone.utc))
        assert target.load_session_progress() is None
        assert target.stats()["total_reviews"] == (0 if kind.startswith("word_") else 1)
        assert target.stats()["word_exercises_today"] == int(kind.startswith("word_"))
    with StudyStore(tmp_path / "target") as reopened:
        assert reopened.daily_new_limit == 27
        assert reopened.stats()["total_reviews"] == (0 if kind.startswith("word_") else 1)
        assert reopened.stats()["word_exercises_today"] == int(kind.startswith("word_"))


def test_import_older_backup_without_memory_limit_restores_default(tmp_path):
    with StudyStore(tmp_path / "old") as old, StudyStore(tmp_path / "target") as target:
        target.set_daily_new_limit(75)
        target.import_backup(old.export_backup())
        assert target.daily_new_limit == 10
        assert target.get_setting("daily_new_limit") is None


@pytest.mark.parametrize("value", [0, -1, True, "20", 2.5, None, {}, []])
def test_invalid_imported_memory_limit_cannot_replace_live_data(tmp_path, value):
    with StudyStore(tmp_path) as store:
        store.set_daily_new_limit(20)
        before = decode_current(store.export_backup())["tables"]
        payload = decode_current(store.export_backup())
        for row in payload["tables"]["app_setting"]:
            if row["key"] == "daily_new_limit":
                row["value_json"] = json.dumps(value)
        with pytest.raises(ValueError):
            store.import_backup(encode_current(payload))
        assert store.daily_new_limit == 20
        assert decode_current(store.export_backup())["tables"] == before
        assert not list(store.data_dir.glob("learning-before-restore-*.json"))


@pytest.mark.parametrize("value", [None, 3, [], "x" * 10001])
def test_romaji_checkpoint_rejects_invalid_text(value):
    with pytest.raises(ValueError):
        validate_session_state({"romaji_answer": value})


def test_legacy_typed_alias_backup_import_and_export_preserve_old_sessions(tmp_path):
    alias = {"kind": "alternate_romanization", "text": "si"}
    question = Question("kana-shi", "kana_choice", "hiragana", "し", "shi",
                        accepted_answers=(alias,), metadata={"audio_only": False})
    with StudyStore(tmp_path / "source") as source, StudyStore(tmp_path / "target") as target:
        session = source.start_session([question])
        source.save_session_progress(session, 0)
        raw_before = source._db.execute("SELECT snapshot_json FROM session_question").fetchone()[0]
        exported = decode_current(source.export_backup())
        snapshot = json.loads(exported["tables"]["session_question"][0]["snapshot_json"])
        assert snapshot["accepted_answers"] == [alias]
        assert source._db.execute("SELECT snapshot_json FROM session_question").fetchone()[0] == raw_before
        legacy = as_legacy(exported, version=1)
        historical = [json.loads(raw_before)]
        historical[0]['accepted_answers'] = [alias]
        legacy['tables']['study_session'][0]['question_snapshot_json'] = json.dumps(historical)
        from kanamemory.progress_backup import convert_legacy_backup
        target.import_backup(convert_legacy_backup(target, json.dumps(legacy).encode()))
        restored = target.load_session_progress()["questions"][0]
        assert restored.accepted_answers == ("si",)
        assert check_answer(restored, "ＳＩ")
        stored = json.loads(target._db.execute("SELECT snapshot_json FROM session_question").fetchone()[0])
        assert stored["accepted_answers"] == [alias]


@pytest.mark.parametrize("alias", [{"kind": "alternate_romanization", "text": 1},
                                    {"text": "si"}, {"kind": "x", "text": "si", "extra": "x"}])
def test_invalid_legacy_alias_backup_rejected_before_restore(tmp_path, alias):
    with StudyStore(tmp_path) as store:
        session = store.start_session([Question("shi", "kana_choice", "hiragana", "し", "shi")])
        store.save_session_progress(session, 0)
        payload = decode_current(store.export_backup())
        row = payload["tables"]["session_question"][0]
        snapshot = json.loads(row["snapshot_json"])
        snapshot["accepted_answers"] = [alias]
        row["snapshot_json"] = json.dumps(snapshot)
        with pytest.raises(ValueError):
            store.import_backup(encode_current(payload))
        assert not list(store.data_dir.glob("learning-before-restore-*.json"))
