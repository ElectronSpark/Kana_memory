"""Round order freezes decisions, never authorizes or scores their entries."""
from copy import deepcopy
from types import SimpleNamespace
import sqlite3

from kanamemory import practice_round as rounds


def started():
    store = SimpleNamespace()
    rounds.enable(store)
    rounds.begin(store)
    return store


def test_only_explicit_root_enable_and_begin_create_a_round():
    root, worker = SimpleNamespace(), SimpleNamespace()
    assert rounds.begin(root) is None
    rounds.enable(root)
    assert not rounds.active(root) and not hasattr(root, '_practice_run_plan')
    plan = rounds.begin(root)
    assert rounds.collecting(root) and rounds.active(root)
    assert rounds.begin(root) is plan
    assert rounds.begin(root, restart_finished=True) is plan
    assert not rounds.active(worker) and rounds.begin(worker) is None


def test_home_resume_seal_finish_and_explicit_next_round():
    store = started()
    first = rounds.begin(store)
    rounds.frozen_priority(store, 'course', 'A', (2, 1))
    rounds.seal(store)
    assert rounds.sealed(store) and not rounds.collecting(store)
    assert rounds.begin(store) is first  # Navigation does not reset the plan.
    rounds.mark_finished(store)
    assert not rounds.active(store) and rounds.sealed(store)
    assert rounds.begin(store) is first
    second = rounds.begin(store, restart_finished=True)
    assert second is not first and second.run_id != first.run_id
    assert rounds.collecting(store) and not rounds.sealed(store)
    assert second.priorities == {} and second.course_order is None


def test_priorities_freeze_semantic_keys_across_lane_and_queue_reindexing():
    store = started()
    key = ('kana:ki', 'reading', 'hiragana')
    original = [4, 2]
    assert rounds.frozen_priority(store, 'native', key, original) == [4, 2]
    original[0] = 0
    output = rounds.frozen_priority(store, 'native', key, [0, 9])
    output[1] = 99
    assert rounds.frozen_priority(store, 'native', key, [0, 9]) == [4, 2]
    assert rounds.frozen_priority(store, 'course', key, (1, 0)) == (1, 0)
    rounds.seal(store)
    assert rounds.frozen_priority(store, 'native', ('new',), (0, 0)) is None
    assert rounds.frozen_priority(store, 'native', key, (0, 0)) == [4, 2]


def test_course_order_captures_once_filters_and_retains_missing_places():
    store = started()
    rows = [{'question_id': 'B', 'priority': (2, 0)},
            {'question_id': 'A', 'priority': (1, 0)},
            {'question_id': 'C', 'priority': (2, 0)}]
    before = deepcopy(rows)
    priority = lambda row: row['priority']
    assert [r['question_id'] for r in rounds.freeze_course_rows(store, rows, priority)] == ['A', 'B', 'C']
    assert rows == before
    changed = [{'question_id': 'C', 'priority': (0, 0)},
               {'question_id': 'new', 'priority': (-1, 0)}, rows[1]]
    assert [r['question_id'] for r in rounds.freeze_course_rows(store, changed, priority)] == ['A', 'C']
    rounds.seal(store)
    assert [r['question_id'] for r in rounds.freeze_course_rows(store, rows[::-1], priority)] == ['A', 'B', 'C']


def test_no_late_course_capture_after_seal_and_no_implicit_empty_reset():
    store = started()
    rounds.seal(store)
    assert rounds.freeze_course_rows(store, [{'id': 'late'}], lambda r: 0) == []
    other = started()
    assert rounds.freeze_course_rows(other, [], lambda r: 0) == []
    assert rounds.freeze_course_rows(other, [{'id': 'late'}], lambda r: 0) == []


def test_sqlite_row_protocol_and_legacy_paths_need_no_round():
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT 'B' AS question_id, 2 AS priority UNION ALL SELECT 'A',1").fetchall()
        store = started()
        assert [r['question_id'] for r in rounds.freeze_course_rows(store, rows, lambda r: r['priority'])] == ['A', 'B']
    disabled = SimpleNamespace()
    assert rounds.frozen_priority(disabled, 'native', 'A', (3, 0)) == (3, 0)
    assert rounds.freeze_course_rows(disabled, [{'id': 'B'}, {'id': 'A'}], lambda r: r['id']) == [{'id': 'A'}, {'id': 'B'}]
    assert not hasattr(disabled, '_practice_run_plan')


