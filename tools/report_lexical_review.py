"""Check explicit review coverage and expose follow-up work without approving it."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from build_lexical_review import ROOT, normalize_review


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def report(audit, *, allow_partial=False):
    baseline = {r["id"]: r for r in map(json.loads, (audit / "baseline_examples.jsonl").read_text(encoding="utf-8").splitlines())}
    words = json.loads((audit / "baseline_words.json").read_text(encoding="utf-8"))
    seen, errors, changes, parts = set(), [], [], []
    counts = Counter()
    for part in range(1, 4):
        folder = audit / f"part_{part}"
        assignment = json.loads((folder / "assignment.json").read_text(encoding="utf-8"))
        expected = assignment.get("ids") or assignment["example_ids"]
        part_seen, readings = set(), []
        for file in sorted(folder.glob("reviews_*.json")):
            for decision in json.loads(file.read_text(encoding="utf-8")):
                identity = decision["id"]
                if identity in seen or identity not in expected:
                    errors.append({"id": identity, "part": part, "file": file.name, "error": "duplicate or unassigned review"})
                    continue
                seen.add(identity)
                part_seen.add(identity)
                try:
                    row = normalize_review(baseline[identity], decision, words)
                except (ValueError, KeyError, TypeError) as error:
                    errors.append({"id": identity, "part": part, "file": file.name, "error": str(error)})
                    continue
                counts["sentences"] += 1
                counts["sentence_" + row["state"]] += 1
                counts["japanese_" + row["japanese"]["status"]] += 1
                counts["chinese_" + row["chinese_translation"]["status"]] += 1
                if not baseline[identity]["payload"].get("translations"):
                    counts["previously_missing_translation"] += 1
                for t in row["translations"]:
                    counts["translation_" + t["status"]] += 1
                for a in row["associations"]:
                    counts["association_" + a["status"]] += 1
                    counts["active_associations"] += int(a["active"])
                    if a["status"] in {"confirmed", "corrected"} and not a["resolved_reading"]:
                        counts["accepted_sense_reading_unresolved"] += 1
                        readings.append({"id": identity, "file": file.name, "association_index": a["index"],
                            "text": row["text"], "chinese": row["chinese_translation"],
                            "association": a, "readings": words[a["entry_id"]]["readings"]})
                if row["state"] != "reviewed":
                    changes.append({"id": identity, "part": part, "file": file.name,
                        "original": baseline[identity], "review": row})
        write_json(folder / "reading_followup.json", readings)
        parts.append({"part": part, "reviewed": len(part_seen), "expected": len(expected),
            "missing": [identity for identity in expected if identity not in part_seen],
            "reading_unresolved": len(readings)})
    complete = set(baseline) == seen and not errors
    result = {"complete": complete, "semantic_review_method": "model review with references, not native-editor certification",
        "validation_scope": "Coverage, identity, schema and anchors only; this report does not infer linguistic approval.",
        "counts": dict(counts), "parts": parts, "errors": errors}
    write_json(audit / "validation_report.json", result)
    write_json(audit / "findings.json", changes)
    print(json.dumps({**result, "parts": [{k: v for k, v in p.items() if k != "missing"} for p in parts]}, ensure_ascii=False, indent=2))
    if errors or (not allow_partial and not complete):
        raise SystemExit(1)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, default=ROOT / "research/lexical_sentence_audit")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    report(args.audit, allow_partial=args.allow_partial)
