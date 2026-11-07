"""Real-store integration of app-instance rounds and durable learning evidence."""
from datetime import timedelta
from types import MethodType
import json

import pytest

from kanamemory import practice_round as rounds, domain
from kanamemory.app import KanaMemory
from kanamemory.domain import Question, StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.practice_archive import (
    COURSE_PREFIX, archive_previous_rounds, archived_attempts, archived_question,
)
from kanamemory.progress_backup import export_backup, import_backup, inspect_backup
from support.learning_store_fixtures import START, item, question, ready
from support.app_fixtures import setup_app, question_app
from support.learning_fixtures import learning_app
from support.study_path_runtime_support import path_app, home

NOW = START + timedelta(minutes=10)


def test_protected_native_draft_captures_whole_round_and_preserves_remaining_instances(tmp_path):
    with StudyStore(tmp_path / 'native-fixed', time_zone='UTC') as store:
        LearningStore(store)
        a = Question('kana-3042', 'kana_romaji', 'hiragana', 'あ', 'a')
        b = Question('kana-3044', 'kana_romaji', 'hiragana', 'い', 'i')
        sid = store.start_session([a, b, a], now=NOW)
        store.save_session_progress(sid, 0, state={'page': 'question', 'romaji_answer': 'a'})
        rounds.enable(store)
        plan = rounds.begin(store)
        protected = store.prepare_next_question(sid, now=NOW)
        assert protected['state']['romaji_answer'] == 'a'
        assert len(plan.native_orders[sid]) == 3
        rounds.seal(store)
        store.save_session_progress(sid, 1, state={})
        next_head = store.prepare_next_question(sid, now=NOW)
        assert [q.content_id for q in next_head['questions'][1:]] == [b.content_id, a.content_id]
        assert next_head['question_index'] == 1
        assert rows(store, 'review_event') == []


def rows(store, table):
    return [tuple(row) for row in store._db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]


def course(store, suffix, *, kind='grammar_meaning', now=NOW):
    learning = LearningStore(store)
    cid, qid = 'grammar:round_lifecycle:' + suffix, 'round-question:' + suffix
    ability = 'application' if kind == 'grammar_cloze' else 'understanding'
    learning.enroll(item(cid, title='合成轮次', abilities=[ability]), now=now - timedelta(minutes=5))
    ready(learning, cid, when=now - timedelta(minutes=5))
    snapshot = question(cid, qid, kind=kind, ability=ability, family='grammar',
                        feedback='合成结果', choices=['正在读书', '已经结束'])
    assert learning.append_questions(snapshot, now=now) == [qid]
    return learning, snapshot


def startup(store, *, now=NOW):
    """The explicit archive/enable sequence used by production app startup."""
    summary = archive_previous_rounds(store, now=now)
    rounds.enable(store)
    return summary


def test_controller_home_resume_keeps_initial_order_when_other_draft_priority_changes(path_app):
    app = path_app
    now = app._test_clock[0]
    learning, first = course(app.store, 'home-first', now=now)
    _, second = course(app.store, 'home-second', now=now)
    app.store.set_setting('learning_preferences_v1', {'enabled': ['grammar_meaning']})
    rounds.enable(app.store)
    app.continue_study_path()
    assert app.page == 'learning_question'
    assert app._learning_question['id'] == first['id']
    plan = rounds.begin(app.store)
    initial = plan.course_order
    assert initial == (first['id'], second['id'])
    learning.save_draft(second['id'], {'answer': '后来开始的草稿'})
    before = rows(app.store, 'learning_question')
    home(app)
    app.continue_study_path()
    assert rounds.begin(app.store) is plan
    assert plan.course_order == initial
    assert app._learning_question['id'] == first['id']
    assert rows(app.store, 'learning_question') == before
    assert rows(app.store, 'learning_event') == []


def test_new_store_startup_archives_old_draft_and_reissues_new_question_id(tmp_path):
    directory = tmp_path / 'restart'
    with StudyStore(directory, time_zone='UTC') as store:
        learning, original = course(store, 'restart')
        rounds.enable(store)
        first_run = rounds.begin(store).run_id
        assert learning.current(['grammar_meaning'], now=NOW)['question']['id'] == original['id']
        rounds.seal(store)
        learning.save_draft(original['id'], {'answer': '原始未提交草稿'})
        before_units = rows(store, 'learning_unit')
    with StudyStore(directory, time_zone='UTC') as store:
        learning = LearningStore(store)
        # A worker connection alone does not archive anything or create a round.
        assert not rounds.active(store)
        assert learning.get_question(original['id'])['status'] == 'pending'
        assert startup(store)['course'] == 1
        assert rounds.begin(store).run_id != first_run
        receipt = archived_question(store, original['id'])
        assert receipt['evidence']['draft'] == {'answer': '原始未提交草稿'}
        assert receipt['evidence']['result'] is None
        added = learning.append_questions(original, now=NOW)
        assert len(added) == 1 and added[0].startswith(original['id'] + ':round:')
        assert learning.append_questions(original, now=NOW) == []
        assert learning.current(['grammar_meaning'], now=NOW)['question']['id'] == added[0]
        assert rows(store, 'learning_unit') == before_units
        assert rows(store, 'learning_event') == []


