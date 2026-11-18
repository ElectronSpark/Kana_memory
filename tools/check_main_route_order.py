"""Check the given beginning of the main route (study_path_catalog MAIN_ROUTE_ORDER).

The order is a list of units ({"title", "sections": [{"title", "entries"}]}) or
a flat list of content ids. Reports every unknown, repeated or malformed entry,
every lesson listed before one of its prerequisites and, for units, every
empty or untitled unit or section, every section over its family's size and
every section of several families (main_route_order.order_problems), against
the installed learning catalogue. The order is read from, in this priority:
--order FILE (a .tsv: its ``id`` column as a flat list; a .json: either form), --namespace FILE
(an authored study_path_catalog namespace document, e.g.
data/content_packs/full/teaching/study_path_catalog.json), else the installed
study_path_catalog namespace. Exit status 1 when there is an error, 0 otherwise
(notes, such as a lesson of the sounds stage keeping its place, do not fail).
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/src"))


def read_order(order_file=None, namespace_file=None):
    """The MAIN_ROUTE_ORDER value to check (None when there is none)."""
    from kanamemory.main_route_order import ORDER_KEY
    if order_file is not None:
        path = Path(order_file)
        if path.suffix.lower() == ".tsv":
            with path.open(encoding="utf-8", newline="") as handle:
                return [row.get("id", "") for row in csv.DictReader(handle, delimiter="\t")]
        return json.loads(path.read_text(encoding="utf-8"))
    if namespace_file is not None:
        from kanamemory.content_pack import decode_value
        document = json.loads(Path(namespace_file).read_text(encoding="utf-8"))
        return decode_value(document.get("payload", {})).get(ORDER_KEY)
    from kanamemory.content_pack import load_namespace
    return load_namespace("study_path_catalog", {}).get(ORDER_KEY)


def check(value, items=None):
    """(error count, report lines) of an order over the catalogue (default: the installed one)."""
    from kanamemory import main_route_order as given
    from kanamemory.learning_content import learning_items
    from kanamemory.study_path_catalog import STAGES, _REFERENCE_STAGE, study_path_records
    records = study_path_records([], learning_items() if items is None else items)
    problems = given.order_problems(value, records, first_stage=1, optional_stage=_REFERENCE_STAGE)
    errors = given.problem_count(problems)
    form = "units" if given.is_structured(value) else "a flat list"
    lines = given.describe_problems(problems)
    head = (f"{given.ORDER_KEY}: absent (the route is built as before)" if value is None
            else f"{given.ORDER_KEY} ({form}): {len(given.order_ids(value))} lessons listed, {errors} errors, "
                 f"{len(problems['kept_in_place'])} notes; the beginning opens stage {STAGES[1][1]}")
    return errors, [head, *lines]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--order", type=Path, help="a .tsv with an id column or a .json list")
    parser.add_argument("--namespace", type=Path, help="an authored study_path_catalog namespace document")
    args = parser.parse_args(argv)
    errors, lines = check(read_order(args.order, args.namespace))
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
