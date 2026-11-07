"""Static projections must preserve every old predicate and database row."""
from runtime_record_test_support import snapshot_json, write_snapshot
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from kanamemory import domain
from kanamemory.domain import Question, StudyStore, question_identities
from support.lazy_question_sequence_support import clone
from support.reenabled_kinds_support import filtered_learning_queue

NOW = datetime(2027, 6, 1, 12, tzinfo=timezone.utc)


class UncachedPolicy:
    """Oracle: the old implementation called the original predicate each time."""
    def __init__(self, items):
        self.items = items

    def allowed(self, index):
        return domain.question_allowed(self.items[index])


def original_identities(questions, start=0, *, excluded_kinds=(), allowed_only=True):
    return tuple((q.content_id, q.kind, q.script) for q in questions[start:]
                 if q.kind not in excluded_kinds and (not allowed_only or domain.question_allowed(q)))


def all_rows(db):
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {table: [tuple(r) for r in db.execute('SELECT * FROM "'+table+'" ORDER BY rowid')] for table in tables}


def questions():
    return [Question('kana-3042', 'kana_romaji', 'hiragana', 'あ', 'a', ['a','i'],
                     metadata={'kana_category':'basic','kana_symbol':'あ'}),
            Question('kana-30c4-30a1', 'kana_romaji', 'hiragana', 'つぁ', 'tsa', ['tsa','ka']),
            Question('kana-30c4-30a1', 'kana_romaji', 'katakana', 'ツァ', 'tsa', ['tsa','ka'])]


@pytest.mark.parametrize('start', [0, 1, -2, 3])
@pytest.mark.parametrize('allowed_only', [False, True])
def test_projection_matches_eager_and_observes_mutated_public_metadata(tmp_path, start, allowed_only):
    with StudyStore(tmp_path) as store:
        sid = store.start_session(questions(), now=NOW)
        view = store.get_session_questions(sid, lazy=True)
        expected = original_identities(store.get_session_questions(sid), start, allowed_only=allowed_only)
        assert question_identities(view, start, allowed_only=allowed_only) == expected
        assert view._copies == {}
        view[0].metadata['kana_category'] = 'foreign_combination'
        view[0].metadata['kana_symbol'] = 'ティ'
        assert question_identities(view, start, allowed_only=allowed_only) == original_identities(view, start, allowed_only=allowed_only)
        assert question_identities(store.get_session_questions(sid, lazy=True), start, allowed_only=allowed_only) == expected


def test_projection_rechecks_only_materialized_copies_and_is_bounded(tmp_path, monkeypatch):
    with StudyStore(tmp_path) as store:
        sid = store.start_session(questions(), now=NOW)
        calls, original = [], domain.question_allowed
        def observed(q):
            calls.append(q)
            return original(q)
        monkeypatch.setattr(domain, 'question_allowed', observed)
        view = store.get_session_questions(sid, lazy=True)
        question_identities(view)
        assert len(calls) == 3
        for _ in range(5):
            question_identities(store.get_session_questions(sid, lazy=True))
        assert len(calls) == 3
        view[0].metadata['kana_category'] = 'foreign_combination'
        question_identities(view)
        assert len(calls) == 4
        assert len(store._snapshot_policy_cache._allowed) == 3
        assert not any('policy_cache' in name for name in all_rows(store._db))


@pytest.mark.parametrize('external', [False, True])
def test_external_write_rollback_revision_reuse_and_restore_match_source(tmp_path, external):
    with StudyStore(tmp_path) as store:
        sid = store.start_session(questions(), now=NOW)
        store.save_session_progress(sid, 0)
        old_view = store.get_session_questions(sid, lazy=True)
        old = question_identities(old_view)
        backup = store.export_backup()
        initial = deepcopy(store._session_snapshot(sid))
        changed = deepcopy(initial)
        changed[0]['metadata']['kana_category'] = 'foreign_combination'
        conn = sqlite3.connect(store.path) if external else store._db
        try:
            with conn:
                write_snapshot(conn, sid, json.dumps(changed))
        finally:
            if external:
                conn.close()
        assert question_identities(store.get_session_questions(sid,lazy=True)) != old
        assert question_identities(old_view) == old
        for symbol in ('ティ', 'ツァ'):
            store._db.execute('BEGIN')
            transient = deepcopy(initial)
            transient[0]['metadata']['kana_symbol'] = symbol
            write_snapshot(store._db, sid, json.dumps(transient))
            temporary = store.get_session_questions(sid,lazy=True)
            assert question_identities(temporary) == original_identities(list(temporary))
            assert store._snapshot_policy_cache.items is not temporary._items
            store._db.rollback()
        # No intervening committed read before another write reuses the same
        # transactional revision number with different source values.
        with store._db:
            write_snapshot(store._db, sid, json.dumps(initial))
        assert question_identities(store.get_session_questions(sid,lazy=True)) == old
        store.import_backup(backup)
        assert question_identities(store.get_session_questions(sid,lazy=True)) == old


@pytest.mark.parametrize('kind', ['kana_romaji', 'romaji_handwriting', 'kana_handwriting'])
def test_reconcile_restore_full_rows_match_old_predicates_with_live_due_and_controls(tmp_path, kind):
    source = tmp_path/'source'
    with StudyStore(source, time_zone='UTC') as store:
        sid, learned, keep, card = filtered_learning_queue(store,kind,'hiragana')
    results = []
    for cached in (False, True):
        folder = tmp_path/str(cached)
        clone(source/'progress.sqlite3', folder)
        with StudyStore(folder, time_zone='UTC') as store:
            if not cached:
                store._snapshot_question_policy = UncachedPolicy
            observed = []
            for enabled, at in (((keep.kind,kind), NOW), ((keep.kind,), NOW), ((keep.kind,kind), NOW+timedelta(days=1))):
                state = store.reconcile_session_kinds(sid,enabled,now=at,lazy_questions=True)
                observed.append(({**state,'questions':list(state['questions'])},all_rows(store._db)))
            store.set_content_control(learned.content_id,'suspend',now=NOW)
            observed.append((store.prepare_next_question(sid,now=NOW,lazy_questions=True),all_rows(store._db)))
            store.set_content_control(learned.content_id,'resume',now=NOW)
            observed.append((store.prepare_next_question(sid,now=NOW+timedelta(days=30),lazy_questions=True),all_rows(store._db)))
            results.append(observed)
    assert results[0] == results[1]


def test_unused_invalid_legacy_row_is_not_eagerly_evaluated():
    good = questions()[0].to_dict()
    malformed = deepcopy(good)
    malformed['metadata']['kana_category'] = []
    view = domain._QuestionSequence([malformed,good])
    assert question_identities(view,1) == ((good['content_id'],good['kind'],good['script']),)
    with pytest.raises(TypeError):
        question_identities(view,0)
