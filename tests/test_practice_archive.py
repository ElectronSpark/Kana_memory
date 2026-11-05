"""Runtime archival preserves observations and only retires local ownership."""
from datetime import timedelta
import json

import pytest

from kanamemory.domain import Question
from kanamemory import kana_routing
from kanamemory.practice_archive import (
    COURSE_PREFIX, NATIVE_PREFIX, archive_previous_rounds, archived_question, archived_attempts,
    archived_session, assert_native_answerable, validate_database,
)
from support.learning_store_fixtures import START, item, question, ready, stores


def rows(store, table):
    return [dict(row) for row in store._db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]


def test_explicit_learning_reset_clears_archives_and_all_runtime_lanes(stores):
    from kanamemory import practice_round as rounds
    store, learning = stores
    native(store, state={'page': 'feedback', 'feedback_pending': True,
                        'answered_text': 'a', 'answer_correct': True})
    when = course(learning)
    learning.submit('q1', '正在读书', True, now=when)
    archive_previous_rounds(store, now=when)
    assert len(archived_attempts(store)) == 2
    rounds.enable(store)
    main = rounds.begin(store)
    rounds.seal(store)
    store.practice_lane = 'word_romaji'
    word = rounds.begin(store)
    rounds.seal(store)
    store.set_setting('pitch_display_v1', {'number': True})
    report = store.clear_learning_data()
    assert any(k.startswith(NATIVE_PREFIX) for k in report['deleted_settings'])
    assert any(k.startswith(COURSE_PREFIX) for k in report['deleted_settings'])
    assert archived_attempts(store) == [] and validate_database(store._db) == set()
    assert not rounds.active(store) and not rounds.sealed(store)
    assert store.get_setting('pitch_display_v1') == {'number': True}
    assert rounds.begin(store) is not word
    store.practice_lane = 'main'
    assert rounds.begin(store) is not main
    assert archive_previous_rounds(store, now=when)['native'] == 0


def native(store, *, word=False, state=None):
    q = (Question('word:test', 'word_meaning_kana', 'hiragana', '水', 'みず',
                  metadata={'word_romaji_drill': True}) if word else
         Question('kana-3042', 'kana_romaji', 'hiragana', 'あ', 'a'))
    sid = store.start_session([q], now=START)
    store.save_session_progress(sid, 0, state=state or {})
    return sid


def course(learning, *, cid='grammar:te_iru:progressive', qid='q1', **kw):
    learning.enroll([item(cid)], now=START)
    when = ready(learning, cid)
    learning.append_questions([question(cid, qid, **kw)], now=when)
    return when


def settings(store, prefix):
    return {key: json.loads(value) for key, value in store._db.execute(
        'SELECT key,value_json FROM app_setting WHERE key GLOB ?', (prefix + '*',))}


def test_atomic_idempotent_archive_retains_unrated_native_and_course(stores):
    store, learning = stores
    state = {'page': 'feedback', 'feedback_pending': True, 'answered_text': 'a',
             'answer_correct': True, 'submitted_at_utc': START.isoformat(),
             'answer_duration_ms': 123}
    sid = native(store, state=state)
    word_sid = native(store, word=True, state={'word_answer': 'み'})
    when = course(learning)
    learning.save_draft('q1', {'answer': '正在读书', 'hint_used': True})
    learning.submit('q1', '正在读书', True, now=when, duration_ms=456)
    checkpoint = rows(store, 'session_checkpoint')
    courses = rows(store, 'learning_question')
    unchanged = {name: rows(store, name) for name in (
        'study_session', 'session_question', 'review_event', 'session_answer',
        'practice_attempt', 'word_spelling_attempt', 'study_card', 'learning_event',
        'learning_unit', 'learning_content')}

    assert archive_previous_rounds(store, now=when) == {
        'native': 2, 'course': 1, 'skipped_course': 0, 'skipped_owners': {}}
    assert rows(store, 'session_checkpoint') == []
    for name, before in unchanged.items():
        assert rows(store, name) == before, name
    assert rows(store, 'learning_question') == [{**courses[0], 'status': 'completed'}]
    receipts = settings(store, NATIVE_PREFIX)
    for before in checkpoint:
        receipt = receipts[NATIVE_PREFIX + before['session_id']]
        assert receipt['before'] == before
    assert receipts[NATIVE_PREFIX + sid]['evidence']['state'] == state
    assert receipts[NATIVE_PREFIX + sid]['evidence']['submitted_unrated'] is True
    assert receipts[NATIVE_PREFIX + word_sid]['evidence']['state']['word_answer'] == 'み'
    archive = archived_question(store, 'q1')
    assert archive['before'] == courses[0]
    assert archive['evidence']['result'] == json.loads(courses[0]['result_json'])
    assert archive['evidence']['rating'] is None
    assert validate_database(store._db) == {'q1'}
    assert not store._db.execute('PRAGMA foreign_key_check').fetchall()
    saved = settings(store, 'practice_archive_v1:')
    assert archive_previous_rounds(store, now=when + timedelta(days=1)) == {
        'native': 0, 'course': 0, 'skipped_course': 0, 'skipped_owners': {}}
    assert settings(store, 'practice_archive_v1:') == saved


