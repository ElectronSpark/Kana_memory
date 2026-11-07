"""Shared sentence annotation ui fixtures and assertions; no collected scenarios."""
from kanamemory import ui_components as component_ui
from unittest.mock import Mock
from types import SimpleNamespace
from copy import deepcopy
import pytest
from kanamemory import sentence_annotation_ui as renderer


class Node:
    def __init__(self, kind, *children, text="", **style):
        self.kind, self.children, self.text, self.style = kind, list(children), str(text), style


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


@pytest.fixture
def app(monkeypatch):
    from kanamemory import app as u, example_highlight, text_layout
    monkeypatch.setattr(component_ui, "label", lambda text, **style: Node("label", text=text, **style))
    monkeypatch.setattr(component_ui, "row", lambda *children, **style: Node("row", *children, **style))
    monkeypatch.setattr(component_ui, "col", lambda *children, **style: Node("col", *children, **style))
    monkeypatch.setattr(example_highlight, "current_platform", "windows")
    monkeypatch.setattr(text_layout, "font_scale", lambda: 1)
    return SimpleNamespace(main_window=SimpleNamespace(size=SimpleNamespace(width=360)),
                           platform=SimpleNamespace(link_kana_label=Mock()))


def use_plan(monkeypatch, text, segments):
    source = dict(text=text, segments=deepcopy(segments))
    monkeypatch.setattr(renderer, "_annotation_plan", lambda _: source)
    return source


def labels(result):
    return [n for n in walk(result) if n.kind == "label"]


def bodies(result):
    return [n for n in labels(result) if hasattr(n, "_sentence_text_range")]


def annotations(result):
    return [n for n in labels(result) if hasattr(n, "_sentence_annotation_range")]
