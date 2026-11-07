"""Actual-review proportional service, including low-volume cross-day visits."""
from datetime import timedelta
import pytest

from support.first_teaching_dispatch_core_support import NOW
from support.first_teaching_dispatch_core_support import stores
from support.first_teaching_dispatch_core_support import kana
from support.first_teaching_dispatch_core_support import install_queue
from support.first_teaching_dispatch_core_support import normal
from support.first_teaching_dispatch_core_support import course
from support.first_teaching_dispatch_core_support import pick
from support.first_teaching_dispatch_core_support import rows
from support.learning_store_fixtures import item, question
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.first_teaching_dispatch import due_reviews_served, teaching_review_threshold


from support.first_teaching_backlog_support import backlog, serve


def test_cross_day_old_due_no_longer_starves_ready_enrolled_instruction(stores):
    store, learning = stores
    sid = backlog(store)
    course(learning, 'ready')
    original_due = store.get_card('old-waiting', 'kana_choice', 'hiragana')['due_at_utc']
    serve(store, 63, NOW-timedelta(minutes=3))
    assert pick(store, learning, normal(store, sid)) is None
    serve(store, 1, NOW-timedelta(minutes=1), prefix='earned')
    before = rows(store)
    selected = pick(store, learning, normal(store, sid))
    assert selected[0] == 'course' and selected[1]['content']['id'] == 'ready'
    assert selected[1]['type'] == 'instruction'
    assert rows(store) == before
    assert store.get_card('old-waiting', 'kana_choice', 'hiragana')['due_at_utc'] == original_due


@pytest.mark.parametrize('daily', [3, 6])
def test_low_volume_reviews_accumulate_across_midnight_restarts_and_backup(stores, tmp_path, daily):
    store, learning = stores
    sid = backlog(store)
    course(learning, 'low-volume-ready')
    # Even the highest pressure threshold has a finite cross-day bound:
    # 3/day earns a block on active day 22; 6/day on active day 11.
    days = (64 + daily - 1) // daily
    for day in range(days):
        at = NOW+timedelta(days=day)
        serve(store, daily, at-timedelta(minutes=2), prefix=f'day-{day}')
        path = store.data_dir
        store.close()
        with StudyStore(path, time_zone='UTC') as reopened:
            teaching = LearningStore(reopened)
            head = normal(reopened, sid, at)
            before = rows(reopened)
            selected = pick(reopened, teaching, head, now=at)
            assert rows(reopened) == before
            if day < days - 1:
                assert selected is None
            else:
                assert selected[1]['content']['id'] == 'low-volume-ready'
                payload = reopened.export_backup()
        if day < days - 1:
            store = StudyStore(path, time_zone='UTC')
            learning = LearningStore(store)
    with StudyStore(tmp_path/'restored', time_zone='UTC') as restored:
        teaching = LearningStore(restored)
        restored.import_backup(payload)
        selected = pick(restored, teaching, normal(restored, sid, at), now=at)
        assert selected[1]['content']['id'] == 'low-volume-ready'


def test_one_block_then_more_actual_service_required_and_two_blocks_same_day_allowed(stores):
    store, learning = stores
    sid = backlog(store)
    course(learning, 'first')
    course(learning, 'second')
    # Long historical activity does not buy an unlimited run of introductions.
    serve(store, 96, NOW-timedelta(minutes=3))
    selected = pick(store, learning, normal(store, sid))
    assert selected[1]['content']['id'] == 'first'
    learning.mark_instruction_exposed('first', now=NOW)
    learning.advance_instruction('first', 'intro_done', now=NOW+timedelta(seconds=10))
    at = NOW+timedelta(seconds=11)
    assert pick(store, learning, normal(store, sid, at), now=at)[1]['stage'] == 'guide'
    learning.advance_instruction('first', 'guide_correct', now=at)
    assert pick(store, learning, normal(store, sid, at), now=at) is None
    at += timedelta(seconds=61)
    selected = pick(store, learning, normal(store, sid, at), now=at)
    assert selected[1]['question']['id'] == 'q-first'
    learning.submit('q-first', selected[1]['question']['answer'], True, now=at)
    learning.finalize('q-first', 3, now=at+timedelta(seconds=4))
    at += timedelta(minutes=1)
    assert pick(store, learning, normal(store, sid, at), now=at) is None
    serve(store, 63, at, prefix='next-63')
    at += timedelta(minutes=2)
    assert pick(store, learning, normal(store, sid, at), now=at) is None
    serve(store, 1, at, prefix='next-64')
    at += timedelta(seconds=1)
    selected = pick(store, learning, normal(store, sid, at), now=at)
    assert selected[1]['content']['id'] == 'second'


