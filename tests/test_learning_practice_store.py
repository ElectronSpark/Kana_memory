"""Bounded history and frozen guided groups on real, disposable SQLite stores."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore, TABLES, validate_backup_rows, _ability_due
from kanamemory.learning_questions import build_learning_question
from kanamemory.learning_practice_policy import select_practice_plan
from support.learning_practice_policy_support import lesson
from support.learning_practice_policy_support import CID

from support.learning_practice_store_support import START, all_rows, answer_guide


@pytest.fixture
def stores(tmp_path):
    with StudyStore(tmp_path / 'synthetic', time_zone='UTC') as store:
        yield store, LearningStore(store)


def introduced(learning, item):
    learning.enroll(item, now=START)
    learning.advance_instruction(item['id'], 'intro_done', now=START)
    learning.advance_instruction(item['id'], 'guide_correct', now=START)
    return START + timedelta(seconds=61)


def formal(learning, item, kind, variant, qid, now):
    q = build_learning_question(item, kind, variant=variant)
    q['id'] = qid
    learning.append_questions(q, now=now)
    learning.submit(qid, q['answer'], True, now=now)
    return learning.finalize(qid, 3, now=now)


def start_sequence(learning):
    item = lesson()
    learning.enroll(item, now=START)
    saved = learning.get_item(CID)
    seq = learning.guided_sequence(saved, list(item['question_templates']))
    assert len(seq) == 2
    learning.advance_instruction(CID, 'intro_done', now=START)
    learning.advance_instruction(CID, 'guide', now=START, state={
        'guide_sequence': seq, 'guide_index': 0, 'guide_completed_ids': [],
        'guide_question': seq[0], 'guide_draft': {}})
    return seq


def test_history_is_bounded_indexed_detached_and_does_not_load_other_abilities(stores):
    store, learning = stores
    item = lesson()
    now = introduced(learning, item)
    for n in range(27):
        event = formal(learning, item, 'grammar_cloze', n % 3, 'q' + str(n), now)
        now = datetime.fromisoformat(event['due_at_utc']) + timedelta(seconds=61)
    before = all_rows(store)
    queries = []
    store._db.set_trace_callback(queries.append)
    rows = learning.practice_history(CID, 'application')
    store._db.set_trace_callback(None)
    assert len(rows) == 24
    assert [row['question_id'] for row in rows] == ['q' + str(n) for n in range(3, 27)]
    assert len(queries) == 1 and 'LIMIT 24' in queries[0] and 'unit_id=' in queries[0]
    assert learning.practice_history(CID, 'understanding') == []
    rows[-1]['question']['practice']['context_id'] = 'mutated'
    assert learning.practice_history(CID, 'application', 1)[0]['question']['practice']['context_id'] != 'mutated'
    assert all_rows(store) == before


@pytest.mark.parametrize('limit', [True, 0, -1, 25, None])
def test_history_rejects_unbounded_requests(stores, limit):
    with pytest.raises(ValueError):
        stores[1].practice_history(CID, 'application', limit)


def test_selection_and_restart_preserve_pending_snapshot_draft_due_and_events(stores):
    store, learning = stores
    item = lesson()
    now = introduced(learning, item)
    done = formal(learning, item, 'grammar_cloze', 0, 'graded', now)
    q = build_learning_question(item, 'grammar_transform', variant=1)
    q['id'] = 'old-pending'
    learning.append_questions(q, now=now)
    learning.save_draft(q['id'], {'answer': '窓', 'assisted': True})
    before = all_rows(store)
    plan = select_practice_plan(item, 'application', ['grammar_cloze'],
        learning.get_unit(CID, 'application'), learning.practice_history(CID, 'application'))
    assert plan['kind'] == 'grammar_cloze'
    reopened = LearningStore(store)
    assert reopened.current([], now=now)['type'] == 'disabled'
    assert reopened.get_question(q['id'])['draft'] == {'answer': '窓', 'assisted': True}
    assert learning.get_unit(CID, 'application')['due_at_utc'] == done['due_at_utc']
    assert all_rows(store) == before


def test_cued_typing_does_not_set_independent_production_but_retrieval_does(stores):
    store, learning = stores
    item = lesson()
    now = introduced(learning, item)
    done = formal(learning, item, 'grammar_transform', 0, 'cued', now)
    assert learning.get_unit(CID, 'application')['last_unassisted_success_at_utc'] is None
    now = datetime.fromisoformat(done['due_at_utc']) + timedelta(seconds=61)
    q = build_learning_question(item, 'grammar_transform', variant=1)
    q['id'] = 'retrieve'
    learning.append_questions(q, now=now)
    projected = next(learning._dispatch_pending())
    probe_unit = {**dict(projected), 'due_at_utc': (now + timedelta(days=10)).isoformat(),
                  'probe_due_at_utc': now.isoformat()}
    assert _ability_due(probe_unit, json.loads(projected['snapshot_json'])) == now.isoformat()
    learning.submit(q['id'], q['answer'], True, now=now)
    result = learning.finalize(q['id'], 3, now=now)
    assert learning.get_unit(CID, 'application')['last_unassisted_success_at_utc'] == now.isoformat()
    before = all_rows(store)
    assert learning.finalize(q['id'], 4, now=now + timedelta(days=1)) == result
    assert all_rows(store) == before
    validate_backup_rows(store._db)


@pytest.mark.parametrize('metadata', [None, [], {'reviewed': False}])
def test_dispatch_projection_cannot_turn_invalid_metadata_into_legacy_retrieval(stores, metadata):
    _, learning = stores
    item = lesson()
    now = introduced(learning, item)
    q = build_learning_question(item, 'grammar_transform', variant=1)
    q['practice'] = metadata
    learning.append_questions(q, now=now)
    projected = next(learning._dispatch_pending())
    unit = {**dict(projected), 'due_at_utc': (now + timedelta(days=10)).isoformat(),
            'probe_due_at_utc': now.isoformat()}
    assert _ability_due(unit, json.loads(projected['snapshot_json'])) == _ability_due(unit, q)
    assert _ability_due(unit, q) == unit['due_at_utc']


def test_two_guides_resume_atomically_without_any_formal_review(stores):
    store, learning = stores
    sequence = start_sequence(learning)
    first, second = sequence
    assert first['practice']['context_id'] != second['practice']['context_id']
    assert first['input_mode'] == 'choice' and second['input_mode'] == 'text'
    learning.advance_instruction(CID, 'guide', now=START, state={'guide_draft': {'answer': '依頼'}})
    reopened = LearningStore(store)
    assert reopened.guided_question(reopened.get_item(CID), 'grammar_cloze') == first
    answer_guide(reopened, first)
    stepped = reopened.advance_guided_sequence(CID, question_id=first['id'])
    assert not stepped['finished'] and stepped['item']['checkpoint']['guide_index'] == 1
    assert stepped['item']['checkpoint']['guide_draft'] == {}
    assert 'guide_answer' not in stepped['item']['checkpoint']
    assert reopened.advance_guided_sequence(CID, question_id=first['id']) == stepped
    reopened.advance_instruction(CID, 'guide', now=START, state={'guide_draft': {'answer': '依'}})
    again = LearningStore(store)
    assert again.guided_question(again.get_item(CID), 'grammar_cloze') == second
    assert again.get_item(CID)['checkpoint']['guide_draft']['answer'] == '依'
    answer_guide(again, second)
    assert again.advance_guided_sequence(CID, question_id=second['id'])['finished']
    again.advance_instruction(CID, 'guide_correct', now=START)
    assert again.get_unit(CID, 'application')['review_count'] == 0
    assert again.get_unit(CID, 'application')['due_at_utc'] is None
    assert again.history() == []
    validate_backup_rows(store._db)


@pytest.mark.parametrize('tamper', ['index', 'prompt', 'answer', 'draft', 'completed', 'current', 'sequence'])
def test_damaged_guide_backup_is_rejected_not_replayed(stores, tamper):
    store, learning = stores
    seq = start_sequence(learning)
    state = deepcopy(learning.get_item(CID)['checkpoint'])
    if tamper == 'index': state['guide_index'] = True
    elif tamper == 'prompt': state['guide_sequence'][0]['prompt'] = state['guide_question']['prompt'] = 'unreviewed'
    elif tamper == 'answer': state['guide_sequence'][0]['answer'] = state['guide_question']['answer'] = 'unreviewed'
    elif tamper == 'draft': state['guide_draft'] = {'answer': []}
    elif tamper == 'completed': state['guide_completed_ids'] = [seq[1]['id']]
    elif tamper == 'current': state['guide_question'] = seq[1]
    else: state['guide_sequence'] = [seq[0]]
    with store._db:
        store._db.execute('UPDATE learning_content SET checkpoint_json=? WHERE content_id=?',
                         (json.dumps(state), CID))
    with pytest.raises(ValueError):
        validate_backup_rows(store._db)


def test_paused_current_guide_preserves_group_and_allows_other_content(stores):
    store, learning = stores
    seq = start_sequence(learning)
    # Current guide uses cloze, but the same content also has a text pending.
    own = build_learning_question(lesson(), 'grammar_transform', variant=1)
    learning.append_questions(own, now=START)
    other = lesson()
    other['id'] = 'grammar:other-policy'
    learning.enroll(other, now=START)
    q = build_learning_question(other, 'grammar_transform', variant=0)
    learning.append_questions(q, now=START)
    before = all_rows(store)
    current = learning.current(['grammar_transform'], now=START)
    assert current['content']['id'] == other['id']
    assert learning.counts(['grammar_transform'], now=START)['disabled'] == 1
    reopened = LearningStore(store)
    assert reopened.guided_question(reopened.get_item(CID), 'grammar_transform') == seq[0]
    assert reopened.current(['grammar_cloze', 'grammar_transform'], now=START)['content']['id'] == CID
    assert all_rows(store) == before


def test_old_answered_choice_guide_is_not_replaced_by_new_group(stores):
    store, learning = stores
    item = lesson()
    for variants in item['question_templates'].values():
        for variant in variants: variant.pop('practice')
    learning.enroll(item, now=START)
    learning.advance_instruction(CID, 'intro_done', now=START)
    q = build_learning_question(item, 'grammar_cloze', guided=True)
    learning.advance_instruction(CID, 'guide', now=START, state={'guide_question': q, 'guide_answer': q['answer'], 'guide_correct': True})
    before = all_rows(store)
    assert learning.guided_sequence(learning.get_item(CID), ['grammar_cloze']) == []
    assert learning.guided_question(learning.get_item(CID), 'grammar_cloze') == q
    validate_backup_rows(store._db)
    assert all_rows(store) == before
