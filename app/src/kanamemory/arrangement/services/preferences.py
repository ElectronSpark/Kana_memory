"""ArrangementPreferences: reading and writing the learner's orientation and focus (design 12 B1, H, 10).

Pure application layer: the store is any object with the existing public
``get_setting(key, default)`` / ``set_setting(key, value)`` methods; no SQL.
A missing key means THOROUGH and no focus. A stored value of the wrong shape
raises ValueError on read, like the comparable settings (new_dispatch_v1,
explicit_learning_start_v1), instead of being silently replaced: only a
direct database edit can store one, because writes and restores validate.
Only an explicit learner action calls the setters; nothing here writes on read.
The kana depth preset (D1) is a collaborator, ``depth`` (services.kana_depth),
reached through kana_depth() / apply_kana_depth(level).
"""
from __future__ import annotations

from ..model import Focus, Orientation
from ..rules.depth import KanaDepth
from ..settings import (FOCUS_KEY, ORIENTATION_KEY, focus_of, focus_value, orientation_of, orientation_value,
                        validate_focus, validate_orientation)
from .kana_depth import KanaDepthSetting

_MISSING = object()

# The two keys and their strict validators: the backup validation chain and the
# feedback settings snapshot use these (design 12 section 10 (1), (5)).
_VALIDATORS = {ORIENTATION_KEY: validate_orientation, FOCUS_KEY: validate_focus}
KEYS = tuple(_VALIDATORS)


def validate_stored(key: str, value) -> dict:
    """Strictly validate a stored value of one of the two keys; raise ValueError otherwise."""
    if key not in _VALIDATORS:
        raise ValueError("Not a study arrangement setting")
    return _VALIDATORS[key](value)


class ArrangementPreferences:
    """The orientation and focus of one learner store."""

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
        return focus_of(self._read(FOCUS_KEY))

    def set_focus(self, focus: Focus) -> Focus:
        """Store the learner's focus; anything but a valid Focus raises ValueError and writes nothing."""
        if not isinstance(focus, Focus):
            raise ValueError("A Focus is required")
        value = validate_focus(focus_value(focus))
        self._store.set_setting(FOCUS_KEY, value)
        return focus_of(value)

    def kana_depth(self) -> KanaDepth | None:
        """The level the stored mixed kind switches match, None when they match no preset (D1)."""
        return self.depth.level()

    def apply_kana_depth(self, level) -> dict:
        """Apply a kana depth preset (D1); returns the new cleaned practice preferences for the App to adopt.

        See KanaDepthSetting.apply. Only an explicit learner confirmation calls it.
        """
        return self.depth.apply(level)
