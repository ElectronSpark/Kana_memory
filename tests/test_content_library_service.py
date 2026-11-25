"""Registry, install/enable/remove, startup reconciliation and layered loading."""
import hashlib
import io
import json
import zipfile

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_library_service as service_module
from kanamemory import content_pack
from kanamemory.content_availability import Catalogue, build_view
from kanamemory.content_library_service import ContentLibraryService
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore, validate_backup_rows
from support.content_library_support import CORE_ITEMS, build_core_resources, extension_namespaces, pack_bytes
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)


@pytest.fixture
def world(tmp_path, monkeypatch):
    resources = build_core_resources(tmp_path / "resources")
    root = tmp_path / "libraries"
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(root))
    content_pack.clear_cache()
    store = StudyStore(tmp_path / "learner", time_zone="UTC")
    LearningStore(store)
    learned = set()
    state = {"catalogue": lambda: set(CORE_ITEMS), "learned": lambda: learned}
    from kanamemory.material_review import item_fingerprint

    def availability():
        # The home page's projection over the synthetic catalogue and learned rows (review B-11).
        return build_view(Catalogue(ids=frozenset(state["catalogue"]()), providers=state.get("providers", {})),
                          store.get_setting(rules.REGISTRY_SETTING, None), [{"content_id": cid} for cid in sorted(learned)])
    service = ContentLibraryService(store, catalogue_ids=state["catalogue"],
                                    catalogue_fingerprints=lambda: {cid: item_fingerprint(i) for cid, i in CORE_ITEMS.items()},
                                    learned_ids=state["learned"], word_exists=lambda word_id: False,
                                    availability=availability)
    yield {"store": store, "service": service, "root": root, "resources": resources, "learned": learned, "state": state}
    store.close()
    content_pack.clear_cache()


def test_first_start_writes_core_only_registry_and_mirror(world):
    service, store, root = world["service"], world["store"], world["root"]
    assert store.get_setting(rules.REGISTRY_SETTING) is None
    registry = service.registry()
    assert registry == rules.default_registry()
    assert store.get_setting(rules.REGISTRY_SETTING) == registry
    assert json.loads((root / rules.MIRROR_FILE).read_text("utf-8")) == registry
    rows = service.libraries()
    assert len(rows) == 1 and rows[0]["is_core"] and not rows[0]["removable"] and rows[0]["families"] == {"grammar": 1, "word": 1}
    assert len(service.availability().orphaned_ids) == 0
    assert not service.root_mismatch()


def test_install_enable_remove_round_trip_keeps_progress_and_files(world):
    service, store, root, learned = world["service"], world["store"], world["root"], world["learned"]
    learned.update({"grammar:demo:desu", "grammar:ext:masu"})
    inspection = service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip")
    assert inspection.precheck.new_item_count == 1 and inspection.precheck.blocking_reasons() == ()
    staging = inspection.staging
    assert staging.is_dir()
    entry = service.install(inspection, now=None)
    assert not staging.exists()
    assert entry["state"] == "pending_restart" and entry["enabled"]
    assert entry["path"] == "author.n3/20260102." + entry["content_sha256"][:8]  # review B-03: never an in-use folder
    folder = root / entry["path"]
    assert (folder / "manifest.json").is_file() and (folder / "teaching/lesson.json").is_file()
    registry = store.get_setting(rules.REGISTRY_SETTING)
    assert registry["priority"] == [rules.CORE_LIBRARY_ID, "author.n3"]
    rows = {row["library_id"]: row for row in service.libraries()}
    assert rows["author.n3"]["title"] == "测试扩展库" and rows["author.n3"]["check"] == "重启后生效"
    assert rows["author.n3"]["learned"] == 1 and rows["author.n3"]["families"] == {"grammar": 1}
    # The item is not yet in this process's catalogue: it counts as orphaned until restart.
    assert rows["author.n3"]["orphaned"] == 1 and len(service.availability().orphaned_ids) == 1
    with pytest.raises(ValueError, match="已经安装"):
        service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip"))
    service.set_enabled("author.n3", False)
    assert rules.registry_entry(service.registry(), "author.n3")["enabled"] is False
    assert {row["library_id"]: row["orphaned"] for row in service.libraries()}["author.n3"] == 1
    with pytest.raises(ValueError, match="核心库"):
        service.set_enabled(rules.CORE_LIBRARY_ID, False)
    with pytest.raises(ValueError, match="不能移除"):
        service.remove(rules.CORE_LIBRARY_ID)
    assert "已学项 1 个将变为孤儿" in service.removal_notice("author.n3")
    service.remove("author.n3")
    # Review B-03: the folder stays for this process; the next start deletes it.
    assert (folder / "teaching/lesson.json").is_file()
    assert service.registry()["pending_delete"] == ["author.n3"]
    assert [e["library_id"] for e in service.registry()["libraries"]] == [rules.CORE_LIBRARY_ID]
    content_pack.clear_cache()  # "restart"
    service.startup_sync()
    assert not (root / "author.n3").exists() and "pending_delete" not in service.registry()
    assert learned == {"grammar:demo:desu", "grammar:ext:masu"}


