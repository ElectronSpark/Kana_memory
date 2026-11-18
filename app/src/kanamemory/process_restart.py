"""Whether to restart the process so that a saved content library change takes effect.

The process keeps the content it started with (content_pack, content_caches). On Android leaving
the app and coming back does not end the process: the system creates the window again and runs
the app's start-up again in the same interpreter, which still serves the old content. When that
happens while a library change is pending, the app restarts the process before any page is
built, at most once per process. A first start in a process, a start without a pending change
(a plain return to the app) and a platform that cannot restart change nothing.

Pure: the state of this process and one decision function; the platform does the restart.
"""
from __future__ import annotations

RESTART = "restart"
CONTINUE = "continue"

_STATE = {"starts": 0, "attempted": False}


def note_start() -> bool:
    """Record one start of the App in this process; True when the App had already started in it."""
    _STATE["starts"] += 1
    return _STATE["starts"] > 1


def attempted() -> bool:
    """A restart was already tried in this process (it returned or failed: the process lives on)."""
    return _STATE["attempted"]


def mark_attempt() -> None:
    _STATE["attempted"] = True


def decide(*, started_before: bool, change_pending: bool, can_restart: bool, already_attempted: bool) -> str:
    """RESTART only for a start again in this process with a pending change, a platform that can
    restart and no restart tried before in this process; else CONTINUE (the page notes the change)."""
    if started_before and change_pending and can_restart and not already_attempted:
        return RESTART
    return CONTINUE
