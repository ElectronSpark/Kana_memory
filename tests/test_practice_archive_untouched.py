"""Ending a round: an untouched course question leaves no trace, a met one is archived exactly as before.

Made-up learners only. "Before" is the pinned copy of the previous pass
(support/practice_archive_original.py) run on a byte copy of the same record.
"""
from datetime import timedelta
import hashlib
import json
import sqlite3
import time

import pytest

from kanamemory import practice_archive
from kanamemory.domain import Question, StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.practice_archive import (
    COURSE_PREFIX, NATIVE_PREFIX, PREFIX, archive_previous_rounds, archived_attempts, archived_question,
    untouched_course_questions, validate_database,
)
from kanamemory.progress_backup import export_backup, import_backup, inspect_backup
from support import practice_archive_original as original
from support.learning_store_fixtures import START, item, question, ready, stores  # noqa: F401
from support.learning_fixtures import learning_app, click  # noqa: F401
from support.app_fixtures import question_app, setup_app  # noqa: F401 (fixtures used by learning_app)

KINDS = ['grammar_meaning']


def cid(n):
    return f'grammar:untouched:l{n:03d}'


def lessons(learning, count, *, when=START):
    """``count`` lessons, each with one queued, untouched question 'u<n>' (understanding)."""
    learning.enroll([item(cid(n)) for n in range(count)], now=when)
    for n in range(count):
        ready(learning, cid(n), when=when)
    at = when + timedelta(seconds=61)
    queue(learning, count, at)
    return at


def queue(learning, count, at):
    """The next collection: one proposal per lesson, under the lesson's own proposed id."""
    return learning.append_questions([question(cid(n), f'u{n}') for n in range(count)], now=at)


def rows(store, table):
    return [dict(row) for row in store._db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]


