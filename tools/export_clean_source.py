"""Export a fresh allowlisted demo source tree without Git history or user data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = Path(__file__).with_name("public_source_files.txt")
FORBIDDEN_PARTS = {".git", ".venv", "__pycache__", ".codex", ".agents", ".claude", "data", "resources", "research", "user-data", "artifacts"}
SECRET = re.compile(rb"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{30,}")
PRIVATE_PATH = re.compile(rb"[A-Za-z]:[\\/]Users[\\/][^\\/\r\n\"']+")


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def selected_files(root, allowlist=None):
    root = Path(root).resolve()
    paths = []
    for line in Path(allowlist or ALLOWLIST).read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        path = PurePosixPath(line)
        if path.is_absolute() or ".." in path.parts or set(path.parts) & FORBIDDEN_PARTS:
            raise ValueError("Unsafe public source allowlist entry: " + line)
        source = root.joinpath(*path.parts)
        if not source.is_file():
            raise ValueError("Allowlisted source is missing: " + line)
        if source.is_symlink() or any(p.is_symlink() for p in source.parents if p != root.parent):
            raise ValueError("Source links are not permitted: " + line)
        if not source.resolve().is_relative_to(root):
            raise ValueError("Source escapes the project: " + line)
        raw = source.read_bytes()
        if SECRET.search(raw) or PRIVATE_PATH.search(raw):
            raise ValueError("Credential or personal path detected in allowlisted file: " + line)
        paths.append((line, raw))
    if len(paths) != len({name for name, _ in paths}):
        raise ValueError("Duplicate public allowlist entry")
    return sorted(paths)


def _public_file(name, raw):
    if name == "app/pyproject.toml":
        text = raw.decode("utf-8")
        text, count = re.subn(r'(?m)^bundle\s*=.*$', 'bundle = "org.example.demo"', text)
        if count != 1:
            raise ValueError("Expected one Briefcase bundle")
        text, count = re.subn(r'(?m)^formal_name\s*=.*$', 'formal_name = "KanaMemoryDemo"', text)
        if count != 1:
            raise ValueError("Expected one Briefcase formal name")
        return text.encode("utf-8")
    return raw


def validate_engine_sources(root, selected):
    """Reject incomplete engines without automatically exporting unreviewed files."""
    root = Path(root)
    engine = root / "app/src/kanamemory"
    required = {
        path.relative_to(root).as_posix()
        for path in engine.rglob("*.py")
        if "resources" not in path.relative_to(engine).parts
    }
    missing = required - {name for name, _ in selected}
    if missing:
        raise ValueError("Engine modules missing from public allowlist: " + ", ".join(sorted(missing)))


def export_source(destination, *, root=ROOT, allowlist=None, archive=None):
    root, destination = Path(root).resolve(), Path(destination).absolute()
    if destination.is_symlink() or any(p.is_symlink() for p in destination.parents):
        raise ValueError("Export destination must not use symbolic links")
    if destination == root or root.is_relative_to(destination):
        raise ValueError("Export must not overwrite the project or its ancestors")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Use a new or empty export directory; existing files are never deleted")
    selected = selected_files(root, allowlist)
    validate_engine_sources(root, selected)
    # Validate everything before creating a partial distribution.
    files = {name: _public_file(name, raw) for name, raw in selected}
    files["README.txt"] = files["docs/public-build.txt"]
    files[".gitignore"] = b".venv/\n__pycache__/\n.pytest_cache/\n.ruff_cache/\n.test_runs/\n*.pyc\napp/src/kanamemory/resources/\nenvironment/artifacts/\n*.apk\n*.aab\n.env*\n*.keystore\n*.jks\n"
    manifest = {"format": "kanamemory-clean-source-v1", "profile": "demo",
                "package_id": "org.example.demo.kanamemory", "contains_git_history": False,
                "files": {name: sha256(raw) for name, raw in sorted(files.items())}}
    files["clean-source-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    destination.mkdir(parents=True, exist_ok=True)
    for name, raw in sorted(files.items()):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    if archive is not None:
        archive = Path(archive)
        if archive.exists():
            raise ValueError("Archive already exists; it will not be overwritten")
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
            for name, raw in sorted(files.items()):
                info = zipfile.ZipInfo("kana-memory-demo/" + name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                zipped.writestr(info, raw)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    result = export_source(args.destination, archive=args.archive)
    print(json.dumps({"files": len(result["files"]), "profile": result["profile"], "package_id": result["package_id"]}))


if __name__ == "__main__":
    main()
