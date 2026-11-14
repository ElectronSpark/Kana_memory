"""Shapes of the two arrangement settings: strict validation, defaults, conversion.

Pure: no storage access. A missing orientation means THOROUGH (the rule the
app had before), so existing data and test stores keep their behaviour; a
missing focus means no focus. The app never writes either key on its own.
"""
from __future__ import annotations

from .model import Focus, FocusScope, KanaTilt, Orientation, SCOPE_KINDS, Tilt

ORIENTATION_KEY = "study_orientation_v1"
FOCUS_KEY = "study_focus_v1"
VERSION = 1


def _version(value, fields: set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"Invalid {name}")
    if type(value["version"]) is not int or value["version"] != VERSION:
        raise ValueError(f"Unsupported {name} version")


def validate_orientation(value) -> dict:
    """Return a clean copy of a stored orientation or raise ValueError."""
    _version(value, {"version", "mode"}, "study orientation")
    if value["mode"] not in {mode.value for mode in Orientation}:
        raise ValueError("Unknown study orientation")
    return {"version": VERSION, "mode": value["mode"]}


def orientation_of(value) -> Orientation:
    """The orientation a stored value stands for; nothing stored means THOROUGH."""
    if value is None:
        return Orientation.THOROUGH
    return Orientation(validate_orientation(value)["mode"])


def orientation_value(mode: Orientation) -> dict:
    return {"version": VERSION, "mode": Orientation(mode).value}


def validate_focus(value) -> dict:
    """Return a clean copy of a stored focus or raise ValueError."""
    _version(value, {"version", "tilt", "kana", "scope"}, "study focus")
    if value["tilt"] not in {tilt.value for tilt in Tilt}:
        raise ValueError("Unknown focus tilt")
    if value["kana"] not in {tilt.value for tilt in KanaTilt}:
        raise ValueError("Unknown kana tilt")
    scope = value["scope"]
    if scope is not None:
        if (not isinstance(scope, dict) or set(scope) != {"kind", "id"} or scope["kind"] not in SCOPE_KINDS
                or not isinstance(scope["id"], str) or not 1 <= len(scope["id"]) <= 1024):
            raise ValueError("Invalid focus scope")
        scope = {"kind": scope["kind"], "id": scope["id"]}
    return {"version": VERSION, "tilt": value["tilt"], "kana": value["kana"], "scope": scope}


def focus_of(value) -> Focus:
    """The focus a stored value stands for; nothing stored means no focus."""
    if value is None:
        return Focus()
    clean = validate_focus(value)
    scope = clean["scope"]
    return Focus(Tilt(clean["tilt"]), KanaTilt(clean["kana"]),
                 FocusScope(scope["kind"], scope["id"]) if scope else None)


def focus_value(focus: Focus) -> dict:
    scope = focus.scope
    return {"version": VERSION, "tilt": Tilt(focus.tilt).value, "kana": KanaTilt(focus.kana).value,
            "scope": {"kind": scope.kind, "id": scope.id} if scope else None}
