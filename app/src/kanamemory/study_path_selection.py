"""Pure hierarchical selection over a path overview, with no learner writes.

Selection belongs to entry identities. Expanding/collapsing the UI therefore
cannot lose choices, and parent toggles never include already enrolled entries.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

NodeKey = tuple[str, str]
SelectionState = Literal["none", "all", "partial"]


@dataclass(frozen=True, slots=True)
class NodeSelectionStatus:
    """One immutable read shared by checkbox, description and feedback rendering."""

    total_count: int
    new_count: int
    selected_count: int

    @property
    def state(self) -> SelectionState:
        if not self.selected_count:
            return "none"
        return "all" if self.selected_count == self.new_count else "partial"

    def counts(self) -> dict[str, int]:
        return {
            "total_count": self.total_count,
            "new_count": self.new_count,
            "added_count": self.total_count - self.new_count,
            "selected_count": self.selected_count,
        }


class StudyPathSelection:
    def __init__(self, overview, selected_entry_ids=()):
        self._selected = set()
        self.refresh(overview)
        if isinstance(selected_entry_ids, str):
            selected_entry_ids = [selected_entry_ids]
        self._selected = set(selected_entry_ids) & self._new

    def refresh(self, overview):
        """Keep still-eligible choices when enrollment or the catalogue changes."""
        snapshot = deepcopy(overview)
        # Retired script-specific signs stay in the catalogue for old links,
        # but enrollment offers their one shared rule, not duplicate read flags.
        removed_sections, removed_units = set(), set()
        for stage in snapshot["stages"]:
            for unit in stage["units"]:
                for section in unit["sections"]:
                    previous = bool(section["entries"])
                    section["entries"] = [
                        entry
                        for entry in section["entries"]
                        if not (entry.get("kind") == "reference" and entry.get("reading_rule_id"))
                    ]
                    if previous and not section["entries"]:
                        removed_sections.add(section["id"])
                previous = bool(unit["sections"])
                unit["sections"] = [
                    section for section in unit["sections"] if section["id"] not in removed_sections
                ]
                if previous and not unit["sections"]:
                    removed_units.add(unit["id"])
            stage["units"] = [unit for unit in stage["units"] if unit["id"] not in removed_units]
        nodes, descendants, order, new = {}, {}, [], set()

        def visit(kind, value):
            identity = (kind, value["id"])
            if identity in nodes:
                raise ValueError("学习路线含重复的节点标识。")
            nodes[identity] = value
            if kind == "entry":
                eid = value["id"]
                order.append(eid)
                if value.get("state") == "new":
                    new.add(eid)
                descendants[identity] = (eid,)
            else:
                child_kind, field = {
                    "stage": ("unit", "units"),
                    "unit": ("section", "sections"),
                    "section": ("entry", "entries"),
                }[kind]
                entries = []
                for child in value[field]:
                    entries.extend(visit(child_kind, child))
                descendants[identity] = tuple(entries)
            return descendants[identity]

        for stage in snapshot["stages"]:
            visit("stage", stage)
        self.overview = snapshot
        self._nodes, self._descendants = nodes, descendants
        self._order, self._new = tuple(order), new
        self._selected &= new
        return self.selected_entry_ids

    @property
    def selected_entry_ids(self):
        return [eid for eid in self._order if eid in self._selected]

    @property
    def selected_count(self):
        return len(self._selected)

    def node_status(self, key: NodeKey) -> NodeSelectionStatus:
        entries = self._descendants[key]
        eligible = [eid for eid in entries if eid in self._new]
        return NodeSelectionStatus(
            total_count=len(entries),
            new_count=len(eligible),
            selected_count=sum(eid in self._selected for eid in eligible),
        )

    def counts(self, key: NodeKey) -> dict[str, int]:
        return self.node_status(key).counts()

    def state(self, key: NodeKey) -> SelectionState:
        return self.node_status(key).state

    def select(self, key, selected=True):
        if type(selected) is not bool:
            raise ValueError("选择状态必须是布尔值。")
        eligible = set(self._descendants[key]) & self._new
        if selected:
            self._selected.update(eligible)
        else:
            self._selected.difference_update(eligible)
        return self.selected_entry_ids

    def toggle(self, key):
        return self.select(key, self.state(key) != "all")

    def clear(self):
        self._selected.clear()
        return self.selected_entry_ids
