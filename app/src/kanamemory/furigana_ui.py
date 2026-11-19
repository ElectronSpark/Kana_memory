"""Word cells with their kana above: the one renderer of a question prompt and of a result's answer.

A Toga-level component like ui_components: it lays out and draws cells that it is given and reads
no data (no store, page, content or question_support). question_support draws a prompt's reading
plan with it; answer_result_ui.answer_furigana_block draws a result's answer (owner 2026-11-18:
the standard spelling with kana above each kanji word); the practice deck page draws the deck's
answer cells. Plain Toga labels, both backends; all sizes grow with the system font setting.
"""
from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
import re
import unicodedata

_PLAIN_PROMPT_UNIT = re.compile(r"（(?:[^\S\r\n]*|[①-⑥1-6])）|［[1-6]］|\([^\S\r\n]*\)|\[[^\S\r\n]*\]|【[^\S\r\n]*】|[\s\S]")
_KANA = re.compile(r"[ぁ-ゖァ-ヶー]+\Z")
_POINT_TO_DP = 96 / 72


def accent_caption(number):
    if type(number) is not int or number < 0:
        return ""
    return "⓪" if number == 0 else chr(0x2460 + number - 1) if number <= 20 else f"[{number}]"


def _annotation_text(span):
    """Format an already approved display plan, retaining accent boundaries."""
    phrases = span.get("accent_phrases")
    if isinstance(phrases, (list, tuple)) and 2 <= len(phrases) <= 16:
        if all(isinstance(p, Mapping) and type(p.get("accent_number")) is int
               and p["accent_number"] >= 0
               and (p.get("reading") is None or isinstance(p["reading"], str)
                    and _KANA.fullmatch(p["reading"])) for p in phrases):
            return "｜".join((p.get("reading") or "") + accent_caption(p["accent_number"]) for p in phrases)
    number = span.get("accent_number")
    # A lesson word with several accents (accent_options) shows them all, as its reading line does.
    caption = "／".join(map(accent_caption, number)) if isinstance(number, tuple) else accent_caption(number)
    return (span.get("reading") or "") + caption


def _text_width(text, size, scale):
    # Circled accent digits are East Asian Ambiguous, but Android's Japanese
    # fallback font renders them at a full cell. Treating them as Latin-width
    # caused only the number to wrap underneath an otherwise fitting reading.
    units = sum(1 if c == "⓪" or unicodedata.east_asian_width(c) in "WFA" else .6 for c in text)
    return max(1, units * size * _POINT_TO_DP * scale)


def _prompt_lines(segments, available, size, scale):
    """Wrap complete words and blank markers; other plain text may wrap by character."""
    available = max(1, available)
    annotation_size = max(11, size * .55)
    cells = []
    for span in segments:
        annotation = _annotation_text(span)
        # Keep a visible answer slot intact at a row boundary. Splitting its
        # brackets from the blank makes enlarged cloze questions ambiguous.
        surfaces = (span["surface"],) if annotation else _PLAIN_PROMPT_UNIT.findall(span["surface"])
        for surface in surfaces:
            # Native font fallback/hinting rounds per glyph, so reserve more
            # than one pixel at enlarged system sizes. This is internal text
            # allowance, not reduced type size or fixed clipping height.
            allowance = max(4, 4 * scale) if annotation else 2
            width = min(available, max(_text_width(surface, size, scale),
                                      _text_width(annotation, annotation_size, scale) if annotation else 0) + allowance)
            cells.append({"surface": surface, "annotation": annotation, "width": width,
                          "annotation_size": annotation_size})
    lines, current, occupied = [], [], 0
    for cell in cells:
        if cell["surface"] == "\n":
            if current:
                lines.append(current)
                current, occupied = [], 0
            continue
        if current and occupied + cell["width"] > available:
            if cell["surface"] in "。、，．！？!?」』）】" and len(current) > 1 and current[-1]["width"] + cell["width"] <= available:
                # Keep closing punctuation with its preceding cell instead of
                # creating a line containing only a full stop.
                previous = current.pop()
                lines.append(current)
                current, occupied = [previous], previous["width"]
            else:
                lines.append(current)
                current, occupied = [], 0
        current.append(cell)
        occupied += cell["width"]
    if current:
        lines.append(current)
    return lines


