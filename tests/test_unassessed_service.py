"""Real stores and real earned due reviews; no simulated/mocked credit."""
from datetime import timedelta, timezone
import json

import pytest

from support.first_teaching_dispatch_core_support import NOW
from support.first_teaching_dispatch_core_support import stores
from support.first_teaching_dispatch_core_support import kana
from support.first_teaching_dispatch_core_support import install_queue
from support.first_teaching_dispatch_core_support import normal
from support.first_teaching_dispatch_core_support import course
from support.first_teaching_dispatch_core_support import rows
from support.first_teaching_backlog_support import backlog
from support.first_teaching_backlog_support import serve
from support.learning_store_fixtures import item, question, ready
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.first_teaching_dispatch import choose_protected, _teaching_service, _ability_service, due_reviews_served
from kanamemory.new_dispatch import SETTING

from support.unassessed_service_support import KINDS, secondary, setup, protected


@pytest.mark.parametrize('service_count',[64,128])
def test_real_earned_service_reaches_unassessed_ability_despite_old_due(stores,service_count):
    store,learning=stores
    sid,qid=setup(store,learning)
    if service_count>64:
        serve(store,64,NOW-timedelta(minutes=3),prefix='more')
    actual=protected(store,learning,sid)
    assert actual[0]=='course' and actual[1]['question']['id']==qid
    assert actual[1]['protected_unassessed_ability'] is True
    assert actual[1]['service_reason']=='first_ability_check'
    assert actual[1]['dispatch_priority']==(2.5,0)
    assert learning.get_unit('known-content','application')['review_count']==0


def test_first_formal_rating_consumes_credit_and_next_course_slot_serves_intro(stores):
    store,learning=stores
    sid,qid=setup(store,learning,fresh_course=True)
    assert protected(store,learning,sid)[1]['question']['id']==qid
    learning.submit(qid,'正在读书',True,now=NOW)
    # Frozen feedback still wins before a real finalized rating.
    assert learning.current(KINDS,now=NOW)['dispatch_priority']==(0,0)
    learning.finalize(qid,3,now=NOW+timedelta(seconds=2),fair_new=True)
    at=NOW+timedelta(minutes=2)
    assert protected(store,learning,sid,at) is None
    state=_ability_service(store,at,_teaching_service(store,at))
    assert state['latest']==NOW+timedelta(seconds=2)
    serve(store,64,at,prefix='second-slot')
    at+=timedelta(minutes=2)
    selected=protected(store,learning,sid,at)
    assert selected[1]['type']=='instruction' and selected[1]['content']['id']=='fresh-course'


def test_completed_new_intro_returns_next_course_slot_to_unassessed_ability(stores):
    store,learning=stores
    sid,qid=setup(store,learning)
    # Another already taught content also awaits its second ability.
    second=secondary(store,learning,'second-content')
    course(learning,'fresh-course')
    assert protected(store,learning,sid)[1]['question']['id']==qid
    learning.submit(qid,'正在读书',True,now=NOW)
    learning.finalize(qid,3,now=NOW+timedelta(seconds=1),fair_new=True)
    at=NOW+timedelta(minutes=2)
    serve(store,64,at,prefix='intro-slot')
    at+=timedelta(minutes=2)
    assert protected(store,learning,sid,at)[1]['content']['id']=='fresh-course'
    learning.mark_instruction_exposed('fresh-course',now=at)
    when=ready(learning,'fresh-course',at)
    assert protected(store,learning,sid,when)[1]['question']['id']=='q-fresh-course'
    learning.submit('q-fresh-course','正在读书',True,now=when)
    learning.finalize('q-fresh-course',3,now=when+timedelta(seconds=1),fair_new=True)
    serve(store,64,when+timedelta(minutes=1),prefix='ability-slot')
    chosen=protected(store,learning,sid,when+timedelta(minutes=3))
    assert chosen[1]['question']['id']==second


