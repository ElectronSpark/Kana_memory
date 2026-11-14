"""The main-route mix plan (design 11 section 16, M2/M7/M10; pure).

A plan is, per mixed stage, the sections in their new order with the unit
each belongs to, and the key it was computed for (algorithm, parameters,
derivation tree digest, content identity). plan_mix computes it from a
derivation tree and the readiness of its items; apply_plan turns a matching
plan into the mixed tree and refuses (None) any plan that does not fit the
tree exactly. Section and entry identities, titles and depends_on never change.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json

from .params import DEFAULT_PARAMS, MixParams
from .placement import WORD, arrange, section_of
from .readiness import Readiness

FORMAT = "kanamemory-main-route-mix"
VERSION = 1
_MAX_SECTIONS = 20000
_MAX_TEXT = 1024
_ENTRY_FIELDS = ("id", "content_id", "kind", "family", "domain")


def snapshot(tree) -> dict:
    """A detached copy of what plan_mix and tree_digest read (handed to a worker thread)."""
    stages = []
    for stage in tree["stages"]:
        units = []
        for unit in stage["units"]:
            sections = []
            for section in unit["sections"]:
                row = {"id": section["id"], "unit_id": section.get("unit_id", unit["id"]),
                       "entries": [{**{name: entry.get(name) for name in _ENTRY_FIELDS},
                                    "depends_on": list(entry.get("depends_on", ()))}
                                   for entry in section["entries"]]}
                if section.get("unresolved_dependencies"):
                    row["unresolved_dependencies"] = list(section["unresolved_dependencies"])
                sections.append(row)
            units.append({"id": unit["id"], "sections": sections})
        stages.append({"id": stage["id"], "optional": bool(stage.get("optional")), "units": units})
    return {"stages": stages}


def tree_digest(tree) -> str:
    """sha256 of the non-optional stages: stage, unit, section, entry (with kind/family/domain) and
    hard prerequisites, in route order. Optional stages (reference, personal lessons) are left out."""
    rows = []
    for stage in tree["stages"]:
        if stage.get("optional"):
            continue
        rows.append([stage["id"], [[section["id"], section.get("unit_id", unit["id"]),
                                    bool(section.get("unresolved_dependencies")),
                                    [[entry.get(name) for name in _ENTRY_FIELDS] + [sorted(entry.get("depends_on", ()))]
                                     for entry in section["entries"]]]
                                   for unit in stage["units"] for section in unit["sections"]]])
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8", "surrogatepass")).hexdigest()


def plan_key(params: MixParams, digest: str, content) -> dict:
    return {"algorithm": params.algorithm, "params": params.key(), "tree": digest, "content": content}


@dataclass(frozen=True)
class MixPlan:
    """stages: ((stage id, ((section id, unit id), ...)), ...) in route order; summary: report only."""
    key: Mapping
    stages: tuple
    summary: Mapping = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"format": FORMAT, "version": VERSION, "key": dict(self.key),
                "stages": [{"id": stage_id, "sections": [list(row) for row in rows]} for stage_id, rows in self.stages],
                "summary": dict(self.summary)}

    @classmethod
    def from_json(cls, value) -> MixPlan:
        """The plan of a stored value; ValueError for anything else."""
        if (not isinstance(value, dict) or value.get("format") != FORMAT or value.get("version") != VERSION
                or not isinstance(value.get("key"), dict) or not isinstance(value.get("stages"), list)):
            raise ValueError("Not a main route mix plan")
        stages, total, seen = [], 0, set()
        for stage in value["stages"]:
            if (not isinstance(stage, dict) or not _text(stage.get("id")) or stage["id"] in seen
                    or not isinstance(stage.get("sections"), list)):
                raise ValueError("Invalid main route mix stage")
            seen.add(stage["id"])
            rows = []
            for row in stage["sections"]:
                if not isinstance(row, list) or len(row) != 2 or not all(_text(part) for part in row):
                    raise ValueError("Invalid main route mix section")
                rows.append((row[0], row[1]))
            total += len(rows)
            if total > _MAX_SECTIONS:
                raise ValueError("Main route mix plan is too large")
            stages.append((stage["id"], tuple(rows)))
        summary = value.get("summary", {})
        return cls(dict(value["key"]), tuple(stages), summary if isinstance(summary, dict) else {})


def _text(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= _MAX_TEXT


def _route_parts(tree, mixable, library_of):
    """(prefix entry ids, [(stage id, Sections)], suffix entry ids, hard prerequisites on the route)."""
    on_route = {entry["id"] for stage in tree["stages"] for unit in stage["units"]
                for section in unit["sections"] for entry in section["entries"]}
    prereqs, prefix, suffix, stages = {}, [], [], []
    seen_mixed = False
    for stage in tree["stages"]:
        sections = [section for unit in stage["units"] for section in unit["sections"]]
        for section in sections:
            for entry in section["entries"]:
                needed = tuple(d for d in entry.get("depends_on", ()) if d in on_route and d != entry["id"])
                if needed:
                    prereqs[entry["id"]] = needed
        if stage["id"] in mixable and not stage.get("optional"):
            seen_mixed = True
            stages.append((stage["id"], tuple(section_of(section, library_of) for section in sections)))
        else:
            target = suffix if seen_mixed else prefix
            target.extend(entry["id"] for section in sections for entry in section["entries"])
    return prefix, stages, suffix, prereqs


def assign_units(sections: Sequence, fixed_ids, deferred_units: Mapping[str, str]) -> list[str]:
    """M7 for one stage in its new order: the unit id of each section.

    A skeleton section keeps its unit; a movable section before skeleton
    section B joins B's unit; after the last skeleton section a movable
    section keeps its own unit when all of that unit's sections are there and
    adjacent, else it joins the unit of the section before it (the last
    skeleton unit, or a unit kept just before), so that every unit stays one
    contiguous run. A section M8(b) deferred here (``deferred_units``: id ->
    the unit it joins with no section to follow, the stage's last unit) never
    keeps its own unit, which belongs to an earlier stage.
    """
    result = [None] * len(sections)
    fixed = [section.id in fixed_ids for section in sections]
    last = max((index for index, flag in enumerate(fixed) if flag), default=-1)
    following = None
    for index in range(len(sections) - 1, -1, -1):
        if fixed[index]:
            following = deferred_units.get(sections[index].id, sections[index].unit_id)
            result[index] = following
        elif index < last:
            result[index] = following
    members = {}
    for index, section in enumerate(sections):
        if section.id not in deferred_units:
            members.setdefault(section.unit_id, []).append(index)
    for index in range(last + 1, len(sections)):
        section = sections[index]
        own = members.get(section.unit_id) if section.id not in deferred_units else None
        if own and own[0] > last and own[-1] - own[0] + 1 == len(own):
            result[index] = section.unit_id
        elif index > 0:
            result[index] = result[index - 1]
        else:
            result[index] = deferred_units.get(section.id, section.unit_id)
    return result


def plan_mix(tree, readiness: Readiness, *, mixable: Sequence[str], library_of: Mapping[str, str],
             key: Mapping | None = None, params: MixParams = DEFAULT_PARAMS, cancelled=None,
             overridden: frozenset = frozenset()) -> MixPlan:
    """The plan of a derivation tree (or its snapshot). ``mixable``: ids of the stages to mix
    (stage 1 onwards, never optional); ``library_of``: entry id -> extension library;
    ``cancelled``: asked between stages (placement.MixCancelled when it says so); ``overridden``:
    entry ids that are core although a library provides them (sources.overridden_entries)."""
    mixable = set(mixable)
    prefix, stages, suffix, prereqs = _route_parts(tree, mixable, library_of)
    result = arrange(stages, prefix=prefix, suffix=suffix, readiness=readiness, prereqs=prereqs,
                     library_of=library_of, params=params, cancelled=cancelled, overridden=frozenset(overridden))
    deferred_units = {sid: row[2] for sid, row in result.deferred.items()}
    planned, summary = [], {"deferred": len(result.deferred), "stages": {}}
    for stage in result.stages:
        if stage.changed and not stage.keep_units:
            units = assign_units(stage.sections, stage.fixed, deferred_units)
        else:
            units = [section.unit_id for section in stage.sections]
        planned.append((stage.stage_id, tuple((section.id, unit) for section, unit in zip(stage.sections, units))))
        summary["stages"][stage.stage_id] = {"changed": stage.changed, "variant": stage.variant,
                                             "limit": stage.limit, "lower": stage.lower,
                                             "sections": len(stage.sections),
                                             "words": sum(section.word_count for section in stage.sections
                                                          if section.kind == WORD)}
    return MixPlan(dict(key or {}), tuple(planned), summary)


def apply_plan(tree, plan: MixPlan, *, mixable, providers: Mapping[str, str]):
    """The mixed tree of a plan computed for this very tree, else None (nothing is changed).

    The plan may name only mixed stages (``mixable``: stage 1 onwards, never optional) and
    must name exactly the sections of those stages, put each in a unit of the stage it is placed
    in, keep every unit one contiguous run and keep every hard prerequisite before its dependent.
    A section changes stage only as M8(b) moves one: to a later stage, a grammar section of one
    extension library (``providers``: entry id -> extension library) that nobody needs first.
    Stages the plan does not name are shared with the input.
    """
    if not isinstance(plan, MixPlan):
        return None
    mixable = set(mixable)
    stages = {stage["id"]: (number, stage) for number, stage in enumerate(tree["stages"])}
    sections, home = {}, {}
    for stage in tree["stages"]:
        for unit in stage["units"]:
            for section in unit["sections"]:
                sections[section["id"]] = section
                home[section["id"]] = stage["id"]
    named = [stage_id for stage_id, _ in plan.stages]
    if len(set(named)) != len(named) or any(stage_id not in stages or stage_id not in mixable
                                            or stages[stage_id][1].get("optional") for stage_id in named):
        return None
    expected = {sid for sid, stage_id in home.items() if stage_id in set(named)}
    listed = [sid for _, rows in plan.stages for sid, _ in rows]
    if len(listed) != len(set(listed)) or set(listed) != expected:
        return None
    needed = {dependency for section in sections.values() for entry in section["entries"]
              for dependency in entry.get("depends_on", ())}
    replaced = {}
    for stage_id, rows in plan.stages:
        number, stage = stages[stage_id]
        units = {unit["id"]: unit for unit in stage["units"]}
        built, closed = [], set()
        for sid, unit_id in rows:
            if unit_id not in units:
                return None
            if home[sid] != stage_id and (stages[home[sid]][0] > number
                                          or not _deferrable(sections[sid], providers, needed)):
                return None
            if built and built[-1]["id"] == unit_id:
                current = built[-1]
            elif unit_id in closed:
                return None
            else:
                if built:
                    closed.add(built[-1]["id"])
                current = dict(units[unit_id], sections=[])
                built.append(current)
            current["sections"].append(dict(sections[sid], unit_id=unit_id, unit_title=current.get("title"),
                                            stage_id=stage_id, stage_title=stage.get("title")))
        replaced[stage_id] = dict(stage, units=built)
    result = dict(tree, stages=[replaced.get(stage["id"], stage) for stage in tree["stages"]])
    if not _prerequisites_kept(tree, result):
        return None
    return result


def _deferrable(section, providers, needed) -> bool:
    """M8(b): a grammar section of one extension library whose entries nobody needs first."""
    entries = section["entries"]
    owners = {providers.get(entry["id"]) for entry in entries}
    return (bool(entries) and len(owners) == 1 and None not in owners
            and all(entry.get("kind") == "learning" and entry.get("family") == "grammar"
                    and entry.get("domain") != "pronunciation" and entry["id"] not in needed for entry in entries))


def _entry_positions(tree):
    return {entry["id"]: index for index, entry in enumerate(
        entry for stage in tree["stages"] for unit in stage["units"] for section in unit["sections"]
        for entry in section["entries"])}


def _prerequisites_kept(before, after) -> bool:
    """No hard prerequisite that came before its dependent comes after it now."""
    old, new = _entry_positions(before), _entry_positions(after)
    if set(old) != set(new):
        return False
    for stage in after["stages"]:
        for unit in stage["units"]:
            for section in unit["sections"]:
                for entry in section["entries"]:
                    at_old, at_new = old[entry["id"]], new[entry["id"]]
                    for dependency in entry.get("depends_on", ()):
                        if dependency in old and old[dependency] < at_old and new[dependency] > at_new:
                            return False
    return True
