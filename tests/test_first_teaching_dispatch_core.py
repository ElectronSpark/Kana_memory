"""Private candidate boundaries; real stores, no rendered UI or learner tuning."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from pathlib import Path
import sys

import pytest


from kanamemory.domain import StudyStore, Question
from kanamemory.learning_store import LearningStore
from kanamemory.first_teaching_dispatch import choose_protected, _teaching_state, _teaching_service
from kanamemory.review_dispatch import stored_review_priority
from kanamemory.kana_learning_intro import finish_introduction, should_introduce
from support.learning_store_fixtures import item, question

from support.first_teaching_dispatch_core_support import NOW, stores, kana, install_queue, normal, course, pick, rows, snapshots


def repeated(store):
    hot=kana('hot','あ','a')
    for ago in (10,5):
        store.record_review(hot.content_id,hot.kind,hot.script,1,now=NOW-timedelta(minutes=ago))
    fresh=kana('fresh','い','i')
    sid=install_queue(store,[hot,fresh])
    return sid,hot,fresh


def test_new_block_is_readonly_and_preserves_full_queue_multiset_until_commit(stores):
    store,learning=stores
    sid,hot,fresh=repeated(store)
    k=normal(store,sid)
    before,original=rows(store),snapshots(store,sid)
    selected=pick(store,learning,k)
    assert selected[0]=='kana' and selected[1]['type']=='introduction'
    assert selected[1]['question']==fresh and selected[1]['dispatch_priority']==(2.5,0)
    assert rows(store)==before and snapshots(store,sid)==original
    committed=store.prepare_next_question(sid,now=NOW,lazy_questions=True,fair_new=True,
        teaching_only=True,teaching_kinds=['kana_choice'])
    assert committed['questions'][committed['question_index']]==fresh
    assert snapshots(store,sid)==original
    assert store.get_card(hot.content_id,hot.kind,hot.script)['review_count']==2
    assert store.get_card(fresh.content_id,fresh.kind,fresh.script) is None


@pytest.mark.parametrize('priority',[(0,0),(1,0),(2,NOW.timestamp()),(3,(NOW-timedelta(days=1)).timestamp()),(4,0),(5,0)])
def test_protection_does_not_preempt_ineligible_normal_head(stores,priority):
    store,learning=stores
    sid,_,_=repeated(store)
    k=normal(store,sid);k['dispatch_priority']=priority
    course(learning)
    before=rows(store)
    assert pick(store,learning,k) is None
    assert rows(store)==before


def test_daily_real_count_is_required_for_new_block_and_frozen_answer_is_not_score(stores):
    store,learning=stores
    hot=kana('hot','あ','a');fresh=kana('fresh','い','i')
    store.record_review('hot','kana_choice','hiragana',1,now=NOW-timedelta(minutes=5))
    sid=install_queue(store,[hot,fresh])
    k=normal(store,sid)
    k['dispatch_priority']=(3,(NOW-timedelta(minutes=1)).timestamp())
    assert pick(store,learning,k) is None
    store.save_session_progress(sid,0,state={'feedback_pending':True,'page':'feedback'})
    before=rows(store)
    assert store.prepare_next_question(sid,now=NOW,teaching_only=True,dry_run=True) is None
    assert rows(store)==before


def test_completed_kana_spends_protection_but_normal_new_work_is_not_capped(stores):
    store,learning=stores
    sid,_,fresh=repeated(store)
    selected=pick(store,learning,normal(store,sid))
    p=store.prepare_next_question(sid,now=NOW,teaching_only=True,fair_new=True,teaching_kinds=['kana_choice'])
    finish_introduction(store,sid,p['question_index'],fresh,now=NOW,fair_new=True)
    second=kana('second','う','u')
    sid=install_queue(store,[kana('hot','あ','a'),second])
    k=normal(store,sid)
    assert pick(store,learning,k) is None
    # Protection is not a quota: ordinary calls can still retrieve fresh heads.
    assert store.prepare_next_question(sid,now=NOW,teaching_only=True,dry_run=True)['questions'][0]==second


@pytest.mark.parametrize('mode',['closed','future','paused','already_exposed'])
def test_ineligible_kana_intro_is_not_selected_or_deleted(stores,mode):
    store,learning=stores
    sid,hot,fresh=repeated(store)
    if mode=='future':
        fresh.metadata['not_before_utc']=(NOW+timedelta(hours=1)).isoformat()
        sid=install_queue(store,[hot,fresh])
    elif mode=='paused':
        store.set_content_control(fresh.content_id,suspended=True,now=NOW)
    elif mode=='already_exposed':
        store.set_setting('kana_introductions_v1',{'version':1,'introduced':{
            'fresh|hiragana':{'content_id':'fresh','script':'hiragana'}}})
    k=normal(store,sid);before=rows(store)
    assert pick(store,learning,k,kinds=[] if mode=='closed' else ['kana_choice']) is None
    assert rows(store)==before


def test_enabled_candidate_after_disabled_intro_is_same_in_preview_and_commit(stores):
    store,learning=stores
    sid,hot,_=repeated(store)
    disabled=kana('disabled','い','i','kana_romaji')
    enabled=kana('enabled','う','u')
    sid=install_queue(store,[hot,disabled,enabled])
    k=normal(store,sid);original=snapshots(store,sid)
    selected=pick(store,learning,k,kinds=['kana_choice'])
    assert selected[1]['question']==enabled
    committed=store.prepare_next_question(sid,now=NOW,teaching_only=True,fair_new=True,teaching_kinds=['kana_choice'])
    assert committed['questions'][committed['question_index']]==enabled
    assert snapshots(store,sid)==original


def test_course_candidates_respect_dependencies_enabled_and_cooldown(stores):
    store,learning=stores
    missing=course(learning,'blocked',depends_on=['missing'])
    future=course(learning,'future')
    with store._db:
        store._db.execute('UPDATE learning_question SET not_before_utc=? WHERE question_id=?',
            ((NOW+timedelta(hours=1)).isoformat(),'q-future'))
    eligible=course(learning,'eligible')
    before=rows(store)
    assert learning.current(['grammar_meaning'],now=NOW,teaching_only=True)['content']['id']==eligible
    assert learning.current([],now=NOW,teaching_only=True) is None
    assert learning.current(['grammar_meaning'],now=NOW,teaching_only=True,teaching_content_ids={missing,future}) is None
    assert rows(store)==before


def test_started_course_continues_across_midnight_without_two_new_day_scores(stores):
    store,learning=stores
    course(learning,'started')
    learning.mark_instruction_exposed('started',now=NOW)
    learning.advance_instruction('started','intro_done',now=NOW)
    other=kana('mature','あ','a')
    store.record_review(other.content_id,other.kind,other.script,4,now=NOW)
    due=datetime.fromisoformat(store.get_card(other.content_id,other.kind,other.script)['due_at_utc'])
    later=due+timedelta(seconds=1)
    assert later.date()>NOW.date()
    sid=install_queue(store,[other,kana('fresh','い','i')])
    # Reopen the same business state, not a new in-memory protection cursor.
    path=store.data_dir;store.close()
    with StudyStore(path,time_zone='UTC') as reopened:
        learning=LearningStore(reopened)
        k=normal(reopened,sid,later)
        assert k['dispatch_priority'][0]==3
        before=rows(reopened)
        selected=pick(reopened,learning,k,now=later)
        assert selected[0]=='course' and selected[1]['content']['id']=='started'
        assert selected[1]['stage']=='guide'
        assert rows(reopened)==before


def test_ready_cross_day_card_hidden_by_same_content_interleave_cannot_be_preempted(stores):
    store,learning=stores
    sid,hot,fresh=repeated(store)
    old=kana('old','う','u')
    store.record_review(old.content_id,old.kind,old.script,1,now=NOW-timedelta(days=1))
    sid=install_queue(store,[hot,old,fresh])
    pending=store.load_session_progress(lazy_questions=True)
    k={'type':'question','question':hot,'session_id':sid,'question_index':0,'state':{},'pending':pending,
       'dispatch_priority':stored_review_priority(store,store.get_card('hot','kana_choice','hiragana'),now=NOW)}
    before=rows(store)
    assert pick(store,learning,k) is None
    assert rows(store)==before


def test_dry_run_never_clears_exhausted_or_all_paused_checkpoint(stores):
    store,_=stores
    q=kana('fresh','い','i');sid=install_queue(store,[q])
    store.set_content_control(q.content_id,suspended=True,now=NOW)
    before=rows(store)
    assert store.prepare_next_question(sid,now=NOW,dry_run=True) is None
    assert rows(store)==before
    assert store.prepare_next_question(sid,now=NOW,teaching_only=True,dry_run=True) is None
    assert rows(store)==before
    with store._db:
        store._db.execute('UPDATE session_checkpoint SET question_index=1 WHERE session_id=?',(sid,))
    before=rows(store)
    assert store.prepare_next_question(sid,now=NOW,dry_run=True) is None
    assert rows(store)==before


def test_course_actual_scores_qualify_and_completed_guide_consumes_only_protection(stores):
    store,learning=stores
    old=NOW-timedelta(days=1)
    learning.enroll(item('old-course',abilities=['understanding']),now=old)
    learning.advance_instruction('old-course','intro_done',now=old)
    learning.advance_instruction('old-course','guide_correct',now=old)
    first=question('old-course',qid='old-history')
    learning.append_questions(first,now=old+timedelta(seconds=61))
    learning.submit(first['id'],first['answer'],True,now=old+timedelta(seconds=61))
    learning.finalize(first['id'],1,now=old+timedelta(seconds=65))
    for n,ago in enumerate((10,5)):
        q=question('old-course',qid=f'old-{n}')
        at=NOW-timedelta(minutes=ago)
        learning.append_questions(q,now=at)
        learning.submit(q['id'],q['answer'],True,now=at)
        learning.finalize(q['id'],1,now=at)
    learning.append_questions(question('old-course',qid='old-next'),now=NOW)
    course(learning,'fresh-course')
    current=learning.current(['grammar_meaning'],now=NOW,fair_new=True)
    assert current['dispatch_priority'][0]==3 and current['content']['id']=='old-course'
    before=rows(store)
    selected=pick(store,learning,None,current)
    assert selected[0]=='course' and selected[1]['content']['id']=='fresh-course'
    assert rows(store)==before
    learning.mark_instruction_exposed('fresh-course',now=NOW)
    learning.advance_instruction('fresh-course','intro_done',now=NOW)
    learning.advance_instruction('fresh-course','guide_correct',now=NOW)
    course(learning,'more-course')
    assert pick(store,learning,None,current) is None
    assert learning.current(['grammar_meaning'],now=NOW,teaching_only=True)['content']['id']=='more-course'


def test_diagnostic_title_exposure_is_not_a_completed_teaching_block(stores):
    store,learning=stores
    sid,_,_=repeated(store)
    course(learning,'already-known')
    learning.set_initial_familiarity('already-known',{'understanding':'familiar'},
        title_answer_exposed=True,now=NOW,fair_new=True)
    assert learning.get_item('already-known')['stage']=='bridge'
    course(learning,'new-rule')
    selected=pick(store,learning,normal(store,sid))
    assert selected is not None


@pytest.mark.parametrize('mode',['direction_paused','content_paused','closed','intro_cooldown','queue_cooldown'])
def test_ineligible_cross_day_kana_does_not_block_protected_teaching(stores,mode):
    store,learning=stores
    _,hot,fresh=repeated(store)
    kind='kana_romaji' if mode=='closed' else 'kana_choice'
    old=kana('old','う','u',kind)
    store.record_review(old.content_id,old.kind,old.script,1,now=NOW-timedelta(days=1))
    if mode=='direction_paused':
        with store._db:
            store._db.execute('UPDATE study_card SET suspended=1 WHERE content_id=?',(old.content_id,))
    elif mode=='content_paused':
        store.set_content_control(old.content_id,suspended=True,now=NOW)
    elif mode=='intro_cooldown':
        store.set_setting('kana_introductions_v1',{'version':1,'introduced':{
            'old|hiragana':{'wait_until_utc':(NOW+timedelta(minutes=2)).isoformat()}}})
    elif mode=='queue_cooldown':
        old.metadata['not_before_utc']=(NOW+timedelta(minutes=2)).isoformat()
    sid=install_queue(store,[hot,old,fresh])
    pending=store.load_session_progress(lazy_questions=True)
    k={'type':'question','question':hot,'session_id':sid,'question_index':0,'state':{},'pending':pending,
       'dispatch_priority':stored_review_priority(store,store.get_card('hot','kana_choice','hiragana'),now=NOW)}
    before=rows(store)
    selected=pick(store,learning,k)
    assert selected[0]=='kana' and selected[1]['question']==fresh
    assert rows(store)==before


@pytest.mark.parametrize('mode',['disabled','suspended','dependency','cooldown'])
def test_unavailable_started_course_cannot_permanently_block_other_first_teaching(stores,mode):
    store,learning=stores
    sid,_,fresh=repeated(store)
    cid=course(learning,'unavailable',**({'depends_on':['missing']} if mode=='dependency' else {}))
    learning.mark_instruction_exposed(cid,now=NOW-timedelta(days=1))
    if mode=='suspended':
        learning.set_suspended(cid,'understanding',True)
    elif mode=='cooldown':
        with store._db:
            store._db.execute('UPDATE learning_question SET not_before_utc=? WHERE question_id=?',
                ((NOW+timedelta(minutes=2)).isoformat(),'q-unavailable'))
    k=normal(store,sid);before=rows(store)
    selected=pick(store,learning,k,enabled=[] if mode=='disabled' else ['grammar_meaning'])
    assert selected[0]=='kana' and selected[1]['question']==fresh
    assert rows(store)==before
    assert store.get_card(fresh.content_id,fresh.kind,fresh.script) is None


@pytest.mark.parametrize('correct',[True,False])
def test_started_real_guide_protection_reaches_first_independent_rating(stores,correct):
    store,learning=stores
    sid,_,_=repeated(store)
    cid=course(learning,'guided')
    learning.mark_instruction_exposed(cid,now=NOW)
    learning.advance_instruction(cid,'intro_done',now=NOW)
    learning.advance_instruction(cid,'guide_correct',now=NOW)
    k=normal(store,sid)
    assert learning.current(['grammar_meaning'],now=NOW,teaching_only=True,
        teaching_content_ids={cid}) is None
    assert pick(store,learning,k,now=NOW) is None
    later=NOW+timedelta(seconds=61)
    before=rows(store)
    selected=pick(store,learning,k,now=later)
    assert selected[0]=='course' and selected[1]['type']=='question'
    assert selected[1]['content']['id']==cid and rows(store)==before
    q=selected[1]['question']
    learning.submit(q['id'],q['answer'] if correct else 'wrong',correct,now=later)
    learning.finalize(q['id'],3 if correct else 1,now=later+timedelta(seconds=4))
    assert learning.get_item(cid)['stage']=='done'
    assert cid not in _teaching_state(store,later+timedelta(seconds=4))[1]
    assert pick(store,learning,k,now=later+timedelta(seconds=4)) is None


def test_yesterdays_guided_block_first_rating_today_spends_today_protection(stores):
    store,learning=stores
    yesterday=NOW-timedelta(days=1)
    course(learning,'rollover')
    learning.mark_instruction_exposed('rollover',now=yesterday)
    learning.advance_instruction('rollover','intro_done',now=yesterday)
    learning.advance_instruction('rollover','guide_correct',now=yesterday)
    learning.submit('q-rollover','正在读书',True,now=yesterday+timedelta(seconds=61))
    assert _teaching_state(store,NOW)[2] is False
    learning.finalize('q-rollover',3,now=NOW)
    assert _teaching_state(store,NOW)[2] is True
    assert 'rollover' not in _teaching_state(store,NOW)[1]


def test_closed_course_reopens_without_blocking_kana_and_enabled_course_resumes_same_block(stores):
    store,learning=stores
    sid,_,fresh=repeated(store)
    cid=course(learning,'toggle')
    learning.mark_instruction_exposed(cid,now=NOW-timedelta(days=1))
    learning.advance_instruction(cid,'intro_done',now=NOW-timedelta(days=1))
    course_question=learning.get_question('q-toggle')
    store.set_setting('learning_preferences_v1',{'enabled':[]})
    path=store.data_dir;store.close()
    with StudyStore(path,time_zone='UTC') as reopened:
        learning=LearningStore(reopened)
        enabled=reopened.get_setting('learning_preferences_v1')['enabled']
        chosen=pick(reopened,learning,normal(reopened,sid),enabled=enabled)
        assert chosen[0]=='kana' and chosen[1]['question']==fresh
        p=reopened.prepare_next_question(sid,now=NOW,teaching_only=True,teaching_kinds=['kana_choice'])
        finish_introduction(reopened,sid,p['question_index'],fresh,now=NOW,fair_new=True)
        assert _teaching_state(reopened,NOW)[2] is True
        reopened.set_setting('learning_preferences_v1',{'enabled':['grammar_meaning']})
    with StudyStore(path,time_zone='UTC') as reopened:
        learning=LearningStore(reopened)
        enabled=reopened.get_setting('learning_preferences_v1')['enabled']
        k=normal(reopened,sid);before=rows(reopened)
        chosen=pick(reopened,learning,k,enabled=enabled)
        # Today's kana exposure reserves its delayed first check; reopening a
        # different course cannot start another protected teaching block.
        assert chosen is None
        assert rows(reopened)==before
        tomorrow=NOW+timedelta(days=1)
        for ago in (10,5):
            reopened.record_review('hot','kana_choice','hiragana',1,now=tomorrow-timedelta(minutes=ago))
        k=normal(reopened,sid,now=tomorrow);before=rows(reopened)
        chosen=pick(reopened,learning,k,enabled=enabled,now=tomorrow)
        # Midnight does not abandon the most recently started teaching block.
        # Its delayed first check continues before the older reopened course.
        assert chosen[0]=='kana' and chosen[1]['question']==fresh
        assert chosen[1]['type']=='question'
        assert learning.get_question('q-toggle')==course_question
        assert learning.get_unit(cid,'understanding')['review_count']==0
        assert rows(reopened)==before


def test_failed_diagnostic_title_anchor_does_not_become_a_fake_completed_guide(stores):
    store,learning=stores
    course(learning,'diagnostic-failure')
    learning.set_initial_familiarity('diagnostic-failure',{'understanding':'familiar'},
        title_answer_exposed=True,now=NOW)
    later=NOW+timedelta(seconds=61)
    learning.submit('q-diagnostic-failure','wrong',False,now=later)
    learning.finalize('q-diagnostic-failure',now=later+timedelta(seconds=4))
    assert learning.get_item('diagnostic-failure')['checkpoint']['initial_familiarity']['route']=='full'
    assert _teaching_state(store,later+timedelta(seconds=4))[2] is False
    learning.mark_instruction_exposed('diagnostic-failure',now=later+timedelta(minutes=1))
    learning.advance_instruction('diagnostic-failure','intro_done',now=later+timedelta(minutes=1))
    learning.advance_instruction('diagnostic-failure','guide_correct',now=later+timedelta(minutes=2))
    assert _teaching_state(store,later+timedelta(minutes=2))[2] is True


def test_first_primary_uses_instant_not_insert_order_or_offset_spelling_and_survives_restore(stores,tmp_path):
    store,learning=stores
    old=NOW-timedelta(hours=14)
    cid='historical'
    learning.enroll(item(cid,abilities=['understanding','application']),now=old)
    learning.advance_instruction(cid,'intro_done',now=old)
    learning.advance_instruction(cid,'guide_correct',now=old)
    for n,at in enumerate((old+timedelta(seconds=61),NOW)):
        q=question(cid,qid=f'historical-{n}',ability=['understanding','application'][n])
        learning.append_questions(q,now=at)
        learning.submit(q['id'],q['answer'],True,now=at)
        learning.finalize(q['id'],1,now=at+timedelta(seconds=4))
    assert _teaching_state(store,NOW+timedelta(seconds=4))[2] is False
    records=[dict(row) for row in store._db.execute('SELECT * FROM learning_event ORDER BY rowid')]
    first=json.loads(records[0]['result_json'])
    first['finalized_at_utc']=datetime.fromisoformat(first['finalized_at_utc']).astimezone(
        timezone(timedelta(hours=14))).isoformat()
    records[0]['result_json']=json.dumps(first)
    with store._db:
        store._db.execute('DELETE FROM learning_event')
        for row in reversed(records):
            store._db.execute('INSERT INTO learning_event VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
    assert _teaching_state(store,NOW+timedelta(seconds=4))[2] is False
    payload=store.export_backup()
    with StudyStore(tmp_path/'restored',time_zone='UTC') as restored:
        LearningStore(restored)
        restored.import_backup(payload)
        assert _teaching_state(restored,NOW+timedelta(seconds=4))[2] is False


@pytest.mark.parametrize('outcome',['unresolved','device_failure','invalid','cancelled'])
def test_two_unscored_checks_spend_only_same_day_priority_and_recover_next_day(stores,outcome):
    store,learning=stores
    sid,hot,_=repeated(store)
    cid=course(learning,'technical')
    guided=NOW-timedelta(minutes=2)
    learning.mark_instruction_exposed(cid,now=guided)
    learning.advance_instruction(cid,'intro_done',now=guided)
    learning.advance_instruction(cid,'guide_correct',now=guided)
    at=NOW
    original_unit=learning.get_unit(cid,'understanding')
    original_hot=store.get_card(hot.content_id,hot.kind,hot.script)
    for index in range(2):
        selected=pick(store,learning,normal(store,sid,now=at),now=at)
        assert selected[0]=='course' and selected[1]['content']['id']==cid
        q=selected[1]['question']
        learning.submit(q['id'],'',None,outcome=outcome,now=at)
        result=learning.finalize(q['id'],now=at+timedelta(seconds=1))
        assert not result['scheduled'] and result['rating'] is None
        assert learning.finalize(q['id'],now=at+timedelta(seconds=2))==result
        assert len(learning.history(cid))==index+1
        at+=timedelta(seconds=66)
        learning.append_questions(question(cid,qid=f'technical-retry-{index}'),now=at)
    k=normal(store,sid,now=at)
    before=rows(store)
    assert pick(store,learning,k,now=at) is None
    assert rows(store)==before
    assert learning.current(['grammar_meaning'],now=at)['type']=='question'
    unit=learning.get_unit(cid,'understanding')
    for key in ('due_at_utc','review_count','scheduler_state_json'):
        assert unit[key]==original_unit[key]
    assert store.get_card(hot.content_id,hot.kind,hot.script)==original_hot
    path=store.data_dir;store.close()
    with StudyStore(path,time_zone='UTC') as reopened:
        learning=LearningStore(reopened)
        assert pick(reopened,learning,normal(reopened,sid,now=at),now=at) is None
        tomorrow=NOW+timedelta(days=1)
        for ago in (10,5):
            reopened.record_review(hot.content_id,hot.kind,hot.script,1,now=tomorrow-timedelta(minutes=ago))
        selected=pick(reopened,learning,normal(reopened,sid,now=tomorrow),now=tomorrow)
        assert selected[0]=='course' and selected[1]['content']['id']==cid
        q=selected[1]['question']
        learning.submit(q['id'],q['answer'],True,now=tomorrow)
        learning.finalize(q['id'],3,now=tomorrow+timedelta(seconds=4))
        assert learning.get_unit(cid,'understanding')['review_count']==1
        assert cid not in _teaching_state(reopened,tomorrow+timedelta(seconds=4))[1]


def test_first_primary_today_stops_other_started_protection_but_not_normal_idle_work(stores):
    store,learning=stores
    sid,_,_=repeated(store)
    yesterday=NOW-timedelta(days=1)
    for cid in ('first-old-block','second-old-block'):
        course(learning,cid)
        learning.advance_instruction(cid,'intro_done',now=yesterday)
        learning.advance_instruction(cid,'guide_correct',now=yesterday)
    selected=pick(store,learning,normal(store,sid),now=NOW)
    assert selected[0]=='course'
    q=selected[1]['question'];cid=q['target_id']
    learning.submit(q['id'],q['answer'],True,now=NOW)
    learning.finalize(q['id'],3,now=NOW+timedelta(seconds=4))
    at=NOW+timedelta(seconds=5)
    k=normal(store,sid,now=at);before=rows(store)
    assert pick(store,learning,k,now=at) is None
    assert rows(store)==before
    ordinary=learning.current(['grammar_meaning'],now=at)
    assert ordinary['type']=='question' and ordinary['content']['id']!=cid


def test_guide_completed_today_only_continues_that_check_and_not_other_started_intro(stores):
    store,learning=stores
    sid,_,_=repeated(store)
    course(learning,'older-intro')
    learning.mark_instruction_exposed('older-intro',now=NOW-timedelta(days=1))
    course(learning,'guided-today')
    learning.advance_instruction('guided-today','intro_done',now=NOW)
    learning.advance_instruction('guided-today','guide_correct',now=NOW)
    assert pick(store,learning,normal(store,sid),now=NOW) is None
    later=NOW+timedelta(seconds=61)
    selected=pick(store,learning,normal(store,sid,now=later),now=later)
    assert selected[0]=='course' and selected[1]['content']['id']=='guided-today'
    assert selected[1]['type']=='question'
    # Disabled current block does not authorize another protected introduction;
    # it also never removes or changes either ordinary queued block.
    before=rows(store)
    assert pick(store,learning,normal(store,sid,now=later),now=later,enabled=[]) is None
    assert rows(store)==before


def test_unscored_budget_is_global_across_started_initial_checks(stores):
    store,learning=stores
    sid,_,_=repeated(store)
    yesterday=NOW-timedelta(days=1)
    for cid in ('failed-one','failed-two','still-unattempted'):
        course(learning,cid)
        learning.advance_instruction(cid,'intro_done',now=yesterday)
        learning.advance_instruction(cid,'guide_correct',now=yesterday)
    for index in range(2):
        at=NOW+timedelta(seconds=index*70)
        selected=pick(store,learning,normal(store,sid,now=at),now=at)
        assert selected[0]=='course'
        q=selected[1]['question']
        learning.submit(q['id'],'',None,outcome='unresolved',now=at)
        learning.finalize(q['id'],now=at+timedelta(seconds=1))
    at=NOW+timedelta(seconds=140)
    k=normal(store,sid,now=at);before=rows(store)
    assert pick(store,learning,k,now=at) is None
    assert rows(store)==before
    assert learning.current(['grammar_meaning'],now=at)['content']['id']=='still-unattempted'
    assert all(learning.get_unit(cid,'understanding')['review_count']==0
               for cid in ('failed-one','failed-two','still-unattempted'))


def test_future_finalized_first_rating_does_not_spend_service_before_its_instant(stores):
    store,learning=stores
    cid=course(learning,'future-finalization')
    yesterday=NOW-timedelta(days=1)
    learning.advance_instruction(cid,'intro_done',now=yesterday)
    learning.advance_instruction(cid,'guide_correct',now=yesterday)
    learning.submit('q-'+cid,'正在读书',True,now=NOW-timedelta(minutes=1))
    learning.finalize('q-'+cid,3,now=NOW+timedelta(minutes=1))
    assert _teaching_service(store,NOW)['first_primary_today'] is False
    assert _teaching_service(store,NOW+timedelta(minutes=1))['first_primary_today'] is True


def test_first_rating_submillisecond_tie_across_midnight_uses_exact_instant(stores):
    store,learning=stores
    cid='precision'
    yesterday=NOW-timedelta(days=1)
    learning.enroll(item(cid,abilities=['understanding','application']),now=yesterday)
    learning.advance_instruction(cid,'intro_done',now=yesterday)
    learning.advance_instruction(cid,'guide_correct',now=yesterday)
    midnight=NOW.replace(hour=0,minute=0,second=0,microsecond=0)
    # SQLite julianday rounds both into the same millisecond bucket. Exact
    # datetime comparison must still identify the previous-day first rating.
    for n in range(2):
        submitted=midnight-timedelta(minutes=3-n)
        q=question(cid,qid=f'precision-{n}',ability=['understanding','application'][n])
        learning.append_questions(q,now=submitted)
        learning.submit(q['id'],q['answer'],True,now=submitted)
    for n,at in enumerate((midnight-timedelta(microseconds=100),midnight+timedelta(microseconds=100))):
        learning.finalize(f'precision-{n}',3,now=at)
    assert _teaching_service(store,NOW)['first_primary_today'] is False


def test_failed_second_diagnostic_reteaching_continues_without_changing_prior_ability(stores):
    store,learning=stores
    sid,_,_=repeated(store)
    old=NOW-timedelta(days=1)
    cid='partial-diagnostic'
    learning.enroll(item(cid),now=old)
    learning.append_questions(question(cid,qid='understood'),now=old)
    learning.append_questions(question(cid,qid='application-failed',ability='application'),now=old)
    learning.set_initial_familiarity(cid,{'understanding':'familiar','application':'familiar'},now=old)
    learning.submit('understood','正在读书',True,now=old)
    learning.finalize('understood',3,now=old)
    previous=learning.get_unit(cid,'understanding')
    learning.submit('application-failed','wrong',False,now=NOW-timedelta(minutes=10))
    learning.finalize('application-failed',now=NOW-timedelta(minutes=10))
    learning.mark_instruction_exposed(cid,now=NOW-timedelta(minutes=5))
    learning.advance_instruction(cid,'intro_done',now=NOW-timedelta(minutes=5))
    learning.advance_instruction(cid,'guide_correct',now=NOW-timedelta(minutes=3))
    learning.append_questions(question(cid,qid='application-after-guide',ability='application'),now=NOW)
    k=normal(store,sid);before=rows(store)
    selected=pick(store,learning,k)
    assert selected[0]=='course' and selected[1]['question']['ability']=='application'
    assert rows(store)==before
    q=selected[1]['question']
    learning.submit(q['id'],q['answer'],True,now=NOW)
    learning.finalize(q['id'],3,now=NOW+timedelta(seconds=4))
    assert _teaching_service(store,NOW+timedelta(seconds=4))['first_primary_today']
    assert cid not in _teaching_service(store,NOW+timedelta(seconds=4))['started']
    assert learning.get_unit(cid,'understanding')==previous


def test_answer_frozen_before_current_guide_cannot_complete_that_teaching_block(stores):
    store,learning=stores
    cid='frozen-before-guide'
    old=NOW-timedelta(days=1)
    learning.enroll(item(cid),now=old)
    learning.append_questions(question(cid,qid='old-answer'),now=old)
    learning.advance_instruction(cid,'intro_done',now=old)
    learning.advance_instruction(cid,'guide_correct',now=old)
    learning.submit('old-answer','正在读书',True,now=NOW-timedelta(minutes=2))
    # Simulate a newer explicit guide while another ability's answer was
    # already frozen. Finalizing the old answer is not retrieval of this guide.
    with store._db:
        store._db.execute('UPDATE learning_content SET guided_at_utc=? WHERE content_id=?',
            ((NOW-timedelta(minutes=1)).isoformat(),cid))
    learning.finalize('old-answer',3,now=NOW)
    assert _teaching_service(store,NOW)['first_primary_today'] is False
