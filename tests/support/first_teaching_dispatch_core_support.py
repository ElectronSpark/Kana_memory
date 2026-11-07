"""Shared first teaching dispatch core fixtures and assertions; no collected scenarios."""
from collections import Counter
from kanamemory.learning_store import LearningStore
from kanamemory.domain import Question
from kanamemory.domain import StudyStore
from kanamemory.first_teaching_dispatch import choose_protected
from datetime import datetime
from support.learning_store_fixtures import item
import json
import pytest
from support.learning_store_fixtures import question
from kanamemory.review_dispatch import stored_review_priority
from datetime import timezone


NOW = datetime(2027,1,2,12,tzinfo=timezone.utc)


@pytest.fixture
def stores(tmp_path):
    with StudyStore(tmp_path/'candidate',time_zone='UTC') as store:
        yield store,LearningStore(store)


def kana(cid,symbol,answer,kind='kana_choice',**metadata):
    return Question(cid,kind,'hiragana',symbol,answer,['a','i','u','e'],metadata={
        'kana_intro_v1':True,'practice_config':{'schedule_mode':'spaced'},**metadata})


def install_queue(store,questions,state=None):
    sid=store.start_session(questions,now=NOW)
    # Expected fixtures include the contract frozen by the real queue commit.
    for source, saved in zip(questions, store.get_session_questions(sid)):
        source.metadata.update(saved.metadata)
    store.save_session_progress(sid,0,state=state or {})
    return sid


def normal(store,sid,now=NOW):
    p=store.prepare_next_question(sid,now=now,fair_new=True,lazy_questions=True)
    if not p or p.get('waiting_until_utc'):
        return None
    i=p['question_index'];q=p['questions'][i]
    priority=stored_review_priority(store,store.get_card(q.content_id,q.kind,q.script),now=now)
    return {'type':'question','question':q,'session_id':sid,'question_index':i,
            'pending':p,'state':p['state'],'dispatch_priority':priority}


def course(learning,cid='new-course',**kw):
    learning.enroll(item(cid,abilities=['understanding'],**kw),now=NOW)
    learning.append_questions(question(cid,qid='q-'+cid),now=NOW)
    return cid


def pick(store,learning,k,c=None,now=NOW,**kw):
    return choose_protected(store,learning,k,c,enabled_kinds=kw.get('enabled',['grammar_meaning']),
                            kana_kinds=kw.get('kinds',['kana_choice']),now=now)


def rows(store):
    return tuple(store._db.iterdump())


def snapshots(store,sid):
    return Counter(json.dumps(vars(q),sort_keys=True,ensure_ascii=False) for q in store.get_session_questions(sid))
