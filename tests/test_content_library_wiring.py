"""Design 08 section 16.5 (A1–A6): the service wiring of signatures, rule c, trusted keys and overrides.

Synthetic core resources and packs only (support/content_library_support.py);
no private course content is read.
"""
import asyncio
import copy
from datetime import datetime, timezone
import io
import json
from types import SimpleNamespace
import zipfile
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_library_signing as signing
from kanamemory import content_pack, learning_content
from kanamemory.content_library_service import ContentLibraryService
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore
from kanamemory.material_review import item_fingerprint
from support.app_fixtures import setup_app, question_app  # noqa: F401
from support.content_library_support import CORE_ITEMS, build_core_resources, extension_namespaces, pack_bytes
from support.fake_ui import descendants, text_of
from support.learning_fixtures import learning_app, click  # noqa: F401

SEED_A = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")  # RFC 8032 TEST 1
KEY_A = signing.public_key_from_seed(SEED_A).hex()
SEED_B = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")  # RFC 8032 TEST 2
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
CORE = rules.CORE_LIBRARY_ID
MIZU = "word:demo:mizu"


def _rewrite_manifest(data, change):
    source = zipfile.ZipFile(io.BytesIO(data))
    manifest = change(json.loads(source.read(rules.MANIFEST_FILE).decode("utf-8")))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as target:
        target.writestr(rules.MANIFEST_FILE, json.dumps(manifest, ensure_ascii=False))
        for info in source.infolist():
            if info.filename != rules.MANIFEST_FILE:
                target.writestr(info.filename, source.read(info))
    return buffer.getvalue()


def _signed(data, seed=SEED_A, key_id="author:a"):
    return _rewrite_manifest(data, lambda m: signing.sign_manifest(m, seed, public_key_id=key_id))


def _override_pack(*, version="20260102", revision="2", accept_reason="better gloss"):
    changed = dict(CORE_ITEMS[MIZU], revision=2, meaning="改写的水")
    if version != "20260102":
        changed["meaning"] += "（" + version + "）"  # a new version has new content
    return pack_bytes("author.n3", {"lesson": {"_ITEMS": {MIZU: changed}}}, version=version,
                      manifest_overrides={"overrides": [{"id": MIZU, "reason": accept_reason}],
                                          "revisions": {MIZU: revision}})


@pytest.fixture
def world(tmp_path, monkeypatch):
    resources = build_core_resources(tmp_path / "resources")
    root = tmp_path / "libraries"
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(root))
    content_pack.clear_cache()
    store = StudyStore(tmp_path / "learner", time_zone="UTC")
    LearningStore(store)
    service = ContentLibraryService(store, catalogue_ids=lambda: set(CORE_ITEMS),
                                    catalogue_fingerprints=lambda: {cid: item_fingerprint(i) for cid, i in CORE_ITEMS.items()},
                                    catalogue_revisions=lambda: {cid: i["revision"] for cid, i in CORE_ITEMS.items()},
                                    learned_ids=lambda: set(), word_exists=lambda word_id: False)
    yield SimpleNamespace(store=store, service=service, root=root)
    store.close()
    content_pack.clear_cache()


def _library_ids(world):
    return [e["library_id"] for e in world.store.get_setting(rules.REGISTRY_SETTING)["libraries"]]


# ---------------------------------------------------------------- A1 signature and blocking through the service

def test_invalid_signature_is_refused_by_the_service_even_with_accept_missing_references(world):
    service = world.service
    tampered = _rewrite_manifest(_signed(pack_bytes("author.n3", extension_namespaces())), lambda m: {**m, "title": "改过"})
    inspection = service.inspect_bytes(tampered, "n3.zip")
    assert inspection.report.signature.status == "invalid"
    assert inspection.precheck.signature_error and inspection.precheck.blocking_reasons()[0].startswith("签名无效")
    with pytest.raises(ValueError, match="签名无效"):
        service.install(inspection, accept_missing_references=True)
    inspection = service.inspect_bytes(tampered, "n3.zip")
    with pytest.raises(ValueError, match="签名无效"):
        service.install(inspection)
    assert _library_ids(world) == [CORE] and not (world.root / "author.n3").exists()


def test_accept_missing_references_still_skips_only_reference_blocks(world):
    service = world.service
    items = {"grammar:n3:a": {**CORE_ITEMS["grammar:demo:desu"], "id": "grammar:n3:a", "depends_on": ["grammar:nowhere"]}}
    data = pack_bytes("author.n3", {"lesson": {"_ITEMS": items}}, manifest_overrides={"references": [
        {"id": "grammar:nowhere", "kind": "item", "requirement": "required"}]})
    inspection = service.inspect_bytes(data, "n3.zip")
    assert inspection.precheck.missing_required_references == ("grammar:nowhere",)
    with pytest.raises(ValueError, match="必需引用"):
        service.install(inspection)
    entry = service.install(service.inspect_bytes(data, "n3.zip"), accept_missing_references=True)
    assert entry["library_id"] == "author.n3" and entry["source"]["signature_status"] == "unsigned"
    assert "public_key_id" not in entry["source"] and "key_fingerprint" not in entry["source"]


