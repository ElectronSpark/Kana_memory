"""Stateless native controls shared by pages and the application shell.

Handlers are supplied by callers. These constructors do not reveal answers,
change learning state or own application/worker resources. Toga, Pack, the text
adapters and theme constants form the small namespace passed to composers.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import toga
from toga.platform import current_platform
from toga.style import Pack
from toga.style.pack import COLUMN, ROW

from .presentation import wrapped as fixed_wrapped
from .text_layout import configure_text, font_scale, text_style
from .typography import font_family, configure_text_role
from .selection_layout import configure_selection
from .ui_theme import GREEN, INK, MUTED, PAPER, WHITE, ENTRY_BACKGROUNDS, NEUTRAL, SELECTED_TINT
from .crash_events import install_app_loop_hook, install_toga_hook

# Every page builds its controls here, so this is where handler errors start
# being kept for feedback (crash_events) instead of only reaching logcat.
install_toga_hook()

__all__ = [
    "col", "row", "wrapped", "switch", "selection", "label", "button", "speaker_button",
    "activity_indicator",
    "GREEN", "INK", "MUTED", "PAPER", "WHITE", "ENTRY_BACKGROUNDS",
    "toga", "Pack", "text_style", "font_scale", "current_platform",
]


def col(*children: toga.Widget, **style: Any) -> toga.Box:
    return toga.Box(children=list(children), style=Pack(direction=COLUMN, **style))


def row(*children: toga.Widget, **style: Any) -> toga.Box:
    return toga.Box(children=list(children), style=Pack(direction=ROW, **style))


def wrapped(text: object, columns: int = 44) -> str:
    # Android wraps native text at its actual allocated width, including font
    # scaling. Keep the desktop preview's existing explicit line breaks.
    return str(text or "") if current_platform == "android" else fixed_wrapped(text, columns)


def activity_indicator(**style: Any) -> toga.Widget:
    """A running indicator for work in progress (Android: an indeterminate ProgressBar)."""
    return toga.ActivityIndicator(running=True, style=Pack(**style))


def switch(*args: Any, style: Any = None, **kwargs: Any) -> toga.Switch:
    return configure_text(toga.Switch(*args, style=text_style(**dict(style or {})), **kwargs), minimum_height=48)


def dismiss_keyboard(control: toga.Widget) -> None:
    """Hide the soft keyboard once a text entry is confirmed; Android only."""
    if current_platform != "android":
        return
    native = control._impl.native
    # "input_method" is android.content.Context.INPUT_METHOD_SERVICE.
    native.getContext().getSystemService("input_method").hideSoftInputFromWindow(native.getWindowToken(), 0)


def use_done_key(control: toga.Widget) -> None:
    """Show Done on the soft keyboard, so confirming stays on this field; Android only.

    With another field below, Android otherwise shows Next, which moves the
    cursor there even though confirming hides the keyboard.
    """
    if current_platform != "android":
        return
    # 6 is android.view.inputmethod.EditorInfo.IME_ACTION_DONE.
    control._impl.native.setImeOptions(6)


def selection(*args: Any, style: Any = None, **kwargs: Any) -> toga.Selection:
    return configure_selection(toga.Selection(*args, style=text_style(**dict(style or {})), **kwargs))


def label(text: object, size: float = 14, color: str = INK, bold: bool = False, wrap: bool = True, text_role: str = "ui", **style: Any) -> toga.Label:
    options = dict(font_size=size, color=color, font_weight="bold" if bold else "normal", margin_bottom=8)
    options.update(style)
    options["font_family"] = font_family(text_role, options.get("font_family"))
    widget = toga.Label(wrapped(text, columns=max(12, round(40 * 14 / max(14, size)))) if wrap else str(text), style=text_style(**options))
    configure_text_role(widget, text_role, options["font_family"])
    return configure_text(widget)


def button(text: str, handler: Callable[..., Any], *, primary: bool = False, selected: bool = False, compact: bool = False, multiline: bool = False, text_role: str = "ui", start_aligned: bool = False, **style: Any) -> toga.Button:
    # start_aligned: on Android the caption starts at the leading edge on every wrapped line (tree captions).
    # selected: the chosen one of a row of choices (a tab row): the neutral shape on a light tint of GREEN, its
    # caption GREEN and bold; not an action, so never together with primary. Toga's Android button applies the
    # colour, the background (as a tint of the native shape) and the font weight, as on the desktop.
    if primary and selected:
        raise ValueError("A button is either the primary action or the selected choice")
    # The app loop exists once pages build buttons: keep its task errors too.
    install_app_loop_hook()
    options = dict(font_size=14, height=46, margin_bottom=6, color=WHITE if primary else GREEN,
                   background_color=GREEN if primary else SELECTED_TINT if selected else NEUTRAL)
    if selected:
        options["font_weight"] = "bold"
    options.update(style)
    options["font_family"] = font_family(text_role, options.get("font_family"))
    options["height"] = max(options["height"], 2 * options["font_size"] + 20)
    minimum_height = options["height"]
    if current_platform == "android":
        del options["height"]
    widget = toga.Button(text, on_press=handler, style=text_style(**options))
    configure_text_role(widget, text_role, options["font_family"])
    if current_platform == "android":
        native = widget._impl.native
        native.setAllCaps(False)
        if multiline:
            native.setSingleLine(False)
            native.setMaxLines(20)
            native.setEllipsize(None)
            # Toga's Button constructor keeps only the first text line.
            # Restore the complete caption after enabling native wrapping.
            native.setText(str(text))
        if compact:
            native.setPadding(0, native.getPaddingTop(), 0, native.getPaddingBottom())
        configure_text(widget, minimum_height=minimum_height)
        widget.refresh()
        if start_aligned:
            # android.view.Gravity.START (0x800003) | CENTER_VERTICAL (0x10); the native horizontal
            # padding around large-font CJK glyphs stays.
            native.setGravity(0x800003 | 0x10)
    elif multiline:
        widget._impl.set_text(str(text))
        widget.refresh()
    return widget


def speaker_button(handler: Callable[..., Any], description: str = "播放读音") -> toga.Button:
    widget = button("🔊", handler, compact=True, width=52, height=52, font_size=16,
                    margin_left=6, margin_bottom=0)
    if current_platform == "android":
        widget._impl.native.setContentDescription(description)
    return widget

