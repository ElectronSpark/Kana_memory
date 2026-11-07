"""Shared reading audio ui fixtures and assertions; no collected scenarios."""



class Node:
    def __init__(self, *children, text="", handler=None, **kwargs):
        self.children, self.text, self.handler = children, text, handler
        self.options = kwargs

    def add(self, *children):
        self.children += children

    def clear(self):
        self.children = ()


def nodes(items):
    for node in items:
        yield node
        yield from nodes(node.children)
