"""Gated questions of route items (design 11 section 16, M5; pure).

A gated question is a declared template (question dependencies and/or reviewed
practice.background_ids) whose requirements are all learned; templates are
counted by fingerprint. Fallback templates (nothing declared), relaxed tiers and
generated sound tasks are not in the table, nor templates that can never be met
(an out-of-vocabulary surface): the adapter (sources) leaves them out.

Each template is listed under its ability and under the learner's choice it
belongs to: group_key(ability, settings group) for each settings group that
switches its kind, kind_key(kind) for a kind no group switches. M5 floors both
(per ability, and per choice: what a learner who keeps only that group on meets).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

KIND_PREFIX, GROUP_PREFIX = "kind:", "group:"


def kind_key(kind: str) -> str:
    """The table key of one question kind no settings group switches (beside the ability keys)."""
    return KIND_PREFIX + kind


def group_key(ability: str, group: str) -> str:
    """The table key of one settings group's kinds of one ability."""
    return f"{GROUP_PREFIX}{ability}:{group}"


def is_choice_key(key: str) -> bool:
    """A group or kind key (choice floor), not an ability (ability floor)."""
    return key.startswith((KIND_PREFIX, GROUP_PREFIX))


class Readiness:
    """item id -> key -> ((template fingerprint, requirement ids), ...), read-only; a key is an ability,
    a group_key or a kind_key.

    One fingerprint may appear more than once (the same raw template under two
    kinds of one ability); it is one question, met when any of its rows is met.
    The target itself is never a requirement.
    """
    __slots__ = ("_table",)

    def __init__(self, table: Mapping[str, Mapping[str, Iterable[tuple[str, Iterable[str]]]]] = ()):
        rows = {}
        for item, abilities in dict(table).items():
            per = {}
            for ability, templates in dict(abilities).items():
                cleaned = sorted({(sha, tuple(sorted(set(requires) - {item}))) for sha, requires in templates})
                if cleaned:
                    per[ability] = tuple(cleaned)
            rows[item] = per
        self._table = rows

    def __contains__(self, item_id) -> bool:
        return item_id in self._table

    def __len__(self) -> int:
        return len(self._table)

    def item_ids(self) -> tuple:
        return tuple(self._table)

    def abilities(self, item_id) -> Mapping[str, tuple]:
        return self._table.get(item_id, {})

    def requirement_index(self) -> dict[str, frozenset]:
        """requirement id -> items with a template that needs it."""
        index = {}
        for item, abilities in self._table.items():
            for templates in abilities.values():
                for _, requires in templates:
                    for requirement in requires:
                        index.setdefault(requirement, set()).add(item)
        return {key: frozenset(value) for key, value in index.items()}

    def counts(self, item_id, known: Callable[[str], bool]) -> dict[str, int]:
        """ability -> distinct fingerprints whose requirements all satisfy ``known``."""
        result = {}
        for ability, templates in self._table.get(item_id, {}).items():
            met = {sha for sha, requires in templates if all(known(r) for r in requires)}
            result[ability] = len(met)
        return result


class ReadinessCounter:
    """Gated counts of some items while the entries of one stage are learned, one by one.

    Requirements known before the stage are met from the start; requirements
    outside the stage and not known before it can never be met here, so their
    templates are left out. Each template row keeps a missing count, each
    stage entry the rows waiting on it: learn() is linear in those rows and
    count() is a lookup. copy() shares the fixed structure.
    """
    __slots__ = ("_index", "_ia", "_ia_groups", "_group_ia", "_group_alts", "_alt_group", "_alt_reqs",
                 "_waiting", "missing", "ready", "count", "known")

    def __init__(self, readiness: Readiness, items: Iterable[str], local_ids, known_before: Callable[[str], bool]):
        if readiness is None:
            return  # copy()
        index = {entry: position for position, entry in enumerate(local_ids)}
        ia, ia_groups, group_ia, group_alts, alt_group, alt_reqs = {}, [], [], [], [], []
        waiting = [[] for _ in index]
        for item in dict.fromkeys(items):
            for ability, templates in readiness.abilities(item).items():
                slot, groups = None, {}
                for sha, requires in templates:
                    local, dead = set(), False
                    for requirement in requires:
                        if known_before(requirement):
                            continue
                        position = index.get(requirement)
                        if position is None:
                            dead = True
                            break
                        local.add(position)
                    if dead:
                        continue
                    if slot is None:
                        slot = ia[item, ability] = len(ia_groups)
                        ia_groups.append([])
                    group = groups.get(sha)
                    if group is None:
                        group = groups[sha] = len(group_ia)
                        group_ia.append(slot)
                        group_alts.append([])
                        ia_groups[slot].append(group)
                    row = len(alt_reqs)
                    alt_reqs.append(tuple(sorted(local)))
                    alt_group.append(group)
                    group_alts[group].append(row)
                    for position in local:
                        waiting[position].append(row)
        self._index, self._ia, self._ia_groups, self._group_ia = index, ia, ia_groups, group_ia
        self._group_alts, self._alt_group, self._alt_reqs, self._waiting = group_alts, alt_group, alt_reqs, waiting
        self.missing = [len(requires) for requires in alt_reqs]
        self.ready = [0] * len(group_ia)
        self.count = [0] * len(ia_groups)
        self.known = bytearray(len(index))
        for row, missing in enumerate(self.missing):
            if not missing:
                self._met(row)

    def copy(self) -> ReadinessCounter:
        other = ReadinessCounter(None, (), (), None)
        for name in ("_index", "_ia", "_ia_groups", "_group_ia", "_group_alts", "_alt_group", "_alt_reqs", "_waiting"):
            setattr(other, name, getattr(self, name))
        other.missing, other.ready, other.count = list(self.missing), list(self.ready), list(self.count)
        other.known = bytearray(self.known)
        return other

    def _met(self, row):
        group = self._alt_group[row]
        self.ready[group] += 1
        if self.ready[group] == 1:
            self.count[self._group_ia[group]] += 1

    def position(self, entry_id):
        """The stage position of an entry (None outside the stage)."""
        return self._index.get(entry_id)

    def learn(self, position: int) -> None:
        if self.known[position]:
            return
        self.known[position] = 1
        missing = self.missing
        for row in self._waiting[position]:
            missing[row] -= 1
            if not missing[row]:
                self._met(row)

    def count_of(self, item, ability) -> int:
        slot = self._ia.get((item, ability))
        return 0 if slot is None else self.count[slot]

    def count_with(self, item, ability, extra: set) -> int:
        """count_of if the stage positions in ``extra`` were learned too (earlier entries of one section)."""
        slot = self._ia.get((item, ability))
        if slot is None:
            return 0
        result, known, size = self.count[slot], self.known, len(extra)
        for group in self._ia_groups[slot]:
            if self.ready[group]:
                continue
            for row in self._group_alts[group]:
                if self.missing[row] <= size and all(known[p] or p in extra for p in self._alt_reqs[row]):
                    result += 1
                    break
        return result

    def requirements(self, item, ability) -> frozenset:
        """Stage positions some template row of (item, ability) still waits on."""
        slot = self._ia.get((item, ability))
        if slot is None:
            return frozenset()
        return frozenset(p for group in self._ia_groups[slot] for row in self._group_alts[group]
                         for p in self._alt_reqs[row])
