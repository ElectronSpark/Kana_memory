"""Check the immutable review input against the current packaged dictionary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/src"))
from kanamemory.lexical_review import fingerprint


def check_snapshot(content_path, audit):
    baselines = [json.loads(x) for x in (audit / "baseline_examples.jsonl").read_text(encoding="utf-8").splitlines()]
    words = json.loads((audit / "baseline_words.json").read_text(encoding="utf-8"))
    db = sqlite3.connect(Path(content_path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        actual = {identity: fingerprint(identity, text, json.loads(payload))
                  for identity, text, payload in db.execute("SELECT id,text,payload FROM example")}
        expected = {s["id"]: s["input_sha256"] for s in baselines}
        if len(baselines) != len(expected) or actual != expected:
            raise ValueError("Candidate source snapshot differs from the packaged dictionary.")
        source_words = {identity: json.loads(payload) for identity, payload in db.execute("SELECT id,payload FROM word")}
        if source_words != words:
            raise ValueError("Dictionary senses or restrictions changed after review assignment.")
    finally:
        db.close()
    assigned = []
    for part in range(1, 4):
        assignment = json.loads((audit / f"part_{part}/assignment.json").read_text(encoding="utf-8"))
        if len(assignment["ids"]) != assignment["count"]:
            raise ValueError("Assignment count mismatch.")
        assigned.extend(assignment["ids"])
    if len(assigned) != len(expected) or set(assigned) != set(expected):
        raise ValueError("Assignments do not cover each source exactly once.")
    return {"sentences": len(actual), "words": len(words), "snapshot_unchanged": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", type=Path, default=ROOT / "app/src/kanamemory/resources/content.sqlite3")
    parser.add_argument("--audit", type=Path, default=ROOT / "research/lexical_sentence_audit")
    args = parser.parse_args()
    print(json.dumps(check_snapshot(args.content, args.audit), ensure_ascii=False))
