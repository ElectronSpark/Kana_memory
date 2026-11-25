"""The bundled core entry stores the stamp's version (design 08 sections 5.7 and 16.10). Synthetic data only.

With a valid stamp the registry entry of the bundled content holds the stamp's library_version, "bundled"
without one; the page, the precheck and the registry content_pack loaded agree; a registry written before is
brought up at start with no restart note, and an app start after that changes nothing more.
"""
import json

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_pack
from kanamemory.content_library_service import ContentLibraryService
from kanamemory.domain import StudyStore
from kanamemory.learning_store import LearningStore
from support.content_library_support import CORE_ITEMS, build_core_resources, extension_namespaces, pack_bytes
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)

STAMPED = "20261121"


@pytest.fixture
def world(tmp_path, monkeypatch):
    resources = build_core_resources(tmp_path / "resources")
    root = tmp_path / "libraries"
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(content_pack, "_resources", lambda: resources)
    monkeypatch.setattr(content_pack, "bundled_teaching", lambda: (resources, resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    content_pack.clear_cache()
    store = StudyStore(tmp_path / "learner", time_zone="UTC")
    LearningStore(store)
    service = ContentLibraryService(store, catalogue_ids=lambda: set(CORE_ITEMS), catalogue_fingerprints=lambda: {},
                                    learned_ids=lambda: set(), word_exists=lambda word_id: False)
    yield {"store": store, "service": service, "root": root, "resources": resources, "tmp": tmp_path}
    store.close()
    content_pack.clear_cache()


def _stamp(world, version=STAMPED, *, stale=False):
    resources = world["resources"]
    sha = "0" * 64 if stale else content_pack.validate_pack(resources / "teaching_content.sqlite3")["content_sha256"]
    (resources / rules.BUNDLED_STAMP_FILE).write_text(json.dumps(rules.make_bundled_stamp(version, "date", sha)),
                                                      encoding="utf-8")
    content_pack.clear_cache()


def _core(registry):
    return rules.registry_entry(registry, rules.CORE_LIBRARY_ID)


def _old_registry(world):
    """A registry file written by an app before this rule: the bundled entry stores "bundled"."""
    registry = rules.default_registry()
    world["store"].set_setting(rules.REGISTRY_SETTING, registry)
    world["service"].write_mirror(registry)
    content_pack.clear_cache()
    return registry


def _mirror(world):
    return json.loads((world["root"] / rules.MIRROR_FILE).read_text("utf-8"))


def _core_pack(world, version):
    from package_library_pack import package
    output = world["tmp"] / f"core-{version}.kmlib"
    package(world["resources"], output, library_version=version)
    return output.read_bytes()


def test_a_first_start_with_a_stamp_stores_its_version(world):
    _stamp(world)
    registry = world["service"].registry()
    assert _core(registry)["library_version"] == STAMPED and _core(registry)["source"]["kind"] == "bundled"
    assert _core(_mirror(world))["library_version"] == STAMPED
    row, = world["service"].libraries()
    assert row["version"] == row["bundled_version"] == STAMPED


def test_without_a_valid_stamp_the_entry_keeps_bundled(world):
    assert _core(world["service"].registry())["library_version"] == rules.BUNDLED_VERSION == "bundled"
    _stamp(world, stale=True)  # a stamp of other content counts for nothing
    world["service"].startup_sync()
    assert _core(world["service"].registry())["library_version"] == "bundled"
    row, = world["service"].libraries()
    assert row["version"] == "bundled" and row["bundled_version"] is None


def test_an_older_registry_is_brought_up_at_start_without_a_restart_note(world):
    old = _old_registry(world)
    _stamp(world)  # the app update brings stamped content; the next start
    loaded = content_pack.active_registry()
    assert _core(loaded)["library_version"] == STAMPED  # what this process loaded agrees before the sync
    result = world["service"].startup_sync()
    registry = world["service"].registry()
    assert _core(registry) == dict(_core(old), library_version=STAMPED) and _core(registry)["state"] == "ready"
    assert _core(_mirror(world))["library_version"] == STAMPED
    assert result["pending_restart"] is False and not world["service"].pending_restart()
    assert world["service"].bundled_notice() is None
    content_pack.clear_cache()  # the start after: nothing more changes
    writes = []
    real = world["store"].set_setting
    world["store"].set_setting = lambda key, value: writes.append(key) or real(key, value)
    world["service"].startup_sync()
    assert writes == [] and world["service"].registry() == registry


def test_a_registry_with_a_stamped_version_goes_back_to_bundled_when_the_stamp_is_gone(world):
    _stamp(world)
    world["service"].registry()
    (world["resources"] / rules.BUNDLED_STAMP_FILE).unlink()
    content_pack.clear_cache()
    assert _core(content_pack.active_registry())["library_version"] == "bundled"
    world["service"].startup_sync()
    assert _core(world["service"].registry())["library_version"] == "bundled"
    assert not world["service"].pending_restart()


def test_the_precheck_of_a_core_pack_names_the_stamped_version_it_replaces(world):
    _stamp(world)
    world["service"].startup_sync()
    inspection = world["service"].inspect_bytes(_core_pack(world, "20261122"), "core.kmlib")
    assert inspection.precheck.replaces_version == STAMPED and inspection.precheck.refusals() == ()
    world["service"].discard(inspection)


def test_removing_an_imported_core_pack_restores_the_entry_with_the_stamped_version(world, monkeypatch):
    service = world["service"]
    _stamp(world)
    service.startup_sync()
    service.install(service.inspect_bytes(_core_pack(world, "20261122"), "core.kmlib"))
    assert _core(service.registry())["source"]["kind"] == "file"
    service.remove(rules.CORE_LIBRARY_ID)
    core = _core(service.registry())
    assert core["source"]["kind"] == "bundled" and core["library_version"] == STAMPED
    assert core["state"] == "pending_restart"
    monkeypatch.delenv("KANAMEMORY_TEACHING_PACK")
    content_pack.clear_cache()  # the restart
    service.startup_sync()
    assert _core(service.registry()) == dict(rules.core_entry(content_pack.bundled_stamp()))
    assert not service.pending_restart()


def test_an_extension_library_is_never_touched(world):
    _stamp(world)
    service = world["service"]
    service.install(service.inspect_bytes(pack_bytes("author.n3", extension_namespaces()), "n3.kmlib"))
    entry = rules.registry_entry(service.registry(), "author.n3")
    content_pack.clear_cache()
    service.startup_sync()
    assert {**rules.registry_entry(service.registry(), "author.n3"), "state": None} == {**entry, "state": None}


def test_the_rule_itself():
    stamp = rules.make_bundled_stamp(STAMPED, "date", "a" * 64)
    registry = rules.default_registry()
    current = rules.with_bundled_version(registry, stamp)
    assert _core(current)["library_version"] == STAMPED and _core(registry)["library_version"] == "bundled"
    assert rules.with_bundled_version(current, stamp) is None  # already so
    assert _core(rules.with_bundled_version(current, None))["library_version"] == "bundled"
    assert rules.default_registry(stamp) == current and rules.core_entry(stamp)["library_version"] == STAMPED
