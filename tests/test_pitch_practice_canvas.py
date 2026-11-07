"""Canvas updates retain geometry and disclose only caller-supplied pitch."""
from copy import deepcopy
import sys
from types import ModuleType, SimpleNamespace

import pytest

from kanamemory.pitch_practice_canvas import build_pitch_scene, native_pitch_diagram


from support.pitch_practice_canvas_support import view, anchors, fake_native


@pytest.mark.parametrize('width,scale', [(160, 1), (320, 1), (320, 2), (420, 3)])
def test_scene_anchor_geometry_survives_levels_cursor_merge_and_undo(width, scale):
    empty = view()
    started = {**empty, 'levels': ['L', None, None, None], 'groups': [{'start': 0, 'end': 1}],
               'cursor': 1, 'active_span': {'start': 1, 'end': 2}}
    merged = {**started, 'levels': ['L', 'L', None, None], 'groups': [{'start': 0, 'end': 2}],
              'cursor': 2, 'active_span': {'start': 2, 'end': 3}}
    scenes = [build_pitch_scene(**data, width=width, font_scale=scale) for data in (empty, started, merged, started)]
    assert all(anchors(scene) == anchors(scenes[0]) for scene in scenes)
    assert all(scene['layout'] == scenes[0]['layout'] for scene in scenes)
    assert [e for e in scenes[0]['elements'] if e.get('role') in {'tie', 'tie_connector'}] == []
    assert [e for e in scenes[2]['elements'] if e.get('role') == 'tie']
    assert all(g['anchor_x'] >= 8 and g['anchor_x'] + 24 * max(1, scale) <= width - 8
               for scene in scenes for g in scene['glyphs'])
    assert all(0 <= e['baseline_y'] <= scene['height'] for scene in scenes
               for e in scene['elements'] if e['kind'] == 'text')


def test_unentered_levels_and_grouping_are_never_invented_or_accessibly_exposed():
    data = view(); before = deepcopy(data)
    scene = build_pitch_scene(**data)
    assert data == before
    assert not any(e.get('selected') for e in scene['elements'])
    assert not any(e.get('role') in {'connector', 'tie', 'count'} for e in scene['elements'])
    assert '已合拍' not in scene['accessible_description']
    assert '调号' not in scene['accessible_description']
    assert '词拍数' not in scene['accessible_description']
    assert scene['accessible_description'].count('未作答') == 4
    assert '助词 が 未作答' in scene['accessible_description']
    assert '当前位置：き' in scene['accessible_description']


def test_authored_counts_exclude_particle_and_connectors_stay_on_same_row():
    data = view()
    data.update(levels=['L', 'L', 'H', 'L'],
                groups=[{'start': 0, 'end': 2}, {'start': 2, 'end': 3}, {'start': 3, 'end': 4}],
                cursor=None, active_span=None, complete=True, number=2)
    scene = build_pitch_scene(**data, width=160, font_scale=2, show_counts=True)
    assert scene['word_counts'] == [2]
    assert [(e['index'], e['text']) for e in scene['elements'] if e.get('role') == 'count'] == [(0, '1'), (2, '2')]
    assert '词拍数：2' in scene['accessible_description']
    assert '调号 2' in scene['accessible_description']
    assert '当前位置' not in scene['accessible_description']
    for element in scene['elements']:
        if element.get('role') in {'connector', 'guide_connector', 'tie_connector'}:
            assert 8 <= element['x1'] <= element['x2'] <= 160 - 8


def test_tokens_and_punctuation_drive_wraps_not_pitch_values():
    chars = list('あいう、えお。')
    tokens = [{'start': 0, 'end': 3, 'role': 'word'}, {'start': 4, 'end': 6, 'role': 'word'}]
    empty = build_pitch_scene(chars, [None] * len(chars), tokens=tokens, width=210)
    filled = build_pitch_scene(chars, ['H', 'L', 'H', None, 'L', 'H', None], tokens=tokens, width=210)
    assert anchors(empty) == anchors(filled)
    assert not any(row[0]['character'] in '、。' for row in empty['layout']['rows'])
    glyphs = [e for e in empty['elements'] if e.get('role') == 'glyph']
    assert glyphs[0]['color'] != glyphs[4]['color']


@pytest.mark.parametrize('width,scale', [(160, 1), (198, 1), (320, 2), (360, 2)])
@pytest.mark.parametrize('word,morae,actions', [
    ('橋脚', ['きょ', 'う', 'きゃ', 'く'], ['H', 'merge', 'L', 'H', 'merge', 'undo', 'L', 'undo', 'clear']),
    ('特急', ['と', 'っ', 'きゅ', 'う'], ['H', 'L', 'H', 'merge', 'undo', 'merge', 'L', 'undo', 'clear']),
])
def test_small_kana_wraps_remain_fixed_through_real_advanced_input_and_undo(width, scale, word, morae, actions):
    from kanamemory import pitch_practice as pitch
    from support.pitch_practice_support import question

    q = question(learner=True, morae=morae, word=word, particle='')
    draft = pitch.initial_draft(q)
    original = build_pitch_scene(**pitch.fixed_view(q, draft), width=width, font_scale=scale)
    assert not any(e.get('role') in {'tie', 'tie_connector', 'count'} for e in original['elements'])
    assert not any(row[0]['character'] in 'ゃゅょっ' for row in original['layout']['rows'])
    assert '已合拍' not in original['accessible_description']
    for action in actions:
        draft = pitch.editor_action(q, draft, action)
        data = pitch.fixed_view(q, draft)
        scene = build_pitch_scene(**data, width=width, font_scale=scale)
        assert anchors(scene) == anchors(original)
        assert scene['layout'] == original['layout']
        assert scene['data']['groups'] == data['groups']
        assert scene['data']['levels'] == data['levels']
    assert scene['data']['groups'] == []
    assert scene['data']['levels'] == [None] * len(''.join(morae))
    assert '已合拍' not in scene['accessible_description']


