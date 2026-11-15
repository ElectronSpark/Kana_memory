"""The ink pad: the strokes written so far and the pad they were written on (design 13).

The handwriting question of the kana practice and the free writing page share one pad. A stroke is a list of
points ``(x, y, time in milliseconds)`` in the pad's own units: whatever size the canvas has on the screen, the
strokes keep these coordinates, and recognition and the saved checkpoint take them with the pad's size.

Pure: no Toga, no store, no app object.
"""
from __future__ import annotations

from dataclasses import dataclass

from .fields import UNSET, PartlySet


@dataclass(eq=False)
class InkPad(PartlySet):
    """The strokes and the size of the pad.

    The app side keeps one of these and REPLACES it whenever a field is assigned; it never edits one in
    place. The list of strokes inside is the pad's own list and is changed in place where it always was (a
    stroke is appended while it is written, the last one is taken away by undo). An ink page kept for Back
    holds all three fields, as far as they were set.
    """

    strokes: object = UNSET      # list: the strokes in the order they were written
    ink_width: object = UNSET    # number: the width of the pad in its own units
    ink_height: object = UNSET   # number: the height of the pad in its own units


FIELDS = ("strokes", "ink_width", "ink_height")
