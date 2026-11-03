"""Unlabelled full kana tables and compact, ordered candidate buttons."""
from .word_keyboard import keyboard_grid


def key_table(u, options, on_key, *, width=360, scale=1., show_axes=False):
    rows, extras = keyboard_grid(options)
    longest = max((len(key) for key in options), default=1)
    gutter = max(30, round(30 * scale)) if show_axes else 0
    cell = max(42, round(longest * 18 * (96 / 72) * scale + 12), (width - gutter) / 5 - 4)
    # app.button enforces 2 * font_size + 20 (56 at 18pt), even when its
    # caller supplies less. Include that real minimum in scroll sizing.
    height = max(56, round(56 * scale))
    body = u.col(width=gutter + 5 * (cell + 4))

    def key_button(key):
        return u.button(key, lambda w, value=key: on_key(value), width=cell, height=height,
                        margin=2, font_size=18, compact=True, text_role="study")

    count = 0
    if rows and show_axes:
        body.add(u.row(u.label("", width=gutter, margin_bottom=0),
            *[u.label(v if show_axes else "", width=cell, margin=2, size=13, text_align="center", color=u.MUTED)
              for v in ("a", "i", "u", "e", "o")]))
        count += 1
    for label, cells in rows:
        body.add(u.row(*([u.label(label, width=gutter, size=12, margin_top=12, color=u.MUTED)] if show_axes else []),
            *[u.col(*(key_button(key) for key in cell_keys), width=cell + 4)
              if cell_keys else u.col(width=cell + 4) for cell_keys in cells]))
        count += max(len(cell_keys) for cell_keys in cells)
    for start in range(0, len(extras), 5):
        body.add(u.row(*([u.label("", width=gutter, margin_bottom=0)] if show_axes else []),
                      *(key_button(key) for key in extras[start:start + 5])))
        count += 1
    return body, max(height + 4, count * (height + 8)), gutter + 5 * (cell + 4)


def candidate_keys(u, options, on_key, *, width=360, scale=1.):
    """Wrap the saved candidates without sorting, axes, or alphabet gaps."""
    keys = list(dict.fromkeys(options))
    longest = max((len(key) for key in keys), default=1)
    minimum = max(56, round(longest * 18 * (96 / 72) * scale + 12))
    columns = max(1, min(5, len(keys), int(width // (minimum + 4))))
    cell = max(minimum, width / columns - 4)
    height = max(56, round(56 * scale))
    table_width = columns * (cell + 4)
    body = u.col(width=table_width)
    for start in range(0, len(keys), columns):
        body.add(u.row(*(u.button(key, lambda w, value=key: on_key(value),
            width=cell, height=height, margin=2, font_size=18, compact=True,
            text_role="study") for key in keys[start:start + columns])))
    return body, max(1, len(body.children)) * (height + 8), table_width
