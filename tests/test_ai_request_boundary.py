"""A well-shaped response must still belong to the user's selected request."""
from copy import deepcopy

import pytest

from kanamemory.ai_service import AIServiceError, OpenAIProvider
from support.ai_service_support import FAKE_KEY
from support.ai_service_support import completed
from support.ai_service_support import request
from support.ai_material_fixtures import examples, new_word


def material(mode='examples'):
    req = request(mode)
    req['target']['id'] = 'candidate:stars'
    result = {'items': [], 'example_pack': examples(count=2), 'segments': []}
    if mode == 'analyze':
        req['source_text'] = '星空を見ます。星空がきれいです。'
        result['items'] = [new_word()]
        end = 0
        for ex in result['example_pack']['examples']:
            result['segments'].append({'start': end, 'end': end + len(ex['text']),
                                      'quote': ex['text'], 'example_id': ex['id']})
            end += len(ex['text'])
    return req, result


def generate(req, result):
    return OpenAIProvider(FAKE_KEY, transport=lambda *_a, **_k: completed(result)).generate(req)


@pytest.mark.parametrize('mode', ['examples', 'analyze'])
def test_requested_material_passes_without_mutating_provider_object(mode):
    req, response = material(mode)
    original = deepcopy(response)
    assert generate(req, response)['example_pack']['examples'] == response['example_pack']['examples']
    assert response == original


@pytest.mark.parametrize('case', ['new_item', 'segments', 'unrelated', 'overcount', 'extra_field'])
def test_examples_cannot_silently_expand_scope(case):
    req, response = material()
    if case == 'new_item': response['items'] = [new_word()]
    elif case == 'segments': response['segments'] = [{'example_id': 'ex1'}]
    elif case == 'unrelated': response['example_pack']['references'][1]['target_id'] = 'grammar:copula:polite'
    elif case == 'overcount': req['count'] = 1
    else: response['enroll'] = True
    with pytest.raises(AIServiceError) as error:
        generate(req, response)
    assert error.value.code == 'response_format'


@pytest.mark.parametrize('case', ['extra_example', 'overcount'])
def test_passage_analysis_cannot_add_unrequested_material(case):
    req, response = material('analyze')
    if case == 'extra_example': response['segments'].pop()
    else:
        req['count'] = 1
        response['items'].append(new_word(alias='second'))
    with pytest.raises(AIServiceError): generate(req, response)


def test_questions_enforce_requested_quantity_locally():
    req = request()
    req['count'] = 1
    with pytest.raises(AIServiceError): generate(req, {'questions': [{}, {}]})


@pytest.mark.parametrize('bad', [None, [], 'bad'])
def test_malformed_material_containers_fail_before_anchors(bad):
    req, response = material()
    response['example_pack'] = bad
    with pytest.raises(AIServiceError): generate(req, response)
