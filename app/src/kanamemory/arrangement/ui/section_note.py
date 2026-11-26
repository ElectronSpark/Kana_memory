"""B10 "已加入的内容都学过了" (design 12 B10, UI3): the note rows and the once-per-launch prompt; pages under design 13 C4.

Input: display.SectionNote and callbacks; output: widgets. Nothing is added
here: the callbacks decide (on_add_next(section_id) / on_add_and_start() go
through the existing "add sections" path, on_review_first() continues as usual).
"""
from __future__ import annotations

from collections.abc import Callable

import kanamemory.ui_components as u
from ..display import SectionNote


def section_note_rows(note: SectionNote, *, on_add_next: Callable[[str], object]) -> list:
    """The note and the button that adds the route's next section, to stand first on the home and end pages;
    with the two-line design's mark (建议先加 · <line>) and its reason between them when the note has them."""
    rows = [u.label(note.text, size=15, bold=True, margin_bottom=4)]
    if note.mark:
        rows.append(u.label(note.mark, size=13, bold=True, color=u.GREEN, margin_bottom=2))
    if note.reason:
        rows.append(u.label(note.reason, size=12, color=u.MUTED, margin_bottom=2))
    rows.append(u.button(note.add_next, lambda widget: on_add_next(note.section_id), multiline=True))
    return rows


def section_prompt(note: SectionNote, *, on_add_and_start: Callable[[], object],
                   on_review_first: Callable[[], object]) -> list:
    """The prompt before the first "继续学习" of a launch: add the next section and start, or review first."""
    return [u.label(note.text, size=17),
            u.label(note.title, size=15, bold=True, color=u.GREEN),
            u.button(note.add_and_start, lambda widget: on_add_and_start(), primary=True, multiline=True),
            u.button(note.review_first, lambda widget: on_review_first())]