def test_retired_ids_get_idempotent_new_identity_without_progress_writes(monkeypatch):
    retired = {'old'}
    reads = []
    def archived(store, identity):
        reads.append(identity)
        return {'id': identity} if identity in retired else None
    monkeypatch.setattr(rounds, '_archived_question', archived)
    store = started()
    store.attempt_count = 7
    assert rounds.next_question_id(store, 'untouched') == 'untouched'
    replacement = rounds.next_question_id(store, 'old')
    assert replacement.startswith('old:round:')
    assert rounds.next_question_id(store, 'old') == replacement
    retired.add(replacement)
    second = rounds.next_question_id(store, 'old')
    assert second != replacement
    rounds.mark_finished(store)
    rounds.begin(store, restart_finished=True)
    assert rounds.next_question_id(store, 'old') not in {replacement, second}
    assert store.attempt_count == 7 and 'untouched' in reads


def native(content, **overrides):
    return SimpleNamespace(content_id=content, kind='kana_choice', script='hiragana',
        prompt=content, answer=content, options=['あ', 'い'], audio_text='',
        accepted_answers=(), metadata=overrides)


def test_native_key_ignores_runtime_metadata_but_binds_real_question_fields():
    first, second = native('A', presented=True), native('A', not_before_utc='later')
    assert rounds.native_key(first) == rounds.native_key(second)
    assert hash(rounds.native_key(first)) == hash(rounds.native_key(second))
    second.answer = 'different'
    assert rounds.native_key(first) != rounds.native_key(second)
    assert rounds.native_key(vars(first)) == rounds.native_key(first)


def test_native_order_survives_reindexing_filters_unknown_and_keeps_duplicates():
    store = started()
    a, b, other_a = native('A'), native('B'), native('A', parked=True)
    original = [(7, a), (3, b), (9, other_a)]
    assert rounds.freeze_native_order(store, 'session', original) == original
    rounds.seal(store)
    # Only session indices change; questions and original order do not.
    rounds.remap_native_order(store, 'session', {7: 1, 3: 0, 9: 2})
    changed = [(0, b), (1, a), (2, other_a), (3, native('new')), (4, native('A'))]
    ordered = rounds.freeze_native_order(store, 'session', changed)
    assert [index for index, _ in ordered] == [1, 0, 2]
    assert original == [(7, a), (3, b), (9, other_a)]
    assert rounds.freeze_native_order(store, 'session', [(0, b)]) == [(0, b)]
    assert rounds.freeze_native_order(store, 'session', [(10, b)]) == []
    assert rounds.freeze_native_order(store, 'late-session', [(0, a)]) == []


def test_native_priority_is_session_scoped_and_unknown_after_seal_is_filtered():
    store = started()
    q = native('A')
    assert rounds.native_priority(store, 'one', q, (4, 0)) == (4, 0)
    assert rounds.native_priority(store, 'two', q, (2, 0)) == (2, 0)
    rounds.seal(store)
    assert rounds.native_priority(store, 'one', q, (0, 0)) == (4, 0)
    assert rounds.native_priority(store, 'one', native('new'), (0, 0)) is None
    assert rounds.freeze_native_order(SimpleNamespace(), 'unused', [(4, q)]) == [(4, q)]


def test_consumed_duplicate_keeps_later_occurrence_after_intervening_question():
    store = started()
    a, b = native('A'), native('B')
    rounds.freeze_native_order(store, 's', [(0, a), (1, b), (2, a)])
    rounds.seal(store)
    # Index zero was answered; the second A must not inherit its rank zero.
    assert rounds.freeze_native_order(store, 's', [(1, b), (2, a)]) == [(1, b), (2, a)]
    # Ready and future filters independently preserve the identity of each A.
    assert rounds.freeze_native_order(store, 's', [(2, a)]) == [(2, a)]
    assert rounds.freeze_native_order(store, 's', [(0, a)]) == [(0, a)]
    assert rounds.freeze_native_order(store, 's', [(2, a), (0, a), (1, b)]) == [(0, a), (1, b), (2, a)]