def test_rated_native_and_course_history_and_srs_do_not_change(stores):
    store, learning = stores
    sid = native(store)
    store.record_review('kana-3042', 'kana_romaji', 'hiragana', 3,
                        session_id=sid, question_index=0, answered_text='a',
                        outcome='correct', now=START)
    when = course(learning)
    learning.submit('q1', '正在读书', True, now=when)
    learning.finalize('q1', 3, now=when)
    before = {name: rows(store, name) for name in (
        'review_event', 'session_answer', 'study_card', 'word_spelling_attempt',
        'learning_question', 'learning_unit', 'learning_event')}
    archive_previous_rounds(store, now=when)
    for name, value in before.items():
        assert rows(store, name) == value, name
    assert archived_question(store, 'q1') is None
    assert validate_database(store._db) == set()


def test_planned_kana_identity_and_frozen_questions_reusable_next_round(stores):
    store, _ = stores
    sid = native(store)
    original = rows(store, 'session_question')
    token = json.loads(original[0]['snapshot_json'])['metadata'][kana_routing.ROUTE_KEY]
    assert kana_routing.unit_for(store, 'kana-3042', 'kana_romaji', 'hiragana')['status'] == 'planned'
    archive_previous_rounds(store, now=START)
    other = native(store)
    assert other != sid
    next_q = json.loads(store._db.execute(
        'SELECT snapshot_json FROM session_question WHERE session_id=?', (other,)).fetchone()[0])
    assert next_q['metadata'][kana_routing.ROUTE_KEY] == token
    assert rows(store, 'session_question')[0] == original[0]
    kana_routing.validate_database(store._db)
    validate_database(store._db)
    assert not store._db.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('owner', ['reservation', 'handoff', 'source_guard', 'ai'])
def test_owned_course_is_left_to_its_protocol(stores, owner):
    store, learning = stores
    when = course(learning)
    if owner == 'ai':
        # Enrollment/append safety gates still reject new AI material. This
        # isolated row tests archival's conservative flag boundary only.
        snapshot = json.loads(rows(store, 'learning_question')[0]['snapshot_json'])
        snapshot['ai_generated'] = True
        with store._db:
            store._db.execute('UPDATE learning_question SET snapshot_json=? WHERE question_id=?',
                              (json.dumps(snapshot), 'q1'))
    row = rows(store, 'learning_question')[0]
    if owner != 'ai':
        from kanamemory.retention_session import RESERVATION_PREFIX, handoff_key
        from kanamemory.retention_sources import guard_key
        key = {'reservation': RESERVATION_PREFIX + row['unit_id'],
               'handoff': handoff_key('q1'), 'source_guard': guard_key('q1')}[owner]
        # Archival recognizes ownership, without interpreting/rewriting its protocol.
        store.set_setting(key, {'owner_test': True})
    before = rows(store, 'learning_question')
    assert archive_previous_rounds(store, now=when) == {
        'native': 0, 'course': 0, 'skipped_course': 1,
        'skipped_owners': {'ai' if owner == 'ai' else 'retention': 1}}
    assert rows(store, 'learning_question') == before
    assert archived_question(store, 'q1') is None
    assert settings(store, COURSE_PREFIX) == {}


@pytest.mark.parametrize('column,value', [
    ('draft_json', '{"answer":"changed"}'),
    ('result_json', '{"outcome":"correct"}'),
    ('snapshot_json', '{"id":"q1"}'),
    ('status', 'pending'),
])
def test_receipt_never_exempts_another_or_mutated_course_row(stores, column, value):
    store, learning = stores
    when = course(learning)
    archive_previous_rounds(store, now=when)
    with store._db:
        store._db.execute('UPDATE learning_question SET ' + column + '=? WHERE question_id=?', (value, 'q1'))
    with pytest.raises(ValueError):
        validate_database(store._db)
    with pytest.raises(ValueError):
        archived_question(store, 'q1')


def test_whole_transaction_rolls_back_on_failure(stores):
    store, learning = stores
    native(store)
    when = course(learning)
    before = {name: rows(store, name) for name in ('session_checkpoint', 'learning_question', 'app_setting')}
    store._db.execute("CREATE TEMP TRIGGER refuse_archive BEFORE UPDATE OF status ON learning_question "
                      "BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END")
    with pytest.raises(Exception, match='synthetic write failure'):
        archive_previous_rounds(store, now=when)
    for name, value in before.items():
        assert rows(store, name) == value
    assert not store._db.in_transaction


def test_requires_own_transaction_and_valid_aware_time(stores):
    store, _ = stores
    store._db.execute('BEGIN')
    try:
        with pytest.raises(RuntimeError, match='owns its transaction'):
            archive_previous_rounds(store, now=START)
    finally:
        store._db.rollback()
    with pytest.raises(ValueError):
        archive_previous_rounds(store, now=START.replace(tzinfo=None))


