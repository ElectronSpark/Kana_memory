"""Compile explicit, complete per-sentence judgments; never infer approval.

The read ledger is an audit artifact. This compiler checks coverage, immutable
source fingerprints, anchors and dictionary identities, not linguistic truth.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "src"))
from kanamemory.lexical_review import FORMAT, VERSION, fingerprint, word_fingerprint, reading_key


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def checked(condition, message):
    if not condition:
        raise ValueError(message)


def indexed(values, length, label):
    checked(isinstance(values, list), label + " must be a list")
    checked(all(type(x.get("index")) is int for x in values), label + " missing index")
    checked(len(values) == length and {x["index"] for x in values} == set(range(length)),
            label + " must decide every source index exactly once")
    return sorted(values, key=lambda x: x["index"])


def resolve_reading(association, word):
    """A unique dictionary reading may be copied; ambiguous ones need judgment."""
    if association.get("reading_status") == "uncertain":
        return ""
    senses = [s for s in word["senses"] if s["sense_id"] in association["sense_ids"]]
    readings = {r["text"] for r in word.get("readings", [])
                if "search-only kana form" not in r.get("info", [])
                and all(not s.get("reading_restrictions") or r["text"] in s["reading_restrictions"] for s in senses)}
    explicit = association.get("reading")
    if explicit:
        checked(explicit in readings, "review reading is absent or sense-restricted: " + str(explicit))
        return explicit
    return next(iter(readings)) if len(readings) == 1 else ""


_NOTE_URL = re.compile(r"(?<![\w])https?://[^\s<>\"'`“”‘’，。；：！？、…]+", re.IGNORECASE)
_URL_BRACKETS = {"(": ")", "（": "）", "[": "]", "【": "】", "{": "}",
                 "「": "」", "『": "』", "〈": "〉", "《": "》"}


def review_sources(notes, existing=()):
    """Append only URLs explicitly written in notes, retaining source order."""
    sources = []
    # Older reviews may store one source as a string rather than a list.
    for value in [existing] if isinstance(existing, str) else existing:
        if value not in sources:
            sources.append(deepcopy(value))
    if not isinstance(notes, str):
        return sources
    closing = {right: left for left, right in _URL_BRACKETS.items()}
    position = 0
    while match := _NOTE_URL.search(notes, position):
        candidate = match.group()
        depths = dict.fromkeys(_URL_BRACKETS, 0)
        for index, char in enumerate(candidate):
            if char in depths:
                depths[char] += 1
            elif char in closing:
                opener = closing[char]
                if not depths[opener]:
                    # A prose/Markdown wrapper ends the URL, but balanced
                    # parentheses such as /word/後_(あと)/ belong to its path.
                    candidate = candidate[:index]
                    break
                depths[opener] -= 1
        position = match.start() + len(candidate)
        candidate = candidate.rstrip(".,;:!?")
        try:
            parsed = urlsplit(candidate)
            valid = parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
        except ValueError:
            valid = False
        if valid and candidate not in sources:
            sources.append(candidate)
    return sources


def normalize_review(source, decision, words):
    identity, original = source["id"], source["payload"]
    checked(decision.get("id") == identity, "review identity mismatch")
    expected = fingerprint(identity, source["text"], original)
    checked(source.get("input_sha256") == expected == decision.get("input_sha256"), identity + ": source fingerprint mismatch")
    checked(all(k in decision for k in ("japanese", "translations", "chinese_translation", "associations", "notes")), identity + ": incomplete judgment")
    checked(not decision.get("course_refs"), identity + ": explicit course_refs require a shared-example pack; review v1 only maps dictionary associations")
    jp = decision["japanese"]
    checked(jp.get("status") in {"reviewed", "corrected", "uncertain"}, identity + ": invalid Japanese status")
    checked(jp.get("status") == "reviewed" or bool(jp.get("reason")), identity + ": missing Japanese reason")
    text = jp.get("text", source["text"])
    checked(isinstance(text, str) and 0 < len(text.strip()) <= 4000, identity + ": missing Japanese text")
    checked(text == source["text"] or jp["status"] == "corrected", identity + ": text changed without correction")
    chinese = decision["chinese_translation"]
    checked(chinese.get("status") in {"reviewed", "uncertain"} and isinstance(chinese.get("text"), str)
            and 0 < len(chinese["text"].strip()) <= 4000, identity + ": missing Chinese decision")
    checked(chinese["status"] != "uncertain" or bool(chinese.get("reason")), identity + ": missing Chinese uncertainty reason")
    original_translations = original.get("translations", [])
    translations = indexed(decision["translations"], len(original_translations), identity + ": translations")
    for t in translations:
        checked(t.get("status") in {"reviewed", "corrected", "uncertain", "rejected"}, identity + ": translation status")
        checked(t["status"] == "reviewed" or bool(t.get("reason")), identity + ": translation reason")
        checked(t["status"] != "corrected" or bool(t.get("text", "").strip()), identity + ": corrected translation absent")
    associations = deepcopy(indexed(decision["associations"], len(original.get("associations", [])), identity + ": associations"))
    for a in associations:
        checked(a.get("status") in {"confirmed", "corrected", "uncertain", "rejected"}, identity + ": association status")
        checked(a.get("entry_id") in words and bool(a.get("reason")), identity + ": association entry/reason")
        word = words[a["entry_id"]]
        senses = {s["sense_id"] for s in word["senses"]}
        checked(isinstance(a.get("sense_ids"), list) and set(a["sense_ids"]) <= senses, identity + ": unknown sense")
        checked(len(a["sense_ids"]) == len(set(a["sense_ids"])), identity + ": duplicate sense")
        checked(isinstance(a.get("spans"), list), identity + ": spans missing")
        for span in a["spans"]:
            start, end, quote = span.get("start"), span.get("end"), span.get("quote")
            checked(type(start) is int and type(end) is int and 0 <= start < end <= len(text)
                    and text[start:end] == quote, identity + ": anchor mismatch")
        if a["status"] in {"confirmed", "corrected"}:
            checked(bool(a["sense_ids"]) and bool(a["spans"]), identity + ": accepted association needs sense and span")
            before = original["associations"][a["index"]]
            if (before.get("entry_id") != a["entry_id"] or before.get("sense_id")
                    and before["sense_id"] not in a["sense_ids"]):
                a["status"] = "corrected"
            a["resolved_reading"] = resolve_reading(a, word)
        else:
            checked(not a["sense_ids"], identity + ": inactive association must not assert a resolved sense")
            a["resolved_reading"] = ""
        a["active"] = bool(a["status"] in {"confirmed", "corrected"} and a["resolved_reading"]
                           and jp["status"] != "uncertain" and chinese["status"] == "reviewed")
    statuses = [jp["status"], chinese["status"], *(t["status"] for t in translations), *(a["status"] for a in associations)]
    reading_unresolved = any(a["status"] in {"confirmed", "corrected"} and not a["resolved_reading"] for a in associations)
    state = "uncertain" if "uncertain" in statuses or reading_unresolved else "corrected" if {"corrected", "rejected"} & set(statuses) else "reviewed"
    return {**deepcopy(decision), "text": text, "state": state, "associations": associations,
            "sources": review_sources(decision["notes"], decision.get("sources", [])),
            "review_method": "model_semantic_review_with_references; not_native_editor_certification"}


def compile_database(baselines, decisions, words, destination, *, expected_count=8823):
    checked(len(baselines) == expected_count, "unexpected source coverage")
    source_by_id = {s["id"]: s for s in baselines}
    checked(len(source_by_id) == expected_count, "duplicate source ID")
    by_id = {d["id"]: d for d in decisions}
    checked(len(decisions) == expected_count and len(by_id) == expected_count
            and set(by_id) == set(source_by_id), "every candidate needs one explicit review")
    reviews = [normalize_review(s, by_id[s["id"]], words) for s in baselines]
    stats = Counter()
    for row in reviews:
        stats["sentences"] += 1
        stats["sentence_" + row["state"]] += 1
        stats["japanese_" + row["japanese"]["status"]] += 1
        stats["chinese_" + row["chinese_translation"]["status"]] += 1
        if not source_by_id[row["id"]]["payload"].get("translations"):
            stats["previously_missing_translation"] += 1
        for t in row["translations"]:
            stats["translation_" + t["status"]] += 1
        for a in row["associations"]:
            stats["association_" + a["status"]] += 1
            stats["active_associations"] += int(a["active"])
            if a["status"] in {"confirmed", "corrected"} and not a["resolved_reading"]:
                stats["accepted_sense_reading_unresolved"] += 1
    meta = {"format": FORMAT, "version": VERSION, "complete": True, "counts": dict(stats),
            "source_manifest_sha256": hashlib.sha256(encoded([(s["id"], s["input_sha256"]) for s in baselines]).encode()).hexdigest(),
            "review_sha256": hashlib.sha256(encoded(reviews).encode()).hexdigest(),
            "review_method": "model_semantic_review_with_references; not_native_editor_certification"}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".building.sqlite3")
    if temporary.exists():
        temporary.unlink()
    db = sqlite3.connect(temporary)
    try:
        db.executescript("""CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE reviewed_word(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL);
            CREATE TABLE review(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,state TEXT NOT NULL,
                search_text TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE association(entry_id TEXT NOT NULL,example_id TEXT NOT NULL REFERENCES review(id),
                sense_id TEXT NOT NULL,reading TEXT NOT NULL,status TEXT NOT NULL,active INTEGER NOT NULL);
            CREATE INDEX association_target ON association(entry_id,sense_id,reading,active);
            CREATE INDEX association_example ON association(example_id);
            CREATE INDEX review_state ON review(state,id);""")
        db.executemany("INSERT INTO metadata VALUES(?,?)", [(k, encoded(v)) for k, v in meta.items()])
        db.executemany("INSERT INTO reviewed_word VALUES(?,?)", [(k, word_fingerprint(v)) for k, v in sorted(words.items())])
        for row in reviews:
            db.execute("INSERT INTO review VALUES(?,?,?,?,?)", (row["id"], row["input_sha256"], row["state"],
                row["text"] + "\n" + row["chinese_translation"]["text"], encoded(row)))
            for link in row["associations"]:
                for sense in link["sense_ids"] or [""]:
                    db.execute("INSERT INTO association VALUES(?,?,?,?,?,?)", (link["entry_id"], row["id"],
                        sense, reading_key(link["resolved_reading"]), link["status"], int(link["active"])))
        db.commit()
        checked(db.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "review database integrity failed")
    finally:
        db.close()
    temporary.replace(destination)
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=ROOT / "research" / "lexical_sentence_audit")
    parser.add_argument("--output", type=Path, default=ROOT / "app/src/kanamemory/resources/lexical_sentence_review.sqlite3")
    parser.add_argument("--content", type=Path, default=ROOT / "app/src/kanamemory/resources/content.sqlite3")
    args = parser.parse_args()
    from check_lexical_review_snapshot import check_snapshot
    check_snapshot(args.content, args.audit)
    baselines = [json.loads(line) for line in (args.audit / "baseline_examples.jsonl").read_text(encoding="utf-8").splitlines()]
    words = json.loads((args.audit / "baseline_words.json").read_text(encoding="utf-8"))
    decisions = []
    for part in range(1, 4):
        folder = args.audit / f"part_{part}"
        assignment = json.loads((folder / "assignment.json").read_text(encoding="utf-8"))
        rows = []
        for file in sorted(folder.glob("reviews_*.json")):
            rows.extend(json.loads(file.read_text(encoding="utf-8")))
        expected = assignment.get("ids") or assignment.get("example_ids")
        checked(expected is not None and len(rows) == len(expected) and {r["id"] for r in rows} == set(expected), f"part {part} coverage incomplete")
        decisions.extend(rows)
    meta = compile_database(baselines, decisions, words, args.output)
    (args.audit / "compilation_report.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
