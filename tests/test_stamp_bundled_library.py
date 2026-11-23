"""Design 08 section 5.7: the version stamp of the bundled core content.

The version order of the rules, the stamp reader of content_pack, tools/stamp_bundled_library.py and the rule
that tools/package_library_pack.py never puts the stamp into a pack. Synthetic data only.
"""
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_pack
from support.content_library_support import build_core_resources, extension_namespaces, pack_bytes
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)

ROOT = Path(__file__).resolve().parents[1]


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(resources):
    return content_pack.validate_pack(resources / "teaching_content.sqlite3")["content_sha256"]


@pytest.mark.parametrize("left, right, order", [
    ("20261119.3", "20261121", -1), ("20261121", "20261121.1", -1), ("20261121", "20261121", 0),
    ("20261121.10", "20261121.9", 1), ("20261122", "20261121.1", 1)])
def test_date_versions_compare_by_their_digit_groups(left, right, order):
    assert rules.library_version_key("20261119.3", "date") == (20261119, 3)
    assert rules.compare_library_versions(left, "date", right, "date") == order


@pytest.mark.parametrize("version, scheme", [("1.0.0", "semver"), ("20261121a", "date"), ("", "date"),
                                             ("20261121..1", "date"), (None, "date")])
def test_anything_else_is_not_comparable(version, scheme):
    assert rules.library_version_key(version, scheme) is None
    assert rules.compare_library_versions(version, scheme, "20261121", "date") is None


def test_the_stamp_reader_accepts_only_a_stamp_of_the_bundled_teaching_database(tmp_path):
    resources = build_core_resources(tmp_path / "resources")
    path = resources / rules.BUNDLED_STAMP_FILE
    assert content_pack.bundled_stamp(resources) is None  # missing
    valid = rules.make_bundled_stamp("20261121", "date", _sha(resources))
    path.write_text(json.dumps(valid), encoding="utf-8")
    assert content_pack.bundled_stamp(resources) == valid
    path.write_text(json.dumps(dict(valid, teaching_content_sha256="0" * 64)), encoding="utf-8")
    assert content_pack.bundled_stamp(resources) is None  # stale: another teaching database
    for broken in ("{not json", json.dumps(dict(valid, extra=1)), json.dumps(dict(valid, library_id="x.y.z")),
                   json.dumps(dict(valid, version_scheme="other")), json.dumps([valid])):
        path.write_text(broken, encoding="utf-8")
        assert content_pack.bundled_stamp(resources) is None  # malformed: ignored, never an error


def _core_pack(resources, output, version="20261121"):
    _tool("package_library_pack").package(resources, output, library_version=version)
    return output


def test_the_tool_stamps_resources_for_the_core_pack_built_from_them(tmp_path, capsys):
    resources = build_core_resources(tmp_path / "resources")
    pack = _core_pack(resources, tmp_path / "core.kmlib", "20261119.3")
    value = _tool("stamp_bundled_library").stamp(resources, pack)
    assert value == rules.make_bundled_stamp("20261119.3", "date", _sha(resources))
    assert content_pack.bundled_stamp(resources) == value


def test_the_tool_refuses_a_core_pack_of_other_content_and_an_extension_pack(tmp_path):
    tool = _tool("stamp_bundled_library")
    resources = build_core_resources(tmp_path / "resources")
    other = build_core_resources(tmp_path / "other", {"lesson": {"CONTENT_REVISION": 2, "_ITEMS": {},
                                                                 "SOURCES": [{"id": "other"}]}})
    pack = _core_pack(other, tmp_path / "other.kmlib")
    with pytest.raises(ValueError, match="not the bundled one"):
        tool.stamp(resources, pack)
    extension = tmp_path / "n3.kmlib"
    extension.write_bytes(pack_bytes("author.n3", extension_namespaces()))
    with pytest.raises(ValueError, match="Not a core library pack"):
        tool.stamp(resources, extension)
    assert not (resources / rules.BUNDLED_STAMP_FILE).exists()


def test_a_pack_never_carries_the_stamp(tmp_path):
    resources = build_core_resources(tmp_path / "resources")
    (resources / rules.BUNDLED_STAMP_FILE).write_text(
        json.dumps(rules.make_bundled_stamp("20261121", "date", _sha(resources))), encoding="utf-8")
    pack = _core_pack(resources, tmp_path / "core.kmlib")
    with zipfile.ZipFile(pack) as archive:
        assert not any(Path(name).name == rules.BUNDLED_STAMP_FILE for name in archive.namelist())
