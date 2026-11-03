"""Presentation-only Japanese pitch annotations with width-aware mora rows.

Pitch marks never become part of answer strings or keyboard candidates. The
native labels use their measured height so accessibility text sizes can grow.
"""
from __future__ import annotations

from math import isfinite

PITCH_DISPLAY_SETTING = "pitch_display_v1"
_POINT_TO_DP = 96 / 72
_CELL_GAP = 4
# Nested word examples reserve 32 dp screen padding + 24 dp card padding +
# 68 dp detail button + 6 dp separation, plus 14 dp of measurement allowance.
_HORIZONTAL_RESERVE = 144


def clean_pitch_preferences(value):
    """Both notations start enabled; only actual booleans override defaults."""
    settings = value if isinstance(value, dict) else {}
    return {key: settings[key] if isinstance(settings.get(key), bool) else True
            for key in ("contour", "number")}


def pitch_display_variants(record, *, reading=None):
    """Coalesce identical visible patterns while retaining model evidence.

    Sources can repeat the same pronunciation under alternative spellings.
    Their complete metadata stays in the read-only model/source record; the
    display only needs one copy of each reading, accent type and qualifier.
    """
    from .pitch_accent import word_pitch_variants

    variants = word_pitch_variants(record, reading=reading)
    by_reading = {}
    for variant in variants:
        if variant["source_form"]:
            forms = by_reading.setdefault(variant["reading"], {})
            forms.setdefault(variant["source_form"], set()).add(
                (variant["position"], variant.get("qualifier") or ""))
    distinct_forms = {reading for reading, forms in by_reading.items()
                      if len({frozenset(patterns) for patterns in forms.values()}) > 1}
    result, seen = [], set()
    for variant in variants:
        source_form = variant["source_form"] if variant["reading"] in distinct_forms else ""
        key = (variant["reading"], variant["position"], variant.get("qualifier") or "", source_form)
        if key not in seen:
            seen.add(key)
            result.append({**variant, "display_source_form": source_form})
    return tuple(result)


def _available_width(app):
    try:
        width = float(app.main_window.size.width)
    except (AttributeError, TypeError, ValueError):
        width = 360
    if not isfinite(width) or width <= 0:
        width = 360
    return max(1, width - _HORIZONTAL_RESERVE)


def _mora_lines(morae, available, size, scale, *, masked=False):
    """Greedily wrap whole morae, retaining an explicit width for every cell.

    For exceptionally narrow windows a single oversized mora shrinks enough to
    remain intact. Ordinary narrow screens and large fonts simply gain rows.
    Masked cells have identical width, hiding whether a mora includes small kana.
    """
    lines, current, occupied = [], [], 0
    for mora in morae:
        visible = "□" if masked else mora
        characters = max(1, len(visible))
        effective_size = min(size, max(0.5, (available - _CELL_GAP) /
                                      (characters * _POINT_TO_DP * scale)))
        width = min(available, characters * effective_size * _POINT_TO_DP * scale + _CELL_GAP)
        if current and occupied + width > available:
            lines.append(tuple(current))
            current, occupied = [], 0
        current.append((visible, width, effective_size))
        occupied += width
    if current:
        lines.append(tuple(current))
    return tuple(lines)


def _number_caption(variant):
    text = f"[{variant['position']}]"
    qualifier = variant.get("qualifier") or ""
    source_form = variant.get("display_source_form") or ""
    return text + (f"（{qualifier}）" if qualifier else "") + (f"「{source_form}」" if source_form else "")


def _contour_caption(variant):
    source_form = variant.get("display_source_form") or ""
    return " · ".join(value for value in (
        f"「{source_form}」" if source_form else "", variant.get("qualifier") or "") if value)


