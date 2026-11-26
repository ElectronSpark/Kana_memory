"""ui_components.button(selected=True): the chosen one of a row of choices has a look of its own (design 02, 学习库).

The device walk of 0.36.27 saw the 学习库's open tab in the primary style, the same filled green as the page's
action button. A selected button keeps the neutral shape on a light tint of GREEN, its caption GREEN and bold;
an unselected one and the primary action stay as they were; selected and primary together are refused.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from toga.colors import Color

from kanamemory import ui_components as component_ui
from kanamemory.ui_theme import GREEN, NEUTRAL, SELECTED_TINT, WHITE


def _c(value):
    return str(Color.parse(value))


@pytest.fixture
def styles(monkeypatch):
    """The style each fake toga.Button was built with (desktop path: no native calls)."""
    built = []

    def fake_button(caption, **kwargs):
        built.append(kwargs["style"])
        return SimpleNamespace(_impl=SimpleNamespace(set_text=Mock()), refresh=Mock(), style=kwargs["style"])
    monkeypatch.setattr(component_ui, "current_platform", "windows")
    monkeypatch.setattr(component_ui.toga, "Button", fake_button)
    return built


def test_the_selected_look_is_a_tint_with_a_bold_green_caption(styles):
    component_ui.button("单词", Mock(), selected=True)
    style = styles[-1]
    assert (_c(style.background_color), _c(style.color), style.font_weight) == (
        _c(SELECTED_TINT), _c(GREEN), "bold")


def test_unselected_and_primary_buttons_stay_as_they_were(styles):
    component_ui.button("语法", Mock())
    plain = styles[-1]
    assert (_c(plain.background_color), _c(plain.color), plain.font_weight) == (
        _c(NEUTRAL), _c(GREEN), "normal")
    component_ui.button("学习下一项", Mock(), primary=True)
    primary = styles[-1]
    assert (_c(primary.background_color), _c(primary.color), primary.font_weight) == (
        _c(GREEN), _c(WHITE), "normal")
    assert NEUTRAL == "#E4EEE8"   # the neutral background the buttons had before the selected look existed


def test_a_caller_style_still_wins_and_selected_with_primary_is_refused(styles):
    component_ui.button("读音规则", Mock(), selected=True, font_weight="normal")
    assert styles[-1].font_weight == "normal"
    with pytest.raises(ValueError):
        component_ui.button("单词", Mock(), selected=True, primary=True)
