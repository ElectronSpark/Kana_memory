"""One durable explicit start; production enrollment, queues and scoring."""
from copy import deepcopy
from datetime import timedelta
import pytest

from support.study_path_service_support import setup_path
from support.study_path_service_support import BASE
from support.study_path_service_support import MORE
from support.study_path_service_support import NOW
from support.study_path_service_support import lesson
from support.first_teaching_dispatch_core_support import kana
from support.first_teaching_dispatch_core_support import normal
from support.first_teaching_dispatch_core_support import pick
from support.first_teaching_dispatch_core_support import rows
from support.learning_store_fixtures import question
from kanamemory.app_clock import FrozenClock, using_clock
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.explicit_learning_start import SETTING, active, read


from support.explicit_learning_start_support import clock, append, backlog, finish_first


def test_explicit_add_starts_one_eligible_block_before_old_ordinary_due(setup_path):
    _, store, learning, _, make, _ = setup_path
    sid = backlog(store)
    make().add_entries([BASE, MORE], start_learning=True, action_id='explicit-test-first')
    append(learning, BASE); append(learning, MORE)
    head = normal(store, sid, NOW)
    before = rows(store)
    selected = pick(store, learning, head, now=NOW)
    assert selected[0] == 'course' and selected[1]['content']['id'] == BASE
    assert selected[1]['type'] == 'instruction'
    assert selected[1]['explicit_learning_start'] == 'explicit-test-first'
    assert rows(store) == before


def test_plain_add_and_repeated_already_added_selection_do_not_grant_priority(setup_path):
    _, store, learning, _, make, _ = setup_path
    sid = backlog(store)
    service = make()
    service.add_entries(BASE)
    append(learning, BASE)
    assert read(store) is None
    assert service.add_entries(BASE, start_learning=True, action_id='duplicate')['total_count'] == 0
    service.add_sections(['section:base'], start_learning=True, action_id='same-section')
    assert read(store) is None
    assert pick(store, learning, normal(store, sid, NOW), now=NOW) is None


def test_existing_unopened_parent_is_eligible_but_no_extra_block_for_child(setup_path, clock):
    _, store, learning, _, make, _ = setup_path
    sid = backlog(store)
    service = make()
    service.add_entries(BASE)
    receipt = service.add_entries(MORE, start_learning=True, action_id='child-start')
    assert receipt['added_learning'] == [MORE]
    append(learning, BASE); append(learning, MORE)
    assert pick(store, learning, normal(store, sid, NOW), now=NOW)[1]['content']['id'] == BASE
    finish_first(learning, BASE, clock)
    assert active(store) is None
    assert read(store)['selected_entry_id'] == BASE
    assert len(read(store)['entries']) == 1
    assert pick(store, learning, normal(store, sid, clock.now), now=clock.now) is None
    assert learning.get_item(MORE)['stage'] == 'intro'
    service.add_entries([MORE, BASE], start_learning=True, action_id='same-again')
    assert active(store) is None


def test_continuous_multi_section_additions_keep_one_outstanding_request(setup_path):
    _, store, _, _, make, _ = setup_path
    service = make()
    service.add_sections(['section:more'], start_learning=True, action_id='one-block')
    state = read(store)
    service.add_sections(['section:h', 'section:k'], start_learning=True, action_id='second-add')
    assert read(store) == state
    assert len(store.get_setting('study_path_v1')['reserved_entries']) == 5


def test_disabled_kinds_preserve_intent_for_reenable_without_reenrollment(setup_path):
    _, store, learning, _, make, _ = setup_path
    sid = backlog(store)
    make().add_entries(BASE, start_learning=True, action_id='temporarily-disabled')
    append(learning, BASE)
    head = normal(store, sid, NOW)
    before = rows(store)
    assert pick(store, learning, head, enabled=[], now=NOW) is None
    assert rows(store) == before and active(store)
    assert pick(store, learning, head, now=NOW)[1]['content']['id'] == BASE


def test_backup_restores_pending_and_consumed_intent_without_extra_priority(setup_path, tmp_path, clock):
    _, store, learning, _, make, _ = setup_path
    sid = backlog(store)
    make().add_entries([BASE, MORE], start_learning=True, action_id='roundtrip')
    append(learning, BASE); append(learning, MORE)
    for completed in (False, True):
        if completed:
            finish_first(learning, BASE, clock)
        raw = store.export_backup()
        before = read(store)
        with StudyStore(tmp_path/('restored-'+str(completed)), time_zone='UTC') as restored:
            restored.import_backup(raw)
            target = LearningStore(restored)
            assert read(restored) == before
            head = normal(restored, sid, clock.now)
            selected = pick(restored, target, head, now=clock.now)
            if completed:
                assert active(restored) is None and selected is None
            else:
                assert active(restored) and selected[1]['content']['id'] == BASE


