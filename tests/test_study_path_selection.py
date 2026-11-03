"""Hierarchy selection is pure entry identity state, independent of expansion."""
from copy import deepcopy

import pytest

from kanamemory.study_path_selection import StudyPathSelection


def overview():
    def entry(identity, state="new", kind="learning"):
        return {"id": identity, "state": state, "kind": kind, "title": identity}
    return {"stages": [
        {"id": "first", "units": [
            {"id": "a", "sections": [
                {"id": "one", "entries": [entry("e1"), entry("e2", "added"), entry("e3")]},
                {"id": "two", "entries": [entry("e4", "started"), entry("e5", "completed")]}]},
            {"id": "b", "sections": [{"id": "three", "entries": [entry("r1", kind="reference")]}]}]},
        {"id": "last", "units": [{"id": "c", "sections": [
            {"id": "four", "entries": [entry("e6"), entry("e7")]}]}]}]}


def test_multilevel_tristate_only_counts_new_descendants_and_stays_in_directory_order():
    model = StudyPathSelection(overview())
    assert model.toggle(("entry", "e3")) == ["e3"]
    assert model.state(("section", "one")) == "partial"
    assert model.state(("unit", "a")) == "partial"
    assert model.state(("stage", "first")) == "partial"
    assert model.toggle(("stage", "last")) == ["e3", "e6", "e7"]
    assert model.toggle(("stage", "first")) == ["e1", "e3", "r1", "e6", "e7"]
    assert model.selected_count == 5
    assert model.state(("unit", "a")) == "all"
    assert model.state(("stage", "first")) == "all"
    assert model.counts(("stage", "first")) == {
        "total_count": 6, "new_count": 3, "added_count": 3, "selected_count": 3}
    assert model.toggle(("section", "one")) == ["r1", "e6", "e7"]
    assert model.state(("stage", "first")) == "partial"
    assert model.toggle(("stage", "first")) == ["e1", "e3", "r1", "e6", "e7"]


@pytest.mark.parametrize("key", [("entry", "e2"), ("entry", "e4"), ("entry", "e5"),
                                  ("section", "two")])
def test_already_added_started_or_completed_nodes_are_not_selectable(key):
    model = StudyPathSelection(overview())
    assert model.counts(key)["new_count"] == 0
    assert model.select(key) == []
    assert model.toggle(key) == []
    assert model.state(key) == "none"


def test_reference_selectable_and_clear_does_not_mutate_original_or_returned_snapshots():
    source = overview(); original = deepcopy(source)
    model = StudyPathSelection(source, ["e7", "r1", "e2", "e7"])
    assert model.selected_entry_ids == ["r1", "e7"]
    ids = model.selected_entry_ids
    ids.append("e1")
    model.counts(("stage", "first"))["new_count"] = 99
    assert model.selected_entry_ids == ["r1", "e7"]
    assert source == original
    assert model.clear() == []
    assert model.selected_count == 0


def test_refresh_keeps_choices_across_rebuild_and_drops_newly_enrolled_or_removed_entries():
    source = overview()
    model = StudyPathSelection(source, ["e1", "e3", "r1", "e6"])
    assert model.refresh(deepcopy(source)) == ["e1", "e3", "r1", "e6"]
    source["stages"][0]["units"][0]["sections"][0]["entries"][0]["state"] = "added"
    source["stages"][1]["units"][0]["sections"][0]["entries"] = []
    assert model.refresh(source) == ["e3", "r1"]
    assert model.state(("section", "one")) == "all"
    assert model.state(("stage", "last")) == "none"


def test_typed_node_identity_and_invalid_keys_cannot_select_an_unrelated_node():
    source = overview()
    source["stages"][0]["id"] = "one"  # Reuse a section ID at another level.
    model = StudyPathSelection(source)
    model.select(("section", "one"))
    assert model.state(("stage", "one")) == "partial"
    with pytest.raises(KeyError):
        model.toggle(("entry", "one"))
    with pytest.raises(ValueError):
        model.select(("section", "one"), "yes")
    assert model.selected_entry_ids == ["e1", "e3"]


def test_refresh_rejects_duplicate_entry_identity_without_losing_old_choices():
    model = StudyPathSelection(overview(), ["e1"])
    broken = overview()
    broken["stages"][1]["units"][0]["sections"][0]["entries"][0]["id"] = "e1"
    with pytest.raises(ValueError, match="重复"):
        model.refresh(broken)
    assert model.selected_entry_ids == ["e1"]


def test_node_status_is_an_immutable_read_not_a_live_progress_cache():
    from dataclasses import FrozenInstanceError

    model = StudyPathSelection(overview(), ["e1"])
    key = ("section", "one")
    status = model.node_status(key)
    assert status.state == "partial"
    assert status.counts() == model.counts(key)
    with pytest.raises(FrozenInstanceError):
        status.selected_count = 100
    model.select(key)
    assert status.selected_count == 1
    assert model.node_status(key).state == "all"
    updated = overview()
    updated["stages"][0]["units"][0]["sections"][0]["entries"][0]["state"] = "added"
    model.refresh(updated)
    assert model.node_status(key).counts() == {
        "total_count": 3, "new_count": 1, "added_count": 2, "selected_count": 1,
    }
