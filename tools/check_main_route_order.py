"""Check the given beginning of the main route (study_path_catalog MAIN_ROUTE_ORDER).

The order is a list of units ({"title", "sections": [{"title", "entries"}]}) or
a flat list of content ids. Reports every unknown, repeated or malformed entry,
every lesson an extension library provides (the order is core content), every
lesson listed before one of its prerequisites and every unlisted lesson the
route will place in the beginning to satisfy such a prerequisite chain (each
once) and, for units, every empty or untitled unit or section, every section
over its family's size and every section of several families
(main_route_order.order_problems), against the installed learning catalogue.

A flat list is cut as the app cuts it: consecutive words form sections of up to
8; every grammar lesson opens a unit of its own, so grammar points are never
merged; the rule "up to 4 consecutive lessons of one topic" applies to the
other non-word lessons (reading rules).

The question floor: for every lesson of the beginning, every ability and every
choice key (the abilities over the kinds switched on by default), the gated
questions at its place in the route built with the order are compared with
those at its place in the route built without it (route_mix readiness, as the
mix counts them). Fewer than min(count without the order, floor) is an error
(floor: MixParams.floor, choice_floor for a choice key); the report names the
requirements that block the most questions (the lessons to bring forward).
--readiness-report FILE writes every row as a TSV.

Runs: the longest run of word lessons and of other lessons in the beginning (in
lessons and sections); a warning when the word run exceeds 2 x
MixParams.word_target or the other run 4 x MixParams.nonword_target (the mix
never breaks up the given beginning). --strict makes warnings fail.

The order is read from, in this priority: --order FILE (a .tsv: its ``id``
column as a flat list; a .json: either form), --namespace FILE (an authored
study_path_catalog namespace document, e.g.
data/content_packs/full/teaching/study_path_catalog.json), else the installed
study_path_catalog namespace. Exit status 1 when there is an error, 0 otherwise
(notes, such as a lesson of the sounds stage keeping its place, do not fail).
"""
from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/src"))

_BLOCKERS = 3  # requirement ids named per short row


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


def default_params():
    """The mix parameters of a learner with the default switches (as the app builds its mix)."""
    from kanamemory import learning_questions as questions
    from kanamemory.route_mix.params import MixParams, switched_kinds
    return MixParams(kinds=switched_kinds(questions.stored_enabled_kinds({})))


def given_routes(value, items, kana=()):
    """(route built with the order, route built without it) of the catalogue ``items``."""
    from kanamemory.study_path_catalog import build_study_path
    return (build_study_path(kana, items, main_route_order=value),
            build_study_path(kana, items, main_route_order=[]))


def _flat(tree):
    return [section for stage in tree["stages"] for unit in stage["units"] for section in unit["sections"]]


def given_sections(tree):
    from kanamemory.route_mix.params import GIVEN_ORDER
    return [section for section in _flat(tree) if section.get(GIVEN_ORDER)]


def route_positions(tree) -> dict:
    """entry id -> its place in the route (every entry of every stage, in route order)."""
    return {entry["id"]: index for index, entry in enumerate(e for s in _flat(tree) for e in s["entries"])}


def key_floor(key, params) -> int:
    """The floor of one readiness key: MixParams.choice_floor for a choice key, else MixParams.floor."""
    from kanamemory.route_mix.readiness import is_choice_key
    return params.choice_floor if is_choice_key(key) else params.floor


def required_count(key, without, params) -> int:
    """The question floor of a lesson of the beginning: min(gated questions without the order, floor)."""
    return min(without, key_floor(key, params))


def counts_before(readiness, cid, positions, at) -> dict:
    """key -> gated questions of ``cid`` at route place ``at`` (requirements placed before ``at`` are learned)."""
    return readiness.counts(cid, lambda r: positions.get(r, at) < at)


