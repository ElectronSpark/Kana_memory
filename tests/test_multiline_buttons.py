"""Native multi-line captions must survive Toga's single-line constructor."""
from kanamemory import ui_components as component_ui
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kanamemory import app as ui


@pytest.mark.parametrize("platform", ["android", "windows"])
@pytest.mark.parametrize("text", ["あ\nア", "复习到期 · 平假名\n看罗马音从完整假名表选择（10）"])
def test_complete_caption_is_restored_after_constructor_truncation(monkeypatch, platform, text):
    native = SimpleNamespace(text=None, setAllCaps=Mock(), setSingleLine=Mock(),
                             setMaxLines=Mock(), setEllipsize=Mock())
    native.setText = lambda value: setattr(native, "text", value)
    impl = SimpleNamespace(native=native, set_text=native.setText)
    widget = SimpleNamespace(_impl=impl, refresh=Mock(), style=SimpleNamespace())

    def constructor(caption, **kwargs):
        native.text = caption.split("\n")[0]
        return widget

    monkeypatch.setattr(ui, "current_platform", platform)
    monkeypatch.setattr(component_ui, "current_platform", platform)
    monkeypatch.setattr(ui.toga, "Button", constructor)
    component_ui.button(text, Mock(), multiline=True)
    assert native.text == text
    if platform == "android":
        native.setSingleLine.assert_called_once_with(False)
        native.setEllipsize.assert_called_once_with(None)
    assert widget.style.font_family == "sans-serif"
    widget.refresh.assert_called()
