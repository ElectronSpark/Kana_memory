"""The streamed import of a library pack (design 08 section 16.10). Synthetic packs only.

The file is copied and hashed in blocks as it arrives, each member is read from the archive once and written to
its place while it is hashed, install moves the unpacked members instead of reading the archive again, and no
step holds the pack or a large member in memory. Four named limits, each with its own sentence. A failed or
interrupted import leaves the installed library as it was, also across an app start.
"""
import base64
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import tracemalloc
import zipfile

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_pack
from kanamemory import platform_android as bridge
from kanamemory import platform_desktop
from kanamemory.content_library_service import ContentLibraryService
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore
from support.content_library_support import CORE_ITEMS, build_core_resources, extension_namespaces, pack_bytes
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)

MIB = 1024 * 1024


class Interrupted(BaseException):
    """The process going away in the middle of the work (not an error anything may catch)."""


@pytest.fixture
def world(tmp_path, monkeypatch):
    resources = build_core_resources(tmp_path / "resources")
    root = tmp_path / "libraries"
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(root))
    content_pack.clear_cache()
    store = StudyStore(tmp_path / "learner", time_zone="UTC")
    LearningStore(store)
    service = ContentLibraryService(store, catalogue_ids=lambda: set(CORE_ITEMS), catalogue_fingerprints=lambda: {},
                                    learned_ids=lambda: set(), word_exists=lambda word_id: False)
    yield SimpleNamespace(store=store, service=service, root=root, tmp=tmp_path)
    store.close()
    content_pack.clear_cache()


def _stage(service, data, name="n3.kmlib", block=64 * 1024):
    """Through the platform's way: the file written into the staging block by block."""
    staging = service.open_staging(name)
    try:
        for start in range(0, len(data), block):
            staging.write(data[start:start + block])
    except BaseException:
        staging.discard()
        raise
    return service.stage_file(staging, registry=service.registry())


def _install(service, data, name="n3.kmlib"):
    return service.install(service.precheck_staged(_stage(service, data, name)))


def _incoming(root):
    return sorted(p.name for p in Path(root).iterdir() if p.name.startswith((".incoming-", ".new-")))


def _tree(folder):
    """path -> sha256 of every file under ``folder``."""
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(folder).rglob("*") if p.is_file()}


# ------------------------------------------------------------------ the limits


def test_four_named_limits_each_with_its_own_sentence():
    assert rules.MAX_PACK_BYTES == 512 * MIB and rules.MAX_FILE_BYTES == 400 * MIB
    assert rules.MAX_UNPACKED_BYTES == 1024 * MIB and rules.MAX_PACK_MEMBERS == 20000
    sentences = [rules.PACK_TOO_LARGE, rules.MEMBER_TOO_LARGE, rules.UNPACKED_TOO_LARGE, rules.TOO_MANY_MEMBERS]
    assert len(set(sentences)) == 4
    for sentence, number in zip(sentences, ("512 MiB", "400 MiB", "1024 MiB", "20000")):
        assert number in sentence


def test_a_file_over_the_pack_limit_is_refused_while_it_is_copied(world, monkeypatch):
    data = pack_bytes("author.n3", extension_namespaces())
    monkeypatch.setattr(rules, "MAX_PACK_BYTES", len(data) - 1)
    with pytest.raises(ValueError) as refused:
        _stage(world.service, data, block=1000)
    assert str(refused.value) == rules.PACK_TOO_LARGE and rules.learner_error(refused.value) == rules.PACK_TOO_LARGE
    assert _incoming(world.root) == []
    monkeypatch.setattr(rules, "MAX_PACK_BYTES", len(data))  # exactly at the limit: accepted
    world.service.discard(_stage(world.service, data))


@pytest.mark.parametrize("limit, value, sentence", [
    ("MAX_FILE_BYTES", 100, rules.MEMBER_TOO_LARGE),
    ("MAX_UNPACKED_BYTES", 150, rules.UNPACKED_TOO_LARGE),
    ("MAX_PACK_MEMBERS", 1, rules.TOO_MANY_MEMBERS),
])
def test_a_member_limit_refuses_before_anything_is_unpacked(world, monkeypatch, limit, value, sentence):
    data = pack_bytes("author.n3", extension_namespaces(), extra_files={"media/a.bin": b"x" * 120})
    monkeypatch.setattr(rules, limit, value)
    read = []
    monkeypatch.setattr(rules, "_hash_member", lambda *args, **kwargs: read.append(args) or (_ for _ in ()).throw(
        AssertionError("decompressed")))
    with pytest.raises(rules.ContentLibraryError) as refused:
        _stage(world.service, data)
    assert rules.learner_error(refused.value) == sentence and not read
    assert _incoming(world.root) == []


