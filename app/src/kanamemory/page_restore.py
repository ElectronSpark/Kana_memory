"""Mechanical restoration only; callers own navigation and learning policy.

Never score, checkpoint, change lanes, compensate timers or reveal answers here.
Content/diagnostic refresh must be decided before mounting retained widgets.
"""
from .kana_line.fields import PartlySet
from .navigation import PageState
from .personal_content_notes_ui import reset_cached_notes


def restore_bindings(app, bindings: dict) -> None:
    """Rebind retained widgets without recreating their input or stroke state."""
    for name, value in bindings.items():
        if isinstance(value, PartlySet):
            # State kept as one object: the page gives back the fields it held, as it gave back the
            # attributes that existed when it was kept; a field it never held stays as it is now.
            value = value.restored_over(getattr(app, name, None))
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
