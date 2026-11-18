"""Arrange a wished beginning of the main route (MAIN_ROUTE_ORDER) so that it passes the check.

tools/check_main_route_order.py holds every lesson of the beginning to a floor: at its place, per
ability and per choice key, at least min(gated questions in the route built without the order, floor)
gated questions. A wished order that brings lessons forward often fails it: their questions need lessons
the order puts later. This tool keeps the wished order where it can and moves a lesson that is not ready
later, until it is ready; it does not remove what could be placed and does not reorder for runs.

The wished order: --order FILE, a .tsv with a header that has the columns ``id`` and ``pos`` (the
wished rank; other columns are ignored; rows are taken by pos, ties in file order) or a .json in either
form MAIN_ROUTE_ORDER accepts (a flat list of ids, or units with sections: the result is always a flat
list, cut by the app as a flat order; the units and their titles are not kept).

The rule. Ids that are unknown, repeated, of an extension library or kept in place by the app (the
sounds stage, an optional stage) are left out and reported (a library lesson is an error, as in the
check). Unlisted hard prerequisites are put in just before the lesson that needs them, as the app lifts
them (main_route_order.beginning_sequence), and reported. Then, while a lesson is ready, the ready
lesson with the smallest wished rank is placed. A lesson is READY when its hard prerequisites are placed
and every key of it has at least min(count without the order, floor) gated questions with what is
learned before the beginning plus what is placed so far (the checker's own required_count and the
route_mix readiness table, Readiness / ReadinessCounter). So a lesson lands at the earliest point after
its wished place where it is ready, and lessons that were ready keep their wished order. What is
never ready inside the list is left out and reported: per key the counts and the requirements that block
its questions (``list``: a listed lesson that is left out itself; ``out``: a lesson of the route outside
the list; ``library``: an extension library's; ``none``: nothing the beginning can hold), most first.

Blockers: a greedy ranking of lessons from outside the list (with their unlisted prerequisites), each
subject to the same readiness rule: in each round the one that lets the most waiting lessons in
(ties: fewer lessons added, then the one whose first unlocked lesson is wished earliest, then by id).
When no addition lets any lesson in (lessons that need each other), the round takes the one that would
let the most in if it were ready ("if ready"); it then waits in the pool like any lesson, and a later
addition may make it ready. Reported with the lessons it lets in alone (the only addition to the list),
in its round and cumulatively (always under the readiness rule). Lessons no addition
helps: some key has fewer gated questions than its floor even with every lesson of the route learned.

Options: --add-blockers N adds the first N of that ranking (each with its prerequisites just before the
first lesson it unlocks, itself subject to the readiness rule) and arranges again; --limit N keeps the
first N lessons of the result and lists the rest; --out FILE writes the result as a flat JSON list;
--report DIR writes order.tsv, left_out.tsv, blockers.tsv and summary.txt. The result is checked with
check_main_route_order.evaluate before the tool ends: any error (a floor row, a prerequisite, anything
order_problems counts) or a listed library lesson makes the exit status 1. The check's run lengths are
reported, not acted on. Deterministic: the same input gives the same bytes.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
import csv
from dataclasses import dataclass, field
import heapq
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_main_route_order as checker  # noqa: E402

TOP_BLOCKERS = 30
_ROW_BLOCKERS = 10  # requirement ids per left-out row


# --- input ---------------------------------------------------------------------------------------

def read_wished(path) -> tuple:
    """(MAIN_ROUTE_ORDER value, {id: wished rank label}) of a .tsv (id and pos columns) or .json."""
    from kanamemory.main_route_order import order_ids
    path = Path(path)
    if path.suffix.lower() == ".tsv":
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if not {"id", "pos"} <= set(reader.fieldnames or ()):
                raise ValueError(f"{path}: a wished order TSV needs the columns id and pos")
            rows = [(row.get("pos") or "", number, row.get("id") or "") for number, row in enumerate(reader)]

        def rank(pos):
            try:
                return (0, float(pos))
            except ValueError:
                return (1, 0.0)
        rows.sort(key=lambda row: (rank(row[0]), row[1]))
        ranks = {}
        for pos, _, cid in rows:
            ranks.setdefault(cid, pos)
        return [cid for _, _, cid in rows], ranks
    value = json.loads(path.read_text(encoding="utf-8"))
    ranks = {}
    for number, cid in enumerate(order_ids(value)):
        ranks.setdefault(cid, str(number))
    return value, ranks


# --- the catalogue the arrangement reads ---------------------------------------------------------

@dataclass
class Base:
    """What every arrangement of one catalogue shares (read once)."""
    lessons: dict            # content id -> study_path_records record of a learning entry
    first_stage: int
    optional_stage: int
    known_before: frozenset  # entries of the stages before the beginning
    without: dict            # entry id -> place in the route built without the order
    readiness: object        # route_mix Readiness
    params: object           # MixParams
    library_of: Callable
    required: dict = field(default_factory=dict)   # id -> key -> min(without, floor)
    then: dict = field(default_factory=dict)       # id -> key -> gated questions without the order
    local: tuple = ()                              # orderable lesson ids (ReadinessCounter positions)
    dependents: dict = field(default_factory=dict)  # requirement or prerequisite -> ids that need it
    lifts: dict = field(default_factory=dict)       # id -> it with its prerequisites, as the app lifts them

    def orderable(self, cid) -> bool:
        record = self.lessons.get(cid)
        return record is not None and self.first_stage <= record[0] < self.optional_stage

    def prerequisites(self, cid) -> tuple:
        return tuple(d for d in self.lessons[cid][5].get("depends_on", ()) if self.orderable(d))

    def requirements(self, cid) -> tuple:
        return tuple(sorted({r for templates in self.readiness.abilities(cid).values()
                             for _, requires in templates for r in requires}))

    def title(self, cid) -> str:
        return str(self.lessons[cid][5].get("title", "")) if cid in self.lessons else ""

    def kind(self, cid) -> str:
        if cid not in self.lessons:
            return ""
        entry = self.lessons[cid][5]
        return "rule" if entry.get("domain") == "pronunciation" else str(entry.get("family") or "")

    def prepare(self, ids) -> None:
        """Floor counts and dependents of these lessons (once each)."""
        for cid in ids:
            if cid in self.required:
                continue
            there = self.without.get(cid, len(self.without))
            then = checker.counts_before(self.readiness, cid, self.without, there)
            self.then[cid] = then
            self.required[cid] = {key: checker.required_count(key, count, self.params) for key, count in then.items()}
            for need in (*self.requirements(cid), *self.prerequisites(cid)):
                self.dependents.setdefault(need, set()).add(cid)


def load_base(items, *, kana, library_of, params, readiness=None, wanted=()) -> Base:
    """The Base of a catalogue; readiness: a route_mix Readiness (None: read with the app's adapter for
    ``wanted`` and every lesson outside them that one of their questions needs, with prerequisites)."""
    from kanamemory.route_mix import sources
    from kanamemory.route_mix.readiness import Readiness
    from kanamemory.study_path_catalog import STAGES, _REFERENCE_STAGE, build_study_path, study_path_records
    records = study_path_records([], items)
    lessons = {record[5]["id"]: record for record in records if record[5].get("kind") == "learning"}
    without_tree = build_study_path(kana, items, main_route_order=[])
    rank = {stage_id: index for index, (stage_id, _) in enumerate(STAGES)}
    known = set()
    for stage in without_tree["stages"]:  # the stages before the beginning (the sounds stage)
        if rank.get(stage["id"], len(rank)) >= 1:
            break
        known.update(entry["id"] for unit in stage["units"] for section in unit["sections"]
                     for entry in section["entries"])
    base = Base(lessons, 1, _REFERENCE_STAGE, frozenset(known), checker.route_positions(without_tree),
                Readiness({}), params, library_of)
    base.local = tuple(cid for cid in lessons if base.orderable(cid))
    if readiness is None:
        by_id = {item["id"]: item for item in items}
        wanted = [cid for cid in dict.fromkeys(wanted) if base.orderable(cid)]
        first_table = sources.read_readiness(wanted, by_id, kinds=params.kinds)
        base.readiness = first_table
        extra = _outside_closure(base, wanted)
        second = sources.read_readiness(extra, by_id, kinds=params.kinds) if extra else Readiness({})
        readiness = Readiness({**{cid: first_table.abilities(cid) for cid in first_table.item_ids()},
                               **{cid: second.abilities(cid) for cid in second.item_ids()}})
    base.readiness = readiness
    return base


def _outside_closure(base, wanted) -> list:
    """Lessons outside ``wanted`` that their questions need, with their prerequisite closures."""
    inside = set(wanted)
    found = []
    for cid in wanted:
        for need in base.requirements(cid):
            if need not in inside and need not in base.known_before and base.orderable(need):
                inside.add(need)
                found.append(need)
    for cid in found:  # grows while walked: prerequisites of prerequisites
        for dependency in base.prerequisites(cid):
            if dependency not in inside:
                inside.add(dependency)
                found.append(dependency)
    return found


# --- the arrangement -----------------------------------------------------------------------------

class State:
    """Placed lessons and the readiness counter that has learned them."""

    def __init__(self, base: Base, pool_items):
        from kanamemory.route_mix.readiness import ReadinessCounter
        self.base = base
        if pool_items is None:
            return  # copy()
        items = sorted(set(pool_items) | set(base.readiness.item_ids()))  # outside lessons too (blockers)
        self.counter = ReadinessCounter(base.readiness, items, base.local,
                                        lambda r: r in base.known_before)
        self.placed, self.placed_set = [], set()

    def copy(self) -> State:
        other = State(self.base, None)
        other.counter, other.placed, other.placed_set = self.counter.copy(), list(self.placed), set(self.placed_set)
        return other

    def known(self, cid) -> bool:
        return cid in self.placed_set or cid in self.base.known_before

    def count(self, cid, key) -> int:
        return self.counter.count_of(cid, key)

    def ready(self, cid) -> bool:
        base = self.base
        return (all(self.known(d) for d in base.prerequisites(cid))
                and all(self.count(cid, key) >= need for key, need in base.required[cid].items()))

    def place(self, cid) -> None:
        self.counter.learn(self.counter.position(cid))
        self.placed.append(cid)
        self.placed_set.add(cid)


def arrange(base: Base, keys: dict, state: State | None = None) -> State:
    """Place the lessons of ``keys`` (id -> wished sort key): always the ready one with the smallest key."""
    base.prepare(keys)
    state = State(base, keys) if state is None else state
    heap, queued = [], set()
    for cid in sorted(keys, key=keys.__getitem__):
        if cid not in state.placed_set and state.ready(cid):
            heap.append((keys[cid], cid))
            queued.add(cid)
    heapq.heapify(heap)
    while heap:
        _, cid = heapq.heappop(heap)
        state.place(cid)
        for other in sorted(base.dependents.get(cid, ())):
            if other in keys and other not in queued and other not in state.placed_set and state.ready(other):
                heapq.heappush(heap, (keys[other], other))
                queued.add(other)
    return state


def _spread(state: State, pool, start) -> list:
    """Place every lesson of ``pool`` that becomes ready from ``start`` on (closure; order is free)."""
    base, placed, work = state.base, [], list(start)
    while work:
        cid = work.pop()
        if cid in state.placed_set or cid not in pool or not state.ready(cid):
            continue
        state.place(cid)
        placed.append(cid)
        work.extend(sorted(base.dependents.get(cid, ()), reverse=True))
    return placed


def additions(base: Base, cid, taken) -> list:
    """``cid`` with its unlisted prerequisites (as the app lifts them), those not in ``taken``."""
    from kanamemory.main_route_order import beginning_sequence
    sequence = base.lifts.get(cid)
    if sequence is None:
        sequence, _ = beginning_sequence([cid], list(base.lessons.values()), first_stage=base.first_stage,
                                         optional_stage=base.optional_stage)
        base.lifts[cid] = sequence
    return [lesson for lesson in sequence if lesson not in taken and lesson not in base.known_before]


@dataclass(frozen=True)
class Blocker:
    lesson: str
    added: tuple       # its unlisted prerequisites (as the app lifts them), then the lesson
    ready: bool        # it is placed itself after its round (else it waits like any lesson)
    estimated: bool    # picked by what it would let in if it were ready (no addition let any lesson in)
    alone: int         # waiting lessons it lets in when it is the only addition (round 1)
    this_round: int    # waiting lessons it lets in (would let in, when estimated) in its round
    cumulative: int    # waiting lessons in after the additions up to this one (honest: readiness rule)
    unlocked: tuple    # the lessons of this_round, by wished key
    first: str         # the first of them
    needs: tuple       # not ready: the outside lessons that block its own questions most


def _outside(base: Base, state: State, waiting, keys) -> list:
    found = set()
    for cid in waiting:
        for need in base.requirements(cid):
            if (not state.known(need) and need not in keys and base.orderable(need)
                    and base.library_of(need) is None):
                found.add(need)
    return sorted(found)


def _force(state: State, added, pool) -> list:
    """Place ``added`` whatever their readiness (as if ready), then what that lets in from ``pool``."""
    for lesson in added:
        if not state.known(lesson):
            state.place(lesson)
    starts = sorted({other for lesson in added for other in state.base.dependents.get(lesson, ())}, reverse=True)
    return _spread(state, pool, starts)


def _own_blockers(base: Base, state: State, cid, keys) -> tuple:
    """Outside lessons blocking the most short questions of ``cid`` (its keys under the floor)."""
    blocked = {}
    for key, need in base.required.get(cid, {}).items():
        if state.count(cid, key) >= need:
            continue
        for requirement, n in checker.blocking_requirements(base.readiness.abilities(cid).get(key, ()), state.known):
            if requirement not in keys and base.orderable(requirement):
                blocked[requirement] = blocked.get(requirement, 0) + n
    return tuple(r for r, _ in sorted(blocked.items(), key=lambda pair: (-pair[1], pair[0]))[:3])


def rank_blockers(base: Base, state: State, keys: dict, waiting, rounds=TOP_BLOCKERS) -> list:
    """The greedy ranking of outside lessons (Blocker, best first).

    Each round adds the outside lesson (with its unlisted prerequisites) that lets the most waiting
    lessons in under the readiness rule. When none lets any in, it adds the one that would let the most
    in if it were ready; that one waits in the pool (a later addition may make it ready). ``cumulative``
    always counts what the readiness rule lets in."""
    listed, keys = set(waiting), dict(keys)
    waiting, pending = set(waiting), set()
    ranking, alone = [], None
    for _ in range(rounds):
        trials = []
        for cid in _outside(base, state, waiting | pending, keys):
            added = additions(base, cid, keys)
            if any(base.library_of(lesson) is not None for lesson in added):
                continue
            base.prepare(added)
            trial = state.copy()
            placed = _spread(trial, waiting | pending | set(added), [*pending, *added])
            trials.append((cid, added, trial, placed))
        if alone is None:
            alone = {cid: sum(1 for lesson in placed if lesson in listed) for cid, _, _, placed in trials}
        best, ready = None, True
        for cid, added, trial, placed in trials:
            unlocked = sorted((lesson for lesson in placed if lesson in waiting), key=keys.__getitem__)
            score = (-len(unlocked), len(added), keys[unlocked[0]], cid) if unlocked else None
            if score is not None and (best is None or score < best[0]):
                best = (score, cid, added, trial, placed, unlocked)
        if best is None:
            ready = False
            for cid, added, _, _ in trials:
                trial = state.copy()
                placed = _force(trial, added, waiting | pending)
                unlocked = sorted((lesson for lesson in placed if lesson in waiting), key=keys.__getitem__)
                score = (-len(unlocked), len(added), keys[unlocked[0]], cid) if unlocked else None
                if score is not None and (best is None or score < best[0]):
                    best = (score, cid, added, None, (), unlocked)
        if best is None:
            break
        _, cid, added, trial, placed, unlocked = best
        for lesson in added:
            keys[lesson] = keys[unlocked[0]]
        estimated = not ready
        if ready:
            ready = cid in trial.placed_set  # what let them in may be one of its prerequisites
            state = trial
            waiting -= set(placed)
            pending = (pending | set(added)) - set(placed)
        else:
            pending |= set(added)
        needs = () if ready else _own_blockers(base, state, cid, keys)
        ranking.append(Blocker(cid, tuple(added), ready, estimated, alone.get(cid, 0), len(unlocked),
                               len(listed) - len(waiting), tuple(unlocked), unlocked[0], needs))
    return ranking


# --- one run -------------------------------------------------------------------------------------

@dataclass
class Result:
    order: list
    keys: dict                 # id -> wished sort key
    wished: list               # ids by wished sort key
    ranks: dict                # id -> wished rank label (listed lessons)
    notes: dict                # id -> "lifted for X" / "added: ..."
    dropped: list              # (id, wished rank, reason)
    waiting: list              # ids never ready, by wished key
    beyond: list               # ids after --limit
    blockers: list
    added: list                # Blocker of --add-blockers
    state: State
    library_errors: list
    structured: bool = False   # the input had units (the result is flat)


def _listed(base: Base, value, ranks):
    """(usable ids in order, dropped [(id, rank, reason)], library ids)."""
    from kanamemory.main_route_order import order_ids
    seen, usable, dropped, library = set(), [], [], []
    for cid in order_ids(value):
        rank = ranks.get(cid, "")
        if cid in seen:
            dropped.append((cid, rank, "repeated"))
            continue
        seen.add(cid)
        if cid not in base.lessons:
            dropped.append((cid, rank, "unknown id"))
        elif base.library_of(cid) is not None:
            dropped.append((cid, rank, f"extension library {base.library_of(cid)} (an error)"))
            library.append(cid)
        elif not base.orderable(cid):
            dropped.append((cid, rank, "kept in place by the app (sounds stage or optional stage)"))
        else:
            usable.append(cid)
    return usable, dropped, library


def run(base: Base, value, ranks, *, add_blockers=0, limit=None) -> Result:
    from kanamemory.main_route_order import beginning_sequence, is_structured
    usable, dropped, library = _listed(base, value, ranks)
    records = list(base.lessons.values())
    sequence, lifted = beginning_sequence(usable, records, first_stage=base.first_stage,
                                          optional_stage=base.optional_stage)
    keys = {cid: (index, 0) for index, cid in enumerate(sequence)}
    notes = {}
    for needer, prerequisite in lifted:
        notes.setdefault(prerequisite, f"unlisted prerequisite of {needer}")
    state = arrange(base, keys)
    waiting = [cid for cid in sorted(keys, key=keys.__getitem__) if cid not in state.placed_set]
    rounds = max(TOP_BLOCKERS, add_blockers)
    ranking = rank_blockers(base, state, keys, waiting, rounds) if waiting else []
    chosen = []
    if add_blockers and ranking:
        chosen = ranking[:add_blockers]
        for number, blocker in enumerate(chosen):
            first = keys[blocker.first]
            for offset, lesson in enumerate(blocker.added):
                keys[lesson] = (first[0], -1, number, offset)
                notes[lesson] = (f"added (blocker {number + 1}, before {blocker.first})" if lesson == blocker.lesson
                                 else f"added: unlisted prerequisite of {blocker.lesson}")
        state = arrange(base, keys)
        waiting = [cid for cid in sorted(keys, key=keys.__getitem__) if cid not in state.placed_set]
        ranking = rank_blockers(base, state, keys, waiting, TOP_BLOCKERS) if waiting else []
    order = list(state.placed)
    beyond = []
    if limit is not None and len(order) > limit:
        order, beyond = order[:limit], order[limit:]
    wished = sorted(keys, key=keys.__getitem__)
    return Result(order, keys, wished, ranks, notes, dropped, waiting, beyond, ranking, chosen, state, library,
                  is_structured(value))


# --- reports -------------------------------------------------------------------------------------

def _where(base: Base, state: State, keys, need) -> str:
    if need in keys:
        return "list"
    if base.library_of(need) is not None:
        return "library"
    return "out" if base.orderable(need) else "none"


def left_out_rows(base: Base, result: Result) -> list:
    """Rows (id, title, kind, wished rank, reason, key, without, at end, required, at best, blocked by)."""
    state, rows = result.state, []
    for cid in result.waiting:
        rank, title, kind = result.ranks.get(cid, ""), base.title(cid), base.kind(cid)
        missing = [d for d in base.prerequisites(cid) if not state.known(d)]
        if missing:
            rows.append((cid, title, kind, rank, "prerequisite left out: " + " ".join(missing), "", "", "", "", "", ""))
        best = base.readiness.counts(cid, lambda r: r in base.known_before or base.orderable(r))
        for key, need in sorted(base.required[cid].items()):
            now = state.count(cid, key)
            if now >= need:
                continue
            reason = "no addition helps" if best.get(key, 0) < need else "too few gated questions"
            templates = base.readiness.abilities(cid).get(key, ())
            blocked = checker.blocking_requirements(templates, state.known, _ROW_BLOCKERS)
            text = " ".join(f"{r}:{n}:{_where(base, state, result.keys, r)}" for r, n in blocked)
            rows.append((cid, title, kind, rank, reason, key, base.then[cid].get(key, 0), now, need,
                         best.get(key, 0), text))
    for cid, rank, reason in result.dropped:
        rows.append((cid, base.title(cid), base.kind(cid), rank, reason, "", "", "", "", "", ""))
    for cid in result.beyond:
        rows.append((cid, base.title(cid), base.kind(cid), result.ranks.get(cid, ""), "after --limit",
                     "", "", "", "", "", ""))
    return rows


def _write_tsv(path, header, rows) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def moves(result: Result) -> dict:
    """id -> new place minus its place in the wished order of the placed lessons (lessons left out do not
    count, so this is how far waiting moved it; negative: lessons wished before it moved behind it)."""
    placed = set(result.order)
    wished = {cid: index for index, cid in enumerate(cid for cid in result.wished if cid in placed)}
    return {cid: index - wished[cid] for index, cid in enumerate(result.order)}


def summary_lines(base: Base, result: Result, found, errors, left) -> list:
    moved = moves(result)
    later = sorted(value for value in moved.values() if value > 0)
    listed = sum(1 for cid in result.wished if cid in result.ranks)
    added = sum(1 for cid in result.wished if result.notes.get(cid, "").startswith("added"))
    lifted = sum(1 for cid in result.wished if cid not in result.ranks) - added
    reasons = {}
    for _, _, reason in result.dropped:
        reason = "extension-library lesson (an error)" if reason.startswith("extension") else reason
        reasons[reason] = reasons.get(reason, 0) + 1
    median = statistics.median(moved.values()) if moved else 0
    lines = (["the input has units: the result is a flat list (units and titles are not kept)"]
             if result.structured else [])
    lines += [f"wished lessons usable: {listed} listed + {lifted} unlisted prerequisites put in"
              + (f" + {added} added ({len(result.added)} blockers with their unlisted prerequisites)"
                 if result.added else ""),
              "dropped from the input: " + (", ".join(f"{n} {reason}" for reason, n in sorted(reasons.items()))
                                            or "none"),
              f"placed: {len(result.order)} lessons" + (f" (limit {len(result.order)}; {len(result.beyond)} more "
                                                        "were ready after it)" if result.beyond else ""),
              "moved (new place minus place in the wished order of the placed lessons): "
              f"median {median:g}, largest {max(moved.values()) if moved else 0}; "
              f"{len(later)} moved later (median of those {statistics.median(later) if later else 0:g})"]
    kinds = {}
    for cid in result.waiting:
        kinds[base.kind(cid)] = kinds.get(base.kind(cid), 0) + 1
    why = {"waits on a lesson outside the list": 0, "waits only on listed lessons left out themselves": 0,
           "no addition helps": 0, "a prerequisite is left out": 0}
    by_lesson = {}
    for row in left:
        if row[0] in result.waiting:
            by_lesson.setdefault(row[0], []).append(row)
    for cid in result.waiting:
        rows = by_lesson.get(cid, [])
        if any(row[4] == "no addition helps" for row in rows):
            why["no addition helps"] += 1
        elif any(":out" in row[10] for row in rows):
            why["waits on a lesson outside the list"] += 1
        elif any(row[5] for row in rows):
            why["waits only on listed lessons left out themselves"] += 1
        else:
            why["a prerequisite is left out"] += 1
    lines.append(f"left out (never ready inside the list): {len(result.waiting)}"
                 + (" (" + ", ".join(f"{n} {kind or '?'}" for kind, n in sorted(kinds.items())) + ")" if kinds else ""))
    lines.extend(f"  {n} {reason}" for reason, n in why.items() if n)
    helpless = [row for row in left if row[4] == "no addition helps"]
    if helpless:
        lines.append("lessons no addition helps (key: without the order / at best / needed):")
        lines.extend(f"  {row[0]} {row[1]}: {row[5]} {row[6]} / {row[9]} / {row[8]}" for row in helpless)
    if result.added:
        lines.append(f"--add-blockers {len(result.added)}: added")
        lines.extend(f"  {b.lesson} {base.title(b.lesson)} (before {b.first}; unlocked {b.this_round} in its round"
                     + (f"; with {' '.join(b.added[:-1])}" if len(b.added) > 1 else "") + ")" for b in result.added)
    lines.append("blockers outside the list (greedy; lesson, title: lets in alone / in its round / cumulatively;"
                 " 'if ready': picked by what it would let in if it were ready; 'waits': not ready itself"
                 " after its round, its own blockers follow):")
    if not result.blockers:
        lines.append("  none")
    for number, b in enumerate(result.blockers, 1):
        lines.append(f"  {number:2d}. {b.lesson} {base.title(b.lesson)}: {b.alone} / {b.this_round}"
                     f"{' if ready' if b.estimated else ''} / {b.cumulative}"
                     + (f"; with {' '.join(b.added[:-1])}" if len(b.added) > 1 else "")
                     + ("" if b.ready else "; waits" + (f", needs {' '.join(b.needs)}" if b.needs else "")))
    if found is not None and found.runs is not None:
        (words, word_sections), (others, other_sections) = found.runs["word"], found.runs["other"]
        lines.append(f"longest runs in the beginning (reported, not arranged for): {words} word lessons "
                     f"({word_sections} sections), {others} other lessons ({other_sections} sections)")
    lines.append(f"check of the result (check_main_route_order.evaluate): {errors} errors"
                 + (f"; {len(result.library_errors)} extension-library lessons in the input (errors)"
                    if result.library_errors else ""))
    return lines


def report(base: Base, result: Result, found, errors, directory) -> list:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rows_by = {}
    for row in (found.rows if found is not None else ()):
        rows_by.setdefault(row.lesson, []).append(row)
    moved = moves(result)
    _write_tsv(directory / "order.tsv",
               ["rank", "id", "title", "kind", "wished_rank", "moved", "note", "counts (key at its place/without)"],
               [(index, cid, base.title(cid), base.kind(cid), result.ranks.get(cid, ""), moved[cid],
                 result.notes.get(cid, ""),
                 " ".join(f"{row.key} {row.with_order}/{row.without}" for row in rows_by.get(cid, ())))
                for index, cid in enumerate(result.order)])
    left = left_out_rows(base, result)
    _write_tsv(directory / "left_out.tsv",
               ["id", "title", "kind", "wished_rank", "reason", "key", "without_order", "at_end", "required",
                "at_best", "blocked_by (id:questions:where)"], left)
    _write_tsv(directory / "blockers.tsv",
               ["rank", "id", "title", "kind", "placed_after_its_round", "picked_if_ready", "unlocks_alone",
                "unlocks_in_round", "cumulative", "put_in_with", "its_own_blockers", "first_unlocked", "unlocked"],
               [(number, b.lesson, base.title(b.lesson), base.kind(b.lesson), "yes" if b.ready else "no",
                 "yes" if b.estimated else "", b.alone, b.this_round, b.cumulative, " ".join(b.added[:-1]),
                 " ".join(b.needs), b.first, " ".join(b.unlocked))
                for number, b in enumerate(result.blockers, 1)])
    lines = summary_lines(base, result, found, errors, left)
    (directory / "summary.txt").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return lines


def verify(result: Result, items, kana, library_of, params, readiness=None):
    """(error count, Evaluation) of the result with the checker's own functions (readiness: as there)."""
    from kanamemory import main_route_order as given
    if not result.order:
        return 0, None
    found = checker.evaluate(result.order, items, kana=kana, readiness=readiness, library_of=library_of,
                             params=params)
    short = sum(1 for row in found.rows if row.short)
    return given.problem_count(found.problems) + short, found


