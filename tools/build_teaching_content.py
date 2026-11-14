"""Build a non-executable teaching pack from versioned namespace JSON files.

The default layout (pack version 2) stores each namespace as checksummed
shards and records every shard's sha256 and size in a manifest whose checksum
is metadata.manifest_sha256. Large item tables are split per content id so the
App decodes only the records it reads. layout="legacy" writes version 1.
"""
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
from kanamemory.content_pack import (FORMAT, VERSION, SHARDED_VERSION, NAMESPACE_FORMAT,
                                     PERSONAL_REQUIRED_NAMESPACES, ContentPackError,
                                     ITEM_PREFIX, SHARDED, TOP_PREFIX, WHOLE, WHOLE_KEY,
                                     decode_value, encode_value, manifest_digest, namespace_document,
                                     shard_digest, validate_pack)
from kanamemory.loanword_origins import NAMESPACE as LOANWORD_ORIGINS, candidate_count

# The two largest namespaces (about 32 and 4 MiB of JSON), both looked up by
# content id and bound by item_fingerprint (08 section 6.2, 09 section 3), and
# the per-template dependency and reading tables (11 sections 5 and 7), looked
# up by content id and raw template sha256; all four use the items key "items".
SHARDED_NAMESPACES = frozenset({"varied_practice", "context_choice", "question_dependencies", "question_readings"})
ITEMS_KEY = "items"
_LIMIT = 32 * 1024 * 1024


def _encoded(value):
    return json.dumps(encode_value(value), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _shardable(value):
    return (type(value) is dict and type(value.get(ITEMS_KEY)) is dict
            and all(type(record) is dict for record in value[ITEMS_KEY].values()))


def _shards(name, value, shard):
    """Rows (shard_key, kinds_json, item_fingerprint, payload) and the structure."""
    if name not in shard or not _shardable(value):
        return WHOLE, {}, [(WHOLE_KEY, "[]", None, _encoded(value))]
    structure = {"top": list(value), "items_key": ITEMS_KEY}
    rows = [(TOP_PREFIX + key, "[]", None, _encoded(part)) for key, part in value.items() if key != ITEMS_KEY]
    for cid, record in value[ITEMS_KEY].items():
        templates = record.get("templates")
        kinds = sorted(templates) if type(templates) is dict else []
        fingerprint = record.get("item_fingerprint")
        rows.append((ITEM_PREFIX + cid, json.dumps(kinds, ensure_ascii=False),
                     fingerprint if isinstance(fingerprint, str) else None, _encoded(record)))
    return SHARDED, structure, rows


def _write_sharded(connection, profile, names, values, shard):
    connection.executescript("""
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE namespace(name TEXT PRIMARY KEY,layout TEXT NOT NULL,structure_json TEXT NOT NULL);
        CREATE TABLE shard(namespace TEXT NOT NULL REFERENCES namespace(name),shard_key TEXT NOT NULL,
            ordinal INTEGER NOT NULL,kinds_json TEXT NOT NULL,item_fingerprint TEXT,size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(namespace,shard_key));""")
    manifest, shard_count = [], 0
    for name, value in values:
        layout, structure, rows = _shards(name, value, shard)
        raw = json.dumps(structure, ensure_ascii=False, separators=(",", ":"))
        connection.execute("INSERT INTO namespace VALUES (?,?,?)", (name, layout, raw))
        listed = []
        for ordinal, (key, kinds, fingerprint, payload) in enumerate(rows):
            if len(payload) > _LIMIT:
                raise ContentPackError("Namespace shard too large: " + name + "/" + key)
            sha = shard_digest(payload)
            connection.execute("INSERT INTO shard VALUES (?,?,?,?,?,?,?,?)",
                               (name, key, ordinal, kinds, fingerprint, len(payload), sha, payload))
            listed.append([key, ordinal, kinds, fingerprint, len(payload), sha])
        shard_count += len(listed)
        manifest.append([name, layout, raw, listed])
    digest = manifest_digest(manifest)
    connection.executemany("INSERT INTO metadata VALUES (?,?)", [
        ("format", FORMAT), ("version", str(SHARDED_VERSION)), ("profile", profile),
        ("namespaces", json.dumps(sorted(names))),
        ("manifest_sha256", digest), ("content_sha256", digest)])
    return digest, shard_count


def _write_legacy(connection, profile, names, documents):
    connection.executescript("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
                             "CREATE TABLE namespace(name TEXT PRIMARY KEY,payload_json TEXT NOT NULL);")
    digest = hashlib.sha256(json.dumps(documents, ensure_ascii=False).encode("utf-8")).hexdigest()
    connection.executemany("INSERT INTO metadata VALUES (?,?)", [
        ("format", FORMAT), ("version", str(VERSION)), ("profile", profile),
        ("namespaces", json.dumps(sorted(names))),
        ("content_sha256", digest)])
    connection.executemany("INSERT INTO namespace VALUES (?,?)", documents)
    return digest


def build(source, destination, *, profile="personal", layout="sharded", shard=SHARDED_NAMESPACES):
    source, destination = Path(source), Path(destination)
    if profile not in {"personal", "demo"}:
        raise ContentPackError("Unknown pack profile")
    if layout not in {"sharded", "legacy"}:
        raise ContentPackError("Unknown pack layout")
    documents, values = [], []
    for path in sorted(source.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("format") != NAMESPACE_FORMAT or document.get("version") != VERSION:
            raise ContentPackError("Unsupported namespace document: " + path.name)
        value = decode_value(document["payload"])
        canonical = namespace_document(document["name"], value)
        payload = json.dumps(canonical["payload"], ensure_ascii=False, separators=(",", ":"))
        if layout == "legacy" and len(payload.encode("utf-8")) > _LIMIT:
            raise ContentPackError("Namespace too large: " + path.name)
        documents.append((canonical["name"], payload))
        values.append((canonical["name"], value))
    if not documents or len({name for name, _ in documents}) != len(documents):
        raise ContentPackError("Need unique, nonempty namespace documents")
    # Namespace identity, not an author's filename, defines checksum order.
    # The runtime validates rows with ORDER BY name after SQLite persistence.
    documents.sort(key=lambda row: row[0])
    values.sort(key=lambda row: row[0])
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
    result = {"namespaces": len(documents), "profile": profile}
    origins = dict(values).get(LOANWORD_ORIGINS)
    if origins is not None:  # design 11 section 19: a released pack should hold no unreviewed entry
        result["loanword_origin_candidates"] = candidate_count(origins)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            if layout == "legacy":
                digest = _write_legacy(connection, profile, names, documents)
            else:
                digest, result["shards"] = _write_sharded(connection, profile, names, values, frozenset(shard))
            connection.commit()
        # The written file must pass the full (every shard) check before use.
        validate_pack(temporary, deep=True)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    result["content_sha256"] = digest
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Directory containing only namespace JSON documents")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--profile", choices=("personal", "demo"), default="personal")
    parser.add_argument("--layout", choices=("sharded", "legacy"), default="sharded")
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.destination, profile=args.profile, layout=args.layout),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
