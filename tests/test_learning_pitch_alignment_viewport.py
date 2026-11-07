"""Actual controller edits keep a real Travertino content tree rooted once."""
from kanamemory import ui_components as component_ui
from copy import deepcopy
from types import SimpleNamespace

import pytest
from toga.style import Pack
from travertino.node import Node

from kanamemory.pitch_alignment_canvas import alignment_scene
from kanamemory.learning_store import LearningStore
from support.pitch_alignment_support import registry as alignment_registry
from support.learning_pitch_alignment_controls_support import registry
from support.learning_fixtures import learning_app
from support.app_fixtures import question_app
from support.app_fixtures import setup_app
from support.app_fixtures import app_module
from support.learning_pitch_alignment_controls_support import prepare
from support.learning_pitch_sequence_controls_support import act
from support.learning_fixtures import legacy_dump
from support.fake_ui import View, ScrollView


def node_of(view):
    if not hasattr(view, 'tree_node'):
        view.tree_node = Node(Pack(), children=[])
    return view.tree_node


class TreeBox(View):
    """Use the installed tree's actual add/remove root semantics, not a mock."""
    def __init__(self, *children, **kwargs):
        super().__init__(*children, **kwargs)
        self.tree_node = Node(Pack(direction='column'), children=[node_of(c) for c in children])

    def add(self, *children):
        for child in children:
            self.tree_node.add(node_of(child))
        super().add(*children)

    def clear(self):
        self.tree_node.clear()
        super().clear()

    def remove(self, child):
        self.tree_node.remove(node_of(child))
        super().remove(child)


@pytest.fixture
def rooted_controls(learning_app, monkeypatch):
    app = learning_app
    app.main_window.size = SimpleNamespace(width=360, height=820)
    monkeypatch.setattr(component_ui, 'font_scale', lambda: 2)
    monkeypatch.setattr(component_ui, 'col', TreeBox)
    monkeypatch.setattr(component_ui, 'row', TreeBox)
    diagrams, viewports = [], []

    class Scroll(TreeBox, ScrollView):
        def __init__(self, *, content=None, **kwargs):
            # Like real Toga, ScrollContainer.content is a separate layout root,
            # not a Node child. Assigning it does not repair descendant roots.
            View.__init__(self, **kwargs)
            self.tree_node = Node(Pack(), children=[])
            self.content = content
            self.vertical_position = self.horizontal_position = 0
            self.layout = SimpleNamespace(content_height=self.style.height, content_width=312)
            viewports.append(self)

        content = ScrollView.content

    monkeypatch.setattr(app_module.toga, 'ScrollContainer', Scroll)

    def diagram(u, *, width=312, viewport_owned=False, **data):
        canvas = View()
        canvas.tree_node = Node(Pack(width=width, height=1))
        canvas.layout = SimpleNamespace(absolute_content_top=0)
        result = TreeBox(canvas)
        result.layout = SimpleNamespace(absolute_content_top=0)
        result._pitch_diagram, result._pitch_canvas = True, canvas

        def update(**value):
            available = value.pop('width', width)
            scene = alignment_scene(**value, width=available, font_scale=u.font_scale())
            canvas.tree_node.style.width = scene['layout']['content_width']
            canvas.tree_node.style.height = scene['height']
            result._pitch_layout = scene['layout']
            result._feedback_fragment = deepcopy(value)
            return True

        result._update_pitch = update
        update(**data)
        diagrams.append(result)
        return result

    monkeypatch.setattr('kanamemory.pitch_practice_ui.fixed_pitch_diagram', diagram)

    def close_task(coroutine):
        coroutine.close()
        return SimpleNamespace(cancel=lambda: None)

    app.loop.create_task = close_task
    return app, diagrams, viewports


def assert_rooted(context, diagram, viewport):
    assert context['diagram'] is diagram and context['viewport'] is viewport
    assert viewport.content is diagram
    canvas = diagram._pitch_canvas.tree_node
    assert canvas.parent is diagram.tree_node
    assert canvas.root is diagram.tree_node
    assert diagram.tree_node.parent is None


def test_actual_controller_wide_threshold_and_undo_never_detach_canvas_root(rooted_controls, registry):
    app, diagrams, viewports = rooted_controls
    item, q = prepare(app, registry)
    context = app._learning_pitch_context
    diagram, viewport = context['diagram'], context.get('viewport')
    assert viewport is not None  # v3 has one stable content owner from first mount.
    assert_rooted(context, diagram, viewport)
    units = deepcopy(app.learning_store.get_item(item['id'])['units'])
    legacy = legacy_dump(app.store)
    original_geometry = deepcopy(diagram._pitch_layout)
    # 今日 uses two beats, then a single written 雨 receives an intentionally
    # excessive track. Reparenting at this threshold is the native regression.
    actions = ['L', 'H', 'split'] + ['H', 'L'] * 12
    for value in actions:
        act(app, [value])
        assert not context['message'].text
        assert_rooted(context, diagram, viewport)
    assert diagram._pitch_layout['content_width'] > 312
    assert app.learning_store.get_question(q['id'])['draft']['pitch_state']['actions'] == actions
    for _ in actions:
        act(app, ['undo'])
        assert_rooted(context, diagram, viewport)
    assert diagram._pitch_layout == original_geometry
    assert len(diagrams) == len(viewports) == 1
    assert app.learning_store.get_item(item['id'])['units'] == units
    assert legacy_dump(app.store) == legacy
    assert not app.learning_store.history()


def test_wide_partial_resume_owns_one_root_and_read_does_not_mutate_draft(rooted_controls, registry):
    app, diagrams, viewports = rooted_controls
    item, q = prepare(app, registry)
    act(app, ['L', 'H', 'split'] + ['H'] * 24)
    saved = deepcopy(app.learning_store.get_question(q['id']))
    units = deepcopy(app.learning_store.get_item(item['id'])['units'])
    app.learning_store = LearningStore(app.store)
    app.show_learning_question({'question': saved, 'draft': saved['draft']})
    context = app._learning_pitch_context
    assert_rooted(context, context['diagram'], context['viewport'])
    assert context['diagram']._pitch_layout['content_width'] > 312
    assert app.learning_store.get_question(q['id']) == saved
    act(app, ['undo'])
    assert_rooted(context, context['diagram'], context['viewport'])
    assert len(diagrams) == len(viewports) == 2
    assert app.learning_store.get_item(item['id'])['units'] == units
    assert not app.learning_store.history()


def test_installed_tree_explains_why_removed_subtree_needs_stable_content_owner():
    canvas = Node(Pack(width=312, height=176))
    diagram = Node(Pack(direction='column'), children=[canvas])
    holder = Node(Pack(direction='column'), children=[diagram])
    assert canvas.root is holder
    holder.remove(diagram)
    # This is the actual installed framework behavior. A native content setter
    # alone does not perform Node.add and therefore does not repair this root.
    assert canvas.parent is diagram and canvas.root is canvas
    wrapper = Node(Pack(direction='column'), children=[diagram])
    assert canvas.root is wrapper