@pytest.mark.parametrize("kwargs", [
    dict(extra_files={"teaching/evil.py": b"import os"}),
    dict(corrupt="teaching/route.json"),
    dict(manifest_overrides={"profile": "core"}),
])
def test_bad_packs_are_refused_without_leaving_staging_or_registry_changes(world, kwargs):
    service, root = world["service"], world["root"]
    before = service.registry()
    with pytest.raises(ValueError):
        service.inspect_bytes(pack_bytes("author.n3", extension_namespaces(), **kwargs), "bad.zip")
    assert [p.name for p in root.iterdir()] == [rules.MIRROR_FILE]
    assert service.registry() == before


def test_a_pack_made_for_a_newer_app_is_refused_with_the_version_it_needs(world, monkeypatch):
    service, root = world["service"], world["root"]
    before = service.registry()

    def pack(version):
        return pack_bytes("author.n3", extension_namespaces(), manifest_overrides={
            "requires": {"teaching_format": 1, "namespace_version": 1, "app_min_version": version}})
    monkeypatch.setattr(service_module, "APP_VERSION", "0.35.0")
    with pytest.raises(rules.ContentLibraryError) as refused:
        service.inspect_bytes(pack("0.36.0"), "newer.zip")
    assert str(refused.value) == "这个内容库需要 0.36.0 或更新版本的应用（当前 0.35.0）。请先更新应用，再导入。"
    assert [p.name for p in root.iterdir()] == [rules.MIRROR_FILE] and service.registry() == before
    # the same or an older requirement passes, and so does text that cannot be compared (older packs)
    for version in ("0.35.0", "0.35", "0.9", "the next one"):
        inspection = service.inspect_bytes(pack(version), "fits.zip")
        assert inspection.report.manifest["requires"]["app_min_version"] == version
        service.discard(inspection)
    # the running app's own version is the one compared
    from kanamemory.build_info import VERSION
    monkeypatch.undo()
    assert service_module.APP_VERSION == VERSION and rules.app_version_key(VERSION) is not None


def test_an_oversized_pack_is_refused_with_the_limit_that_is_applied(world, monkeypatch):
    # The message names the limit actually used (it once said 256 MiB while 512 MiB was applied).
    assert rules.PACK_TOO_LARGE == f"内容库文件超过 {rules.MAX_PACK_BYTES // (1024 * 1024)} MiB 限制。"
    assert "512 MiB" in rules.PACK_TOO_LARGE
    service, root = world["service"], world["root"]
    data = pack_bytes("author.n3", extension_namespaces())
    monkeypatch.setattr(rules, "MAX_PACK_BYTES", len(data) - 1)
    with pytest.raises(ValueError) as refused:
        service.stage_bytes(data, "big.zip")
    assert str(refused.value) == rules.PACK_TOO_LARGE
    assert not root.exists() or [p.name for p in root.iterdir()] == [rules.MIRROR_FILE]
    monkeypatch.setattr(rules, "MAX_PACK_BYTES", len(data))
    service.discard(service.stage_bytes(data, "fits.zip"))


def test_learner_tables_in_a_bundled_database_are_refused(world, tmp_path):
    import sqlite3
    path = tmp_path / "learner.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE study_card(id TEXT)")
    data = pack_bytes("author.n3", extension_namespaces(), extra_files={"annotations/notes.sqlite3": path.read_bytes()},
                      manifest_overrides={})
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    manifest["layers"]["annotations"] = ["annotations/notes.sqlite3"]
    data = pack_bytes("author.n3", extension_namespaces(), extra_files={"annotations/notes.sqlite3": path.read_bytes()},
                      manifest_overrides={"layers": manifest["layers"]})
    with pytest.raises(ValueError, match="Learner state"):
        world["service"].inspect_bytes(data, "bad.zip")


