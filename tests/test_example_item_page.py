"""Item-specific paging ranks live progress before detaching visible examples."""
from copy import deepcopy

import pytest

from kanamemory.example_store import ExampleStore, empty_pack, example_id_for
from support.example_store_support import reference, snapshot, targets
from support.learning_store_fixtures import stores, question, ready, START


def page_fixture(stores):
    store, learning = stores
    items = targets()
    value = empty_pack()
    for index in range(21):
        text = f'私は学生です。{index:02}'
        example = {'id': example_id_for(text), 'revision': '1', 'text': text,
                   'translation': f'我是学生。{index:02}', 'source_ids': []}
        value['examples'].append(example)
        value['references'].append(reference(example, 'grammar:topic', 'は'))
    # Two explanations for one target/example are alternatives, not two rows.
    value['references'].append(reference(value['examples'][0], 'grammar:topic', '私'))
    items[0]['instruction']['example'] = value['examples'][17]['text']
    return ExampleStore(store, learning, items, value), value


def test_page_keeps_total_order_alternatives_and_copies_only_requested_rows(stores, monkeypatch):
    service, value = page_fixture(stores)
    expected = service.for_item('grammar:topic')
    assert expected[0]['id'] == value['examples'][17]['id']
    assert len(expected) == len({row['id'] for row in expected}) == 21
    before = snapshot(stores[0])
    copied = []
    original = service._get

    def track(identity):
        copied.append(identity)
        return original(identity)

    monkeypatch.setattr(service, '_get', track)
    page = service.for_item_page('grammar:topic', offset=4, limit=8)
    assert page == {'total': 21, 'examples': expected[4:12]}
    assert copied == [row['id'] for row in expected[4:12]]
    copied.clear()
    assert service.for_item_page('grammar:topic', limit=0) == {'total': 21, 'examples': []}
    assert service.for_item_page('grammar:topic', offset=100)['examples'] == []
    assert copied == []
    assert service.for_item_page('grammar:missing')['total'] == 0
    assert snapshot(stores[0]) == before

    first = service.for_item_page('grammar:topic', limit=None)
    assert first == {'total': 21, 'examples': expected}
    shared = next(row for row in first['examples'] if row['id'] == value['examples'][0]['id'])
    assert len(shared['reference_options']) == 2
    shared['reference_options'][0]['spans'].clear()
    shared['reference']['hint'].clear()
    shared['source_ids'].append('mutated')
    assert service.for_item_page('grammar:topic', limit=None)['examples'] == expected


def test_item_page_reranks_after_real_context_review_without_writes(stores):
    store, learning = stores
    service, value = page_fixture(stores)
    chosen = value['examples'][20]
    value = deepcopy(value)
    value['references'].append(reference(chosen, 'grammar:polite', 'です'))
    service = ExampleStore(store, learning, list(service.items.values()), value)
    before = service.for_item_page('grammar:topic', limit=2)
    assert chosen['id'] not in {row['id'] for row in before['examples']}
    learning.enroll(targets()[1], now=START)
    learning.append_questions(question(cid='grammar:polite'), now=START)
    when = ready(learning, 'grammar:polite')
    learning.submit('q1', '正在读书', True, now=when)
    learning.finalize('q1', 3, now=when)
    saved = snapshot(store)
    after = service.for_item_page('grammar:topic', limit=2)
    assert after['total'] == before['total']
    assert after['examples'][0]['id'] == chosen['id']
    assert after['examples'][0]['score'] > 0
    assert snapshot(store) == saved


@pytest.mark.parametrize('kwargs', [{'offset': -1}, {'offset': True}, {'limit': -1}, {'limit': False}])
def test_item_page_rejects_invalid_range(stores, kwargs):
    service, _ = page_fixture(stores)
    with pytest.raises(ValueError):
        service.for_item_page('grammar:topic', **kwargs)