def test_reset_removes_intent_and_invalid_import_is_rejected_before_live_writes(setup_path, tmp_path):
    _, store, _, _, make, _ = setup_path
    make().add_entries(BASE, start_learning=True, action_id='reset')
    good = deepcopy(read(store))
    store.clear_learning_data()
    assert read(store) is None
    bad = {**good, 'created_at_utc': 42}
    store.set_setting(SETTING, bad)
    data = store.export_backup()
    with StudyStore(tmp_path/'reject', time_zone='UTC') as target:
        LearningStore(target)
        before = rows(target)
        with pytest.raises(ValueError):
            target.import_backup(data)
        assert rows(target) == before


def test_invalid_action_id_rolls_back_the_complete_enrollment(setup_path):
    _, store, _, _, make, _ = setup_path
    service = make()
    before = rows(store)
    with pytest.raises(ValueError):
        service.add_entries(BASE, start_learning=True, action_id=42)
    assert rows(store) == before


def test_one_thousand_actual_new_items_still_grant_only_one_block(setup_path, clock):
    _, store, learning, _, make, _ = setup_path
    sid = backlog(store)
    service = make()
    section = service.by_id['section:base']
    for n in range(999):
        cid = 'grammar:explicit:bulk-'+str(n)
        entry = {'id': cid, 'content_id': cid, 'kind': 'learning'}
        section['entries'].append(entry)
        service.items[cid] = lesson(cid)
        service.entries[cid] = entry
        service.learning_entries[cid] = cid
        service.entry_sections[cid] = section['id']
    receipt = service.add_sections([section['id']], start_learning=True, action_id='one-thousand')
    assert len(receipt['added_learning']) == len(read(store)['entries']) == 1000
    append(learning, BASE)
    assert pick(store, learning, normal(store, sid, NOW), now=NOW)[1]['content']['id'] == BASE
    finish_first(learning, BASE, clock)
    assert active(store) is None and len(read(store)['entries']) == 1
    assert store._db.execute("SELECT COUNT(*) FROM learning_content WHERE stage='intro'").fetchone()[0] == 999
    assert pick(store, learning, normal(store, sid, clock.now), now=clock.now) is None


def test_kana_claim_and_consumption_are_specific_to_the_selected_script(setup_path, clock):
    _, store, learning, _, make, _ = setup_path
    service = make()
    service.add_sections(['section:k'], start_learning=True, action_id='two-scripts')
    queued = service.ensure_kana_queue(['kana_choice'], now=NOW)
    sid = queued['session_id']
    selected = pick(store, learning, normal(store, sid, NOW), now=NOW)
    assert selected[0] == 'kana'
    q = selected[1]['question']
    pending = store.prepare_next_question(sid, now=NOW, fair_new=True, teaching_only=True,
        teaching_kinds=['kana_choice'], teaching_targets=selected[1]['teaching_targets'])
    from kanamemory.kana_learning_intro import select_initial_familiarity
    select_initial_familiarity(store, sid, pending['question_index'], q, 'familiar', now=NOW, fair_new=True)
    assert read(store)['entries'][0]['script'] == q.script
    other = 'katakana' if q.script == 'hiragana' else 'hiragana'
    store.record_review(q.content_id, q.kind, other, 3, now=NOW)
    assert active(store)
    clock.advance(61)
    store.record_review(q.content_id, q.kind, q.script, 3, now=clock.now)
    assert active(store) is None


def test_reference_and_retired_entries_do_not_create_a_start_right(setup_path):
    _, store, _, _, make, _ = setup_path
    service = make()
    service.add_entries('ref:old-kana', start_learning=True)
    assert read(store) is None
    eid = service.by_id['section:h']['entries'][0]['id']
    service.entries[eid]['practice_retired'] = True
    service.add_entries(eid, start_learning=True)
    assert read(store) is None


def independent_courses(service):
    service.items[BASE].update(family='word', abilities=['understanding'], reading='てがみ',
        question_templates={'vocab_meaning': {'prompt': '手紙'}})
    service.items[MORE].update(depends_on=[], question_templates={'grammar_meaning': {'prompt': 'です'}})


