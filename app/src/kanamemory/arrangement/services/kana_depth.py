"""KanaDepthSetting: the kana depth preset read from and applied to one learner store (design 12 D1, D2).

Application layer without SQL. The stored practice preferences are read and
cleaned the way the App reads them at start (practice_preferences.
clean_preferences), so a missing or old-plan value is read as the App would
use it. Only apply() writes, through the one save path the practice-setup
page uses (practice_kind_policy.save_kind_switches). The App keeps its own
in-memory copy of the preferences; apply() returns the new cleaned
preferences for the caller to adopt.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from ...practice_kind_policy import PREFERENCES_KEY, save_kind_switches
from ...practice_preferences import (ALL_MIXED_KINDS, MIXED_PRACTICE_KINDS, PRACTICE_KINDS, clean_preferences,
                                     enabled_preference_kinds)
from ..rules.depth import LEVELS, KanaDepth, level_of, mixed_kinds_for, stops


class KanaDepthSetting:
    """The preset over the mixed-practice kind switches of one store."""

    def __init__(self, store):
        self._store = store

    def preferences(self) -> dict:
        """The stored practice preferences, cleaned as the App cleans them at start (nothing written)."""
        return clean_preferences(self._store.get_setting(PREFERENCES_KEY, {}))

    def level(self, preferences: Mapping | None = None) -> KanaDepth | None:
        """The level the current mixed switches match, None when they match no preset."""
        preferences = self.preferences() if preferences is None else preferences
        return level_of(preferences["mixed_kinds"])

    def preview(self, level: KanaDepth, preferences: Mapping | None = None) -> dict:
        """The cleaned preferences ``level`` would store; nothing is written."""
        preferences = self.preferences() if preferences is None else dict(preferences)
        kinds = mixed_kinds_for(KanaDepth(level), preferences["mixed_kinds"], ALL_MIXED_KINDS)
        return clean_preferences({**preferences, "mixed_kinds": list(kinds)})

    @staticmethod
    def kinds_in_force(preferences: Mapping) -> tuple[str, ...]:
        """The question kinds these preferences let the practice plan use (enabled_preference_kinds).

        A mixed plan uses its enabled mixed switches; a single-kind plan uses
        its one kind whatever the switches say.
        """
        return tuple(enabled_preference_kinds(preferences))

    @staticmethod
    def single_kind(preferences: Mapping) -> bool:
        """Whether the practice plan is one question kind rather than a mixed practice."""
        return PRACTICE_KINDS[clean_preferences(preferences)["kind"]] not in MIXED_PRACTICE_KINDS

    def stopping_due(self, now: datetime, preferences: Mapping | None = None) -> dict[KanaDepth, int]:
        """For each level, the kana cards due at ``now`` that it would stop serving (design 12 D2).

        A card stops when an enabled kind of its ability is on now and none is
        on after the level (enabled_preference_kinds before and after, so a
        single-kind plan stops nothing). Due cards are StudyStore.get_due(now),
        the due list of the records and home counts. Nothing is written; due
        times are never changed by the preset.
        """
        preferences = self.preferences() if preferences is None else preferences
        before = enabled_preference_kinds(preferences)
        skills = [card["skill"] for card in self._store.get_due(now=now)]
        result = {}
        for level in LEVELS:
            after = enabled_preference_kinds(self.preview(level, preferences))
            result[level] = sum(stops(skill, before, after) for skill in skills)
        return result

    def apply(self, level: KanaDepth) -> dict:
        """Rewrite the mixed kind switches for ``level``, save, and update the pending main queue's kind policy.

        The same writes as switching a kind on the practice-setup page: the
        cleaned preferences under practice_preferences, then, when the main
        lane has a pending session, practice_queue_kinds for its enabled kinds
        (recorded against the new preferences). Idempotent. Returns the new
        cleaned preferences: the App must adopt them as _practice_preferences.
        An unknown level raises ValueError and writes nothing.
        """
        level = KanaDepth(level)
        preferences = self.preview(level)
        save_kind_switches(self._store, preferences, regular_preferences=preferences, lane="main")
        return preferences
