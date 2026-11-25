"""Shapes of the three arrangement settings: strict validation, defaults, conversion.

Pure: no storage access. A missing orientation means THOROUGH (the rule the
app had before), so existing data and test stores keep their behaviour; a
missing focus means no focus; a missing review filter means 只做范围内 off and
nothing paused (design 12 H8, H9). The app never writes any of them on its own.
"""
from __future__ import annotations

from .model import Focus, FocusScope, KanaTilt, Orientation, ReviewFilterSetting, SCOPE_KINDS, Tilt

ORIENTATION_KEY = "study_orientation_v1"
FOCUS_KEY = "study_focus_v1"
REVIEW_FILTER_KEY = "study_review_filter_v1"
MAX_EXCLUDED = 20   # scopes 暂不复习 can hold (design 12 H9)
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
        scope = _scope_value(scope, "Invalid focus scope")
    return {"version": VERSION, "tilt": value["tilt"], "kana": value["kana"], "scope": scope}


def _scope_value(scope, message: str) -> dict:
    """A clean {kind, id} of a stored scope (the focus scope's kinds and id limits) or ValueError(message)."""
    if (not isinstance(scope, dict) or set(scope) != {"kind", "id"} or scope["kind"] not in SCOPE_KINDS
            or not isinstance(scope["id"], str) or not 1 <= len(scope["id"]) <= 1024):
        raise ValueError(message)
    return {"kind": scope["kind"], "id": scope["id"]}


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


def validate_review_filter(value) -> dict:
    """Return a clean copy of a stored review filter (design 12 H8, H9) or raise ValueError.

    {"version": 1, "only": bool, "excluded": [{"kind", "id"}, ...]}: at most
    MAX_EXCLUDED scopes of the focus scope's kinds, no scope twice.
    """
    _version(value, {"version", "only", "excluded"}, "study review filter")
    if type(value["only"]) is not bool:
        raise ValueError("Invalid review filter switch")
    excluded = value["excluded"]
    if not isinstance(excluded, list) or len(excluded) > MAX_EXCLUDED:
        raise ValueError("Invalid paused scopes")
    clean, seen = [], set()
    for scope in excluded:
        item = _scope_value(scope, "Invalid paused scope")
        identity = (item["kind"], item["id"])
        if identity in seen:
            raise ValueError("A paused scope is listed twice")
        seen.add(identity)
        clean.append(item)
    return {"version": VERSION, "only": value["only"], "excluded": clean}


def review_filter_of(value) -> ReviewFilterSetting:
    """The review filter a stored value stands for; nothing stored means off and nothing paused."""
    if value is None:
        return ReviewFilterSetting()
    clean = validate_review_filter(value)
    return ReviewFilterSetting(clean["only"], tuple(FocusScope(item["kind"], item["id"]) for item in clean["excluded"]))


def review_filter_value(setting: ReviewFilterSetting) -> dict:
    return {"version": VERSION, "only": bool(setting.only),
            "excluded": [{"kind": scope.kind, "id": scope.id} for scope in setting.excluded]}
