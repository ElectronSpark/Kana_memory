"""ArrangementPreferences: reading and writing the learner's orientation, focus (with the line tilt, LS4) and
review filter (design 12 B1, H, 10).

Pure application layer: the store is any object with the existing public
``get_setting(key, default)`` / ``set_setting(key, value)`` methods; no SQL.
A missing key means THOROUGH, no focus, and no review filter (H8, H9). A stored value of the wrong shape
raises ValueError on read, like the comparable settings (new_dispatch_v1,
explicit_learning_start_v1), instead of being silently replaced: only a
direct database edit can store one, because writes and restores validate.
Only an explicit learner action calls the setters; nothing here writes on read.
The kana depth preset (D1) is a collaborator, ``depth`` (services.kana_depth),
reached through kana_depth() / apply_kana_depth(level).
"""
from __future__ import annotations

from ..model import Focus, LineTilt, Orientation, ReviewFilterSetting
from ..rules.depth import KanaDepth
from ..settings import (FOCUS_KEY, LINES_KEY, ORIENTATION_KEY, REVIEW_FILTER_KEY, focus_of, focus_value, lines_of,
                        lines_value, orientation_of, orientation_value, review_filter_of, review_filter_value,
                        validate_focus, validate_lines, validate_orientation, validate_review_filter)
from .kana_depth import KanaDepthSetting

_MISSING = object()

# The four keys and their strict validators: the backup validation chain and the
# feedback settings snapshot use these (design 12 section 10 (1), (5), (7), (8)).
_VALIDATORS = {ORIENTATION_KEY: validate_orientation, FOCUS_KEY: validate_focus,
               REVIEW_FILTER_KEY: validate_review_filter, LINES_KEY: validate_lines}
KEYS = tuple(_VALIDATORS)


def _with_lines(focus: Focus, lines: LineTilt) -> Focus:
    return Focus(focus.tilt, focus.kana, focus.scope, lines)


def validate_stored(key: str, value) -> dict:
    """Strictly validate a stored value of one of the four keys; raise ValueError otherwise."""
    if key not in _VALIDATORS:
        raise ValueError("Not a study arrangement setting")
    return _VALIDATORS[key](value)


class ArrangementPreferences:
    """The orientation, focus and review filter of one learner store."""

    def __init__(self, store):
        self._store = store
        self.depth = KanaDepthSetting(store)   # the kana depth preset of this store (D1, D2)

    def _read(self, key: str):
        """The raw stored value, None when the key is missing; orientation_of/focus_of validate it.

        A stored JSON null is not "missing": it raises like any other bad shape.
        """
        value = self._store.get_setting(key, _MISSING)
        if value is None:
            raise ValueError("Invalid stored study arrangement setting")
        return None if value is _MISSING else value

    def orientation(self) -> Orientation:
        return orientation_of(self._read(ORIENTATION_KEY))

    def chosen_orientation(self) -> Orientation | None:
        """The stored orientation, None when the learner never chose one (one settings read)."""
        value = self._read(ORIENTATION_KEY)
        return None if value is None else orientation_of(value)

    def set_orientation(self, mode) -> Orientation:
        """Store the learner's choice; an unknown mode raises ValueError and writes nothing."""
        value = validate_orientation(orientation_value(Orientation(mode)))
        self._store.set_setting(ORIENTATION_KEY, value)
        return Orientation(value["mode"])

    def focus(self) -> Focus:
        """The stored focus with the stored line tilt (two settings reads: study_focus_v1 and study_lines_v1)."""
        return _with_lines(focus_of(self._read(FOCUS_KEY)), self.lines())

    def lines(self) -> LineTilt:
        """The line tilt between words and grammar (design 12 LS4); 正常 when the key is missing (one read)."""
        return lines_of(self._read(LINES_KEY))

    def set_focus(self, focus: Focus) -> Focus:
        """Store the learner's focus; anything but a valid Focus raises ValueError and writes nothing.

        The line tilt goes to its own key (design 12 LS4), written only when it differs from the stored
        one, so a learner who never touches it gets no key; study_focus_v1 keeps its shape and is
        written as before, except when only the line tilt changed (its bytes stay as they are).
        """
        if not isinstance(focus, Focus):
            raise ValueError("A Focus is required")
        value = validate_focus(focus_value(focus))
        line_value = validate_lines(lines_value(focus.lines))
        stored_lines = self.lines()
        lines_changed = LineTilt(focus.lines) is not stored_lines
        if not lines_changed or focus_of(self._read(FOCUS_KEY)) != _with_lines(focus, LineTilt.EVEN):
            self._store.set_setting(FOCUS_KEY, value)
        if lines_changed:
            self._store.set_setting(LINES_KEY, line_value)
        return _with_lines(focus_of(value), LineTilt(line_value["tilt"]))

    def review_filter(self) -> ReviewFilterSetting:
        """只做范围内 and 暂不复习 (design 12 H8, H9); off and nothing paused when the key is missing."""
        return review_filter_of(self._read(REVIEW_FILTER_KEY))

    def set_review_filter(self, setting: ReviewFilterSetting) -> ReviewFilterSetting:
        """Store the learner's switches; anything but a valid ReviewFilterSetting raises ValueError and writes nothing."""
        if not isinstance(setting, ReviewFilterSetting):
            raise ValueError("A ReviewFilterSetting is required")
        value = validate_review_filter(review_filter_value(setting))
        self._store.set_setting(REVIEW_FILTER_KEY, value)
        return review_filter_of(value)

    def kana_depth(self) -> KanaDepth | None:
        """The level the stored mixed kind switches match, None when they match no preset (D1)."""
        return self.depth.level()

    def apply_kana_depth(self, level) -> dict:
        """Apply a kana depth preset (D1); returns the new cleaned practice preferences for the App to adopt.

        See KanaDepthSetting.apply. Only an explicit learner confirmation calls it.
        """
        return self.depth.apply(level)