@dataclass(frozen=True)
class Outcome:
    base: Base
    result: Result
    errors: int        # of the result, by check_main_route_order.evaluate
    found: object      # that Evaluation (None for an empty result)

    @property
    def failed(self) -> bool:
        return bool(self.errors or self.result.library_errors)


def arrange_order(value, ranks=None, items=None, *, kana=None, readiness=None, library_of: Callable | None = None,
                  params=None, add_blockers=0, limit=None) -> Outcome:
    """Arrange a MAIN_ROUTE_ORDER value over a catalogue (default: the installed one; arguments as in
    check_main_route_order.check) and check the result. ranks: id -> wished rank label (default: the
    place in the value)."""
    from kanamemory.learning_content import learning_items
    from kanamemory.main_route_order import order_ids
    if items is None:
        items = learning_items()
        kana = checker._installed_kana() if kana is None else kana
    kana = [] if kana is None else kana
    library_of = checker._installed_library_of() if library_of is None else library_of
    params = checker.default_params() if params is None else params
    if ranks is None:
        ranks = {}
        for number, cid in enumerate(order_ids(value)):
            ranks.setdefault(cid, str(number))
    base = load_base(items, kana=kana, library_of=library_of, params=params, readiness=readiness,
                     wanted=order_ids(value))
    result = run(base, value, ranks, add_blockers=add_blockers, limit=limit)
    errors, found = verify(result, items, kana, library_of, params, readiness)
    return Outcome(base, result, errors, found)