def test_secondary_first_event_seek_matches_exact_timestamp_oracle(stores):
    store,learning=stores
    _,qid=setup(store,learning)
    learning.submit(qid,'正在读书',True,now=NOW)
    learning.finalize(qid,3,now=NOW+timedelta(microseconds=800),fair_new=True)
    # Create a genuine repeat, then make its stored row a sub-ms adjacent
    # timestamp bucket with an alternate UTC offset. The SQL must return all
    # first-bucket candidates; exact Python instants select the real first.
    learning.append_questions(question('known-content',qid='repeat',ability='application',kind='grammar_cloze'),now=NOW)
    at=NOW+timedelta(days=1)
    learning.submit('repeat','正在读书',True,now=at)
    learning.finalize('repeat',3,now=at,fair_new=True)
    with store._db:
        row=store._db.execute("SELECT result_json FROM learning_event WHERE question_id='repeat'").fetchone()
        result=json.loads(row[0]);result['finalized_at_utc']=(NOW+timedelta(microseconds=900)).astimezone(timezone(timedelta(hours=9))).isoformat()
        store._db.execute("UPDATE learning_event SET occurred_at_utc=?,result_json=? WHERE question_id='repeat'",
            ((NOW+timedelta(microseconds=850)).isoformat(),json.dumps(result)))
    before=rows(store)
    service=_teaching_service(store,at)
    result=_ability_service(store,at,service)
    assert result['latest']==NOW+timedelta(microseconds=800)
    assert rows(store)==before


def test_successful_diagnostic_is_baseline_for_later_non_diagnostic_first_rating(stores):
    store,learning=stores
    at=NOW-timedelta(days=1)
    cid='diagnostic-then-taught'
    learning.enroll(item(cid,family='word',abilities=['understanding','reading','use']),now=at)
    learning.append_questions([question(cid,qid='d-meaning',kind='vocab_meaning'),
        question(cid,qid='d-reading',ability='reading',kind='vocab_reading'),
        question(cid,qid='d-use',ability='use',kind='vocab_usage')],now=at)
    learning.set_initial_familiarity(cid,{key:'familiar' for key in ('understanding','reading','use')},now=at)
    learning.submit('d-meaning','正在读书',True,now=at)
    learning.finalize('d-meaning',3,now=at)
    at+=timedelta(minutes=2)
    failed=learning.submit('d-reading','wrong',False,now=at)
    assert failed['diagnostic_needs_teaching']
    learning.finalize('d-reading',None,now=at)
    when=ready(learning,cid,at+timedelta(minutes=2))
    learning.append_questions(question(cid,qid='d-reading-retry',ability='reading',kind='vocab_reading'),now=when)
    learning.submit('d-reading-retry','正在读书',True,now=when)
    learning.finalize('d-reading-retry',3,now=when+timedelta(seconds=1),fair_new=True)
    service=_teaching_service(store,NOW)
    assert _ability_service(store,NOW,service)['latest']==when+timedelta(seconds=1)
    sid=backlog(store)
    serve(store,64,NOW-timedelta(minutes=3))
    kinds=['vocab_meaning','vocab_reading','vocab_usage']
    chosen=protected(store,learning,sid,kinds=kinds)
    assert chosen[1]['question']['id']=='d-use'
    learning.submit('d-use','正在读书',True,now=NOW)
    learning.finalize('d-use',3,now=NOW+timedelta(seconds=1),fair_new=True)
    course(learning,'after-diagnostic')
    assert protected(store,learning,sid,NOW+timedelta(minutes=2),kinds=kinds+KINDS) is None


def test_restart_keeps_question_draft_credit_and_does_not_create_another_unit(stores):
    store,learning=stores
    sid,qid=setup(store,learning)
    normal(store,sid,NOW)
    before=rows(store);folder=store.data_dir
    store.close()
    with StudyStore(folder,time_zone='UTC') as reopened:
        resumed=LearningStore(reopened)
        assert rows(reopened)==before
        assert protected(reopened,resumed,sid)[1]['question']['id']==qid
        assert rows(reopened)==before