def test_committed_remap_preserves_prefix_and_does_not_admit_changed_question():
    store = started()
    a, b, c = native('A'), native('B'), native('C')
    rounds.freeze_native_order(store, 's', [(0, a), (1, b), (2, c)])
    rounds.seal(store)
    before = deepcopy(store._practice_run_plan.native_positions)
    # A preview can filter rows, but cannot alter their committed identity map.
    rounds.freeze_native_order(store, 's', [(2, c), (1, b)])
    assert store._practice_run_plan.native_positions == before
    rounds.remap_native_order(store, 's', {1: 2, 2: 1})
    assert rounds.freeze_native_order(store, 's', [(0, a), (1, c), (2, b)]) == [(0, a), (2, b), (1, c)]
    assert rounds.freeze_native_order(store, 's', [(1, b)]) == []
    assert rounds.freeze_native_order(store, 's', [(2, b), (2, b)]) == [(2, b)]


def test_invalid_remap_is_atomic_and_other_connections_have_no_plan():
    import pytest
    store = started()
    rounds.freeze_native_order(store, 's', [(0, native('A')), (1, native('B'))])
    before = deepcopy(store._practice_run_plan.native_positions)
    for mapping in ({1: 0}, {True: 2}, {0: -1}, {0: '2'}):
        with pytest.raises(ValueError):
            rounds.remap_native_order(store, 's', mapping)
        assert store._practice_run_plan.native_positions == before
    other_connection = SimpleNamespace()
    rounds.remap_native_order(other_connection, 's', {0: 1})
    assert not hasattr(other_connection, '_practice_run_plan')


def test_main_word_main_switch_preserves_independent_round_identity():
    store = started()
    main = rounds.begin(store)
    rounds.freeze_course_rows(store, [{'id': 'main-question'}], lambda r: 0)
    rounds.seal(store)
    store.practice_lane = 'word'
    assert not rounds.active(store) and not rounds.sealed(store)
    assert store._practice_run_plan is None
    word = rounds.begin(store)
    assert word is not main and rounds.collecting(store)
    rounds.freeze_native_order(store, 'word-session', [(0, native('word'))])
    rounds.seal(store)
    store.practice_lane = 'main'
    assert rounds.begin(store) is main and rounds.sealed(store)
    assert store._practice_run_plan is main
    assert main.course_order == ('main-question',) and main.native_orders == {}
    store.practice_lane = 'word'
    assert rounds.begin(store) is word and rounds.sealed(store)


def test_explicit_main_restart_does_not_switch_or_reset_active_word_lane():
    store = started()
    prior_main = rounds.begin(store)
    rounds.seal(store)
    store.practice_lane = 'word'
    word = rounds.begin(store)
    rounds.seal(store)
    replacement_main = rounds.restart(store, lane='main')
    assert replacement_main is not prior_main and prior_main.finished
    assert store.practice_lane == 'word' and store._practice_run_plan is word
    assert rounds.sealed(store) and not word.finished
    store.practice_lane = 'main'
    assert rounds.begin(store) is replacement_main and rounds.collecting(store)


def test_retired_course_id_cache_uses_main_plan_even_from_word_lane(monkeypatch):
    monkeypatch.setattr(rounds, '_archived_question', lambda store, identity: identity == 'old')
    store = started()
    main = rounds.begin(store)
    first = rounds.next_question_id(store, 'old')
    store.practice_lane = 'word'
    word = rounds.begin(store)
    assert rounds.next_question_id(store, 'old') == first
    assert main.question_ids == {'old': first} and word.question_ids == {}
    assert store._practice_run_plan is word