def test_archived_attempts_only_submitted_observations_and_frozen_details(stores):
    store, learning = stores
    sid = native(store, state={'page': 'feedback', 'feedback_pending': True,
                              'answered_text': 'a', 'submitted_at_utc': START.isoformat()})
    native(store, word=True, state={'page': 'practice', 'word_answer': 'み'})
    when = course(learning)
    learning.submit('q1', '正在读书', True, now=when)
    pending = rows(store, 'learning_question')[0]
    archive_previous_rounds(store, now=when)
    attempts = archived_attempts(store)
    assert len(attempts) == 2
    by_lane = {entry['lane']: entry for entry in attempts}
    assert set(by_lane) == {'main', 'course'}
    entry = by_lane['main']
    assert entry['session_id'] == sid and entry['question_index'] == 0
    assert entry['answered_text'] == 'a' and entry['correct'] is None
    assert entry['outcome'] == 'unrated' and entry['at'] == START.isoformat()
    assert entry['question'] == json.loads(store._db.execute(
        'SELECT snapshot_json FROM session_question WHERE session_id=?', (sid,)).fetchone()[0])
    assert by_lane['course']['question'] == json.loads(pending['snapshot_json'])
    assert by_lane['course']['result'] == json.loads(pending['result_json'])
    assert all(entry['rating'] is None and entry['scheduled'] is False for entry in attempts)
    assert len(archived_attempts(store, limit=1)) == 1
    assert archived_attempts(store, limit=0) == []
    for invalid in (-1, 101, True, '20'):
        with pytest.raises(ValueError):
            archived_attempts(store, limit=invalid)
    entry['question']['prompt'] = 'caller mutation'
    assert all(row['question']['prompt'] != 'caller mutation' for row in archived_attempts(store))


def test_archived_attempts_excludes_already_recorded_native_head(stores):
    store, _ = stores
    sid = native(store, state={'page': 'feedback', 'feedback_pending': True, 'answered_text': 'a'})
    store.record_review('kana-3042', 'kana_romaji', 'hiragana', 3,
                        session_id=sid, question_index=0, answered_text='a',
                        outcome='correct', now=START)
    archive_previous_rounds(store, now=START)
    assert archived_attempts(store) == []


def test_receipt_cannot_exempt_completed_scored_course(stores):
    store, learning = stores
    when = course(learning)
    learning.submit('q1', '正在读书', True, now=when)
    before = rows(store, 'learning_question')[0]
    learning.finalize('q1', 3, now=when)
    # Even a recomputed hash matching a completed row cannot own a real event.
    from kanamemory.practice_archive import _course_key, _sha, _evidence
    after = rows(store, 'learning_question')[0]
    before = {**after, 'status': 'pending'}
    forged = {'version': 1, 'kind': 'course', 'archived_at_utc': when.isoformat(),
              'before': before, 'before_sha256': _sha(before),
              'after_sha256': _sha(after), 'evidence': _evidence(before)}
    store.set_setting(_course_key('q1'), forged)
    with pytest.raises(ValueError, match='exact unscored row'):
        validate_database(store._db)


def test_native_guard_accepts_live_session_and_rejects_validated_archived_round(stores):
    store, _ = stores
    sid = native(store)
    assert archived_session(store, sid) is None
    assert assert_native_answerable(store, sid) is None
    assert archived_session(store, 'unknown-session') is None
    checkpoint = rows(store, 'session_checkpoint')[0]
    archive_previous_rounds(store, now=START)
    receipt = archived_session(store, sid)
    assert receipt['before'] == checkpoint
    receipt['before']['state_json'] = '{} caller mutation'
    assert archived_session(store, sid)['before'] == checkpoint
    with pytest.raises(ValueError, match='这轮练习已结束'):
        assert_native_answerable(store, sid)


def test_native_guard_rejects_tampered_receipt_instead_of_treating_it_as_live(stores):
    store, _ = stores
    sid = native(store)
    archive_previous_rounds(store, now=START)
    receipt = archived_session(store, sid)
    receipt['head']['snapshot_json'] = '{}'
    store.set_setting(NATIVE_PREFIX + sid, receipt)
    with pytest.raises(ValueError, match='frozen source'):
        archived_session(store, sid)
    with pytest.raises(ValueError, match='frozen source'):
        assert_native_answerable(store, sid)


@pytest.mark.parametrize('correct', [True, False])
def test_native_saved_judgement_is_preserved_as_observation_without_grading(stores, correct):
    store, _ = stores
    sid = native(store, state={'page': 'feedback', 'feedback_pending': True,
                              'answered_text': 'x', 'answer_correct': correct})
    archive_previous_rounds(store, now=START)
    assert archived_session(store, sid)['evidence']['state']['answer_correct'] is correct
    [attempt] = archived_attempts(store)
    assert attempt['correct'] is correct
    assert attempt['outcome'] == ('correct' if correct else 'incorrect')
    assert attempt['rating'] is None and attempt['scheduled'] is False
    assert rows(store, 'review_event') == []