@pytest.mark.parametrize('priority', [(0, 0), (1, 0), (2, NOW.timestamp())])
def test_earned_slot_cannot_displace_feedback_draft_or_true_short_step(stores, priority):
    store, learning = stores
    sid = backlog(store)
    course(learning, 'ready')
    serve(store, 64, NOW-timedelta(minutes=2))
    head = normal(store, sid)
    head['dispatch_priority'] = priority
    before = rows(store)
    assert pick(store, learning, head) is None
    assert rows(store) == before


def test_only_finalized_already_due_primary_reviews_earn_credit(stores):
    store, learning = stores
    since = NOW-timedelta(days=1)
    # First tests and premature re-testing are not due-review service.
    for i in range(10):
        store.record_review(f'first-{i}', 'kana_choice', 'hiragana', 4, now=NOW-timedelta(minutes=5))
        store.record_review(f'first-{i}', 'kana_choice', 'hiragana', 4, now=NOW-timedelta(minutes=4))
    assert due_reviews_served(store, NOW, since) == 0
    cid = 'course-review'
    old = NOW-timedelta(days=4)
    learning.enroll(item(cid, abilities=['understanding']), now=old)
    learning.advance_instruction(cid, 'intro_done', now=old)
    learning.advance_instruction(cid, 'guide_correct', now=old)
    first = question(cid, qid='first-course')
    learning.append_questions(first, now=old+timedelta(minutes=2))
    learning.submit(first['id'], first['answer'], True, now=old+timedelta(minutes=2))
    learning.finalize(first['id'], 1, now=old+timedelta(minutes=2))
    q = question(cid, qid='real-due-course')
    learning.append_questions(q, now=NOW)
    learning.submit(q['id'], q['answer'], True, now=NOW)
    assert due_reviews_served(store, NOW, since) == 0
    learning.finalize(q['id'], 3, now=NOW+timedelta(seconds=5))
    assert due_reviews_served(store, NOW, since) == 0
    assert due_reviews_served(store, NOW+timedelta(seconds=5), since) == 1


@pytest.mark.parametrize('mode', ['disabled', 'suspended', 'dependency', 'future'])
def test_earned_service_survives_unavailable_latest_started_block(stores, mode):
    store, learning = stores
    backlog(store)
    course(learning, 'unavailable', **({'depends_on': ['missing']} if mode == 'dependency' else {}))
    learning.mark_instruction_exposed('unavailable', now=NOW-timedelta(days=1))
    if mode == 'suspended':
        learning.set_suspended('unavailable', 'understanding', True)
    elif mode == 'future':
        with store._db:
            store._db.execute('UPDATE learning_question SET not_before_utc=? WHERE question_id=?',
                ((NOW+timedelta(days=1)).isoformat(), 'q-unavailable'))
    fresh = kana('available-kana', 'い', 'i')
    sid = install_queue(store, [kana('old-waiting', 'あ', 'a'), fresh])
    serve(store, 64, NOW-timedelta(minutes=2))
    head = normal(store, sid)
    before = rows(store)
    selected = pick(store, learning, head, enabled=[] if mode == 'disabled' else ['grammar_meaning'])
    assert selected[0] == 'kana' and selected[1]['question'] == fresh
    assert rows(store) == before


@pytest.mark.parametrize('age,expected', [(0, 8), (.99, 8), (1, 16), (2.99, 16),
                                       (3, 32), (6.99, 32), (7, 64), (1000, 64)])
def test_backlog_threshold_is_bounded_and_responds_to_available_due_age(age, expected):
    assert teaching_review_threshold(age * 86400) == expected