def build_pitch_block(app, record, *, reading=None, contour=True, number=True,
                      masked=False, size=20, compact=False, show_missing=False,
                      link_kana=False):
    """Build a display-only annotation; missing data is never guessed.

    ``masked`` is used on spelling questions: only the marks are drawn in equal
    width cells, without kana or placeholders that resemble an answer field.
    Numeric-only mode does not repeat the word's reading.
    """
    if not contour and not number:
        return None
    # Import at call time so the app's established native/fake widget factories
    # are shared, without a module-level circular import.
    from .ui_components import label, col, row, GREEN, MUTED
    from .text_layout import font_scale

    variants = pitch_display_variants(record, reading=reading)
    if masked and any(item.get("display_source_form") for item in variants):
        # Source spellings can themselves contain the answer. For a question,
        # use only evidence for its exact word form and never print that form.
        # Unmatched ambiguous forms supply no hint rather than another word's
        # accent. The post-answer detail retains every sourced alternative.
        form = record.get("form") if isinstance(record, dict) else None
        variants = tuple({**item, "display_source_form": ""} for item in variants
                         if form and item["source_form"] == form)
        if not variants:
            return None
    if not variants:
        missing = label("音高资料未收录", size=12, color=MUTED) if show_missing else None
        if missing is not None:
            missing._pitch_has_contour = False
        return missing
    if not contour:
        numeric = label("调号 " + " · ".join(_number_caption(item) for item in variants),
                        size=12 if compact else 14, color=MUTED, margin_bottom=6)
        numeric._pitch_has_contour = False
        return numeric

    try:
        scale = float(font_scale())
    except (TypeError, ValueError):
        scale = 1.0
    if not isfinite(scale) or scale <= 0:
        scale = 1.0
    available = _available_width(app)
    blocks = []
    for variant in variants:
        children = []
        if number:
            children.append(label("调号 " + _number_caption(variant), size=12 if compact else 14,
                                  color=MUTED, margin_bottom=2))
        elif _contour_caption(variant):
            children.append(label(_contour_caption(variant), size=12, color=MUTED, margin_bottom=2))
        offset = 0
        for line in _mora_lines(variant["morae"], available, size, scale, masked=masked):
            cells = []
            for visible, width, cell_size in line:
                mark = variant["levels"][offset]
                offset += 1
                cell_children = [label(mark, size=cell_size, color=GREEN, bold=True, wrap=False,
                    text_align="center", width=width, margin_bottom=0, font_family="monospace")]
                if not masked:
                    kana_label = app.content_label if link_kana else label
                    cell_children.append(kana_label(visible, size=cell_size, text_align="center",
                                                    width=width, margin_bottom=0, text_role="study"))
                cells.append(col(*cell_children, width=width, align_items="center"))
            children.append(row(*cells, margin_bottom=4, align_items="start"))
        blocks.append(col(*children, margin_bottom=4 if compact else 8))
    result = col(*blocks, margin_bottom=4)
    result._pitch_has_contour = True
    return result


def native_pitch_preview(platform, message, record, preferences):
    """Build an unlinked, vertically scrolling native index preview body.

    The caller owns the AlertDialog lifecycle. Natural text heights and a
    conservatively sized line width keep long readings within the dialog at
    large system font sizes; the dialog's buttons stay outside the scroll area.
    """
    from .typography import configure_native_text

    c = platform._jclass
    activity = c("org.beeware.android.MainActivity").singletonThis
    resources = activity.getResources()
    metrics = resources.getDisplayMetrics()
    density = max(0.1, float(metrics.density))
    scale = max(0.1, float(resources.getConfiguration().fontScale))
    # Dialogs stop growing on wide/landscape screens even when the underlying
    # activity is much wider; do not plan a tablet-wide native text row.
    available = max(1, min(420, float(metrics.widthPixels) / density - 140))
    linear = c("android.widget.LinearLayout")
    params = c("android.widget.LinearLayout$LayoutParams")
    text_view = c("android.widget.TextView")
    scroll = c("android.widget.ScrollView")(activity)
    scroll.setFillViewport(False)
    body = linear(activity)
    body.setOrientation(1)
    pad = round(16 * density)
    body.setPadding(pad, round(8 * density), pad, pad)
    scroll.addView(body)

    def add_text(parent, text, size=16, width=-1, centered=False, green=False,
                 text_role="ui", font_family=None):
        widget = text_view(activity)
        configure_native_text(widget, text_role=text_role, font_family=font_family)
        widget.setText(str(text))
        widget.setTextSize(float(size))
        widget.setTextColor((0xFF185C50 if green else 0xFF1E3430) - 2**32)
        widget.setSingleLine(False)
        widget.setHorizontallyScrolling(False)
        widget.setMaxLines(2147483647)
        widget.setEllipsize(None)
        widget.setAutoLinkMask(0)
        widget.setLinksClickable(False)
        widget.setMovementMethod(None)
        if centered:
            widget.setGravity(17)
        parent.addView(widget, params(round(width * density) if width >= 0 else width, -2))
        return widget

    add_text(body, message)
    settings = clean_pitch_preferences(preferences)
    if not any(settings.values()):
        return scroll
    variants = pitch_display_variants(record)
    if not variants:
        add_text(body, "音高资料未收录", size=13)
        return scroll
    if not settings["contour"]:
        add_text(body, "调号 " + " · ".join(_number_caption(item) for item in variants), size=14)
        return scroll

    for variant in variants:
        if settings["number"]:
            add_text(body, "调号 " + _number_caption(variant), size=14)
        elif _contour_caption(variant):
            add_text(body, _contour_caption(variant), size=13)
        offset = 0
        # Native TextView font sizes are sp; the shared row planner uses points.
        for line in _mora_lines(variant["morae"], available, 20 / _POINT_TO_DP, scale):
            native_row = linear(activity)
            native_row.setOrientation(0)
            body.addView(native_row, params(-1, -2))
            for visible, width, cell_size in line:
                cell = linear(activity)
                cell.setOrientation(1)
                native_row.addView(cell, params(round(width * density), -2))
                add_text(cell, variant["levels"][offset], size=cell_size * _POINT_TO_DP,
                         centered=True, green=True, font_family="monospace")
                add_text(cell, visible, size=cell_size * _POINT_TO_DP, centered=True,
                         text_role="study")
                offset += 1
    return scroll
