from types import SimpleNamespace
import json

import pytest

from kanamemory.ai_service import AIServiceError
from kanamemory.tutor_suggestions import generate_suggestions, suggestion_options


def provider(ids, calls):
    def transport(url, request, **kwargs):
        calls.append((request, kwargs))
        return {'status':'completed', 'output':[{'type':'message','content':[
            {'type':'output_text','text':json.dumps({'ids':ids})}]}]}
    return SimpleNamespace(_request=lambda body, **kwargs: transport(None, body, **kwargs), model='test')


def test_before_answer_only_safe_local_phrases_no_generated_spoiler():
    calls = []
    result = generate_suggestions(provider(['hint','task'], calls), kind='vocab_reading', target='word:example')
    assert result == [suggestion_options()['hint'], suggestion_options()['task']]
    payload, options = calls[0]
    assert payload['store'] is False and payload['max_output_tokens'] == 400
    assert options['timeout'] == 12
    assert 'draft' not in payload['input'] and 'answer' not in json.loads(payload['input'])
    with pytest.raises(AIServiceError):
        generate_suggestions(provider(['正解はこたえです'], []), kind='vocab_reading')
    with pytest.raises(AIServiceError):
        generate_suggestions(provider(['contrast'], []), after_answer=False)


def test_after_answer_adds_contrast_but_still_rejects_duplicate_ids():
    assert generate_suggestions(provider(['contrast'], []), after_answer=True) == ['这些选项的用法有什么区别？']
    with pytest.raises(AIServiceError):
        generate_suggestions(provider(['contrast','contrast'], []), after_answer=True)


@pytest.mark.parametrize('correct', [True, False, None])
def test_contrast_suggestion_never_claims_a_correct_or_unscored_answer_was_wrong(correct):
    calls = []
    outcome = 'revealed' if correct is None else 'correct' if correct else 'incorrect'
    suggestions = generate_suggestions(provider(['contrast'], calls), after_answer=True,
        recent=[{'outcome': outcome, 'correct': correct}])
    assert json.loads(calls[0][0]['input'])['recent'][0]['outcome'] == outcome
    assert suggestions == ['这些选项的用法有什么区别？']
    assert all('我的答案不适用' not in text and '答错' not in text for text in suggestions)
