"""Build the answer_overlay teaching namespace from reviewed tables (design 03 and 08).

    .venv/Scripts/python.exe -X utf8 tools/build_answer_overlay.py --table T.tsv [--table ...] --check
    .venv/Scripts/python.exe -X utf8 tools/build_answer_overlay.py --table T.tsv --write [--teaching DIR]

A table is UTF-8 TSV with a header row and the columns
    sha           the template sha256 (learning_questions.template_fingerprint); a unique prefix of at
                  least 12 hex digits is accepted
    content_id    the lesson of the template
    kind          the question kind of the template
    strict        1, 0, or empty (unchanged)
    accepted_add  answers accepted besides the template's, separated by ｜
    known_wrong   wrong answers with their feedback, "wrong answer=>feedback", separated by ｜
    fold          1 (the Chinese hint waits behind the hint button) or empty
A row with all five fields empty removes the template's row. The templates are the ones this tree's
content holds (the bundled resources, or KANAMEMORY_TEACHING_PACK). A row is refused, naming its table
and line, for: an unknown or ambiguous sha; a content id or kind that is not the template's; strict,
accepted_add or known_wrong on a template that is not typed; an added form that is empty, already
accepted, or equal (normalised as the judge compares) to a known-wrong form; a known-wrong form the
judge accepts; fold on a question without a folded hint; an added form a gloss of the question shows.
--check reports and writes nothing (exit 1 with refusals). --write needs no refusal and stores the
namespace document answer_overlay.json in the content source directory (--teaching, default
data/content_packs/full/teaching, where question_readings.json is kept): the rows of the listed
templates are replaced, other rows are kept, and rows whose template no longer exists are reported and
removed. Never writes anywhere else.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "src"))

from kanamemory.answer_policy import overlay  # noqa: E402

COLUMNS = ("sha", "content_id", "kind", "strict", "accepted_add", "known_wrong", "fold")
SEPARATOR = "｜"
ARROW = "=>"
DEFAULT_TEACHING = ROOT / "data" / "content_packs" / "full" / "teaching"
OUTPUT_NAME = overlay.NAMESPACE + ".json"
_HEX = re.compile(r"[0-9a-f]{12,64}\Z")


class TableError(ValueError):
    """A table that cannot be read at all (missing columns, not UTF-8)."""


def installed_templates():
    """{sha256: [(item, kind, variant, raw template), ...]} of every template the content holds (one raw
    template can be held by two lessons or kinds)."""
    from kanamemory.learning_content import learning_items
    from kanamemory.learning_questions import practice_templates, template_fingerprint
    found = {}
    for item in learning_items():
        try:
            templates = practice_templates(item)
        except (ValueError, KeyError, TypeError):
            continue
        for kind, variants in (templates or {}).items():
            if not isinstance(variants, list):
                continue
            for variant, raw in enumerate(variants):
                if isinstance(raw, dict):
                    found.setdefault(template_fingerprint(raw), []).append((item, kind, variant, raw))
    return found


def read_table(path):
    """[(line number, {column: text})] of one table."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise TableError(f"{path}: not UTF-8") from error
    rows = list(csv.reader(text.splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE))
    if not rows:
        raise TableError(f"{path}: empty table")
    header = [name.strip() for name in rows[0]]
    missing = [name for name in COLUMNS if name not in header]
    if missing:
        raise TableError(f"{path}: missing columns " + ", ".join(missing))
    result = []
    for number, values in enumerate(rows[1:], start=2):
        if not any(value.strip() for value in values):
            continue
        values = values + [""] * (len(header) - len(values))
        result.append((number, {name: values[header.index(name)].strip() for name in COLUMNS}))
    return result


def _split(text):
    return [part.strip() for part in text.split(SEPARATOR)] if text.strip() else []


def _resolve(prefix, templates):
    if not _HEX.fullmatch(prefix):
        return None, "the sha is not 12 to 64 lowercase hex digits"
    matches = [sha for sha in templates if sha.startswith(prefix)]
    if not matches:
        return None, "unknown template sha " + prefix
    if len(matches) > 1:
        return None, f"ambiguous template sha {prefix} ({len(matches)} templates)"
    return matches[0], None


def _question(item, kind, variant):
    from kanamemory.learning_questions import build_learning_question
    return build_learning_question(item, kind, variant=variant)


def check_row(fields, templates):
    """(sha, content id, kind, row or None to remove, [problems]) of one table row."""
    sha, problem = _resolve(fields["sha"], templates)
    if problem:
        return None, None, None, None, [problem]
    holders = templates[sha]
    item, kind, variant, raw = next((held for held in holders if held[0].get("id") == fields["content_id"]
                                     and held[1] == fields["kind"]), holders[0])
    problems = []
    if fields["content_id"] != item.get("id"):
        problems.append(f"content_id {fields['content_id']} is not the template's ({item.get('id')})")
    if fields["kind"] != kind:
        problems.append(f"kind {fields['kind']} is not the template's ({kind})")
    row = {}
    if fields["strict"] not in ("", "0", "1"):
        problems.append("strict must be 1, 0 or empty")
    elif fields["strict"]:
        row["strict"] = fields["strict"] == "1"
    if fields["fold"] not in ("", "1"):
        problems.append("fold must be 1 or empty")
    elif fields["fold"]:
        row["fold"] = True
    added = _split(fields["accepted_add"])
    wrong = []
    for part in _split(fields["known_wrong"]):
        answer, arrow, feedback = part.partition(ARROW)
        if not arrow or not answer.strip() or not feedback.strip():
            problems.append(f"known_wrong entry {part!r} is not 'wrong answer=>feedback'")
        else:
            wrong.append({"answer": answer.strip(), "feedback": feedback.strip()})
    if added:
        row["accepted"] = added
    if wrong:
        row["known_wrong"] = wrong
    if problems:
        return sha, item.get("id"), kind, None, problems
    typed = raw.get("input_mode", "choice") == "text"
    if not typed and set(row) & {"strict", "accepted", "known_wrong"}:
        problems.append("strict, accepted_add and known_wrong need a typed template")
    try:
        question = _question(item, kind, variant)
    except (ValueError, KeyError, TypeError) as error:
        return sha, item.get("id"), kind, None, [*problems, f"the template does not build: {error}"]
    problems.extend(_policy_problems(question, row, added, wrong))
    if row.get("fold"):
        from kanamemory.question_presentation import question_presentation
        if not question_presentation(question)["folded_hint"]:
            problems.append("fold on a question without a folded hint")
    if added:
        from kanamemory.question_support import glosses_showing
        for surface, text in glosses_showing(question, added):
            problems.append(f"a gloss of the question shows an added answer: {surface} {text}")
    return sha, item.get("id"), kind, (row or None), problems