def blocking_requirements(templates, known, limit=None) -> tuple:
    """((requirement id, questions it blocks), ...) over the unmet ``templates`` ((fingerprint,
    requirements), ...) of one key, most first (ties by id); ``known``: requirement id -> learned;
    ``limit``: at most that many (None: all)."""
    met = {sha for sha, requires in templates if all(known(r) for r in requires)}
    blocked = Counter(r for r, _ in {(r, sha) for sha, requires in templates if sha not in met
                                     for r in requires if not known(r)})
    ranked = tuple(sorted(blocked.items(), key=lambda pair: (-pair[1], pair[0])))
    return ranked if limit is None else ranked[:limit]


@dataclass(frozen=True)
class FloorRow:
    """One (lesson, key) of the beginning: gated questions without and with the order, the
    requirement (min(without, floor)) and the unmet requirement ids that block the most questions
    at the lesson's place with the order ((id, questions blocked), most first)."""
    lesson: str
    key: str
    without: int
    with_order: int
    required: int
    blockers: tuple

    @property
    def short(self) -> bool:
        return self.with_order < self.required


def floor_rows(with_tree, without_tree, readiness, params) -> list:
    """FloorRow of every lesson of the given beginning (route order) and every key the readiness
    table has for it (abilities and choice keys)."""
    new, old = route_positions(with_tree), route_positions(without_tree)
    rows = []
    for section in given_sections(with_tree):
        for entry in section["entries"]:
            cid = entry["id"]
            if cid not in readiness:
                continue
            here, there = new[cid], old.get(cid, new[cid])
            known = lambda r, at=here: new.get(r, at) < at  # noqa: E731
            now = counts_before(readiness, cid, new, here)
            then = counts_before(readiness, cid, old, there)
            for key in sorted(set(now) | set(then)):
                top = blocking_requirements(readiness.abilities(cid).get(key, ()), known, _BLOCKERS)
                rows.append(FloorRow(cid, key, then.get(key, 0), now.get(key, 0),
                                     required_count(key, then.get(key, 0), params), top))
    return rows


def beginning_runs(with_tree) -> dict:
    """The longest run of word lessons and of other lessons in the given beginning:
    {"word": (lessons, sections), "other": (lessons, sections)}."""
    best = {"word": (0, 0), "other": (0, 0)}
    current, family = (0, 0), None
    for section in given_sections(with_tree):
        kind = "word" if all(entry.get("family") == "word" for entry in section["entries"]) else "other"
        current = (current[0] + len(section["entries"]), current[1] + 1) if kind == family else (
            len(section["entries"]), 1)
        family = kind
        best[kind] = max(best[kind], current)
    return best


def read_given_readiness(with_tree, items, params):
    """The route_mix readiness of the beginning's lessons (sources.read_readiness, the app's adapter)."""
    from kanamemory.route_mix import sources
    ids = [entry["id"] for section in given_sections(with_tree) for entry in section["entries"]]
    return sources.read_readiness(ids, {item["id"]: item for item in items}, kinds=params.kinds)


def write_report(path, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["lesson", "key", "without_order", "with_order", "required", "short", "blocked_by"])
        for row in rows:
            writer.writerow([row.lesson, row.key, row.without, row.with_order, row.required,
                             "yes" if row.short else "", " ".join(f"{r}:{n}" for r, n in row.blockers)])


def _installed_kana():
    from kanamemory import content_pack
    from kanamemory.content import ContentRepository
    with ContentRepository(content_pack.lexicon_path()) as repo:
        return repo.kana_list()


def _installed_library_of():
    from kanamemory.content_library_service import availability_catalogue
    from kanamemory.route_mix.sources import route_library_of
    return route_library_of(availability_catalogue())


@dataclass(frozen=True)
class Evaluation:
    """What the check finds in an order: problems (main_route_order.order_problems), the floor rows
    (FloorRow, route order; empty without listed ids), the beginning's runs (beginning_runs; None
    without listed ids) and the params used."""
    problems: dict
    rows: tuple
    runs: dict | None
    params: object


