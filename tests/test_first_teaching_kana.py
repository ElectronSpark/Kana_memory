from datetime import datetime,timedelta,timezone
from copy import deepcopy
from support.first_teaching_dispatch_core_support import stores
from support.first_teaching_dispatch_core_support import kana
from support.first_teaching_dispatch_core_support import install_queue
from support.first_teaching_dispatch_core_support import NOW
from support.first_teaching_dispatch_core_support import rows
from kanamemory.domain import Question
from kanamemory.first_teaching_kana import teaching_state,probe
from kanamemory.first_teaching_dispatch import choose_protected
from kanamemory.kana_learning_intro import finish_introduction,select_initial_familiarity


def state(store,now=NOW):
    start,end=map(datetime.fromisoformat,store._day_bounds(now))
    return teaching_state(store,now,start,end)


def introduce(store,q,now=NOW):
    sid=install_queue(store,[q])
    finish_introduction(store,sid,0,q,now=now)
    return sid


def test_kana_first_test_is_readonly_obeys_delay_and_ends_on_any_real_rating(stores):
    store,_=stores
    q=kana('fresh','あ','a')
    sid=introduce(store,q)
    assert state(store)['started']=={('fresh','hiragana')}
    before=rows(store)
    assert probe(store,['kana_choice'],NOW,slots={('fresh','hiragana')}) is None
    assert rows(store)==before
    later=NOW+timedelta(seconds=61)
    before=rows(store)
    selected=probe(store,['kana_choice'],later,slots={('fresh','hiragana')})
    assert selected['type']=='question' and selected['question']==q and rows(store)==before
    store.record_review('fresh','kana_choice','hiragana',1,now=later)
    result=state(store,later)
    assert not result['started'] and result['first_primary_today']


def test_script_specific_first_event_does_not_credit_sibling_or_familiarity(stores):
    store,_=stores
    h=kana('pair','あ','a')
    k=Question('pair','kana_choice','katakana','ア','a',['a','i'],metadata=deepcopy(h.metadata))
    introduce(store,h)
    introduce(store,k)
    store.record_review('pair','kana_choice','hiragana',1,now=NOW+timedelta(seconds=61))
    result=state(store,NOW+timedelta(seconds=61))
    assert result['started']=={('pair','katakana')}
    familiar=kana('known','い','i',kana_familiarity_v1=True)
    sid=install_queue(store,[familiar])
    select_initial_familiarity(store,sid,0,familiar,'familiar',now=NOW)
    assert ('known','hiragana') not in state(store)['started']
    assert store.get_card('known','kana_choice','hiragana') is None


def test_disabled_paused_and_future_queue_do_not_get_first_test_protection(stores):
    store,_=stores
    q=kana('fresh','あ','a')
    sid=introduce(store,q)
    later=NOW+timedelta(seconds=61)
    before=rows(store)
    assert probe(store,[],later,slots={('fresh','hiragana')}) is None
    assert rows(store)==before
    store.set_content_control('fresh',suspended=True,now=later)
    before=rows(store)
    assert probe(store,['kana_choice'],later,slots={('fresh','hiragana')}) is None
    assert rows(store)==before
    store.set_content_control('fresh',suspended=False,now=later)
    q.metadata['not_before_utc']=(later+timedelta(hours=1)).isoformat()
    sid=install_queue(store,[q])
    before=rows(store)
    assert probe(store,['kana_choice'],later,slots={('fresh','hiragana')}) is None
    assert rows(store)==before


def test_future_real_event_is_not_credited_before_its_actual_completion(stores):
    store,_=stores
    q=kana('fresh','あ','a')
    introduce(store,q)
    future=NOW+timedelta(days=1)
    store.record_review('fresh','kana_choice','hiragana',1,now=future)
    assert state(store)['started']=={('fresh','hiragana')}
    assert not state(store)['first_primary_today']


def test_real_kana_technical_attempts_use_existing_unique_rows_and_no_score(stores):
    store,_=stores
    q=kana('fresh','あ','a')
    introduce(store,q)
    later=NOW+timedelta(seconds=61)
    for n in range(2):
        store.record_review('fresh','kana_choice','hiragana',None,audio_ok=False,
                            now=later+timedelta(seconds=n))
    result=state(store,later+timedelta(seconds=3))
    assert result['initial_unscored_today']==2 and result['started']=={('fresh','hiragana')}
    assert store.get_card('fresh','kana_choice','hiragana') is None
    assert state(store,later+timedelta(days=1))['initial_unscored_today']==0
