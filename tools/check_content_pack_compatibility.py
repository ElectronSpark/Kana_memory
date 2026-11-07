"""Fingerprint observable teaching behavior in an isolated source checkout.

Run separately against a frozen pre-migration checkout and the current checkout.
The report contains identifiers and hashes, not the licensed lesson text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


def canonical(value):
    if isinstance(value, dict):
        return {"dict": [[canonical(k), canonical(v)] for k, v in value.items()]}
    if isinstance(value, (list, tuple)):
        return {type(value).__name__: [canonical(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        return {type(value).__name__: sorted((canonical(v) for v in value), key=repr)}
    return value


def fingerprint(value):
    raw = json.dumps(canonical(value), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def snapshot(source):
    sys.path.insert(0, str(source / "app" / "src"))
    from kanamemory.learning_content import learning_items, learning_sources
    from kanamemory.example_catalogue import bundled_example_pack
    from kanamemory.structured_lessons import lesson_view
    from kanamemory.lesson_diagrams import diagram_for
    from kanamemory.learning_questions import question_kinds, build_learning_question
    from kanamemory.study_path_catalog import build_study_path
    from kanamemory.content import ContentRepository
    items = learning_items()
    observed = {"items": fingerprint(items), "sources": fingerprint(learning_sources()),
                "examples": fingerprint(bundled_example_pack(items))}
    for item in items:
        identity = item["id"]
        observed["item/" + identity] = fingerprint(item)
        observed["lesson/" + identity] = fingerprint(lesson_view(item))
        observed["diagram/" + identity] = fingerprint(diagram_for(identity))
        for kind in question_kinds(identity):
            for variant in range(3):
                for guided in (False, True):
                    key = f"question/{identity}/{kind}/{variant}/{guided}"
                    observed[key] = fingerprint(build_learning_question(identity, kind, variant=variant, guided=guided))
    repo = ContentRepository(source / "app/src/kanamemory/resources/content.sqlite3")
    observed["study_path"] = fingerprint(build_study_path(repo.kana_list(), items))
    return {"format": "kanamemory-content-compatibility", "version": 1,
            "item_count": len(items), "observations": observed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    report = snapshot(args.source.resolve())
    if args.baseline:
        before = json.loads(args.baseline.read_text(encoding="utf-8"))
        old, new = before["observations"], report["observations"]
        report["comparison"] = {
            "added": sorted(set(new) - set(old)), "removed": sorted(set(old) - set(new)),
            "changed": sorted(key for key in old.keys() & new.keys() if old[key] != new[key])}
        report["passed"] = not any(report["comparison"].values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"items": report["item_count"], "observations": len(report["observations"]),
                      "passed": report.get("passed"), "comparison": report.get("comparison")}, ensure_ascii=False))
    if report.get("passed") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