def evaluate(value, items=None, *, kana=None, readiness=None, library_of: Callable | None = None,
             params=None) -> Evaluation:
    """The findings of ``check`` (``value`` not None; arguments as there), unformatted."""
    from kanamemory import main_route_order as given
    from kanamemory.learning_content import learning_items
    from kanamemory.study_path_catalog import _REFERENCE_STAGE, study_path_records
    if items is None:
        items = learning_items()
        kana = _installed_kana() if kana is None else kana
    kana = [] if kana is None else kana
    library_of = _installed_library_of() if library_of is None else library_of
    records = study_path_records([], items)
    problems = given.order_problems(value, records, first_stage=1, optional_stage=_REFERENCE_STAGE,
                                    library_of=library_of)
    params = default_params() if params is None else params
    if not given.order_ids(value):
        return Evaluation(problems, (), None, params)
    with_tree, without_tree = given_routes(value, items, kana)
    if readiness is None:
        readiness = read_given_readiness(with_tree, items, params)
    rows = tuple(floor_rows(with_tree, without_tree, readiness, params))
    return Evaluation(problems, rows, beginning_runs(with_tree), params)


def check(value, items=None, *, kana=None, readiness=None, library_of: Callable | None = None, params=None,
          strict=False, report=None):
    """(error count, report lines) of an order over the catalogue (default: the installed one).

    kana: the kana rows of the routes (default: the installed ones with the installed catalogue, none
    with ``items``); readiness: route_mix Readiness of the beginning's lessons (default: read with the
    app's adapter); library_of: content id -> extension library or None (default: the installed
    catalogue's providers); params: MixParams (default: a learner with the default switches); strict:
    warnings count as errors; report: a path for the TSV of every floor row."""
    from kanamemory import main_route_order as given
    from kanamemory.study_path_catalog import STAGES
    if value is None:
        return 0, [f"{given.ORDER_KEY}: absent (the route is built as before)"]
    found = evaluate(value, items, kana=kana, readiness=readiness, library_of=library_of, params=params)
    problems, params = found.problems, found.params
    errors = given.problem_count(problems)
    lines = given.describe_problems(problems)
    warnings = 0
    if found.runs is not None:
        rows = found.rows
        if report is not None:
            write_report(report, rows)
        for row in rows:
            if row.short:
                errors += 1
                blockers = ", ".join(f"{r} ({n})" for r, n in row.blockers) or "nothing on the route"
                lines.append(f"{row.lesson}: {row.with_order} gated {row.key} questions at its place, "
                             f"{row.without} without the order, needs {row.required}; blocked most by {blockers}")
        (words, word_sections), (others, other_sections) = found.runs["word"], found.runs["other"]
        lines.append(f"longest runs in the beginning: {words} word lessons ({word_sections} sections), "
                     f"{others} other lessons ({other_sections} sections)")
        if words > 2 * params.word_target:
            warnings += 1
            lines.append(f"warning: a run of {words} word lessons, more than 2 x {params.word_target} "
                         "(the mix never breaks up the beginning)")
        if others > 4 * params.nonword_target:
            warnings += 1
            lines.append(f"warning: a run of {others} other lessons, more than 4 x {params.nonword_target} "
                         "(the mix never breaks up the beginning)")
    if strict:
        errors += warnings
    form = "units" if given.is_structured(value) else "a flat list"
    head = (f"{given.ORDER_KEY} ({form}): {len(given.order_ids(value))} lessons listed, {errors} errors, "
            f"{warnings} warnings, {len(problems['kept_in_place']) + len(problems['lifted'])} notes; "
            f"the beginning opens stage {STAGES[1][1]}")
    return errors, [head, *lines]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--order", type=Path, help="a .tsv with an id column or a .json list")
    parser.add_argument("--namespace", type=Path, help="an authored study_path_catalog namespace document")
    parser.add_argument("--readiness-report", type=Path, help="write every (lesson, key) floor row as a TSV")
    parser.add_argument("--strict", action="store_true", help="warnings (long runs) fail too")
    args = parser.parse_args(argv)
    errors, lines = check(read_order(args.order, args.namespace), strict=args.strict, report=args.readiness_report)
    print("\n".join(lines))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
