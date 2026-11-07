"""A source word owns an entered track, never one pitch slot per kanji."""
from copy import deepcopy
from types import SimpleNamespace
import sys

import pytest

from kanamemory.pitch_alignment_canvas import (
    alignment_scene, alignment_diagram, native_alignment_diagram, validate_surface)
from kanamemory.pitch_practice_ui import fixed_pitch_diagram
from support.pitch_practice_canvas_support import fake_native
from support.pitch_practice_ui_support import ui
from support.pitch_practice_ui_support import descendants


from support.pitch_alignment_canvas_support import display, cells, anchors


@pytest.mark.parametrize('word,levels', [('雨', ['H', 'L']), ('失礼', ['L', 'H', 'H', 'H']),
    ('失礼します', ['L', 'H', 'H', 'H', 'H', 'L', 'L'])])
def test_source_word_remains_whole_and_track_length_is_independent(word, levels):
    scene = alignment_scene(**display((word,), entered={0: levels}), width=320, font_scale=2)
    assert [g['text'] for g in scene['glyphs']] == [word]
    marks = [e for e in scene['elements'] if e.get('role') == 'pitch']
    assert [e['level'] for e in marks] == levels
    assert scene['layout']['content_width'] == 320
    assert len(scene['layout']['rows']) == 1
    assert cells(scene)[0]['active']  # The renderer never advances on an expected count.


def test_empty_track_has_no_preallocated_marks_or_reading_in_any_display_metadata():
    data = display(('雨', '。'))
    data['surface']['units'][0].update(reading='あめ', expected_mora_count=2)
    data['surface']['expected_levels'] = ['H', 'L']
    before = deepcopy(data)
    scene = alignment_scene(**data)
    assert data == before
    assert not [e for e in scene['elements'] if e.get('role') in ('pitch', 'connector', 'count')]
    assert anchors(scene) == [('雨。', 12, 70)]
    assert 'あめ' not in repr(scene) and 'expected' not in repr(scene)
    assert '2 拍' not in scene['accessible_description']


@pytest.mark.parametrize('width,scale', [(160, 1), (160, 3), (312, 2), (420, 3)])
def test_level_changes_preserve_word_baselines_and_whole_word_wraps(width, scale):
    a = display(('また', '雨だ', '。'), entered={0: ['H', 'L'], 1: ['L', 'H', 'L']}, cursor=1)
    b = deepcopy(a)
    b['surface']['units'][0]['levels'] = ['L', 'H']
    b['surface']['units'][1]['levels'] = ['H', 'L', 'H']
    first, second = (alignment_scene(**v, width=width, font_scale=scale) for v in (a, b))
    assert anchors(first) == anchors(second)
    assert [(c['left'], c['right'], c['top'], c['bottom']) for c in cells(first)] == [
        (c['left'], c['right'], c['top'], c['bottom']) for c in cells(second)]
    assert [g['text'] for g in first['glyphs']] == ['また', '雨だ。']
    assert all(c['right'] <= first['layout']['content_width'] - 8 for c in cells(first))


@pytest.mark.parametrize('width,scale', [(160, 1), (312, 2), (420, 3)])
def test_thirty_two_entered_morae_use_one_safe_horizontal_block_without_splitting_source(width, scale):
    scene = alignment_scene(**display(('雨', '降る'), entered={0: ['H', 'L'] * 16}),
                            width=width, font_scale=scale)
    first, second = cells(scene)
    assert first['row'] == 0 and second['row'] == 1
    assert first['right'] > width and scene['layout']['content_width'] == first['right'] + 8
    assert first['focus_x'] > width and first['focus_x'] < first['right']
    assert second['left'] == 8
    assert len(scene['glyphs']) == 2
    assert [g['text'] for g in scene['glyphs']] == ['雨', '降る']
    assert len([e for e in scene['elements'] if e.get('role') == 'pitch']) == 32
    assert first['bottom'] - first['top'] == second['bottom'] - second['top']


def test_appending_and_undo_only_reflows_using_actual_entered_length():
    first = alignment_scene(**display(('雨', '失礼')))
    extended = alignment_scene(**display(('雨', '失礼'), entered={0: ['H'] * 32}))
    undone = alignment_scene(**display(('雨', '失礼')))
    assert anchors(first) == anchors(undone)
    assert cells(first)[0]['source_x'] == cells(extended)[0]['source_x']
    assert cells(first)[0]['source_baseline'] == cells(extended)[0]['source_baseline']
    assert cells(extended)[1]['row'] > cells(first)[1]['row']


def test_completed_answer_uses_only_supplied_levels_and_has_no_active_word():
    scene = alignment_scene(**display(('雨', 'だ', '。'), entered={0: ['H', 'L'], 1: ['L']}, complete=True), show_counts=True)
    assert not [e for e in scene['elements'] if e.get('role') == 'active_source']
    assert all(not e['bold'] for e in scene['glyphs'])
    assert '2 拍' in scene['accessible_description'] and '1 拍' in scene['accessible_description']
    assert '当前位置' not in scene['accessible_description']


