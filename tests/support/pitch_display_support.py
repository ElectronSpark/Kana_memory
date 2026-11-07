"""Shared pitch display fixtures and assertions; no collected scenarios."""



class Node:
    def __init__(self, kind, *children, text="", **style):
        self.kind, self.children, self.text, self.style = kind, list(children), str(text), style


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)
