"""Unhandled UI errors: where they happened, kept on the device for feedback.

Toga reports an exception escaping a widget handler only with
``traceback.print_exc()`` (Android logcat), and the page simply stays put.
This module records each such error, and each never-retrieved asyncio task
error, as code locations only: the exception class name, the page name, and
(file, function, line) frames inside this package. Exception messages, local
values, answers, prompts, course text, paths outside the package and
credentials are never recorded.

Entries are deduplicated by signature, bounded, and kept in a small JSON file
in the app data directory, so an error from an earlier run is still included
in the next feedback export. Recording is best effort and never replaces the
original error report.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback as _traceback
from collections import OrderedDict
from datetime import datetime, timezone
from threading import Lock

MAX_ENTRIES = 30
MAX_FRAMES = 14
MAX_COUNT = 9999
FILE_NAME = "unhandled_errors.json"
FORMAT = "kanamemory-unhandled-errors"
_PACKAGE = "kanamemory"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.<>]{0,79}$")


def _name(value, fallback="?"):
    text = str(value or "")
    return text if _NAME.match(text) else fallback


def _frames(tb):
    """Package frames only; an outside frame keeps just its module file name."""
    frames = []
    for frame in _traceback.extract_tb(tb):
        path = frame.filename.replace("\\", "/")
        marker = "/" + _PACKAGE + "/"
        if marker in path:
            module = path.rsplit(marker, 1)[1]
            if not re.match(r"^[A-Za-z0-9_/]{1,80}\.py$", module):
                continue
            frames.append([module, _name(frame.name), int(frame.lineno or 0)])
        else:
            base = os.path.basename(path)
            frames.append([base if re.match(r"^[A-Za-z0-9_]{1,60}\.py$", base) else "(external)",
                           _name(frame.name), 0])
    # Keep the innermost frames: they name the failing call.
    return frames[-MAX_FRAMES:]


def _chain(error):
    names, seen, current = [], set(), error
    while current is not None and id(current) not in seen and len(names) < 4:
        seen.add(id(current))
        names.append(_name(type(current).__name__, "OtherError"))
        current = current.__cause__ or current.__context__
    return names


class UnhandledErrorLog:
    def __init__(self):
        self._entries = OrderedDict()
        self._lock = Lock()
        self._path = None
        self._loaded = False

    def configure(self, directory):
        """Persist under ``directory``; loads earlier entries once."""
        with self._lock:
            self._path = os.path.join(str(directory), FILE_NAME) if directory else None
            self._loaded = False
            self._load_locked()

    def _load_locked(self):
        if self._loaded or not self._path:
            return
        self._loaded = True
        try:
            with open(self._path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            return
        for entry in data.get("entries", [])[-MAX_ENTRIES:]:
            if isinstance(entry, dict) and isinstance(entry.get("signature"), str):
                self._entries.setdefault(entry["signature"], entry)

    def _save_locked(self):
        if not self._path:
            return
        temporary = self._path + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump({"format": FORMAT, "version": 1, "entries": list(self._entries.values())},
                          handle, ensure_ascii=False)
            os.replace(temporary, self._path)
        except OSError:
            pass

    def record(self, error, *, source, page=None, version=None):
        if not isinstance(error, BaseException):
            return None
        frames = _frames(error.__traceback__)
        chain = _chain(error)
        signature = json.dumps([source, chain, frames], separators=(",", ":"))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._load_locked()
            entry = self._entries.get(signature)
            if entry is None:
                entry = {"signature": signature, "source": _name(source), "error_type": chain[0],
                         "chained_types": chain[1:], "frames": frames, "first_at_utc": now,
                         "count": 0, "pages": [], "app_versions": []}
                self._entries[signature] = entry
            entry["last_at_utc"] = now
            entry["count"] = min(MAX_COUNT, int(entry.get("count", 0)) + 1)
            for key, value in (("pages", page), ("app_versions", version)):
                value = _name(value, None) if value is not None else None
                if value and value not in entry[key]:
                    entry[key] = (entry[key] + [value])[-5:]
            self._entries.move_to_end(signature)
            while len(self._entries) > MAX_ENTRIES:
                self._entries.popitem(last=False)
            self._save_locked()
            return dict(entry)

    def snapshot(self):
        with self._lock:
            self._load_locked()
            return [{key: value for key, value in entry.items() if key != "signature"}
                    for entry in reversed(self._entries.values())]

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._save_locked()


_log = UnhandledErrorLog()


def _current_page():
    try:
        import toga
        app = toga.App.app
    except Exception:
        return None, None
    page = getattr(app, "page", None)
    if app is not None and _log._path is None:
        try:
            _log.configure(app.paths.data)
        except Exception:
            pass
    try:
        from .build_info import VERSION
    except Exception:
        VERSION = None
    return (page if isinstance(page, str) else None), VERSION


def record_unhandled(error, source):
    """Best effort: never raises."""
    try:
        page, version = _current_page()
        return _log.record(error, source=source, page=page, version=version)
    except Exception:
        return None


def unhandled_errors():
    try:
        _current_page()
        return _log.snapshot()
    except Exception:
        return []


def clear_unhandled_errors():
    try:
        _log.clear()
    except Exception:
        pass


class _ReportingTraceback:
    """Stand-in for ``toga.handlers.traceback``: record, then print as before."""

    def __getattr__(self, name):
        return getattr(_traceback, name)

    @staticmethod
    def print_exc(*args, **kwargs):
        error = sys.exc_info()[1]
        if error is not None:
            record_unhandled(error, "ui_handler")
        return _traceback.print_exc(*args, **kwargs)


def install_toga_hook():
    """Route every Toga handler error report through ``record_unhandled``.

    Toga 0.5's sync, async and long-running handler wrappers all report with
    ``traceback.print_exc()`` inside their ``except`` block.
    """
    try:
        import toga.handlers as handlers
    except Exception:
        return False
    if not isinstance(getattr(handlers, "traceback", None), _ReportingTraceback):
        handlers.traceback = _ReportingTraceback()
    return True


def install_app_loop_hook():
    """Hook the running Toga app's loop once it exists (cheap when done)."""
    try:
        import toga
        app = toga.App.app
        loop = getattr(app, "loop", None) if app is not None else None
    except Exception:
        return False
    return install_loop_hook(loop) if loop is not None else False


def install_loop_hook(loop):
    """Record never-retrieved task errors; the previous handler still runs."""
    if loop is None or getattr(loop, "_kanamemory_error_hook", False):
        return False
    try:
        previous = loop.get_exception_handler()
    except Exception:
        return False

    def handler(event_loop, context):
        error = context.get("exception") if isinstance(context, dict) else None
        if isinstance(error, BaseException):
            record_unhandled(error, "async_task")
        if previous is not None:
            previous(event_loop, context)
        else:
            event_loop.default_exception_handler(context)

    try:
        loop.set_exception_handler(handler)
        loop._kanamemory_error_hook = True
    except Exception:
        return False
    return True
