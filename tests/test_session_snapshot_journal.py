"""Incremental reads are equivalent to SELECT, including unusual SQL writes."""
from copy import deepcopy
import json
import sqlite3

import pytest

from kanamemory import session_questions
from kanamemory.domain import Question, StudyStore


def question(index=0):
    return Question(f'kana-{index}', 'kana_romaji', 'hiragana', 'あ', 'a', ['a', 'i'],
                    metadata={'value': index, 'nested': [{'flag': True}]}).to_dict()


def exact_read(store, sid):
    actual = store._session_snapshot(sid)
    expected = session_questions.load(store._db, sid)
    # Preserve signed zero and bool/int distinction, not just Python equality.
    assert json.dumps(actual, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)
    return actual


def test_one_changed_ordinal_decodes_only_that_row_and_preserves_old_view(tmp_path, monkeypatch):
    with StudyStore(tmp_path) as store:
        sid = store.start_session([Question(**question(i)) for i in range(1000)])
        old = exact_read(store, sid)
        calls = []
        parse = store._snapshot_reader._parse
        def observed(text):
            calls.append(text)
            return parse(text)
        monkeypatch.setattr(store._snapshot_reader, '_parse', observed)
        revised = deepcopy(old[-1]); revised['answer'] = 'new'
        with store._db:
            store._db.execute('UPDATE session_question SET snapshot_json=? WHERE session_id=? AND question_index=999',
                              (json.dumps(revised), sid))
        updated = exact_read(store, sid)
        assert len(calls) == 1
        assert updated[0] is old[0] and updated[-1] is not old[-1]
        assert old[-1]['answer'] == 'a'
        assert updated[-1]['answer'] == 'new'


def test_tombstone_reinsert_ignore_and_replace_keep_journal_fresh(tmp_path):
    with StudyStore(tmp_path) as store:
        sid = store.start_session([Question(**question())])
        old = exact_read(store, sid)
        for clause in ('OR IGNORE', 'OR REPLACE', ''):
            with store._db:
                store._db.execute('DELETE FROM session_question WHERE session_id=?', (sid,))
            assert exact_read(store, sid) == []
            changed = deepcopy(old[0]); changed['answer'] = clause
            with store._db:
                store._db.execute(f'INSERT {clause} INTO session_question VALUES(?,?,?)',
                                  (sid, 0, json.dumps(changed)))
            assert exact_read(store, sid)[0]['answer'] == clause


def test_moved_keys_and_implicit_rowid_deletes_invalidate_both_sessions(tmp_path):
    with StudyStore(tmp_path) as store:
        first = store.start_session([Question(**question(1))])
        second = store.start_session([Question(**question(2))])
        exact_read(store, first); exact_read(store, second)
        with store._db:
            store._db.execute('UPDATE session_question SET session_id=?,question_index=1 WHERE session_id=?',
                              (second, first))
        assert exact_read(store, first) == []
        assert len(exact_read(store, second)) == 2
        victim = store._db.execute('SELECT rowid FROM session_question WHERE session_id=? AND question_index=1',
                                   (second,)).fetchone()[0]
        with store._db:
            store._db.execute('INSERT OR REPLACE INTO session_question(rowid,session_id,question_index,snapshot_json) VALUES(?,?,?,?)',
                              (victim, first, 0, json.dumps(question(3))))
        assert len(exact_read(store, second)) == 1
        assert exact_read(store, first)[0]['content_id'] == 'kana-3'
        # UPDATE OR REPLACE may also implicitly delete a different ordinal.
        with store._db:
            store._db.execute('UPDATE OR REPLACE session_question SET rowid=? WHERE session_id=?', (victim, second))
        assert exact_read(store, first) == []
        assert len(exact_read(store, second)) == 1


def test_transient_reads_never_publish_after_rollback_and_revision_reuse(tmp_path):
    with StudyStore(tmp_path) as store:
        sid = store.start_session([Question(**question())])
        old = exact_read(store, sid)
        cached = store._snapshot_reader.cache[sid]
        for value in ('transient', 'other transient'):
            changed = deepcopy(old[0]); changed['answer'] = value
            store._db.execute('BEGIN')
            store._db.execute('UPDATE session_question SET snapshot_json=? WHERE session_id=?', (json.dumps(changed), sid))
            assert exact_read(store, sid)[0]['answer'] == value
            assert store._snapshot_reader.cache[sid] is cached
            store._db.rollback()
        with store._db:
            store._db.execute('UPDATE session_question SET snapshot_json=? WHERE session_id=?', (json.dumps(question(9)), sid))
        assert exact_read(store, sid)[0]['content_id'] == 'kana-9'
        assert old[0]['content_id'] == 'kana-0'


def test_external_commit_between_token_and_delta_forces_complete_read(tmp_path, monkeypatch):
    with StudyStore(tmp_path) as store:
        sid = store.start_session([Question(**question(i)) for i in range(2)])
        exact_read(store, sid)
        with store._db:
            store._db.execute('UPDATE session_question SET snapshot_json=? WHERE session_id=? AND question_index=1',
                              (json.dumps(question(4)), sid))
        token = store._snapshot_reader.token
        def interleaved():
            value = token()
            with sqlite3.connect(store.path) as other:
                other.execute('UPDATE session_question SET snapshot_json=? WHERE session_id=? AND question_index=0',
                              (json.dumps(question(7)), sid))
            return value
        monkeypatch.setattr(store._snapshot_reader, 'token', interleaved)
        assert [q['content_id'] for q in exact_read(store, sid)] == ['kana-7', 'kana-4']


@pytest.mark.parametrize('malformed', [None, [], 'text', 3])
def test_replace_keeps_non_object_rejection(tmp_path, malformed):
    with StudyStore(tmp_path) as store:
        sid = store.start_session([Question(**question())])
        with store._db:
            store._db.execute('UPDATE session_question SET snapshot_json=? WHERE session_id=?', (json.dumps(malformed), sid))
        store._db.execute('BEGIN')
        with pytest.raises(ValueError, match='快照必须是对象'):
            session_questions.replace(store._db, sid, [question()], reader=store._snapshot_reader)
        store._db.rollback()


def test_unchanged_legacy_bytes_and_budget_boundary_match_original_writer(tmp_path, monkeypatch):
    with StudyStore(tmp_path) as store:
        sid = store.start_session([Question(**question())])
        raw = json.dumps(question(), ensure_ascii=True, indent=2)
        with store._db:
            store._db.execute('UPDATE session_question SET snapshot_json=? WHERE session_id=?', (raw, sid))
        rows = exact_read(store, sid)
        with store._db:
            store._db.execute('BEGIN')
            session_questions.replace(store._db, sid, rows, reader=store._snapshot_reader)
        assert store._db.execute('SELECT snapshot_json FROM session_question WHERE session_id=?', (sid,)).fetchone()[0] == raw
        size = len(session_questions._encoded(rows[0]).encode('utf8')) + 3
        monkeypatch.setattr(session_questions, 'MAX_SESSION_BYTES', size - 1)
        store._db.execute('BEGIN')
        with pytest.raises(ValueError, match='队列数据过大'):
            session_questions.replace(store._db, sid, rows, reader=store._snapshot_reader)
        store._db.rollback()
