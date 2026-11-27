"""Values that one cache hands to many callers, and the test suite's guard that keeps them unchanged.

A shared value (today: a lesson's validated template table, learning_questions.practice_templates) is
built once and handed to every caller as the same object; a caller reads it and never changes it, and
one that needs a changed template takes its own copy of that template (copy.deepcopy, dict(raw),
list(values)). In production ``share`` returns the value itself: the rule costs nothing. Under the test
suite's guard (the suite's configuration sets GUARD_VARIABLE to "1" before the app is imported, or a test
sets GUARD itself; ``share`` reads it at every call) ``share`` returns a read-only
copy whose dicts and lists raise ReadOnlyValue on any change, so a caller that changes what it got is
found by the tests; a copy taken from it (deepcopy, dict(), list(), copy()) is an ordinary value again.

Pure: no Toga, database, platform or content loading.
"""
from __future__ import annotations

from copy import deepcopy
import os

GUARD_VARIABLE = "KANAMEMORY_SHARED_VALUE_GUARD"
GUARD = os.environ.get(GUARD_VARIABLE) == "1"


class ReadOnlyValue(TypeError):
    """A caller changed a shared value instead of its own copy."""


def _refuse(*_args, **_kwargs):
    raise ReadOnlyValue("A shared value is read-only: change a copy of it")


class ReadOnlyDict(dict):
    """A dict that refuses every change; copies of it are plain dicts."""

    __slots__ = ()
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _refuse

    def copy(self):
        return dict(self)

    def __copy__(self):
        return dict(self)

    def __deepcopy__(self, memo):
        result = {}
        memo[id(self)] = result
        for key, value in dict.items(self):
            result[deepcopy(key, memo)] = deepcopy(value, memo)
        return result

    def __reduce__(self):
        return dict, (dict(self),)


class ReadOnlyList(list):
    """A list that refuses every change; copies of it are plain lists."""

    __slots__ = ()
    __setitem__ = __delitem__ = append = extend = insert = pop = remove = clear = sort = reverse = _refuse
    __iadd__ = __imul__ = _refuse

    def copy(self):
        return list(self)

    def __copy__(self):
        return list(self)

    def __deepcopy__(self, memo):
        result = []
        memo[id(self)] = result
        result.extend(deepcopy(value, memo) for value in list.__iter__(self))
        return result

    def __reduce__(self):
        return list, (list(self),)


def read_only(value):
    """A read-only deep copy of ``value``'s dicts and lists (other values are kept as they are)."""
    if isinstance(value, dict):
        return ReadOnlyDict((key, read_only(item)) for key, item in value.items())
    if isinstance(value, list):
        return ReadOnlyList(read_only(item) for item in value)
    return value


def share(value):
    """``value`` as a cache hands it out: itself, or under the test suite's guard its read-only copy."""
    return read_only(value) if GUARD else value


_ATOMS = frozenset((str, int, float, bool, type(None)))   # immutable: shared as they are, also under the guard


def tree_copy(tree, children):
    """deepcopy(tree) as plain data for a caller that changes only the tree's nodes and their child lists.

    children: the key of each level's child list, outermost first (a route tree: ("stages", "units",
    "sections", "entries")). Every node (a dict on the path of those keys) and every child list is new,
    with its keys in their order; every other value of a node is shared (share: itself, or under the
    test suite's guard its read-only copy, so a caller that changes one is found). A top-level value that is
    one of the tree's nodes (an overview's next section) is that node's copy, as deepcopy keeps it.
    """
    copies = {}

    def node(value, level):
        child = children[level] if level < len(children) else None
        copy = {}
        for key, part in value.items():
            copy[key] = ([node(item, level + 1) for item in part] if key == child
                         else part if type(part) in _ATOMS else share(part))
        copies[id(value)] = copy
        return copy
    if not children:
        return {key: share(part) for key, part in tree.items()}
    top = {key: [node(item, 1) for item in part] for key, part in tree.items() if key == children[0]}
    return {key: top[key] if key in top else copies.get(id(part)) if isinstance(part, dict) and id(part) in copies
            else share(part) for key, part in tree.items()}
