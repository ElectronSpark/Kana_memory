"""Bounded display copies without caching learner ranking or pending writes."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest


from kanamemory.example_store import ExampleStore, SETTING, empty_pack, example_id_for
from kanamemory.example_ui import ExampleUIMixin
from support.example_store_support import pack
from support.example_store_support import reference
from support.example_store_support import targets
from support.example_store_support import snapshot
from support.learning_store_fixtures import stores, question, ready, START


def many_examples():
    value = empty_pack()
    for index in range(31):
        example = {'id': example_id_for(f'私は学生です。{index}'), 'revision': '1',
                   'text': f'私は学生です。{index}', 'translation': f'我是学生。{index}', 'source_ids': []}
        value['examples'].append(example)
        value['references'].append(reference(example, 'grammar:topic', 'は'))
    return value


def test_page_materializes_only_visible_rows_and_keeps_detached_shared_refs(stores, monkeypatch):
    store, learning = stores
    value = many_examples()
    value['references'].append(reference(value['examples'][0], 'grammar:polite', 'は'))
    resources = ExampleStore(store, learning, targets(), value)
    expected = resources.all_examples()
    before = snapshot(store)
    original_get = resources._get
    calls = []
    def tracked(identity):
        calls.append(identity)
        return original_get(identity)
    monkeypatch.setattr(resources, '_get', tracked)
    page = resources.examples_page(offset=10, limit=10)
    assert page == {'total': 31, 'examples': expected[10:20]}
    assert calls == [row['id'] for row in expected[10:20]]
    page['examples'][0]['reference']['spans'].clear()
    assert resources.examples_page(offset=10, limit=10)['examples'] == expected[10:20]
    shared = value['examples'][0]['id']
    assert len(resources.overlapping_references(shared, 1, 2)) == 2
    assert snapshot(store) == before
    assert resources.examples_page('不存在')['total'] == 0
    assert resources.examples_page(offset=100)['examples'] == []


def test_page_reorders_immediately_after_actual_review(stores):
    store, learning = stores
    value = many_examples()
    # Pick a row below the first page in the initial equal-score ordering.
    chosen = sorted(value['examples'], key=lambda row: row['id'])[-1]
    value['references'].append(reference(chosen, 'grammar:polite', 'です'))
    resources = ExampleStore(store, learning, targets(), value)
    before = resources.examples_page()
    assert chosen['id'] not in {row['id'] for row in before['examples']}
    learning.enroll(targets()[1], now=START)
    learning.append_questions(question(cid='grammar:polite'), now=START)
    when = ready(learning, 'grammar:polite')
    learning.submit('q1', '正在读书', True, now=when)
    learning.finalize('q1', 3, now=when)
    page = resources.examples_page()
    assert page['total'] == before['total']
    assert page['examples'][0]['id'] == chosen['id']
    assert page['examples'][0]['score'] > 0


class Harness(ExampleUIMixin):
    def example_targets_available(self, **kwargs):
        self.catalogue_reads += 1
        result = {item['id']: item for item in targets()}
        for item in self.learning_store.list_items():
            result.setdefault(item['id'], item)
        return list(result.values())


def harness(stores, monkeypatch):
    from kanamemory import example_catalogue
    monkeypatch.setattr(example_catalogue, 'bundled_example_pack', lambda items: empty_pack())
    app = Harness()
    app.store, app.learning_store = stores
    app.repo = SimpleNamespace(lexical_review=None)
    app.catalogue_reads = 0
    return app


def test_accessor_reuses_catalogue_and_observes_external_and_own_writes(stores, monkeypatch):
    app = harness(stores, monkeypatch)
    resources = app.example_store()
    assert app.example_store() is resources
    assert app.catalogue_reads == 1
    path = app.store._db.execute('PRAGMA database_list').fetchone()[2]
    # A restore/import may commit from a different connection: total_changes on
    # the original connection alone would not notice it.
    with closing(sqlite3.connect(path)) as other:
        with other:
            other.execute('INSERT INTO app_setting VALUES (?, ?)', (SETTING, json.dumps(pack())))
    fresh = app.example_store()
    assert fresh.examples_page()['total'] == 1
    assert app.catalogue_reads == 2
    app.store.set_setting(SETTING, empty_pack())
    assert app.example_store().examples_page()['total'] == 0
    new = {**targets()[0], 'id': 'grammar:added', 'revision': 'v2'}
    app.learning_store.enroll(new, now=START)
    assert new['id'] in app.example_store().items


def test_transaction_reads_and_rollback_do_not_publish_stale_resources(stores, monkeypatch):
    app = harness(stores, monkeypatch)
    old = app.example_store()
    before = snapshot(app.store)
    db = app.store._db
    db.execute('BEGIN')
    db.execute('INSERT INTO app_setting VALUES (?, ?)', (SETTING, json.dumps(pack())))
    temporary = app.example_store()
    assert temporary is not old
    assert temporary.examples_page()['total'] == 1
    # Also exercise an already-held service during the transaction.
    assert old.examples_page()['total'] == 1
    assert old._token is None
    db.rollback()
    assert app.example_store() is old
    assert old.examples_page() == {'total': 0, 'examples': []}
    assert temporary.examples_page() == {'total': 0, 'examples': []}
    assert snapshot(app.store) == before


@pytest.mark.parametrize('kwargs', [{'offset': -1}, {'limit': True}, {'limit': -1}])
def test_invalid_page_bounds(stores, kwargs):
    resources = ExampleStore(*stores, targets(), empty_pack())
    with pytest.raises(ValueError):
        resources.examples_page(**kwargs)