def tables(store):
    names = [row[0] for row in store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {name: rows(store, name) for name in names}


def counts(store):
    return {name: len(value) for name, value in tables(store).items()}


def receipts(store, prefix=PREFIX):
    return {key: value for key, value in store._db.execute(
        'SELECT key,value_json FROM app_setting WHERE key GLOB ?', (prefix + '*',))}


def twin(store, directory):
    """A second store on a byte copy of this record (same ids, uuids and times)."""
    directory.mkdir()
    target = sqlite3.connect(directory / 'progress.sqlite3')
    store._db.backup(target)
    target.close()
    copy = StudyStore(directory, time_zone='UTC')
    LearningStore(copy)
    return copy


def native(store, *, state=None, at=START):
    sid = store.start_session([Question('kana-3042', 'kana_romaji', 'hiragana', 'あ', 'a')], now=at)
    store.save_session_progress(sid, 0, state=state or {})
    return sid


# R1 ---------------------------------------------------------------------------------------------------------

def test_a_start_with_untouched_questions_adds_no_receipt_and_no_completed_row(stores):
    store, learning = stores
    when = lessons(learning, 12)
    before = counts(store)
    settings = rows(store, 'app_setting')
    assert untouched_course_questions(store._db, rows(store, 'learning_question')) == {f'u{n}' for n in range(12)}
    assert archive_previous_rounds(store, now=when) == {
        'native': 0, 'course': 0, 'skipped_course': 0, 'skipped_owners': {}}
    assert receipts(store) == {} and rows(store, 'app_setting') == settings
    assert rows(store, 'learning_question') == []
    assert counts(store) == {**before, 'learning_question': 0}
    validate_database(store._db)


@pytest.mark.parametrize('starts', [1, 10, 100])
def test_row_counts_do_not_grow_over_starts_beyond_what_met_questions_write(stores, starts):
    store, learning = stores
    when = lessons(learning, 6)
    learning.save_draft('u0', {'presented': True})   # one met question, archived once
    base = counts(store)
    for start in range(1, starts + 1):
        at = when + timedelta(hours=start)
        archive_previous_rounds(store, now=at)
        queue(learning, 6, at)
        native(store, at=at)
        now = counts(store)
        # Per start: the kana lane's one receipt (and its session), nothing for an untouched question.
        # The met question's receipt and completed row exist once; its unit gets a round copy, which
        # stays untouched and so leaves the queue at the next start like the others.
        assert len(receipts(store, COURSE_PREFIX)) == 1
        assert len(receipts(store, NATIVE_PREFIX)) == start - 1
        # app_setting: the met receipt, start - 1 kana receipts and the kana lane's one bridge setting.
        assert {key: now[key] - base[key] for key in now if now[key] != base[key]} == {
            key: value for key, value in {'app_setting': start + 1, 'learning_question': 1,
                                          'study_session': start, 'session_question': start,
                                          'session_checkpoint': 1}.items() if value}
    assert [row['status'] for row in rows(store, 'learning_question')].count('completed') == 1


def test_next_collection_serves_the_same_units_in_the_same_order_as_before(stores, tmp_path):
    store, learning = stores
    when = lessons(learning, 5)
    learning.save_draft('u2', {'answer': '正在'})  # one met question among untouched ones
    old = twin(store, tmp_path / 'before')
    try:
        original.archive_previous_rounds(old, now=when)
        archive_previous_rounds(store, now=when)
        later = when + timedelta(minutes=1)
        before_ids = queue(LearningStore(old), 5, later)
        after_ids = queue(learning, 5, later)
        assert len(before_ids) == len(after_ids) == 5
        # Ids differ only where the old pass retired an untouched id (':round:' copies); u2 is retired by both.
        assert [qid.split(':round:')[0] for qid in before_ids] == [qid.split(':round:')[0] for qid in after_ids]
        assert after_ids[2].startswith('u2:round:') and ':round:' not in ''.join(after_ids[:2] + after_ids[3:])

        def served(target):
            pending = [row for row in rows(target, 'learning_question') if row['status'] == 'pending']
            # The question's own id also appears inside its frozen assessment contract, whose digest
            # covers it: compare with the id masked and without that one digest.
            def same_but_id(row):
                snapshot = json.loads(row['snapshot_json'].replace(json.dumps(row['question_id'])[1:-1], '<id>'))
                snapshot['assessment_contract'].pop('sha256')
                return snapshot
            shape = [(row['unit_id'], row['kind'], row['created_at_utc'], row['not_before_utc'], row['exposure_group'],
                      same_but_id(row)) for row in pending]
            head = LearningStore(target).current(KINDS, now=later + timedelta(days=30))
            return shape, (head['question']['target_id'], head['question']['ability'])
        assert served(store) == served(old)
    finally:
        old.close()


# R2 ---------------------------------------------------------------------------------------------------------

def _evidence_reference(store, learning, qid):
    learning.record_practice_exposure('deck:' + qid, {**question(cid(0), qid), 'id': qid}, {
        'outcome': 'correct', 'correct': True, 'answered_text': '正在读书'}, now=START + timedelta(minutes=5))


TOUCHES = {
    'presented': lambda store, learning, qid: learning.save_draft(qid, {'presented': True}),
    'draft': lambda store, learning, qid: learning.save_draft(qid, {'answer': '正在'}),
    'help_flag': lambda store, learning, qid: learning.save_draft(qid, {'hint_used': True}),
    'attempt': lambda store, learning, qid: learning.submit(qid, '正在读书', True, now=START + timedelta(minutes=5)),
    'evidence_row': _evidence_reference,
    'setting_value': lambda store, learning, qid: store.set_setting('synthetic_reference_v1:x', {'question_id': qid}),
    'setting_key': lambda store, learning, qid: store.set_setting('synthetic_reference_v1:' + qid, {}),
    'hashed_key': lambda store, learning, qid: store.set_setting(
        'synthetic_reference_v1:' + hashlib.sha256(qid.encode()).hexdigest(), {}),
    'lesson_checkpoint': lambda store, learning, qid: store._db.execute(
        "UPDATE learning_content SET checkpoint_json=json_set(checkpoint_json,'$.synthetic_ref',?) "
        "WHERE content_id=?", (qid, cid(0))) and store._db.commit(),
}


@pytest.mark.parametrize('touch', sorted(TOUCHES))
def test_each_kind_of_touched_is_archived_exactly_as_the_previous_pass(stores, tmp_path, touch):
    store, learning = stores
    when = lessons(learning, 3)
    TOUCHES[touch](store, learning, 'u0')
    assert untouched_course_questions(store._db, [r for r in rows(store, 'learning_question')
                                                  if r['status'] == 'pending']) == {'u1', 'u2'}
    old = twin(store, tmp_path / 'before')
    try:
        assert original.archive_previous_rounds(old, now=when)['course'] == 3
        assert archive_previous_rounds(store, now=when)['course'] == 1
        gone = {practice_archive._course_key(q) for q in ('u1', 'u2')}
        expected = tables(old)
        expected['app_setting'] = [row for row in expected['app_setting'] if row['key'] not in gone]
        expected['learning_question'] = [row for row in expected['learning_question']
                                         if row['question_id'] not in ('u1', 'u2')]
        assert tables(store) == expected   # byte-equal receipt and completed row for u0, nothing else differs
        assert receipts(store, COURSE_PREFIX)[practice_archive._course_key('u0')] == \
            receipts(old, COURSE_PREFIX)[practice_archive._course_key('u0')]
    finally:
        old.close()


def test_only_met_questions_reach_the_archive_and_owned_ones_stay_pending(stores, tmp_path):
    store, learning = stores
    when = lessons(learning, 4)
    from kanamemory.retention_session import RESERVATION_PREFIX
    unit = rows(store, 'learning_question')[3]['unit_id']
    store.set_setting(RESERVATION_PREFIX + unit, {'owner_test': True})
    learning.save_draft('u1', {'presented': True})
    assert archive_previous_rounds(store, now=when) == {
        'native': 0, 'course': 1, 'skipped_course': 1, 'skipped_owners': {'retention': 1}}
    assert {row['question_id']: row['status'] for row in rows(store, 'learning_question')} == {
        'u1': 'completed', 'u3': 'pending'}
    assert set(receipts(store, COURSE_PREFIX)) == {practice_archive._course_key('u1')}


def _choice(prompt, answer, other):
    return {'prompt': prompt, 'answer': answer, 'choices': [answer, other], 'feedback': '说明',
            'input_mode': 'choice', 'safe': True}


def test_a_replacement_end_is_kept_so_its_receipt_stays_valid(stores):
    from kanamemory.learning_questions import build_learning_question
    from kanamemory.learning_replacement import LearningReplacementStore, validate_database as validate_replacements
    store, learning = stores
    word = 'word:untouched:replaced'
    learning.enroll([{'id': word, 'content_id': word, 'family': 'word', 'title': '語一', 'reading': 'ごいち',
                      'meaning': '词一', 'revision': '1', 'abilities': ['understanding'], 'source_ids': ['test'],
                      'instruction': {'summary': '词', 'rule': '名词。', 'steps': [], 'example': '語一です。',
                                      'translation': '是词一。', 'prerequisite': '', 'contrast': ''},
                      'question_templates': {'vocab_meaning': [_choice('語一', '词一', '别的词'),
                                                               _choice('語一です', '是词一', '不是')]}}], now=START)
    when = ready(learning, word)
    source = learning.get_item(word)
    learning.append_questions([{**build_learning_question(source, 'vocab_meaning', variant=0), 'id': 'r0'}], now=when)
    with store._db:
        learning._transaction()
        new = LearningReplacementStore(learning).exchange_in_transaction(
            'r0', build_learning_question(source, 'vocab_meaning', variant=1), now=when)
    validate_replacements(store._db)
    pending = [r for r in rows(store, 'learning_question') if r['status'] == 'pending']
    assert [r['question_id'] for r in pending] == [new] and json.loads(pending[0]['draft_json']) == {}
    assert untouched_course_questions(store._db, pending) == set()   # only its 'replacement_of' tells
    # Removing it would break the replacement receipt that names it (tried and rolled back here):
    with pytest.raises(ValueError):
        with store._db:
            store._db.execute('BEGIN IMMEDIATE')
            store._db.execute('DELETE FROM learning_question WHERE question_id=?', (new,))
            validate_replacements(store._db)
    archive_previous_rounds(store, now=when)
    assert archived_question(store, new) is not None
    validate_replacements(store._db)
    validate_database(store._db)


# Lifecycle ----------------------------------------------------------------------------------------------------

def test_an_interrupted_start_leaves_the_record_as_before_and_a_second_start_is_correct(stores, tmp_path, monkeypatch):
    store, learning = stores
    when = lessons(learning, 4)
    native(store, state={'page': 'question'})
    learning.save_draft('u3', {'presented': True})
    before = tables(store)
    old = twin(store, tmp_path / 'before')
    calls = []
    real = practice_archive._evidence

    def failing(before_row, **kw):
        calls.append(kw)
        if len(calls) == 2:   # after the kana receipt and the removal of the untouched rows
            raise RuntimeError('synthetic failure inside the pass')
        return real(before_row, **kw)
    monkeypatch.setattr(practice_archive, '_evidence', failing)
    with pytest.raises(RuntimeError, match='synthetic failure'):
        archive_previous_rounds(store, now=when)
    assert not store._db.in_transaction and tables(store) == before
    monkeypatch.setattr(practice_archive, '_evidence', real)
    try:
        original.archive_previous_rounds(old, now=when)
        archive_previous_rounds(store, now=when)
        expected = tables(old)
        gone = {practice_archive._course_key(q) for q in ('u0', 'u1', 'u2')}
        expected['app_setting'] = [row for row in expected['app_setting'] if row['key'] not in gone]
        expected['learning_question'] = [row for row in expected['learning_question'] if row['question_id'] == 'u3']
        assert tables(store) == expected
    finally:
        old.close()


def test_a_failing_removal_rolls_the_whole_start_back(stores):
    store, learning = stores
    when = lessons(learning, 2)
    native(store)
    before = tables(store)
    store._db.execute("CREATE TEMP TRIGGER refuse_removal BEFORE DELETE ON learning_question "
                      "BEGIN SELECT RAISE(ABORT, 'synthetic removal failure'); END")
    with pytest.raises(Exception, match='synthetic removal failure'):
        archive_previous_rounds(store, now=when)
    assert tables(store) == before and not store._db.in_transaction


def test_a_kana_lane_and_a_deck_exposure_end_as_before(stores, tmp_path):
    """A restart during a practice deck: its exposure row and lesson stamp stay; the course queue ends."""
    store, learning = stores
    when = lessons(learning, 2)
    learning.record_practice_exposure('deck:sitting', {**question(cid(0), 'deck-question'), 'id': 'deck-question'},
                                      {'outcome': 'correct', 'correct': True, 'answered_text': '正在读书'}, now=when)
    sid = native(store, state={'page': 'feedback', 'feedback_pending': True, 'answered_text': 'a'})
    evidence, contents = rows(store, 'learning_evidence'), rows(store, 'learning_content')
    old = twin(store, tmp_path / 'before')
    try:
        original.archive_previous_rounds(old, now=when)
        archive_previous_rounds(store, now=when)
        assert rows(store, 'learning_evidence') == evidence and rows(store, 'learning_content') == contents
        assert receipts(store, NATIVE_PREFIX) == receipts(old, NATIVE_PREFIX)
        assert NATIVE_PREFIX + sid in receipts(store) and rows(store, 'learning_question') == []
    finally:
        old.close()


# R3 ---------------------------------------------------------------------------------------------------------

def _count_validations(monkeypatch):
    seen = []
    for name in ('_validate_course', '_validate_native'):
        real = getattr(practice_archive, name)

        def counted(db, key, value, _real=real):
            seen.append(key)
            return _real(db, key, value)
        monkeypatch.setattr(practice_archive, name, counted)
    return seen


def test_a_start_validates_only_the_receipts_it_writes_once_each(stores, monkeypatch):
    store, learning = stores
    when = lessons(learning, 30)
    for n in range(30):
        learning.save_draft(f'u{n}', {'presented': True})
    native(store, state={'page': 'question'})
    archive_previous_rounds(store, now=when)            # 31 old receipts
    queue(learning, 30, when + timedelta(hours=1))
    learning.save_draft(rows(store, 'learning_question')[-1]['question_id'], {'presented': True})
    sid = native(store, at=when + timedelta(hours=1))
    seen = _count_validations(monkeypatch)
    archive_previous_rounds(store, now=when + timedelta(hours=1))
    met = rows(store, 'learning_question')[-1]['question_id']
    assert sorted(seen) == sorted([NATIVE_PREFIX + sid, practice_archive._course_key(met)])


def test_backup_inspection_and_restore_still_reject_a_damaged_old_receipt(stores, tmp_path):
    store, learning = stores
    when = lessons(learning, 2)
    learning.save_draft('u0', {'presented': True})
    archive_previous_rounds(store, now=when)
    key = practice_archive._course_key('u0')
    damaged = json.loads(receipts(store)[key])
    damaged['evidence']['draft'] = {'presented': False}
    store.set_setting(key, damaged)
    # The start no longer reads old receipts: it succeeds and leaves the damaged one as it is.
    # (A collection that proposes the damaged receipt's own id still refuses, as before: archived_question.)
    archive_previous_rounds(store, now=when + timedelta(hours=1))
    assert json.loads(receipts(store)[key]) == damaged
    with pytest.raises(ValueError):
        validate_database(store._db)
    payload = export_backup(store)
    with StudyStore(tmp_path / 'target', time_zone='UTC') as target:
        LearningStore(target)
        before = tables(target)
        with pytest.raises(ValueError):
            inspect_backup(target, payload)
        with pytest.raises(ValueError):
            import_backup(target, payload)
        assert tables(target) == before


def test_the_evidence_scan_reads_only_rows_written_since_the_earliest_queued_question(stores, tmp_path):
    """100,000 old evidence rows (written before the queue) plus a few recent ones, one naming a queued question."""
    store, learning = stores
    old = (START - timedelta(days=30)).isoformat()
    filler = json.dumps({'question_id': 'u1', 'prompt': '語' * 800}, ensure_ascii=False)   # old, same id text
    learning.enroll([item(cid(n)) for n in range(5)], now=START)
    with store._db:
        store._db.execute('BEGIN IMMEDIATE')
        store._db.executemany(
            "INSERT INTO learning_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'',NULL)",
            ((f'synthetic:old:{n}', cid(n % 5), 'understanding', 'grammar_meaning', 'v1', 'course', 'exposure', 'k',
              '{}', old, 'correct', 0, 0, filler) for n in range(100_000)))
    for n in range(5):
        ready(learning, cid(n))
    when = START + timedelta(seconds=61)
    queue(learning, 5, when)
    recent = when + timedelta(minutes=1)
    for n in (0, 3):   # recent rows that name no queued question
        learning.record_practice_exposure(f'deck:other{n}', {**question(cid(n), f'deck{n}'), 'id': f'deck{n}'},
                                          {'outcome': 'correct', 'correct': True, 'answered_text': '正在读书'}, now=recent)
    learning.record_practice_exposure('deck:u2', {**question(cid(2), 'u2'), 'id': 'u2'},
                                      {'outcome': 'correct', 'correct': True, 'answered_text': '正在读书'}, now=recent)
    pending = [row for row in rows(store, 'learning_question') if row['status'] == 'pending']
    # The old rows' 'u1' is not a reference to the queued u1 (written before it existed); the recent one names u2.
    assert untouched_course_questions(store._db, pending) == {'u0', 'u1', 'u3', 'u4'}
    copy = twin(store, tmp_path / 'before')
    try:
        original.archive_previous_rounds(copy, now=recent)
        t0 = time.perf_counter()
        assert archive_previous_rounds(store, now=recent)['course'] == 1
        elapsed = time.perf_counter() - t0
        assert archived_question(store, 'u2') == archived_question(copy, 'u2')    # archived as before
        assert [row['question_id'] for row in rows(store, 'learning_question')] == ['u2']
        # Generous bound: measured about 0.02 s here, against about 0.35 s when every snapshot was parsed.
        assert elapsed < 1.0, elapsed
    finally:
        copy.close()


# R4 ---------------------------------------------------------------------------------------------------------

def _heavy(store, learning, starts, count):
    """A record the previous pass grew: ``starts`` starts, each archiving ``count`` untouched questions."""
    when = lessons(learning, count)
    for start in range(1, starts + 1):
        at = when + timedelta(hours=start)
        original.archive_previous_rounds(store, now=at)
        queue(learning, count, at)
    return when + timedelta(hours=starts + 1)


def test_thousands_of_old_receipts_stay_readable_round_trip_and_no_longer_slow_the_start(stores, tmp_path):
    store, learning = stores
    at = _heavy(store, learning, 25, 80)                  # 2000 old receipts of untouched questions
    old_receipts = receipts(store, COURSE_PREFIX)
    assert len(old_receipts) == 2000
    archived = [row['question_id'] for row in rows(store, 'learning_question') if row['status'] == 'completed']
    assert len(archived) == 2000
    # Every reader still works on them.
    assert archived_question(store, archived[0])['before']['question_id'] == archived[0]
    assert learning.get_question(archived[-1])['status'] == 'archived'
    assert archived_attempts(store) == []
    assert len(validate_database(store._db)) == 2000
    # Backup and restore round-trip them unchanged.
    payload = export_backup(store)
    with StudyStore(tmp_path / 'restored', time_zone='UTC') as target:
        LearningStore(target)
        inspect_backup(target, payload)
        import_backup(target, payload)
        assert receipts(target, COURSE_PREFIX) == old_receipts
        assert [r for r in rows(target, 'learning_question')] == rows(store, 'learning_question')
    # The start: untouched rows go, old receipts stay byte for byte, and the time does not follow their number.
    with StudyStore(tmp_path / 'light', time_zone='UTC') as light:
        light_learning = LearningStore(light)
        light_at = lessons(light_learning, 80)
        t0 = time.perf_counter()
        archive_previous_rounds(light, now=light_at)
        light_time = time.perf_counter() - t0
    t0 = time.perf_counter()
    archive_previous_rounds(store, now=at)
    heavy_time = time.perf_counter() - t0
    assert receipts(store, COURSE_PREFIX) == old_receipts
    assert [row['status'] for row in rows(store, 'learning_question')].count('pending') == 0
    assert heavy_time < max(1.0, 5 * light_time), (heavy_time, light_time)


# Lifecycle in the app (fake UI): a restart with a question on screen, a draft, after 还不会, while teaching ------

WORD = 'word:untouched:app'
APP_KINDS = ['vocab_meaning', 'vocab_reading_choice', 'vocab_reading']


def app_word():
    return {'id': WORD, 'content_id': WORD, 'family': 'word', 'title': '語一', 'reading': 'ごいち', 'meaning': '词一',
            'revision': '1', 'abilities': ['understanding', 'reading', 'use'], 'source_ids': ['test'],
            'instruction': {'summary': '词一', 'rule': '名词。', 'steps': [], 'example': '語一です。',
                            'translation': '是词一。', 'prerequisite': '', 'contrast': ''},
            'question_templates': {
                'vocab_meaning': [_choice('語一', '词一', '别的词')],
                'vocab_reading_choice': [{**_choice('語一', 'ごいち', 'ごに'), 'task': '読み方'}],
                'vocab_reading': [{'prompt': '語一', 'answer': 'ごいち', 'input_mode': 'text', 'feedback': '说明',
                                   'strict': True}]}}


def app_start(app):
    from kanamemory.learning_questions import PREFERENCE_VERSION, SOUND_PREFERENCE_VERSION
    from kanamemory.learning_ui import LEARNING_PREFS
    app.store.set_setting(LEARNING_PREFS, {'enabled': APP_KINDS, SOUND_PREFERENCE_VERSION: PREFERENCE_VERSION})
    app.learning_store.enroll([app_word()])
    app.continue_learning()


def restart_like_before(app, directory):
    """An app start, checked against the previous pass on a copy: met questions archived byte for byte,
    untouched ones gone, nothing else different. Returns the ids that were removed."""
    from kanamemory import practice_round
    store, at = app.store, app._test_clock[0]
    pending = [row for row in rows(store, 'learning_question') if row['status'] == 'pending']
    untouched = untouched_course_questions(
        store._db, [row for row in pending if not practice_archive._owned_course(store._db, row)])
    old = twin(store, directory)
    try:
        original.archive_previous_rounds(old, now=at)
        archive_previous_rounds(store, now=at)
        expected = tables(old)
        gone = {practice_archive._course_key(qid) for qid in untouched}
        expected['app_setting'] = [row for row in expected['app_setting'] if row['key'] not in gone]
        expected['learning_question'] = [row for row in expected['learning_question']
                                         if row['question_id'] not in untouched]
        assert tables(store) == expected
    finally:
        old.close()
    practice_round.reset(store)
    practice_round.enable(store)
    return untouched


@pytest.mark.parametrize('draft', [False, True])
def test_restart_with_the_question_on_screen_archives_it(learning_app, tmp_path, draft):
    app = learning_app
    app_start(app)
    assert app.page == 'learning_question'
    shown = app._learning_question['id']
    assert json.loads(app.store._db.execute('SELECT draft_json FROM learning_question WHERE question_id=?',
                                            (shown,)).fetchone()[0]) == {'presented': True}
    if draft:
        app.learning_store.save_draft(shown, {'answer': '词'})
    removed = restart_like_before(app, tmp_path / 'before')
    assert shown not in removed and archived_question(app.store, shown) is not None
    app._test_clock[0] += timedelta(minutes=11)
    app.continue_learning()
    assert app.page == 'learning_question' and app._learning_question['id'] != shown


def test_restart_right_after_still_unknown_and_while_teaching_keeps_the_lesson_state(learning_app, tmp_path):
    app = learning_app
    app_start(app)
    probe = app._learning_question['id']
    click(app, '还不会')        # the probe's failed diagnostic; the round is sealed
    lesson = rows(app.store, 'learning_content')
    events = rows(app.store, 'learning_event')
    restart_like_before(app, tmp_path / 'after_decline')
    assert rows(app.store, 'learning_content') == lesson and rows(app.store, 'learning_event') == events
    assert archived_question(app.store, probe) is None     # answered (an event), not archived
    app._test_clock[0] += timedelta(minutes=2)
    app.continue_learning()
    assert app.page == 'learning_intro'                     # in the middle of the teaching block
    lesson = rows(app.store, 'learning_content')
    restart_like_before(app, tmp_path / 'teaching')
    assert rows(app.store, 'learning_content') == lesson
    app.continue_learning()
    assert app.page == 'learning_intro'
