"""Snapshot read acceleration must preserve isolated public return values."""
from runtime_record_test_support import snapshot_json, write_snapshot
from copy import deepcopy
import json
import sqlite3
from pathlib import Path
import sys

import pytest

from kanamemory.domain import Question, StudyStore, _question_from_snapshot


def test_repeated_reads_own_all_nested_containers_and_keep_legacy_normalization(tmp_path):
    metadata = {'practice_config': {'kinds': ['kana_romaji'], 'flags': [None, False, 0, 1.5]},
                'nested': [{'empty_dict': {}, 'empty_list': [], 'rows': [[{'text': 'あ'}]]}]}
    original = Question('a', 'kana_romaji', 'hiragana', 'あ', 'a', ['a', 'i'],
                        accepted_answers=('a',), metadata=metadata)
    with StudyStore(tmp_path) as store:
        sid = store.start_session([original, original])
        raw = json.loads(json.dumps([q.to_dict() for q in (original, original)]))
        raw[0]['accepted_answers'] = [{'kind': 'hepburn', 'text': 'a'}]
        with store._db:
            write_snapshot(store._db, sid, json.dumps(raw, ensure_ascii=False))
        expected = [_question_from_snapshot(item) for item in raw]
        first = store.get_session_questions(sid)
        second = store.get_session_questions(sid)
        assert first == second == expected
        pristine = deepcopy(second)
        first[0].metadata['nested'][0]['rows'][0][0]['text'] = 'wrong'
        first[0].metadata['nested'][0]['empty_dict']['wrong'] = True
        first[0].metadata['nested'][0]['empty_list'].append('wrong')
        first[0].metadata['practice_config']['flags'].reverse()
        first[0].options.clear()
        first.pop()
        assert second == pristine == expected
        assert store.get_session_questions(sid) == expected
        assert json.loads(snapshot_json(store._db, sid)) == raw


def test_reordered_tail_and_changed_prefix_are_fresh_across_connections_and_rollback(tmp_path):
    questions = [Question('same-id', 'kana_choice', 'hiragana', 'あ', 'a', [str(i), 'a'],
                          metadata={'practice_config': {'kinds': ['kana_choice']},
                                    'variant': {'index': i, 'notes': [f'original-{i}']}})
                 for i in range(3)]
    with StudyStore(tmp_path) as store:
        sid = store.start_session(questions)
        # Compare the stored enrollment snapshot, including ability bindings.
        raw = json.loads(snapshot_json(store._db, sid))
        questions = [_question_from_snapshot(item) for item in raw]
        first = store.get_session_questions(sid)
        assert first == questions
        revised = [deepcopy(raw[0]), deepcopy(raw[2]), deepcopy(raw[1])]
        with store._db:
            write_snapshot(store._db, sid, json.dumps(revised))
        assert store.get_session_questions(sid) == [questions[0], questions[2], questions[1]]
        # Identity is deliberately unchanged: a cache keyed only by content,
        # direction or prompt would silently return the old metadata/options.
        revised[0]['metadata']['variant']['notes'] = ['external replacement']
        revised[0]['options'] = ['new option', 'a']
        with sqlite3.connect(store.path) as other:
            write_snapshot(other, sid, json.dumps(revised))
        updated = store.get_session_questions(sid)
        assert updated[0].options == ['new option', 'a']
        assert updated[0].metadata['variant']['notes'] == ['external replacement']
        assert first == questions
        transient = deepcopy(revised)
        transient[0]['metadata']['variant']['notes'] = ['rolled back']
        store._db.execute('BEGIN')
        write_snapshot(store._db, sid, json.dumps(transient))
        assert store.get_session_questions(sid)[0].metadata['variant']['notes'] == ['rolled back']
        store._db.rollback()
        assert store.get_session_questions(sid) == updated
        updated[0].metadata['variant']['notes'].append('caller mutation')
        assert store.get_session_questions(sid)[0].metadata['variant']['notes'] == ['external replacement']


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('before,after', [(True, 1), (1, True), (False, 0), (0, False),
                                        (1, 1.0), (1.0, 1), (0.0, -0.0)])
def test_cached_metadata_preserves_json_scalar_types_after_updates(tmp_path, external, before, after):
    question = Question('a', 'kana_romaji', 'hiragana', 'あ', 'a',
                        metadata={'kana_intro_v1': before, 'nested': [{'value': before}]})
    with StudyStore(tmp_path) as store:
        sid = store.start_session([question])
        first = store.get_session_questions(sid)[0]
        assert type(first.metadata['kana_intro_v1']) is type(before)
        revised = question.to_dict()
        revised['metadata'] = {'kana_intro_v1': after, 'nested': [{'value': after}]}
        connection = sqlite3.connect(store.path) if external else store._db
        try:
            with connection:
                write_snapshot(connection, sid, json.dumps([revised]))
        finally:
            if external:
                connection.close()
        result = store.get_session_questions(sid)[0]
        for value in (result.metadata['kana_intro_v1'], result.metadata['nested'][0]['value']):
            assert type(value) is type(after)
            assert json.dumps(value) == json.dumps(after)
        # This exact identity check controls whether a kana introduction is
        # rendered, so equality with integer 1 is not sufficient.
        assert (result.metadata['kana_intro_v1'] is True) == (after is True)
        assert type(first.metadata['kana_intro_v1']) is type(before)
