"""Large valid session snapshots remain portable without relaxing input limits."""
import gc
import json
from pathlib import Path
import sys

import pytest

from kanamemory.domain import Question, StudyStore
from kanamemory import progress_backup, record_transport
from record_test_support import decode_current, as_legacy


def question():
    return Question("kana-a", "kana_choice", "hiragana", "あ", "a",
                    options=["a", "ka", "sa", "ta"], audio_text="あ")


def structured_payload(store):
    return as_legacy(decode_current(store.export_backup()), 2)


def test_small_exports_use_current_normalized_records(tmp_path):
    with StudyStore(tmp_path) as store:
        store.start_session([question()])
        payload = decode_current(store.export_backup())
        assert payload['format'] == 'kanamemory-learning-records' and payload['version'] == 1
        assert 'question_snapshot_json' not in payload['tables']['study_session'][0]
        assert len(payload['tables']['session_question']) == 1


def test_structured_snapshot_retains_checkpoint_and_validates_old_aliases(tmp_path):
    with StudyStore(tmp_path / "source") as source, StudyStore(tmp_path / "target") as target:
        sid = source.start_session([question()])
        source.save_session_progress(sid, 0, state={"romaji_answer": "A"})
        payload = structured_payload(source)
        payload["tables"]["study_session"][0]["question_snapshot_json"][0]["accepted_answers"] = [
            {"kind": "alternate_romanization", "text": "aa"}]
        target.import_backup(progress_backup.convert_legacy_backup(target, json.dumps(payload, ensure_ascii=False).encode()))
        pending = target.load_session_progress()
        assert pending["state"] == {"romaji_answer": "A"}
        assert pending["questions"][0].accepted_answers == ("aa",)


@pytest.mark.parametrize("corruption", ["string", "mapping", "question", "kind", "alias", "checkpoint", "foreign_key"])
def test_v2_is_subject_to_the_same_validation_before_mutation(tmp_path, corruption):
    with StudyStore(tmp_path / "source") as source, StudyStore(tmp_path / "target") as target:
        sid = source.start_session([question()])
        source.save_session_progress(sid, 0)
        target.set_setting("keep", "original")
        payload = structured_payload(source)
        row = payload["tables"]["study_session"][0]
        if corruption == "string":
            row["question_snapshot_json"] = json.dumps(row["question_snapshot_json"])
        elif corruption == "mapping":
            row["question_snapshot_json"] = {"compressed": "unsupported"}
        elif corruption == "question":
            row["question_snapshot_json"] = ["not a question"]
        elif corruption == "kind":
            row["question_snapshot_json"][0]["kind"] = "unknown"
        elif corruption == "alias":
            row["question_snapshot_json"][0]["accepted_answers"] = [{"text": "a"}]
        elif corruption == "checkpoint":
            payload["tables"]["session_checkpoint"][0]["question_index"] = 99
        elif corruption == "foreign_key":
            payload["tables"]["session_checkpoint"][0]["session_id"] = "missing"
        with pytest.raises(ValueError):
            target.import_backup(progress_backup.convert_legacy_backup(target, json.dumps(payload, ensure_ascii=False).encode()))
        assert target.get_setting("keep") == "original"
        assert target._db.execute("SELECT COUNT(*) FROM study_session").fetchone()[0] == 0
        assert not list(target.data_dir.glob("learning-before-restore-*.json"))


def test_current_and_legacy_outer_expansion_caps_are_independent(tmp_path, monkeypatch):
    with StudyStore(tmp_path) as store:
        sid = store.start_session([question()])
        store.save_session_progress(sid, 0)
        old = json.dumps(structured_payload(store), ensure_ascii=False).encode()
        monkeypatch.setattr(progress_backup, 'MAX_BYTES', len(old) - 1)
        with pytest.raises(ValueError, match='64 MiB'):
            progress_backup.convert_legacy_backup(store, old)
        monkeypatch.setattr(progress_backup, 'MAX_BYTES', 8192)
        store.set_setting('large', 'x' * 20000)
        current = store.export_backup()
        assert current.startswith(record_transport.PREFIX) and len(current) <= 8192
        assert store.inspect_backup(current)['sessions'] == 1
        monkeypatch.setattr(record_transport, 'EXPANDED_LIMIT', 32)
        with pytest.raises(ValueError, match='磁盘数据上限'):
            store.export_backup()


