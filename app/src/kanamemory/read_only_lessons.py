"""Lessons that are only read, and abilities a lesson no longer lists (brief J6 R1-R4).

A lesson may list no ability at all (``abilities: []``): it is valid content that is only read. It has
no unit and no question; it is offered once as new content, its explanation page ends with 「知道了」,
and that completes it (its stage is done). From then on it counts as taught and known wherever a
rule asks about a prerequisite or a requirement.

A content update may also take abilities away from a lesson a learner has already enrolled. The
saved units of the abilities its catalogue entry no longer lists stay stored (history), but are never
planned, served or counted again; a lesson left with no unit of a listed ability is only read, and
counts as read if its explanation had already been shown.

This module reads the learning tables and the effective catalogue; it writes nothing. The learning
store owns every transaction (LearningStore.complete_read_only).
"""
from __future__ import annotations

import json

from . import ability_teaching as _teaching
from .content_caches import register

# The ability spellings older lessons use (LearningStore normalises them the same way).
ABILITY_ALIASES = {"meaning": "understanding", "usage": "use"}
_MEMO = {}   # id(connection) -> (enrolment key, candidates, connection) of _candidates


def _ability(value):
    return ABILITY_ALIASES.get(value, value)


def retired_ability(content_id, ability) -> bool:
    """Whether a saved unit's ability is one its lesson no longer lists (R4).

    The effective catalogue's lesson of that id lists its abilities now. Content outside the catalogue
    (custom, AI, restored from elsewhere) keeps every saved unit.
    """
    from .learning_content import catalogue_abilities
    listed = catalogue_abilities(content_id)
    return listed is not None and _ability(ability) not in {_ability(value) for value in listed}


def _clear_memo():
    _MEMO.clear()


register(_clear_memo, "read_only_lessons._MEMO")


def _enrolment_key(db):
    """What changes when lessons or units are enrolled or removed (never with an ordinary answer): the unit and
    content row counts and largest rowids, read in one statement."""
    return tuple(db.execute("SELECT (SELECT COUNT(*) FROM learning_unit),(SELECT MAX(rowid) FROM learning_unit),"
                            "(SELECT COUNT(*) FROM learning_content),(SELECT MAX(rowid) FROM learning_content)").fetchone())


def _candidates(db):
    """The enrolled lessons that CAN be only read: no unit, or every unit of an ability the catalogue no longer lists.

    It changes only with the catalogue (a process constant; content_caches drops the memo) and with
    enrolment, so it is remembered per connection under _enrolment_key; an answer does not touch it.
    Returns (frozenset of ids, the set of those that have units).
    """
    key = _enrolment_key(db)
    memo = _MEMO.get(id(db))
    if memo is not None and memo[0] == key and memo[2] is db:
        return memo[1]
    abilities, empty = {}, set()
    # One statement: every enrolled lesson with the abilities of its units (none: a lesson without a unit).
    for content_id, ability in db.execute("""SELECT c.content_id,u.ability FROM learning_content c
            LEFT JOIN learning_unit u ON u.content_id=c.content_id"""):
        if ability is None:
            empty.add(content_id)
        else:
            abilities.setdefault(content_id, []).append(ability)
    retired_all = {cid for cid, values in abilities.items() if all(retired_ability(cid, value) for value in values)}
    result = (frozenset(empty | retired_all), frozenset(retired_all))
    if len(_MEMO) >= 8:
        _MEMO.clear()   # a few connections at a time; old ones are not kept alive
    _MEMO[id(db)] = (key, result, db)
    return result


def read_only_lessons(db, *, lesson_ids=None) -> dict:
    """{content_id: {"position", "stage", "read"}} of the enrolled lessons that are only read.

    Only read: no unit (enrolled with ``abilities: []``), or every unit is of an ability the lesson no
    longer lists (retired_ability). Read: its stage is done (「知道了」), or -- for one that became
    read-only by a content update -- its explanation had already been shown in one of its scopes.
    position is the content row's rowid (the order of enrolment, which follows the route). Which
    lessons can be only read is remembered per connection until enrolment changes or the content caches
    are dropped (_candidates); each call then reads only those lessons' rows (none in a course without
    such lessons), so a pass after an ordinary answer costs one small statement. lesson_ids: the ids of every
    enrolled lesson when the caller has just read them in the same pass; none enrolled needs no statement.
    """
    from .probe_rules import explanation_shown
    if lesson_ids is not None and not lesson_ids:
        return {}
    candidates, with_units = _candidates(db)
    if not candidates:
        return {}
    result = {}
    ids = sorted(candidates)
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        for content_id, position, stage, checkpoint_json in db.execute(
                "SELECT content_id,rowid AS position,stage,checkpoint_json FROM learning_content "
                "WHERE content_id IN (%s)" % ",".join("?" * len(chunk)), chunk):
            read = stage == "done"
            if not read and content_id in with_units:
                state = _teaching.read(db, content_id) or {}
                original = json.loads(checkpoint_json) if checkpoint_json else {}
                checkpoints = [original, *(json.loads(value["checkpoint_json"])
                                           for value in (state.get("additions") or {}).values())]
                read = explanation_shown(checkpoints, batch_route=original.get("intro_route") == "batch_v1")
            result[content_id] = {"position": position, "stage": stage, "read": read}
    return result


def read_lesson_ids(db) -> frozenset:
    """Lessons that are only read and were read: taught and known wherever a rule asks (R2)."""
    return frozenset(cid for cid, value in read_only_lessons(db).items() if value["read"])


class ReadOnlyLessonService:
    """The learning pages' one way to lessons that are only read (J6 R1, R2): values out, one write in.

    Pages never hold the learning store for this: ``for_app(app)`` builds the service from the app's
    store, and the store keeps the transaction (LearningStore.complete_read_only).
    """

    def __init__(self, learning):
        self._learning = learning

    def lesson(self, content_id):
        """The lesson's teaching item to show its explanation page (None: not enrolled)."""
        return self._learning.instruction_item(content_id)

    def unread_prerequisites(self, dependency_status):
        """The unmet prerequisites of a dependant that are only read and not read yet, as {id, title}."""
        state = self._learning.read_only_state()
        return [{"id": entry["id"], "title": entry.get("title", entry["id"])}
                for entry in (dependency_status or {}).get("dependencies", ())
                if not entry.get("ready") and entry.get("id") in state and not state[entry["id"]]["read"]]

    def complete(self, item, *, fair_new):
        """「知道了」: True when it completed the lesson now, False when it was read before."""
        return self._learning.complete_read_only(item["id"], expected_introduction_id=item["introduction_id"],
                                                 fair_new=fair_new)


def for_app(app) -> ReadOnlyLessonService:
    """The service over the app's learning store (the page passes itself, never the store)."""
    return ReadOnlyLessonService(app.learning_store)
