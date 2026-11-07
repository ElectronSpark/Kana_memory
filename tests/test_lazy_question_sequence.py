"""Opt-in queue views preserve list values, isolation and canonical writes."""
from runtime_record_test_support import snapshot_json, write_snapshot
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from kanamemory import domain
from kanamemory.domain import Question, StudyStore

NOW = datetime(2027, 6, 1, 12, tzinfo=timezone.utc)


def make_questions(count=10):
    return [Question(f'kana-{i}', 'kana_romaji', 'hiragana', 'あ', 'a', ['a', 'i'],
                     metadata={'practice_config': {'schedule_mode': 'spaced'}, 'nested': [{'i': i}]})
            for i in range(count)]


def test_lazy_index_slice_iteration_equality_and_mutation_isolation(tmp_path, monkeypatch):
    with StudyStore(tmp_path) as store:
        sid = store.start_session(make_questions(1000))
        eager = store.get_session_questions(sid)
        calls = []
        original = domain._question_from_snapshot
        def observed(item, **kwargs):
            calls.append(item['content_id'])
            return original(item, **kwargs)
        monkeypatch.setattr(domain, '_question_from_snapshot', observed)
        first, second = [store.get_session_questions(sid, lazy=True) for _ in range(2)]
        assert len(first) == 1000 and calls == []
        tail = first[-2:]
        assert tail == eager[-2:] and calls == ['kana-998', 'kana-999']
        assert first[-1] is first[999] is tail[-1]
        assert first[:0] == []
        with pytest.raises(IndexError):
            _ = first[1000]
        with pytest.raises(IndexError):
            _ = first[-1001]
        with pytest.raises(ValueError):
            _ = first[::0]
        first[-1].metadata['nested'][0]['i'] = 'caller change'
        first[-1].options.append('caller change')
        assert tail[-1].metadata['nested'][0]['i'] == 'caller change'
        assert second[-1] == eager[-1]
        assert store.get_session_questions(sid, lazy=True)[-1] == eager[-1]
        assert second == eager and eager == second and list(second) == eager
        assert second[::-1] == eager[::-1]
        assert second != tuple(eager)
        assert isinstance(store.get_session_questions(sid), list)


def test_metadata_flags_match_list_values_without_copying_history(tmp_path, monkeypatch):
    with StudyStore(tmp_path) as store:
        questions = make_questions(1000)
        questions[410].metadata['research_content'] = 1
        sid = store.start_session(questions)
        eager = store.get_session_questions(sid)
        view = store.get_session_questions(sid, lazy=True)
        assert domain.any_question_metadata(view, 'research_content') is True
        assert domain.any_question_metadata(view, 'research_content') == domain.any_question_metadata(eager, 'research_content')
        assert not view._copies
        view[410].metadata['research_content'] = False
        assert domain.any_question_metadata(view, 'research_content') is False
        assert len(view._copies) == 1
        view[-1].metadata['research_content'] = [True]
        assert domain.any_question_metadata(view, 'research_content') is True
        assert len(view._copies) == 2
        assert domain.any_question_metadata(view, 'absent') is False


from support.lazy_question_sequence_support import clone


