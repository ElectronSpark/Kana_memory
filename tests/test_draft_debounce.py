"""Coalesced draft persistence: pure state machine and loop wake helpers."""
import sqlite3
from unittest.mock import Mock

import pytest

from kanamemory.draft_debounce import (DEFAULT_DELAY, DraftDebouncer, call_later_awake,
                                       create_task_awake, is_database_locked, loop_scheduler,
                                       persist_with_lock_notice)


class Timers:
    """A manual scheduler: nothing fires until the test lets time pass."""

    def __init__(self):
        self.armed = []

    def __call__(self, delay, callback):
        handle = Mock()
        handle.cancelled = False
        handle.cancel.side_effect = lambda: setattr(handle, "cancelled", True)
        self.armed.append((delay, callback, handle))
        return handle

    def elapse(self):
        due, self.armed = self.armed, []
        for _, callback, handle in due:
            if not handle.cancelled:
                callback()


def writer():
    written = []
    holder = {}

    def on_due():
        pending = holder["draft"].flush_now()
        if pending is not None:
            written.append(pending)
            holder["draft"].mark_saved(*pending)

    timers = Timers()
    holder["draft"] = DraftDebouncer(timers, on_due)
    return holder["draft"], timers, written


def test_keystrokes_coalesce_into_one_write_of_the_latest_value():
    draft, timers, written = writer()
    for value in ("t", "te", "teg"):
        assert draft.accept("q1", value) is True
    assert [delay for delay, _, _ in timers.armed] == [DEFAULT_DELAY]
    assert written == []
    timers.elapse()
    assert written == [("q1", "teg")]


def test_continuous_typing_still_persists_within_one_delay():
    draft, timers, written = writer()
    draft.accept("q1", "a")
    timers.elapse()
    draft.accept("q1", "ab")
    draft.accept("q1", "abc")
    timers.elapse()
    assert written == [("q1", "a"), ("q1", "abc")]


def test_flush_now_returns_pending_once_and_disarms_the_timer():
    draft, timers, written = writer()
    draft.accept("q1", "draft")
    handle = timers.armed[0][2]
    assert draft.flush_now() == ("q1", "draft")
    assert handle.cancelled and draft.pending is None
    assert draft.flush_now() is None
    timers.elapse()
    assert written == []


def test_unchanged_value_after_save_is_not_written_again():
    draft, timers, written = writer()
    draft.accept("q1", "same")
    timers.elapse()
    assert draft.accept("q1", "same") is False
    assert timers.armed == []
    # A failed write forgets the saved value: the next change writes again.
    draft.mark_failed()
    assert draft.accept("q1", "same") is True


def test_new_target_persists_the_old_draft_first():
    draft, timers, written = writer()
    draft.accept("q1", "old")
    draft.accept("q2", "new")
    assert written == [("q1", "old")]
    timers.elapse()
    assert written == [("q1", "old"), ("q2", "new")]


def test_discard_drops_pending_without_writing():
    draft, timers, written = writer()
    draft.accept("q1", "gone")
    draft.discard()
    timers.elapse()
    assert written == [] and draft.pending is None


def test_invalid_construction_is_rejected():
    with pytest.raises(TypeError):
        DraftDebouncer(None, lambda: None)
    with pytest.raises(ValueError):
        DraftDebouncer(lambda d, c: None, lambda: None, delay=-1)


def test_lock_detection_matches_only_sqlite_busy_timeouts():
    assert is_database_locked(sqlite3.OperationalError("database is locked"))
    assert not is_database_locked(sqlite3.OperationalError("no such table: x"))
    assert not is_database_locked(RuntimeError("database is locked"))


