"""Pure content-library rules: registry/manifest shapes, pack checks, precheck, merge."""
import copy
import json
import zipfile

import pytest

from kanamemory import content_library_rules as rules
from support.content_library_support import CORE_ITEMS, CORE_NAMESPACES, extension_namespaces, pack_bytes


def test_default_registry_has_only_the_enabled_core_library():
    registry = rules.validate_registry(rules.default_registry())
    assert [entry["library_id"] for entry in registry["libraries"]] == [rules.CORE_LIBRARY_ID]
    assert registry["libraries"][0]["enabled"] is True and registry["priority"] == [rules.CORE_LIBRARY_ID]
    assert rules.effective_entries(registry)[0]["source"]["kind"] == "bundled"
    assert not rules.pending_restart(registry)


def _installed_entry(**overrides):
    entry = {"library_id": "author.n3", "library_version": "20260102", "profile": "extension",
             "content_sha256": "a" * 64, "enabled": True, "installed_at_utc": "2026-11-08T00:00:00+00:00",
             "source": {"kind": "file", "name": "n3.zip", "sha256": "b" * 64, "signature_status": "unsigned"},
             "path": "author.n3/20260102", "state": "pending_restart"}
    entry.update(overrides)
    return entry


def test_registry_accepts_installed_extension_and_orders_by_priority():
    registry = rules.default_registry()
    registry["libraries"].append(_installed_entry(summary={"title": "N3", "families": {"grammar": 2}, "item_count": 2}))
    registry["priority"].append("author.n3")
    normalized = rules.validate_registry(registry)
    assert [e["library_id"] for e in rules.effective_entries(normalized)] == [rules.CORE_LIBRARY_ID, "author.n3"]
    assert rules.pending_restart(normalized)
    registry["libraries"][1]["enabled"] = False
    assert [e["library_id"] for e in rules.effective_entries(rules.validate_registry(registry))] == [rules.CORE_LIBRARY_ID]


@pytest.mark.parametrize("mutate", [
    lambda r: r["libraries"][0].update(enabled=False),
    lambda r: r.update(priority=[]),
    lambda r: r["libraries"].append(_installed_entry(library_id=rules.CORE_LIBRARY_ID)),
    lambda r: r["libraries"].append(_installed_entry(path="../escape")) or r["priority"].append("author.n3"),
    lambda r: r["libraries"].append(_installed_entry(content_sha256=None)) or r["priority"].append("author.n3"),
    lambda r: r["libraries"].append(_installed_entry(state="loaded")) or r["priority"].append("author.n3"),
    lambda r: r["libraries"].append(_installed_entry(extra=1)) or r["priority"].append("author.n3"),
    lambda r: r["libraries"].append(_installed_entry()) or r["priority"].insert(0, "author.n3"),
    lambda r: r.update(trusted_keys=[{"public_key_id": "k"}]),
    lambda r: r.update(version=2),
])
def test_registry_rejects_bad_shapes(mutate):
    registry = rules.default_registry()
    mutate(registry)
    with pytest.raises(rules.ContentLibraryError):
        rules.validate_registry(registry)


def _manifest_of(data):
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        return json.loads(archive.read("manifest.json"))


def test_manifest_round_trip_and_summary():
    manifest = rules.validate_manifest(_manifest_of(pack_bytes("author.n3", extension_namespaces())))
    assert manifest["provides"]["items"] == ["grammar:ext:masu"]
    assert manifest["layers"]["teaching"] == ["teaching/lesson.json", "teaching/route.json"]
    summary = rules.manifest_summary(manifest)
    assert summary == {"title": "测试扩展库", "families": {"grammar": 1}, "namespaces": ["lesson", "route"], "item_count": 1}


@pytest.mark.parametrize("overrides,drop", [
    ({"content_sha256": "0" * 64}, ()),
    ({"library_id": "Bad Id"}, ()),
    ({"version": 2}, ()),
    ({"merge_policies": {"route": "singleton"}}, ()),
    ({"provides": {"items": [], "namespaces": ["lesson"]}}, ()),
    ({"requires": {"teaching_format": 2, "namespace_version": 1}}, ()),
    ({"unknown": 1}, ()),
    ({}, ("title",)),
])
def test_manifest_rejects_bad_fields(overrides, drop):
    manifest = _manifest_of(pack_bytes("author.n3", extension_namespaces(), manifest_overrides=overrides, drop_manifest_keys=drop))
    with pytest.raises(rules.ContentLibraryError):
        rules.validate_manifest(manifest)


