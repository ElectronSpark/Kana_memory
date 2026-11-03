"""Build the original, tiny demo pack without private files or third-party data.

This module deliberately uses only the Python standard library. Its input is a
separate JSON content file, never Python lesson modules or a private database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
FORMAT = "kanamemory-public-demo-v1"
SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA user_version=3;
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL CHECK(json_valid(value)));
CREATE TABLE kana(id TEXT PRIMARY KEY,hiragana TEXT,katakana TEXT,romaji TEXT,category TEXT NOT NULL,tier TEXT NOT NULL,payload TEXT NOT NULL CHECK(json_valid(payload)));
CREATE TABLE origin(id TEXT PRIMARY KEY,kana_id TEXT NOT NULL REFERENCES kana(id),payload TEXT NOT NULL CHECK(json_valid(payload)));
CREATE TABLE locality(id TEXT PRIMARY KEY,label TEXT NOT NULL,payload TEXT NOT NULL CHECK(json_valid(payload)));
CREATE TABLE dialect(id TEXT PRIMARY KEY,hanzi TEXT NOT NULL,locality_id TEXT NOT NULL REFERENCES locality(id),payload TEXT NOT NULL CHECK(json_valid(payload)));
CREATE TABLE historical(id TEXT PRIMARY KEY,hanzi TEXT,payload TEXT NOT NULL CHECK(json_valid(payload)));
CREATE TABLE character_synthesis(id TEXT PRIMARY KEY,hanzi TEXT NOT NULL,payload TEXT NOT NULL CHECK(json_valid(payload)));
CREATE TABLE orthographic_relation(character TEXT NOT NULL,related_character TEXT NOT NULL,payload TEXT NOT NULL CHECK(json_valid(payload)));
CREATE TABLE source(id TEXT PRIMARY KEY,title TEXT NOT NULL,url TEXT NOT NULL,license TEXT,payload TEXT NOT NULL CHECK(json_valid(payload)));
CREATE TABLE word(id TEXT PRIMARY KEY,form TEXT NOT NULL,reading TEXT NOT NULL,gloss TEXT NOT NULL,collection TEXT NOT NULL,rank INTEGER NOT NULL,payload TEXT NOT NULL CHECK(json_valid(payload)));
CREATE TABLE word_collection(word_id TEXT NOT NULL REFERENCES word(id),collection TEXT NOT NULL,PRIMARY KEY(word_id,collection));
CREATE TABLE word_search(word_id TEXT NOT NULL REFERENCES word(id),term TEXT NOT NULL,priority INTEGER NOT NULL,PRIMARY KEY(word_id,term));
CREATE TABLE example(id TEXT PRIMARY KEY,text TEXT NOT NULL,payload TEXT NOT NULL CHECK(json_valid(payload)));
CREATE TABLE word_example(word_id TEXT NOT NULL REFERENCES word(id),example_id TEXT NOT NULL REFERENCES example(id),sense_id TEXT NOT NULL,PRIMARY KEY(word_id,example_id,sense_id));
CREATE TABLE kana_word(kana_id TEXT NOT NULL REFERENCES kana(id),word_id TEXT NOT NULL REFERENCES word(id),reading TEXT NOT NULL,match_kind TEXT NOT NULL CHECK(match_kind IN ('whole_token','component_of_combination')),teaching_rank INTEGER NOT NULL,payload TEXT NOT NULL CHECK(json_valid(payload)),PRIMARY KEY(kana_id,word_id,reading,match_kind));
CREATE INDEX word_reading ON word(reading);
CREATE INDEX word_form ON word(form);
CREATE INDEX word_collection_lookup ON word_collection(collection,word_id);
CREATE INDEX word_search_term ON word_search(term);
CREATE INDEX word_example_lookup ON word_example(word_id);
CREATE INDEX kana_example_lookup ON kana_word(kana_id,match_kind,teaching_rank);
CREATE INDEX kana_word_reverse ON kana_word(word_id);
CREATE INDEX origin_kana ON origin(kana_id);
CREATE INDEX dialect_hanzi ON dialect(hanzi);
CREATE INDEX character_synthesis_hanzi ON character_synthesis(hanzi);
"""


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_database(path, data):
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        metadata = {
            "content_profile": "demo", "content_format": FORMAT,
            "content_revision": data["revision"],
            "audio_policy": {"personal_audio_included": False,
                "contains_audio_without_public_redistribution_clearance": False},
        }
        db.executemany("INSERT INTO metadata VALUES (?,?)", [(k, encoded(v)) for k, v in metadata.items()])
        for row in data["kana"]:
            db.execute("INSERT INTO kana VALUES (?,?,?,?,?,?,?)", (
                row["id"], row["hiragana"], row["katakana"], row["romaji"], "basic", "core", encoded(row)))
        for row in data["sources"]:
            db.execute("INSERT INTO source VALUES (?,?,?,?,?)", (row["id"], row["title"], row.get("url", ""), row.get("license"), encoded(row)))
        for row in data["examples"]:
            db.execute("INSERT INTO example VALUES (?,?,?)", (row["id"], row["text"], encoded(row)))
        for rank, row in enumerate(data["words"], 1):
            db.execute("INSERT INTO word VALUES (?,?,?,?,?,?,?)", (row["id"], row["form"], row["reading"], row["gloss"], "everyday_supplement", rank, encoded(row)))
            db.execute("INSERT INTO word_collection VALUES (?,?)", (row["id"], "everyday_supplement"))
            for term in sorted({row["form"], row["reading"], row["romaji"], row["gloss"]}):
                db.execute("INSERT INTO word_search VALUES (?,?,?)", (row["id"], term, 0))
            for identity in row.get("example_ids", []):
                db.execute("INSERT INTO word_example VALUES (?,?,?)", (row["id"], identity, "demo-1"))
            for kana in data["kana"]:
                if kana["hiragana"] in row["reading"]:
                    db.execute("INSERT INTO kana_word VALUES (?,?,?,?,?,?)", (kana["id"], row["id"], row["reading"], "whole_token", rank, "{}"))
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def _write_teaching(path, data):
    documents = [(key, encoded(value)) for key, value in sorted(data["teaching_namespaces"].items())]
    checksum = hashlib.sha256(json.dumps(documents, ensure_ascii=False).encode("utf-8")).hexdigest()
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE namespace(name TEXT PRIMARY KEY,payload_json TEXT NOT NULL CHECK(json_valid(payload_json)));""")
        db.executemany("INSERT INTO metadata VALUES (?,?)", [
            ("format", "kanamemory-teaching-content"), ("version", "1"), ("profile", "demo"),
            ("namespaces", json.dumps([name for name, _ in documents])), ("content_sha256", checksum)])
        db.executemany("INSERT INTO namespace VALUES (?,?)", documents)


def build_demo(output, source=None):
    source = Path(source or ROOT / "demo/content.json").resolve()
    output = Path(output).absolute()
    if output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError("Demo output must not use symbolic links")
    # Rebuilding into an empty/new folder is intentional: no stale media can
    # survive a switch from a private pack. Never delete a user's old pack.
    if output.exists() and any(output.iterdir()):
        raise ValueError("Demo output must be new or empty; choose a new directory")
    data = json.loads(source.read_text("utf-8"))
    if data.get("format") != FORMAT:
        raise ValueError("Not an original demo content source")
    output.mkdir(parents=True, exist_ok=True)
    _write_database(output / "content.sqlite3", data)
    _write_teaching(output / "teaching_content.sqlite3", data)
    (output / "learning_material_review.json").write_text(encoded({
        "format": "kanamemory-material-review", "version": 1,
        "revision": data["revision"], "items": {}, "plans": {}, "questions": {}, "examples": {}}) + "\n",
        encoding="utf-8", newline="\n")
    (output / "NOTICE.txt").write_text(data["notice"] + "\n\n" + (ROOT / "LICENSE").read_text("utf-8"),
                                     encoding="utf-8", newline="\n")
    manifest = {"format": FORMAT, "profile": "demo", "revision": data["revision"],
                "source_sha256": digest(source),
                "files": {p.name: digest(p) for p in sorted(output.iterdir()) if p.is_file()}}
    (output / "public-content-manifest.json").write_text(encoded(manifest) + "\n", encoding="utf-8", newline="\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "app/src/kanamemory/resources")
    parser.add_argument("--source", type=Path, default=ROOT / "demo/content.json")
    args = parser.parse_args()
    result = build_demo(args.output, args.source)
    print(json.dumps({"profile": result["profile"], "files": sorted(result["files"])}))


if __name__ == "__main__":
    main()
