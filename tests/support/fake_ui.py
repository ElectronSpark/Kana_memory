"""In-memory controls and tree traversal, with no App or native UI imports."""

class View:
    def __init__(self, *children, text="", items=None, value=None, on_change=None,
                 on_press=None, enabled=True, style=None, **kwargs):
        self.children = list(children)
        self.text = text
        self.items = list(items or [])
        self.value = self.items[0] if value is None and self.items else value
        self.on_change, self.on_press = on_change, on_press
        self.enabled = enabled
        self.style = style

    def add(self, *children):
        self.children.extend(children)

    def remove(self, child):
        self.children.remove(child)

    def clear(self):
        self.children.clear()

class ScrollView(View):
    def __init__(self, *, content=None, **kwargs):
        super().__init__(**kwargs)
        self.content = content
        self.vertical_position = 0

    @property
    def content(self):
        return self.children[0] if self.children else None

    @content.setter
    def content(self, value):
        self.children = [value] if value is not None else []

def descendants(node):
    yield node
    for child in node.children:
        yield from descendants(child)

def text_of(node):
    return "\n".join(str(item.text) for item in descendants(node))
