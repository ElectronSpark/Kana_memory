"""Production renderer selects Canvas only for Android, retaining old adapters."""
from copy import deepcopy
import sys
from types import ModuleType

from kanamemory.pitch_practice_ui import fixed_pitch_diagram, pitch_diagram
from support.pitch_practice_canvas_support import fake_native
from support.pitch_practice_canvas_support import view
from support.pitch_practice_canvas_support import anchors
from support.pitch_practice_ui_support import ui
from support.pitch_practice_ui_support import descendants


def test_android_entrypoint_reuses_canvas_and_live_feedback_without_native_cell_tree(fake_native):
    env = fake_native
    data = view()
    widget = fixed_pitch_diagram(env.u, **data)
    original = anchors(widget._pitch_canvas._pitch_scene)
    assert len(env.created) == 1
    assert len(env.boxes) == 1
    changed = {**data, 'levels': ['L', None, None, None],
               'groups': [{'start': 0, 'end': 1}], 'cursor': 1,
               'active_span': {'start': 1, 'end': 2}}
    before = deepcopy(changed)
    assert widget._update_pitch(**changed) is True
    assert changed == before
    assert anchors(widget._pitch_canvas._pitch_scene) == original
    assert widget._feedback_fragment['levels'] == changed['levels']
    assert len(env.created) == len(env.boxes) == 1
    assert widget._update_pitch(**{**changed, 'label': '另一题'}) is False


def test_legacy_teaching_adapter_uses_canvas_and_keeps_original_mora_metadata(fake_native):
    widget = pitch_diagram(fake_native.u, ['きょ', 'う', 'が'], ['H', 'L', 'L'],
                           word_mora_count=2, number=1, show_counts=True)
    assert widget._pitch_canvas._pitch_scene['word_counts'] == [2]
    assert widget._feedback_fragment['morae'] == ['きょ', 'う', 'が']
    assert widget._feedback_fragment['word_mora_count'] == 2
    assert widget._feedback_fragment['characters'] == list('きょうが')
    assert widget._feedback_fragment['number'] == 1


def test_non_android_keeps_headless_widget_adapter(monkeypatch):
    platform = ModuleType('toga.platform')
    platform.current_platform = 'windows'
    monkeypatch.setitem(sys.modules, 'toga.platform', platform)
    widget = fixed_pitch_diagram(ui(), **view())
    assert not hasattr(widget, '_pitch_canvas')
    assert len([n for n in descendants(widget) if hasattr(n, '_pitch_character')]) == 4
