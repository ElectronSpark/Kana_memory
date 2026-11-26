"""Display models and fixed wording of the add-content page 添加学习内容 (design 13 S2, C2).

Pure, immutable data: strings, numbers, node keys, tuples and the feedback
dicts the page hangs on its widgets (their shapes are a recorded contract).
No widgets, stores, services or callbacks. Every sentence is composed here or
by the service; the page only lays it out. What expanded, open or typed is
the page's own display state and comes in as an argument.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace

from ..study_lines import SUGGEST_MARK, RouteLines, counts_text
from ..study_path_display import SECTION_STATES, path_display_snapshot
from ..study_path_selection import NodeKey, NodeSelectionStatus

# Fixed wording of the page.
PAGE_TITLE = "添加学习内容"
SUBMIT = "加入并继续"
CLEAR = "清空选择"
SHORTCUT = "按顺序选多节"
ADD_PREVIEWED = "加入勾选"
SECTION_COUNT_PLACEHOLDER = "小节数量"
SAVE_ROUTE = "保存为路线"
ROUTE_SAVE_NOTE = "把本次选择保存为个人路线，所需前置自动放进“前置”阶段。只保存路线，不加入学习。"
ROUTE_TITLE_PLACEHOLDER = "路线标题"
CREATE_ROUTE = "创建路线"
CANCEL = "取消"
COUNT_INVALID = "请输入大于 0 的小节数量。"
SELECTION_SAVE_FAILED = "未能保存本次选择，请重试。"
ROUTE_SAVE_FAILED = "未能保存路线，请重试。"
ROUTE_EMPTY = "请先选择要放进路线的学习项。"
UNTITLED = "学习内容"
OUTSIDE_ROUTE = "（路线外）"
PREVIEW_VISIBLE = 5   # section titles the shortcut preview names before "另有 N 节"
# The first part of the two-line design: the quick choices by line, each line's next section, the mark, the
# readiness line and the lessons it misses.
ADD_LINE = {"word": "只勾选词汇", "grammar": "只勾选语法"}   # beside 加入勾选: the next N sections of one line
# The preview's line per quick choice by line: what that button would tick for the same count.
LINE_PREVIEW = "{button}：{count} 节 · {titles}"
LINE_PREVIEW_MORE = " 等"   # after the titles when the button ticks more sections than it names
LINE_PREVIEW_NONE = "{button}：没有可加的小节"
LINE_PREVIEW_VISIBLE = 3
LINE_PREVIEW_JOINER = "、"
LINE_NEXT = "下一节{line}：{title}"
SUGGESTED = " · " + SUGGEST_MARK
SUGGEST_OTHER = SUGGEST_MARK + "：{title}"
MISSING = "还缺：{items}"
MISSING_ITEM = "{title}（{section}）"
MISSING_VISIBLE = 6   # missing lessons named before "等 N 项"
MISSING_MORE = " 等 {count} 项"
ADD_MISSING = "一并加入"


@dataclass(frozen=True)
class RouteChoice:
    route_id: str
    caption: str   # 改看<route>


@dataclass(frozen=True)
class RouteSwitch:
    """The route line at the top of the page; only when the learner has other routes."""

    label: str                          # 路线：<route>（同一进度，不同路线）
    choices: tuple[RouteChoice, ...]    # the other enabled routes, in route order


def route_switch(current_title: str, others: Sequence[tuple[str, str]]) -> RouteSwitch:
    """``others``: (route_id, title) of the other enabled routes."""
    return RouteSwitch("路线：" + current_title + "（同一进度，不同路线）",
                       tuple(RouteChoice(rid, "改看" + title) for rid, title in others))


@dataclass(frozen=True)
class NodeModel:
    """One row of the selection tree: a stage, unit, section or entry."""

    key: NodeKey
    depth: int                      # 0 stage, 1 unit, 2 section, 3 entry
    title: str
    status: NodeSelectionStatus     # counts and the check state
    description: str
    snapshot: Mapping[str, object]  # the node's path_display_snapshot fields (read only), for its feedback fragment
    detail: tuple[str, str] | None = None   # an entry's detail page: ("kana" | "learning", content id)
    study_text: bool = False        # an entry title in Japanese study type (kana, reference, word)

    @classmethod
    def of(cls, key: NodeKey, value: Mapping, depth: int, status: NodeSelectionStatus) -> NodeModel:
        title = value.get("title") or value.get("label") or UNTITLED
        detail, study_text = None, False
        if key[0] == "entry":
            detail = ("kana" if value.get("kind") in {"kana", "reference"} else "learning", value.get("content_id"))
            study_text = value.get("kind") in {"kana", "reference"} or value.get("family") == "word"
        # Not copied here: feedback() deep-copies it at every draw, so each fragment is still its own copy (as
        # before the move). The values stay the selection's overview copy's, unchanged after its refresh().
        return cls(key, depth, title, status, node_description(key, value, status.counts()),
                   path_display_snapshot(value, "study_path_" + key[0], copy=False), detail, study_text)

    def with_status(self, status: NodeSelectionStatus) -> NodeModel:
        """The same row after the selection changed (an entry's description does not depend on it)."""
        description = self.description if self.key[0] == "entry" else group_description(status.counts())
        return replace(self, status=status, description=description)

    @property
    def state(self) -> str:
        return self.status.state

    @property
    def enabled(self) -> bool:
        """Whether the box can be ticked: something under it is not enrolled yet."""
        return bool(self.status.new_count)

    def counts(self) -> dict[str, int]:
        return self.status.counts()

    def feedback(self, expanded: bool) -> dict:
        """The row's feedback fragment (kind study_path_selection_node)."""
        # A copy: the fragment hangs on a widget and must not share a dict with this model.
        return {"kind": "study_path_selection_node", "node_key": list(self.key),
                "path_snapshot": deepcopy(self.snapshot),
                "selection_state": self.state, "counts": self.counts(), "expanded": expanded}


def node_description(key: NodeKey, value: Mapping, counts: Mapping[str, int]) -> str:
    if key[0] == "entry":
        state = value.get("state", "new")
        if value.get("kind") == "reference":
            return "阅读参考 · " + ("已读" if state == "completed" else "未加入" if state == "new" else "已加入")
        return SECTION_STATES.get(state, "未加入")
    return group_description(counts)


def group_description(counts: Mapping[str, int]) -> str:
    result = f"已加入 {counts['added_count']}/{counts['total_count']} 项"
    if counts["new_count"]:
        result += f" · 可选 {counts['new_count']} 项"
    if counts["selected_count"]:
        result += f"\n本次已选 {counts['selected_count']} 项"
    return result


def tree_nodes(overview: Mapping) -> Iterator[tuple[NodeKey, Mapping, int]]:
    """(key, node, depth) of every stage, unit, section and entry, in tree order."""
    for stage in overview["stages"]:
        yield ("stage", stage["id"]), stage, 0
        for unit in stage["units"]:
            yield ("unit", unit["id"]), unit, 1
            for section in unit["sections"]:
                yield ("section", section["id"]), section, 2
                for entry in section["entries"]:
                    yield ("entry", entry["id"]), entry, 3


def visible_rows(selection, expanded) -> tuple[NodeModel, ...]:
    """The rows whose ancestors are all expanded, in tree order (``selection``: a StudyPathSelection)."""
    rows, parents = [], []
    for key, value, depth in tree_nodes(selection.overview):
        parents = parents[:depth]
        visible = all(parent in expanded for parent in parents)
        parents.append(key)
        if visible:
            rows.append(NodeModel.of(key, value, depth, selection.node_status(key)))
    return tuple(rows)


def selection_feedback(plan: Mapping) -> dict:
    """The selection tree's fragment for a plan (the page adds its display fields)."""
    return {"kind": "study_path_selection", "version": 1, "valid": True,
            "snapshot_scope": "selection_and_visible_nodes",
            "selected_entry_ids": list(plan["selected_entry_ids"]),
            "prerequisite_entry_ids": list(plan["prerequisite_entry_ids"]),
            "selected_count": plan["selected_count"], "prerequisite_count": plan["prerequisite_count"],
            "total_count": plan["total_count"], "section_count": plan["section_count"]}


@dataclass(frozen=True)
class LineRow:
    """One line's next section on the add page (the first part of the two-line design).

    ``text``: 下一节词汇：<title>, with · 建议先加 when it is the suggestion;
    ``readiness``: 现在能出：理解 6／应用 5, "" without a readiness table;
    ``missing``: 还缺：<lesson>（<section>）、…, "" while the section is ready;
    ``missing_ids``: the entries 一并加入 ticks.
    """

    line: str
    section_id: str
    text: str
    suggested: bool = False
    readiness: str = ""
    missing: str = ""
    missing_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class LinesModel:
    """Both lines' rows, and the suggestion when it is no line's next section (with its reason)."""

    rows: tuple[LineRow, ...] = ()
    suggestion: str = ""
    reason: str = ""

    @classmethod
    def of(cls, lines: RouteLines) -> LinesModel:
        suggestion = lines.suggestion
        rows = []
        marked = False
        for item in lines.lines:
            suggested = (suggestion is not None and suggestion.line is not None
                         and suggestion.section_id == item.section_id)
            marked = marked or suggested
            readiness = missing = ""
            missing_ids: tuple[str, ...] = ()
            if item.verdict is not None:
                readiness = counts_text(item.verdict.counts) if item.verdict.counts else ""
                if not item.verdict.ready and item.verdict.missing:
                    named = [MISSING_ITEM.format(title=m.title, section=m.section_title)
                             for m in item.verdict.missing[:MISSING_VISIBLE]]
                    more = len(item.verdict.missing) - MISSING_VISIBLE
                    missing = MISSING.format(items="、".join(named)) + (MISSING_MORE.format(count=more) if more > 0 else "")
                    missing_ids = tuple(m.entry_id for m in item.verdict.missing)
            rows.append(LineRow(item.line, item.section_id,
                                LINE_NEXT.format(line=item.line_title, title=item.title) + (SUGGESTED if suggested else ""),
                                suggested, readiness, missing, missing_ids))
        other = ""
        if suggestion is not None and suggestion.line is not None and not marked:
            other = SUGGEST_OTHER.format(title=suggestion.title)
        return cls(tuple(rows), other, suggestion.reason if suggestion is not None else "")


@dataclass(frozen=True)
class PlanModel:
    """What the page shows about planning the current selection.

    ``valid`` False: planning failed; ``summary`` is the error, the workload
    line is emptied but its feedback is left as it was (``workload`` None),
    the prerequisites are cleared and the closure line is left as it was.
    """

    summary: str
    can_submit: bool
    can_save_route: bool
    feedback: Mapping                  # the selection tree's fragment, without the page's display fields
    valid: bool = True
    routes_line: str | None = None     # the closure line of a non-core route; None: not shown or left as it was
    workload_text: str = ""
    workload: Mapping | None = None    # admission_workload.admission_snapshot
    prerequisite_count: int = 0
    lines: LinesModel | None = None    # the two lines (two-line design); None: not shown (a failed plan)

    @classmethod
    def planned(cls, plan: Mapping, workload: Mapping, workload_text: str, *, routes_line: str | None,
                can_save_route: bool, lines: LinesModel | None = None) -> PlanModel:
        return cls(plan["summary"], bool(plan["total_count"]), can_save_route, selection_feedback(plan),
                   routes_line=routes_line, workload_text=workload_text, workload=workload,
                   prerequisite_count=plan["prerequisite_count"], lines=lines)

    @classmethod
    def failed(cls, error: str, selected: Sequence[str], *, can_save_route: bool) -> PlanModel:
        return cls(error, False, can_save_route, {"kind": "study_path_selection", "version": 1, "valid": False,
                                                  "selected_entry_ids": list(selected), "error": error},
                   valid=False)

    @property
    def workload_feedback(self) -> dict | None:
        """The workload line's fragment (kind admission_workload); None: keep the one it has."""
        return None if self.workload is None else {"kind": "admission_workload", "version": 1,
                                                    "snapshot": deepcopy(self.workload)}


@dataclass(frozen=True)
class Prerequisites:
    """查看/收起前置内容 and, while open, the prerequisite titles (outside the route marked （路线外）)."""

    count: int
    open: bool
    titles: tuple[str, ...] = ()

    @property
    def caption(self) -> str:
        return ("收起" if self.open else "查看") + f"前置内容（{self.count} 项）"


@dataclass(frozen=True)
class ShortcutPreview:
    """The preview under the section-count field of 按顺序选多节; it never selects."""

    feedback: Mapping                  # kind study_path_add_preview
    error: str = ""                    # COUNT_INVALID when the text is not a count
    heading: str = ""                  # 将勾选 N 节
    titles: tuple[str, ...] = ()       # at most PREVIEW_VISIBLE section titles
    remainder: str = ""                # 另有 N 节
    complete: str = ""                 # <route>小节都已加入。
    lines: tuple[LinePreview, ...] = ()   # below the block above: one per quick choice by line (none when invalid)

    @classmethod
    def invalid(cls, requested_text: str) -> ShortcutPreview:
        return cls(_preview_feedback(requested_text), error=COUNT_INVALID)

    @classmethod
    def planned(cls, requested_text: str, count: int, sections: Sequence, visible: Sequence[Mapping],
                complete_title: str | None = None, lines: Sequence[LinePreview] = ()) -> ShortcutPreview:
        """``visible``: the first sections as section dicts; ``complete_title``: the route title when none is left;
        ``lines``: what each quick choice by line would tick for the same count (LinePreview.of)."""
        feedback = {**_preview_feedback(requested_text), "valid": True,
                    "requested_count": count, "planned_count": len(sections),
                    "selection_rule": "next_new_main_sections_in_catalogue_order",
                    "visible_sections": [path_display_snapshot(section, "study_path_section") for section in visible],
                    "additional_count": max(0, len(sections) - len(visible))}
        if lines:
            feedback["line_sections"] = {line.line: list(line.section_ids) for line in lines}
        remainder = f"另有 {len(sections) - PREVIEW_VISIBLE} 节" if len(sections) > PREVIEW_VISIBLE else ""
        complete = complete_title + "小节都已加入。" if not sections and complete_title is not None else ""
        return cls(feedback, heading=f"将勾选 {len(sections)} 节",
                   titles=tuple(section["title"] for section in visible), remainder=remainder, complete=complete,
                   lines=tuple(lines))


@dataclass(frozen=True)
class LinePreview:
    """One quick choice by line in the preview: the sections its button would tick (all of them, by id) and its
    text, 「只勾选词汇：N 节 · <title>、<title>、<title>」 (「 等」 after more than three) or 没有可加的小节."""

    line: str                          # study_lines.WORD / GRAMMAR
    text: str
    section_ids: tuple[str, ...]

    @classmethod
    def of(cls, line: str, section_ids: Sequence[str], titles: Sequence[str]) -> LinePreview:
        """``titles``: the titles of the first sections (at least LINE_PREVIEW_VISIBLE of them when there are)."""
        button = ADD_LINE[line]
        if not section_ids:
            return cls(line, LINE_PREVIEW_NONE.format(button=button), ())
        count = len(section_ids)
        text = LINE_PREVIEW.format(button=button, count=count,
                                   titles=LINE_PREVIEW_JOINER.join(titles[:LINE_PREVIEW_VISIBLE]))
        if count > LINE_PREVIEW_VISIBLE:
            text += LINE_PREVIEW_MORE
        return cls(line, text, tuple(section_ids))


def _preview_feedback(requested_text: str) -> dict:
    return {"kind": "study_path_add_preview", "snapshot_scope": "displayed_batch_preview",
            "requested_text": requested_text, "valid": False}


@dataclass(frozen=True)
class AddDelta:
    """The answer to a tick, 清空选择 or 加入勾选: only the rows whose state changed, and the new plan.

    ``rejection``: the request was refused (an invalid count) and nothing changed; ``plan`` is then None.
    """

    nodes: tuple[NodeModel, ...] = ()
    plan: PlanModel | None = None
    rejection: str = ""


@dataclass(frozen=True)
class AddPageModel:
    """The whole page: on opening, and after a rebase (Back, 加入并继续)."""

    nodes: tuple[NodeModel, ...]        # the visible rows, in tree order
    expanded: frozenset[NodeKey]        # on opening: the branch of the preselected node
    plan: PlanModel
    on_route: bool                      # a non-core route: the closure line is shown
    route_switch: RouteSwitch | None = None   # on opening only, and only with other routes
    preview: ShortcutPreview | None = None    # after a rebase while 按顺序选多节 is open
    # The prerequisites line of ``plan`` (None after a failed plan), read with the page: a rebase before
    # 加入并继续 reads the titles before the enrollment is written, as the page always did.
    prerequisites: Prerequisites | None = None
