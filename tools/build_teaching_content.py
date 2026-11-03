"""Build a non-executable teaching pack from versioned namespace JSON files."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "src"))
from kanamemory.content_pack import (FORMAT, VERSION, NAMESPACE_FORMAT,
                                     PERSONAL_REQUIRED_NAMESPACES, ContentPackError,
                                     decode_value, namespace_document)


def build(source, destination, *, profile="personal"):
    source, destination = Path(source), Path(destination)
    if profile not in {"personal", "demo"}:
        raise ContentPackError("Unknown pack profile")
    documents = []
    for path in sorted(source.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("format") != NAMESPACE_FORMAT or document.get("version") != VERSION:
            raise ContentPackError("Unsupported namespace document: " + path.name)
        canonical = namespace_document(document["name"], decode_value(document["payload"]))
        payload = json.dumps(canonical["payload"], ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > 32 * 1024 * 1024:
            raise ContentPackError("Namespace too large: " + path.name)
        documents.append((canonical["name"], payload))
    if not documents or len({name for name, _ in documents}) != len(documents):
        raise ContentPackError("Need unique, nonempty namespace documents")
    # Namespace identity, not an author's filename, defines checksum order.
    # The runtime validates rows with ORDER BY name after SQLite persistence.
    documents.sort(key=lambda row: row[0])
    names = {name for name, _ in documents}
    if profile == "personal" and not PERSONAL_REQUIRED_NAMESPACES <= names:
        raise ContentPackError("Personal teaching pack is incomplete: " + ", ".join(sorted(PERSONAL_REQUIRED_NAMESPACES - names)))
    if destination.exists():
        try:
            with closing(sqlite3.connect(destination.resolve().as_uri() + "?mode=ro", uri=True)) as old:
                metadata = dict(old.execute("SELECT key,value FROM metadata"))
            if metadata.get("format") != FORMAT:
                raise ContentPackError("Refusing to overwrite a non-content database")
        except sqlite3.Error as error:
            raise ContentPackError("Refusing to overwrite an unrelated file") from error
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=".teaching-", suffix=".sqlite3", dir=destination.parent, delete=False)
    temporary = Path(handle.name)
    handle.close()
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.executescript("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
                                     "CREATE TABLE namespace(name TEXT PRIMARY KEY,payload_json TEXT NOT NULL);")
            digest = hashlib.sha256(json.dumps(documents, ensure_ascii=False).encode("utf-8")).hexdigest()
            connection.executemany("INSERT INTO metadata VALUES (?,?)", [
                ("format", FORMAT), ("version", str(VERSION)), ("profile", profile),
                ("namespaces", json.dumps(sorted(names))),
                ("content_sha256", digest)])
            connection.executemany("INSERT INTO namespace VALUES (?,?)", documents)
            connection.commit()
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"namespaces": len(documents), "profile": profile, "content_sha256": digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Directory containing only namespace JSON documents")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--profile", choices=("personal", "demo"), default="personal")
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.destination, profile=args.profile), ensure_ascii=False))


if __name__ == "__main__":
    main()