def test_equal_pitch_morae_remain_separate_visible_marks_above_unsplit_word():
    scene = alignment_scene(**display(('失礼',), entered={0: ['H'] * 4}))
    marks = [e for e in scene['elements'] if e.get('role') == 'pitch']
    assert len(marks) == 4
    assert all(a['x2'] < b['x1'] for a, b in zip(marks, marks[1:]))
    assert not [e for e in scene['elements'] if e.get('role') == 'connector']
    assert [g['text'] for g in scene['glyphs']] == ['失礼']


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(version=2), lambda d: d.update(cursor=[]),
    lambda d: d.update(complete=1), lambda d: d['surface'].update(units={}),
    lambda d: d['surface']['units'][0].update(role=[]),
    lambda d: d['surface']['units'][0].update(levels=['H'] * 33),
    lambda d: d['surface']['units'][0].update(levels=[None]),
    lambda d: d['surface']['units'][0].update(active=False),
    lambda d: d['surface']['units'][0].update(text='あめ'),
    lambda d: d['surface']['units'][1].update(start=0),
    lambda d: d['surface']['units'][-1].update(levels=['H']),
])
def test_malformed_public_surface_is_rejected_as_value_error(mutation):
    data = display(); mutation(data)
    with pytest.raises(ValueError):
        alignment_scene(**data)


def test_native_retains_one_canvas_and_does_not_reinterpret_content_width_as_viewport(fake_native):
    env = fake_native
    env.scale[0] = 2
    data = display(('雨', '失礼します'))
    root = native_alignment_diagram(env.u, **data, width=312)
    canvas = root._pitch_canvas
    assert len(env.created) == len(env.boxes) == 1 and not env.labels  # No duplicate full-source title.
    draws = canvas.redraws
    assert root._update_pitch(**data, width=312) and canvas.redraws == draws
    entered = display(('雨', '失礼します'), entered={0: ['H'] * 32})
    assert root._update_pitch(**entered, width=312)
    wide = canvas.style.width
    assert wide > 312 and root._pitch_layout['width'] == 312
    canvas.on_resize(canvas, wide, canvas.style.height)
    assert root._pitch_layout['width'] == 312
    assert len(root._pitch_layout['rows']) == 2
    assert canvas is root._pitch_canvas and len(env.created) == 1
    assert canvas.importance == 1 and '雨：高' in canvas.description
    assert root._feedback_fragment['surface']['units'][0]['levels'] == ['H'] * 32
    assert root._update_pitch(**data, width=312)
    assert canvas.style.width == 312
    assert all(f['size'] == 18 for f in env.fonts)


def test_native_identity_change_declines_update_and_resize_uses_explicit_available_width(fake_native):
    root = native_alignment_diagram(fake_native.u, **display(('雨', '失礼')), width=320)
    before = deepcopy(root._feedback_fragment)
    assert not root._update_pitch(**display(('雪', '失礼')), width=320)
    assert root._feedback_fragment == before
    assert root._update_pitch(**display(('雨', '失礼')), width=160)
    assert root._pitch_layout['width'] == 160


def test_public_router_uses_new_surface_only_for_v3_and_desktop_source_stays_whole():
    value = display(('失礼', 'します', '。'), entered={0: ['L', 'H', 'H', 'H']})
    root = fixed_pitch_diagram(ui(), **value)
    assert root._feedback_fragment['version'] == 3
    source = [n.text for n in descendants(root) if n.text]
    assert source == ['失礼', 'します。']
    assert root._pitch_layout['surface']


@pytest.mark.parametrize('entered', [[], ['H', 'L'] * 16])
def test_static_narrow_result_scrolls_whole_source_without_duplicate_feedback(fake_native, monkeypatch, entered):
    env = fake_native
    env.scale[0] = 2
    created = []

    def scroll(**kwargs):
        node = SimpleNamespace(**kwargs)
        node.children = [node.content]
        created.append(node)
        return node

    monkeypatch.setattr(sys.modules['toga'], 'ScrollContainer', scroll, raising=False)
    data = display(('失礼します',), entered={0: entered}, complete=True)
    root = fixed_pitch_diagram(env.u, **data, width=160)
    assert len(created) == 1
    viewport = root._pitch_static_viewport
    assert viewport.horizontal and not viewport.vertical and viewport.style.width == 160
    assert not getattr(root, '_pitch_diagram', False)
    diagram = viewport.content
    assert diagram._pitch_diagram and diagram._pitch_layout['content_width'] > 160
    assert viewport.style.height >= diagram._pitch_canvas._pitch_scene['height']
    assert [g['text'] for g in diagram._pitch_canvas._pitch_scene['glyphs']] == ['失礼します']
    assert diagram._feedback_fragment['surface']['units'][0]['levels'] == entered
    # The editing controller takes responsibility for both axes instead.
    owned = fixed_pitch_diagram(env.u, **data, width=160, viewport_owned=True)
    assert owned._pitch_diagram and len(created) == 1


def test_short_static_surface_does_not_add_native_wrapper(fake_native, monkeypatch):
    monkeypatch.setattr(sys.modules['toga'], 'ScrollContainer',
        lambda **kwargs: pytest.fail('No viewport needed for this short source'), raising=False)
    root = fixed_pitch_diagram(fake_native.u, **display(('雨',), entered={0: ['H', 'L']}, complete=True), width=320)
    assert root._pitch_diagram
