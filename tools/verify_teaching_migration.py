"""Compare runtime teaching outputs with an externally saved migration baseline.

No production sentences or expected catalogue IDs are stored in this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "src"))


def runtime_contract():
    from kanamemory import learning_content as lc, example_catalogue as ec
    from kanamemory import structured_lessons as sl, lesson_diagrams as ld, vocabulary_lessons as vl
    items = lc.learning_items()
    return {"items": items, "raw_items": list(lc._ITEMS.values()), "sources": lc.learning_sources(),
            "examples": ec.bundled_example_pack(items),
            "examples_raw": ec.bundled_example_pack(list(lc._ITEMS.values()), apply_review=False),
            "structured_catalogue": sl.catalogue(), "lesson_views": [sl.lesson_view(i) for i in items],
            "diagram_catalogue": ld.catalogue(), "diagrams": [ld.diagram_for(i["id"]) for i in items],
            "vocabulary_specs": vl.lesson_specs()}


def verify(baseline):
    from kanamemory.content_pack import encode_value, decode_value
    expected = decode_value(json.loads(Path(baseline).read_text(encoding="utf-8")))
    actual = runtime_contract()
    checks = {key: actual.get(key) == value for key, value in expected.items()}
    raw = json.dumps(encode_value(actual), ensure_ascii=False, separators=(",", ":"))
    result = {"passed": all(checks.values()) and expected.keys() == actual.keys(), "checks": checks,
              "items": len(actual["items"]), "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = verify(args.baseline)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered)
    raise SystemExit(0 if result["passed"] else 1)
