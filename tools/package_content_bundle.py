"""Package only inert runtime content, independently of application sources."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

FORBIDDEN_SUFFIXES = {".py", ".pyc", ".pyd", ".dll", ".exe", ".ps1", ".js", ".jks", ".keystore"}
LEARNER_TABLES = {"study_card", "review_event", "session_checkpoint", "learning_unit", "app_setting"}


def package(resources, output):
    resources, output = Path(resources).resolve(), Path(output).resolve()
    if output.is_relative_to(resources) or output.exists():
        raise ValueError("Choose a new output file outside the runtime resources")
    paths = sorted(p for p in resources.rglob("*") if p.is_file())
    files = {}
    for path in paths:
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError("Content bundles cannot contain links")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or any(p.startswith(".") for p in path.relative_to(resources).parts):
            raise ValueError("Executable code, temporary files or credential material is not runtime content")
        raw = path.read_bytes()
        if raw.startswith(b"SQLite format 3\0"):
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables & LEARNER_TABLES:
                    raise ValueError("Learner state must never enter a content bundle")
        files[path.relative_to(resources).as_posix()] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    required = {"content.sqlite3", "teaching_content.sqlite3", "NOTICE.txt"}
    if not required <= files.keys():
        raise ValueError("Incomplete runtime content")
    with closing(sqlite3.connect((resources / "teaching_content.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        teaching = dict(db.execute("SELECT key,value FROM metadata"))
    with closing(sqlite3.connect((resources / "content.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        lexical = dict(db.execute("SELECT key,value FROM metadata"))
    policy = json.loads(lexical.get("audio_policy", "{}"))
    manifest = {"format": "kanamemory-content-bundle", "version": 1,
        "profile": teaching["profile"], "teaching_format": teaching["format"],
        "teaching_version": teaching["version"], "content_version": json.loads(lexical.get("content_version", "null")),
        "license_notice": "resources/NOTICE.txt", "files": files,
        "contains_audio_without_public_redistribution_clearance": bool(policy.get("contains_audio_without_public_redistribution_clearance")),
        "application_code_included": False, "learner_state_included": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        for path in paths:
            archive.write(path, "resources/" + path.relative_to(resources).as_posix())
    return {"path": str(output), "files": len(files), "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "profile": manifest["profile"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resources", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.resources, args.output), ensure_ascii=False, indent=2))
