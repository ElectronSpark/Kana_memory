"""Retained views preserve identity; deferred layout cannot affect a newer page."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kanamemory.navigation import PageState
from kanamemory.page_restore import schedule_scroll_restore


@pytest.mark.parametrize("only_if_changed", [False, True])
@pytest.mark.parametrize("same_position", [False, True])
def test_scroll_setter_policy_and_stale_callback(only_if_changed, same_position):
    class Scroll:
        def __init__(self):
            self.value, self.writes = ((0, 50) if same_position else (0, 0)), []
        @property
        def position(self):
            return self.value
        @position.setter
        def position(self, value):
            self.writes.append(value)
            self.value = value
    scroll = Scroll()
    old = PageState(("detail", "one"), "detail", "", object(), scroll=scroll, position=(0, 50))
    app = SimpleNamespace(_page_token=7, loop=SimpleNamespace(call_soon=Mock()))
    schedule_scroll_restore(app, old, only_if_changed=only_if_changed)
    callback = app.loop.call_soon.call_args.args[0]
    callback()
    assert scroll.writes == ([] if only_if_changed and same_position else [(0, 50)])
    scroll.writes.clear()
    scroll.value = (0, 0)
    app._page_token += 1
    callback()
    assert scroll.value == (0, 0) and scroll.writes == []