def test_restore_connection_carries_full_lane_map_without_merging_rounds():
    store = started()
    main = rounds.begin(store)
    rounds.seal(store)
    store.practice_lane = 'word'
    word = rounds.begin(store)
    rounds.mark_finished(store)
    reopened = SimpleNamespace(practice_lane='main')
    rounds.restore_connection(store, reopened)
    assert rounds.begin(reopened) is main and rounds.sealed(reopened)
    reopened.practice_lane = 'word'
    assert rounds.begin(reopened) is word and rounds.finished(reopened)


def test_restore_import_discards_runtime_plans_and_worker_stays_unenabled(monkeypatch):
    from kanamemory import practice_archive
    calls = []
    monkeypatch.setattr(practice_archive, 'archive_previous_rounds', lambda store: calls.append(store))
    store = started()
    rounds.seal(store)
    reopened = SimpleNamespace(_practice_run_plans={'main': store._practice_run_plan})
    rounds.restore_connection(store, reopened, imported=True)
    assert calls == [reopened] and not rounds.active(reopened)
    assert rounds.collecting(reopened) is False and reopened._practice_run_plans == {}
    assert rounds.begin(reopened) is not store._practice_run_plan
    worker = SimpleNamespace()
    rounds.restore_connection(SimpleNamespace(), worker)
    assert not hasattr(worker, '_practice_run_plan')


def test_initial_course_protection_promotes_only_existing_head_before_seal():
    store = started()
    rows = [{'id': 'review'}, {'id': 'teaching'}, {'id': 'tail'}]
    rounds.freeze_course_rows(store, rows, lambda row: 0)
    rounds.frozen_priority(store, 'cross-course', 'teaching', (5, 0))
    assert not rounds.promote_initial(store, course_id='unknown')
    assert rounds.promote_initial(store, course_id='teaching')
    assert [r['id'] for r in rounds.freeze_course_rows(store, rows, lambda row: 0)] == ['teaching', 'review', 'tail']
    assert rounds.frozen_priority(store, 'cross-course', 'teaching', (5, 0)) == (1, 0)
    rounds.seal(store)
    before = deepcopy(store._practice_run_plan)
    assert not rounds.promote_initial(store, course_id='tail')
    assert store._practice_run_plan == before


def test_initial_native_protection_uses_earliest_semantic_instance_and_keeps_tail():
    store = started()
    a, b = native('A'), native('B')
    rows = [(4, b), (8, a), (2, a)]
    rounds.freeze_native_order(store, 's', rows)
    rounds.frozen_priority(store, 'cross-kana', ('s', rounds.native_key(a)), (5, 0))
    assert rounds.promote_initial(store, session_id='s', question=native('A', preview_index=99))
    assert store._practice_run_plan.native_positions['s'][8][1] == -1
    assert store._practice_run_plan.native_positions['s'][4][1] == 0
    assert store._practice_run_plan.native_positions['s'][2][1] == 2
    assert rounds.freeze_native_order(store, 's', rows) == [(8, a), (4, b), (2, a)]
    assert rounds.frozen_priority(store, 'cross-kana', ('s', rounds.native_key(a)), (5, 0)) == (1, 0)
    rounds.seal(store)
    before = deepcopy(store._practice_run_plan)
    assert not rounds.promote_initial(store, session_id='s', question=b)
    assert store._practice_run_plan == before


def test_long_retired_question_id_uses_bounded_hash_base_without_changing_normal_ids(monkeypatch):
    long_id = '課' * 1024
    monkeypatch.setattr(rounds, '_archived_question', lambda store, identity: identity == long_id)
    store = started()
    replacement = rounds.next_question_id(store, long_id)
    assert len(replacement) <= 1024 and replacement.startswith('practice:')
    assert rounds.next_question_id(store, long_id) == replacement
    assert rounds.next_question_id(store, 'unchanged') == 'unchanged'


def test_home_plan_count_policy_tracks_main_even_when_word_lane_is_selected():
    store = started()
    assert not rounds.planned_round_running(store)
    rounds.seal(store)
    store.practice_lane = 'word_romaji'
    assert rounds.planned_round_running(store)
    assert not rounds.planned_round_running(store, lane='word_romaji')
    store.practice_lane = 'main'
    rounds.mark_finished(store)
    assert not rounds.planned_round_running(store)