def test_restart_merges_extension_into_effective_catalogue_and_reconciles_states(world):
    service, store = world["service"], world["store"]
    assert content_pack.load_namespace("lesson")["CONTENT_REVISION"] == 1
    service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces(conflict=True)), "n3.zip"))
    # Same process: nothing changes until restart.
    assert set(content_pack.load_namespace("lesson")["_ITEMS"]) == set(CORE_ITEMS)
    content_pack.clear_cache()  # "restart"
    lesson = content_pack.load_namespace("lesson")
    assert set(lesson["_ITEMS"]) == set(CORE_ITEMS) | {"grammar:ext:masu"}
    assert lesson["_ITEMS"]["grammar:demo:desu"]["meaning"] == "是" and lesson["CONTENT_REVISION"] == 1
    assert content_pack.load_namespace("route")["STAGES"] == ["intro"]
    assert set(content_pack.load_namespace("route")["WORD_TOPICS"]) == {"basic", "ext"}
    report = content_pack.library_merge_report()
    assert ["_ITEMS", "grammar:demo:desu", rules.CORE_LIBRARY_ID, "author.n3"] in [list(c) for c in report["lesson"]["conflicts"]]
    assert content_pack.library_status() == {"author.n3": {"state": "ready", "reason": None}}
    assert content_pack.load_namespace("absent", {"x": 1}) == {"x": 1}
    summary = service.startup_sync()
    assert summary == {"root_mismatch": False, "pending_restart": False}
    assert rules.registry_entry(service.registry(), "author.n3")["state"] == "ready"
    # Disable, restart: the extension no longer contributes and progress rows are untouched.
    service.set_enabled("author.n3", False)
    content_pack.clear_cache()
    assert set(content_pack.load_namespace("lesson")["_ITEMS"]) == set(CORE_ITEMS)
    world["learned"].add("grammar:ext:masu")
    assert service.availability().orphaned_ids == {"grammar:ext:masu"}
    service.startup_sync()
    assert rules.registry_entry(service.registry(), "author.n3")["state"] == "ready"
    service.set_enabled("author.n3", True)
    content_pack.clear_cache()
    assert "grammar:ext:masu" in content_pack.load_namespace("lesson")["_ITEMS"]
    assert store.get_setting(rules.REGISTRY_SETTING)["libraries"][1]["enabled"] is True


def test_damaged_library_is_excluded_and_marked_without_touching_progress(world):
    service, root = world["service"], world["root"]
    entry = service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip"))
    target = root / entry["path"] / "teaching/lesson.json"
    target.write_bytes(target.read_bytes() + b" ")
    content_pack.clear_cache()
    assert set(content_pack.load_namespace("lesson")["_ITEMS"]) == set(CORE_ITEMS)
    assert content_pack.library_status()["author.n3"]["state"] == "damaged"
    service.startup_sync()
    entry = rules.registry_entry(service.registry(), "author.n3")
    assert entry["state"] == "damaged"
    assert {row["library_id"]: row["check"] for row in service.libraries()}["author.n3"].startswith("校验失败")
    # Reinstalling the same version writes a fresh folder next to the damaged one.
    fresh = service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip"))
    assert fresh["path"] != entry["path"] and target.is_file()
    content_pack.clear_cache()
    assert content_pack.library_status()["author.n3"]["state"] == "ready"
    service.startup_sync()  # ready: the superseded damaged folder is deleted now
    assert not (root / entry["path"]).exists() and (root / fresh["path"]).is_dir()


def test_core_pack_replaces_bundled_teaching_and_lexicon_paths(world, tmp_path, monkeypatch):
    from package_library_pack import package
    service, resources = world["service"], world["resources"]
    output = tmp_path / "core.zip"
    result = package(resources, output)
    assert result["library_id"] == rules.CORE_LIBRARY_ID and result["items"] == 2 and result["namespaces"] == 2
    inspection = service.inspect_bytes(output.read_bytes(), "core.zip")
    assert inspection.precheck.replaces_core and inspection.report.has_teaching_pack and inspection.report.has_lexicon
    entry = service.install(inspection)
    assert entry["library_id"] == rules.CORE_LIBRARY_ID and entry["source"]["kind"] == "file"
    assert entry["path"].startswith("kanamemory.core/20260101.")
    monkeypatch.delenv("KANAMEMORY_TEACHING_PACK")
    content_pack.clear_cache()
    assert content_pack.pack_path() == world["root"] / entry["path"] / "teaching/teaching_content.sqlite3"
    assert content_pack.lexicon_path() == world["root"] / entry["path"] / "lexicon/content.sqlite3"
    assert set(content_pack.load_namespace("lesson")["_ITEMS"]) == set(CORE_ITEMS)
    assert content_pack.validate_installed_pack()["profile"] == "demo"
    assert "恢复为内置核心库" in service.removal_notice(rules.CORE_LIBRARY_ID)
    service.remove(rules.CORE_LIBRARY_ID)
    assert rules.registry_entry(service.registry(), rules.CORE_LIBRARY_ID)["source"]["kind"] == "bundled"
    content_pack.clear_cache()
    assert content_pack.pack_path().name == "teaching_content.sqlite3" and "libraries" not in content_pack.pack_path().parts


def _with_namespace_json(pack, namespaces):
    """The same core pack in the form built before 2026-11-18: each namespace also as teaching/<name>.json."""
    with zipfile.ZipFile(pack) as archive:
        manifest = json.loads(archive.read(rules.MANIFEST_FILE))
        entries = {name: archive.read(name) for name in manifest["files"]}
    for name, value in namespaces.items():
        raw = json.dumps(content_pack.namespace_document(name, value), ensure_ascii=False).encode("utf-8")
        path = "teaching/" + name + ".json"
        entries[path] = raw
        manifest["files"][path] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        manifest["layers"]["teaching"].append(path)
    manifest["content_sha256"] = rules.content_digest(manifest["files"])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(rules.MANIFEST_FILE, json.dumps(manifest, ensure_ascii=False))
        for name, raw in entries.items():
            archive.writestr(name, raw)
    return buffer.getvalue()


