"""Reference contours have exact text binding and stable word geometry."""
from copy import deepcopy
import pytest

from kanamemory.prosody_cue import validate_cue, cue_layout
from kanamemory.assessment_policy import contract_values
from kanamemory.tutor_service import _question


def cue():
    return {'version': 1, 'source_ids': ['synthetic-reference'], 'units': [
        {'text': '東京', 'mora_count': 4, 'points': [[0, 0], [.5, 3], [1, 2]]},
        {'text': 'へ', 'mora_count': 1, 'points': [[0, 2], [1, 2]]},
        {'text': '行く？', 'mora_count': 2, 'points': [[0, 2], [.7, 0], [1, 3]]}]}


@pytest.mark.parametrize('width,scale', [(160, 1), (320, 1.3), (320, 2), (420, 3)])
def test_words_keep_baseline_and_wrap_without_character_or_pitch_count_conflation(width, scale):
    original = cue()
    scene = cue_layout(original, '東京へ行く？', width=width, scale=scale)
    changed = deepcopy(original)
    changed['units'][0]['points'] = [[0, 3], [1, 0]]
    other = cue_layout(changed, '東京へ行く？', width=width, scale=scale)
    assert [(x['text'], x['x'], x['y'], x['width']) for x in scene['units']] == [
        (x['text'], x['x'], x['y'], x['width']) for x in other['units']]
    for cell in scene['units']:
        assert cell['x'] + cell['width'] <= width + 2
        assert all(0 <= x <= width and 0 <= y < cell['y'] for x, y in cell['points'])
        assert all(peer['y'] == cell['y'] for peer in scene['units'] if peer['row'] == cell['row'])
    assert original == cue()


@pytest.mark.parametrize('mutation', [lambda c: c['units'][0].update(text='大阪'),
    lambda c: c['units'][0].update(points=[[0, 0], [1, float('nan')]]),
    lambda c: c['units'][0].update(points=[[0, 0], [.8, 1], [.5, 2], [1, 3]]),
    lambda c: c.update(source_ids=[]), lambda c: c['units'][0].update(mora_count=True)])
def test_invalid_reference_cannot_be_rendered(mutation):
    candidate = cue(); mutation(candidate)
    with pytest.raises(ValueError):
        validate_cue(candidate, '東京へ行く？')


def test_api_receives_public_curve_and_assessment_binds_changes_only_when_present():
    original = {'prompt': '東京へ行く？', 'kind': 'pronunciation_application', 'prosody_cue': cue()}
    assert _question(original)['prosody_cue'] == cue()
    before = contract_values(original)
    changed = deepcopy(original); changed['prosody_cue']['units'][0]['points'][0][1] = 1
    assert before != contract_values(changed)
    assert 'prosody_cue' not in contract_values({'prompt': 'legacy'})
    assert 'prosody_cue' not in _question({'prompt': 'legacy'})
