"""Multiline selections retain the existing values, events and source updates."""
import math
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kanamemory import selection_layout as layout


class Adapter:
    def __init__(self, activity=None, values=()):
        self.values = list(values)
        self.widths = []

    def getCount(self):
        return len(self.values)

    def getItem(self, index):
        return self.values[index]

    def add(self, value):
        self.values.append(value)

    def setContentWidth(self, width):
        self.widths.append(width)


class SelectionImpl(SimpleNamespace):
    """Like Toga's Python implementation, support weak native listeners."""


class PythonRunnable:
    """Match toga_android.libs.events.PythonRunnable's Java Runnable contract."""

    def __init__(self, callback):
        self.callback = callback

    def run(self):
        self.callback()


@pytest.fixture
def selection(monkeypatch):
    measure_spec = SimpleNamespace(UNSPECIFIED=0, EXACTLY=1,
                                   makeMeasureSpec=lambda width, mode: (width, mode))
    monkeypatch.setitem(sys.modules, "android.view", SimpleNamespace(View=SimpleNamespace(MeasureSpec=measure_spec)))
    monkeypatch.setitem(sys.modules, "android.widget", SimpleNamespace(AdapterView=SimpleNamespace(OnItemSelectedListener=object)))
    monkeypatch.setitem(sys.modules, "java", SimpleNamespace(dynamic_proxy=lambda interface: object))
    monkeypatch.setitem(sys.modules, "toga_android.libs.events", SimpleNamespace(PythonRunnable=PythonRunnable))
    monkeypatch.setitem(sys.modules, "org.example.kanamemory", SimpleNamespace(KanaSelectionAdapter=Adapter))
    monkeypatch.setattr(layout, "current_platform", "android")
    original_adapter = Adapter(values=["短", "看罗马音，从完整假名表中选择正确的假名（包括外来语组合）"])
    native = SimpleNamespace(adapter=original_adapter, selected=1, height=0)
    native.getSelectedItemPosition = lambda: native.selected
    native.setSelection = lambda index: setattr(native, "selected", index)
    native.setAdapter = lambda adapter: setattr(native, "adapter", adapter)
    native.getPaddingLeft = lambda: 12
    native.getPaddingRight = lambda: 18
    native.setDropDownWidth = Mock()
    native.pending = []
    native.post = lambda callback: native.pending.append(callback)
    native.setOnItemSelectedListener = lambda listener: setattr(native, "listener", listener)

    def drain():
        while native.pending:
            native.pending.pop(0).run()

    native.drain = drain

    def measure(spec, height_spec):
        width = max(1, spec[0] - 30)
        text = str(native.adapter.getItem(native.selected)) if native.adapter.values else ""
        native.height = max(48, 40 * math.ceil(len(text) * 24 / width))

    native.measure = Mock(side_effect=measure)
    native.getMeasuredHeight = lambda: native.height
    metrics = SimpleNamespace(widthPixels=1080)
    resources = SimpleNamespace(getDisplayMetrics=lambda: metrics)
    activity = SimpleNamespace(getResources=lambda: resources)
    widget = SimpleNamespace(intrinsic=SimpleNamespace(width=None, height=None))
    impl = SelectionImpl(interface=widget, native=native, adapter=original_adapter,
                           _native_activity=activity, last_selection=1,
                           scale_in=lambda value: value * 2,
                           scale_out=lambda value, rounding: math.ceil(value / 2))
    widget._impl = impl
    widget.refresh = Mock(side_effect=lambda: impl.rehint())
    impl.events = []

    def on_change(index):
        if index != impl.last_selection:
            impl.events.append(index)
            impl.last_selection = index

    def source_insert(*, index, item):
        impl.adapter.values.insert(index, item)
        if index <= impl.last_selection:
            impl.last_selection += 1
            native.setSelection(impl.last_selection)

    def source_change(*, index, item):
        impl.adapter.values[index] = item

    def source_remove(*, index, item=None):
        impl.adapter.values.pop(index)
        removed = impl.last_selection == index
        if index <= impl.last_selection:
            native.setSelection(max(0, impl.last_selection - 1))
            impl.last_selection = native.selected
        if removed:
            impl.events.append(impl.last_selection)

    def source_clear():
        impl.adapter.values.clear()
        native.selected = -1
        impl.on_change(None)

    impl.on_change = on_change
    impl.source_insert = Mock(side_effect=source_insert)
    impl.source_change = Mock(side_effect=source_change)
    impl.source_remove = Mock(side_effect=source_remove)
    impl.source_clear = Mock(side_effect=source_clear)
    return widget


def test_configuration_preserves_original_text_selection_and_events(selection):
    original = selection._impl.adapter
    expected = original.values.copy()
    assert layout.configure_selection(selection) is selection
    assert selection._impl.adapter.values == expected
    assert selection._impl.native.selected == 1
    assert selection._impl.last_selection == 1
    assert selection._impl.events == []
    assert selection._impl.adapter is selection._impl.native.adapter
    assert selection._impl.adapter is not original
    # A second configuration must not reset a later selection or chain handlers.
    installed = selection._impl.adapter
    layout.configure_selection(selection)
    assert selection._impl.adapter is installed
    assert selection.refresh.call_count == 1


