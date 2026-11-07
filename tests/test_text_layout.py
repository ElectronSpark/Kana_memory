"""Layout constraints must follow the actual allocation, including nested rows."""
import math
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from toga.style import Pack
from travertino.node import Node
from travertino.size import at_least

from kanamemory import text_layout


from support.text_layout_support import leaf


@pytest.mark.parametrize('scale', [1, 1.3, 2])
def test_width_constraint_controls_height_without_expanding_card(scale):
    node, widths = leaf(text_layout.TextPack(margin_left=12, margin_right=12), font_scale=scale)
    node.style._layout_node(260, 0, True, False)
    first_height = node.layout.content_height
    assert widths[-1] == 236
    assert node.layout.content_width == 236
    assert first_height > 18
    node.style._layout_node(160, 0, True, False)
    assert widths[-1] == 136
    assert node.layout.content_width == 136
    assert node.layout.content_height > first_height


def test_style_copy_keeps_all_pack_properties_and_measurement_behavior():
    original = text_layout.TextPack(font_size=17, color='#185C50', width=80, margin=3, flex=1)
    copied = original.copy()
    assert dict(copied) == dict(original)
    node, widths = leaf(copied)
    node.style._layout_node(320, 0, True, False)
    assert widths == [80]
    assert node.layout.content_width == 80


def test_nested_flexible_label_respects_space_reserved_for_speaker():
    root = Node(Pack(direction='row'), children=[])
    root.style._assigned_applicator = SimpleNamespace(node=root)
    text, widths = leaf(text_layout.TextPack(flex=1, margin_right=6), font_scale=2)
    speaker = Node(Pack(width=52, height=52))
    speaker.style._assigned_applicator = SimpleNamespace(node=speaker)
    root.add(text)
    root.add(speaker)
    root.style._layout_node(260, 0, True, False)
    assert widths[-1] == 202
    assert root.layout.content_width == 260
    assert text.layout.content_height > 52
    assert root.layout.content_height >= text.layout.content_height


def test_native_measurement_keeps_text_spans_and_updates_after_text_change(monkeypatch):
    class Specs:
        UNSPECIFIED = 0
        EXACTLY = 1

        @staticmethod
        def makeMeasureSpec(width, mode):
            return width, mode

    monkeypatch.setitem(sys.modules, 'android.view', SimpleNamespace(View=SimpleNamespace(MeasureSpec=Specs)))
    monkeypatch.setattr(text_layout, 'current_platform', 'android')
    # An opaque styled CharSequence: measuring must never replace its text.
    styled = object()
    native = SimpleNamespace(setSingleLine=Mock(), setHorizontallyScrolling=Mock(),
        setMaxLines=Mock(), setEllipsize=Mock(), text=styled, height=36)
    native.measure = Mock()
    native.getMeasuredHeight = lambda: native.height
    impl = SimpleNamespace(native=native, interface=SimpleNamespace(intrinsic=SimpleNamespace()),
        scale_in=lambda n: n * 2, scale_out=lambda n, rounding: n // 2,
        set_text=lambda value: setattr(native, 'text', value))
    widget = SimpleNamespace(_impl=impl, refresh=Mock())
    text_layout.configure_text(widget, minimum_height=46)
    impl._measure_text_width(200)
    native.measure.assert_called_with((400, Specs.EXACTLY), Specs.UNSPECIFIED)
    assert impl.interface.intrinsic.width == at_least(0)
    assert impl.interface.intrinsic.height == 46
    native.height = 180
    impl.rehint()
    assert impl.interface.intrinsic.height == 90
    assert native.text is styled
    native.setHorizontallyScrolling.assert_called_once_with(False)
    impl.set_text('未下载模型 → 识别笔迹')
    assert native.text == '未下载模型 → 识别笔迹'


def test_desktop_style_remains_standard_pack(monkeypatch):
    monkeypatch.setattr(text_layout, 'current_platform', 'windows')
    assert type(text_layout.text_style(font_size=14)) is Pack
    assert text_layout.font_scale() == 1
