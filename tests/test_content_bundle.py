"""Standalone content distribution never contains learner state or app code."""
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from package_content_bundle import package


def resources(tmp_path):
    folder = tmp_path / "resources"
    folder.mkdir()
    for name, values in (
        ("teaching_content.sqlite3", {"format": "kanamemory-teaching-content", "version": "1", "profile": "personal"}),
        ("content.sqlite3", {"content_version": '"test"', "audio_policy": '{"contains_audio_without_public_redistribution_clearance":true}'}),
    ):
        with sqlite3.connect(folder / name) as db:
            db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
            db.executemany("INSERT INTO metadata VALUES (?,?)", values.items())
    (folder / "NOTICE.txt").write_text("Synthetic test notice", encoding="utf-8")
    return folder


def test_manifest_keeps_private_distribution_flag_and_exact_inventory(tmp_path):
    folder = resources(tmp_path)
    result = package(folder, tmp_path / "pack.zip")
    with zipfile.ZipFile(result["path"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["contains_audio_without_public_redistribution_clearance"] is True
        assert manifest["application_code_included"] is False
        assert set(archive.namelist()) == {"manifest.json", *("resources/" + name for name in manifest["files"])}


@pytest.mark.parametrize("filename", ["hidden.py", ".env.local", "learner.sqlite3", ".teaching-temp.sqlite3"])
def test_rejects_code_credentials_and_learner_tables(tmp_path, filename):
    folder = resources(tmp_path)
    if filename.endswith("sqlite3"):
        with sqlite3.connect(folder / filename) as db:
            db.execute("CREATE TABLE study_card(id TEXT)")
    else:
        (folder / filename).write_text("synthetic", encoding="utf-8")
    with pytest.raises(ValueError):
        package(folder, tmp_path / "pack.zip")
    assert not (tmp_path / "pack.zip").exists()
