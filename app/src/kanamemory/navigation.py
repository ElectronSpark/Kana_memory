"""Small, bounded back stack. Views stay alive so form inputs survive Back."""
from dataclasses import dataclass, field
from functools import wraps
from inspect import signature
from typing import Any


@dataclass
class PageState:
    key: tuple
    name: str
    title: str
    content: Any
    bindings: dict = field(default_factory=dict)
    scroll: Any = None
    position: tuple = (0, 0)
    content_generation: int = 0


class BackStack:
    def __init__(self, limit=24):
        if limit < 2:
            raise ValueError("A back stack must retain the root and one previous page")
        self.limit = limit
        self.pages = []

    def remember(self, previous, next_key, *, replace=False):
        if previous is None or replace or previous.key == next_key:
            return
        self.pages.append(previous)
        if len(self.pages) > self.limit:
            # Keep the original home page reachable, while bounding native views.
            del self.pages[1]

    def pop(self):
        return self.pages.pop() if self.pages else None

    def discard(self, names):
        self.pages[:] = [page for page in self.pages if page.name not in names]


def page_view(*, detail=False, replace=False, refresh=False):
    """Capture the old view before a builder changes any widget bindings.

    Nested builders count as one navigation. Repeated renders of the same page
    replace it; detail identities include the record ID. Question advancement
    uses replace=True so a saved review cannot reappear through Back.
    Mutable detail workflows opt into refresh=True: same-identity state changes
    repaint without adding history; static self-links retain their current view.
    """
    def decorate(method):
        parameter = next(iter(list(signature(method).parameters)[1:]), None)
        @wraps(method)
        def render(self, *args, **kwargs):
            if getattr(self, "_backup_busy", False):
                return
            if self._navigating:
                return method(self, *args, **kwargs)
            previous = self._capture_page()
            token = self._page_token
            value = (args[0] if args else kwargs.get(parameter)) if detail else None
            identity = value.get("id", value.get("title")) if isinstance(value, dict) else value
            key = (method.__name__, identity)
            if detail and key == self._view_key and not refresh:
                return
            self._navigating = True
            previous_render_key = getattr(self, "_render_key", None)
            self._render_key = key
            try:
                result = method(self, *args, **kwargs)
            finally:
                self._render_key = previous_render_key
                self._navigating = False
            if token != self._page_token:
                self.back_stack.remember(previous, key, replace=replace)
                self._view_key = key
                self._sync_back_handler()
            return result
        return render
    return decorate
