"""List the typed templates without a kana answer (design 03 rule T9; design 08 pack check).

The app's own rule decides (kanamemory.answer_policy.kana_answer.has_kana_answer through
answer_input.available, with the effective answer policy): a typed template is checked as the app's
selection checks it, as its raw template with the lesson's kind, family, domain and id, and its answer
overlay row. Such a template is never served.

    .venv/Scripts/python.exe -X utf8 tools/typed_answer_check.py [--teaching-pack DB] [--json OUT]

--teaching-pack: check that teaching database (in a separate process, with no installed library) instead
of the content this tree loads. Writes nothing unless --json names a file ("-": standard output).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "src"))


def typed_templates(items=None):
    """(item, kind, variant, raw template, the question the selection checks) of every typed template."""
    from kanamemory.learning_content import learning_items
    from kanamemory.learning_questions import practice_templates
    for item in items if items is not None else learning_items():
        try:
            templates = practice_templates(item)
        except (ValueError, KeyError, TypeError):
            continue
        for kind, variants in (templates or {}).items():
            if not isinstance(variants, list):
                continue
            for variant, raw in enumerate(variants):
                if not isinstance(raw, dict) or raw.get("input_mode", "choice") != "text":
                    continue
                question = dict(raw, kind=kind, family=item.get("family"), domain=item.get("domain"),
                                target_id=raw.get("target_id", item.get("id")))
                yield item, kind, variant, raw, question


def without_kana(items=None, content_ids=None):
    """Rows {content_id, kind, variant, template_sha256, answer} of the typed templates without a kana answer."""
    from kanamemory.answer_input import japanese_text
    from kanamemory.answer_policy import source
    from kanamemory.answer_policy.kana_answer import has_kana_answer
    from kanamemory.learning_questions import template_fingerprint
    rows = []
    for item, kind, variant, raw, question in typed_templates(items):
        if content_ids is not None and item.get("id") not in content_ids:
            continue
        if not japanese_text(question):
            continue
        policy = source.policy_for(question, row_value=source.template_row(item.get("id"), kind, raw))
        if not has_kana_answer(question, policy):
            rows.append({"content_id": item.get("id"), "kind": kind, "variant": variant,
                         "template_sha256": template_fingerprint(raw), "answer": raw.get("answer")})
    return rows


def check_pack(teaching_db, content_ids=None):
    """without_kana for a teaching database, in a separate process (no installed library is read)."""
    with tempfile.TemporaryDirectory(prefix="km-typed-") as folder:
        env = dict(os.environ, KANAMEMORY_TEACHING_PACK=str(Path(teaching_db).resolve()),
                   KANAMEMORY_LIBRARY_ROOT=str(Path(folder) / "libraries"), PYTHONDONTWRITEBYTECODE="1")
        out = Path(folder) / "rows.json"
        command = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--json", str(out)]
        if content_ids is not None:
            ids = Path(folder) / "ids.json"
            ids.write_text(json.dumps(sorted(content_ids), ensure_ascii=False), encoding="utf-8")
            command += ["--ids", str(ids)]
        done = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8")
        if done.returncode != 0:
            raise RuntimeError("typed answer check failed: " + (done.stderr or done.stdout)[-2000:])
        return json.loads(out.read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--teaching-pack", type=Path)
    parser.add_argument("--json", help="write the rows as JSON to this file ('-': standard output)")
    parser.add_argument("--ids", type=Path, help=argparse.SUPPRESS)  # check_pack: limit to these content ids
    args = parser.parse_args(argv)
    if args.teaching_pack is not None:
        rows = check_pack(args.teaching_pack)
    else:
        ids = set(json.loads(args.ids.read_text(encoding="utf-8"))) if args.ids else None
        rows = without_kana(content_ids=ids)
    text = json.dumps(rows, ensure_ascii=False, indent=1)
    if args.json == "-":
        print(text)
    elif args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    else:
        for row in rows:
            print("\t".join(str(row[key]) for key in ("content_id", "kind", "variant", "template_sha256", "answer")))
        print(f"{len(rows)} typed templates without a kana answer", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
