"""An intro membership cache must observe SQL changes without owning settings."""
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory.domain import StudyStore
from kanamemory.kana_learning_intro import KANA_INTRO_SETTING, _introduced_keys, _saved


@pytest.fixture
def store(tmp_path):
    with StudyStore(tmp_path / "learner") as value:
        yield value


def setting(*keys):
    return {"version": 1, "introduced": {key: {"route": "teaching"} for key in keys}}


def test_membership_reuses_only_immutable_keys_and_keeps_mutator_reads_independent(store):
    store.set_setting(KANA_INTRO_SETTING, setting("a", "b"))
    keys = _introduced_keys(store)
    assert isinstance(keys, frozenset) and keys == {"a", "b"}
    assert _introduced_keys(store) is keys
    draft = _saved(store)
    draft["introduced"].pop("a")
    draft["introduced"]["b"]["route"] = "changed-but-not-saved"
    assert _introduced_keys(store) is keys
    assert _saved(store) == setting("a", "b")
    store.set_setting(KANA_INTRO_SETTING, draft)
    assert _introduced_keys(store) == {"b"}


def test_membership_observes_external_sql_update_delete_and_reinsert(store):
    store.set_setting(KANA_INTRO_SETTING, setting("a"))
    assert _introduced_keys(store) == {"a"}
    with sqlite3.connect(store.path) as other:
        other.execute("UPDATE app_setting SET value_json=? WHERE key=?",
                      (json.dumps(setting("b")), KANA_INTRO_SETTING))
    assert _introduced_keys(store) == {"b"}
    with sqlite3.connect(store.path) as other:
        other.execute("DELETE FROM app_setting WHERE key=?", (KANA_INTRO_SETTING,))
    assert _introduced_keys(store) == set()
    store.set_setting(KANA_INTRO_SETTING, setting("a"))
    assert _introduced_keys(store) == {"a"}


def test_membership_observes_uncommitted_write_then_rollback_without_writing(store):
    store.set_setting(KANA_INTRO_SETTING, setting("before"))
    assert _introduced_keys(store) == {"before"}
    store._db.execute("UPDATE app_setting SET value_json=? WHERE key=?",
                      (json.dumps(setting("temporary")), KANA_INTRO_SETTING))
    changes = store._db.total_changes
    assert _introduced_keys(store) == {"temporary"}
    assert store._db.total_changes == changes and store._db.in_transaction
    store._db.rollback()
    assert _introduced_keys(store) == {"before"}
    assert store._db.total_changes == changes and not store._db.in_transaction


@pytest.mark.parametrize("value", [None, [], {}, {"version": 2, "introduced": {"x": {}}},
                                  {"version": 1, "introduced": []}, setting("x")])
def test_membership_has_the_same_invalid_setting_semantics_as_saved(store, value):
    store.set_setting(KANA_INTRO_SETTING, value)
    assert _introduced_keys(store) == frozenset(_saved(store)["introduced"])


def test_membership_keeps_malformed_json_failure_and_supports_store_adapters(store):
    store._db.execute("INSERT INTO app_setting VALUES (?,?)", (KANA_INTRO_SETTING, "not-json"))
    with pytest.raises(json.JSONDecodeError):
        _introduced_keys(store)
    for value in (setting("adapter"), None):
        adapter = SimpleNamespace(get_setting=lambda key, default: value)
        assert _introduced_keys(adapter) == frozenset(_saved(adapter)["introduced"])
