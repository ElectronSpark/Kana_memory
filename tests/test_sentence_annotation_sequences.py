"""A verified numeric phrase chain is never flattened into a global pitch."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from kanamemory import sentence_annotation_ui as renderer, sentence_annotations
from kanamemory.question_support import prompt_support, build_prompt_block, _prompt_lines
from support.sentence_annotation_ui_support import app
from support.sentence_annotation_ui_support import annotations
from support.sentence_annotation_ui_support import bodies
from support.sentence_annotation_ui_support import labels
from support.sentence_annotation_ui_support import use_plan
from support.sentence_annotation_ui_support import walk


TEXT = '締切は２５日です。'


def sequence():
    return {'start': 3, 'end': 6, 'surface': '２５日', 'reading': 'にじゅうごにち',
        'reading_source_id': 'curated-reading-reference', 'accent_number': None,
        'accent_scope': 'phrase_sequence', 'accent_source_id': 'curated-accent-reference',
        'accent_surface': '２５日', 'accent_reading': 'にじゅうごにち',
        'accent_phrases': [{'reading': 'にじゅう', 'accent_number': 1},
                           {'reading': 'ごにち', 'accent_number': 1}]}


def question():
    return {'id': 'sequence-meaning', 'revision': '1', 'kind': 'grammar_meaning',
        'ability': 'understanding', 'response': 'choice', 'prompt': TEXT,
        'answer': '截止日期是25日。', 'choices': ['截止日期是25日。', '不是25日。'],
        'support': {'reviewed': True, 'content_revision': '1', 'prompt': TEXT,
                    'reading_safe': True, 'accent_safe': True, 'audio_safe': True,
                    'has_gap': False, 'segments': []}}


@pytest.fixture
def lookup(monkeypatch):
    service = Mock(return_value={'text': TEXT, 'segments': [sequence()]})
    monkeypatch.setattr(sentence_annotations, 'sentence_annotation_plan', service)
    return service


@pytest.mark.parametrize('furigana,number,expected', [
    (True, True, ['にじゅう①｜ごにち①']), (True, False, ['にじゅうごにち']),
    (False, True, ['①｜①']), (False, False, []),
])
def test_teaching_sequence_preferences_keep_whole_body_and_original_link_offsets(app, monkeypatch,
                                                                               furigana, number, expected):
    source = use_plan(monkeypatch, TEXT, [sequence()]); before = deepcopy(source)
    chosen = Mock()
    regions = [{'start': 3, 'end': 6, 'reference_ids': ['date-word', 'calendar-use']}]
    highlight = [{'start': 3, 'end': 6, 'quote': '２５日'}]
    block = renderer.build_sentence_block(app, TEXT, preferences={'furigana': furigana, 'number': number},
        links=regions, highlight_spans=highlight, on_select=chosen)
    assert [node.text for node in annotations(block)] == expected
    assert ''.join(node.text for node in bodies(block)) == TEXT
    linked = next(node for node in bodies(block) if node._example_link_regions)
    start, end = linked._sentence_text_range
    assert linked.text == TEXT[start:end]
    assert linked._example_link_regions == ({'start': 3 - start, 'end': 6 - start,
                                             'reference_ids': ['date-word', 'calendar-use']},)
    native = next(call for call in app.platform.link_kana_label.call_args_list if call.args[0] is linked)
    native.args[2]('0')
    chosen.assert_called_once_with(['date-word', 'calendar-use'])
    assert all(not hasattr(node, '_example_link_regions') for node in annotations(block))
    assert block._sentence_annotation_plan['accent_visible'] is number
    assert block._sentence_annotation_plan['furigana_visible'] is furigana
    assert source == before
    assert 'curated-' not in ''.join(node.text for node in labels(block))
    assert block._feedback_fragment['plan']['segments'][1]['accent_number'] is None


@pytest.mark.parametrize('patch', [
    {'accent_source_id': 'model-guessed'}, {'accent_number': 1}, {'accent_surface': '２３日'},
    {'accent_reading': 'べつのよみ'},
    {'accent_phrases': [{'reading': 'にじゅうごにち', 'accent_number': 1}]},
    {'accent_phrases': [{'reading': 'にじゅう', 'accent_number': True}, {'reading': 'ごにち', 'accent_number': 1}]},
    {'accent_phrases': [{'reading': 'にじゅう', 'accent_number': 7}, {'reading': 'ごにち', 'accent_number': 1}]},
    {'accent_phrases': [{'reading': 'にじゅう', 'accent_number': 1}, {'reading': 'こたえ', 'accent_number': 1}]},
])
def test_invalid_sequence_does_not_create_scalar_zero_or_one(app, monkeypatch, patch):
    part = sequence(); part.update(patch)
    use_plan(monkeypatch, TEXT, [part])
    block = renderer.build_sentence_block(app, TEXT)
    assert [node.text for node in annotations(block)] == ['にじゅうごにち']
    assert block._sentence_annotation_plan['accent_visible'] is False
    assert ''.join(node.text for node in bodies(block)) == TEXT


@pytest.mark.parametrize('furigana,number,expected', [
    (True, True, 'にじゅう①｜ごにち①'), (True, False, 'にじゅうごにち'),
    (False, True, '①｜①'), (False, False, ''),
])
def test_safe_meaning_prompt_supports_sequence_without_mutating_question(app, lookup, furigana, number, expected):
    q = question(); before = deepcopy(q)
    result = prompt_support(q, {'furigana': furigana, 'number': number})
    assert ''.join(part['surface'] for part in result['segments']) == TEXT
    display = _prompt_lines(result['segments'], 264, 20, 1)
    assert ''.join(cell['annotation'] for line in display for cell in line) == expected
    assert result['accent_visible'] is number
    assert result['furigana_visible'] is furigana
    block = build_prompt_block(app, q, {'furigana': furigana, 'number': number})
    assert expected in ''.join(node.text for node in labels(block))
    assert q == before
    assert [call.args for call in lookup.call_args_list] == [(TEXT,), (TEXT,)]


@pytest.mark.parametrize('patch', [
    {'kind': 'vocab_reading'}, {'kind': 'listening_comprehension'}, {'kind': 'vocab_orthography'},
    {'kind': 'kana_write_from_audio'}, {'kind': 'pitch_discrimination'}, {'ability': 'reading'},
    {'tests_reading': True}, {'tests_pitch': True}, {'audio_only': True}, {'domain': 'pronunciation'},
])
def test_sequence_cannot_bypass_question_reading_gate_even_when_authored(app, lookup, patch):
    q = question(); q.update(patch)
    q['support']['segments'] = [{**sequence(), 'reading_safe': True, 'accent_safe': True}]
    before = deepcopy(q)
    result = prompt_support(q)
    assert result['furigana_visible'] is False and result['accent_visible'] is False
    assert all(not part.get('accent_phrases') for part in result['segments'])
    lookup.assert_not_called()
    assert q == before


@pytest.mark.parametrize('field', ['reading_safe', 'accent_safe'])
def test_sequence_hydration_obeys_authored_safety_flags(lookup, field):
    q = question()
    q['support']['segments'] = [{'start': 3, 'end': 6, 'surface': '２５日', field: False}]
    plan = prompt_support(q)
    assert plan['furigana_visible'] is (field != 'reading_safe')
    assert plan['accent_visible'] is (field != 'accent_safe')
    parts = next(p for p in plan['segments'] if p['surface'] == '２５日')
    if field == 'reading_safe':
        assert all(p['reading'] is None for p in parts['accent_phrases'])
    else:
        assert not parts.get('accent_phrases')


def test_authored_context_reading_clears_incompatible_phrase_chain(lookup):
    q = question()
    q['support']['segments'] = [{'start': 3, 'end': 6, 'surface': '２５日',
        'reading': 'ニジュウゴニチ', 'reading_source_id': 'authored',
        'reading_safe': True, 'accent_safe': True}]
    before = deepcopy(q)
    plan = prompt_support(q)
    assert plan['furigana_visible'] and not plan['accent_visible']
    part = next(p for p in plan['segments'] if p['surface'] == '２５日')
    assert part['reading'] == 'ニジュウゴニチ' and not part.get('accent_phrases')
    assert q == before


def test_sequence_does_not_use_answers_or_hidden_fields_as_reading_material(lookup):
    q = question(); expected = prompt_support(q)
    q.update(answer='新しい答え', choices=['隠された選択肢'], audio_text='別の録音', feedback='秘密の読み方')
    assert prompt_support(q) == expected
    assert [call.args for call in lookup.call_args_list] == [(TEXT,), (TEXT,)]


@pytest.mark.parametrize('width', [200, 264, 328])
def test_sequence_uses_one_wrapping_cell_at_200_percent_without_fixed_height(app, monkeypatch, lookup, width):
    from kanamemory import text_layout
    monkeypatch.setattr(text_layout, 'font_scale', lambda: 2)
    use_plan(monkeypatch, TEXT, [sequence()])
    block = renderer.build_sentence_block(app, TEXT, available_width=width)
    assert [node.text for node in annotations(block)] == ['にじゅう①｜ごにち①']
    date = next(node for node in bodies(block) if node.text == '２５日')
    assert date._sentence_text_range == (3, 6)
    assert all('height' not in node.style for node in walk(block))
    assert all(node.style.get('width', 0) <= width for node in walk(block))
    question_block = build_prompt_block(app, question(), available_width=width)
    assert any(node.text == 'にじゅう①｜ごにち①' for node in labels(question_block))
    assert all('height' not in node.style for node in walk(question_block))
    assert all(node.style.get('width', 0) <= width for node in walk(question_block))
