"""Explicit, bounded live evaluation using original synthetic tasks only.

No learner database or production content is read. Keys stay in memory; provider
error bodies and exception traces are never exported. Semantic review is manual.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "src"))

from kanamemory.ai_service import AIServiceError, OpenAIProvider, validate_api_key, _post
from kanamemory.tutor_service import make_tutor_request


def _credential(path):
    value = os.environ.get("OPENAI_API_KEY", "")
    if not value and path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, raw = line.partition("=")
            if separator and key.strip() == "OPENAI_API_KEY":
                value = raw.strip().strip('"').strip("'")
                break
    return validate_api_key(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "docs" / "adaptive_ai_tutor_eval_cases.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-cases", type=int, default=18)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_cases <= 24:
        parser.error("Choose 1 to 24 cases")
    if args.output.exists():
        parser.error("Output exists; use a fresh path to retain previous evidence")
    source = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = source if isinstance(source, list) else source["cases"]
    if args.case_id:
        cases = [case for case in cases if case["id"] in args.case_id]
    cases = cases[:args.max_cases]
    requests = [(case, make_tutor_request(case["context"], case["intent"],
        "eval_" + str(index).zfill(4) + "_" + "20261029original", message=case.get("message", ""),
        messages=case.get("messages", []))) for index, case in enumerate(cases)]
    result = {"format": "kanamemory-tutor-evaluation", "version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "model": args.model,
        "live": args.live, "private_content_used": False, "automatic_semantic_certification": False,
        "rows": []}
    diagnostic = {}
    def transport(url, value, **kwargs):
        response = _post(url, value, **kwargs)
        # Only successful model text for these original fixtures; never HTTP
        # error bodies, request headers or credential-bearing arguments.
        parts = [part.get("text", "") for entry in response.get("output", []) if isinstance(entry, dict)
                 for part in entry.get("content", []) if isinstance(part, dict) and part.get("type") == "output_text"]
        diagnostic["text"] = re.sub(r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}", "[redacted]", "".join(parts))[:16000]
        return response
    provider = OpenAIProvider(_credential(ROOT / ".env.local"), model=args.model, transport=transport) if args.live else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for case, request in requests:
        row = {"id": case["id"], "intent": case["intent"], "rubric": case.get("rubric", []),
               "status": "validated_only", "semantic_review": "pending"}
        start = time.monotonic()
        if provider:
            diagnostic.clear()
            try:
                row["response"] = provider.tutor(request)
                row["status"] = "response_validated"
            except AIServiceError as exc:
                row.update(status="request_failed", error_code=exc.code)
                if exc.code == "tutor_format" and diagnostic.get("text"):
                    row["unaccepted_model_text"] = diagnostic["text"]
            except Exception:
                row.update(status="request_failed", error_code="unexpected_error_redacted")
            row["usage"] = dict(provider.last_usage)
        row["elapsed_seconds"] = round(time.monotonic() - start, 3)
        result["rows"].append(row)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"id": row["id"], "status": row["status"], "seconds": row["elapsed_seconds"]}), flush=True)
        # Unavailable model/credentials/quota: no blind repeat against all cases.
        if row.get("error_code") in {"credentials", "quota", "http_404", "http_400"}:
            break
    print(json.dumps({"completed": len(result["rows"]), "output": str(args.output),
        "validated": sum(row["status"] == "response_validated" for row in result["rows"])}))


if __name__ == "__main__":
    main()
