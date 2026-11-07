"""Written-word beat controls exercise real drafts, feedback and scheduling."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import json

import pytest

from kanamemory import pitch_practice as pitch
from kanamemory.learning_questions import build_learning_question
from kanamemory.learning_store import LearningStore
from kanamemory.learning_ui import LEARNING_PREFS
from support.pitch_alignment_support import registry as alignment_registry
from support.pitch_alignment_support import lesson
from support.pitch_alignment_support import CORRECT
from support.learning_fixtures import learning_app
from support.app_fixtures import question_app
from support.app_fixtures import setup_app
from support.app_fixtures import app_module
from support.learning_fixtures import NOW
from support.learning_fixtures import click
from support.learning_fixtures import legacy_dump
from support.learning_pitch_controls_support import button
from support.learning_pitch_controls_support import diagrams
from support.fake_ui import text_of
from support.fake_ui import descendants
from support.learning_pitch_sequence_controls_support import act


from support.learning_pitch_alignment_controls_support import registry, prepare


def test_written_word_multiple_beats_confirm_undo_restart_and_score_once(learning_app, registry):
    app = learning_app
    item, q = prepare(app, registry)
    before = legacy_dump(app.store)
    units = deepcopy(app.learning_store.get_item(item['id'])['units'])
    public = diagrams(app)[0]._feedback_fragment
    assert public['surface']['text'] == '今日、雨。'
    assert not any(u['levels'] for u in public['surface']['units'])
    assert 'あめ' not in json.dumps(public, ensure_ascii=False)
    assert '参考读法：きょう、あめ。' in text_of(app.body)
    assert q['prompt'] == '今日、雨。' and '调号' not in text_of(app.body)
    assert not any(n.text == '🔊' for n in descendants(app.body))
    assert '‿' not in text_of(app.body) and not button(app, '│').enabled
    act(app, CORRECT[:3])
    act(app, ['H', 'L', 'H'])
    ctx = app._learning_pitch_context
    assert pitch.state_view(q, ctx['draft'])['cursor'] == 2  # Rain: one kanji, three entered beats.
    assert not button(app, '提交').enabled and button(app, '│').enabled
    partial = deepcopy(ctx['draft'])
    act(app, ['split'])
    assert button(app, '提交').enabled  # Wrong count is not rejected prematurely.
    assert pitch.grade_answer(q, ctx['draft']['answer'])['pitch_errors'] == ['morae']
    act(app, ['undo'])
    assert ctx['draft'] == partial
    app.learning_store = LearningStore(app.store)
    saved = app.learning_store.get_question(q['id'])
    saved_before = deepcopy(saved)
    app.show_learning_question({'question': saved, 'draft': saved['draft']})
    assert app.learning_store.get_question(q['id']) == saved_before
    assert app._learning_pitch_context['draft']['pitch_state'] == partial['pitch_state']
    assert app._learning_pitch_context['draft']['answer'] == partial['answer']
    assert app.learning_store.get_item(item['id'])['units'] == units
    assert not app.learning_store.history()
    act(app, ['undo', 'split'])
    click(app, '提交')
    assert app.learning_store.get_question(q['id'])['result']['correct'] is True
    assert len(diagrams(app)) == 1 and '我的回答' not in text_of(app.body)
    assert 'pp3:' not in text_of(app.body) and not app.speak_text.called
    click(app, '记住了')
    assert len(app.learning_store.history()) == 1
    app.learning_store.finalize(q['id'], 3)
    assert len(app.learning_store.history()) == 1
    assert app.learning_store.get_unit(item['id'], 'understanding')['review_count'] == 0
    assert legacy_dump(app.store) == before


def test_wrong_count_feedback_names_written_word_and_never_scores_on_edit(learning_app, registry):
    app = learning_app
    _, q = prepare(app, registry)
    act(app, ['L', 'H', 'split', 'H', 'L', 'H', 'split'])
    assert not app.learning_store.history()
    click(app, '提交')
    text = text_of(app.body)
    assert '「雨」' in text and '2 拍' in text and '3 拍' in text
    assert '保存并继续' in text and '记住了' not in text
    assert 'pp3:' not in text and 'あめ' not in text
    assert len(diagrams(app)) == 1
    assert diagrams(app)[0]._feedback_fragment['surface']['units'][2]['levels'] == ['H', 'L']


def test_show_answer_partial_is_assisted_and_retains_written_surface(learning_app, registry):
    app = learning_app
    item, q = prepare(app, registry)
    act(app, ['H'])
    click(app, '显示答案')
    assert app.learning_store.get_question(q['id'])['result']['assisted'] is True
    assert '记住了' not in text_of(app.body)
    assert diagrams(app)[0]._feedback_fragment['surface']['text'] == '今日、雨。'
    assert not app.learning_store.history()


def test_surface_horizontal_follow_targets_entered_track_not_hidden_reading():
    from kanamemory.learning_pitch_ui import _reveal_pitch_row
    viewport = SimpleNamespace(layout=SimpleNamespace(content_height=250, content_width=300),
                               vertical_position=0, horizontal_position=0)
    cell = {'index': 0, 'row': 0, 'top': 8, 'bottom': 150, 'left': 8,
            'right': 700, 'focus_x': 680}
    diagram = SimpleNamespace(layout=SimpleNamespace(absolute_content_top=0),
        _pitch_canvas=SimpleNamespace(layout=SimpleNamespace(absolute_content_top=0)),
        _pitch_layout={'rows': [[cell]], 'row_height': 154, 'row_content_height': 142})
    data = {'surface': {'units': [{'text': '雨'}]}, 'cursor': 0}
    assert _reveal_pitch_row(viewport, diagram, data)
    assert viewport.horizontal_position <= 668 < 692 <= viewport.horizontal_position + 300
    cell.update(right=60, focus_x=40)
    assert _reveal_pitch_row(viewport, diagram, data)
    assert viewport.horizontal_position <= 8

    class UnsettledScroll:
        layout = SimpleNamespace(content_height=250, content_width=300)
        vertical_position = 0
        horizontal_position = property(lambda self: 0, lambda self, value: None)
    cell.update(right=700, focus_x=680)
    assert not _reveal_pitch_row(UnsettledScroll(), diagram, data)


def test_variable_word_pitch_spacing_reveals_the_whole_entered_tail():
    from kanamemory.learning_pitch_ui import _reveal_pitch_row
    viewport = SimpleNamespace(layout=SimpleNamespace(content_height=250, content_width=300),
                               vertical_position=0, horizontal_position=0)
    cell = {'index': 0, 'row': 0, 'top': 8, 'bottom': 120, 'left': 8,
            'right': 710, 'focus_x': 680, 'beat_width': 40}
    diagram = SimpleNamespace(layout=SimpleNamespace(absolute_content_top=0),
        _pitch_canvas=SimpleNamespace(layout=SimpleNamespace(absolute_content_top=0)),
        _pitch_layout={'rows': [[cell]], 'row_height': 130, 'row_content_height': 112,
                       'beat_width': 8})
    data = {'surface': {'units': [{'text': '長い言葉'}]}, 'cursor': 0}
    assert _reveal_pitch_row(viewport, diagram, data)
    assert viewport.horizontal_position <= 656
    assert 704 <= viewport.horizontal_position + 300
