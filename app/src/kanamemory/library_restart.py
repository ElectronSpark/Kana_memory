"""The content library page's automatic restart after a saved change: at most one countdown per app.

After a change on the page that needs a restart (install, enable, disable, remove), the page
restarts the app by itself through the same path as 立即重启 (content_library_ui) once the
countdown reaches zero. This module holds only the state and its rules; the page draws it and
performs the restart:

- one pending countdown at most: a second change starts it again (start cancels the running one);
- it belongs to the page that showed it (token): a rebuild of that same page moves it along
  (follow), any other screen change ends it at its next second (tick returns "ended");
- a cancelled or replaced countdown's queued second does nothing (tick returns "stale").

``note`` is the sentence saying why the last change did not restart the app by itself; the page
shows it after today's note until the page is left. ``slot`` is the notice box of the libraries
page now shown (None when nothing is pending).
"""
from __future__ import annotations

SECONDS = 3


class Countdown:
    """One pending automatic restart: its page (token), its notice box (slot), seconds left, timer."""

    def __init__(self, token, slot, seconds=SECONDS):
        self.token, self.slot, self.remaining, self.handle = token, slot, seconds, None


class LibraryRestart:
    def __init__(self):
        self.countdown = None
        self.note = None
        self.slot = None

    def start(self, token, slot, schedule, seconds=SECONDS):
        """A new countdown on the page ``token``; ``schedule(countdown)`` queues its next second."""
        self.cancel()
        countdown = Countdown(token, slot, seconds)
        self.countdown = countdown
        countdown.handle = schedule(countdown)
        return countdown

    def cancel(self):
        """End the pending countdown, if any; returns it (its slot may need drawing again)."""
        countdown, self.countdown = self.countdown, None
        if countdown is not None and countdown.handle is not None:
            countdown.handle.cancel()
            countdown.handle = None
        return countdown

    def follows(self, *, rebuilt, token):
        """Whether the pending countdown may go on into a page being built now: only when that build
        replaces the very page that shows it (``rebuilt``: the libraries page was current, ``token``
        its page token before the build)."""
        return self.countdown is not None and rebuilt and self.countdown.token == token

    def tick(self, countdown, *, current, schedule):
        """One second of ``countdown``. ``current``: its page is still shown and the app is not
        closing. Returns "stale" (no longer the pending one), "ended" (its page was left: no
        restart), "count" (seconds remain; the next one is queued) or "restart" (zero: restart now)."""
        if self.countdown is not countdown:
            return "stale"
        countdown.handle = None
        if not current:
            self.cancel()
            return "ended"
        countdown.remaining -= 1
        if countdown.remaining > 0:
            countdown.handle = schedule(countdown)
            return "count"
        self.countdown = None
        return "restart"


def of(app):
    """The app's LibraryRestart (created on first use)."""
    state = getattr(app, "_library_restart", None)
    if state is None:
        state = LibraryRestart()
        app._library_restart = state
    return state
