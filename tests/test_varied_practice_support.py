"""Background help remains separate from targets and answers in new tasks."""
from copy import deepcopy

import pytest

from kanamemory import learning_questions
from kanamemory.question_support import _practice_background_context, learning_question_display


def question():
    return {'target_id':'grammar:demo:place','family':'grammar','ability':'application',
        'kind':'grammar_cloze','prompt':'公園（　）本を読みます。','answer':'で','accepted_answers':['で'],
        'practice_source':{'template_sha256':'synthetic-test-binding'},
        'practice':{'target_spans':[{'start':2,'end':5,'quote':'（　）'}]}}


@pytest.fixture
def reviewed(monkeypatch):
    def resolve(q):
        if q.get('prompt') != question()['prompt']:
            return {}
        return {'background_glosses':[
            {'start':0,'end':2,'surface':'公園','reading':'こうえん','meaning':'公园','target_id':'word:demo:park'},
            {'start':5,'end':6,'surface':'本','reading':'ほん','meaning':'书','target_id':'word:demo:book'},
            {'start':2,'end':5,'surface':'（　）','meaning':'在这里用で'},
        ]}
    monkeypatch.setattr(learning_questions,'reviewed_practice_context',resolve)
    return resolve


def test_only_public_non_target_lexical_support_survives(reviewed):
    original=question()
    result=_practice_background_context(original)
    assert [s['surface'] for s in result['background_glosses']]==['公園','本']
    assert original==question()


@pytest.mark.parametrize('flag', ['tests_reading','tests_pitch','requires_audio','audio_only','has_hidden_transcript','ai_generated'])
def test_no_background_hint_channel_for_hidden_or_reading_tasks(reviewed,flag):
    q=question();q[flag]=True
    assert _practice_background_context(q)=={}


def test_answer_meaning_and_target_span_are_excluded(reviewed):
    q=question();q['answer']='公园';q['accepted_answers']=['公园']
    assert [s['surface'] for s in _practice_background_context(q)['background_glosses']]==['本']
    q=question();q['practice']['target_spans']=[{'start':0,'end':2,'quote':'公園'}]
    assert [s['surface'] for s in _practice_background_context(q)['background_glosses']]==['本']


def test_no_copied_registry_flag_or_raw_gloss_can_authorize_changed_prompt(reviewed):
    q=question();q['prompt']='別の問題。'
    q['background_glosses']=[{'surface':'答え','meaning':'秘密の答え'}]
    q['context_hint']='秘密の答え'
    result=learning_question_display(q)
    assert 'background_glosses' not in result and 'context_hint' not in result


def test_guided_help_is_allowed_but_does_not_change_evidence_or_schedule(reviewed):
    q=question();q.update(guided=True,evidence_mode='exposure_only')
    before=deepcopy(q)
    assert _practice_background_context(q)['background_glosses']
    assert q==before