def test_60060_question_batch_export_restore_and_undo(tmp_path):
    """Equivalent byte pressure to 3,003 words × 2 forms × 10 rounds.

    Construct inert, valid snapshots directly: generating the content catalogue
    and choices is unrelated to this regression and would slow this test down.
    """
    from kanamemory import record_transport as backup_stream
    import hashlib
    count = 60060
    with StudyStore(tmp_path / "large") as large, StudyStore(tmp_path / "empty") as empty:
        sid = large.start_session([question()])
        large.save_session_progress(sid, 0, state={"romaji_answer": "a"})
        # Every row keeps the native route the session reserved, as real rows do.
        item = large.get_session_questions(sid)[0].to_dict()
        target_item_bytes = (progress_backup.MAX_BYTES - 1024 * 1024) // count - 2
        item["metadata"]["description"] = ""
        base = json.dumps(item, ensure_ascii=False)
        item["metadata"]["description"] = "x" * (target_item_bytes - len(base.encode("utf-8")))
        encoded_item = json.dumps(item, ensure_ascii=False)
        snapshot = "[" + ", ".join([encoded_item] * count) + "]"
        expected_snapshot_sha = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
        # The accepted snapshot itself is below budget; only outer escaping breaks v1.
        assert len(snapshot.encode("utf-8")) < progress_backup.MAX_BYTES
        assert len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > progress_backup.MAX_BYTES
        with large._db:
            large._db.execute('DELETE FROM session_question WHERE session_id=?',(sid,))
            large._db.executemany('INSERT INTO session_question VALUES(?,?,?)',
                ((sid,i,encoded_item) for i in range(count)))
        del snapshot
        gc.collect()
        backup = large.export_backup()
        assert len(backup) <= progress_backup.MAX_BYTES
        assert backup.startswith(backup_stream.PREFIX)
        assert large.inspect_backup(backup)["has_unfinished_session"]
        empty.import_backup(backup)
        with empty._db:
            restored = '[' + ', '.join(row[0] for row in empty._db.execute(
                'SELECT snapshot_json FROM session_question WHERE session_id=? ORDER BY question_index',(sid,))) + ']'
        assert restored.count('"content_id"') == count
        assert hashlib.sha256(restored.encode("utf-8")).hexdigest() == expected_snapshot_sha
        del restored, backup
        # A large live store must still be restorable: its automatic rollback
        # uses the bounded stream too, and remains user-exportable and reimportable.
        with StudyStore(tmp_path / "replacement") as replacement:
            replacement.set_setting("replacement", True)
            result = large.import_backup(replacement.export_backup())
        assert large.get_setting("replacement") is True
        rollback = Path(result["rollback_path"]).read_bytes()
        assert rollback.startswith(backup_stream.PREFIX)
        assert large.previous_backup() == rollback
        large.import_backup(rollback)
        assert large.get_setting("replacement") is None
        checkpoint = large._db.execute("SELECT question_index,state_json FROM session_checkpoint").fetchone()
        assert checkpoint["question_index"] == 0
        assert json.loads(checkpoint["state_json"]) == {"romaji_answer": "a"}
        restored = '[' + ', '.join(row[0] for row in large._db.execute(
            'SELECT snapshot_json FROM session_question WHERE session_id=? ORDER BY question_index',(sid,))) + ']'
        assert restored.count('"content_id"') == count
        assert hashlib.sha256(restored.encode("utf-8")).hexdigest() == expected_snapshot_sha
