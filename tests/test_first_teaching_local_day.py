"""Protection follows the same device-local day as real daily review counts."""
from datetime import datetime, timedelta, timezone

from support.first_teaching_dispatch_core_support import kana
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.first_teaching_dispatch import _teaching_state, choose_protected
from kanamemory.review_dispatch import reviews_today, stored_review_priority


def head(store,sid,now):
    pending=store.prepare_next_question(sid,now=now,lazy_questions=True,fair_new=True)
    q=pending['questions'][pending['question_index']]
    return {'type':'question','question':q,'pending':pending,'state':pending['state'],
            'session_id':sid,'question_index':pending['question_index'],
            'dispatch_priority':stored_review_priority(store,store.get_card(q.content_id,q.kind,q.script),now=now)}


def test_local_midnight_not_utc_midnight_controls_protection_and_actual_scores(tmp_path):
    at=datetime(2027,1,2,2,30,tzinfo=timezone.utc)  # Jan 1, 21:30 in Toronto.
    with StudyStore(tmp_path/'local-day',time_zone='America/Toronto') as store:
        learning=LearningStore(store)
        old=kana('hot','あ','a');fresh=kana('fresh','い','i')
        for ago in (10,5):
            store.record_review('hot','kana_choice','hiragana',1,now=at-timedelta(minutes=ago))
        sid=store.start_session([old,fresh],now=at)
        store.save_session_progress(sid,0,state={})
        # Freeze the actual queued snapshot after its declared native routing.
        from copy import deepcopy
        saved = store.get_session_questions(sid)[1]
        payload = deepcopy(saved)
        route = payload.metadata.pop('kana_ability_route_v1')
        assert route['native_session_id'] == sid and route['skill'] == fresh.kind
        assert payload == fresh
        fresh = saved
        # UTC date changed, but this teaching and both scores belong to the
        # same local day. Offset-bearing saved timestamps also mean instants.
        taught=datetime(2027,1,1,23,tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=9)))
        store.set_setting('kana_introductions_v1',{'version':1,'introduced':{
            'earlier|hiragana':{'introduced_at_utc':taught.isoformat()}}})
        start,_,completed=_teaching_state(store,at)
        assert start==datetime(2027,1,1,5,tzinfo=timezone.utc) and completed
        counts=reviews_today(store,at)[1]
        assert counts[store.get_card('hot','kana_choice','hiragana')['card_id']]==2
        assert choose_protected(store,learning,head(store,sid,at),None,
            enabled_kinds=[],kana_kinds=['kana_choice'],now=at) is None
        midnight=datetime(2027,1,2,5,1,tzinfo=timezone.utc)
        assert _teaching_state(store,midnight)[2] is False
        assert reviews_today(store,midnight)[1] == {
            store._card_key('fresh','kana_choice','hiragana'): 0}
        # Old real due times keep their cross-day priority; resetting the
        # protection opportunity cannot move yesterday's overdue work.
        assert choose_protected(store,learning,head(store,sid,midnight),None,
            enabled_kinds=[],kana_kinds=['kana_choice'],now=midnight) is None
        for delta in (2,7):
            store.record_review('hot','kana_choice','hiragana',1,now=midnight+timedelta(minutes=delta))
        later=midnight+timedelta(minutes=12)
        selected=choose_protected(store,learning,head(store,sid,later),None,
            enabled_kinds=[],kana_kinds=['kana_choice'],now=later)
        assert selected[0]=='kana' and selected[1]['question']==fresh
