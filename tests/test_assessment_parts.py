from copy import deepcopy
import json

import pytest

from kanamemory.assessment_contract import for_question, observations, validate_result
from kanamemory.assessment_parts import aggregate
from kanamemory.learning_store import validate_backup_rows
from support.learning_store_fixtures import stores
from support.pitch_practice_support import item
from support.pitch_practice_support import question
from support.pitch_practice_support import perform
from support.learning_store_fixtures import ready
from support.learning_store_fixtures import START


def part(identity, outcome, *, role='auxiliary', target='A', assisted=False):
    return {'part_id':identity, 'unit':{'knowledge_id':target,'ability':'use','script':''},
            'role':role,'outcome':outcome,'correct':True if outcome=='correct' else False if outcome=='incorrect' else None,
            'assisted':assisted,'answered_text':identity}


def test_multiple_references_to_same_unit_are_reduced_once_without_erasing_parts():
    raw = [part('first','correct'),part('second','correct'),part('other','incorrect',target='B')]
    saved = deepcopy(raw)
    reduced = aggregate(raw)
    assert len(reduced)==2 and reduced[0]['part_ids']==['first','second']
    assert reduced[0]['correct'] is True and reduced[1]['correct'] is False
    assert raw == saved


@pytest.mark.parametrize('statuses',[('correct','incorrect'),('incorrect','unresolved'),('correct','device_failure')])
def test_contradiction_or_missing_attribution_without_primary_is_unresolved(statuses):
    value=aggregate([part(str(i),status) for i,status in enumerate(statuses)])[0]
    assert value['outcome']=='unresolved' and value['correct'] is None


def test_declared_primary_owns_outcome_and_same_ability_exposure_is_not_ignored():
    value=aggregate([part('whole','incorrect',role='primary'),part('piece','correct',assisted=True)])[0]
    assert value['role']=='primary' and value['correct'] is False and value['assisted']
    with pytest.raises(ValueError,match='one declared primary'):
        aggregate([part('a','correct',role='primary'),part('b','incorrect',role='primary')])


def test_background_never_grants_progress_or_counts_as_a_scored_part():
    value=aggregate([part('background','correct',role='background')])[0]
    assert value['role']=='exposure' and value['outcome']=='unresolved'


def test_pitch_parts_are_only_controls_the_learner_actually_supplies():
    q=question()
    assert [p['id'] for p in for_question(q)['scored_parts']]==['answer','pitch:levels']
    q=question(learner=True,segmentation=True)
    assert len(for_question(q)['scored_parts'])==4
    result={'answered_text':'unchanged raw input','outcome':'incorrect','correct':False,'assisted':False,'pitch_errors':['morae']}
    raw=observations(q,result)
    assert raw[0]['answered_text']=='unchanged raw input'
    assert {p['part_id']:p['outcome'] for p in raw}=={
        'answer':'incorrect','pitch:morae':'incorrect','pitch:boundaries':'unresolved','pitch:levels':'unresolved'}
    assert len(aggregate(raw))==1 and aggregate(raw)[0]['correct'] is False


def test_parts_survive_reopen_and_backup_and_settle_only_one_formal_review(stores):
    store,learning=stores
    lesson,q=item(),question()
    learning.enroll(lesson,now=START);learning.append_questions(q,now=START)
    when=ready(learning,lesson['id'])
    answer=perform(q,['H','L','L'])['answer']
    submitted=learning.submit(q['id'],answer,True,now=when)
    assert len(submitted['observations'])==2 and len(submitted['unit_observations'])==1
    store.import_backup(store.export_backup())
    assert learning.get_question(q['id'])['result']==submitted
    event=learning.finalize(q['id'],3,now=when)
    assert learning.finalize(q['id'],1,now=when)==event
    assert learning.get_unit(q['target_id'],q['ability'])['review_count']==1
    evidence=store._db.execute('SELECT snapshot_json FROM learning_evidence').fetchone()
    assert json.loads(evidence[0])['opportunity']['part_ids']==['answer','pitch:levels']
    validate_backup_rows(store._db)
    tampered=deepcopy(submitted);tampered['unit_observations'][0]['correct']=False
    with pytest.raises(ValueError,match='parts disagree'):validate_result(q,tampered)
