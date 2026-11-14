"""Parallel study routes: navigation views over one shared learning catalogue.

A route owns no progress (design 10). Entry identities are item identities
(content IDs, kana:<id>:<script>) and are shared by every route; hard
prerequisites belong to items, so a route can only order them and declare a
soft "after" order that never blocks. Pure rules: no Toga, platform,
network or database access. The core route is still built by
study_path_catalog.build_study_path; build_route serves every other route.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

CORE_LIBRARY_ID = "kanamemory.core"
LOCAL_LIBRARY_ID = "local"
CORE_ROUTE_ID = "route:kanamemory.core:main"
CORE_ROUTE_TITLE = "主线"
ROUTES_SETTING = "study_routes_v1"
ROUTE_TREE_VERSION = "study-route-v1"
MAX_ROUTE_ENTRIES = 20000
MAX_ROUTES = 1000
MAX_IDENTITY_LENGTH = 1024
_LIBRARY_ID = re.compile(r"[a-z][a-z0-9_.-]{2,63}\Z")
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SPEC_KEYS = {"route_id", "title", "description", "library_id", "version", "stages", "build"}
_STAGE_KEYS = {"id", "title", "optional", "units"}
_UNIT_KEYS = {"id", "title", "sections"}
_SECTION_KEYS = {"id", "title", "optional", "entries", "after"}
_SELECT_KEYS = {"tags", "families", "ids"}
_ROUTE_STATE_REQUIRED = {"added_sections", "enabled", "source", "library_id", "created_at_utc"}
_ROUTE_STATE_KEYS = _ROUTE_STATE_REQUIRED | {"spec"}


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def route_id(library_id, key):
    """Return "route:<library_id>:<key>" after checking both parts."""
    value = f"route:{library_id}:{key}"
    parse_route_id(value)
    return value


def local_route_id(key):
    """A personal route (design 10 section 6.1) uses the local prefix."""
    return route_id(LOCAL_LIBRARY_ID, key)


def parse_route_id(value):
    """Return (library_id, key) or raise ValueError for an invalid route ID."""
    if not isinstance(value, str) or len(value) > MAX_IDENTITY_LENGTH:
        raise ValueError("学习路线标识无效。")
    parts = value.split(":")
    if (len(parts) != 3 or parts[0] != "route" or not _LIBRARY_ID.match(parts[1])
            or not _KEY.match(parts[2])):
        raise ValueError("学习路线标识无效：" + value[:200])
    return parts[1], parts[2]


def is_local_route(value):
    return parse_route_id(value)[0] == LOCAL_LIBRARY_ID


def _text(value, label, limit=200, *, optional=False):
    if value is None and optional:
        return
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("学习路线规格无效：" + label)


def _flag(container, label):
    if "optional" in container and type(container["optional"]) is not bool:
        raise ValueError("学习路线规格无效：" + label + " 的 optional 必须是布尔值")


def _keys(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError("学习路线规格无效：" + label)
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("学习路线规格含未知字段：" + label + " " + ", ".join(unknown))


def _local_id(container, label, seen):
    if "id" not in container:
        return
    value = container["id"]
    if not isinstance(value, str) or not _KEY.match(value) or value in seen:
        raise ValueError("学习路线规格无效：" + label + ".id")
    seen.add(value)


def _entry_identity(entry):
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict) and set(entry) == {"id", "kind"} and entry["kind"] == "reference":
        return entry["id"], "reference"
    raise ValueError("学习路线条目格式无效。")


def _known(identity, catalogue_ids, kana_identities):
    if identity.startswith("kana:"):
        return identity in kana_identities
    return identity in catalogue_ids


def _string_list(value, label, *, allow_empty=False):
    if (not isinstance(value, list) or (not value and not allow_empty)
            or any(not isinstance(x, str) or not x or len(x) > MAX_IDENTITY_LENGTH for x in value)
            or len(set(value)) != len(value)):
        raise ValueError("学习路线规格无效：" + label)
    return value


def _validate_filter(build, catalogue_ids, kana_identities):
    _keys(build, {"kind", "select"}, "build")
    if build.get("kind") != "filter":
        raise ValueError("学习路线规格无效：build.kind")
    select = build.get("select")
    _keys(select, _SELECT_KEYS, "build.select")
    if not select:
        raise ValueError("学习路线规格无效：build.select 为空")
    for key in sorted(select):
        _string_list(select[key], "build.select." + key)
    missing = [cid for cid in select.get("ids", ()) if not _known(cid, catalogue_ids, kana_identities)]
    if missing:
        raise ValueError("学习路线引用目录外身份：" + missing[0])


def validate_route_spec(spec, *, catalogue_ids, kana_identities, library_id=None):
    """Validate one route spec (design 10 section 6.2); return a detached copy.

    catalogue_ids are learning content IDs of the effective catalogue and
    kana_identities the kana:<id>:<script> identities. A route may not waive
    or add hard prerequisites: there is no such field, only soft "after".
    """
    _keys(spec, _SPEC_KEYS, "route")
    rid = spec.get("route_id")
    owner, _ = parse_route_id(rid)
    if rid == CORE_ROUTE_ID:
        raise ValueError("核心主线不由路线规格构建。")
    if library_id is not None and owner != library_id:
        raise ValueError("学习路线标识必须以本库标识为前缀：" + rid)
    if spec.get("library_id") != owner:
        raise ValueError("学习路线规格的 library_id 与路线标识不一致。")
    _text(spec.get("title"), "title")
    _text(spec.get("description"), "description", 2000, optional=True)
    version = spec.get("version")
    if not ((type(version) is int and version >= 1)
            or (isinstance(version, str) and version.strip() and len(version) <= 64)):
        raise ValueError("学习路线规格无效：version")
    catalogue_ids, kana_identities = set(catalogue_ids), set(kana_identities)
    if ("stages" in spec) == ("build" in spec):
        raise ValueError("学习路线规格须二选一：显式 stages 或 build.kind=filter。")
    if "build" in spec:
        _validate_filter(spec["build"], catalogue_ids, kana_identities)
        return deepcopy(spec)
    stages = spec["stages"]
    if not isinstance(stages, list) or not stages:
        raise ValueError("学习路线规格无效：stages")
    seen, after_refs = set(), []
    ids = {"stage": set(), "unit": set(), "section": set()}
    for stage in stages:
        _keys(stage, _STAGE_KEYS, "stage")
        if "id" not in stage:
            raise ValueError("学习路线规格无效：stage.id")
        _local_id(stage, "stage", ids["stage"])
        _text(stage.get("title"), "stage.title")
        _flag(stage, "stage")
        units = stage.get("units")
        if not isinstance(units, list) or not units:
            raise ValueError("学习路线规格无效：stage.units")
        unit_titles = set()
        for unit in units:
            _keys(unit, _UNIT_KEYS, "unit")
            _text(unit.get("title"), "unit.title")
            _local_id(unit, "unit", ids["unit"])
            if "id" not in unit and unit["title"] in unit_titles:
                # Without an id the unit identity is derived from its title.
                raise ValueError("同一阶段内未给 id 的单元标题重复：" + unit["title"])
            unit_titles.add(unit["title"])
            sections = unit.get("sections")
            if not isinstance(sections, list) or not sections:
                raise ValueError("学习路线规格无效：unit.sections")
            for section in sections:
                _keys(section, _SECTION_KEYS, "section")
                _text(section.get("title"), "section.title")
                _flag(section, "section")
                _local_id(section, "section", ids["section"])
                entries = section.get("entries")
                if not isinstance(entries, list) or not entries:
                    raise ValueError("学习路线规格无效：section.entries")
                for entry in entries:
                    identity, _ = _entry_identity(entry)
                    if not isinstance(identity, str) or not identity or len(identity) > MAX_IDENTITY_LENGTH:
                        raise ValueError("学习路线条目身份无效。")
                    if identity in seen:
                        raise ValueError("学习路线内条目身份重复：" + identity)
                    if not _known(identity, catalogue_ids, kana_identities):
                        raise ValueError("学习路线引用目录外身份：" + identity)
                    seen.add(identity)
                    if len(seen) > MAX_ROUTE_ENTRIES:
                        raise ValueError("学习路线条目超过上限。")
                if "after" in section:
                    after_refs.extend(_string_list(section["after"], "section.after", allow_empty=True))
    outside = [identity for identity in after_refs if identity not in seen]
    if outside:
        raise ValueError("软顺序 after 只能引用本路线条目：" + outside[0])
    return deepcopy(spec)


def default_routes_state():
    """The missing-key state: only the core route, which is also active."""
    return {"version": 1, "active_route": CORE_ROUTE_ID, "routes": {}}


def validate_routes_state(value):
    """Validate study_routes_v1 (design 10 section 7.1); return a detached copy.

    The core route is never stored here: its added_sections remain in
    study_path_v1 and it cannot be disabled (12 Q2). A row whose library is
    absent stays valid; falling back to the core route is a read-time choice.
    """
    if (not isinstance(value, dict) or set(value) != {"version", "active_route", "routes"}
            or value["version"] != 1):
        raise ValueError("学习路线集合记录版本无效。")
    routes = value["routes"]
    if not isinstance(routes, dict) or len(routes) > MAX_ROUTES:
        raise ValueError("学习路线集合记录无效：routes")
    for rid, state in routes.items():
        owner, _ = parse_route_id(rid)
        if rid == CORE_ROUTE_ID:
            raise ValueError("学习路线集合记录不得包含核心主线。")
        if not isinstance(state, dict) or not (_ROUTE_STATE_REQUIRED <= set(state) <= _ROUTE_STATE_KEYS):
            raise ValueError("学习路线集合记录无效：" + rid)
        items = state["added_sections"]
        if (not isinstance(items, list) or len(items) > MAX_ROUTE_ENTRIES
                or any(not isinstance(x, str) or not x or len(x) > MAX_IDENTITY_LENGTH for x in items)
                or len(set(items)) != len(items)):
            raise ValueError("学习路线集合记录无效：added_sections")
        if type(state["enabled"]) is not bool:
            raise ValueError("学习路线集合记录无效：enabled")
        source = state["source"]
        if (source not in {"library", "local"} or state["library_id"] != owner
                or (source == "local") != (owner == LOCAL_LIBRARY_ID)):
            raise ValueError("学习路线集合记录无效：source/library_id")
        created = state["created_at_utc"]
        if not isinstance(created, str) or not created or len(created) > 64:
            raise ValueError("学习路线集合记录无效：created_at_utc")
        if "spec" in state:
            spec = state["spec"]
            if source != "local" or not isinstance(spec, dict) or spec.get("route_id") != rid:
                raise ValueError("学习路线集合记录无效：spec")
    active = value["active_route"]
    parse_route_id(active)
    if active != CORE_ROUTE_ID and active not in routes:
        raise ValueError("学习路线集合记录无效：active_route")
    return deepcopy(value)


def _identity_maps(catalogue, kana_rows, extra_items):
    from .study_path_catalog import study_path_records
    items = list(catalogue.values()) if isinstance(catalogue, dict) else list(catalogue)
    records = study_path_records(kana_rows, items, extra_items)
    by_identity = {record[5]["id"]: record for record in records}
    sources = {item["id"]: item for item in items}
    for item in extra_items:
        sources.setdefault(item["id"], item)
    return records, by_identity, sources


def _mark_external(sections, route_ids, known):
    for section in sections:
        external = sorted({d for entry in section["entries"] for d in entry["depends_on"]
                           if d not in route_ids})
        section["external_prerequisites"] = external
        unresolved = [d for d in external if d not in known]
        if unresolved:
            section["unresolved_dependencies"] = unresolved
        else:
            section.pop("unresolved_dependencies", None)


def build_route(spec, *, catalogue, kana_rows, extra_items=(), library_id=None):
    """Build one non-core route tree (design 10 section 6.3), never learner state.

    The result has the build_study_path shape (version, scope, stages[units
    [sections]]) plus route_id, library_id, title, route_version and
    diagnostics; every section carries external_prerequisites (hard item
    prerequisites whose identity is not in this route). Soft "after" order
    only reorders; an order that would form a cycle is ignored and reported.
    A hard prerequisite cycle between sections of one unit is rejected.
    """
    from .study_path_catalog import flatten_sections
    extra_items = list(extra_items)
    records, by_identity, sources = _identity_maps(catalogue, kana_rows, extra_items)
    kana_identities = {identity for identity in by_identity if identity.startswith("kana:")}
    spec = validate_route_spec(spec, catalogue_ids=set(by_identity) - kana_identities,
                               kana_identities=kana_identities, library_id=library_id)
    owner, _ = parse_route_id(spec["route_id"])
    known = set(by_identity) | set(sources)
    if "build" in spec:
        tree = _build_filtered(spec, records, sources, known)
    else:
        tree = _build_explicit(spec, by_identity)
    sections = flatten_sections(tree)
    _mark_external(sections, {entry["id"] for section in sections for entry in section["entries"]}, known)
    result = {"route_id": spec["route_id"], "library_id": owner, "title": spec["title"]}
    if "description" in spec:
        result["description"] = spec["description"]
    result["route_version"] = spec["version"]
    result.update(tree)
    return result


def _selected(select, entry, source):
    """AND across the given select keys, OR within one key's values."""
    if "ids" in select and entry["id"] not in select["ids"]:
        return False
    if "families" in select:
        family = "kana" if entry["id"].startswith("kana:") else entry.get("family")
        if family not in select["families"]:
            return False
    if "tags" in select:
        tags = source.get("tags", ()) if source else ()
        if not isinstance(tags, (list, tuple)) or not set(tags) & set(select["tags"]):
            return False
    return True


