"""Synthetic contours test protocol mechanics, not real Japanese pitch claims."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from kanamemory import pitch_alignment as alignment, pitch_practice as pitch
from kanamemory.learning_questions import build_learning_question
from kanamemory.learning_store import LearningStore, validate_backup_rows
from support.learning_store_fixtures import START, stores, ready, legacy_dump


from support.pitch_alignment_support import synthetic_spec, registry, register, lesson, CORRECT


def question(registry):
    return build_learning_question(lesson(registry), 'pronunciation_application')


def perform(q, actions, draft=None):
    result = pitch.initial_draft(q) if draft is None else draft
    for action in actions:
        result = pitch.editor_action(q, result, action)
    return result


def test_reference_reading_is_given_without_mora_or_pitch_answer(registry):
    q = question(registry)
    assert q['prompt'] == '今日、雨。'
    assert q['task'].splitlines()[1] == '参考读法：きょう、あめ。'
    # Three kana in きょう still require the learner to identify two morae.
    assert not any(x in q['task'] for x in ('きょ・う', '2 拍', 'L H', 'きょう│'))
    draft = pitch.initial_draft(q)
    assert draft['answer'] == ''
    view = pitch.fixed_view(q, draft)
    assert all(u['levels'] == [] for u in view['surface']['units'])
    public = json.dumps(view, ensure_ascii=False)
    assert not any(x in public for x in ('きょう', 'あめ', 'reading', 'morae', 'expected'))
    altered = deepcopy(q); altered['task'] = q['task'].replace('きょう', 'きのう')
    with pytest.raises(ValueError):
        pitch.validate_snapshot(altered)


def test_whole_written_units_do_not_force_one_character_one_mora(registry):
    q = question(registry)
    initial = pitch.fixed_view(q)
    assert initial['surface']['text'] == '今日、雨。'
    assert [u['text'] for u in initial['surface']['units']] == ['今日', '、', '雨', '。']
    assert [u['levels'] for u in initial['surface']['units']] == [[], [], [], []]
    assert not any(key in json.dumps(initial, ensure_ascii=False) for key in ['reading', 'morae', 'きょう', 'あめ', 'expected'])
    partial = perform(q, ['L', 'H', 'H'])
    view = pitch.state_view(q, partial)
    assert view['cursor'] == 0 and view['can_input'] and view['can_split']
    assert view['display']['surface']['units'][0]['text'] == '今日'
    assert view['display']['surface']['units'][0]['levels'] == ['L', 'H', 'H']
    moved = pitch.editor_action(q, partial, 'split')
    assert pitch.state_view(q, moved)['cursor'] == 2
    assert pitch.editor_action(q, moved, 'undo') == partial
    assert pitch.grade_answer(q, partial['answer'])['scorable'] is False


def test_last_word_confirmation_is_required_and_undo_roundtrips(registry):
    q = question(registry)
    initial = pitch.initial_draft(q)
    before_last = perform(q, CORRECT[:-1])
    assert not pitch.state_view(q, before_last)['complete']
    assert pitch.grade_answer(q, before_last['answer'])['status'] == 'empty'
    done = pitch.editor_action(q, before_last, 'split')
    assert done['answer'] == q['answer']
    assert pitch.state_view(q, done)['complete']
    assert pitch.grade_answer(q, done['answer'])['correct']
    for _ in CORRECT:
        done = pitch.editor_action(q, done, 'undo')
    assert done == initial
    with pytest.raises(ValueError):
        pitch.editor_action(q, initial, 'merge')
    with pytest.raises(ValueError):
        pitch.editor_action(q, initial, 'split')


def test_count_errors_precede_pitch_and_do_not_shift_other_word_diagnoses(registry):
    q = question(registry)
    wrong = perform(q, ['H', 'split', 'L', 'H', 'H', 'split'])
    result = pitch.grade_answer(q, wrong['answer'])
    assert result['pitch_errors'] == ['morae'] and result['source_unit_id'] == 'today'
    wrong_pitch = perform(q, ['H', 'H', 'split', 'H', 'L', 'split'])
    result = pitch.grade_answer(q, wrong_pitch['answer'])
    assert result['pitch_errors'] == ['levels'] and result['beat_index'] == 0
    assert '今日' in result['message'] and 'きょ' not in result['message']


def test_unit_limit_does_not_depend_on_hidden_expected_count(registry):
    q = question(registry)
    filled = perform(q, ['H'] * 32)
    assert not pitch.state_view(q, filled)['can_input'] and pitch.state_view(q, filled)['can_split']
    with pytest.raises(ValueError):
        pitch.editor_action(q, filled, 'L')
    assert pitch.state_view(q, pitch.editor_action(q, filled, 'split'))['cursor'] == 2


def test_total_input_bound_is_independent_of_reference_and_cannot_be_bypassed(registry):
    spec = synthetic_spec(); spec['text'] = '甲乙丙丁戊'
    spec['source_units'] = [{'id': str(i), 'start': i, 'end': i+1, 'role': 'word', 'reading': 'あ', 'morae': ['あ']} for i in range(5)]
    spec['reviewed_patterns'][0]['levels'] = ['H'] * 5
    q = build_learning_question(lesson(registry, spec=spec), 'pronunciation_application')
    draft = perform(q, (['H'] * 32 + ['split']) * 4)
    view = pitch.state_view(q, draft)
    assert view['cursor'] == 4 and not view['can_input'] and not view['complete']
    with pytest.raises(ValueError):
        pitch.editor_action(q, draft, 'H')
    assert pitch.grade_answer(q, draft['answer'])['scorable'] is False
    assert pitch.editor_action(q, draft, 'clear') == pitch.initial_draft(q)


@pytest.mark.parametrize('mutate', [
    lambda s: s['source_units'][0].update(end=3),
    lambda s: s['source_units'][2].update(start=2),
    lambda s: s['source_units'][2].update(id='today'),
    lambda s: s['source_units'][1].update(morae=['あ'], reading='あ'),
    lambda s: s['source_units'][0].update(morae=['き', 'ょう']),
    lambda s: s['reviewed_patterns'][0].update(levels=['H']),
    lambda s: s.update(selected_pattern_id=[]),
])
def test_malformed_source_mapping_or_review_is_rejected(registry, mutate):
    spec = synthetic_spec(); mutate(spec); register(registry, spec)
    with pytest.raises(ValueError):
        pitch.specification({'input_mode': 'pitch_fill', 'pitch_practice': spec})


def test_hash_claim_without_exact_registered_version_does_not_authorize(registry):
    q = question(registry)
    forged = deepcopy(q)
    forged['pitch_practice']['source_units'][0]['reading'] = 'きのう'
    forged['pitch_practice']['source_units'][0]['morae'] = ['き', 'の', 'う']
    forged['pitch_practice']['reviewed_patterns'][0]['levels'].append('H')
    forged['pitch_practice']['review']['content_sha256'] = alignment.semantic_fingerprint(forged['pitch_practice'])
    with pytest.raises(ValueError):
        pitch.specification(forged)


def test_draft_identity_actions_answer_and_frozen_prompt_are_strict(registry):
    q = question(registry)
    draft = perform(q, ['L'])
    for mutate in (lambda d: d['pitch_state'].update(actions=['L', 'split']),
                   lambda d: d['pitch_state'].update(context_id='other'),
                   lambda d: d.update(answer=q['answer'])):
        bad = deepcopy(draft); mutate(bad)
        with pytest.raises(ValueError):
            pitch.validate_draft(q, bad)
    for key, value in [('prompt', 'きょう、あめ。'), ('task', '改写语境'), ('audio_text', '今日')]:
        bad = deepcopy(q); bad[key] = value
        with pytest.raises(ValueError):
            pitch.validate_snapshot(bad)
    assert pitch.grade_answer(q, 'pp3:{}')['status'] == 'invalid'
    encoded = json.loads(q['answer'][4:]); encoded['units'][0]['id'] = 'rain'
    assert pitch.grade_answer(q, 'pp3:' + json.dumps(encoded))['scorable'] is False


@pytest.mark.parametrize('kind', ['pronunciation_application', 'pronunciation_rule'])
def test_real_store_backup_restart_and_exactly_one_rule_review(registry, stores, monkeypatch, kind):
    store, learning = stores
    item = lesson(registry, kind=kind); q = build_learning_question(item, kind)
    learning.enroll(item, now=START); learning.append_questions(q, now=START)
    when = ready(learning, item['id'])
    legacy = legacy_dump(store)
    other = learning.get_unit(item['id'], 'understanding' if kind == 'pronunciation_application' else 'application')
    partial = perform(q, CORRECT[:4], {**pitch.initial_draft(q), 'hint_used': False})
    learning.save_draft(q['id'], partial)
    assert LearningStore(store).get_question(q['id'])['draft'] == partial
    with pytest.raises(ValueError):
        learning.submit(q['id'], partial['answer'], True, now=when)
    from kanamemory import learning_content
    monkeypatch.setattr(learning_content, 'get_learning_item', lambda cid: deepcopy(item))
    for _ in range(2):
        store.import_backup(store.export_backup())
        assert LearningStore(store).get_question(q['id'])['draft'] == partial
    assert not learning.history()
    done = perform(q, CORRECT[4:], partial); learning.save_draft(q['id'], done)
    result = learning.submit(q['id'], done['answer'], False, now=when)
    assert result['correct'] is True
    assert learning.submit(q['id'], 'bad', False, now=when) == result
    event = learning.finalize(q['id'], 3, now=when)
    assert learning.finalize(q['id'], 1, now=when + timedelta(days=1)) == event
    assert len(learning.history()) == 1
    assert learning.get_unit(item['id'], other['ability']) == other
    assert legacy_dump(store) == legacy
    validate_backup_rows(store._db)


def test_unregistered_import_rejected_before_existing_rows_change(registry, stores):
    store, learning = stores
    item = lesson(registry); q = build_learning_question(item, 'pronunciation_application')
    learning.enroll(item, now=START); learning.append_questions(q, now=START)
    learning.save_draft(q['id'], perform(q, ['L']))
    payload = store.export_backup()
    before = [tuple(row) for row in store._db.execute('SELECT * FROM learning_question')]
    registry['records'].clear()
    with pytest.raises(ValueError):
        store.import_backup(payload)
    assert [tuple(row) for row in store._db.execute('SELECT * FROM learning_question')] == before
