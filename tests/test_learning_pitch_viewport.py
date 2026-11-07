"""Bounded answer viewport: real drafts/store with retained synthetic Canvas."""
from kanamemory import ui_components as component_ui
from copy import deepcopy
from types import SimpleNamespace

import pytest

from kanamemory import pitch_practice as pitch
from kanamemory.pitch_practice_canvas import build_pitch_scene
from kanamemory.learning_store import LearningStore
from support.learning_pitch_sequence_controls_support import registry
from support.pitch_sequence_support import registry as synthetic_registry
from support.learning_pitch_sequence_controls_support import prepare
from support.learning_pitch_sequence_controls_support import act
from support.pitch_sequence_support import CORRECT
from support.learning_fixtures import learning_app
from support.app_fixtures import question_app
from support.app_fixtures import setup_app
from support.app_fixtures import app_module
from support.learning_fixtures import click
from support.learning_fixtures import legacy_dump
from support.learning_pitch_controls_support import diagrams
from support.learning_pitch_controls_support import prepare as prepare_single
from support.fake_ui import View, ScrollView


@pytest.fixture
def retained_viewport(learning_app, monkeypatch):
    app = learning_app
    app.main_window.size = SimpleNamespace(width=360, height=820)
    monkeypatch.setattr(component_ui, 'font_scale', lambda: 2)
    callbacks = []
    tasks = []

    class Tick:
        def __await__(self):
            yield None

    async def sleep(delay):
        assert delay == .05
        await Tick()

    class Task:
        def __init__(self, coroutine):
            self.coroutine, self.cancelled = coroutine, False
            self.callback = self.step

        def step(self):
            if self.cancelled:
                return
            try:
                self.coroutine.send(None)
            except StopIteration:
                return
            callbacks.append(self)

        def cancel(self):
            self.cancelled = True
            self.coroutine.close()

    def create_task(coroutine):
        task = Task(coroutine)
        tasks.append(task)
        task.step()
        return task

    monkeypatch.setattr('kanamemory.learning_pitch_ui.asyncio.sleep', sleep)
    app.loop.create_task = create_task

    def flush(*, include_cancelled=False):
        jobs, callbacks[:] = callbacks[:], []
        for job in jobs:
            if include_cancelled or not job.cancelled:
                job.callback()

    class LaidOutScroll(ScrollView):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.layout = SimpleNamespace(content_height=self.style.height)

    monkeypatch.setattr(app_module.toga, 'ScrollContainer', LaidOutScroll)
    constructions = []

    def diagram(u, **data):
        width = data.pop('width', 312)
        result = View()
        result._pitch_diagram = True
        result.layout = SimpleNamespace(absolute_content_top=80)
        result._pitch_canvas = SimpleNamespace(layout=SimpleNamespace(absolute_content_top=116))
        result.children = [View(text='synthetic retained Canvas')]

        def update(**value):
            scene = build_pitch_scene(**value, width=width, font_scale=u.font_scale())
            result._pitch_layout = scene['layout']
            result._feedback_fragment = deepcopy(value)
            return True

        result._update_pitch = update
        update(**data)
        constructions.append(result)
        return result

    monkeypatch.setattr('kanamemory.pitch_practice_ui.fixed_pitch_diagram', diagram)
    yield app, flush, constructions, callbacks
    for task in tasks:
        task.cancel()


def assert_current_row_visible(app):
    ctx = app._learning_pitch_context
    diagram, viewport = ctx['diagram'], ctx['viewport']
    data = diagram._feedback_fragment
    index = (data.get('active_span') or {}).get('start', data.get('cursor'))
    index = len(data['characters']) - 1 if index is None else index
    cells = [cell for row in diagram._pitch_layout['rows'] for cell in row]
    cell = next((cell for cell in cells if cell['index'] >= index), cells[-1])
    top = 36 + 8 + cell['row'] * diagram._pitch_layout['row_height']
    bottom = top + diagram._pitch_layout['row_content_height']
    assert viewport.vertical_position <= top
    assert bottom <= viewport.vertical_position + viewport.layout.content_height


