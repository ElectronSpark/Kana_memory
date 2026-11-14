"""A shorter interpreter switch interval, so the UI thread is not starved by a CPU-bound worker.

Every SQLite call releases the interpreter lock and must take it back afterwards. While another thread runs pure
Python code, the waiting thread gets the lock back only after ``sys.getswitchinterval()`` (5 ms by default), so a UI
action that issues thousands of statements waits that long for each of them for as long as the worker runs.
Start-up warm-up steps (the reviewed-example index, the main-route plan) are such workers; removing the call in
``KanaMemory.startup`` brings back taps that take tens of seconds during the warm-up.

What this does not help: one long C call that keeps the lock (for example decoding a large JSON payload) still
blocks the UI thread for its whole duration, and a UI action still pays for every statement it issues.
"""
from __future__ import annotations

import sys

# Measured on the host on 2026-11-14 (docs/design_current/07_engineering_structure.txt, the record on the switch
# interval): beside a busy Python thread, 6,400 point
# reads took 36.6 s with the default 5 ms and 59 ms with 0.5 ms (10 ms alone); the app's return to home (5,715
# statements, 0.22 s alone) had not finished after 60 s with 5 ms and took 0.50 s with 0.5 ms. The cost is more
# thread switches, and only while two threads compete for the lock; an idle UI thread costs nothing.
# Changing the number needs a new measurement.
UI_SWITCH_INTERVAL = 0.0005


def shorten_switch_interval(seconds: float = UI_SWITCH_INTERVAL) -> float | None:
    """Set the switch interval to ``seconds`` when it is longer; return the interval replaced, or None.

    It only ever shortens: an interval already as short or shorter (set by a QA harness or a developer) is kept.
    A later caller can still set any value with ``sys.setswitchinterval``.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError(f"switch interval must be a positive number of seconds, not {seconds!r}")
    if not (0 < seconds < float("inf")):
        raise ValueError(f"switch interval must be a positive finite number of seconds, not {seconds!r}")
    current = sys.getswitchinterval()
    if current <= seconds:
        return None
    sys.setswitchinterval(seconds)
    return current
