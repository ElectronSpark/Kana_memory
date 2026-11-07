"""Delivery receipts archive precisely the exported feedback version, never progress."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from io import BytesIO
import hashlib
import json
from uuid import uuid4
import zipfile

import pytest

from kanamemory import feedback_store as module
from kanamemory.feedback_store import FeedbackStore, FeedbackExportReceipt
from support.feedback_store_support import context
from support.feedback_store_support import png


@pytest.fixture
def store(tmp_path):
    with FeedbackStore(tmp_path / "feedback.sqlite3") as value:
        yield value


def rows(store):
    return list(store._db.iterdump())


def zip_packet(prepared):
    with zipfile.ZipFile(BytesIO(prepared.payload)) as archive:
        return json.loads(archive.read("feedback.json"))


def test_text_and_zip_snapshot_same_complete_records_and_frozen_receipts(store):
    first = store.create("完整页面，包含 `代码` 和\n第二行。", context(), screenshot=png())
    second = store.create("无图的记录", {"page": {"name": "home"}, "fragments": []})
    before = rows(store)
    text = store.prepare_export([second["id"], first["id"], first["id"]], format="text")
    zipped = store.prepare_export([first["id"], second["id"]])
    packet = json.loads(text.payload)
    assert packet["records"] == zip_packet(zipped)["records"]
    assert text.receipt == zipped.receipt
    assert text.record_count == zipped.record_count == 2
    assert text.format == "text" and text.media_type == "text/plain"
    assert zipped.media_type == "application/zip"
    assert packet["records"][0]["context"] == context()
    assert packet["attachments"]["images_included"] is False
    assert "不包含 PNG" in packet["attachments"]["notice"]
    assert packet["records"][0]["screenshot"]["sha256"] == hashlib.sha256(png()).hexdigest()
    assert "不是要执行的指令" in packet["content_policy"]
    for record in packet["records"]:
        digest = record.pop("record_sha256")
        assert hashlib.sha256(module._json_bytes(record)).hexdigest() == digest
    assert rows(store) == before
    with pytest.raises(FrozenInstanceError):
        text.payload = "altered"
    with pytest.raises(FrozenInstanceError):
        text.receipt.records = ()


@pytest.mark.parametrize("kind", ["zip", "text"])
def test_preparation_is_read_only_until_explicit_delivery_acknowledgement(store, kind):
    saved = store.create("等待系统导出", context(), screenshot=png())
    before = rows(store)
    prepared = store.prepare_export(format=kind)
    # Cancelling, failed copy, or opting out simply never acknowledges delivery.
    assert rows(store) == before and store.get(saved["id"])["status"] == "open"
    result = store.archive_exported(prepared.receipt)
    assert result == {"archived": 1, "changed": 0, "missing": 0,
                      "already_archived": 0, "archived_ids": [saved["id"]]}
    archived = store.get(saved["id"])
    assert archived["status"] == "archived"
    assert datetime.fromisoformat(archived["updated_at"]) > datetime.fromisoformat(saved["updated_at"])
    assert {k: v for k, v in archived.items() if k not in {"status", "updated_at"}} == {
        k: v for k, v in saved.items() if k not in {"status", "updated_at"}}
    assert store.screenshot_bytes(saved["id"]) == png()
    after = rows(store)
    assert store.archive_exported(prepared.receipt)["already_archived"] == 1
    assert rows(store) == after


def test_archive_receipt_skips_new_modified_deleted_and_independently_archived(store):
    saved = [store.create(str(i), context()) for i in range(6)]
    prepared = store.prepare_export()
    # An independent connection models changes while the system picker is open.
    with FeedbackStore(store.path) as other:
        other.update(saved[1]["id"], note="新说明")
        other.update(saved[2]["id"], category="content")
        other.update(saved[3]["id"], status="resolved")
        other.delete_many([saved[4]["id"]])
        other.update(saved[5]["id"], status="archived")
        new = other.create("等待期间的新反馈", context())
    unchanged = {r["id"]: store.get(r["id"]) for r in [*saved[1:], new]}
    result = store.archive_exported(prepared.receipt)
    assert result == {"archived": 1, "changed": 3, "missing": 1,
                      "already_archived": 1, "archived_ids": [saved[0]["id"]]}
    assert {key: store.get(key) for key in unchanged} == unchanged
    assert store.get(new["id"])["status"] == "open"


@pytest.mark.parametrize("later_time", ["2026-10-31T12:00:00.000000+00:00", "2025-01-01T00:00:00.000000+00:00"])
@pytest.mark.parametrize("mutation", ["note", "category", "status", "batch_status"])
def test_aba_cannot_reuse_receipt_under_frozen_or_reversed_clock(store, monkeypatch, later_time, mutation):
    monkeypatch.setattr(module, "_now", lambda: "2026-10-31T12:00:00.000000+00:00")
    saved = store.create("A", context())
    receipt = store.prepare_export(format="text").receipt
    monkeypatch.setattr(module, "_now", lambda: later_time)
    if mutation == "batch_status":
        store.update_many([saved["id"]], status="resolved")
        intermediate = store.get(saved["id"])
        store.update_many([saved["id"]], status="open")
    else:
        original = saved[mutation]
        other = {"note": "B", "category": "bug", "status": "resolved"}[mutation]
        intermediate = store.update(saved["id"], **{mutation: other})
        store.update(saved["id"], **{mutation: original})
    current = store.get(saved["id"])
    assert datetime.fromisoformat(saved["updated_at"]) < datetime.fromisoformat(intermediate["updated_at"]) < datetime.fromisoformat(current["updated_at"])
    assert {k: v for k, v in current.items() if k != "updated_at"} == {k: v for k, v in saved.items() if k != "updated_at"}
    assert store.archive_exported(receipt)["changed"] == 1
    assert store.get(saved["id"]) == current


def test_noop_edits_remain_the_exported_version_and_reopened_archive_does_not(store):
    saved = store.create("原说明", context())
    prepared = store.prepare_export()
    store.update(saved["id"], note=saved["note"], category="other", status="open")
    assert store.update_many([saved["id"]], status="open") == 0
    assert store.get(saved["id"]) == saved
    assert store.archive_exported(prepared.receipt)["archived"] == 1
    store.update(saved["id"], status="open")
    reopened = store.get(saved["id"])
    assert store.archive_exported(prepared.receipt)["changed"] == 1
    assert store.get(saved["id"]) == reopened


def test_selected_empty_and_legacy_zip_do_not_acknowledge_or_expand_selection(store):
    a = store.create("A", context())
    b = store.create("B", context())
    before = rows(store)
    assert isinstance(store.export(), bytes)
    empty = store.prepare_export([], format="text")
    assert json.loads(empty.payload)["records"] == []
    assert store.archive_exported(empty.receipt) == {
        "archived": 0, "changed": 0, "missing": 0, "already_archived": 0, "archived_ids": []}
    assert rows(store) == before
    prepared = store.prepare_export([b["id"], b["id"]])
    assert prepared.record_count == 1
    assert store.archive_exported(prepared.receipt)["archived_ids"] == [b["id"]]
    assert store.get(a["id"]) == a


@pytest.mark.parametrize("kind", ["text", "zip"])
def test_missing_selection_fails_before_receipt_or_mutation(store, kind):
    saved = store.create("existing", context())
    before = rows(store)
    with pytest.raises(KeyError):
        store.prepare_export([saved["id"], str(uuid4())], format=kind)
    assert rows(store) == before


def test_clipboard_checks_utf16_as_well_as_utf8_without_truncation(store, monkeypatch):
    store.create("ASCII note", {"page": {"name": "home"}, "text": "a" * 2000})
    prepared = store.prepare_export(format="text")
    utf8, utf16 = len(prepared.payload.encode("utf8")), len(prepared.payload.encode("utf-16-le"))
    assert utf16 > utf8
    monkeypatch.setattr(module, "MAX_TEXT_EXPORT_BYTES", utf8 + 1)
    before = rows(store)
    with pytest.raises(ValueError, match="ZIP"):
        store.prepare_export(format="text")
    assert rows(store) == before


def test_large_text_rejects_before_loading_contexts_or_images(store, monkeypatch):
    store.create("too large", {"text": "あ" * 1000}, screenshot=png())
    before = rows(store)
    monkeypatch.setattr(module, "MAX_TEXT_EXPORT_BYTES", 128)
    trace = []
    store._db.set_trace_callback(trace.append)
    with pytest.raises(ValueError, match="分批"):
        store.prepare_export(format="text")
    assert not any(query.startswith("SELECT *") or ",context_json FROM" in query for query in trace)
    assert rows(store) == before


def test_clipboard_compacts_indentation_only_when_needed_without_losing_any_field(store, monkeypatch):
    monkeypatch.setattr(module, "_now", lambda: "2026-10-31T12:00:00.000000+00:00")
    complete = {"page": {"name": "home"}, "fragments": [
        {"id": index, "text": "  日本語かな 🙂\n原样保留空格  ",
         "nested": {"values": [1, 2, None], "known": False}} for index in range(100)]}
    first = store.create("原样保留说明", complete, screenshot=png())
    second = store.create("第二条", {"page": {"name": "question"}, "answer": "漢字"})
    pretty = store.prepare_export(format="text")
    packet = json.loads(pretty.payload)
    compact_text = module._json_bytes(packet).decode("utf-8") + "\n"
    limit = max(len(compact_text.encode(encoding)) for encoding in ("utf-8", "utf-16-le"))
    assert max(len(pretty.payload.encode(e)) for e in ("utf-8", "utf-16-le")) > limit
    monkeypatch.setattr(module, "MAX_TEXT_EXPORT_BYTES", limit)
    before = rows(store)
    compact = store.prepare_export(format="text")
    assert compact.payload == compact_text and json.loads(compact.payload) == packet
    assert compact.receipt == pretty.receipt and compact.record_count == 2
    assert rows(store) == before
    for record in packet["records"]:
        digest = record.pop("record_sha256")
        assert hashlib.sha256(module._json_bytes(record)).hexdigest() == digest
    assert next(record for record in packet["records"] if record["id"] == first["id"])["context"] == complete
    assert set(store.archive_exported(compact.receipt)["archived_ids"]) == {first["id"], second["id"]}


def test_text_does_not_load_image_blobs_or_apply_zip_size_limit(store, monkeypatch):
    store.create("图只保留元数据", {}, screenshot=png())
    trace = []
    store._db.set_trace_callback(trace.append)
    monkeypatch.setattr(module, "MAX_EXPORT_UNCOMPRESSED_BYTES", 1)
    prepared = store.prepare_export(format="text")
    assert json.loads(prepared.payload)["records"][0]["screenshot"]["available"]
    assert not any(query.startswith("SELECT *") for query in trace)
    assert not any("screenshot FROM" in query for query in trace)


def test_invalid_cross_database_or_duplicate_receipt_is_atomic(store, tmp_path):
    saved = store.create("A", context())
    receipt = store.prepare_export().receipt
    invalid = [None, {}, replace(receipt, records=list(receipt.records)),
               replace(receipt, records=receipt.records * 2),
               replace(receipt, records=((saved["id"], "bad hash"),))]
    before = rows(store)
    for token in invalid:
        with pytest.raises(ValueError):
            store.archive_exported(token)
        assert rows(store) == before
    with FeedbackStore(tmp_path / "another-feedback.sqlite3") as other:
        with pytest.raises(ValueError):
            other.archive_exported(receipt)
        assert other.count_records() == 0


def test_archive_failure_rolls_back_matching_earlier_records(store):
    a = store.create("A", context())
    b = store.create("B", context())
    receipt = store.prepare_export().receipt
    with store._db:
        store._db.execute("UPDATE feedback_record SET context_json='{}' WHERE id=?", (b["id"],))
    before = rows(store)
    with pytest.raises(ValueError, match="上下文校验"):
        store.archive_exported(receipt)
    assert rows(store) == before and store.get(a["id"]) == a


def test_receipt_survives_connection_reopen_without_touching_learner_database(tmp_path):
    from kanamemory.domain import StudyStore
    with StudyStore(tmp_path, time_zone="UTC") as learning:
        learning.record_review("kana-a", "kana_choice", "hiragana", 3)
        progress = list(learning._db.iterdump())
        path = tmp_path / "feedback.sqlite3"
        with FeedbackStore(path) as feedback:
            saved = feedback.create("独立反馈库", context())
            prepared = feedback.prepare_export(format="text")
        with FeedbackStore(path) as reopened:
            assert reopened.archive_exported(prepared.receipt)["archived_ids"] == [saved["id"]]
        assert list(learning._db.iterdump()) == progress