def test_large_sentence_follows_each_row_then_undo_without_rebuilding_or_grading(retained_viewport, registry):
    app, flush, constructions, _ = retained_viewport
    # Keep this a three-or-more-row scrolling case after compact glyphs allow
    # the same sentence to fit into fewer rows on the normal-width viewport.
    app.main_window.size.width = 260
    item, q = prepare(app, registry)
    before = legacy_dump(app.store)
    units = app.learning_store.get_item(item['id'])['units']
    ctx = app._learning_pitch_context
    diagram, viewport = ctx['diagram'], ctx['viewport']
    assert 240 <= viewport.style.height <= 280
    assert len(diagram._pitch_layout['rows']) >= 3
    geometry = deepcopy(diagram._pitch_layout)
    flush(); assert_current_row_visible(app)
    for action in CORRECT:
        act(app, [action]); flush()
        assert_current_row_visible(app)
        assert ctx['diagram'] is diagram and ctx['viewport'] is viewport
        assert diagram._pitch_layout == geometry
    final_position = viewport.vertical_position
    assert final_position > 0 and len(constructions) == 1
    for _ in CORRECT:
        act(app, ['undo']); flush(); assert_current_row_visible(app)
    assert viewport.vertical_position < final_position
    assert ctx['draft'] == pitch.initial_draft(q)
    assert app.learning_store.get_item(item['id'])['units'] == units
    assert legacy_dump(app.store) == before and not app.learning_store.history()


def test_split_keeps_manual_anchor_and_next_edit_reveals_active_row(retained_viewport, registry):
    app, flush, _, _ = retained_viewport
    _, q = prepare(app, registry)
    act(app, CORRECT[:5]); flush()
    ctx = app._learning_pitch_context
    viewport = ctx['viewport']
    geometry = deepcopy(ctx['diagram']._pitch_layout)
    viewport.vertical_position = 0  # Manual scroll to inspect earlier text.
    act(app, ['split']); flush()
    assert viewport.vertical_position == 0
    assert ctx['diagram']._pitch_layout == geometry
    act(app, ['L']); flush(); assert_current_row_visible(app)
    position = viewport.vertical_position
    act(app, ['split']); flush()
    assert viewport.vertical_position == position
    assert app.learning_store.get_question(q['id'])['draft']['pitch_state']['actions'] == CORRECT[:8]


def test_same_row_input_keeps_position_and_mount_retries_are_bounded(retained_viewport, registry):
    app, flush, _, callbacks = retained_viewport
    prepare(app, registry)
    viewport = app._learning_pitch_context['viewport']
    viewport.layout.content_height = 0
    flush()
    assert len(callbacks) == 1
    viewport.layout.content_height = viewport.style.height
    flush(); assert_current_row_visible(app)
    assert not callbacks
    act(app, CORRECT[:4]); flush()
    position = viewport.vertical_position
    act(app, ['L']); flush()
    assert viewport.vertical_position == position
    # A permanently detached / zero-height surface cannot create an endless timer.
    viewport.layout.content_height = 0
    act(app, ['split', 'L'])
    for _ in range(4): flush()
    assert not callbacks


def test_widget_fallback_viewport_keeps_controls_working(learning_app, registry):
    app = learning_app
    prepare(app, registry)
    act(app, ['L'])
    assert not app._learning_pitch_context['message'].text
    act(app, ['merge'])
    assert not app._learning_pitch_context['message'].text


def test_resumed_partial_and_complete_drafts_reveal_last_row_without_writing(retained_viewport, registry):
    app, flush, _, _ = retained_viewport
    item, q = prepare(app, registry)
    for actions in (CORRECT[:8], CORRECT[8:]):
        act(app, actions); flush()
        saved = deepcopy(app.learning_store.get_question(q['id']))
        units = app.learning_store.get_item(item['id'])['units']
        app.learning_store = LearningStore(app.store)
        app.show_learning_question({'question': saved, 'draft': saved['draft']})
        flush(); assert_current_row_visible(app)
        assert app._learning_pitch_context['viewport'].vertical_position > 0
        assert app.learning_store.get_question(q['id']) == saved
        assert app.learning_store.get_item(item['id'])['units'] == units
    assert not app.learning_store.history()