def test_confirmed_native_and_course_reviews_survive_restart_without_reapplication(tmp_path):
    directory = tmp_path / 'confirmed'
    with StudyStore(directory, time_zone='UTC') as store:
        learning, q = course(store, 'confirmed')
        learning.submit(q['id'], q['answer'], True, outcome='correct', now=NOW)
        learning.finalize(q['id'], 3, now=NOW)
        sid = store.start_session([Question('kana-3042', 'kana_romaji', 'hiragana', 'あ', 'a')], now=NOW)
        store.save_session_progress(sid, 0)
        store.record_review('kana-3042', 'kana_romaji', 'hiragana', 3, session_id=sid,
                            question_index=0, answered_text='a', outcome='correct', now=NOW)
        before = {name: rows(store, name) for name in ('learning_unit', 'learning_event',
                  'review_event', 'session_answer', 'study_card')}
    with StudyStore(directory, time_zone='UTC') as store:
        learning = LearningStore(store)
        startup(store)
        learning.finalize(q['id'], 3, now=NOW + timedelta(hours=1))
        startup(store, now=NOW + timedelta(hours=1))
        for name, original_rows in before.items():
            assert rows(store, name) == original_rows, name
        assert archived_question(store, q['id']) is None
        assert archived_attempts(store) == []


def test_submitted_unrated_answer_archives_as_evidence_without_a_review(tmp_path):
    directory = tmp_path / 'unrated'
    with StudyStore(directory, time_zone='UTC') as store:
        learning, q = course(store, 'unrated')
        learning.save_draft(q['id'], {'answer': '错误原回答'})
        result = learning.submit(q['id'], '错误原回答', False, outcome='incorrect', now=NOW)
        units = rows(store, 'learning_unit')
        assert rows(store, 'learning_event') == []
    with StudyStore(directory, time_zone='UTC') as store:
        LearningStore(store)
        startup(store)
        attempts = archived_attempts(store)
        assert len(attempts) == 1
        assert attempts[0]['answered_text'] == '错误原回答'
        assert attempts[0]['result'] == result
        assert attempts[0]['correct'] is False and attempts[0]['outcome'] == 'incorrect'
        assert attempts[0]['rating'] is None and attempts[0]['scheduled'] is False
        assert rows(store, 'learning_unit') == units
        assert rows(store, 'learning_event') == []
        startup(store)
        assert archived_attempts(store) == attempts


def test_archived_evidence_backup_roundtrip_and_tamper_rejection(tmp_path):
    with StudyStore(tmp_path / 'source', time_zone='UTC') as source, StudyStore(
            tmp_path / 'restored', time_zone='UTC') as target:
        learning, q = course(source, 'backup')
        learning.submit(q['id'], q['answer'], True, outcome='correct', now=NOW)
        startup(source)
        payload = export_backup(source)
        inspect_backup(target, payload)
        import_backup(target, payload)
        LearningStore(target)
        assert archived_attempts(target) == archived_attempts(source)
        assert rows(target, 'learning_unit') == rows(source, 'learning_unit')
        assert rows(target, 'learning_event') == []
        before = rows(target, 'app_setting')
        key = source._db.execute('SELECT key FROM app_setting WHERE key GLOB ?',
                                 (COURSE_PREFIX + '*',)).fetchone()[0]
        receipt = archived_question(source, q['id'])
        receipt['evidence']['result']['answered_text'] = '篡改后的回答'
        source.set_setting(key, receipt)
        with pytest.raises(ValueError):
            inspect_backup(target, export_backup(source))
        assert rows(target, 'app_setting') == before


