"""Unhandled handler/task errors are kept as code locations for feedback."""
import asyncio
import json

import pytest
import toga.handlers

from kanamemory import crash_events
from kanamemory.crash_events import UnhandledErrorLog


SECRET = "答案たべもの sk-test-secret"


@pytest.fixture
def log(tmp_path, monkeypatch):
    fresh = UnhandledErrorLog()
    fresh.configure(tmp_path)
    monkeypatch.setattr(crash_events, "_log", fresh)
    return fresh


def failing_handler(widget, **kwargs):
    raise KeyError(SECRET)


def test_toga_handler_error_is_recorded_without_message(log, capsys):
    assert crash_events.install_toga_hook()
    handler = toga.handlers.wrapped_handler(object(), failing_handler)
    handler()  # Toga swallows it after printing, exactly as on a button press.
    assert "Error in handler" in capsys.readouterr().err
    [entry] = crash_events.unhandled_errors()
    assert entry["source"] == "ui_handler" and entry["error_type"] == "KeyError"
    assert entry["count"] == 1
    assert ["test_crash_events.py", "failing_handler", 0] in entry["frames"]
    dumped = json.dumps(crash_events.unhandled_errors(), ensure_ascii=False)
    assert "たべもの" not in dumped and "sk-test" not in dumped


def test_hook_is_idempotent_and_keeps_traceback_api():
    assert crash_events.install_toga_hook() and crash_events.install_toga_hook()
    assert isinstance(toga.handlers.traceback, crash_events._ReportingTraceback)
    assert toga.handlers.traceback.format_exc is not None


def test_repeated_errors_merge_and_survive_restart(log, tmp_path):
    for _ in range(3):
        try:
            failing_handler(None)
        except KeyError as exc:
            crash_events.record_unhandled(exc, "ui_handler")
    reloaded = UnhandledErrorLog()
    reloaded.configure(tmp_path)
    [entry] = reloaded.snapshot()
    assert entry["count"] == 3 and entry["error_type"] == "KeyError"
    assert SECRET not in (tmp_path / crash_events.FILE_NAME).read_text("utf-8")


def test_entries_are_bounded(log):
    for index in range(crash_events.MAX_ENTRIES + 5):
        error = type(f"Error{index}", (Exception,), {})()
        crash_events.record_unhandled(error, "ui_handler")
    entries = crash_events.unhandled_errors()
    assert len(entries) == crash_events.MAX_ENTRIES
    assert entries[0]["error_type"] == f"Error{crash_events.MAX_ENTRIES + 4}"


def test_package_frames_keep_module_function_and_line(log):
    from kanamemory.crash_events import _frames

    def outer():
        raise ValueError(SECRET)
    try:
        outer()
    except ValueError as exc:
        frames = _frames(exc.__traceback__)
    assert frames[-1] == ["test_crash_events.py", "outer", 0]


def test_unretrieved_task_error_is_recorded_and_previous_handler_runs(log):
    loop = asyncio.new_event_loop()
    seen = []
    loop.set_exception_handler(lambda event_loop, context: seen.append(context["message"]))
    try:
        assert crash_events.install_loop_hook(loop)
        assert not crash_events.install_loop_hook(loop)
        loop.call_exception_handler({"message": "Task exception was never retrieved",
                                     "exception": RuntimeError(SECRET)})
    finally:
        loop.close()
    assert seen == ["Task exception was never retrieved"]
    [entry] = crash_events.unhandled_errors()
    assert entry["source"] == "async_task" and entry["error_type"] == "RuntimeError"


def test_recording_never_raises(monkeypatch):
    monkeypatch.setattr(crash_events, "_log", None)
    assert crash_events.record_unhandled(ValueError(), "ui_handler") is None
    assert crash_events.unhandled_errors() == []
