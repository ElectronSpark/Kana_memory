"""The ink pad on the app side (design 13).

The strokes written so far and the size of the pad they were written on are one InkPad on the app, kept as
the attribute ``_ink_pad``. The handwriting pages reach it through this module; a problem report
(feedback_context) names the attribute itself. It is replaced, never edited, when a field of it is assigned
(assign, set_pad and the set_* functions); the list of strokes inside is the pad's own list. An ink page kept
for Back holds the pad of its moment (keep) under the attribute's name, and Back's restore (page_restore),
which names no attribute, gives the fields of such a kept object back under the name it was kept by.

A field that was never set since the app started is UNSET: the readers below raise AttributeError for it, as
the attribute access they replace did; pad(app).get(name, default) gives a default, as getattr with a default
did, and pad(app).has(name) says whether it was ever set, as hasattr did.

This module reads the app object and imports neither Toga nor the control components nor the app module.
"""
from __future__ import annotations

from dataclasses import replace

from .kana_line.ink import FIELDS, InkPad

BINDING = "_ink_pad"


def pad(app) -> InkPad:
    """The pad the app holds: one with nothing set before the app's start gave it strokes, and whenever the
    attribute holds something that is no pad."""
    value = getattr(app, BINDING, None)
    return value if isinstance(value, InkPad) else InkPad()


def set_pad(app, value: InkPad) -> None:
    app._ink_pad = value


def assign(app, **fields) -> None:
    """Assign fields of the pad: the app gets a NEW pad object with them (the one before is left as it was,
    for whatever page still holds it)."""
    app._ink_pad = replace(pad(app), **fields)


def keep(app, bindings: dict, *, ink_page: bool) -> None:
    """Put into ``bindings`` what the page being kept for Back holds of the pad: an ink page all of it, as far
    as it was set; any other page, and an ink page of an app that holds nothing of the pad, nothing."""
    if not ink_page:
        return
    kept = pad(app).only(FIELDS)
    if kept.set_fields():
        bindings[BINDING] = kept


def _read(app, name: str):
    return pad(app).need(name)


def strokes(app):
    """The strokes written so far; the pad's own list."""
    return _read(app, "strokes")


def set_strokes(app, value) -> None:
    assign(app, strokes=value)


def ink_width(app):
    """The width of the pad in its own units."""
    return _read(app, "ink_width")


def set_ink_width(app, value) -> None:
    assign(app, ink_width=value)


def ink_height(app):
    """The height of the pad in its own units."""
    return _read(app, "ink_height")


def set_ink_height(app, value) -> None:
    assign(app, ink_height=value)
