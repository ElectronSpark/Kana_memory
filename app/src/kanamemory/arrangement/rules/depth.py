"""How far kana practice goes: a preset of the mixed-practice kind switches (design 12 D1, D2).

Pure. Three levels; each turns on the reading kinds and, cumulatively, the
spelling kinds and look-at-romaji handwriting. Listening handwriting
(kana_handwriting) and the two word kinds are outside the preset: they keep
whatever the learner set. The preset only rewrites switches; it never
touches a due time.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import Enum


class KanaDepth(str, Enum):
    RECOGNITION = "recognition"   # 只认读
    SPELLING = "spelling"         # 认读＋拼写
    HANDWRITING = "handwriting"   # 认读＋拼写＋手写


LEVELS = (KanaDepth.RECOGNITION, KanaDepth.SPELLING, KanaDepth.HANDWRITING)

READING_KINDS = ("kana_choice", "kana_romaji")
SPELLING_KINDS = ("romaji_kana", "romaji_kana_full")
HANDWRITING_KINDS = ("romaji_handwriting",)

LEVEL_KINDS = {
    KanaDepth.RECOGNITION: READING_KINDS,
    KanaDepth.SPELLING: READING_KINDS + SPELLING_KINDS,
    KanaDepth.HANDWRITING: READING_KINDS + SPELLING_KINDS + HANDWRITING_KINDS,
}
# The kinds a level decides; every other kind keeps the learner's switch.
PRESET_KINDS = LEVEL_KINDS[KanaDepth.HANDWRITING]

# One group per scheduling ability, as kana_ability_bridge.SKILL_TO_ABILITY
# groups the kinds (a test keeps the two equal): a due card is served by any
# enabled kind of its ability. kana_handwriting is its own group and outside
# the preset, so the preset never stops it.
ABILITY_GROUPS = {"reading": READING_KINDS, "spelling": SPELLING_KINDS,
                  "handwriting": HANDWRITING_KINDS, "legacy_combined": ("kana_handwriting",)}
_GROUP_OF = {kind: group for group, kinds in ABILITY_GROUPS.items() for kind in kinds}

# Titles of the preset groups the consequence sentence names.
GROUP_TITLES = {"reading": "认读", "spelling": "拼写", "handwriting": "手写"}


def mixed_kinds_for(level: KanaDepth, current: Iterable[str], order: Sequence[str]) -> tuple[str, ...]:
    """The mixed kinds after applying ``level`` to the ``current`` switches.

    ``order`` is every known mixed kind in display order (practice_preferences.
    ALL_MIXED_KINDS); the result follows it. Kinds outside the preset stay as
    they are in ``current``; unknown kinds are dropped.
    """
    wanted = set(LEVEL_KINDS[KanaDepth(level)])
    current = set(current)
    return tuple(kind for kind in order
                 if kind in wanted or (kind not in PRESET_KINDS and kind in current))


def level_of(current: Iterable[str]) -> KanaDepth | None:
    """The level whose preset the ``current`` switches match exactly, or None."""
    chosen = {kind for kind in current if kind in PRESET_KINDS}
    return next((level for level in LEVELS if chosen == set(LEVEL_KINDS[level])), None)


def stops(skill: str, before: Iterable[str], after: Iterable[str]) -> bool:
    """Whether a due card of ``skill`` would stop being served when the enabled kinds go from ``before`` to ``after``.

    A card is served while any enabled kind belongs to its ability. Kinds of
    no group (word kinds) never stop here.
    """
    if _GROUP_OF.get(skill) is None:
        return False
    return served(skill, before) and not served(skill, after)


def served(skill: str, kinds: Iterable[str]) -> bool:
    """Whether a due kana card of ``skill`` is served under the enabled ``kinds``: any kind of its ability is on.

    A kind of no group (a word kind) is served only by itself.
    """
    group = _GROUP_OF.get(skill)
    return bool(set(ABILITY_GROUPS[group]) & set(kinds)) if group is not None else skill in set(kinds)


def switched_groups(before: Iterable[str], after: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(groups switched off, groups switched on) among the preset groups, in GROUP_TITLES order.

    Off: some kind of the group was on and none is after. On: some kind of
    the group that was off is on after.
    """
    before, after = set(before), set(after)
    off, on = [], []
    for group in GROUP_TITLES:
        kinds = set(ABILITY_GROUPS[group])
        if kinds & before and not kinds & after:
            off.append(group)
        elif (kinds & after) - before:
            on.append(group)
    return tuple(off), tuple(on)