def test_a_core_pack_of_the_older_form_with_namespace_json_still_installs_and_serves_its_database(world, tmp_path,
                                                                                                   monkeypatch):
    from package_library_pack import package
    from support.content_library_support import CORE_NAMESPACES
    service, root = world["service"], world["root"]
    package(world["resources"], tmp_path / "core.zip")
    data = _with_namespace_json(tmp_path / "core.zip", CORE_NAMESPACES)
    inspection = service.inspect_bytes(data, "core-old.zip")
    assert [name for name, _ in inspection.report.teaching_documents] == ["lesson", "route"]
    assert inspection.report.has_teaching_pack and inspection.precheck.replaces_core
    assert inspection.precheck.blocking_reasons() == ()
    entry = service.install(inspection)
    assert (root / entry["path"] / "teaching" / "lesson.json").is_file()
    monkeypatch.delenv("KANAMEMORY_TEACHING_PACK")
    content_pack.clear_cache()
    service.startup_sync()
    assert content_pack.pack_path() == root / entry["path"] / "teaching/teaching_content.sqlite3"
    assert set(content_pack.load_namespace("lesson")["_ITEMS"]) == set(CORE_ITEMS)
    assert content_pack.library_status() == {rules.CORE_LIBRARY_ID: {"state": "ready", "reason": None}}


def test_a_core_pack_without_namespace_json_installs_without_documents_or_indexes(world, tmp_path, monkeypatch):
    from package_library_pack import package
    service, root = world["service"], world["root"]
    package(world["resources"], tmp_path / "core.zip")
    inspection = service.inspect_bytes((tmp_path / "core.zip").read_bytes(), "core.zip")
    assert inspection.report.teaching_documents == () and inspection.report.manifest["provides"]["namespaces"] == [
        "lesson", "route"]
    entry = service.install(inspection)
    folder = root / entry["path"]
    assert sorted(p.name for p in (folder / "teaching").iterdir()) == ["teaching_content.sqlite3"]
    assert not (folder / content_pack.INDEX_DIRECTORY).exists()
    monkeypatch.delenv("KANAMEMORY_TEACHING_PACK")
    content_pack.clear_cache()
    service.startup_sync()
    assert content_pack.pack_path() == folder / "teaching/teaching_content.sqlite3"
    assert content_pack.load_namespace("route")["STAGES"] == ["intro"]
    assert content_pack.library_status() == {rules.CORE_LIBRARY_ID: {"state": "ready", "reason": None}}


def test_backup_rows_validate_registry_shape(world):
    store, service = world["store"], world["service"]
    service.registry()
    validate_backup_rows(store._db)
    broken = rules.default_registry()
    broken["libraries"][0]["enabled"] = False
    store.set_setting(rules.REGISTRY_SETTING, broken)
    with pytest.raises(ValueError):
        validate_backup_rows(store._db)
    store.set_setting(rules.REGISTRY_SETTING, rules.default_registry())
    validate_backup_rows(store._db)


def test_root_mismatch_is_reported_not_guessed(world, tmp_path):
    service = ContentLibraryService(world["store"], root=tmp_path / "elsewhere", catalogue_ids=lambda: set(),
                                    learned_ids=lambda: set())
    assert service.root_mismatch()
    assert service.startup_sync()["root_mismatch"] is True


def _installed_core(world, tmp_path, monkeypatch):
    from package_library_pack import package
    service = world["service"]
    output = tmp_path / "core.zip"
    package(world["resources"], output)
    entry = service.install(service.inspect_bytes(output.read_bytes(), "core.zip"))
    monkeypatch.delenv("KANAMEMORY_TEACHING_PACK")
    content_pack.clear_cache()  # "restart" onto the installed core pack
    service.startup_sync()
    return entry


def test_removed_library_stays_readable_in_this_process_and_is_deleted_at_next_start(world, tmp_path, monkeypatch):
    """Review B-03: remove only changes the registry; the folder goes at the next startup_sync."""
    service, root = world["service"], world["root"]
    entry = _installed_core(world, tmp_path, monkeypatch)
    folder = root / entry["path"]
    assert content_pack.pack_path() == folder / "teaching/teaching_content.sqlite3"
    service.remove(rules.CORE_LIBRARY_ID)
    assert service.registry()["pending_delete"] == [rules.CORE_LIBRARY_ID]
    # Same process: the v2 shards of the installed pack are still read lazily from that folder.
    assert content_pack.pack_path() == folder / "teaching/teaching_content.sqlite3"
    assert set(content_pack.load_namespace("lesson")["_ITEMS"]) == set(CORE_ITEMS)
    assert content_pack.load_namespace("route")["STAGES"] == ["intro"]
    service.startup_sync()  # this process still reads it: kept and still listed
    assert folder.is_dir() and service.registry()["pending_delete"] == [rules.CORE_LIBRARY_ID]
    content_pack.clear_cache()  # "restart"
    service.startup_sync()
    assert not (root / rules.CORE_LIBRARY_ID).exists() and "pending_delete" not in service.registry()
    assert "libraries" not in content_pack.pack_path().parts


