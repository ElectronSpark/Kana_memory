"""Shared kana table batches fixtures and assertions; no collected scenarios."""
from kanamemory import ui_components as component_ui
from unittest.mock import Mock
from types import SimpleNamespace
from kanamemory import app as app_module
import pytest


class View:
    def __init__(self, *children, kind="col", text="", handler=None, trace=None, **style):
        self.children = list(children)
        self.kind, self.text, self.on_press = kind, text, handler
        self.trace = trace if trace is not None else []
        self.style = style

    def add(self, *children):
        self.trace.append(("attach", self, children))
        self.children.extend(children)

    def remove(self, child):
        self.children.remove(child)

    def clear(self):
        self.children.clear()


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


@pytest.fixture
def builder(monkeypatch):
    trace, native_buttons = [], []
    def make_button(text, handler, **style):
        node = View(kind="button", text=text, handler=handler, trace=trace, **style)
        native_buttons.append(node)
        trace.append(("build", node))
        return node
    monkeypatch.setattr(component_ui, "button", make_button)
    monkeypatch.setattr(component_ui, "col", lambda *nodes, **style: View(*nodes, trace=trace, **style))
    monkeypatch.setattr(component_ui, "row", lambda *nodes, **style: View(*nodes, kind="row", trace=trace, **style))
    monkeypatch.setattr(component_ui, "label", lambda text, **style: View(kind="label", text=text, trace=trace, **style))
    app = SimpleNamespace(
        kana_grid=View(trace=trace), kana_filter=SimpleNamespace(value="全部"),
        main_window=SimpleNamespace(size=SimpleNamespace(width=420)),
        heading=lambda text: View(kind="heading", text=text, trace=trace),
        note=lambda text: View(kind="note", text=text, trace=trace),
        open_table_kana=Mock(), open_character_readings=Mock(), go_back=Mock(), screen=Mock(), _checkpoint=Mock(),
    )
    return app, trace, native_buttons
