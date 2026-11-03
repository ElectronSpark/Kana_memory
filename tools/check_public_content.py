"""Fail closed before publishing a demo build, including stale APK resources."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import zipfile

from build_demo_content import FORMAT, digest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src"))
from kanamemory.content_pack import validate_pack

MANIFEST = "public-content-manifest.json"
EXPECTED_FILES = {"content.sqlite3", "teaching_content.sqlite3", "NOTICE.txt", "learning_material_review.json"}


def _metadata(raw):
    with sqlite3.connect(":memory:") as connection:
        connection.deserialize(raw)
        connection.execute("PRAGMA query_only=ON")
        return dict(connection.execute("SELECT key,value FROM metadata"))


def validate_resources(resources, source=None):
    resources = Path(resources).absolute()
    if resources.is_symlink() or any(p.is_symlink() for p in resources.parents):
        raise ValueError("Public resources must not use symbolic links")
    files = {}
    for file in resources.rglob("*"):
        if file.is_symlink():
            raise ValueError("Public resources contain a symbolic link")
        if file.is_file():
            files[file.relative_to(resources).as_posix()] = file
    if set(files) != EXPECTED_FILES | {MANIFEST}:
        raise ValueError("Public resources contain missing, extra, or private files")
    manifest = json.loads(files[MANIFEST].read_text("utf-8"))
    if manifest.get("format") != FORMAT or manifest.get("profile") != "demo":
        raise ValueError("Public builds require the original demo pack")
    if set(manifest.get("files", {})) != EXPECTED_FILES:
        raise ValueError("Unexpected public content manifest entries")
    if source and manifest.get("source_sha256") != digest(source):
        raise ValueError("Demo source changed; rebuild the demo pack in a new empty directory")
    for name, checksum in manifest["files"].items():
        if not isinstance(checksum, str) or digest(files[name]) != checksum:
            raise ValueError("Public resource checksum mismatch: " + name)
    lexical = _metadata(files["content.sqlite3"].read_bytes())
    if (json.loads(lexical.get("content_profile", '""')) != "demo"
            or json.loads(lexical.get("content_format", '""')) != FORMAT):
        raise ValueError("Private or unclassified lexical content is forbidden")
    policy = json.loads(lexical.get("audio_policy", "{}"))
    if policy.get("personal_audio_included") or policy.get("contains_audio_without_public_redistribution_clearance"):
        raise ValueError("Personal audio cannot enter a public build")
    teaching = _metadata(files["teaching_content.sqlite3"].read_bytes())
    if teaching.get("profile") != "demo":
        raise ValueError("Private or unsupported teaching content is forbidden")
    validate_pack(files["teaching_content.sqlite3"])
    return {name: digest(path) for name, path in files.items()}


def validate_apk(apk, resources, source=None):
    expected = validate_resources(resources, source)
    with zipfile.ZipFile(apk) as outer:
        with zipfile.ZipFile(io.BytesIO(outer.read("assets/chaquopy/app.imy"))) as inner:
            prefix = "kanamemory/resources/"
            names = [item.filename for item in inner.infolist()
                     if not item.is_dir() and item.filename.startswith(prefix)]
            if len(names) != len(set(names)):
                raise ValueError("Duplicate resources in public APK")
            found = {name[len(prefix):]: hashlib.sha256(inner.read(name)).hexdigest() for name in names}
            if found != expected:
                raise ValueError("APK contains stale, private, or mismatched resources; rebuild in its public directory")
            for item in inner.infolist():
                path = PurePosixPath(item.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Unsafe APK application path")
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resources", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--apk", type=Path)
    args = parser.parse_args()
    (validate_apk(args.apk, args.resources, args.source) if args.apk
     else validate_resources(args.resources, args.source))
    print("Original demo content verified" + (" inside APK" if args.apk else ""))


if __name__ == "__main__":
    main()