# ---------------------------------------------------------------- A4 rule c through the service precheck

def test_rule_c_same_revision_different_fingerprint_blocks_in_the_service_precheck(world):
    service = world.service
    data = _override_pack(revision="1")  # core MIZU has revision 1 but other content
    inspection = service.inspect_bytes(data, "n3.zip")
    assert inspection.precheck.revision_errors == (MIZU,)
    assert any("同修订号" in reason for reason in inspection.precheck.blocking_reasons())
    with pytest.raises(ValueError, match="同修订号"):
        service.install(inspection, accept_missing_references=True)
    assert _library_ids(world) == [CORE]
    # A changed revision is a normal conflict, not a package error.
    assert service.inspect_bytes(_override_pack(revision="2"), "n3.zip").precheck.revision_errors == ()


def test_rule_c_also_compares_installed_library_revisions(world):
    service = world.service
    first = pack_bytes("author.a", {"lesson": {"_ITEMS": {"grammar:x:a": dict(CORE_ITEMS["grammar:demo:desu"], id="grammar:x:a")}}},
                       manifest_overrides={"revisions": {"grammar:x:a": "1"}})
    service.install(service.inspect_bytes(first, "a.zip"))
    second = pack_bytes("author.b", {"lesson": {"_ITEMS": {"grammar:x:a": dict(CORE_ITEMS["grammar:demo:desu"], id="grammar:x:a",
                                                                           meaning="不同")}}},
                        manifest_overrides={"revisions": {"grammar:x:a": "1"}})
    inspection = service.inspect_bytes(second, "b.zip")
    assert inspection.precheck.revision_errors == ("grammar:x:a",)


# ---------------------------------------------------------------- A2/A3 source fields and TOFU trust

def test_trusting_a_key_makes_the_next_install_with_that_key_valid(world):
    service = world.service
    first = service.inspect_bytes(_signed(pack_bytes("author.n3", extension_namespaces())), "n3.zip")
    assert first.report.signature.status == "untrusted_key" and first.precheck.signature_error is None
    entry = service.install(first, now=NOW)
    assert entry["source"]["signature_status"] == "untrusted_key"
    assert entry["source"]["public_key_id"] == "author:a" and entry["source"]["key_fingerprint"] == signing.key_fingerprint(KEY_A)
    registry = service.trust_signing_key("author:a", KEY_A, now=NOW)
    assert registry["trusted_keys"] == [{"public_key_id": "author:a", "public_key": KEY_A,
                                         "confirmed_at_utc": "2026-11-08T12:00:00+00:00"}]
    assert service.trust_signing_key("author:a", KEY_A, now=NOW)["trusted_keys"] == registry["trusted_keys"]  # no-op
    with pytest.raises(ValueError, match="另一把公钥"):
        service.trust_signing_key("author:a", signing.public_key_from_seed(SEED_B).hex(), now=NOW)
    upgrade = _signed(pack_bytes("author.n3", {**extension_namespaces(), "lesson_extra": {"NOTE": "v2"}},
                                 version="20260201"))
    staged = service.stage_bytes(upgrade, "n3.zip", registry=service.registry())  # worker thread form
    assert staged.report.signature.status == "valid"
    second = service.precheck_staged(staged)
    assert second.report.signature.status == "valid"
    entry = service.install(second, now=NOW)
    assert entry["source"]["signature_status"] == "valid" and entry["library_version"] == "20260201"
    row = {r["library_id"]: r for r in service.libraries()}["author.n3"]
    assert row["signature_status"] == "valid" and row["key_fingerprint"] == signing.key_fingerprint(KEY_A)
    assert row["public_key_id"] == "author:a" and row["installed_at_utc"] == "2026-11-08T12:00:00+00:00"
    assert row["source_sha256"] == entry["source"]["sha256"]


def test_precheck_re_decides_trust_for_a_pack_staged_without_registry(world):
    service = world.service
    service.trust_signing_key("author:a", KEY_A, now=NOW)
    staged = service.stage_bytes(_signed(pack_bytes("author.n3", extension_namespaces())), "n3.zip")
    assert staged.report.signature.status == "untrusted_key"  # no registry on the worker thread
    assert service.precheck_staged(staged).report.signature.status == "valid"