def _policy_problems(question, row, added, wrong):
    from kanamemory.learning_questions import grade_learning_answer, typed_answer_normal
    problems = []
    normal = typed_answer_normal(question)
    own = overlay.effective(question, None)
    accepted = {normal(value) for value in own.accepted}
    wrong_forms = {normal(value) for value in own.known_wrong} | {normal(entry["answer"]) for entry in wrong}
    seen = set()
    for form in added:
        value = normal(form) if form else ""
        if not value:
            problems.append("an added answer is empty")
        elif value in accepted or value in seen:
            problems.append(f"the added answer {form} is already accepted")
        elif value in wrong_forms:
            problems.append(f"the added answer {form} is a known-wrong answer")
        seen.add(value)
    accepted_only = overlay.effective(question, {"accepted": added} if added else None)
    for entry in wrong:
        value = normal(entry["answer"])
        judged = grade_learning_answer(question, entry["answer"], policy=accepted_only).get("status")
        if value in accepted or value in seen or judged == "correct":
            problems.append(f"the known-wrong answer {entry['answer']} is accepted")
    return problems


def load_existing(teaching):
    path = Path(teaching) / OUTPUT_NAME
    if not path.is_file():
        return {"schema_version": overlay.SCHEMA_VERSION, "items": {}}
    from kanamemory.content_pack import decode_value
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("name") != overlay.NAMESPACE:
        raise TableError(f"{path}: not the {overlay.NAMESPACE} namespace")
    return overlay.validate_namespace(decode_value(document["payload"]))


def build(tables, teaching, *, templates=None):
    """{"namespace", "refusals", "changed", "removed_missing"} for the tables over the existing rows."""
    templates = installed_templates() if templates is None else templates
    value = load_existing(teaching)
    items = {cid: {kind: dict(rows) for kind, rows in entry.items()} for cid, entry in value["items"].items()}
    refusals, changed, removed = [], [], []
    listed = set()
    for table in tables:
        for number, fields in read_table(table):
            sha, content_id, kind, row, problems = check_row(fields, templates)
            if problems:
                refusals.extend(f"{table}:{number}: {problem}" for problem in problems)
                continue
            if sha in listed:
                refusals.append(f"{table}:{number}: template {sha[:12]} is listed twice")
                continue
            listed.add(sha)
            rows = items.setdefault(content_id, {}).setdefault(kind, {})
            if row is None:
                rows.pop(sha, None)
            else:
                rows[sha] = row
            changed.append(sha)
    for content_id in list(items):
        for kind in list(items[content_id]):
            for sha in list(items[content_id][kind]):
                if not any(held[0].get("id") == content_id and held[1] == kind for held in templates.get(sha, ())):
                    removed.append(f"{content_id} {kind} {sha}")
                    del items[content_id][kind][sha]
            if not items[content_id][kind]:
                del items[content_id][kind]
        if not items[content_id]:
            del items[content_id]
    namespace = overlay.validate_namespace({"schema_version": overlay.SCHEMA_VERSION, "items": items})
    return {"namespace": namespace, "refusals": refusals, "changed": changed, "removed_missing": removed}


def write(namespace, teaching):
    from kanamemory.content_pack import decode_value, namespace_document
    path = Path(teaching) / OUTPUT_NAME
    text = json.dumps(namespace_document(overlay.NAMESPACE, namespace), ensure_ascii=False, indent=1,
                      sort_keys=True) + "\n"
    overlay.validate_namespace(decode_value(json.loads(text)["payload"]))
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent,
                                         prefix=".answer-overlay-", suffix=".json", delete=False)
    with handle:
        handle.write(text)
    os.replace(handle.name, path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--table", action="append", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--teaching", type=Path, default=DEFAULT_TEACHING,
                        help="content source directory of the namespace documents (default: %(default)s)")
    args = parser.parse_args(argv)
    try:
        result = build(args.table, args.teaching)
    except TableError as error:
        print(str(error), file=sys.stderr)
        return 2
    rows = overlay.row_count(result["namespace"])
    for line in result["refusals"]:
        print("refused " + line, file=sys.stderr)
    for line in result["removed_missing"]:
        print("removed (template no longer installed) " + line, file=sys.stderr)
    summary = {"rows": rows, "listed": len(result["changed"]), "refusals": len(result["refusals"]),
               "removed_missing": len(result["removed_missing"])}
    if result["refusals"]:
        print(json.dumps(summary, ensure_ascii=False))
        return 1
    if args.write:
        summary["written"] = str(write(result["namespace"], args.teaching))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
