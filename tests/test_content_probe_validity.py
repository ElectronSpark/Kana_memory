"""Deferred R3 contracts for reason invalidation and independent queued work."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from support.kana_context_words_support import setup
from support.kana_context_words_support import learn
from support.word_context_memory_support import NOW
from support.word_context_memory_support import KA
from support.word_context_memory_support import KI
from support.kana_context_words_support import KINDS
from support.kana_rule_evidence_support import record
from kanamemory.domain import Question, WORD_KIND_PAUSE, StudyStore
from kanamemory.kana_rule_evidence import next_rule_probe, SOKUON_RULE, active_probe, SETTING
from kanamemory.kana_context_words import context_active, context_priority
from kanamemory.learning_evidence import LearningEvidenceStore
from kanamemory.practice_availability import summarize_queue_availability


def marked_word(store, periodic):
    learn(store)
    trigger = record(store, 'old-word-trigger', rule=SOKUON_RULE, context='word:がっこう',
        outcome='incorrect', source='word', now=NOW-timedelta(minutes=2))
    candidate = {'rule_id': SOKUON_RULE, 'context_id': 'word:かっき', 'content_id': 'context:kakki',
        'kind': 'word_meaning_kana', 'script': 'hiragana', 'source': 'word', 'required_kana_ids': [KA, KI]}
    with store._db:
        store._db.execute('BEGIN IMMEDIATE')
        probe = next_rule_probe(store, [candidate], enabled_kinds=KINDS, now=NOW, reserve=True)
    assert probe is not None
    marker = {'version': 1, 'review_count': 6}
    if periodic is not None: marker['periodic'] = periodic
    q = Question('context:kakki', 'word_meaning_kana', 'hiragana', 'kakki', 'かっき', ['か', 'っ', 'き'],
        metadata={'kana_context_v1': marker, 'kana_rule_probe_v1':
            {k: probe[k] for k in ('rule_id', 'probe_id', 'context_id', 'trigger_event_id')}})
    return q, trigger


@pytest.mark.parametrize('periodic,remains', [(None, True), (True, True), (False, False)])
def test_only_proven_error_only_context_is_parked(setup, periodic, remains):
    _, store, _ = setup
    q, trigger = marked_word(store, periodic)
    reservation = deepcopy(store.get_setting(SETTING))
    assert context_active(q, store)
    LearningEvidenceStore(store).retract(trigger['event_id'], 'corrected-source', now=NOW)
    assert active_probe(store, q.metadata['kana_rule_probe_v1']) is None
    assert context_active(q, store) is remains
    assert (context_priority(q, store) is not None) is (periodic is True)
    assert store.get_setting(SETTING) == reservation  # No refund or fake completion.


def test_invalid_trigger_parks_exact_draft_across_toggle_restart_and_rejects_stale_submit(setup):
    repo, store, tmp = setup
    word, trigger = marked_word(store, False)
    independent = Question(KA, 'kana_choice', 'hiragana', 'か', 'ka', ['ka', 'ki'])
    sid = store.start_session([word, independent], now=NOW)
    draft = {'page': 'word_question', 'word_answer': 'かっ'}
    store.save_session_progress(sid, 0, state=draft)
    before = [tuple(r) for r in store._db.execute('SELECT * FROM review_event')]
    LearningEvidenceStore(store).retract(trigger['event_id'], 'corrected-source', now=NOW)
    available = summarize_queue_availability(store, now=NOW)
    assert available.ready_count == 1
    current = store.prepare_next_question(sid, now=NOW)
    assert current['questions'][current['question_index']].content_id == KA
    parked = next(q for q in current['questions'] if q.content_id == word.content_id)
    assert parked.metadata[WORD_KIND_PAUSE]['state'] == draft
    assert current['state'].get('word_answer') is None
    store.reconcile_session_kinds(sid, ['kana_choice'], now=NOW)
    store.reconcile_session_kinds(sid, KINDS, now=NOW)
    with StudyStore(tmp / 'learner', 'UTC') as reopened:
        resumed = reopened.prepare_next_question(sid, now=NOW)
        pos = next(i for i, q in enumerate(resumed['questions']) if q.content_id == word.content_id)
        assert resumed['questions'][pos].metadata[WORD_KIND_PAUSE]['state'] == draft
        with pytest.raises(ValueError, match='依据已修订'):
            reopened.record_review(word.content_id, word.kind, word.script, None, session_id=sid,
                question_index=pos, answered_text=word.answer, outcome='correct', now=NOW, kana_rows=repo.kana_list())
    assert [tuple(r) for r in store._db.execute('SELECT * FROM review_event')] == before


def test_invalid_probe_does_not_cancel_independent_kana_or_correction(setup):
    _, store, _ = setup
    word, trigger = marked_word(store, False)
    LearningEvidenceStore(store).retract(trigger['event_id'], 'corrected-source', now=NOW)
    kana = Question(KA, 'kana_choice', 'hiragana', 'か', 'ka', ['ka', 'ki'],
        metadata={'kana_rule_probe_v1': word.metadata['kana_rule_probe_v1']})
    assert context_active(kana, store) and context_priority(kana, store) is None
    word.metadata['spelling_correction_of'] = 'previous-answer'
    assert context_active(word, store)


def test_completed_independent_probe_survives_retraction_without_refunding_reservation(setup):
    _, store, _ = setup
    word, trigger = marked_word(store, False)
    marker = word.metadata['kana_rule_probe_v1']
    completed = record(store, 'completed-independent-probe', rule=SOKUON_RULE,
        context=marker['context_id'], source='word', outcome='correct', probe_id=marker['probe_id'],
        now=NOW+timedelta(minutes=3))
    state = deepcopy(store.get_setting(SETTING))
    assert state['rules'][SOKUON_RULE]['probes'][0]['status'] == 'completed'
    evidence = dict(store._db.execute('SELECT * FROM learning_evidence WHERE event_id=?', (completed['event_id'],)).fetchone())
    LearningEvidenceStore(store).retract(trigger['event_id'], 'corrected-source', now=NOW+timedelta(minutes=4))
    assert store.get_setting(SETTING) == state
    assert dict(store._db.execute('SELECT * FROM learning_evidence WHERE event_id=?', (completed['event_id'],)).fetchone()) == evidence
    repeated = record(store, 'completed-independent-probe', rule=SOKUON_RULE,
        context=marker['context_id'], source='word', outcome='correct', probe_id=marker['probe_id'],
        now=NOW+timedelta(days=1))
    assert repeated['already_recorded']
    assert store.get_setting(SETTING) == state


def test_new_real_error_archives_invalid_pair_without_refunding_original_opportunity(setup):
    from kanamemory.kana_rule_evidence import HISTORY_PREFIX, validate_probe_history
    _, store, _ = setup
    word, trigger = marked_word(store, False)
    old = deepcopy(store.get_setting(SETTING)['rules'][SOKUON_RULE])
    LearningEvidenceStore(store).retract(trigger['event_id'], 'corrected-source', now=NOW)
    stamp = NOW + timedelta(minutes=2)
    fresh = record(store, 'new-independent-word-error', rule=SOKUON_RULE, context='word:きって',
        outcome='incorrect', source='word', now=stamp)
    state = store.get_setting(SETTING)['rules'][SOKUON_RULE]
    assert state['trigger_event_id'] == fresh['event_id'] and state['probes'] == []
    records = [json.loads(raw) for raw, in store._db.execute(
        'SELECT value_json FROM app_setting WHERE key GLOB ?', (HISTORY_PREFIX + '*',))]
    assert len(records) == 1 and records[0]['entry'] == old
    assert active_probe(store, word.metadata['kana_rule_probe_v1']) is None
    repeat = record(store, 'new-independent-word-error', rule=SOKUON_RULE, context='word:きって',
        outcome='incorrect', source='word', now=stamp)
    assert repeat['already_recorded'] and store.get_setting(SETTING)['rules'][SOKUON_RULE] == state
    validate_probe_history(store._db)


def test_retired_tasks_do_not_refund_pair_or_block_later_real_error(setup):
    from kanamemory.kana_rule_evidence import retire_probe_question, validate_probe_history
    from kanamemory.content_queue import PREFIX
    from kanamemory.material_workflow import dump
    _, store, _ = setup
    word, trigger = marked_word(store, False)
    candidate = {'rule_id': SOKUON_RULE, 'context_id': 'word:きっか', 'content_id': 'context:kikka',
        'kind': 'word_meaning_kana', 'script': 'hiragana', 'source': 'word', 'required_kana_ids': [KA, KI]}
    with store._db:
        store._db.execute('BEGIN IMMEDIATE')
        second = next_rule_probe(store, [candidate], enabled_kinds=KINDS, now=NOW, reserve=True)
    old = deepcopy(store.get_setting(SETTING)['rules'][SOKUON_RULE])
    markers = [word.metadata['kana_rule_probe_v1'],
        {k: second[k] for k in ('rule_id', 'probe_id', 'context_id', 'trigger_event_id')}]
    # Unit-level receipt ownership; full content adoption is covered in
    # test_session_content through the actual review and adoption services.
    for index, marker in enumerate(markers):
        receipt = {'id': 'retired-question-' + str(index), 'entry': {'question':
            {'metadata': {'kana_rule_probe_v1': marker}}}}
        with store._db:
            store._db.execute('BEGIN IMMEDIATE')
            store._db.execute('INSERT INTO app_setting VALUES (?,?)', (PREFIX + receipt['id'], dump(receipt)))
            retire_probe_question(store, receipt)
        assert active_probe(store, marker) is None
    assert store.get_setting(SETTING)['rules'][SOKUON_RULE] == old
    assert next_rule_probe(store, [candidate], enabled_kinds=KINDS, now=NOW+timedelta(days=2)) is None
    fresh = record(store, 'new-error-after-retired-tasks', rule=SOKUON_RULE, context='word:きって',
        outcome='incorrect', source='word', now=NOW+timedelta(days=2))
    assert store.get_setting(SETTING)['rules'][SOKUON_RULE]['trigger_event_id'] == fresh['event_id']
    validate_probe_history(store._db)