@pytest.mark.parametrize('mode',['disabled','paused','future','previous-primary'])
def test_ineligible_or_previously_tested_ability_does_not_take_protected_service(stores,mode):
    store,learning=stores
    sid,qid=setup(store,learning)
    kinds=KINDS
    if mode=='disabled':
        kinds=['grammar_meaning']
    elif mode=='paused':
        learning.set_suspended('known-content','application',True)
    elif mode=='future':
        with store._db:
            store._db.execute('UPDATE learning_question SET not_before_utc=? WHERE question_id=?',
                ((NOW+timedelta(days=1)).isoformat(),qid))
    elif mode=='previous-primary':
        learning.submit(qid,'正在读书',True,now=NOW-timedelta(minutes=10))
        learning.finalize(qid,3,now=NOW-timedelta(minutes=10),fair_new=True)
        learning.append_questions(question('known-content',qid='another',ability='application',kind='grammar_cloze'),now=NOW)
        # A legacy/repaired count cannot erase the original primary evidence.
        with store._db:
            store._db.execute("UPDATE learning_unit SET review_count=0 WHERE content_id='known-content' AND ability='application'")
    assert protected(store,learning,sid,kinds=kinds) is None


def test_disabling_and_reenabling_keeps_original_pending_and_credit(stores):
    store,learning=stores
    sid,qid=setup(store,learning)
    original=learning.get_question(qid)
    assert protected(store,learning,sid,kinds=['grammar_meaning']) is None
    assert protected(store,learning,sid)[1]['question']['id']==qid
    assert learning.get_question(qid)==original


@pytest.mark.parametrize('priority',[(0,0),(1,0),(2,NOW.timestamp())])
def test_feedback_real_drafts_and_urgent_steps_still_win(stores,priority):
    store,learning=stores
    sid,qid=setup(store,learning)
    assert protected(store,learning,sid,priority=priority) is None


@pytest.mark.parametrize('last,expected',[('course','kana'),('kana','course')])
def test_existing_kana_course_rotation_includes_first_ability_slot(stores,last,expected):
    store,learning=stores
    _,qid=setup(store,learning)
    sid=install_queue(store,[kana('old-waiting','あ','a'),kana('fresh-kana','い','i')])
    store.set_setting(SETTING,{'version':1,'last_lane':last,'last_kana_band':0})
    chosen=protected(store,learning,sid)
    assert chosen[0]==expected
    if expected=='course':assert chosen[1]['question']['id']==qid
    else:assert chosen[1]['question'].content_id=='fresh-kana'


def test_an_opened_intro_still_finishes_before_another_ability(stores):
    store,learning=stores
    sid,_=setup(store,learning,fresh_course=True)
    learning.mark_instruction_exposed('fresh-course',now=NOW-timedelta(minutes=2))
    chosen=protected(store,learning,sid)
    assert chosen[1]['content']['id']=='fresh-course'
    assert chosen[1]['new_dispatch_continue']


def test_duplicate_rating_does_not_refresh_consumption_anchor(stores):
    store,learning=stores
    sid,qid=setup(store,learning)
    learning.submit(qid,'正在读书',True,now=NOW)
    learning.finalize(qid,3,now=NOW+timedelta(seconds=1),fair_new=True)
    first=_ability_service(store,NOW+timedelta(seconds=2),_teaching_service(store,NOW+timedelta(seconds=2)))
    learning.finalize(qid,3,now=NOW+timedelta(days=1),fair_new=True)
    assert _ability_service(store,NOW+timedelta(days=1),_teaching_service(store,NOW+timedelta(days=1)))==first


def test_auxiliary_and_unscored_attempts_do_not_manufacture_first_rating(stores):
    store,learning=stores
    sid,qid=setup(store,learning)
    initial=_ability_service(store,NOW,_teaching_service(store,NOW))
    assert initial['latest'] is None
    from kanamemory.learning_evidence import LearningEvidenceStore
    with store._db:
        store._db.execute('BEGIN IMMEDIATE')
        LearningEvidenceStore(store).record_attempt('synthetic-auxiliary',learning.get_question(qid),
            {'correct':True,'outcome':'correct','assisted':False,'answered_text':'正在读书'},
            source='tutor',role='auxiliary',trusted=True,adjust=True,now=NOW)
    assert _ability_service(store,NOW,_teaching_service(store,NOW))==initial
    assert protected(store,learning,sid)[1]['question']['id']==qid
    learning.submit(qid,'',None,now=NOW,outcome='device_failure')
    learning.finalize(qid,None,now=NOW+timedelta(seconds=1),fair_new=True)
    after=_ability_service(store,NOW+timedelta(seconds=2),_teaching_service(store,NOW+timedelta(seconds=2)))
    assert after['latest'] is None and after['unscored_today']==1
    assert learning.get_unit('known-content','application')['review_count']==0


