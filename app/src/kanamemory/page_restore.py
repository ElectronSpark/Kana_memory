"""Mechanical restoration only; callers own navigation and learning policy.

Never score, checkpoint, change lanes, compensate timers or reveal answers here.
Content/diagnostic refresh must be decided before mounting retained widgets.
"""
from .navigation import PageState
from .personal_content_notes_ui import reset_cached_notes


def restore_bindings(app, bindings: dict) -> None:
    """Rebind retained widgets without recreating their input or stroke state."""
    for name, value in bindings.items():
        setattr(app, name, value)


def mount_retained_content(app, previous: PageState) -> None:
    app.content_slot.clear()
    reset_cached_notes(previous.content)
    app.content_slot.add(previous.content)


def schedule_scroll_restore(app, previous: PageState, *, only_if_changed: bool) -> None:
    """Wait for layout; a newer page invalidates this callback."""
    if previous.scroll:
        token = app._page_token
        def restore_position():
            if token == app._page_token and (not only_if_changed
                    or tuple(previous.scroll.position) != previous.position):
                previous.scroll.position = previous.position
        app.loop.call_soon(restore_position)
