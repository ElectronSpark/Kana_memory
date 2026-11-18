"""The one list of caches derived from the course content, and the one way to drop them all.

The effective catalogue (content_pack) is built once per process; the word tables, indexes and
lookups that other modules derive from it are cached for that process. Each such cache registers
itself here where it is defined: a cached function through ``content_cache`` (it keeps its own
``cache_clear``), a module-level table through ``register`` with a function that empties it.

``clear_all`` drops every registered cache. content_pack calls it in ``clear_cache`` and whenever it
builds an effective catalogue whose token differs from the one it built before, so that no cache
outlives the catalogue it was computed from; tests that patch a synthetic course in call it too.
It never reloads the catalogue itself: the module-level content read at import stays a process
constant (a library change takes effect after a restart).

Pure: no import of the modules that use it. tests/test_content_caches.py checks that every cached
function and module-level table of the app is registered here or exempt with its reason.
"""
from __future__ import annotations

import threading

_CLEARS = {}  # name -> function that empties the cache, in registration order
_LOCK = threading.Lock()


def register(clear, name):
    """Register ``clear`` (no arguments, empties one cache) under ``name``; returns ``clear``.

    The name is "<module>.<cache>" without the package prefix; registering a name again (a module
    imported anew) replaces the earlier function.
    """
    if not callable(clear) or not isinstance(name, str) or not name:
        raise TypeError("A content cache needs a clear function and a name")
    with _LOCK:
        _CLEARS[name] = clear
    return clear


def content_cache(function):
    """Decorator above a cached function (functools.lru_cache or one with the same ``cache_clear``)."""
    clear = getattr(function, "cache_clear", None)
    if not callable(clear):
        raise TypeError("content_cache needs a cached function with cache_clear()")
    register(clear, function.__module__.removeprefix("kanamemory.") + "." + function.__qualname__)
    return function


def registered():
    """The names of the registered caches (tests and diagnostics)."""
    with _LOCK:
        return tuple(_CLEARS)


def clear_all():
    """Drop every registered cache. Each one is filled again from the catalogue when it is next read."""
    with _LOCK:
        clears = tuple(_CLEARS.values())
    for clear in clears:
        clear()
