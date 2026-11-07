"""A programmatic tree repaint must not behave like a user selecting content."""
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path
import sys

import pytest

from kanamemory.tree_checkbox import update_tree_checkbox


class EagerCheckbox:
    """Model native checked-change callbacks, including programmatic setters."""

    def __init__(self, handler):
        self.on_change = handler
        self._value = False
        self.enabled = True
        self._impl = SimpleNamespace(native=Mock())

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self._value = value
        if self.on_change:
            self.on_change(self)


def test_repainting_all_tree_states_does_not_toggle_selection_but_next_user_change_does():
    selections = []
    callback = lambda widget: selections.append(widget.value)
    checkbox = EagerCheckbox(callback)
    platform = SimpleNamespace(current_platform="android")
    # Includes the problematic all -> partial transition: native setChecked
    # changes true to false, but must not invoke a second parent-group toggle.
    for state, available, total in (("all", 2, 3), ("partial", 2, 3),
                                    ("none", 2, 3), ("none", 0, 3), ("none", 0, 0)):
        update_tree_checkbox(checkbox, platform, state,
                             {"new_count": available, "total_count": total, "added_count": total - available,
                              "selected_count": available if state == "all" else 1 if state == "partial" else 0}, "测试组")
        assert selections == []
        assert checkbox.on_change is callback
    checkbox.value = True
    assert selections == [True]


def test_native_refresh_failure_does_not_detach_the_users_change_handler():
    received = []

    def callback(widget):
        received.append(widget)

    checkbox = EagerCheckbox(callback)
    checkbox._impl.native.setMixed.side_effect = RuntimeError("view temporarily unavailable")
    with pytest.raises(RuntimeError, match="view temporarily unavailable"):
        update_tree_checkbox(checkbox, SimpleNamespace(current_platform="android"), "partial",
                             {"new_count": 2, "total_count": 3, "added_count": 1, "selected_count": 1}, "测试组")
    assert received == []
    assert checkbox.on_change is callback
    checkbox.value = True
    assert received == [checkbox]


def test_repeated_refresh_preserves_toga_raw_callback_and_its_single_widget_argument(capsys):
    from toga.handlers import wrapped_handler

    class TogaCheckbox(EagerCheckbox):
        @property
        def on_change(self):
            return self._on_change

        @on_change.setter
        def on_change(self, handler):
            self._on_change = wrapped_handler(self, handler)

        @EagerCheckbox.value.setter
        def value(self, value):
            self._value = value
            self.on_change()

    received = []

    def callback(widget):
        received.append(widget)

    checkbox = TogaCheckbox(callback)
    platform = SimpleNamespace(current_platform="android")
    for state in ("all", "partial", "none") * 10:
        update_tree_checkbox(checkbox, platform, state,
                             {"new_count": 2, "total_count": 3, "added_count": 1,
                              "selected_count": {"all": 2, "partial": 1, "none": 0}[state]}, "测试组")
        assert checkbox.on_change._raw is callback
        assert received == []
    checkbox.value = True
    assert received == [checkbox]
    assert not capsys.readouterr().err


@pytest.mark.parametrize("state,counts,visual,checked,enabled", [
    ("none", {"new_count": 2, "total_count": 3, "added_count": 1, "selected_count": 0}, "partial", False, True),
    ("all", {"new_count": 2, "total_count": 3, "added_count": 1, "selected_count": 2}, "all", True, True),
    ("none", {"new_count": 0, "total_count": 3, "added_count": 3, "selected_count": 0}, "added", True, False),
    ("none", {"new_count": 0, "total_count": 0, "added_count": 0, "selected_count": 0}, "empty", False, False),
    ("partial", {"new_count": 3, "total_count": 3, "added_count": 0, "selected_count": 1}, "partial", False, True),
    ("none", {"new_count": 3, "total_count": 3, "added_count": 0, "selected_count": 0}, "none", False, True),
])
def test_parent_tree_indicator_includes_enrolled_children_without_selecting_them(state, counts, visual, checked, enabled):
    received = []
    checkbox = EagerCheckbox(lambda widget: received.append(widget.value))
    update_tree_checkbox(checkbox, SimpleNamespace(current_platform="android"), state, counts, "测试组")
    assert checkbox._study_selection_state == visual
    assert checkbox.value is checked and checkbox.enabled is enabled
    checkbox._impl.native.setMixed.assert_called_once_with(visual == "partial")
    assert not received
