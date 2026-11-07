"""Resource refresh caching must not cache learning progress or skip validation."""
from copy import deepcopy

import pytest

from kanamemory import example_store as module
from kanamemory.example_store import ExampleStore, SETTING, empty_pack, example_id_for, reference_id_for
from support.example_store_support import pack
from support.example_store_support import reference
from support.example_store_support import snapshot
from support.example_store_support import targets
from support.learning_store_fixtures import stores, question, ready, START


def unexpected_rebuild(*args, **kwargs):
    raise AssertionError("Unchanged resources must not be revalidated or merged")


def test_empty_custom_is_validated_once_and_bundle_views_are_detached(stores, monkeypatch):
    store, learning = stores
    calls = []
    original_validate = module.validate_pack

    def tracked_validate(value, *args, **kwargs):
        calls.append(deepcopy(value))
        return original_validate(value, *args, **kwargs)

    monkeypatch.setattr(module, "validate_pack", tracked_validate)
    monkeypatch.setattr(module, "_merge_packs", unexpected_rebuild)
    value = pack()
    resources = ExampleStore(store, learning, targets(), value)
    assert calls == [value, empty_pack()]
    eid = value["examples"][0]["id"]
    assert resources.examples[eid] is resources.bundled["examples"][0]
    before = deepcopy(resources.bundled)

    resources.get(eid)["source_ids"].append("changed")
    resources.references(eid)[0]["spans"][0]["quote"] = "changed"
    resources.for_item("grammar:topic")[0]["reference_options"][0]["hint"].clear()
    resources.all_examples()[0]["reference"]["spans"].clear()
    resources.export_pack()["examples"].append({})
    assert resources.bundled == before
    resources.refresh()
    assert calls == [value, empty_pack()]


@pytest.mark.parametrize("custom", [False, True])
def test_learning_writes_reuse_resources_but_update_ranking_and_status(stores, monkeypatch, custom):
    store, learning = stores
    value = pack()
    second = {**value["examples"][0], "id": example_id_for("私は先生です。"),
              "text": "私は先生です。", "translation": "我是老师。"}
    value["examples"].append(second)
    value["references"].extend([
        reference(second, "grammar:topic", "は"),
        reference(second, "grammar:polite", "です"),
    ])
    if custom:
        store.set_setting(SETTING, value)
    resources = ExampleStore(store, learning, targets(), empty_pack() if custom else value)
    old_examples, old_refs = resources.examples, resources.refs
    old_token = resources._token
    assert all(row["score"] == 0 for row in resources.for_item("grammar:topic"))
    monkeypatch.setattr(module, "validate_pack", unexpected_rebuild)
    monkeypatch.setattr(module, "_merge_packs", unexpected_rebuild)

    target = targets()[1]
    learning.enroll(target, now=START)
    learning.append_questions(question(cid=target["id"]), now=START)
    when = ready(learning, target["id"])
    learning.submit("q1", "正在读书", True, now=when)
    learning.finalize("q1", 3, now=when)
    before = snapshot(store)
    rows = resources.for_item("grammar:topic")
    assert rows[0]["id"] == second["id"]
    assert rows[0]["score"] > rows[1]["score"] > 0
    ref = next(row for row in resources.references(second["id"])
               if row["target_id"] == target["id"])
    assert ref["status"]["is_added"] and ref["familiarity"] > 0
    assert resources.examples is old_examples and resources.refs is old_refs
    assert resources._token != old_token
    assert resources._token == resources._database_token()
    assert snapshot(store) == before


def test_changed_custom_refreshes_indexes_and_removed_custom_returns_to_bundle(stores):
    store, learning = stores
    resources = ExampleStore(store, learning, targets(), empty_pack())
    value = pack()
    store.set_setting(SETTING, value)
    eid = value["examples"][0]["id"]
    assert resources.get(eid)["text"] == value["examples"][0]["text"]
    first_refs = resources.refs
    restored = deepcopy(value)
    restored["references"][0]["target_id"] = "grammar:retired"
    restored["references"][0]["id"] = reference_id_for(restored["references"][0])
    store.set_setting(SETTING, restored)
    refs = resources.references(eid)
    assert resources.refs is not first_refs
    retired = next(row for row in refs if row["target_id"] == "grammar:retired")
    assert retired["target"]["unavailable"] and not retired["status"]["is_added"]
    assert resources.export_pack() == restored
    store.set_setting(SETTING, empty_pack())
    assert resources.get(eid) is None
    assert resources.examples == resources.refs == {}


@pytest.mark.parametrize("change", [
    lambda value: value.update(version=True),
    lambda value: value["references"][0]["spans"][0].update(start=True),
])
def test_type_change_cannot_bypass_validation_and_invalid_restore_does_not_poison_cache(stores, change):
    store, learning = stores
    value = pack()
    store.set_setting(SETTING, value)
    resources = ExampleStore(store, learning, targets(), empty_pack())
    old_examples, old_token = resources.examples, resources._token
    invalid = deepcopy(value)
    change(invalid)
    assert invalid == value  # Python equality conflates bool and int.
    store.set_setting(SETTING, invalid)
    eid = value["examples"][0]["id"]
    for _ in range(2):
        with pytest.raises(ValueError):
            resources.get(eid)
    assert resources.examples is old_examples
    assert resources._token == old_token
    store.set_setting(SETTING, value)
    assert resources.get(eid)["text"] == value["examples"][0]["text"]
    assert resources.examples is old_examples


@pytest.mark.parametrize("change", [
    lambda value: value.update(version=True),
    lambda value: value.update(status="draft"),
])
def test_first_empty_custom_still_requires_complete_validation(stores, change):
    store, learning = stores
    invalid = empty_pack()
    change(invalid)
    store.set_setting(SETTING, invalid)
    with pytest.raises(ValueError):
        ExampleStore(store, learning, targets(), pack())