def furigana_block(app, segments, *, size=20, available_width=None, text_role="study", margin_bottom=8,
                   bold=False, **style):
    """(widget, lines, available width, font scale): word cells with their kana above, wrapped natively.

    segments: [{"surface", "reading"?, "accent_number"?}]. The one cell renderer of the question prompt
    and of a result's answer; plain Toga labels, both backends. A width that cannot be read falls back
    to a phone's. bold: the surfaces are bold (an emphasised answer); the kana above stay regular.
    """
    from .ui_components import label, col, row, INK, MUTED
    from .text_layout import font_scale

    try:
        scale = float(font_scale())
    except (TypeError, ValueError):
        scale = 1
    if not isfinite(scale) or scale <= 0:
        scale = 1
    if available_width is None:
        try:
            available_width = float(app.main_window.size.width) - 32
        except (AttributeError, TypeError, ValueError):
            available_width = 328
    if not isfinite(available_width) or available_width < 1:
        available_width = 328
    lines = _prompt_lines(segments, available_width, size, scale)
    rows = []
    for line in lines:
        cells = []
        for cell in line:
            children = []
            if cell["annotation"]:
                children.append(label(cell["annotation"], size=cell["annotation_size"], color=MUTED,
                                      text_align="center", width=cell["width"], margin_bottom=0,
                                      text_role="study"))
            children.append(label(cell["surface"], size=size, color=INK, bold=bold, text_align="center",
                                  width=cell["width"], margin_bottom=0, text_role=text_role))
            cells.append(col(*children, width=cell["width"]))
        rows.append(row(*cells, align_items="end", margin_bottom=6))
    return col(*rows, margin_bottom=margin_bottom, **style), lines, available_width, scale


def cell_segment(cell):
    """The furigana_block segment of a cell: (surface, kana) or (surface, kana, pitch).

    pitch (answer_furigana.pitched_cells): accent numbers (one, or a lesson's several), or (kana, number)
    phrases of a sentence annotation's phrase sequence; drawn after the kana above the surface, as the
    example sentences draw it.
    """
    segment = {"surface": str(cell[0]), "reading": str(cell[1] or "") or None}
    pitch = tuple(cell[2]) if len(cell) > 2 and cell[2] else ()
    if pitch and all(type(number) is int for number in pitch):
        segment["accent_number"] = pitch[0] if len(pitch) == 1 else pitch
    elif pitch:
        segment["accent_phrases"] = [{"reading": reading, "accent_number": number} for reading, number in pitch]
    return segment


def cells_widget(app, cells, *, size=20, text_role="study", bold=False, available_width=None, **style):
    """[(surface, kana or "")] or [(surface, kana, pitch)] drawn as furigana_block; without any kana or
    pitch, one label as before.

    widget._answer_cells keeps the cells (for checks).
    """
    from .ui_components import label

    cells = tuple((str(cell[0]), str(cell[1] or ""), *([tuple(cell[2])] if len(cell) > 2 and cell[2] else []))
                  for cell in cells)
    if not any(cell[1] or len(cell) > 2 for cell in cells):
        widget = label("".join(cell[0] for cell in cells), size=size, text_role=text_role, bold=bold,
                       **style)
    else:
        margin_bottom = style.pop("margin_bottom", 8)
        widget, _, _, _ = furigana_block(app, [cell_segment(cell) for cell in cells],
                                         size=size, available_width=available_width, text_role=text_role,
                                         margin_bottom=margin_bottom, bold=bold, **style)
    widget._answer_cells = cells
    return widget