def test_the_manifest_s_own_table_is_held_to_the_same_limits():
    files = {"NOTICE.txt": {"bytes": 1, "sha256": "0" * 64}, "media/a.bin": {"bytes": rules.MAX_FILE_BYTES + 1,
                                                                            "sha256": "0" * 64}}
    with pytest.raises(rules.ContentLibraryError, match="Library file exceeds its size limit"):
        rules._file_table(files)
    many = {f"media/{n}.bin": {"bytes": rules.MAX_UNPACKED_BYTES // 3, "sha256": "0" * 64} for n in range(4)}
    with pytest.raises(rules.ContentLibraryError, match="Library members exceed their total size limit"):
        rules._file_table(many)


# ------------------------------------------------------------------ one pass over each member


def test_each_member_is_read_from_the_archive_once_and_install_moves_it(world, monkeypatch):
    data = pack_bytes("author.n3", extension_namespaces(), extra_files={"media/a.bin": os.urandom(5000)})
    opened = []
    real_open = zipfile.ZipFile.open

    def counted(self, name, *args, **kwargs):
        opened.append(getattr(name, "filename", name))
        return real_open(self, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", counted)
    staged = _stage(world.service, data)
    assert not (staged.staging / "pack.zip").exists()  # the archive goes once its members are out
    assert _tree(staged.unpacked).keys() == {rules.MANIFEST_FILE, *staged.manifest["files"]}
    inspection = world.service.precheck_staged(staged)
    entry = world.service.install(inspection)
    members = [name for name in opened if name != rules.MANIFEST_FILE]
    assert sorted(members) == sorted(staged.manifest["files"])  # each once, from staging to install
    installed = world.root / entry["path"]
    assert all(_tree(installed)[name] == info["sha256"] for name, info in staged.manifest["files"].items())
    assert not staged.staging.exists() and _incoming(world.root) == []


def test_a_member_changed_after_the_check_is_refused_at_install(world):
    inspection = world.service.precheck_staged(_stage(world.service, pack_bytes("author.n3", extension_namespaces())))
    (inspection.unpacked / rules.NOTICE_FILE).write_bytes(b"swapped after the check")
    with pytest.raises(rules.ContentLibraryError) as refused:
        world.service.install(inspection)
    assert rules.learner_error(refused.value) == rules.DAMAGED_PACK
    assert [e["library_id"] for e in world.service.registry()["libraries"]] == [rules.CORE_LIBRARY_ID]
    assert not inspection.staging.exists()


def test_no_step_holds_a_large_member_in_memory(world):
    """A 6 MiB member that does not compress: copying, checking and installing stay far below its size."""
    big = os.urandom(6 * MIB)
    path = world.tmp / "big.kmlib"
    path.write_bytes(pack_bytes("author.big", extension_namespaces(), extra_files={"media/big.bin": big}))
    del big
    service = world.service
    tracemalloc.start()
    try:
        staging = service.open_staging("big.kmlib")
        platform_desktop.copy_library_file(path, staging)
        inspection = service.precheck_staged(service.stage_file(staging, registry=service.registry()))
        service.install(inspection)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 3 * MIB, peak


# ------------------------------------------------------------------ interrupted: the installed library stays


def _installed_v1(world):
    entry = _install(world.service, pack_bytes("author.n3", extension_namespaces()))
    return entry, world.service.registry(), _tree(world.root / entry["path"])


def _v2():
    namespaces = extension_namespaces()
    namespaces["lesson"]["_ITEMS"]["grammar:ext:masu"] = dict(namespaces["lesson"]["_ITEMS"]["grammar:ext:masu"],
                                                              revision=2, meaning="动词敬体（修订）")
    return pack_bytes("author.n3", namespaces, version="20260201", extra_files={"media/a.bin": os.urandom(4096)})


def _untouched(world, entry, registry, tree):
    assert world.service.registry() == registry
    assert _tree(world.root / entry["path"]) == tree
    assert _incoming(world.root) == [] and sorted(p.name for p in (world.root / "author.n3").iterdir()) == [
        Path(entry["path"]).name]


def test_an_import_interrupted_in_the_middle_of_a_member_leaves_the_installed_library_untouched(world, monkeypatch):
    entry, registry, tree = _installed_v1(world)
    monkeypatch.setattr(rules, "_BLOCK_BYTES", 512)  # the member arrives in several blocks
    real = rules._hash_member

    def cut(archive, info, expected_size, *, sink=None, keep=False):
        if info.filename == "media/a.bin":
            class Cut:
                written = 0

                def write(self, block):
                    sink.write(block)
                    Cut.written += len(block)
                    if Cut.written >= 1024:
                        raise Interrupted()
            return real(archive, info, expected_size, sink=Cut(), keep=keep)
        return real(archive, info, expected_size, sink=sink, keep=keep)
    monkeypatch.setattr(rules, "_hash_member", cut)
    with pytest.raises(Interrupted):
        _stage(world.service, _v2())
    _untouched(world, entry, registry, tree)
    content_pack.clear_cache()  # and across an app start
    world.service.startup_sync()
    assert rules.registry_entry(world.service.registry(), "author.n3")["library_version"] == "20260102"
    assert _tree(world.root / entry["path"]) == tree


def test_an_install_interrupted_while_its_folder_is_made_leaves_the_installed_library_untouched(world, monkeypatch):
    entry, registry, tree = _installed_v1(world)
    inspection = world.service.precheck_staged(_stage(world.service, _v2()))
    monkeypatch.setattr(ContentLibraryService, "_write_indexes", staticmethod(
        lambda directory, manifest: (_ for _ in ()).throw(Interrupted())))
    with pytest.raises(Interrupted):
        world.service.install(inspection)
    _untouched(world, entry, registry, tree)
    assert not inspection.staging.exists()
    content_pack.clear_cache()  # the next start settles the earlier install (ready) and nothing else
    world.service.startup_sync()
    after = rules.registry_entry(world.service.registry(), "author.n3")
    before = rules.registry_entry(registry, "author.n3")
    assert {**after, "state": None} == {**before, "state": None} and after["state"] == "ready"
    assert _tree(world.root / entry["path"]) == tree and _incoming(world.root) == []


# ------------------------------------------------------------------ the platforms write into the staging


def test_android_blocks_go_straight_into_the_staging_with_no_cache_file(world, tmp_path):
    data = pack_bytes("author.n3", extension_namespaces())
    blocks = iter(base64.b64encode(data[start:start + bridge._BACKUP_BLOCK_BYTES]).decode("ascii")
                  for start in range(0, len(data), bridge._BACKUP_BLOCK_BYTES))
    native = SimpleNamespace(readImportBlock=lambda: next(blocks, ""))
    cache = tmp_path / "cache"
    cache.mkdir()
    staging = world.service.open_staging("x")
    assert bridge._read_library_stream(native, str(cache), rules.MAX_PACK_BYTES, sink=staging) == len(data)
    assert not list(cache.iterdir())
    staged = world.service.stage_file(staging)
    assert staged.source_sha256 == hashlib.sha256(data).hexdigest()
    blocks = iter([base64.b64encode(b"x" * 10).decode("ascii")] * 3)
    native = SimpleNamespace(readImportBlock=lambda: next(blocks, ""))
    other = world.service.open_staging("y")
    with pytest.raises(bridge.PlatformError, match="MiB 限制"):
        bridge._read_library_stream(native, str(cache), 25, sink=other)
    other.discard()


def test_the_desktop_copies_a_picked_file_in_blocks_and_names_it(world, tmp_path):
    data = pack_bytes("author.n3", extension_namespaces())
    path = tmp_path / "KanaMemory-0.36.17.1-n3.kmlib"
    path.write_bytes(data)
    writes = []

    class Sink:
        def write(self, block):
            writes.append(len(block))
    assert platform_desktop.copy_library_file(path, Sink(), block=1000) == path.name
    assert sum(writes) == len(data) and max(writes) == 1000