def _build_filtered(spec, records, sources, known):
    """Core grouping/ordering over the selected records, with route identities.

    Records come from the whole catalogue so stage and depth still follow
    every hard prerequisite; position in the section identity is the depth.
    """
    from .study_path_catalog import assemble_study_path
    rid, select = spec["route_id"], spec["build"]["select"]
    chosen = [deepcopy(record) for record in records
              if _selected(select, record[5], sources.get(record[5]["id"]))]
    if not chosen:
        raise ValueError("学习路线筛选结果为空。")

    def section_identity(stage, unit, topic, depth):
        return "section:" + _digest(f"{rid}|{stage}|{unit}|{topic}|{depth}")

    def unit_identity(stage, unit):
        return "unit:" + _digest(f"{rid}|{stage}|{unit}")

    tree = assemble_study_path(chosen, version=ROUTE_TREE_VERSION, scope="route_filter_view",
                               section_identity=section_identity, unit_identity=unit_identity,
                               scope_key=rid, resolvable=frozenset(known))
    tree["diagnostics"] = []
    return tree


def _has_cycle(nodes, edges):
    state = {}
    for start in sorted(nodes):
        if start in state:
            continue
        state[start] = 1
        stack = [(start, iter(sorted(edges.get(start, ()))))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                state[node] = 2
                stack.pop()
            elif child in nodes:
                if state.get(child) == 1:
                    return True
                if child not in state:
                    state[child] = 1
                    stack.append((child, iter(sorted(edges.get(child, ())))))
    return False


def _explicit_sections(spec, by_identity):
    rid = spec["route_id"]
    sections, stage_rows, position = {}, [], 0
    for stage in spec["stages"]:
        stage_id = rid + "#" + stage["id"]
        stage_optional = stage.get("optional", False)
        units = {}
        for unit in stage["units"]:
            unit_key = unit.get("id", unit["title"])
            uid = (rid + "#" + unit["id"] if "id" in unit
                   else "unit:" + _digest(f"{rid}|{stage['id']}|{unit_key}"))
            units[uid] = {"id": uid, "title": unit["title"], "sections": [], "_order": position}
            for index, raw in enumerate(unit["sections"]):
                sid = (rid + "#" + raw["id"] if "id" in raw else
                       "section:" + _digest(f"{rid}|{stage['id']}|{unit_key}|{raw['title']}|{index}"))
                entries = []
                for item in raw["entries"]:
                    identity, kind = _entry_identity(item)
                    entry = deepcopy(by_identity[identity][5])
                    if kind == "reference" and entry["kind"] != "reference":
                        entry.update(kind="reference", depends_on=[])
                    entries.append(entry)
                section = {"id": sid, "title": raw["title"], "entries": entries, "depends_on": [],
                           "optional": stage_optional or raw.get("optional", False),
                           "stage_id": stage_id, "stage_title": stage["title"],
                           "unit_id": uid, "unit_title": unit["title"],
                           "component_section_ids": [sid], "topics": [raw["title"]],
                           "_order": position, "_after": list(raw.get("after", ())),
                           "_stage": len(stage_rows)}
                position += 1
                sections[sid] = section
                units[uid]["sections"].append(section)
        stage_rows.append({"id": stage_id, "title": stage["title"], "optional": stage_optional,
                           "units": units})
    return sections, stage_rows


def _build_explicit(spec, by_identity):
    """Order authored units/sections: hard prerequisites, then after, then authored order."""
    from .study_path_catalog import _merge_cyclic_units, _topological
    sections, stage_rows = _explicit_sections(spec, by_identity)
    diagnostics = []
    owner = {entry["id"]: sid for sid, section in sections.items() for entry in section["entries"]}
    hard = {sid: {owner[d] for entry in section["entries"] for d in entry["depends_on"]
                  if d in owner and owner[d] != sid} for sid, section in sections.items()}
    for sid, section in sections.items():
        for dep in sorted(hard[sid], key=lambda d: sections[d]["_order"]):
            if sections[dep]["_stage"] > section["_stage"]:
                diagnostics.append({"code": "prerequisite_in_later_stage", "section_id": sid,
                                    "prerequisite_section_id": dep})
    unit_of = {}
    for stage in stage_rows:
        before = set(stage["units"])
        merged, _ = _merge_cyclic_units(stage["units"], {sid: sorted(deps) for sid, deps in hard.items()})
        if set(merged) != before:
            diagnostics.append({"code": "units_merged_for_prerequisite_cycle", "stage_id": stage["id"],
                                "unit_ids": sorted(set(merged) - before)})
        stage["units"] = merged
        for uid, unit in merged.items():
            members = {s["id"] for s in unit["sections"]}
            if _has_cycle(members, hard):
                raise ValueError("学习路线小节硬前置成环：" + unit["title"])
            for section in unit["sections"]:
                section["unit_id"], section["unit_title"] = uid, unit["title"]
                unit_of[section["id"]] = uid
    combined = {sid: set(deps) for sid, deps in hard.items()}
    for sid in sorted(sections, key=lambda s: sections[s]["_order"]):
        section = sections[sid]
        for identity in section["_after"]:
            target = owner[identity]
            if target == sid or sections[target]["_stage"] < section["_stage"]:
                continue
            if sections[target]["_stage"] > section["_stage"]:
                diagnostics.append({"code": "soft_order_unsatisfiable", "section_id": sid, "after": identity})
                continue
            trial = {key: set(values) for key, values in combined.items()}
            trial[sid].add(target)
            members = {s for s in sections if sections[s]["_stage"] == section["_stage"]}
            unit_edges = {}
            for node in members:
                unit_edges.setdefault(unit_of[node], set()).update(
                    unit_of[d] for d in trial[node] if d in members and unit_of[d] != unit_of[node])
            if _has_cycle(members, trial) or _has_cycle(set(unit_edges), unit_edges):
                diagnostics.append({"code": "soft_order_cycle", "section_id": sid, "after": identity})
                continue
            combined = trial
    result = {"version": ROUTE_TREE_VERSION, "stages": [], "scope": "route_explicit_view"}
    for stage in stage_rows:
        units = stage["units"]
        edges = {uid: set() for uid in units}
        for uid, unit in units.items():
            for section in unit["sections"]:
                edges[uid].update(unit_of[d] for d in combined[section["id"]]
                                  if unit_of[d] in units and unit_of[d] != uid)
        ordered_units = []
        for uid in _topological(units, edges, {uid: unit["_order"] for uid, unit in units.items()}):
            unit = units[uid]
            by_id = {s["id"]: s for s in unit["sections"]}
            unit["sections"] = [by_id[sid] for sid in _topological(
                by_id, combined, {sid: by_id[sid]["_order"] for sid in by_id})]
            unit.pop("_order", None)
            ordered_units.append(unit)
        result["stages"].append({"id": stage["id"], "title": stage["title"],
                                 "optional": stage["optional"], "units": ordered_units})
    final = {}
    for stage in result["stages"]:
        for unit in stage["units"]:
            for section in unit["sections"]:
                final[section["id"]] = len(final)
    for sid, section in sections.items():
        section["depends_on"] = sorted(hard[sid], key=final.__getitem__)
        for key in ("_order", "_after", "_stage"):
            section.pop(key)
    result["diagnostics"] = diagnostics
    return result


def annotate_core_route(path):
    """A detached core route view: the build_study_path tree plus route fields.

    The core route covers the whole catalogue, so external_prerequisites is
    always empty (design 10 D5). The input tree itself is not changed.
    """
    result = {"route_id": CORE_ROUTE_ID, "library_id": CORE_LIBRARY_ID, "title": CORE_ROUTE_TITLE}
    result.update(deepcopy(path))
    result["diagnostics"] = []
    for stage in result["stages"]:
        for unit in stage["units"]:
            for section in unit["sections"]:
                section["external_prerequisites"] = []
    return result


# ---------------------------------------------------------------- study_routes_v1 helpers (WP-R2)
# Pure transforms of the setting value; the owning Store/service writes it.

def routes_state(value):
    """The validated study_routes_v1 value; None (no key) is the default state."""
    return default_routes_state() if value is None else validate_routes_state(value)


def route_row(state, rid, *, source, library_id, now_utc):
    """A detached row of rid: the stored one, or a new unsaved one."""
    row = state["routes"].get(rid)
    if row is not None:
        return deepcopy(row)
    return {"added_sections": [], "enabled": True, "source": source, "library_id": library_id,
            "created_at_utc": now_utc}


def with_route_sections(state, rid, section_ids, *, source, library_id, now_utc):
    """state with section_ids appended (in order, once) to rid's added_sections."""
    if rid == CORE_ROUTE_ID:
        raise ValueError("核心主线的整节加入记录在 study_path_v1。")
    result = deepcopy(state)
    row = route_row(result, rid, source=source, library_id=library_id, now_utc=now_utc)
    row["added_sections"] = list(dict.fromkeys([*row["added_sections"], *section_ids]))
    result["routes"][rid] = row
    return validate_routes_state(result)


def with_active_route(state, rid, *, source, library_id, now_utc):
    """state whose active_route is rid; a non-core route gets a row if it has none."""
    result = deepcopy(state)
    if rid != CORE_ROUTE_ID:
        result["routes"][rid] = route_row(result, rid, source=source, library_id=library_id, now_utc=now_utc)
    result["active_route"] = rid
    return validate_routes_state(result)


def reset_routes_state(state):
    """learning_reset (12 Q6): forget every route's added_sections, keep specs.

    Rows keep enabled, source, created_at_utc and a personal route's spec;
    the active route returns to the core route. None when nothing is left to
    keep (the key is then deleted, the same as the default state).
    """
    result = deepcopy(validate_routes_state(state))
    for row in result["routes"].values():
        row["added_sections"] = []
    result["active_route"] = CORE_ROUTE_ID
    return validate_routes_state(result) if result["routes"] else None



# ---------------------------------------------------------------- personal routes (WP-R5)
# Design 10 section 6.1 and 12 Q3: the learner's chosen targets form a
# "目标" stage; hard prerequisites outside the targets are merged into the
# route as a separate, earlier "前置" stage. Pure: the service stores the spec.

MAX_PERSONAL_TITLE = 60
PERSONAL_PREREQUISITE_STAGE = ("prerequisites", "前置")
PERSONAL_TARGET_STAGE = ("targets", "目标")


def personal_route_title(title):
    """The whitespace-normalised title of a personal route, or ValueError."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("请填写路线标题。")
    title = " ".join(title.split())
    if len(title) > MAX_PERSONAL_TITLE:
        raise ValueError(f"路线标题最多 {MAX_PERSONAL_TITLE} 个字。")
    return title


def _spec_version(now):
    # Pure rule: no wall clock here. The service passes its (app clock) now.
    from datetime import datetime, timezone
    if now is None:
        return 1
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("路线创建时间无效。")
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _clip(value, fallback):
    text = " ".join(str(value or "").split())[:200]
    return text or fallback


def _route_depths(identities, by_identity):
    """Prerequisite depth inside the route, so no two sections of one unit form a cycle."""
    depths, visiting = {}, set()

    def depth(identity):
        if identity in depths:
            return depths[identity]
        if identity in visiting:
            return 0
        visiting.add(identity)
        value = 1 + max((depth(d) for d in by_identity[identity][5]["depends_on"] if d in identities), default=-1)
        visiting.discard(identity)
        depths[identity] = value
        return value

    for identity in sorted(identities):
        depth(identity)
    return depths


def _personal_stage(stage, identities, by_identity, position, depths):
    """Group identities as the core route does (unit key, topic, depth) into one stage."""
    key, title = stage
    units = {}
    for identity in sorted(identities, key=position.__getitem__):
        core_stage, unit_key, unit_title, topic, _, _ = by_identity[identity]
        unit = units.setdefault((core_stage, unit_key), {
            "id": key[0] + "-u" + _digest(f"{core_stage}|{unit_key}"),
            "title": _clip(unit_title, "学习内容"), "sections": {}})
        section = unit["sections"].setdefault((topic, depths[identity]), {
            "id": key[0] + "-s" + _digest(f"{core_stage}|{unit_key}|{topic}|{depths[identity]}"),
            "title": _clip(topic, "学习内容"), "entries": []})
        section["entries"].append(identity)
    return {"id": key, "title": title,
            "units": [{"id": unit["id"], "title": unit["title"], "sections": list(unit["sections"].values())}
                      for unit in units.values()]}


def personal_route_spec(title, target_identities, *, catalogue, kana_rows, extra_items=(), route_key=None,
                        now=None):
    """An explicit-stages spec of a personal route (route:local:<key>).

    Targets keep the core grouping (study_path_records via _learning_route/
    _kana_route/coherence: unit key, topic, depth) in a "目标" stage. Their
    hard-prerequisite closure (learning_dependencies.build_dependency_plan
    over the entry records) outside the targets goes to a separate, earlier
    "前置" stage (12 Q3); a prerequisite that itself needs a target stays with
    the targets. route_key defaults to uuid4().hex; version is the UTC
    creation stamp of now (1 without now). The result has passed validate_route_spec.
    """
    from uuid import uuid4
    from .learning_dependencies import build_dependency_plan
    title = personal_route_title(title)
    if isinstance(target_identities, str):
        target_identities = [target_identities]
    try:
        targets = list(dict.fromkeys(target_identities))
    except TypeError as exc:
        raise ValueError("目标学习项无效。") from exc
    if not targets:
        raise ValueError("请先选择要放进路线的学习项。")
    if len(targets) > MAX_ROUTE_ENTRIES:
        raise ValueError("学习路线条目超过上限。")
    rid = local_route_id(uuid4().hex if route_key is None else route_key)
    records, by_identity, _ = _identity_maps(catalogue, kana_rows, list(extra_items))
    unknown = [identity for identity in targets if not isinstance(identity, str) or identity not in by_identity]
    if unknown:
        raise ValueError("目标学习项不在当前目录中：" + str(unknown[0])[:200])
    graph = {identity: {"id": identity, "depends_on": list(dict.fromkeys(record[5]["depends_on"]))}
             for identity, record in by_identity.items()}
    plan = build_dependency_plan(targets, graph, allow_unresolved=True)
    chosen = set(targets)
    prerequisites = {identity for identity in plan["ordered_ids"] if identity not in chosen}
    while True:
        moved = {identity for identity in prerequisites
                 if any(d in chosen for d in by_identity[identity][5]["depends_on"])}
        if not moved:
            break
        prerequisites -= moved
        chosen |= moved
    if len(chosen) + len(prerequisites) > MAX_ROUTE_ENTRIES:
        raise ValueError("学习路线条目超过上限。")
    position = {record[5]["id"]: index for index, record in enumerate(records)}
    depths = _route_depths(chosen | prerequisites, by_identity)
    stages = []
    if prerequisites:
        stages.append(_personal_stage(PERSONAL_PREREQUISITE_STAGE, prerequisites, by_identity, position, depths))
    stages.append(_personal_stage(PERSONAL_TARGET_STAGE, chosen, by_identity, position, depths))
    spec = {"route_id": rid, "title": title, "library_id": LOCAL_LIBRARY_ID, "version": _spec_version(now),
            "stages": stages}
    kana_identities = {identity for identity in by_identity if identity.startswith("kana:")}
    return validate_route_spec(spec, catalogue_ids=set(by_identity) - kana_identities,
                               kana_identities=kana_identities, library_id=LOCAL_LIBRARY_ID)


def renamed_route_spec(spec, title):
    """A detached copy of a personal spec with a new title."""
    if not isinstance(spec, dict) or not isinstance(spec.get("route_id"), str) or not is_local_route(spec["route_id"]):
        raise ValueError("只能重命名个人路线。")
    result = deepcopy(spec)
    result["title"] = personal_route_title(title)
    return result


def with_personal_route(state, spec, *, now_utc):
    """state with a new personal route row holding spec (enabled, nothing added yet)."""
    rid = spec["route_id"]
    if not is_local_route(rid):
        raise ValueError("个人路线须使用 local 前缀。")
    if rid in state["routes"]:
        raise ValueError("学习路线已存在：" + rid)
    result = deepcopy(state)
    result["routes"][rid] = {"added_sections": [], "enabled": True, "source": "local",
                             "library_id": LOCAL_LIBRARY_ID, "created_at_utc": now_utc, "spec": deepcopy(spec)}
    return validate_routes_state(result)


def with_route_spec(state, rid, spec):
    """state with the stored personal spec of rid replaced."""
    row = state["routes"].get(rid)
    if row is None or row["source"] != "local" or "spec" not in row or spec.get("route_id") != rid:
        raise ValueError("个人路线不存在。")
    result = deepcopy(state)
    result["routes"][rid]["spec"] = deepcopy(spec)
    return validate_routes_state(result)


def without_route(state, rid):
    """state without rid's row; an active rid hands over to the core route.

    Only the route row (its spec and added_sections) goes; item progress and
    study_path_v1 are not part of this value.
    """
    if rid not in state["routes"]:
        raise ValueError("学习路线不存在。")
    result = deepcopy(state)
    del result["routes"][rid]
    if result["active_route"] == rid:
        result["active_route"] = CORE_ROUTE_ID
    return validate_routes_state(result)


class RouteSet:
    """Every usable route of one effective catalogue (design 10 section 3).

    The core route (always first, always enabled) is the service's
    build_study_path tree, annotated lazily; library and personal routes are
    build_route trees. Routes own no progress: entry identities are shared.
    diagnostics lists skipped routes as {code, route_id, library_id, reason}.
    """

    def __init__(self, core_path, others=(), diagnostics=(), sources=None):
        self._core_path = core_path
        self._core = None
        self._others = {tree["route_id"]: tree for tree in sorted(others, key=lambda t: t["route_id"])}
        self.diagnostics = tuple(diagnostics)
        self._sources = dict(sources or {})
        self._index = {}
        self.errors = ()

    def route_ids(self):
        return (CORE_ROUTE_ID, *self._others)

    def has_other_routes(self):
        return bool(self._others)

    def __contains__(self, rid):
        return rid == CORE_ROUTE_ID or rid in self._others

    def route(self, rid):
        if rid == CORE_ROUTE_ID:
            if self._core is None:
                self._core = annotate_core_route(self._core_path)
            return self._core
        if rid not in self._others:
            raise KeyError(rid)
        return self._others[rid]

    def routes(self):
        """Route trees in order: the core route, then the others by route_id."""
        return [self.route(rid) for rid in self.route_ids()]

    def source(self, rid):
        """'core', 'library' or 'local'."""
        if rid == CORE_ROUTE_ID:
            return "core"
        if rid not in self._others:
            raise KeyError(rid)
        return self._sources.get(rid, "local" if is_local_route(rid) else "library")

    def _indexed(self, rid):
        if rid not in self._index:
            from .study_path_catalog import flatten_sections
            sections = flatten_sections(self.route(rid))
            self._index[rid] = (sections, {s["id"]: s for s in sections},
                                {e["id"]: e for s in sections for e in s["entries"]},
                                {e["id"]: s["id"] for s in sections for e in s["entries"]})
        return self._index[rid]

    def sections(self, rid):
        return self._indexed(rid)[0]

    def sections_by_id(self, rid):
        return self._indexed(rid)[1]

    def entry_sections(self, rid):
        return self._indexed(rid)[3]

    def entries(self, rid=None, *, route_ids=None):
        """identity -> entry of one route, or the union (first route wins) of route_ids/all."""
        if rid is not None:
            return self._indexed(rid)[2]
        result = {}
        for key in (self.route_ids() if route_ids is None else route_ids):
            for identity, entry in self._indexed(key)[2].items():
                result.setdefault(identity, entry)
        return result

    def entry_owners(self, identity):
        """Route IDs (route order) whose tree contains identity."""
        return [rid for rid in self.route_ids() if identity in self._indexed(rid)[2]]


_BUILT = {}  # (token, route_id, spec digest, catalogue digest) -> built tree; one token at a time
_MAX_BUILT = 256


def clear_route_cache():
    _BUILT.clear()


def _catalogue_digest(catalogue, kana_rows, extra_items):
    items = list(catalogue.values()) if isinstance(catalogue, dict) else list(catalogue)
    # A saved lesson that is in the catalogue is read from the catalogue (study_path_records and
    # _identity_maps take an extra item only for an id the catalogue lacks), so only the lessons outside it
    # belong to the key. Listing every saved lesson changed the key, and rebuilt every library route, each
    # time a lesson was enrolled.
    known = {item.get("id") for item in items}
    rows = [[str(item.get("id")), str(item.get("revision", "")), sorted(map(str, item.get("depends_on", ()) or ()))]
            for item in [*items, *(extra for extra in extra_items if extra.get("id") not in known)]]
    rows.append(sorted(str(row.get("id", row.get("entry_id"))) for row in kana_rows))
    return _digest(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))


def build_route_set(core_path, *, library_rows=(), local_specs=(), catalogue, kana_rows, extra_items=(),
                    cache_token=None):
    """A RouteSet from the core tree, library rows (route_id, library_id, spec)
    and personal specs (route_id, spec). An invalid route is skipped: it is
    reported in RouteSet.diagnostics (and its exception in RouteSet.errors);
    the core route is never affected. With cache_token (the
    EffectiveCatalogue token) built trees are reused for an identical spec and
    catalogue while the token stays the same; a new token drops them.
    """
    others, diagnostics, errors, sources = [], [], [], {}
    rows = [(rid, library_id, spec, "library") for rid, library_id, spec in library_rows]
    rows += [(rid, LOCAL_LIBRARY_ID, spec, "local") for rid, spec in local_specs]
    extra_items = list(extra_items)
    catalogue_key = None
    if cache_token is not None and rows:
        if any(key[0] != cache_token for key in _BUILT) or len(_BUILT) > _MAX_BUILT:
            _BUILT.clear()
        catalogue_key = _catalogue_digest(catalogue, kana_rows, extra_items)
    seen = set()
    for rid, library_id, spec, source in rows:
        try:
            if rid in seen or rid == CORE_ROUTE_ID or not isinstance(spec, dict) or spec.get("route_id") != rid:
                raise ValueError("学习路线标识重复或与规格不一致。")
            key = None
            if catalogue_key is not None:
                key = (cache_token, rid, _digest(json.dumps(spec, ensure_ascii=False, sort_keys=True)), catalogue_key)
            tree = _BUILT.get(key) if key is not None else None
            if tree is None:
                tree = build_route(spec, catalogue=catalogue, kana_rows=kana_rows, extra_items=extra_items,
                                   library_id=library_id)
                if key is not None:
                    _BUILT[key] = tree
            tree = deepcopy(tree)
        except (ValueError, KeyError, TypeError) as error:
            diagnostics.append({"code": "route_skipped", "route_id": str(rid)[:200],
                                "library_id": str(library_id)[:64], "source": source,
                                "error_type": type(error).__name__,
                                "reason": (str(error).splitlines()[0] if str(error) else type(error).__name__)[:200]})
            errors.append(error)
            continue
        seen.add(rid)
        sources[rid] = source
        others.append(tree)
    result = RouteSet(core_path, others, diagnostics, sources)
    result.errors = tuple(errors)
    return result