def test_a_removed_library_is_a_change_pending_restart_until_the_process_restarts(world):
    """Removal leaves no pending_restart entry, yet this process still serves the library: the page says so.

    On Android leaving the app and coming back keeps the process; the App's start-up runs again in it
    (startup_sync without a new content_pack), and the change is still pending then.
    """
    service = world["service"]
    service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip"), now=None)
    content_pack.clear_cache()  # restart
    service.startup_sync()
    assert "author.n3" in content_pack.library_status() and not service.pending_restart()
    service.remove("author.n3")
    assert not rules.pending_restart(service.registry())  # no entry is marked: the comparison finds it
    assert service.pending_restart()
    service.startup_sync()  # the same process started its App again
    assert service.pending_restart()
    content_pack.clear_cache()  # a real restart
    service.startup_sync()
    assert not service.pending_restart()


def test_reinstall_after_remove_keeps_the_new_folder(world):
    service, root = world["service"], world["root"]
    first = service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip"))
    service.remove("author.n3")
    second = service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip"))
    assert second["path"] != first["path"] and (root / first["path"]).is_dir()  # the old folder is never overwritten
    assert "pending_delete" not in service.registry()
    content_pack.clear_cache()
    service.startup_sync()
    assert (root / second["path"] / "manifest.json").is_file() and not (root / first["path"]).exists()


def test_damaged_library_recovers_at_start_after_its_files_pass_again(world):
    """Review B-04 path 1: an enabled damaged library is checked again at startup."""
    service, root = world["service"], world["root"]
    entry = service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip"))
    target = root / entry["path"] / "teaching/lesson.json"
    raw = target.read_bytes()
    target.write_bytes(raw.replace(b"masu", b"masx"))  # a transient checksum failure (e.g. a sync tool mid-write)
    content_pack.clear_cache()
    service.startup_sync()
    assert rules.registry_entry(service.registry(), "author.n3")["state"] == "damaged"
    content_pack.clear_cache()
    service.startup_sync()  # still broken: stays damaged
    assert rules.registry_entry(service.registry(), "author.n3")["state"] == "damaged"
    target.write_bytes(raw)
    content_pack.clear_cache()
    assert "author.n3" not in content_pack.library_status()  # not loaded in this process
    service.startup_sync()
    assert rules.registry_entry(service.registry(), "author.n3")["state"] == "ready"
    content_pack.clear_cache()
    assert content_pack.library_status()["author.n3"]["state"] == "ready"
    assert "grammar:ext:masu" in content_pack.load_namespace("lesson")["_ITEMS"]


def test_enabling_a_damaged_library_retries_it_after_restart(world):
    """Review B-04 path 2: set_enabled(True) turns damaged into pending_restart."""
    service = world["service"]
    service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip"))
    registry = service.registry()
    rules.registry_entry(registry, "author.n3")["state"] = "damaged"
    service._write(registry)
    assert service.set_enabled("author.n3", True)["state"] == "pending_restart"  # enabled already: a retry
    registry = service.registry()
    rules.registry_entry(registry, "author.n3")["state"] = "damaged"
    service._write(registry)
    assert service.set_enabled("author.n3", False)["state"] == "damaged"
    assert service.set_enabled("author.n3", True)["state"] == "pending_restart"
    content_pack.clear_cache()
    service.startup_sync()
    assert rules.registry_entry(service.registry(), "author.n3")["state"] == "ready"


def test_trial_merge_refuses_a_too_deep_chain(world, monkeypatch):
    from kanamemory import content_library_merge as merge
    chain = {f"grammar:ext:c{n}": dict(CORE_ITEMS["grammar:demo:desu"], id=f"grammar:ext:c{n}",
                                       depends_on=[f"grammar:ext:c{n - 1}"] if n else [])
             for n in reversed(range(8))}
    original = merge.offline_merge_problems  # a small limit stands in for MAX_DEPENDENCY_DEPTH
    monkeypatch.setattr(merge, "offline_merge_problems", lambda **kwargs: original(**kwargs, max_depth=4))
    inspection = world["service"].inspect_bytes(pack_bytes("author.deep", {"lesson": {"_ITEMS": chain}}), "deep.zip")
    assert any("过深" in reason for reason in inspection.precheck.blocking_reasons())
    with pytest.raises(ValueError, match="过深"):
        world["service"].install(inspection, accept_missing_references=True)
    assert rules.registry_entry(world["service"].registry(), "author.deep") is None


