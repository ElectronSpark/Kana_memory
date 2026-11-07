"""File formatting must not make a valid example export impossible to import."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kanamemory.example_catalogue import bundled_example_pack
from kanamemory.example_store import (
    ExampleStore, MAX_BYTES, MAX_IMPORT_BYTES, empty_pack, parse_pack, serialize_pack,
)
from kanamemory.learning_content import learning_items
from kanamemory.example_ui import ExampleUIMixin
from support.example_store_support import snapshot
from support.learning_store_fixtures import stores, item, question, START


@pytest.fixture(scope="module")
def complete_material():
    items = learning_items()
    return items, bundled_example_pack(items)


def test_complete_pretty_bundle_roundtrip_preserves_learning_and_drafts(stores, complete_material):
    store, learning = stores
    items, pack = complete_material
    # Keep a real pending learning record alongside the material import.
    saved_item = item("grammar:exchange-preserved")
    learning.enroll(saved_item, now=START)
    saved_question = question(saved_item["id"], "exchange-pending")
    learning.append_questions([saved_question], now=START)
    learning.save_draft(saved_question["id"], {"answer": "未完成的草稿"})
    before = snapshot(store)
    pretty = json.dumps(pack, ensure_ascii=False, indent=2).encode("utf-8")
    # The audited catalogue originally exceeded 16 MiB when indented. Keep
    # checking that boundary if later editorial shortening makes it smaller.
    if len(pretty) <= MAX_BYTES:
        pretty += b" " * (MAX_BYTES + 1 - len(pretty))
    assert MAX_BYTES < len(pretty) <= MAX_IMPORT_BYTES
    decoded = parse_pack(pretty, items)
    assert decoded == pack
    resources = ExampleStore(store, learning, items, bundled_pack=empty_pack())
    resources.import_pack(decoded)
    exported = serialize_pack(resources.export_pack())
    assert len(exported) <= MAX_BYTES
    assert parse_pack(exported, items) == pack
    changes = store._db.total_changes
    resources.import_pack(parse_pack(exported, items))
    assert store._db.total_changes == changes
    assert snapshot(store) == before
    assert learning.history() == []


def test_complete_bundle_import_deduplicates_and_full_export_roundtrips(stores, complete_material):
    store, learning = stores
    items, pack = complete_material
    resources = ExampleStore(store, learning, items, bundled_pack=pack)
    before = snapshot(store)
    assert resources.import_pack(pack) == {"examples": 0, "references": 0}
    assert parse_pack(serialize_pack(resources.export_pack(include_bundled=True)), items) == pack
    assert snapshot(store) == before


def test_ui_exports_compact_and_reads_the_full_legacy_pretty_file(complete_material):
    _, pack = complete_material
    captured = []
    pretty = json.dumps(pack, ensure_ascii=False, indent=2).encode("utf-8")

    async def export_backup(data, filename):
        captured.append(data)
        return True

    async def import_backup():
        return pretty

    app = SimpleNamespace(
        example_store=lambda: SimpleNamespace(export_pack=lambda: pack),
        platform=SimpleNamespace(export_backup=export_backup, import_backup=import_backup),
        alert=Mock(), _page_token=1, _example_json_input=SimpleNamespace(value=""),
        preview_example_import=Mock(),
    )
    # The platform stubs complete without I/O. Drive the coroutine directly;
    # Windows' event-loop socketpair is unavailable in restricted test hosts.
    with pytest.raises(StopIteration):
        ExampleUIMixin.export_examples(app).send(None)
    assert len(captured[0]) <= MAX_BYTES
    assert json.loads(captured[0]) == pack
    with pytest.raises(StopIteration):
        ExampleUIMixin.read_examples(app).send(None)
    assert app._example_json_input.value == pretty.decode("utf-8")
    app.preview_example_import.assert_called_once()
    app.alert.assert_called_once_with('已导出例句与关联。')


@pytest.mark.parametrize("encoding", ["bytes", "unicode-text"])
def test_oversized_raw_input_is_rejected_before_json_parsing(encoding):
    raw = (b" " * (MAX_IMPORT_BYTES + 1) if encoding == "bytes"
           else "界" * (MAX_IMPORT_BYTES // 3 + 1))
    with pytest.raises(ValueError, match="32 MiB"):
        parse_pack(raw)


def test_exact_raw_boundary_and_utf8_bom_remain_valid():
    encoded = serialize_pack(empty_pack())
    at_limit = encoded + b" " * (MAX_IMPORT_BYTES - len(encoded))
    assert parse_pack(at_limit) == empty_pack()
    assert parse_pack(b"\xef\xbb\xbf" + encoded) == empty_pack()
    assert parse_pack("\ufeff" + encoded.decode("utf-8")) == empty_pack()


def test_larger_file_allowance_does_not_raise_the_normalized_material_limit():
    pack = {**empty_pack(), "extra": "x" * MAX_BYTES}
    raw = json.dumps(pack, separators=(",", ":")).encode()
    assert MAX_BYTES < len(raw) < MAX_IMPORT_BYTES
    with pytest.raises(ValueError, match="16 MiB"):
        parse_pack(raw)
    with pytest.raises(ValueError, match="16 MiB"):
        serialize_pack(pack)


def test_export_validates_and_import_preview_preserves_explicit_review_gate():
    with pytest.raises(ValueError, match="格式"):
        serialize_pack({**empty_pack(), "format": "wrong"})
    omitted = empty_pack()
    del omitted["status"]
    raw = json.dumps(omitted)
    assert parse_pack(raw)["status"] == "reviewed"  # legacy API compatibility
    with pytest.raises(ValueError, match="审核状态"):
        parse_pack(raw, require_status=True)
    draft = {**empty_pack(), "status": "draft"}
    assert parse_pack(serialize_pack(draft), require_status=True)["status"] == "draft"