def test_explicit_extended_round_admits_late_question_but_home_resume_does_not(tmp_path):
    with StudyStore(tmp_path / 'append', time_zone='UTC') as store:
        learning, first = course(store, 'append-first')
        rounds.enable(store)
        plan = rounds.begin(store)
        assert learning.current(['grammar_meaning'], now=NOW)['question']['id'] == first['id']
        rounds.seal(store)
        _, late = course(store, 'append-late')
        assert learning.current(['grammar_meaning'], now=NOW)['question']['id'] == first['id']
        assert late['id'] not in plan.course_order
        learning.submit(first['id'], first['answer'], True, outcome='correct', now=NOW)
        learning.finalize(first['id'], 3, now=NOW)
        assert learning.current(['grammar_meaning'], now=NOW) is None
        assert rounds.begin(store) is plan
        second = rounds.restart(store)
        assert second.run_id != plan.run_id
        assert learning.current(['grammar_meaning'], now=NOW)['question']['id'] == late['id']


def test_sealed_course_order_still_honors_live_kind_and_suspension_gates(tmp_path):
    with StudyStore(tmp_path / 'settings', time_zone='UTC') as store:
        learning, first = course(store, 'settings-first')
        _, second = course(store, 'settings-second', kind='grammar_cloze')
        rounds.enable(store)
        rounds.begin(store)
        assert learning.current(['grammar_meaning', 'grammar_cloze'], now=NOW)['question']['id'] == first['id']
        rounds.seal(store)
        units = rows(store, 'learning_unit')
        assert learning.current(['grammar_cloze'], now=NOW)['question']['id'] == second['id']
        assert learning.current([], now=NOW)['type'] == 'disabled'
        learning.set_suspended(second['target_id'], 'application', True)
        blocked = learning.current(['grammar_cloze'], now=NOW)
        assert blocked is None or blocked['type'] != 'question'
        learning.set_suspended(second['target_id'], 'application', False)
        assert learning.current(['grammar_meaning'], now=NOW)['question']['id'] == first['id']
        assert rows(store, 'learning_unit') == units
        assert rows(store, 'learning_event') == []


def test_native_feedback_is_durable_before_navigation_and_lanes_restart_independently(path_app, monkeypatch):
    app = path_app
    store, directory = app.store, app.store.data_dir
    now = app._test_clock[0]
    monkeypatch.setattr(domain, 'utc_now', lambda: now)
    main = Question('kana-3042', 'kana_romaji', 'hiragana', 'あ', 'a')
    sid = store.start_session([main], now=now)
    store.save_session_progress(sid, 0)
    word = Question('word:round-test', 'word_meaning_kana', 'hiragana', '水', 'みず',
                    metadata={'word_romaji_drill': True})
    word_sid = store.start_session([word], now=now)
    store.save_session_progress(word_sid, 0, state={'word_answer': 'み'})
    app.session_id, app.questions = sid, store.get_session_questions(sid)
    app.question_index, app.session_right = 0, 0
    app.question = app.questions[0]
    app.page, app.question_audio_ok, app.answer_revealed = 'question', True, False
    app._answer_submitted_at = None
    app._checkpoint = MethodType(KanaMemory._checkpoint, app)
    app.check_answer('wrong')
    assert app.page == 'feedback'
    checkpoint = store.load_session_progress(lane='main')['state']
    # No home/navigation call has occurred: check_answer itself must persist.
    assert checkpoint['feedback_pending'] is True
    assert checkpoint['answered_text'] == 'wrong' and checkpoint['answer_correct'] is False
    assert store.load_session_progress(lane='word_romaji')['state'] == {'word_answer': 'み'}
    before = {name: rows(store, name) for name in ('study_card', 'review_event',
              'session_answer', 'practice_attempt')}
    store.close()
    with StudyStore(directory, time_zone='UTC') as reopened:
        LearningStore(reopened)
        assert startup(reopened, now=now)['native'] == 2
        assert reopened.load_session_progress(lane='main') is None
        assert reopened.load_session_progress(lane='word_romaji') is None
        attempts = archived_attempts(reopened)
        assert len(attempts) == 1 and attempts[0]['answered_text'] == 'wrong'
        assert attempts[0]['rating'] is None and not attempts[0]['scheduled']
        assert attempts[0]['correct'] is False
        for name, original_rows in before.items():
            assert rows(reopened, name) == original_rows
        second = reopened.start_session([main], now=now)
        reopened.save_session_progress(second, 0)
        second_word = reopened.start_session([word], now=now)
        reopened.save_session_progress(second_word, 0)
        assert second != sid and second_word != word_sid
        assert reopened.load_session_progress(lane='main')['session_id'] == second
        assert reopened.load_session_progress(lane='word_romaji')['session_id'] == second_word
        assert reopened.load_session_progress(lane='main')['state'] == {}
        assert reopened.load_session_progress(lane='word_romaji')['state'] == {}
        inspect_backup(reopened, export_backup(reopened))