def test_library_rows_carry_source_fields_for_bundled_and_unsigned(world):
    service = world.service
    service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip"), now=NOW)
    rows = {r["library_id"]: r for r in service.libraries()}
    assert rows[CORE]["source_kind"] == "bundled" and rows[CORE]["key_fingerprint"] is None
    assert rows["author.n3"]["key_fingerprint"] is None and rows["author.n3"]["public_key_id"] is None
    assert rows["author.n3"]["installed_at_utc"] == "2026-11-08T12:00:00+00:00"
    assert len(rows["author.n3"]["source_sha256"]) == 64


# ---------------------------------------------------------------- A4 accepted overrides

def test_accepted_overrides_are_written_and_take_effect_after_restart(world):
    service = world.service
    inspection = service.inspect_bytes(_override_pack(), "n3.zip")
    assert inspection.precheck.conflicting_ids == (MIZU,) and inspection.precheck.revision_errors == ()
    with pytest.raises(ValueError, match="overrides"):
        service.install(inspection, accept_overrides=["word:demo:other"])
    assert _library_ids(world) == [CORE]
    service.install(service.inspect_bytes(_override_pack(), "n3.zip"), accept_overrides=True)
    registry = world.store.get_setting(rules.REGISTRY_SETTING)
    assert registry["overrides_accepted"] == [{"library_id": "author.n3", "id": MIZU}]
    content_pack.clear_cache()  # "restart"
    assert content_pack.effective_catalogue().library_of(MIZU) == "author.n3"
    assert content_pack.load_namespace("lesson")["_ITEMS"][MIZU]["meaning"] == "改写的水"
    # An upgrade without the flag keeps the acceptance for ids it still declares.
    service.install(service.inspect_bytes(_override_pack(version="20260301", revision="3"), "n3.zip"))
    assert world.store.get_setting(rules.REGISTRY_SETTING)["overrides_accepted"] == [{"library_id": "author.n3", "id": MIZU}]
    # Removing the library drops its rows; the registry stays valid.
    service.remove("author.n3")
    assert "overrides_accepted" not in world.store.get_setting(rules.REGISTRY_SETTING)


def test_without_acceptance_the_installed_version_keeps_winning(world):
    service = world.service
    service.install(service.inspect_bytes(_override_pack(), "n3.zip"))
    assert "overrides_accepted" not in world.store.get_setting(rules.REGISTRY_SETTING)
    content_pack.clear_cache()
    assert content_pack.effective_catalogue().library_of(MIZU) == CORE
    assert content_pack.load_namespace("lesson")["_ITEMS"][MIZU]["meaning"] == "水"


# ---------------------------------------------------------------- A5 rejected extension -> damaged

class _Catalogue:
    def __init__(self, payloads, core, token="tok"):
        self.payloads, self._core, self.token = payloads, frozenset(core), token
        self.extension_ids = tuple(payloads)
        self.failed, self.fallback = {}, False

    def core_ids(self):
        return self._core

    def library_of(self, content_id):
        return None  # the library list asks who serves an id (EffectiveCatalogue's interface); nothing is served here


