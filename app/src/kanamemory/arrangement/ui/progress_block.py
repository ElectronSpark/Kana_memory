"""The "时间与进度" block of the records page (design 12 UI4): a read-only page part under design 13 C4.

Input: display.ProgressModel; output: widgets. Layout only: every text,
headings and lines included, comes composed in the model (the presenter
decides the wording). Plain numbers in the ordinary text colours (no warning
colour) and the model's explanatory sentence.
"""
from __future__ import annotations

import kanamemory.ui_components as u
from ..display import ProgressModel


def progress_block(model: ProgressModel) -> list:
    """The route position, what was newly learned, the minutes of each day and the review figures."""
    rows = [u.label(model.heading, size=17, bold=True), u.label(model.route_line), u.label(model.learned_line),
            u.label(model.days_heading, bold=True)]
    rows += [u.label(day.line, size=13) for day in model.days]
    if model.lines_line:
        rows.append(u.label(model.lines_line, size=13))
    if model.units_note:
        rows.append(u.label(model.units_note, size=12, color=u.MUTED))
    rows.append(u.label(model.reviews_heading, bold=True))
    rows += [u.label(figures.line, size=13) for figures in model.reviews]
    rows.append(u.label(model.note, size=12, color=u.MUTED))
    return rows