def test_pack_validation_accepts_a_clean_extension(tmp_path):
    path = tmp_path / "n3.zip"
    path.write_bytes(pack_bytes("author.n3", extension_namespaces()))
    report = rules.validate_library_pack(path)
    assert [name for name, _ in report.teaching_documents] == ["lesson", "route"]
    assert not report.has_lexicon and not report.has_teaching_pack and not report.signed
    assert report.total_bytes == sum(info["bytes"] for info in report.files.values())


@pytest.mark.parametrize("kwargs,match", [
    (dict(extra_files={"teaching/hook.py": b"print(1)"}), "Executable"),
    (dict(extra_files={".env": b"KEY=1"}), "Invalid pack entry"),
    (dict(corrupt="teaching/lesson.json"), "checksum|larger"),
    (dict(extra_files={"lexicon/content.sqlite3": b"SQLite format 3\0"}), "core library"),
    (dict(extra_files={"other/readme.txt": b"x"}), "Unexpected file"),
    (dict(manifest_overrides={"layers": {"teaching": ["teaching/lesson.json"], "notice": ["NOTICE.txt"]}}), "disagree|declared"),
])
def test_pack_validation_rejects_code_credentials_corruption_and_stray_files(tmp_path, kwargs, match):
    path = tmp_path / "bad.zip"
    path.write_bytes(pack_bytes("author.n3", extension_namespaces(), **kwargs))
    with pytest.raises(rules.ContentLibraryError, match=match):
        rules.validate_library_pack(path)


def test_pack_validation_rejects_non_zip_and_missing_notice(tmp_path):
    path = tmp_path / "text.zip"
    path.write_bytes(b"not a zip")
    with pytest.raises(rules.ContentLibraryError, match="ZIP"):
        rules.validate_library_pack(path)
    data = pack_bytes("author.n3", extension_namespaces())
    import io
    source = zipfile.ZipFile(io.BytesIO(data))
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        for info in source.infolist():
            if info.filename != "NOTICE.txt":
                archive.writestr(info.filename, source.read(info.filename))
    path.write_bytes(target.getvalue())
    with pytest.raises(rules.ContentLibraryError):
        rules.validate_library_pack(path)


def test_precheck_counts_new_same_conflicting_missing_and_retired():
    from kanamemory.material_review import item_fingerprint
    manifest = rules.validate_manifest(_manifest_of(pack_bytes("author.n3", extension_namespaces(conflict=True),
        manifest_overrides={"references": [{"id": "grammar:demo:desu", "kind": "item", "requirement": "required"},
                                           {"id": "grammar:nowhere", "kind": "item", "requirement": "required"},
                                           {"id": "word:optional", "kind": "item", "requirement": "optional"}],
                            "retires": [{"id": "word:demo:mizu", "replaced_by": None, "reason": "merged"}],
                            "depends_libraries": [{"library_id": "author.base", "version_range": ">=1"}]})))
    installed = {cid: item_fingerprint(item) for cid, item in CORE_ITEMS.items()}
    check = rules.precheck_pack(manifest, installed_fingerprints=installed, learned_ids={"word:demo:mizu", "grammar:ext:masu"},
                                installed_library_ids=[rules.CORE_LIBRARY_ID])
    assert check.new_item_count == 1
    assert check.conflicting_ids == ("grammar:demo:desu",) and check.undeclared_overrides == ("grammar:demo:desu",)
    assert check.same_fingerprint_ids == ()
    assert check.missing_required_references == ("grammar:nowhere",)
    assert check.missing_optional_references == ("word:optional",)
    assert check.retired_learned_ids == ("word:demo:mizu",)
    assert check.missing_libraries == ("author.base",)
    assert check.blocking_reasons() and "必需引用" in check.blocking_reasons()[0]
    assert len(check.warnings) == 3
    manifest["overrides"] = [{"id": "grammar:demo:desu", "reason": "fix"}]
    same = rules.precheck_pack(manifest, installed_fingerprints={**installed, "grammar:ext:masu": manifest["fingerprints"]["grammar:ext:masu"]},
                               learned_ids=set(), installed_library_ids=[rules.CORE_LIBRARY_ID, "author.base"])
    assert same.new_item_count == 0 and same.same_fingerprint_ids == ("grammar:ext:masu",) and same.undeclared_overrides == ()


