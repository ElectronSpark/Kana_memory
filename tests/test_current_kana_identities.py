"""Batch routing is a current read, not prospective enrollment or a write cache."""
from contextlib import closing
import json
import sqlite3

import pytest

from kanamemory.domain import Question
from kanamemory.kana_ability_bridge import BRIDGE_SETTING, ability_unit_id
from kanamemory import kana_routing
from support.kana_ability_bridge_support import store, START, CID, UID
from support.kana_routing_support import activate


def test_batch_matches_live_scalar_identity_for_active_planned_and_legacy_keys(store, monkeypatch):
    activate(store)
    planned = 'kana-3044'
    store.start_session([Question(planned, 'kana_choice', 'katakana', 'イ', 'i',
        metadata={'practice_config': {'schedule_mode': 'spaced'}})], now=START)
    keys = [(CID, 'kana_choice', 'hiragana'), (CID, 'kana_romaji', 'hiragana'),
            (CID, 'kana_choice', 'katakana'), (CID, 'unknown-skill', 'hiragana'),
            (planned, 'kana_choice', 'katakana'), (planned, 'kana_romaji', 'katakana'),
            ('kana-3046', 'kana_choice', 'hiragana')]
    expected = {key: kana_routing.identity(store, *key) for key in keys}
    before = tuple(store._db.iterdump())
    calls = []
    original = kana_routing.active_units

    def active(current):
        calls.append(current)
        return original(current)

    monkeypatch.setattr(kana_routing, 'active_units', active)
    result = kana_routing.current_identities(store, (key for key in keys + keys))
    assert result == expected
    assert result[keys[0]] == result[keys[1]] == UID
    assert result[keys[2]] == keys[2]
    assert result[keys[4]] == result[keys[5]] == ability_unit_id(planned, 'katakana', 'reading')
    assert result[keys[-1]] == keys[-1]  # A future ability must not be manufactured.
    assert len(calls) == 1
    result.clear()
    assert kana_routing.current_identities(store, keys) == expected
    assert tuple(store._db.iterdump()) == before


@pytest.mark.parametrize('finish', ['commit', 'rollback'])
def test_batch_sees_transaction_changes_and_next_action_after_finish(store, finish):
    activate(store)
    key = CID, 'kana_choice', 'hiragana'
    assert kana_routing.current_identities(store, [key])[key] == UID
    store._db.execute('BEGIN IMMEDIATE')
    try:
        value = store.get_setting(BRIDGE_SETTING)
        value['units'][UID]['status'] = 'deferred'
        store._db.execute('UPDATE app_setting SET value_json=? WHERE key=?',
            (json.dumps(value), BRIDGE_SETTING))
        assert kana_routing.current_identities(store, [key])[key] == key
        getattr(store._db, finish)()
    finally:
        store._db.rollback()
    expected = UID if finish == 'rollback' else key
    assert kana_routing.current_identities(store, [key])[key] == expected


def test_batch_observes_another_connection_commit(store):
    activate(store)
    key = CID, 'kana_romaji', 'hiragana'
    assert kana_routing.current_identities(store, [key])[key] == UID
    database = store._db.execute('PRAGMA database_list').fetchone()[2]
    with closing(sqlite3.connect(database)) as other:
        value = json.loads(other.execute('SELECT value_json FROM app_setting WHERE key=?',
            (BRIDGE_SETTING,)).fetchone()[0])
        value['units'][UID]['status'] = 'deferred'
        with other:
            other.execute('UPDATE app_setting SET value_json=? WHERE key=?',
                (json.dumps(value), BRIDGE_SETTING))
        assert kana_routing.current_identities(store, [key])[key] == key