@pytest.mark.parametrize('disabled', ['closed_kind', 'suspended'])
@pytest.mark.parametrize('claimed', [False, True])
def test_new_explicit_action_replaces_only_disabled_course_without_changing_it(setup_path, clock, disabled, claimed):
    _, store, learning, _, make, _ = setup_path
    sid = backlog(store)
    service = make()
    independent_courses(service)
    service.add_entries(BASE, start_learning=True, action_id='old')
    learning.append_questions({**question(BASE, qid='old-word'), 'kind': 'vocab_meaning',
                               'revision': learning.get_item(BASE)['revision']}, now=NOW)
    if claimed:
        learning.set_initial_familiarity(BASE, {'understanding': 'new'}, now=NOW)
        learning.mark_instruction_exposed(BASE, now=NOW)
        assert read(store)['selected_entry_id'] == BASE
    if disabled == 'closed_kind':
        store.set_setting('learning_preferences_v1', {'enabled': ['grammar_meaning']})
    else:
        learning.set_suspended(BASE, 'understanding', True)
    old_content = deepcopy(learning.get_item(BASE))
    old_question = deepcopy(learning.get_question('old-word'))
    old_unit = deepcopy(learning.get_unit(BASE, 'understanding'))
    service.add_entries(MORE, start_learning=True, action_id='new')
    append(learning, MORE)
    assert read(store)['action_id'] == 'new'
    assert learning.get_item(BASE) == old_content
    assert learning.get_question('old-word') == old_question
    assert learning.get_unit(BASE, 'understanding') == old_unit
    assert pick(store, learning, normal(store, sid, NOW), now=NOW)[1]['content']['id'] == MORE
    finish_first(learning, MORE, clock)
    if disabled == 'closed_kind':
        store.set_setting('learning_preferences_v1', {'enabled': ['grammar_meaning', 'vocab_meaning']})
    else:
        learning.set_suspended(BASE, 'understanding', False)
    assert active(store) is None
    service.add_entries(BASE, start_learning=True, action_id='old')
    assert read(store)['action_id'] == 'new' and active(store) is None
    assert pick(store, learning, normal(store, sid, clock.now), now=clock.now,
                enabled=['grammar_meaning', 'vocab_meaning']) is None


def test_partial_closure_or_prerequisite_wait_does_not_replace_outstanding_start(setup_path):
    _, store, learning, _, make, _ = setup_path
    service = make()
    service.add_entries(MORE, start_learning=True, action_id='blocked')
    append(learning, BASE); append(learning, MORE)
    # Claim a child without bypassing its prerequisite: a dispatch probe is
    # empty, but that is not evidence of a disabled course.
    from kanamemory.explicit_learning_start import arm, claim, candidates
    with store._db:
        store._db.execute('BEGIN')
        claim(store._db, MORE, introduction_id=learning.get_item(MORE)['introduction_id'], now=NOW)
    before = read(store)
    assert learning.current(['grammar_meaning'], now=NOW, fair_new=True, teaching_only=True,
                            teaching_content_ids={MORE}) is None
    learning.set_suspended(MORE, 'application', True)
    with store._db:
        store._db.execute('BEGIN')
        assert arm(store._db, candidates(service, [BASE]), action_id='another', now=NOW, service=service)
    after = read(store)
    assert {**{k: v for k, v in after.items() if k != 'pending_start'}, 'version': 1} == before
    assert after['pending_start']['action_id'] == 'another'
    assert active(store)['selected_entry_id'] == MORE


def test_new_closed_target_does_not_discard_old_pending_start(setup_path):
    _, store, _, _, make, _ = setup_path
    service = make()
    independent_courses(service)
    service.add_entries(BASE, start_learning=True, action_id='keep-old')
    before = read(store)
    store.set_setting('learning_preferences_v1', {'enabled': []})
    service.add_entries(MORE, start_learning=True, action_id='both-closed')
    assert read(store) == before


@pytest.mark.parametrize('preferences', [None, [], {'enabled': None}, {'enabled': 'grammar_meaning'},
                                         {'enabled': ['unknown_kind']}, {'enabled': [42]}])
def test_unknown_preferences_do_not_authorize_replacing_an_outstanding_start(setup_path, preferences):
    _, store, learning, _, make, _ = setup_path
    service = make()
    independent_courses(service)
    service.add_entries(BASE, start_learning=True, action_id='keep-old')
    learning.set_suspended(BASE, 'understanding', True)
    before = read(store)
    store.set_setting('learning_preferences_v1', preferences)
    service.add_entries(MORE, start_learning=True, action_id='unknown-setting')
    assert read(store) == before