def test_extension_rejected_by_the_learning_catalogue_is_reported_damaged(world, monkeypatch):
    ext_item, ext_copy = {"id": "grammar:ext:b"}, {"id": "grammar:ext:b"}
    payloads = {"author.ext": {"grammar_curriculum": {"_ITEMS": {"grammar:ext:b": ext_item}},
                               "usage_grammar_curriculum": {"_ITEMS": {"grammar:ext:b": ext_copy}}},
                "author.other": {"learning_content": {"_WORDS": [{"id": "word:other:x"}]}}}
    catalogue = _Catalogue(payloads, core={"grammar:core:a"})
    layers = tuple(SimpleNamespace(library_id=lid, damaged=False, reason=None) for lid in ("author.ext", "author.other"))
    monkeypatch.setattr(learning_content, "_ITEMS", {"word:other:x": {"id": "word:other:x"}})
    monkeypatch.setattr(learning_content, "_DIAGNOSTICS", [])
    monkeypatch.setattr(content_pack, "_catalogue_rejections", {})
    monkeypatch.setattr(content_pack, "_catalogue_bystanders", {})
    monkeypatch.setattr(content_pack, "library_layers", lambda: layers)
    monkeypatch.setattr(content_pack, "effective_catalogue", lambda: catalogue)
    assert content_pack.library_status() == {"author.ext": {"state": "ready", "reason": None},
                                             "author.other": {"state": "ready", "reason": None}}
    sources = [("grammar_curriculum", [ext_item]), ("usage_grammar_curriculum", [ext_copy])]
    assert learning_content._without_extension_duplicates(sources) == []
    status = content_pack.library_status()
    assert status["author.ext"]["state"] == "damaged" and "重复定义学习 ID（grammar:ext:b）" in status["author.ext"]["reason"]
    # Review B-04: the bystander is only "not loaded" in this process, never damaged.
    assert "author.other" not in status and "一并未加入" in content_pack.library_load_notes()["author.other"]
    # Another merge (another token) never inherits the rejection.
    monkeypatch.setattr(content_pack, "effective_catalogue", lambda: _Catalogue(payloads, core=(), token="other"))
    assert {lid: row["state"] for lid, row in content_pack.library_status().items()} == {
        "author.ext": "ready", "author.other": "ready"}
    # startup_sync persists the damaged state, so availability treats the library as unavailable.
    monkeypatch.setattr(content_pack, "effective_catalogue", lambda: catalogue)
    registry = world.service.registry()
    registry["libraries"].append({"library_id": "author.ext", "library_version": "1", "profile": "extension",
                                  "content_sha256": "a" * 64, "enabled": True,
                                  "installed_at_utc": "2026-11-01T00:00:00+00:00",
                                  "source": {"kind": "file", "name": "x.zip", "sha256": "b" * 64,
                                             "signature_status": "unsigned"},
                                  "path": "author.ext/1", "state": "ready", "summary": {"title": "扩展"}})
    registry["priority"].append("author.ext")
    other = dict(registry["libraries"][-1], library_id="author.other", path="author.other/1",
                 summary={"title": "旁及"})
    registry["libraries"].append(other)
    registry["priority"].append("author.other")
    world.service._write(registry)
    world.service.startup_sync()
    entry = rules.registry_entry(world.store.get_setting(rules.REGISTRY_SETTING), "author.ext")
    assert entry["state"] == "damaged"
    checks = {r["library_id"]: r["check"] for r in world.service.libraries()}
    assert checks["author.ext"].startswith("校验失败，已视为停用：学习目录拒绝了本库内容")
    # Review B-04 path 3: the bystander is never persisted as damaged; the page says why it is not loaded.
    assert rules.registry_entry(world.store.get_setting(rules.REGISTRY_SETTING), "author.other")["state"] == "ready"
    assert checks["author.other"].startswith("本次启动未载入：") and "一并未加入" in checks["author.other"]


# ---------------------------------------------------------------- page wiring (fake UI)

@pytest.fixture
def library_app(learning_app, tmp_path, monkeypatch):
    app = learning_app
    resources = build_core_resources(tmp_path / "resources")
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(tmp_path / "libraries"))
    content_pack.clear_cache()
    app.go_back = Mock()
    service = app.content_library_service()
    service._catalogue_ids = lambda: set(CORE_ITEMS)
    service._catalogue_fingerprints = lambda: {cid: item_fingerprint(i) for cid, i in CORE_ITEMS.items()}
    service._catalogue_revisions = lambda: {cid: i["revision"] for cid, i in CORE_ITEMS.items()}
    yield app
    content_pack.clear_cache()


def _buttons(app):
    return {view.text: view for view in descendants(app.body) if view.on_press}


def _precheck(app, data):
    app.platform.import_library_pack = AsyncMock(return_value=data)
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    assert app.page == "content_library_precheck"
    return text_of(app.body)


def test_precheck_page_offers_accepting_declared_overrides(library_app):
    app = library_app
    _precheck(app, _override_pack())
    name = next(n for n in _buttons(app) if n.startswith("安装并接受本库声明的替代（1 项"))
    assert "安装" in _buttons(app)
    click(app, name)
    registry = app.store.get_setting(rules.REGISTRY_SETTING)
    assert registry["overrides_accepted"] == [{"library_id": "author.n3", "id": MIZU}]
    assert app.page == "content_libraries"


def test_precheck_page_shows_service_rule_c_as_package_error(library_app, monkeypatch):
    app = library_app
    # The page's own core revision lookup finds nothing for synthetic ids; the service's precheck does.
    monkeypatch.setattr(app, "content_library_installed_revisions", lambda ids: {})
    text = _precheck(app, _override_pack(revision="1"))
    assert "包错误：1 项与已安装内容同 ID、同修订号但内容不同，不能安装。" in text
    buttons = _buttons(app)
    assert "安装" not in buttons and not any(n.startswith(("仍然安装", "安装并接受")) for n in buttons)


def test_library_list_reads_source_fields_from_service_rows(library_app, monkeypatch):
    app = library_app
    service = app.content_library_service()
    _precheck(app, pack_bytes("author.n3", extension_namespaces()))
    click(app, "安装")
    real = service.libraries()
    monkeypatch.setattr(service, "libraries", lambda: copy.deepcopy(real))
    monkeypatch.setattr(service, "registry", Mock(side_effect=AssertionError("page must not read the registry")))
    monkeypatch.setattr(service, "pending_restart", lambda: [])
    app.show_content_libraries()
    text = text_of(app.body)
    assert "来源：导入文件 导入文件 · 导入时间 " in text and "签名：未签名" in text
    assert f"sha256 {real[-1]['source_sha256'][:12]}" in text