@pytest.mark.parametrize('bad', [
    {'levels': ['H']}, {'complete': 1}, {'show_counts': 1}, {'label': 1},
    {'groups': [{'start': 0, 'end': 5}]}, {'tokens': [{'start': 3, 'end': 2}]},
])
def test_invalid_data_is_rejected_without_drawing(bad):
    with pytest.raises(ValueError):
        build_pitch_scene(**{**view(), **bad})


def test_native_update_reuses_one_canvas_and_has_no_redraw_for_identical_data(fake_native):
    env = fake_native; data = view()
    root = native_pitch_diagram(env.u, **data)
    canvas = root._pitch_canvas
    old_anchors = anchors(canvas._pitch_scene)
    draws = canvas.redraws
    assert root._update_pitch(**data)
    assert canvas.redraws == draws
    updated = {**data, 'levels': ['L', None, None, None],
               'groups': [{'start': 0, 'end': 1}], 'cursor': 1,
               'active_span': {'start': 1, 'end': 2}}
    saved = deepcopy(updated)
    assert root._update_pitch(**updated)
    assert updated == saved
    assert canvas.redraws == draws + 1
    assert anchors(canvas._pitch_scene) == old_anchors
    assert len(env.created) == len(env.boxes) == 1
    assert len(env.labels) == 1
    assert canvas.importance == 1
    assert 'き 低' in canvas.description and 'ょ 未作答' in canvas.description
    assert root._feedback_fragment == {'kind': 'fixed_pitch_diagram', **updated, 'wrap_units': None, 'show_counts': False}


@pytest.mark.parametrize('replacement', [
    {'characters': list('きょうは')}, {'label': '別'}, {'number': 0}, {'show_counts': True},
    {'tokens': [{'start': 0, 'end': 4, 'role': 'word'}]},
])
def test_identity_change_requests_rebuild_without_mutating_old_view(fake_native, replacement):
    data = view(); root = native_pitch_diagram(fake_native.u, **data)
    before = deepcopy(root._feedback_fragment); scene = root._pitch_canvas._pitch_scene
    assert root._update_pitch(**{**data, **replacement}) is False
    assert root._feedback_fragment == before and root._pitch_canvas._pitch_scene is scene
    assert len(fake_native.created) == 1


def test_resize_and_font_scale_recompute_scene_without_android_double_scaling(fake_native):
    env = fake_native; data = view()
    root = native_pitch_diagram(env.u, **data); canvas = root._pitch_canvas
    env.scale[0] = 2
    canvas.on_resize(canvas, 220, 999)
    assert root._pitch_layout['width'] == 220
    assert len(root._pitch_layout['rows']) > 1
    glyphs = [e for e in canvas._pitch_scene['elements'] if e.get('role') == 'glyph']
    assert all(e['size'] == 48 for e in glyphs)
    assert all(f['size'] == 18 for f in env.fonts if f['family'] == 'serif')
    assert all(f['size'] == 10 for f in env.fonts if f['family'] == 'sans-serif')
    assert len(env.created) == len(env.boxes) == 1
    assert root._feedback_fragment['characters'] == data['characters']
    assert canvas.style.height == canvas._pitch_scene['height']


def test_many_characters_still_have_constant_native_widget_count(fake_native):
    chars = list('あいうえお' * 20)
    root = native_pitch_diagram(fake_native.u, chars, [None] * len(chars),
        tokens=[{'start': 0, 'end': len(chars), 'role': 'word'}],
        label='表示', number=0, show_counts=True)
    assert root._pitch_diagram
    assert len(fake_native.created) == 1 and len(fake_native.boxes) == 1
    assert len(fake_native.labels) == 3
    assert len(root._pitch_canvas._pitch_scene['glyphs']) == len(chars)


def test_reduced_system_font_scale_keeps_same_native_point_size(fake_native):
    env = fake_native
    env.scale[0] = .75
    root = native_pitch_diagram(env.u, **view())
    assert all(f['size'] == pytest.approx(18) for f in env.fonts if f['family'] == 'serif')
    assert all(f['size'] == pytest.approx(10) for f in env.fonts if f['family'] == 'sans-serif')
    glyphs = [e for e in root._pitch_canvas._pitch_scene['elements'] if e.get('role') == 'glyph']
    assert all(e['size'] == 18 for e in glyphs)  # 18pt * 4/3 DIP * .75.
