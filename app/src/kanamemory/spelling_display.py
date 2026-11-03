"""Compact character-level spelling comparison; never supplies learning evidence."""
from __future__ import annotations

from itertools import zip_longest
from math import isfinite
from unicodedata import normalize

ERROR_RED = "#B3261E"
MAX_DISPLAY_CHARACTERS = 128


def spelling_pairs(expected, actual):
    """Return a deterministic minimum-edit alignment of visible characters.

    The memory model aligns catalog units and conservatively handles ambiguity.
    This independent display aligns characters, so only ょ is red in きょう /
    きよう. Empty sides preserve missing/extra positions without fake glyphs.
    """
    expected, actual = (normalize("NFC", value).strip() for value in (expected, actual))
    n, m = len(expected), len(actual)
    if max(n, m) > 128:
        # Keep malformed/very long input linear and bounded in working memory.
        # Such answers already carry the model's limited-evidence notice.
        return tuple(zip_longest(expected, actual, fillvalue=""))
    costs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n, -1, -1):
        for j in range(m, -1, -1):
            if i == n:
                costs[i][j] = m - j
            elif j == m:
                costs[i][j] = n - i
            else:
                costs[i][j] = min((expected[i] != actual[j]) + costs[i + 1][j + 1],
                                  1 + costs[i + 1][j], 1 + costs[i][j + 1])
                if (i + 1 < n and j + 1 < m and expected[i] == actual[j + 1]
                        and expected[i + 1] == actual[j]):
                    costs[i][j] = min(costs[i][j], 1 + costs[i + 2][j + 2])
    result, i, j = [], 0, 0
    while i < n or j < m:
        if i < n and j < m and costs[i][j] == (expected[i] != actual[j]) + costs[i + 1][j + 1]:
            result.append((expected[i], actual[j]))
            i, j = i + 1, j + 1
        elif (i + 1 < n and j + 1 < m and expected[i] == actual[j + 1]
              and expected[i + 1] == actual[j] and costs[i][j] == 1 + costs[i + 2][j + 2]):
            result.extend(((expected[i], actual[j]), (expected[i + 1], actual[j + 1])))
            i, j = i + 2, j + 2
        elif i < n and costs[i][j] == 1 + costs[i + 1][j]:
            result.append((expected[i], ""))
            i += 1
        else:
            result.append(("", actual[j]))
            j += 1
    return tuple(result)


def build_spelling_comparison(app, expected, actual, *, correct=False, assisted=False,
                              text_role="study", size=18, reserved_width=0):
    """Show one canonical answer, adding paired input only when it is useful.

    Accepted alternatives still display the canonical spelling once. A reveal
    without a distinct attempt has no second row; the feedback signal already
    communicates assistance. Neither display normalization nor truncation
    changes the original answer or the evidence used by the scheduler.
    """
    from .ui_components import col, row, label, INK, MUTED
    from .text_layout import font_scale

    try:
        available = float(app.main_window.size.width) - 48
    except (AttributeError, TypeError, ValueError):
        available = 312
    if not isfinite(available) or available <= 0:
        available = 312
    available = max(1, available - max(0, reserved_width))
    try:
        scale = float(font_scale())
    except (TypeError, ValueError):
        scale = 1.0
    if not isfinite(scale) or scale <= 0:
        scale = 1.0
    try:
        size = float(size)
    except (TypeError, ValueError):
        size = 18
    if not isfinite(size) or size <= 0:
        size = 18
    size = min(size, max(1, (available - 2) / (96 / 72 * scale)))
    expected, actual = (normalize("NFC", value).strip() for value in (expected, actual))
    if correct or (assisted and (not actual or actual == expected)):
        canonical = expected[:MAX_DISPLAY_CHARACTERS]
        if len(expected) > MAX_DISPLAY_CHARACTERS:
            canonical += "…"
        return col(label(canonical, size=size, color=INK, text_role=text_role,
                         margin_bottom=0), margin_bottom=8)

    width = min(available, size * (96 / 72) * scale + 2)
    columns = max(1, int(available / width))
    # Malformed pasted input must not create thousands of native widgets.
    # Bound both alignment work and rendered cells, leaving the raw attempt
    # untouched in the caller for persistence and grading.
    pairs = spelling_pairs(expected[:MAX_DISPLAY_CHARACTERS], actual[:MAX_DISPLAY_CHARACTERS])
    truncated = max(len(expected), len(actual), len(pairs)) > MAX_DISPLAY_CHARACTERS
    pairs = pairs[:MAX_DISPLAY_CHARACTERS]
    children = []
    for start in range(0, len(pairs), columns):
        cells = []
        for target, entered in pairs[start:start + columns]:
            mistaken = target != entered and not correct and not assisted
            # A space reserves the missing side's line height, and is not an
            # answer placeholder. No links or pitch marks belong in this block.
            cells.append(col(
                label(target or " ", size=size, color=ERROR_RED if mistaken and target else INK,
                      width=width, text_align="center", wrap=False, margin_bottom=0, text_role=text_role),
                label(entered or " ", size=size * 2 / 3,
                      color=ERROR_RED if mistaken and not target else MUTED,
                      width=width, text_align="center", wrap=False, margin_bottom=0, text_role=text_role),
                width=width))
        children.append(row(*cells, margin_bottom=4, align_items="start"))
    if truncated:
        children.append(label("…", size=size * 2 / 3, color=MUTED, margin_bottom=0,
                              text_role=text_role))
    return col(*children, margin_bottom=8)
