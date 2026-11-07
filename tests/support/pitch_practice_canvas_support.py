"""Shared pitch practice canvas fixtures and assertions; no collected scenarios."""
from types import ModuleType
from types import SimpleNamespace
import pytest
import sys


def view():
    return {'characters': list('きょうが'), 'levels': [None] * 4, 'groups': [],
            'tokens': [{'start': 0, 'end': 3, 'role': 'word', 'id': 'w'},
                       {'start': 3, 'end': 4, 'role': 'particle', 'id': 'p'}],
            'cursor': 0, 'active_span': {'start': 0, 'end': 1},
            'complete': False, 'label': '例', 'number': None}


def anchors(scene):
    return [(g['index'], g['character'], g['row'], g['anchor_x'], g['text_baseline_y'])
            for g in scene['glyphs']]


@pytest.fixture
def fake_native(monkeypatch):
    created, fonts = [], []

    class Node:
        def __init__(self, *children, text='', **style):
            self.children, self.text, self.style = list(children), text, SimpleNamespace(**style)

    class Canvas:
        def __init__(self, *, on_resize, style):
            self.children = []
            self.on_resize, self.style = on_resize, style
            self.root_state = SimpleNamespace(drawing_actions=[])
            self.redraws = 0
            self._impl = SimpleNamespace(native=SimpleNamespace(
                setContentDescription=lambda text: setattr(self, 'description', text),
                setImportantForAccessibility=lambda value: setattr(self, 'importance', value)))
            created.append(self)

        def redraw(self):
            self.redraws += 1

        def measure_text(self, *args, **kwargs):
            raise AssertionError('A bold-dependent measurement must not move fixed glyphs')

    class Action:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs, self.drawing_actions = args, kwargs, []

    def font(**kwargs):
        fonts.append(kwargs)
        return SimpleNamespace(**kwargs)

    modules = {
        'toga': {'Canvas': Canvas, 'Font': font},
        'toga.fonts': {'BOLD': 'bold', 'NORMAL': 'normal'},
        'toga.platform': {'current_platform': 'android'},
        'toga.style': {'Pack': lambda **kw: SimpleNamespace(**kw)},
        'toga.widgets.canvas': {name: Action for name in ['Fill', 'Stroke', 'FillText', 'Rect', 'MoveTo', 'LineTo']},
    }
    for name, values in modules.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    scale = [1]
    labels, boxes = [], []
    def label(text, **kw):
        node = Node(text=text, **kw); labels.append(node); return node
    def col(*children, **kw):
        node = Node(*children, **kw); boxes.append(node); return node
    u = SimpleNamespace(label=label, col=col, font_scale=lambda: scale[0],
                        INK='#123', MUTED='#567', GREEN='#163', WHITE='#fff')
    return SimpleNamespace(u=u, created=created, fonts=fonts, scale=scale, labels=labels, boxes=boxes)