def test_loop_helpers_arm_the_same_timer_and_wake_the_loop():
    loop = Mock()
    handle = call_later_awake(loop, 1, print, "x")
    assert handle is loop.call_later.return_value
    loop.call_later.assert_called_once_with(1, print, "x")
    assert loop.call_soon.call_count == 1
    coroutine = object()
    assert create_task_awake(loop, coroutine) is loop.create_task.return_value
    loop.create_task.assert_called_once_with(coroutine)
    assert loop.call_soon.call_count == 2
    schedule = loop_scheduler(loop)
    schedule(DEFAULT_DELAY, print)
    assert loop.call_later.call_args.args == (DEFAULT_DELAY, print)
    # Loops without call_soon (simple fakes) still get the timer.
    bare = type("Bare", (), {"call_later": lambda self, *a: "handle"})()
    assert call_later_awake(bare, 1, print) == "handle"


LOCKED = sqlite3.OperationalError("database is locked")


def locked_writer():
    """A timer-driven owner whose write hits a lock until `ok` is set."""
    state = {"ok": False, "written": []}
    holder = {}

    def write(pending):
        if not state["ok"]:
            raise LOCKED
        state["written"].append(pending)

    def on_due():
        pending = holder["draft"].flush_now()
        if pending is not None:
            persist_with_lock_notice(lambda: write(pending), record=lambda exc: None,
                                     debouncer=holder["draft"], key=pending[0], value=pending[1])

    timers = Timers()
    holder["draft"] = DraftDebouncer(timers, on_due)
    return holder["draft"], timers, state


def test_requeue_keeps_a_failed_timer_write_for_the_next_forced_flush():
    draft, timers, state = locked_writer()
    draft.accept("q1", "typed")
    timers.elapse()  # The timer's write hits a lock.
    assert state["written"] == [] and timers.armed == []  # No retry timer of its own.
    assert draft.pending == ("q1", "typed")
    # Leaving without another keystroke: the forced flush still gets the value.
    assert draft.flush_now() == ("q1", "typed")
    assert draft.flush_now() is None


def test_requeued_draft_is_rearmed_by_the_next_change_and_written_once():
    draft, timers, state = locked_writer()
    draft.accept("q1", "a")
    timers.elapse()
    state["ok"] = True
    assert draft.accept("q1", "a") is True  # Same text: the last write failed.
    assert len(timers.armed) == 1
    timers.elapse()
    assert state["written"] == [("q1", "a")]
    assert draft.accept("q1", "a") is False


def test_requeue_never_replaces_a_newer_pending_draft():
    draft = DraftDebouncer(Timers(), lambda: None)
    draft.accept("q1", "newer")
    draft.requeue("q1", "older")
    assert draft.pending == ("q1", "newer")


def test_persist_with_lock_notice_only_absorbs_lock_timeouts():
    draft = DraftDebouncer(Timers(), lambda: None)
    recorded, notices = [], []
    result = persist_with_lock_notice(Mock(side_effect=LOCKED), record=recorded.append,
        debouncer=draft, key="k", value="v", notice=lambda: notices.append(1))
    assert result is False and recorded == [LOCKED] and notices == [1]
    assert draft.pending == ("k", "v")
    draft.flush_now()
    assert persist_with_lock_notice(lambda: "saved", record=recorded.append,
                                    debouncer=draft, key="k", value="v") == "saved"
    assert draft.accept("k", "v") is False  # Marked saved.
    with pytest.raises(sqlite3.OperationalError):
        persist_with_lock_notice(Mock(side_effect=sqlite3.OperationalError("disk I/O error")),
                                 record=recorded.append, debouncer=draft, key="k", value="w")
    assert len(recorded) == 1 and draft.pending is None
    # Without a debouncer (no event loop) it is still a retry status.
    assert persist_with_lock_notice(Mock(side_effect=LOCKED), record=recorded.append) is False


def test_trailing_mode_runs_once_after_typing_pauses():
    timers = Timers()
    draft = DraftDebouncer(timers, lambda: None, delay=0.3, trailing=True)
    draft.accept("field")
    first = timers.armed[0][2]
    draft.accept("field")
    assert first.cancelled and len(timers.armed) == 2  # Re-armed from the last change.
    assert DraftDebouncer(Timers(), lambda: None).trailing is False
