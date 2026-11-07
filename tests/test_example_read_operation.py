"""Render-scoped progress reuse must retain live state and ownership boundaries."""
from contextlib import closing
from copy import deepcopy
import sqlite3

import pytest

from kanamemory.example_store import ExampleStore
from support.example_store_support import pack, snapshot, targets
from support.learning_store_fixtures import stores, START


@pytest.fixture
def progress(stores, monkeypatch):
    store, learning = stores
    learning.enroll(targets()[0], now=START)
    service = ExampleStore(store, learning, targets(), pack())
    calls = []
    original = service._statuses_uncached

    def read(identities):
        calls.append(set(identities))
        return original(identities)

    monkeypatch.setattr(service, '_statuses_uncached', read)
    return store, service, calls


def test_ranking_and_links_reuse_progress_without_writes_or_shared_results(progress):
    store, service, calls = progress
    before = snapshot(store)
    expected = service.for_item('grammar:topic')
    calls.clear()
    with service.read_operation():
        assert service.for_item('grammar:topic') == expected
        eid = expected[0]['id']
        refs = service.references(eid)
        untouched = deepcopy(refs)
        refs[0]['status']['abilities'].clear()
        refs[0]['status']['is_added'] = 'mutated'
        assert service.references(eid) == untouched
        assert service.for_item('grammar:topic') == expected
    assert len(calls) == 1
    assert snapshot(store) == before
    service.references(eid)
    assert len(calls) == 2  # A new action must reread progress.


def test_missing_targets_are_loaded_and_cached_results_are_detached(progress):
    _, service, calls = progress
    with service.read_operation():
        first = service._statuses({'grammar:topic'})
        first['grammar:topic']['abilities'].clear()
        values = service._statuses({'grammar:topic', 'word:student'})
        assert values['grammar:topic']['abilities']
        assert not values['word:student']['is_added']
        assert calls == [{'grammar:topic'}, {'word:student'}]


def test_own_committed_write_invalidates_existing_projection(progress):
    _, service, calls = progress
    with service.read_operation():
        assert not service._statuses({'word:student'})['word:student']['is_added']
        service.learning_store.enroll(targets()[2], now=START)
        assert service._statuses({'word:student'})['word:student']['is_added']
    assert len(calls) == 2


def test_external_commit_invalidates_existing_projection(progress):
    store, service, calls = progress
    database = store._db.execute('PRAGMA database_list').fetchone()[2]
    with closing(sqlite3.connect(database)) as other, service.read_operation():
        assert service._statuses({'grammar:topic'})['grammar:topic']['stage'] == 'intro'
        with other:
            other.execute("UPDATE learning_content SET stage='guide' WHERE content_id='grammar:topic'")
        assert service._statuses({'grammar:topic'})['grammar:topic']['stage'] == 'guide'
    assert len(calls) == 2


@pytest.mark.parametrize('finish', ['commit', 'rollback'])
def test_transaction_reads_bypass_cache_and_do_not_publish_uncommitted_state(progress, finish):
    store, service, calls = progress
    with service.read_operation():
        assert service._statuses({'grammar:topic'})['grammar:topic']['stage'] == 'intro'
        store._db.execute('BEGIN')
        try:
            store._db.execute("UPDATE learning_content SET stage='guide' WHERE content_id='grammar:topic'")
            for _ in range(2):
                assert service._statuses({'grammar:topic'})['grammar:topic']['stage'] == 'guide'
            assert len(calls) == 3
            assert service._progress_read['statuses'] == {}
            getattr(store._db, finish)()
        finally:
            store._db.rollback()
        expected = 'guide' if finish == 'commit' else 'intro'
        assert service._statuses({'grammar:topic'})['grammar:topic']['stage'] == expected
    assert len(calls) == 4


def test_nested_scope_and_exceptions_cannot_extend_lifetime(progress):
    _, service, calls = progress
    with pytest.raises(RuntimeError, match='outer'):
        with service.read_operation():
            service._statuses({'grammar:topic'})
            with pytest.raises(RuntimeError, match='inner'):
                with service.read_operation():
                    service._statuses({'grammar:topic'})
                    raise RuntimeError('inner')
            service._statuses({'grammar:topic'})
            assert len(calls) == 1
            raise RuntimeError('outer')
    assert service._progress_read is None
    with service.read_operation():
        service._statuses({'grammar:topic'})
    assert len(calls) == 2


def test_refresh_discards_progress_even_when_database_token_is_unchanged(progress):
    _, service, calls = progress
    with service.read_operation():
        service._statuses({'grammar:topic'})
        service.refresh()
        service._statuses({'grammar:topic'})
    assert len(calls) == 2


def test_write_during_missing_target_read_cannot_merge_two_revisions(progress, monkeypatch):
    store, service, _ = progress
    with service.read_operation():
        service._statuses({'grammar:topic'})
        original = service._statuses_uncached
        changed = False

        def racing_read(identities):
            nonlocal changed
            result = original(identities)
            if not changed:
                with store._db:
                    store._db.execute("UPDATE learning_content SET stage='guide' WHERE content_id='grammar:topic'")
                changed = True
            return result

        monkeypatch.setattr(service, '_statuses_uncached', racing_read)
        values = service._statuses({'grammar:topic', 'word:student'})
        assert values['grammar:topic']['stage'] == 'guide'
        assert not values['word:student']['is_added']
        assert service._progress_read['statuses'] == {}
        assert service._statuses({'grammar:topic'})['grammar:topic']['stage'] == 'guide'
