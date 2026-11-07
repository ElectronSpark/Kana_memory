"""Root Back policy on real app methods, without constructing a Toga app."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory.app import KanaMemory
from kanamemory.navigation import BackStack, PageState


def test_repeated_root_back_is_consumed_without_changing_view_or_history():
    state = SimpleNamespace(
        back_stack=BackStack(),
        page="home",
        _view_key=("show_home", None),
        _page_token=7,
        content_slot=object(),
        title_label=SimpleNamespace(text="假名记忆"),
        platform=Mock(),
    )
    before = vars(state).copy()
    history = state.back_stack.pages

    for _ in range(3):
        assert KanaMemory.go_back(state) is True

    assert vars(state) == before
    assert state.back_stack.pages is history
    assert history == []
    assert state.title_label.text == "假名记忆"
    assert state.platform.mock_calls == []


@pytest.mark.parametrize("with_history", [False, True], ids=["root", "child"])
def test_back_interception_stays_enabled_with_or_without_history(with_history):
    stack = BackStack()
    if with_history:
        stack.pages.append(PageState(("show_home", None), "home", "首页", object()))
    history = list(stack.pages)
    platform = SimpleNamespace(set_back_enabled=Mock())
    state = SimpleNamespace(back_stack=stack, platform=platform)

    KanaMemory._sync_back_handler(state)

    platform.set_back_enabled.assert_called_once_with(True)
    assert stack.pages == history
