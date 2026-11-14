"""Coalesced draft persistence, as a small timer-free state machine.

Keystrokes update the on-screen/in-memory draft at once; persistence happens
at most `delay` seconds after the first unsaved change, or synchronously at a
forced flush (submit, leave, background, page change). The caller supplies the
scheduler and does the actual write, so this module has no Toga, SQLite, clock
or event-loop dependency. What is persisted never changes, only when.

The two loop helpers wrap `call_later`/`create_task` for callers running
inside native callbacks: Toga's Android loop only arranges a wakeup from
`call_soon`, so a timer armed from a native callback could otherwise sleep
until unrelated async work happens to run.
"""

DEFAULT_DELAY = 0.25


class DraftDebouncer:
    """Keep one pending (key, value) draft and one armed timer.

    `schedule(delay, callback)` must return a handle with `cancel()`.
    `on_due()` is called by the timer; the owner then calls `flush_now()` and
    persists what it returns. A first unsaved change arms the timer; later
    changes only replace the pending value, so a continuous typist still
    persists every `delay` seconds rather than never.

    trailing=True instead re-arms on every change, so `on_due` runs once
    `delay` seconds after typing pauses (settings fields and searches, whose
    callback re-plans or re-queries rather than only saving a draft).
    """

    def __init__(self, schedule, on_due, *, delay=DEFAULT_DELAY, trailing=False):
        if not callable(schedule) or not callable(on_due):
            raise TypeError("schedule and on_due must be callable")
        if not isinstance(delay, (int, float)) or delay < 0:
            raise ValueError("delay must be a non-negative number")
        self._schedule, self._on_due, self.delay = schedule, on_due, delay
        self.trailing = bool(trailing)
        self._queued = None
        self._handle = None
        self._saved = None

    @property
    def pending(self):
        return self._queued

    def accept(self, key, value=None):
        """Record a changed draft; False when it equals the last saved one."""
        if self._queued is None and self._saved == (key, value):
            return False
        if self._queued is not None and self._queued[0] != key:
            # A different draft target: persist the old one first, never drop it.
            self._on_due()
        self._queued = (key, value)
        if self._handle is not None and self.trailing:
            handle, self._handle = self._handle, None
            handle.cancel()
        if self._handle is None:
            self._handle = self._schedule(self.delay, self._fire)
        return True

    def _fire(self):
        self._handle = None
        if self._queued is not None:
            self._on_due()

    def flush_now(self):
        """Return the pending (key, value) once and disarm the timer."""
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.cancel()
        pending, self._queued = self._queued, None
        return pending

    def mark_saved(self, key, value=None):
        """The owner persisted this draft (by a flush or any full save)."""
        self._saved = (key, value)

    def mark_failed(self):
        """A write failed: the next accepted change must write again."""
        self._saved = None

    def requeue(self, key, value=None):
        """A write of this draft failed: keep it for the next forced flush.

        No timer is armed (a busy database would just fail again on its own);
        the next accepted change re-arms one, and any forced flush (submit,
        leave, background, exit) gets this value back from flush_now. A newer
        pending draft already queued wins over the failed copy.
        """
        if self._queued is None:
            self._queued = (key, value)

    def discard(self):
        """Drop any pending draft without persisting it (its page is gone)."""
        self.flush_now()
        self._saved = None


def is_database_locked(error):
    """A SQLite busy timeout that a later user action may simply retry.

    Name-based so pure/UI callers need not import sqlite3.
    """
    return (type(error).__name__ == "OperationalError"
            and "database is locked" in str(error).casefold())


def persist_with_lock_notice(write, *, record, debouncer=None, key=None, value=None, notice=None):
    """Run one draft write; a lock timeout becomes a queued, retryable failure.

    `write()` does the Store write and returns its result. On `database is
    locked`: `record(error)` logs the diagnostic, the debouncer (if any) marks
    the draft unsaved and requeues `(key, value)` for the next forced flush,
    `notice()` shows the page's retry status, and False is returned. Any other
    exception propagates unchanged. On success the debouncer marks `(key,
    value)` saved and the write's own result is returned. Pass `value` as a
    private copy: it is kept for the duplicate check and the retry.
    """
    try:
        result = write()
    except Exception as error:
        if not is_database_locked(error):
            raise
        record(error)
        if debouncer is not None:
            debouncer.mark_failed()
            debouncer.requeue(key, value)
        if notice is not None:
            notice()
        return False
    if debouncer is not None:
        debouncer.mark_saved(key, value)
    return result


def _wake(loop):
    soon = getattr(loop, "call_soon", None)
    if callable(soon):
        soon(lambda: None)


def call_later_awake(loop, delay, callback, *args):
    """`loop.call_later` that also wakes Toga Android's loop."""
    handle = loop.call_later(delay, callback, *args)
    _wake(loop)
    return handle


def create_task_awake(loop, coroutine):
    """`loop.create_task` from a native callback, with the same explicit wakeup."""
    task = loop.create_task(coroutine)
    _wake(loop)
    return task


def loop_scheduler(loop):
    """A DraftDebouncer scheduler bound to an event loop."""
    return lambda delay, callback: call_later_awake(loop, delay, callback)
