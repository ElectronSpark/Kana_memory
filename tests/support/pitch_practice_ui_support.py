"""Shared pitch practice ui fixtures and assertions; no collected scenarios."""
from types import SimpleNamespace


class Node:
    def __init__(self, *children, text='', **style):
        self.children, self.text, self.style = list(children), text, style

    def add(self, *children):
        self.children.extend(children)


def ui(scale=1):
    return SimpleNamespace(
        col=lambda *children, **kw: Node(*children, **kw),
        row=lambda *children, **kw: Node(*children, **kw),
        label=lambda text, **kw: Node(text=text, **kw),
        font_scale=lambda: scale, MUTED='#567', GREEN='#163', WHITE='#fff', INK='#123')


def descendants(node):
    yield node
    for child in node.children:
        yield from descendants(child)


def texts(node):
    return [n.text for n in descendants(node) if n.text]