def test_installed_manifest_is_read_through_a_link_above_the_library_root(world, monkeypatch):
    # Android reaches the app's files directory through the link /data/user/0: with every ancestor checked,
    # no installed manifest could be read there (native8: "已学 0 项", an update compared against nothing).
    from pathlib import Path
    service, root, learned = world["service"], world["root"], world["learned"]
    entry = service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip"))
    service.set_enabled("author.n3", False)  # a disabled library is read the same way
    entry = rules.registry_entry(service.registry(), "author.n3")
    learned.add("grammar:ext:masu")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == root.parent or original(path))
    service._manifests.clear()
    assert service.installed_manifest(entry)["provides"]["items"] == ["grammar:ext:masu"]
    assert {row["library_id"]: row["learned"] for row in service.libraries()}["author.n3"] == 1
    assert "已学项 1 个将变为孤儿" in service.removal_notice("author.n3")
    # A link among the library's own folders is still refused.
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == root / "author.n3" or original(path))
    service._manifests.clear()
    assert service.installed_manifest(entry) is None


def test_core_row_counts_only_the_items_no_library_provides(world):
    # The process catalogue holds the loaded extensions' items too (native8: the core row showed 语法 425
    # with a library enabled and 324 with it disabled).
    service, state, learned = world["service"], world["state"], world["learned"]
    service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip"))
    before = {row["library_id"]: row for row in service.libraries()}[rules.CORE_LIBRARY_ID]
    state["catalogue"] = lambda: set(CORE_ITEMS) | {"grammar:ext:masu"}  # after the restart
    state["providers"] = {"grammar:ext:masu": "author.n3"}
    learned.update({"grammar:demo:desu", "grammar:ext:masu"})
    rows = {row["library_id"]: row for row in service.libraries()}
    core, extension = rows[rules.CORE_LIBRARY_ID], rows["author.n3"]
    assert core["families"] == before["families"] == {"grammar": 1, "word": 1} and core["item_count"] == len(CORE_ITEMS)
    assert core["learned"] == 1 and extension["learned"] == 1 and extension["families"] == {"grammar": 1}


@pytest.mark.parametrize("loaded", [True, False])
def test_an_update_is_compared_with_other_libraries_not_with_its_own_old_version(world, loaded):
    # The process catalogue stood in for "what stays installed": with the old version loaded the pack conflicted
    # with itself (0.34.0 audit U1), with it switched off every item counted as new (native8: 新增 1388).
    from kanamemory.material_review import item_fingerprint
    service, state = world["service"], world["state"]
    old = extension_namespaces(conflict=True)  # grammar:ext:masu and a changed copy of the core's grammar:demo:desu
    service.install(service.inspect_bytes(pack_bytes("author.n3", old, manifest_overrides={
        "revisions": {"grammar:ext:masu": "1"}}), "n3.zip"))
    if loaded:  # after the restart: the catalogue serves the library's own item; the core's desu stays the core's
        state["catalogue"] = lambda: set(CORE_ITEMS) | {"grammar:ext:masu"}
        state["providers"] = {"grammar:ext:masu": "author.n3"}
        service._catalogue_fingerprints = lambda: {
            **{cid: item_fingerprint(item) for cid, item in CORE_ITEMS.items()},
            "grammar:ext:masu": item_fingerprint(old["lesson"]["_ITEMS"]["grammar:ext:masu"])}
    else:
        service.set_enabled("author.n3", False)
    new = extension_namespaces(conflict=True)
    new["lesson"]["_ITEMS"]["grammar:ext:masu"] = dict(new["lesson"]["_ITEMS"]["grammar:ext:masu"], revision=2, meaning="敬体（修订）")
    new["lesson"]["_ITEMS"]["grammar:ext:new"] = dict(new["lesson"]["_ITEMS"]["grammar:ext:masu"], id="grammar:ext:new")
    inspection = service.inspect_bytes(pack_bytes("author.n3", new, version="20260201", manifest_overrides={
        "revisions": {"grammar:ext:masu": "2", "grammar:ext:new": "1"}}), "n3.zip")
    check = inspection.precheck
    assert check.replaces_version == "20260102" and check.new_item_count == 1  # only grammar:ext:new is new
    # The core item of the same id is still a conflict; the library's own changed item is not.
    assert check.conflicting_ids == ("grammar:demo:desu",) and check.undeclared_overrides == ("grammar:demo:desu",)
    assert check.same_fingerprint_ids == () and check.revision_errors == () and check.blocking_reasons() == ()
    assert [warning for warning in check.warnings if "同 ID 但内容不同" in warning] == [
        "1 项与已安装内容同 ID 但内容不同，且未在 overrides 中声明；按优先级以已安装库为准"]
    service.discard(inspection)
    # Rule c against the replaced version, loaded or not: changed content under the same revision is a package error.
    stale = service.inspect_bytes(pack_bytes("author.n3", new, version="20260202", manifest_overrides={
        "revisions": {"grammar:ext:masu": "1", "grammar:ext:new": "1"}}), "n3.zip")
    assert stale.precheck.revision_errors == ("grammar:ext:masu",) and stale.precheck.blocking_reasons()
    with pytest.raises(ValueError, match="包错误"):
        service.install(stale)