def test_orphan_projection_and_library_progress():
    # Review B-11: the phase-0 projection (rules.orphan_ids/library_progress) is gone;
    # the library list counts from content_availability, the home page's source.
    from kanamemory.content_availability import Catalogue, build_view, library_of_content
    learned = {"grammar:demo:desu", "grammar:gone:x", "local:ai:note", "word:jmdict:1:よみ:s1"}
    registry = rules.default_registry()
    catalogue = Catalogue(ids=frozenset({"grammar:demo:desu"}), word_exists=lambda word_id: word_id == "jmdict:1")
    view = build_view(catalogue, registry, [{"content_id": cid} for cid in sorted(learned)])
    assert view.orphaned_ids == {"grammar:gone:x"}
    extension = Catalogue(ids=frozenset({"grammar:demo:desu", "grammar:new"}), providers={"grammar:new": "author.n3"})
    disabled = rules.validate_registry({**registry, "libraries": [*registry["libraries"], {
        **rules.core_entry(), "library_id": "author.n3", "library_version": "1", "profile": "extension",
        "content_sha256": "a" * 64, "enabled": False, "installed_at_utc": "2026-11-01T00:00:00+00:00",
        "source": {"kind": "file", "name": "n3.zip", "sha256": "b" * 64, "signature_status": "unsigned"},
        "path": "author.n3/1"}], "priority": [rules.CORE_LIBRARY_ID, "author.n3"]})
    view = build_view(extension, disabled, [{"content_id": "grammar:new"}, {"content_id": "grammar:demo:desu"}])
    assert view.orphaned_ids == {"grammar:new"} and library_of_content(extension, "grammar:new") == "author.n3"
    assert library_of_content(extension, "grammar:demo:desu") is None
    assert rules.count_families(learned) == {"grammar": 2, "other": 1, "word": 1}


def test_merge_follows_priority_singletons_and_contributions():
    core = copy.deepcopy(CORE_NAMESPACES["lesson"])
    extension = copy.deepcopy(extension_namespaces(conflict=True)["lesson"])
    merged, report = rules.merge_namespace([(rules.CORE_LIBRARY_ID, core), ("author.n3", extension)])
    assert merged["CONTENT_REVISION"] == 1
    assert set(merged["_ITEMS"]) == {"grammar:demo:desu", "word:demo:mizu", "grammar:ext:masu"}
    assert merged["_ITEMS"]["grammar:demo:desu"]["meaning"] == "是"
    assert merged["SOURCES"] == [{"id": "core"}, {"id": "ext"}]
    assert report.conflicts == (("_ITEMS", "grammar:demo:desu", rules.CORE_LIBRARY_ID, "author.n3"),)
    assert report.ignored_singletons == (("author.n3", "CONTENT_REVISION"),)
    assert ("author.n3", "_ITEMS", 1) in report.contributed and ("author.n3", "SOURCES", 1) in report.contributed
    route, _ = rules.merge_namespace([(rules.CORE_LIBRARY_ID, CORE_NAMESPACES["route"]), ("author.n3", extension_namespaces()["route"])])
    assert route["STAGES"] == ["intro"] and set(route["WORD_TOPICS"]) == {"basic", "ext"}
    alone, _ = rules.merge_namespace([(rules.CORE_LIBRARY_ID, None), ("author.n3", {"X": 1})])
    assert alone == {"X": 1}
    assert rules.merge_namespace([]) == (None, rules.MergeReport())


def test_change_blocked_reasons():
    assert rules.change_blocked_reason() is None
    assert "备份" in rules.change_blocked_reason(backup_busy=True)
    assert "练习" in rules.change_blocked_reason(round_running=True)
    assert "练习" in rules.change_blocked_reason(focused=True)