def write_outputs(outcome: Outcome, out=None, report_dir=None) -> list:
    """Write the flat JSON order (``out``) and the report files (``report_dir``); the summary lines."""
    base, result = outcome.base, outcome.result
    if out is not None:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes((json.dumps(result.order, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    if report_dir is not None:
        return report(base, result, outcome.found, outcome.errors, report_dir)
    return summary_lines(base, result, outcome.found, outcome.errors, left_out_rows(base, result))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--order", type=Path, required=True, help="the wished order: .tsv (id, pos) or .json")
    parser.add_argument("--add-blockers", type=int, default=0, metavar="N",
                        help="add the first N blockers from outside the list")
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="keep the first N lessons")
    parser.add_argument("--out", type=Path, help="write the result as a flat MAIN_ROUTE_ORDER JSON list")
    parser.add_argument("--report", type=Path, metavar="DIR",
                        help="write order.tsv, left_out.tsv, blockers.tsv and summary.txt")
    args = parser.parse_args(argv)
    if args.add_blockers < 0 or (args.limit is not None and args.limit < 0):
        parser.error("--add-blockers and --limit take a number of lessons (0 or more)")
    started = time.perf_counter()
    value, ranks = read_wished(args.order)
    outcome = arrange_order(value, ranks, add_blockers=args.add_blockers, limit=args.limit)
    print("\n".join(write_outputs(outcome, args.out, args.report)))
    print(f"time: {time.perf_counter() - started:.1f} s")
    return 1 if outcome.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
