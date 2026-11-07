from record_test_support import decode_current, encode_current
"""Preflight and cancellation cannot initialize optional AI learner tables."""
import json
import pytest

from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.tutor_memory import TutorMemoryStore


def dump(store):
    return tuple(store._db.iterdump())


def has_memory(store):
    return store._db.execute("SELECT 1 FROM sqlite_master WHERE name='tutor_memory_meta'").fetchone() is not None


def test_valid_inspection_and_invalid_import_leave_optional_schema_absent(tmp_path):
    with StudyStore(tmp_path/'source') as source, StudyStore(tmp_path/'target') as target:
        LearningStore(source)
        TutorMemoryStore(source).record_message('request-1', 'Prefer short explanations')
        payload = source.export_backup()
        before = dump(target)
        assert not has_memory(target)
        target.inspect_backup(payload)
        assert dump(target) == before and not has_memory(target)
        invalid = decode_current(payload)
        invalid['tables']['tutor_memory_meta'][0]['value'] = 'not-a-version'
        with pytest.raises(ValueError):
            target.import_backup(encode_current(invalid))
        assert dump(target) == before and not has_memory(target)
        assert target.previous_backup() is None


def test_cancel_after_schema_addition_rolls_back_tables_metadata_and_existing_rows(tmp_path):
    with StudyStore(tmp_path/'source') as source, StudyStore(tmp_path/'target') as target:
        LearningStore(source)
        TutorMemoryStore(source).record_message('request-1', 'Prefer short explanations')
        payload = source.export_backup()
        target.set_setting('keep-me', {'value': 123})
        before = dump(target)
        def cancel_when_created():
            if has_memory(target):
                raise InterruptedError('cancelled during transactional restore')
        from kanamemory.progress_backup import import_backup
        with pytest.raises(InterruptedError):
            import_backup(target, payload, cancel_check=cancel_when_created)
        assert dump(target) == before and not has_memory(target)