def rows(db):
    return {name:[tuple(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY rowid')]
            for name in ('app_setting','study_session','session_question','session_checkpoint','study_card','session_answer','review_event')}


@pytest.mark.parametrize('reconcile', [False, True])
def test_lazy_prepare_and_reconcile_match_all_eager_rows_including_legacy_prefix(tmp_path, reconcile):
    master=tmp_path/'master'
    with StudyStore(master, time_zone='UTC') as store:
        prefix = Question('word', 'word_kana', 'katakana', '聞いて', 'キャ', ['キャ','チュ'],
                          accepted_answers=('kya',), metadata={'practice_config': {'word_table':'partial'}})
        questions = [prefix, *make_questions(3)]
        questions[1].metadata['not_before_utc'] = (NOW+timedelta(days=1)).isoformat()
        questions[3] = Question('last','romaji_kana','hiragana','a','あ',['あ','い'],
                                metadata={'practice_config': {'schedule_mode':'spaced'}})
        sid=store.start_session(questions, now=NOW)
        store.save_session_progress(sid,1,state={})
        raw=json.loads(snapshot_json(store._db, sid))
        raw[0]['accepted_answers']=[{'kind':'legacy','text':'kya'}]
        raw=[dict(reversed(list(item.items()))) for item in raw]
        with store._db:
            write_snapshot(store._db, sid, json.dumps(raw,ensure_ascii=False))
    results=[]
    for lazy in (False,True):
        target=tmp_path/str(lazy)
        clone(master/'progress.sqlite3',target)
        with StudyStore(target,time_zone='UTC') as store:
            if reconcile:
                store.reconcile_session_kinds(sid, ['kana_romaji'], now=NOW,lazy_questions=lazy)
            pending=store.prepare_next_question(sid,now=NOW,lazy_questions=lazy)
            assert pending is not None
            pending={**pending,'questions':list(pending['questions'])}
            results.append((pending, rows(store._db)))
    assert results[0] == results[1]


def test_lazy_views_remain_stable_across_external_write_and_rollback(tmp_path):
    with StudyStore(tmp_path,time_zone='UTC') as store:
        sid=store.start_session(make_questions(2),now=NOW)
        old=store.get_session_questions(sid,lazy=True)
        assert old[0].metadata['nested'][0]['i'] == 0
        original=json.loads(snapshot_json(store._db, sid))
        changed=deepcopy(original)
        changed[1]['metadata']['kana_intro_v1']=True
        with sqlite3.connect(store.path) as other:
            write_snapshot(other, sid, json.dumps(changed))
        new=store.get_session_questions(sid,lazy=True)
        assert 'kana_intro_v1' not in old[1].metadata
        assert new[1].metadata['kana_intro_v1'] is True

        transient=deepcopy(changed)
        transient[1]['metadata']['kana_intro_v1']=1
        store._db.execute('BEGIN')
        write_snapshot(store._db, sid, json.dumps(transient))
        aborted=store.get_session_questions(sid,lazy=True)
        store._db.rollback()
        restored=store.get_session_questions(sid,lazy=True)
        assert type(aborted[1].metadata['kana_intro_v1']) is int
        assert restored[1].metadata['kana_intro_v1'] is True
        assert new[1].metadata['kana_intro_v1'] is True


def test_dispatch_uses_one_snapshot_if_another_connection_updates_mid_calculation(tmp_path, monkeypatch):
    master = tmp_path / 'master'
    with StudyStore(master, time_zone='UTC') as store:
        questions = make_questions(3)
        questions[1].metadata['not_before_utc'] = (NOW + timedelta(days=1)).isoformat()
        sid = store.start_session(questions, now=NOW)
        store.save_session_progress(sid, 1)
    results = []
    for lazy in (False, True):
        target = tmp_path / str(lazy)
        clone(master / 'progress.sqlite3', target)
        with StudyStore(target, time_zone='UTC') as store:
            original_unavailable = store._unavailable_content_ids
            def write_between_read_and_reorder(now):
                raw = deepcopy(store._session_snapshot(sid))
                raw[0]['answer'] = 'changed externally'
                raw[2]['metadata']['external_revision'] = True
                with sqlite3.connect(store.path) as other:
                    write_snapshot(other, sid, json.dumps(raw))
                return original_unavailable(now)
            monkeypatch.setattr(store, '_unavailable_content_ids', write_between_read_and_reorder)
            pending = store.prepare_next_question(sid, now=NOW, lazy_questions=lazy)
            assert pending['questions'][1].content_id == 'kana-2'
            assert pending['questions'][0].answer == 'a'
            assert 'external_revision' not in pending['questions'][1].metadata
            results.append(({**pending, 'questions': list(pending['questions'])}, rows(store._db)))
    assert results[0] == results[1]


def test_lazy_reordering_materializes_only_the_live_tail_and_previous_cue(tmp_path, monkeypatch):
    with StudyStore(tmp_path,time_zone='UTC') as store:
        questions=make_questions(1000)
        questions[990].metadata['not_before_utc']=(NOW+timedelta(days=1)).isoformat()
        sid=store.start_session(questions,now=NOW)
        store.save_session_progress(sid,990,state={})
        calls=[]
        original=domain._question_from_snapshot
        def observe(item, **kwargs):
            calls.append(int(item['content_id'].split('-')[-1]))
            return original(item,**kwargs)
        monkeypatch.setattr(domain,'_question_from_snapshot',observe)
        result=store.prepare_next_question(sid,now=NOW,lazy_questions=True)
        assert result['question_index']==990
        assert result['questions'][990].content_id=='kana-991'
        assert len(result['questions'])==1000
        assert min(calls)>=989 and len(calls)<=15
        before=snapshot_json(store._db, sid)
        store.save_session_progress(sid,990,state={'page':'question','romaji_answer':'partial'})
        resumed=store.prepare_next_question(sid,now=NOW+timedelta(seconds=5),lazy_questions=True)
        assert resumed['state']['romaji_answer']=='partial'
        assert snapshot_json(store._db, sid)==before


def test_returned_lazy_view_needs_no_live_database_and_retains_its_snapshot(tmp_path):
    original=make_questions(3)
    store=StudyStore(tmp_path,time_zone='UTC')
    sid=store.start_session(original,now=NOW)
    # Enrollment now binds ability routing metadata into the frozen snapshot.
    original=store.get_session_questions(sid)
    old=store.get_session_questions(sid,lazy=True)
    independent=store.get_session_questions(sid,lazy=True)
    first=old[0]
    revised=[q.to_dict() for q in original]
    revised[1]['answer']='changed after view creation'
    with store._db:
        write_snapshot(store._db, sid, json.dumps(revised))
    newer=store.get_session_questions(sid,lazy=True)
    store.close()
    # Index 1 was never accessed before the database changed and closed.
    assert old[1].answer==original[1].answer
    assert newer[1].answer=='changed after view creation'
    assert old[0] is first is old[:1][0]
    old[:1][0].metadata['nested'][0]['i']='only this view'
    assert old[0].metadata['nested'][0]['i']=='only this view'
    assert independent==original
