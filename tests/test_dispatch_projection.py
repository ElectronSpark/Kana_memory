"""The projected sort is checked against the unchanged full-row reader."""
from datetime import timedelta
import json

import pytest

from support.first_teaching_dispatch_core_support import NOW
from support.first_teaching_dispatch_core_support import stores
from support.first_teaching_dispatch_core_support import course
from support.first_teaching_dispatch_core_support import rows
from support.unassessed_service_support import setup
from support.unassessed_service_support import KINDS


@pytest.mark.parametrize('mode',['text','choice','absent','null','response-text'])
@pytest.mark.parametrize('scope',[{}, {'teaching_only':True},
    {'teaching_only':True,'include_unassessed':True,'prefer_unassessed':True},
    {'teaching_only':True,'include_unassessed':True,'prefer_unassessed':False}])
def test_projection_matches_full_reader_with_fallback_probe_semantics(stores,monkeypatch,mode,scope):
    store,learning=stores
    _,qid=setup(store,learning,fresh_course=True)
    with store._db:
        raw=json.loads(store._db.execute('SELECT snapshot_json FROM learning_question WHERE question_id=?',(qid,)).fetchone()[0])
        raw.pop('input_mode',None);raw.pop('response',None)
        if mode=='null':raw.update(input_mode=None,response='text')
        elif mode=='response-text':raw['response']='text'
        elif mode!='absent':raw['input_mode']=mode
        raw['large_unrelated_explanation']='detail '*6000
        store._db.execute('UPDATE learning_question SET snapshot_json=? WHERE question_id=?',(json.dumps(raw),qid))
    before=rows(store)
    projected=learning.current(KINDS,now=NOW,fair_new=True,**scope)
    with monkeypatch.context() as patch:
        patch.setattr(learning,'_dispatch_pending',learning._pending)
        oracle=learning.current(KINDS,now=NOW,fair_new=True,**scope)
    assert projected==oracle and rows(store)==before
    full=next(row for row in learning._pending() if row['question_id']==qid)
    narrow=next(row for row in learning._dispatch_pending() if row['question_id']==qid)
    assert len(full['snapshot_json'])>40000 and len(narrow['snapshot_json'])<100
    assert 'large_unrelated_explanation' in json.loads(full['snapshot_json'])


def test_projection_preserves_feedback_real_input_waits_and_disabled_choices(stores,monkeypatch):
    store,learning=stores
    _,qid=setup(store,learning,fresh_course=True)
    for state in ({'text':'partially typed'}, {'parts':['a']}, {'presented':True}):
        learning.save_draft(qid,state)
        for kinds in (KINDS,['grammar_meaning'],[]):
            for at in (NOW,NOW+timedelta(days=1)):
                before=rows(store)
                actual=learning.current(kinds,now=at,fair_new=True)
                with monkeypatch.context() as patch:
                    patch.setattr(learning,'_dispatch_pending',learning._pending)
                    expected=learning.current(kinds,now=at,fair_new=True)
                assert actual==expected and rows(store)==before
    learning.submit(qid,'正在读书',True,now=NOW)
    actual=learning.current(KINDS,now=NOW)
    with monkeypatch.context() as patch:
        patch.setattr(learning,'_dispatch_pending',learning._pending)
        assert learning.current(KINDS,now=NOW)==actual
    assert actual['dispatch_priority']==(0,0)


def test_real_both_pool_trace_is_not_an_extra_queue_or_consumption(stores):
    from support.unassessed_service_support import protected
    store,learning=stores
    sid,qid=setup(store,learning,fresh_course=True)
    result=protected(store,learning,sid)
    trace=result[1]['teaching_service']
    assert trace['earned'] and trace['threshold']==trace['due_reviews_served_capped']==64
    assert trace['course_candidate_pool']=='additional' and trace['selected_lane']=='course'
    assert trace['course_candidates']['additional']['question_id']==qid
    assert trace['course_candidates']['introduction']['content_id']=='fresh-course'


def test_real_both_pool_trace_survives_final_kana_selection(stores):
    from support.unassessed_service_support import protected
    from support.first_teaching_dispatch_core_support import install_queue
    from support.first_teaching_dispatch_core_support import kana
    from kanamemory.new_dispatch import SETTING
    store,learning=stores
    _,qid=setup(store,learning,fresh_course=True)
    sid=install_queue(store,[kana('old-waiting','あ','a'),kana('fresh-kana','い','i')])
    store.set_setting(SETTING,{'version':1,'last_lane':'course','last_kana_band':0})
    result=protected(store,learning,sid)
    assert result[0]=='kana'
    trace=result[1]['teaching_service']
    assert trace['selected_lane']=='kana' and trace['course_candidate_pool']=='additional'
    assert trace['course_candidates']['additional']['question_id']==qid
    assert trace['course_candidates']['introduction']['content_id']=='fresh-course'
