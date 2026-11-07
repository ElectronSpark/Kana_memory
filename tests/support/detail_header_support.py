"""Shared detail header fixtures and assertions; no collected scenarios."""
from kanamemory import ui_components as component_ui
from kanamemory.app import KanaMemory
from types import MethodType
from unittest.mock import Mock
from types import SimpleNamespace
from support.fake_ui import View
from kanamemory import app as app_module
import pytest


@pytest.fixture
def chrome(monkeypatch):
    monkeypatch.setattr(app_module, "current_platform", "android")
    monkeypatch.setattr(component_ui, "current_platform", "android")
    monkeypatch.setattr(component_ui, "col", lambda *children, **kw: View(*children, **kw))
    monkeypatch.setattr(app_module.toga, "ScrollContainer", lambda *, content, **kw: View(content, **kw))
    title = View(text="首页", style=SimpleNamespace(font_size=24, margin_bottom=2))
    subtitle = View(text="副标题")
    title_box = View(title, subtitle)
    back_slot = View()
    back_button = View(text="‹")
    back_button._impl = SimpleNamespace(native=Mock())
    header = View(back_slot, title_box, style=SimpleNamespace(padding=16))
    content, navigation = View(), View(text="底部导航")
    result = SimpleNamespace(page="home", _page_token=1, _paint_handle=None,
        _practice_context=False, platform=Mock(), _checkpoint=Mock(),
        header=header, header_title_box=title_box, header_back_slot=back_slot,
        header_back_button=back_button, title_label=title, subtitle_label=subtitle,
        navigation=navigation, content_slot=content, root=View(header, content, navigation))
    result.screen = MethodType(KanaMemory.screen, result)
    return result