@pytest.mark.parametrize('change', ['page', 'context', 'question', 'new_edit', 'locked'])
def test_stale_deferred_scroll_does_not_move_the_view(retained_viewport, registry, change):
    app, flush, _, jobs = retained_viewport
    prepare(app, registry)
    act(app, CORRECT[:5])
    ctx = app._learning_pitch_context
    viewport = ctx['viewport']
    old_callback = jobs[-1].callback
    if change == 'page': app.page = 'home'
    elif change == 'context': app._learning_pitch_context = {}
    elif change == 'question': app._learning_question = {}
    elif change == 'new_edit': act(app, ['split'])
    else: ctx['locked'] = True
    viewport.vertical_position = 0
    old_callback()
    assert viewport.vertical_position == 0


def test_same_row_edits_do_not_rescroll_and_short_v1_stays_unwrapped(retained_viewport, monkeypatch):
    app, flush, _, _ = retained_viewport
    # One row at the normal font size; existing v1 is unchanged.
    monkeypatch.setattr(component_ui, 'font_scale', lambda: 1)
    _, q = prepare_single(app, learner_morae=True, segmentation=True)
    assert app._learning_pitch_context.get('viewport') is None
    before = deepcopy(app.learning_store.get_question(q['id']))
    app.show_learning_question({'question': before, 'draft': before['draft']})
    assert app.learning_store.get_question(q['id']) == before
    click(app, '▔'); click(app, '‿'); flush()
    assert app._learning_pitch_context.get('viewport') is None
    assert not app._learning_pitch_context.get('touch_binding')
    assert not app.learning_store.history()


def touch_surface():
    calls = []
    parent = SimpleNamespace(requestDisallowInterceptTouchEvent=calls.append)
    canvas = SimpleNamespace(on_press=None, on_release=None)
    # The canvas native parent is deliberately unavailable: the hook must only
    # reserve ancestors of the viewport, so the inner scroller can intercept.
    diagram = SimpleNamespace(_pitch_canvas=canvas)
    viewport = SimpleNamespace(_impl=SimpleNamespace(native=SimpleNamespace(getParent=lambda: parent)))
    return diagram, viewport, calls


def test_android_viewport_gesture_reserves_outer_page_and_releases_it():
    from kanamemory.learning_pitch_ui import _bind_viewport_gesture
    diagram, viewport, calls = touch_surface()
    context = {}
    _bind_viewport_gesture(context, viewport, diagram, android=True)
    canvas = diagram._pitch_canvas
    handlers = (canvas.on_press, canvas.on_release)
    canvas.on_press(canvas, 20, 20)
    assert calls == [True]
    # Retained rendering does not replace handlers or release an active drag.
    _bind_viewport_gesture(context, viewport, diagram, android=True)
    assert (canvas.on_press, canvas.on_release) == handlers and calls == [True]
    canvas.on_release(canvas, 20, 150)
    assert calls == [True, False]
    canvas.on_release(canvas, x=20, y=150)
    assert calls == [True, False]


def test_replaced_or_removed_viewport_releases_and_detaches_old_hook():
    from kanamemory.learning_pitch_ui import _bind_viewport_gesture
    diagram, viewport, calls = touch_surface()
    context = {}
    _bind_viewport_gesture(context, viewport, diagram, android=True)
    old = diagram._pitch_canvas
    old.on_press(old)
    replacement, another, second_calls = touch_surface()
    _bind_viewport_gesture(context, another, replacement, android=True)
    assert calls == [True, False]
    assert old.on_press is old.on_release is None
    replacement._pitch_canvas.on_press(replacement._pitch_canvas)
    _bind_viewport_gesture(context, None, replacement, android=True)
    assert second_calls == [True, False]
    assert replacement._pitch_canvas.on_press is replacement._pitch_canvas.on_release is None
    assert not context.get('touch_binding')


@pytest.mark.parametrize('android,viewport_present', [(False, True), (True, False)])
def test_non_android_or_unwrapped_chart_does_not_install_native_hook(android, viewport_present):
    from kanamemory.learning_pitch_ui import _bind_viewport_gesture
    diagram, viewport, calls = touch_surface()
    context = {}
    _bind_viewport_gesture(context, viewport if viewport_present else None, diagram, android=android)
    assert not context and not calls
    assert diagram._pitch_canvas.on_press is diagram._pitch_canvas.on_release is None