def test_actual_allocated_width_wraps_selected_label_and_reuses_width(selection):
    layout.configure_selection(selection)
    impl = selection._impl
    impl._measure_text_width(160)
    narrow_height = selection.intrinsic.height
    assert impl.adapter.widths[-1] == 290  # 160 CSS px at 2x, minus native padding.
    assert impl.native.setDropDownWidth.call_args.args == (290,)
    impl.rehint()
    assert selection.intrinsic.height == narrow_height
    assert impl.native.measure.call_args.args == ((320, 1), 0)
    impl._measure_text_width(320)
    assert selection.intrinsic.height < narrow_height
    assert selection.intrinsic.height >= 48


def test_selection_change_remeasures_height_without_duplicate_callback(selection):
    layout.configure_selection(selection)
    impl = selection._impl
    impl._measure_text_width(120)
    long_height = selection.intrinsic.height
    impl.native.setSelection(0)
    impl.on_change(0)
    assert selection.intrinsic.height < long_height
    assert impl.events == [0]
    refreshes = selection.refresh.call_count
    impl.on_change(0)  # Spinner's delayed callback after programmatic selection.
    assert impl.events == [0]
    assert selection.refresh.call_count == refreshes


def test_native_change_finishes_selection_before_form_layout_and_delivers_once(selection):
    layout.configure_selection(selection)
    impl, native = selection._impl, selection._impl.native
    impl._measure_text_width(120)
    long_height = selection.intrinsic.height
    before = selection.refresh.call_count
    # The native click is still being dispatched when the selection listener
    # runs. A form rebuild here can invalidate the popup before it dismisses.
    native.setSelection(0)
    native.listener.onItemSelected(native, None, 0, 0)
    native.listener.onItemSelected(native, None, 0, 0)
    assert impl.events == []
    assert selection.refresh.call_count == before
    native.drain()  # The native dispatch returned; queued UI work may now run.
    assert impl.events == [0]
    assert selection.refresh.call_count == before + 1
    assert selection.intrinsic.height < long_height
    # A later genuine change still uses its newly allocated width and wraps.
    impl._measure_text_width(160)
    native.setSelection(1)
    native.listener.onItemSelected(native, None, 1, 1)
    native.drain()
    assert impl.events == [0, 1]
    assert impl.adapter.widths[-1] == 290
    assert selection.intrinsic.height > 48


def test_programmatic_change_remains_immediate_and_ignores_delayed_native_echo(selection):
    layout.configure_selection(selection)
    impl, native = selection._impl, selection._impl.native
    native.setSelection(0)
    impl.on_change(0)  # Toga select_item's synchronous notification.
    assert impl.events == [0]
    before = selection.refresh.call_count
    native.listener.onItemSelected(native, None, 0, 0)
    native.drain()
    assert impl.events == [0]
    assert selection.refresh.call_count == before


def test_pending_native_change_cannot_overwrite_newer_selection_or_clear(selection):
    layout.configure_selection(selection)
    impl, native = selection._impl, selection._impl.native
    native.setSelection(0)
    native.listener.onItemSelected(native, None, 0, 0)
    native.setSelection(1)
    impl.on_change(1)
    native.drain()
    assert impl.events == []
    assert impl.last_selection == native.selected == 1
    native.setSelection(0)
    native.listener.onItemSelected(native, None, 0, 0)
    impl.source_clear()
    native.listener.onNothingSelected(native)
    native.drain()
    assert impl.events == [None]
    assert impl.last_selection is None and native.selected == -1


def test_source_updates_use_replacement_adapter_and_preserve_selection_events(selection):
    originals = {name: getattr(selection._impl, name) for name in
                 ("source_insert", "source_change", "source_remove", "source_clear")}
    layout.configure_selection(selection)
    impl = selection._impl
    impl._measure_text_width(100)
    impl.source_insert(index=0, item="新增选项")
    originals["source_insert"].assert_called_once_with(index=0, item="新增选项")
    assert impl.native.selected == 2
    assert impl.events == []
    impl.source_change(index=2, item="短")
    originals["source_change"].assert_called_once_with(index=2, item="短")
    assert selection.intrinsic.height == 48
    impl.source_remove(index=0)
    assert impl.native.selected == 1
    assert impl.events == []
    impl.source_remove(index=1)
    assert impl.native.selected == 0
    assert impl.events == [0]
    impl.source_clear()
    originals["source_clear"].assert_called_once_with()
    assert impl.adapter.values == []
    assert impl.events == [0, None]


@pytest.mark.parametrize("platform", ["windows", "dummy", "macOS"])
def test_other_platforms_keep_their_native_selection(monkeypatch, platform):
    monkeypatch.setattr(layout, "current_platform", platform)
    widget = SimpleNamespace(refresh=Mock())
    assert layout.configure_selection(widget) is widget
    widget.refresh.assert_not_called()
