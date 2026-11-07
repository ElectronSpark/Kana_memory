"""A learner's token boundaries never become geometry or hidden answer hints."""
from copy import deepcopy

import pytest

from kanamemory.pitch_practice_ui import fixed_pitch_layout, fixed_pitch_diagram
from kanamemory.pitch_practice_canvas import build_pitch_scene, native_pitch_diagram
from support.pitch_practice_canvas_support import fake_native
from support.pitch_practice_canvas_support import anchors
from support.pitch_practice_ui_support import ui
from support.pitch_practice_ui_support import descendants


def sentence():
    chars = list('きょうは、とうきょうえきへ。')
    return dict(characters=chars, levels=[None] * len(chars), groups=[], tokens=[],
                wrap_units=[{'start': 0, 'end': 5}, {'start': 5, 'end': len(chars)}],
                cursor=0, active_span={'start': 0, 'end': 1}, complete=False)


def split_view():
    data = sentence()
    data.update(levels=['L', 'L', 'H', None, None] + [None] * 9,
                groups=[{'start': 0, 'end': 2}, {'start': 2, 'end': 3}],
                tokens=[{'id': 'a', 'role': 'word', 'start': 0, 'end': 3},
                        {'id': 'b', 'role': 'word', 'start': 3, 'end': 4},
                        {'id': 'p', 'role': 'punctuation', 'start': 4, 'end': 5}],
                cursor=3, active_span={'start': 3, 'end': 4})
    return data


@pytest.mark.parametrize('width,scale', [(160, 1), (320, 1), (360, 2), (420, 3)])
def test_dynamic_token_colors_split_and_undo_never_move_a_character(width, scale):
    states = [sentence(), split_view(), sentence()]
    scenes = [build_pitch_scene(**data, width=width, font_scale=scale) for data in states]
    assert all(scene['layout'] == scenes[0]['layout'] for scene in scenes)
    assert all(anchors(scene) == anchors(scenes[0]) for scene in scenes)
    glyphs = scenes[1]['glyphs']
    assert glyphs[2]['color'] != glyphs[3]['color']
    assert not any(e.get('role') == 'word_boundary' for e in scenes[1]['elements'])
    assert not any(e.get('role') == 'word_boundary' for e in scenes[0]['elements'])
    assert not any(row[0]['character'] in '、。ゃゅょ' for row in scenes[0]['layout']['rows'])
    assert not any(e.get('role') == 'pitch' and e.get('index') in {4, 13}
                   for scene in scenes for e in scene['elements'])
    assert '、 未作答' not in scenes[0]['accessible_description']
    assert not any(e.get('selected') for e in scenes[0]['elements'])


def test_explicit_wrap_units_prefer_public_phrases_not_hidden_word_boundaries():
    chars = list('あいうえおかき')
    public = [{'start': 0, 'end': 3}, {'start': 3, 'end': 7}]
    first = fixed_pitch_layout(chars, wrap_units=public, width=190)
    alternative = fixed_pitch_layout(chars, tokens=[{'start': 0, 'end': 2}, {'start': 2, 'end': 7}],
                                      wrap_units=public, width=190)
    assert first == alternative
    assert [''.join(c['character'] for c in row) for row in first['rows']] == ['あいう', 'えおかき']
    assert fixed_pitch_layout(chars, tokens=[{'start': 0, 'end': 7}], wrap_units=[], width=190) == fixed_pitch_layout(chars, width=190)


def test_native_canvas_reuses_surface_on_split_and_feedback_tracks_exact_public_state(fake_native):
    initial = sentence(); root = native_pitch_diagram(fake_native.u, **initial)
    canvas = root._pitch_canvas; original = anchors(canvas._pitch_scene)
    changed = split_view(); before = deepcopy(changed)
    assert root._update_pitch(**changed)
    assert root._pitch_canvas is canvas and len(fake_native.created) == 1
    assert anchors(canvas._pitch_scene) == original
    assert root._feedback_fragment['tokens'] == changed['tokens']
    assert root._feedback_fragment['wrap_units'] == initial['wrap_units']
    assert 'review' not in root._feedback_fragment and 'answer' not in root._feedback_fragment
    assert changed == before
    assert not root._update_pitch(**{**changed, 'wrap_units': []})
    assert root._update_pitch(**initial)
    assert not any(e.get('role') == 'word_boundary' for e in canvas._pitch_scene['elements'])


def test_widget_fallback_has_same_fixed_geometry_and_only_visible_input_metadata():
    initial = fixed_pitch_diagram(ui(), **sentence())
    changed = fixed_pitch_diagram(ui(), **split_view())
    assert initial._pitch_layout == changed._pitch_layout
    def positions(widget):
        return [(n._pitch_character['index'], n._pitch_character['x'], n._pitch_character['baseline_y'])
                for n in descendants(widget) if hasattr(n, '_pitch_character')]
    assert positions(initial) == positions(changed)
    assert changed._feedback_fragment['tokens'] == split_view()['tokens']


@pytest.mark.parametrize('bad', [[{'start': 0, 'end': 100}], [{'start': 2, 'end': 1}], 'secret'])
def test_invalid_public_wrap_spans_rejected_before_native_drawing(fake_native, bad):
    with pytest.raises(ValueError):
        native_pitch_diagram(fake_native.u, **{**sentence(), 'wrap_units': bad})
    assert not fake_native.created
