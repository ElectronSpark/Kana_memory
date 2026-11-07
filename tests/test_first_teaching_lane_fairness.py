"""Earned teaching service rotates fresh lanes without splitting real blocks."""
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
from support.first_teaching_backlog_support import backlog
from support.first_teaching_backlog_support import serve
from kanamemory.new_dispatch import SETTING, read


from support.first_teaching_lane_fairness_support import both_fresh


@pytest.mark.parametrize('last_lane,expected', [('course', 'kana'), ('kana', 'course'), (None, 'course')])
def test_earned_fresh_candidates_use_the_cursor_without_read_side_effects(stores, last_lane, expected):
    store, learning = stores
    sid, fresh = both_fresh(store, learning, last_lane=last_lane)
    head = normal(store, sid)
    course_head = learning.current(['grammar_meaning'], now=NOW, fair_new=True, teaching_only=True)
    assert course_head['stage'] == 'intro' and course_head['new_dispatch_continue'] is False
    before = rows(store)
    for _ in range(3):
        selected = pick(store, learning, head)
        assert selected[0] == expected
        if expected == 'kana':
            assert selected[1]['question'] == fresh
        else:
            assert selected[1]['content']['id'] == 'fresh-course'
        assert rows(store) == before
        assert read(store._db)['last_lane'] == last_lane


@pytest.mark.parametrize('stage', ['intro', 'guide', 'first_check'])
def test_genuine_started_course_block_continues_before_opening_fresh_kana(stores, stage):
    store, learning = stores
    sid, _ = both_fresh(store, learning, last_lane='course')
    learning.mark_instruction_exposed('fresh-course', now=NOW-timedelta(minutes=2))
    if stage in {'guide', 'first_check'}:
        learning.advance_instruction('fresh-course', 'intro_done', now=NOW-timedelta(seconds=110))
    if stage == 'first_check':
        learning.advance_instruction('fresh-course', 'guide_correct', now=NOW-timedelta(seconds=90))
    head = normal(store, sid)
    before = rows(store)
    selected = pick(store, learning, head)
    assert selected[0] == 'course' and selected[1]['content']['id'] == 'fresh-course'
    assert selected[1]['type'] == ('question' if stage == 'first_check' else 'instruction')
    assert rows(store) == before


@pytest.mark.parametrize('mode', ['closed', 'paused', 'dependency', 'future'])
def test_ineligible_course_cannot_take_the_earned_slot_from_new_kana(stores, mode):
    store, learning = stores
    sid, fresh = both_fresh(store, learning, last_lane='kana', blocked=mode == 'dependency')
    if mode == 'paused':
        learning.set_suspended('fresh-course', 'understanding', True)
    elif mode == 'future':
        with store._db:
            store._db.execute('UPDATE learning_question SET not_before_utc=? WHERE question_id=?',
                ((NOW+timedelta(days=1)).isoformat(), 'q-fresh-course'))
    head = normal(store, sid)
    before = rows(store)
    selected = pick(store, learning, head, enabled=[] if mode == 'closed' else ['grammar_meaning'])
    assert selected[0] == 'kana' and selected[1]['question'] == fresh
    assert rows(store) == before


@pytest.mark.parametrize('mode', ['closed', 'paused', 'future'])
def test_unavailable_fresh_kana_does_not_block_a_new_course(stores, mode):
    store, learning = stores
    sid, fresh = both_fresh(store, learning, last_lane='course')
    if mode == 'paused':
        store.set_content_control(fresh.content_id, suspended=True, now=NOW)
    elif mode == 'future':
        fresh.metadata['not_before_utc'] = (NOW+timedelta(days=1)).isoformat()
        sid = install_queue(store, [kana('old-waiting', 'あ', 'a'), fresh])
    head = normal(store, sid)
    before = rows(store)
    selected = pick(store, learning, head, kinds=[] if mode == 'closed' else ['kana_choice'])
    assert selected[0] == 'course' and selected[1]['content']['id'] == 'fresh-course'
    assert rows(store) == before
