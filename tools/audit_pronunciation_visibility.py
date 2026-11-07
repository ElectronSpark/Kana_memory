"""Compare current pronunciation resources to released APKs, without mutation.

This checks resource bytes and UI data projection. Audio decoding is performed
separately by audit_chinese_reading_audio.py --decode; neither is a listening test.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/src"))
from kanamemory.content import ContentRepository
from kanamemory.detail_content import dialect_entry, is_kana_combination
from kanamemory.detail_enrichment import history_entries
from kanamemory.pronunciation_sections import pronunciation_sections


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def snapshot(raw, read_resource):
    db = sqlite3.connect(":memory:")
    try:
        db.deserialize(raw)
        counts = {table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                  for table in ("dialect", "historical", "character_synthesis")}
        readings, demos = [], []
        for table in ("dialect", "historical"):
            rows = [json.loads(payload) for payload, in db.execute("SELECT payload FROM " + table)]
            linked = [audio for row in rows for audio in row.get("reading_audio", [])]
            counts[table + "_audio"] = len(linked)
            readings.extend(linked)
        demos = [json.loads(payload) for payload, in db.execute("SELECT payload FROM character_synthesis")]
        policy = json.loads(db.execute("SELECT value FROM metadata WHERE key='audio_policy'").fetchone()[0])
    finally:
        db.close()
    media, errors = {}, []
    for audio in [*readings, *demos]:
        path = audio["local_path"]
        if path in media:
            if media[path] != audio["sha256"]:
                errors.append("conflicting hash: " + path)
            continue
        try:
            data = read_resource(path)
        except (OSError, KeyError) as exc:
            errors.append(f"missing {path}: {type(exc).__name__}")
            continue
        digest = sha(data)
        if digest != audio["sha256"] or len(data) != audio["bytes"]:
            errors.append("size/hash mismatch: " + path)
        media[path] = digest
    return {"database_sha256": sha(raw), "counts": counts,
            "personal_audio_included": policy.get("personal_audio_included"),
            "verified_audio_file_count": len(media), "audio_file_hashes": media, "errors": errors}


def audit(apks):
    resources = ROOT / "app/src/kanamemory/resources"
    current = snapshot((resources / "content.sqlite3").read_bytes(), lambda name: (resources / name).read_bytes())
    releases = []
    for apk in apks:
        with zipfile.ZipFile(apk) as outer:
            with zipfile.ZipFile(io.BytesIO(outer.read("assets/chaquopy/app.imy"))) as inner:
                prefix = "kanamemory/resources/"
                result = snapshot(inner.read(prefix + "content.sqlite3"), lambda name: inner.read(prefix + name))
        result.update(apk=str(apk.relative_to(ROOT)) if apk.is_relative_to(ROOT) else str(apk),
                      database_matches_current=result["database_sha256"] == current["database_sha256"],
                      audio_files_match_current=result["audio_file_hashes"] == current["audio_file_hashes"])
        releases.append(result)
    visibility = []
    with ContentRepository(resources / "content.sqlite3") as repo:
        for summary in repo.kana_list():
            if is_kana_combination(summary):
                continue
            item = repo.get_kana(summary["id"])
            history = history_entries(item)
            modern = tuple(dialect_entry(row) for row in item["dialects"])
            sections = pronunciation_sections(dialects=item["dialects"],
                historical=item.get("historical_reading_references") or item.get("component_historical_reading_references") or [],
                component_history=not item.get("historical_reading_references"), context=item)
            row = {"id": item["id"], "hiragana": item["hiragana"], "katakana": item["katakana"]}
            for kind, entries in (("historical", history), ("modern", modern)):
                first = entries[:4] if len(entries) > 6 else entries
                row[kind] = {"cards": len(entries), "audio_card_positions_one_based": [i + 1 for i, entry in enumerate(entries) if entry.audio],
                    "old_initial_audio_count": sum(len(entry.audio) for entry in first),
                    "all_audio_count": sum(len(entry.audio) for entry in entries)}
            row["groups"] = [{"key": section.key, "readings": section.reading_count, "available_audio": section.audio_count}
                             for section in sections]
            visibility.append(row)
    report = {"current": current, "releases": releases, "kana_visibility": visibility,
              "previously_audio_hidden_kana_pages": {
                  kind: sum(row[kind]["all_audio_count"] > 0 and row[kind]["old_initial_audio_count"] == 0 for row in visibility)
                  for kind in ("historical", "modern")},
              "scope": "Resource integrity and reading-card projection; not a native playback or pronunciation listening assessment."}
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, action="append")
    parser.add_argument("--output", type=Path, default=ROOT / "research/pronunciation_visibility_audit.json")
    args = parser.parse_args()
    apks = args.apk or [ROOT / "environment/artifacts" / f"KanaMemory-{version}.debug.apk" for version in ("0.6.0", "0.6.1")]
    report = audit([path.resolve() for path in apks])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"current": {k: v for k, v in report["current"].items() if k != "audio_file_hashes"},
                      "releases": [{k: v for k, v in release.items() if k != "audio_file_hashes"} for release in report["releases"]],
                      "previously_audio_hidden_kana_pages": report["previously_audio_hidden_kana_pages"]}, ensure_ascii=False, indent=2))
    if any(result["errors"] for result in [report["current"], *report["releases"]]):
        raise SystemExit(1)
