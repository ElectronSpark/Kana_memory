"""Copy optimization must preserve reviewed content, detached views and imports."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest


from kanamemory import material_review
from kanamemory.example_catalogue import bundled_example_pack
from kanamemory.example_store import ExampleStore, SETTING, empty_pack, validate_pack
from kanamemory.learning_content import learning_items
from kanamemory.material_review import fingerprint, item_fingerprint, review_data
from support.example_store_support import pack
from support.example_store_support import targets
from support.learning_store_fixtures import stores, question, ready, START


def original_reviewed_pack(pack, *, include_additions=False, known_targets=None):
    """Frozen pre-optimization oracle from 83a4d49; retain its eager copies."""
    data = review_data()
    result = deepcopy(pack)
    examples, references = {}, {}
    source_examples = {row["id"]: row for row in pack["examples"]}
    payload_changes = {entry["before_payload_sha256"]: entry
                       for entry in data.get("references", {}).values()
                       if entry.get("before_payload_sha256")}
    for source in pack["examples"]:
        change = data.get("examples", {}).get(source["id"])
        row = (change["replacement"] if change and fingerprint(source) in change.get("input_sha256s", [change["before_sha256"]])
               else source)
        if row is not None:
            examples[row["id"]] = deepcopy(row)
    for source in pack["references"]:
        change = data.get("references", {}).get(source["id"])
        exact_match = change and fingerprint(source) in change.get("input_sha256s", [change["before_sha256"]])
        if not exact_match:
            change = payload_changes.get(fingerprint({k: v for k, v in source.items() if k != "id"}))
            exact_match = change is not None
        if change and change.get("example_before_sha256"):
            example = source_examples.get(source["example_id"])
            if example is None or fingerprint(example) != change["example_before_sha256"]:
                change = None
        row = change["replacement"] if change and exact_match else source
        if row is not None:
            references[row["id"]] = deepcopy(row)
    if include_additions:
        targets = set(known_targets) if known_targets is not None else None
        if isinstance(known_targets, dict):
            targets = {cid for cid, item in known_targets.items() if isinstance(item, dict)
                       and item_fingerprint(item) in data.get("target_fingerprints", {}).get(cid, [])}
        for row in data.get("added_references", []):
            if targets is None or row["target_id"] in targets:
                references.setdefault(row["id"], deepcopy(row))
        used = {row["example_id"] for row in references.values()}
        for row in data.get("added_examples", []):
            if row["id"] in used:
                examples.setdefault(row["id"], deepcopy(row))
    used = {row["example_id"] for row in references.values()}
    for source in pack["examples"]:
        if source["id"] in used and source["id"] not in examples:
            examples[source["id"]] = deepcopy(source)
    result["examples"] = sorted(examples.values(), key=lambda row: row["id"])
    result["references"] = sorted(references.values(), key=lambda row: row["id"])
    return result


def mutable_ids(value):
    if isinstance(value, dict):
        return {id(value)}.union(*(mutable_ids(v) for v in value.values()))
    if isinstance(value, list):
        return {id(value)}.union(*(mutable_ids(v) for v in value))
    return set()


@pytest.fixture(scope="module")
def complete_catalogue():
    items = learning_items()
    return items, bundled_example_pack(items, apply_review=False)


@pytest.mark.parametrize("include_additions", [False, True])
def test_full_review_graph_matches_original_and_owns_all_mutable_children(complete_catalogue, include_additions):
    items, raw = complete_catalogue
    known = {item["id"]: item for item in items}
    original_bytes = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    expected = original_reviewed_pack(raw, include_additions=include_additions, known_targets=known)
    actual = material_review.reviewed_pack(raw, include_additions=include_additions, known_targets=known)
    assert actual == expected  # includes translations, hints, spans, order and every reference
    assert json.dumps(raw, ensure_ascii=False, sort_keys=True) == original_bytes
    assert not mutable_ids(actual) & (mutable_ids(raw) | mutable_ids(review_data()))
    validate_pack(actual, known)
    assert material_review.reviewed_pack(actual, include_additions=include_additions, known_targets=known) == actual


def test_metadata_and_duplicate_added_records_remain_detached(monkeypatch):
    raw = pack()
    raw["metadata"] = {"notes": ["user metadata"], "snapshot": raw["examples"][0]}
    data = {"added_examples": deepcopy(raw["examples"]), "added_references": deepcopy(raw["references"])}
    # Existing source records must win even if an added record with that ID differs.
    data["added_examples"][0]["translation"] = "This duplicate must not win."
    data["added_references"][0]["hint"]["rule"] = "This duplicate must not win."
    monkeypatch.setattr(material_review, "review_data", lambda: data)
    actual = material_review.reviewed_pack(raw, include_additions=True)
    assert actual == raw
    assert not mutable_ids(actual) & (mutable_ids(raw) | mutable_ids(data))
    actual["metadata"]["notes"].append("edited in returned view")
    actual["examples"][0]["source_ids"].append("user-source")
    actual["references"][0]["hint"]["rule"] = "edited view"
    assert material_review.reviewed_pack(raw, include_additions=True) == raw
    assert raw["metadata"]["notes"] == ["user metadata"]


def test_previously_enrolled_history_and_draft_are_unchanged_by_equivalent_ranked_views(stores):
    store, learning = stores
    items = targets()
    target = items[1]
    learning.enroll(target, now=START)
    learning.append_questions(question(cid=target["id"]), now=START)
    when = ready(learning, target["id"])
    learning.submit("q1", "正在读书", True, now=when)
    learning.finalize("q1", 3, now=when)
    learning.append_questions(question(cid=target["id"], qid="unfinished"), now=when)
    learning.save_draft("unfinished", {"answer": "unfinished draft", "assisted": False})
    raw = pack()
    store.set_setting(SETTING, raw)
    before = list(store._db.iterdump())
    old = ExampleStore(store, learning, items, original_reviewed_pack(raw))
    new = ExampleStore(store, learning, items, material_review.reviewed_pack(raw))
    for item in items:
        assert new.for_item(item["id"]) == old.for_item(item["id"])
    for example in raw["examples"]:
        assert new.references(example["id"]) == old.references(example["id"])
    returned = new.get(raw["examples"][0]["id"])
    returned["source_ids"].append("mutated")
    assert new.get(returned["id"]) == old.get(returned["id"])
    assert list(store._db.iterdump()) == before


@pytest.mark.parametrize("field,value", [("status", "draft"), ("version", 999)])
def test_custom_pack_validation_is_not_bypassed(stores, field, value):
    store, learning = stores
    invalid = pack()
    invalid[field] = value
    store.set_setting(SETTING, invalid)
    before = list(store._db.iterdump())
    with pytest.raises(ValueError):
        ExampleStore(store, learning, targets(), empty_pack())
    assert list(store._db.iterdump()) == before


def test_invalid_custom_anchor_still_rejected_before_review(stores):
    store, learning = stores
    invalid = pack()
    invalid["references"][0]["spans"][0]["quote"] = "not the original slice"
    store.set_setting(SETTING, invalid)
    before = list(store._db.iterdump())
    with pytest.raises(ValueError):
        ExampleStore(store, learning, targets(), empty_pack())
    assert list(store._db.iterdump()) == before