def test_current_default_result_unchanged_and_new_scope_is_explicit(stores):
    store,learning=stores
    _,qid=setup(store,learning,fresh_course=True)
    original=learning.current(KINDS,now=NOW,fair_new=True)
    assert original['question']['id']==qid
    first=learning.current(KINDS,now=NOW,fair_new=True,teaching_only=True)
    assert first['content']['id']=='fresh-course'
    with pytest.raises(ValueError):learning.current(KINDS,now=NOW,include_unassessed=True)


def test_successful_second_diagnostic_consumes_service_but_earns_no_due_credit(stores):
    store,learning=stores
    sid,qid=setup(store,learning,diagnostic=True,fresh_course=True)
    assert protected(store,learning,sid)[1]['question']['id']==qid
    answer=learning.submit(qid,'正在读书',True,now=NOW)
    assert answer['initial_diagnostic']
    learning.finalize(qid,3,now=NOW+timedelta(seconds=1),fair_new=True)
    assert due_reviews_served(store,NOW+timedelta(minutes=2),NOW-timedelta(seconds=1),limit=100)==0
    assert _ability_service(store,NOW+timedelta(minutes=2),_teaching_service(store,NOW+timedelta(minutes=2)))['latest']==NOW+timedelta(seconds=1)
    assert protected(store,learning,sid,NOW+timedelta(minutes=2)) is None


def test_failed_second_diagnostic_keeps_its_teaching_block_and_cannot_reuse_credit_elsewhere(stores):
    store,learning=stores
    sid,qid=setup(store,learning,diagnostic=True,fresh_course=True)
    assert protected(store,learning,sid)[1]['question']['id']==qid
    answer=learning.submit(qid,'wrong',False,now=NOW)
    assert answer['diagnostic_needs_teaching']
    learning.finalize(qid,None,now=NOW+timedelta(seconds=1),fair_new=True)
    learning.append_questions(question('known-content',qid='retry-known',ability='application',kind='grammar_cloze'),now=NOW)
    at=NOW+timedelta(minutes=2)
    actual=protected(store,learning,sid,at)
    assert actual[1]['content']['id']=='known-content' and actual[1]['type']=='instruction'
    # Closing only that ability cannot recycle its old allowance into fresh-course.
    assert protected(store,learning,sid,at,kinds=['grammar_meaning']) is None
    assert due_reviews_served(store,at,NOW-timedelta(seconds=1),limit=100)==0
    assert learning.get_unit('known-content','application')['review_count']==0
    when=ready(learning,'known-content',at)
    assert protected(store,learning,sid,when)[1]['question']['id']=='retry-known'
    learning.submit('retry-known','正在读书',True,now=when)
    learning.finalize('retry-known',3,now=when+timedelta(seconds=1),fair_new=True)
    assert protected(store,learning,sid,when+timedelta(minutes=2)) is None


@pytest.mark.parametrize('diagnostic',[False,True])
def test_two_device_failures_limit_priority_without_rating_or_credit_gain(stores,diagnostic):
    store,learning=stores
    sid,qid=setup(store,learning,diagnostic=diagnostic)
    at=NOW
    for attempt in range(2):
        assert protected(store,learning,sid,at)[1]['question']['id']==qid
        learning.submit(qid,'',None,outcome='device_failure',now=at)
        learning.finalize(qid,None,now=at+timedelta(seconds=1),fair_new=True)
        qid='failed-device-'+str(attempt)
        learning.append_questions(question('known-content',qid=qid,ability='application',kind='grammar_cloze'),now=at)
        at+=timedelta(minutes=2)
    service=_ability_service(store,at,_teaching_service(store,at))
    assert service['latest'] is None and service['unscored_today']==2
    assert protected(store,learning,sid,at) is None
    assert learning.get_unit('known-content','application')['review_count']==0
    assert due_reviews_served(store,at,NOW-timedelta(seconds=1),limit=100)==0
