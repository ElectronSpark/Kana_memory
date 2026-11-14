"""Pure helpers the study path pages and the add-content service share (moved from study_path_ui, design 13 S2).

The state names of a path node, the feedback snapshot of one hierarchy
level, the route helpers over a path service (other_route_rows since design
13 S3: the home service lists the other routes too) and the closure line of
an enrollment preview. No Toga, no app object; study_path_ui imports them
under the same names.
"""
from __future__ import annotations

from copy import deepcopy

from .study_routes import CORE_ROUTE_ID, CORE_ROUTE_TITLE

SECTION_STATES = {
    "new": "未加入", "partial": "部分已加入", "added": "已加入",
    "started": "学习中", "completed": "初学完成",
}


def path_display_snapshot(value, kind, *, copy=True):
    """Keep the data rendered at this hierarchy level, not its hidden subtree.

    Progress summaries are already derived from the learner's records. A stage
    button does not display every descendant card, so exporting those cards
    both overstates the page's scope and exhausts feedback capture limits.
    Actual entries on a section detail page retain their content identities;
    the ordinary feedback collector still captures their individual progress.
    copy=False: the same fields without copying their values (they stay
    ``value``'s); for a holder that deep-copies the result when it hands it out.
    """
    fields = ("id", "title", "stage_id", "stage_title", "unit_id", "unit_title",
              "state", "progress", "new_count", "optional")
    if kind in {"study_path_entry", "study_path_reference"}:
        fields += ("content_id", "kind", "script", "family", "domain", "label")
    result = {key: deepcopy(value[key]) if copy else value[key] for key in fields if key in value}
    if kind == "study_path_section":
        entries = value.get("entries", [])
        result["entry_count"] = len(entries)
        result["reference_only"] = bool(entries) and all(entry.get("kind") == "reference" for entry in entries)
    return result


# ------------------------------------------------------------ routes (design 10, WP-R3)
# Pages pass explicit route IDs between themselves; the service sees route_id
# only for a non-core route, so the core route keeps its pre-route calls and
# results. A service without the route API (standalone/test doubles) is the
# core route alone. Wording: one progress, different routes (no "sync").

def route_key(route_id):
    """None for the core route (or no route), else the route ID."""
    return None if route_id is None or route_id == CORE_ROUTE_ID else route_id


def route_kwargs(route_id):
    """Keyword arguments for a service or page call: {} for the core route."""
    rid = route_key(route_id)
    return {} if rid is None else {"route_id": rid}


def active_route(service):
    """The current route ID (StudyPathService.active_route_id), core when unsupported."""
    reader = getattr(service, "active_route_id", None)
    return reader() if callable(reader) else CORE_ROUTE_ID


def has_other_routes(service):
    """True when the service's RouteSet has a usable non-core route."""
    reader = getattr(service, "route_set", None)
    return bool(callable(reader) and reader().has_other_routes())


def route_title(service, route_id):
    """Display (short) title of a route: the core route is "主线"."""
    rid = route_key(route_id)
    if rid is None:
        return CORE_ROUTE_TITLE
    return service.route_set().route(rid)["title"]


def other_route_rows(service, route_id, *, limit=None):
    """routes_overview rows of the other enabled, available routes with work.

    "Work": sections in progress or a next section. [] when the service has
    no other usable route, so a core-only learner reads no route list.
    """
    if not has_other_routes(service):
        return []
    current = route_key(route_id) or CORE_ROUTE_ID
    rows = [row for row in service.routes_overview()["routes"]
            if row["available"] and row["enabled"] and row["route_id"] != current
            and (row["in_progress_sections"] or row["next_section"])]
    return rows if limit is None else rows[:limit]


def closure_text(service, plan):
    """Design 10 D4 split of one enrollment preview, or "" without closure_by_route.

    The core route covers the whole catalogue, so a prerequisite outside a
    non-core route normally counts under the core route ("其中主线 M 项").
    """
    split = plan.get("closure_entry_ids_by_route") if isinstance(plan, dict) else None
    if not split:
        return ""
    parts = [f"本路线内 {len(split['this_route'])} 项"]
    others = split["other_routes"]
    outside = list(dict.fromkeys(eid for ids in others.values() for eid in ids))
    if outside:
        names = [f"其中{route_title(service, rid)} {len(ids)} 项" for rid, ids in others.items()]
        parts.append(f"其他路线 {len(outside)} 项（" + "，".join(names) + "）")
    if split["no_route"]:
        parts.append(f"目录项 {len(split['no_route'])} 项")
    return " · ".join(parts)