# --- after any start no pending change is left (owner report: the note stayed after removing a core pack) ---

def test_after_install_start_remove_start_nothing_is_pending(world, tmp_path, monkeypatch):
    """Removing an imported core pack writes the bundled entry as pending_restart; the next start must settle it."""
    service = world["service"]
    _installed_core(world, tmp_path, monkeypatch)  # install, then a start that loads it
    assert not service.pending_restart()
    service.remove(rules.CORE_LIBRARY_ID)
    assert service.pending_restart()  # this process still serves the pack
    content_pack.clear_cache()  # a real restart: the bundled content is loaded
    service.startup_sync()
    assert rules.registry_entry(service.registry(), rules.CORE_LIBRARY_ID)["state"] == "ready"
    assert not service.pending_restart()


def test_a_leftover_folder_never_keeps_a_change_pending(world, tmp_path, monkeypatch):
    service, root = world["service"], world["root"]
    entry = _installed_core(world, tmp_path, monkeypatch)
    service.remove(rules.CORE_LIBRARY_ID)
    content_pack.clear_cache()

    def locked(root_, target):
        raise OSError("locked")
    monkeypatch.setattr(service_module, "_remove_tree", locked)
    service.startup_sync()
    assert (root / entry["path"]).is_dir() and service.registry()["pending_delete"] == [rules.CORE_LIBRARY_ID]
    assert not service.pending_restart()


def test_a_library_this_start_did_not_load_is_not_left_pending(world, monkeypatch):
    """A start settles every saved change it started with, loaded or not (the row then says why it is not loaded)."""
    service = world["service"]
    service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip"))
    content_pack.clear_cache()
    monkeypatch.setattr(content_pack, "library_status", lambda: {})  # e.g. left out by a merge fallback
    service.startup_sync()
    assert not service.pending_restart()
    assert [row["check"] for row in service.libraries() if row["library_id"] == "author.n3"][0].startswith("本次启动未载入")


# --- design 08 section 5.7: newer bundled content wins over an older imported core pack -----------------------

def _stamp(world, monkeypatch, version, scheme="date", sha=None):
    """The bundled resources of this world carry a version stamp (the app was updated to that content)."""
    resources = world["resources"]
    monkeypatch.setattr(content_pack, "_resources", lambda: resources)
    monkeypatch.setattr(content_pack, "bundled_teaching", lambda: (resources, resources / "teaching_content.sqlite3"))
    sha = sha or content_pack.validate_pack(resources / "teaching_content.sqlite3")["content_sha256"]
    stamp = rules.make_bundled_stamp(version, scheme, "0" * 64 if sha == "stale" else sha)
    (resources / rules.BUNDLED_STAMP_FILE).write_text(json.dumps(stamp), encoding="utf-8")


def _core_pack(world, tmp_path, version):
    from package_library_pack import package
    output = tmp_path / f"core-{version}.zip"
    package(world["resources"], output, library_version=version)
    return output.read_bytes()


def _start_on_core_pack(world, tmp_path, monkeypatch, version, *, extension=False):
    service = world["service"]
    if extension:
        service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.zip"))
    entry = service.install(service.inspect_bytes(_core_pack(world, tmp_path, version), "core.zip"))
    monkeypatch.delenv("KANAMEMORY_TEACHING_PACK")
    content_pack.clear_cache()
    service.startup_sync()
    assert rules.registry_entry(service.registry(), rules.CORE_LIBRARY_ID)["source"]["kind"] == "file"
    return entry


@pytest.mark.parametrize("pack, bundled", [("20261119.3", "20261121"), ("20261121", "20261121")])
def test_a_start_replaces_an_older_or_equal_core_pack_by_the_stamped_bundled_content(world, tmp_path, monkeypatch,
                                                                                      pack, bundled):
    service, root = world["service"], world["root"]
    entry = _start_on_core_pack(world, tmp_path, monkeypatch, pack, extension=True)
    _stamp(world, monkeypatch, bundled)  # the app update brings content of that day
    content_pack.clear_cache()  # the next start
    assert rules.registry_entry(content_pack.active_registry(), rules.CORE_LIBRARY_ID)["source"]["kind"] == "bundled"
    assert content_pack.pack_path() == world["resources"] / "teaching_content.sqlite3"  # nothing of the pack loaded
    service.startup_sync()
    registry = service.registry()
    core = rules.registry_entry(registry, rules.CORE_LIBRARY_ID)
    # The removal's steps, ready at once; the bundled entry stores the stamp's version (section 16.10).
    assert core == rules.core_entry(content_pack.bundled_stamp()) and core["library_version"] == bundled
    assert rules.registry_entry(registry, "author.n3")["source"]["kind"] == "file"  # extensions untouched
    assert not (root / entry["path"]).exists() and "pending_delete" not in registry  # nothing read it: deleted
    assert service.bundled_notice() == {"replaced_version": pack, "bundled_version": bundled}
    assert not service.pending_restart()
    content_pack.clear_cache()
    service.startup_sync()  # the start after: the notice stays until the next library change
    assert service.bundled_notice() is not None and not service.pending_restart()
    service.set_enabled("author.n3", False)
    assert service.bundled_notice() is None


def test_a_folder_that_cannot_be_deleted_stays_listed_for_a_later_start(world, tmp_path, monkeypatch):
    service, root = world["service"], world["root"]
    entry = _start_on_core_pack(world, tmp_path, monkeypatch, "20261119.3")
    _stamp(world, monkeypatch, "20261121")
    content_pack.clear_cache()
    monkeypatch.setattr(service_module, "_remove_tree", lambda root_, target: (_ for _ in ()).throw(OSError("locked")))
    service.startup_sync()
    assert (root / entry["path"]).is_dir() and service.registry()["pending_delete"] == [rules.CORE_LIBRARY_ID]
    assert not service.pending_restart()


@pytest.mark.parametrize("pack, bundled, scheme", [
    ("20261122", "20261121", "date"),        # the pack is newer
    ("1.0.0", "20261121", "date"),           # the pack's scheme is not "date": not comparable
    ("20261119.3", "20261121", "semver"),    # the stamp's scheme is not "date": not comparable
    ("20261119.3", "20261121", "stale"),     # a stamp of other content: ignored
    ("20261119.3", None, None),              # no stamp
])
def test_a_start_keeps_a_newer_or_not_comparable_core_pack(world, tmp_path, monkeypatch, pack, bundled, scheme):
    service, root = world["service"], world["root"]
    entry = _start_on_core_pack(world, tmp_path, monkeypatch, pack)
    if bundled is not None:
        _stamp(world, monkeypatch, bundled, *(("date", "stale") if scheme == "stale" else (scheme,)))
    else:
        monkeypatch.setattr(content_pack, "_resources", lambda: world["resources"])
        monkeypatch.setattr(content_pack, "bundled_teaching",
                            lambda: (world["resources"], world["resources"] / "teaching_content.sqlite3"))
    before = service.registry()
    content_pack.clear_cache()
    service.startup_sync()
    assert service.registry() == before and (root / entry["path"]).is_dir()
    assert content_pack.pack_path() == root / entry["path"] / "teaching/teaching_content.sqlite3"
    assert service.bundled_notice() is None and not service.pending_restart()


@pytest.mark.parametrize("pack, refused", [("20261119.3", "旧"), ("20261121", "相同"), ("20261122", None)])
def test_precheck_of_a_core_pack_against_the_stamped_bundled_content(world, tmp_path, monkeypatch, pack, refused):
    service = world["service"]
    _stamp(world, monkeypatch, "20261121")
    inspection = service.inspect_bytes(_core_pack(world, tmp_path, pack), "core.zip")
    error = inspection.precheck.bundled_error
    if refused is None:
        assert error is None and inspection.precheck.blocking_reasons() == ()
        service.install(inspection)
        return
    assert refused in error and pack in error and error in inspection.precheck.blocking_reasons()
    with pytest.raises(ValueError, match=refused):
        service.install(inspection, accept_missing_references=True)
    assert rules.registry_entry(service.registry(), rules.CORE_LIBRARY_ID)["source"]["kind"] == "bundled"


def test_the_core_row_shows_the_bundled_version_only_with_a_valid_stamp(world, monkeypatch):
    service = world["service"]
    monkeypatch.setattr(content_pack, "_resources", lambda: world["resources"])
    monkeypatch.setattr(content_pack, "bundled_teaching",
                        lambda: (world["resources"], world["resources"] / "teaching_content.sqlite3"))
    assert service.libraries()[0]["version"] == "bundled" and service.libraries()[0]["bundled_version"] is None
    _stamp(world, monkeypatch, "20261121")
    row = service.libraries()[0]
    assert row["version"] == "20261121" and row["bundled_version"] == "20261121"


def test_a_start_heals_the_stuck_bundled_entry_an_earlier_version_left(world):
    """Device finding: after a removal on 0.36.15 the registry kept the bundled core entry pending_restart for ever."""
    service, root = world["service"], world["root"]
    stuck = rules.default_registry()
    stuck["libraries"][0]["state"] = "pending_restart"
    world["store"].set_setting(rules.REGISTRY_SETTING, stuck)
    service.write_mirror(stuck)
    content_pack.clear_cache()  # a start on that registry
    assert service.pending_restart()
    service.startup_sync()
    assert service.registry() == rules.default_registry() and not service.pending_restart()
    assert json.loads((root / rules.MIRROR_FILE).read_text("utf-8")) == rules.default_registry()
