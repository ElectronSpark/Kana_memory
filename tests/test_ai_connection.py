"""Offline protocol fixtures only: never use a real endpoint or credential."""
import json
from types import SimpleNamespace

import pytest

from kanamemory.ai_connection import (ConfiguredProvider, credential_slot, fetch_models,
    load_connection, normalize_token, preset_config, validate_connection)
from kanamemory.ai_credentials import DeviceSecrets
from kanamemory.ai_service import AIServiceError, _get
from support.ai_service_support import FAKE_KEY
from support.ai_service_support import completed
from support.ai_service_support import request
from support.tutor_service_support import request as tutor_request
from support.tutor_service_support import explain


def config(protocol, mode='json_schema'):
    preset = {'responses': 'OpenAI', 'chat': 'DeepSeek', 'anthropic': 'Claude', 'ollama': 'Ollama（本地）'}[protocol]
    return {**preset_config(preset), 'model': 'org/model:Q4_K_M', 'tutor_model': 'tutor-small', 'output_mode': mode}


def response(protocol, value):
    text = json.dumps(value, ensure_ascii=False)
    if protocol == 'responses': return completed(value)
    if protocol == 'chat': return dict(choices=[dict(finish_reason='stop', message=dict(content=text))], usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15))
    if protocol == 'anthropic': return dict(stop_reason='end_turn', content=[dict(type='text', text=text)], usage=dict(input_tokens=10, output_tokens=5, cache_read_input_tokens=3))
    return dict(done=True, done_reason='stop', message=dict(content=text), prompt_eval_count=10, eval_count=5)


@pytest.mark.parametrize('protocol', ['responses', 'chat', 'anthropic', 'ollama'])
def test_material_review_uses_configured_protocol_and_rejects_unprovided_sources(protocol):
    import pytest
    request = {'format': 'kanamemory-material-review-request', 'version': 1, 'report_id': 'a' * 64,
        'source_text': '', 'questions': [{'claim_id': 'claim:reading', 'object_kind': 'item',
            'facet': 'reading', 'context': {'title': '星空'}}], 'evidence': []}
    answer = {'findings': [{'claim_id': 'claim:reading', 'status': 'answerable',
        'answer_json': '"ほしぞら"', 'evidence_ids': [], 'note': '读法仍需独立资料支持。'}]}
    calls = []
    def transport(url, body, **kwargs):
        calls.append(body)
        return response(protocol, answer)
    provider = ConfiguredProvider(config(protocol), FAKE_KEY, transport=transport)
    assert provider.verify_material(request) == answer
    assert len(calls) == 1 and 'tools' not in calls[0]
    assert 'ほしぞら' not in json.dumps(calls[0], ensure_ascii=False)
    answer['findings'][0]['evidence_ids'] = ['invented-source']
    with pytest.raises(ValueError, match='未提供'):
        provider.verify_material(request)


@pytest.mark.parametrize('protocol', ['responses', 'chat', 'anthropic', 'ollama'])
def test_material_revision_uses_configured_protocol_without_unselected_field_changes(protocol):
    payload = {'format': 'kanamemory-material-revision-request', 'version': 1, 'workflow_id': 'a' * 64,
        'proposal_sha256': 'b' * 64, 'report_sha256': 'c' * 64, 'source_text': '', 'evidence': [],
        'issues': [{'claim_id': 'claim:meaning', 'object_kind': 'item', 'object_id': 'candidate:stars',
            'facet': 'meaning', 'before': '原释义', 'independent_answer': '新释义',
            'context': {'title': '星空'}, 'note': '待核分歧', 'evidence_ids': []}]}
    answer = {'changes': [{'claim_id': 'claim:meaning', 'value_json': '"新释义"', 'note': '仍待核', 'evidence_ids': []}]}
    calls = []
    def transport(url, body, **kwargs):
        calls.append(body); return response(protocol, answer)
    provider = ConfiguredProvider(config(protocol), FAKE_KEY, transport=transport)
    assert provider.revise_material(payload) == answer and len(calls) == 1
    assert 'tools' not in calls[0]
    answer['changes'][0]['claim_id'] = 'claim:unselected'
    with pytest.raises(ValueError, match='已选择'): provider.revise_material(payload)


@pytest.mark.parametrize('protocol', ['responses', 'chat', 'anthropic', 'ollama'])
@pytest.mark.parametrize('lane', ['generate', 'tutor'])
def test_both_ai_flows_share_provider_and_local_validators(protocol, lane):
    calls = []
    answer = {'questions': []} if lane == 'generate' else explain()
    def transport(url, body, **kwargs):
        calls.append((url, body, kwargs))
        return response(protocol, answer)
    provider = ConfiguredProvider(config(protocol), FAKE_KEY, tutor=lane == 'tutor', transport=transport)
    result = provider.generate(request()) if lane == 'generate' else provider.tutor(tutor_request('ask'))
    assert result == answer and len(calls) == 1
    url, body, headers = calls[0]
    assert url.endswith({'responses': '/responses', 'chat': '/chat/completions', 'anthropic': '/messages', 'ollama': '/api/chat'}[protocol])
    assert body['model'] == ('tutor-small' if lane == 'tutor' else 'org/model:Q4_K_M')
    assert 'tools' not in body and FAKE_KEY not in json.dumps(body)
    assert provider.last_usage['input_tokens'] == (13 if protocol == 'anthropic' else 10)
    assert headers['token'] == (None if protocol == 'ollama' else FAKE_KEY)
    if protocol == 'anthropic':
        assert headers['extra_headers'] == {'anthropic-version': '2026-10-21'}
        assert 'system' in body and body['output_config']['format']['type'] == 'json_schema'
    if protocol == 'chat':
        assert body['response_format']['json_schema']['strict'] is True
    if protocol != 'responses': assert 'reasoning' not in body and body['stream'] is False


@pytest.mark.parametrize('protocol,mode', [('chat','json_object'), ('chat','prompt'), ('responses','json_object'), ('responses','prompt'), ('ollama','json_object'), ('ollama','prompt'), ('anthropic','prompt')])
def test_output_modes_remain_json_and_do_not_retry_or_relax_local_validation(protocol, mode):
    calls=[]
    def transport(url, body, **kwargs):
        calls.append(body)
        # Out-of-scope output is rejected by the existing application boundary.
        return response(protocol, {'questions': [], 'due_at': 'forged'})
    with pytest.raises(AIServiceError) as exc:
        ConfiguredProvider(config(protocol, mode), FAKE_KEY, transport=transport).generate(request())
    assert exc.value.code == 'response_format' and len(calls) == 1
    assert 'JSON schema' in json.dumps(calls[0])
    if mode == 'prompt':
        assert not {'response_format', 'format', 'output_config', 'text'} & calls[0].keys()


@pytest.mark.parametrize('protocol', ['responses', 'chat', 'anthropic', 'ollama'])
def test_discovery_is_get_only_bounded_deduplicated_and_contains_no_learner_data(protocol):
    calls=[]
    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return {'models': [{'name':'z:small'}, {'name':'z:small'}, {'name':'a/model'}]} if protocol == 'ollama' else {'data':[{'id':'z:small'}, {'id':'z:small'}, {'id':'a/model'}, {'id':'\nunsafe'}]}
    assert fetch_models(config(protocol), FAKE_KEY, transport=transport) == ['a/model', 'z:small']
    assert len(calls) == 1 and set(calls[0][1]) == {'token','timeout','extra_headers'}
    assert '/api/tags' in calls[0][0] if protocol == 'ollama' else '/models' in calls[0][0]


def test_anthropic_pagination_cannot_follow_foreign_urls_or_loop():
    calls=[]
    def transport(url, **kw):
        calls.append(url)
        return {'data':[{'id':'a'}], 'has_more':True, 'last_id':'https://foreign.invalid/?token=fake'}
    with pytest.raises(AIServiceError): fetch_models(config('anthropic'), FAKE_KEY, transport=transport)
    assert len(calls) == 2
    assert all(url.startswith('https://api.anthropic.com/v1/models?') for url in calls)
    assert 'after_id=https%3A' in calls[1]


@pytest.mark.parametrize('value', [None, [], {}, {'data':None}, {'data':[{}]*1001}])
def test_invalid_discovery_keeps_manual_fallback(value):
    with pytest.raises(AIServiceError): fetch_models(config('chat'), FAKE_KEY, transport=lambda *a,**k:value)


@pytest.mark.parametrize('url', ['http://example.com/v1', 'http://169.254.169.254', 'https://x.invalid/v1?key=secret', 'https://u:secret@x.invalid', 'https://x.invalid/#secret', 'https://x.invalid/a/../b', 'https://x.invalid/%2e', 'https://x.invalid/v1/chat/completions', 'https://x.invalid:0/v1', 'https://x.invalid:bad', 'https://x.invalid\nHeader:x', 'file:///tmp/model'])
def test_unsafe_or_not_base_endpoint_rejected_before_request(url):
    with pytest.raises(AIServiceError) as exc:
        validate_connection({**config('chat'), 'base_url':url})
    assert exc.value.code == 'endpoint' and 'secret' not in str(exc.value)


@pytest.mark.parametrize('url', ['http://localhost:11434', 'http://127.0.0.1:1234/v1', 'http://10.0.2.2:1234/v1', 'http://192.168.1.10:11434', 'http://[::1]:11434', 'http://[fd00::123]:11434'])
def test_local_addresses_allowed_without_token(url):
    provider=ConfiguredProvider({**config('ollama'), 'base_url':url}, '')
    assert provider._key == ''


def test_legacy_config_and_original_credential_slot_are_preserved():
    old={'version':1, 'model':'old-model', 'tutor_model':'old-tutor'}
    current=validate_connection(old)
    assert current['model']=='old-model' and current['tutor_model']=='old-tutor'
    assert credential_slot(current)=='openai_api_key'
    assert old=={'version':1, 'model':'old-model', 'tutor_model':'old-tutor'}
    assert credential_slot({**current,'base_url':'https://API.OPENAI.COM:443/v1/'})=='openai_api_key'
    foreign={**current, 'base_url':'https://proxy.invalid/v1'}
    assert credential_slot(foreign)!=credential_slot(current)
    assert credential_slot({**foreign,'protocol':'chat'})!=credential_slot(foreign)


def test_non_openai_token_and_model_ids_are_supported_without_env_evaluation():
    c=config('chat')
    assert normalize_token('  example-generic-123456  ',c)=='example-generic-123456'
    with pytest.raises(AIServiceError): normalize_token('fake\nsecond',c)
    with pytest.raises(AIServiceError): normalize_token('fake',config('ollama'))


def test_vault_keeps_each_endpoint_separate_and_import_stays_openai_only(tmp_path, monkeypatch):
    values={}
    class Bridge:
        def put(self, context, name, value): values[name]=value
        def get(self, context, name): return values.get(name,'')
        def remove(self, context, name): values.pop(name,None)
    monkeypatch.setattr(DeviceSecrets,'_android',lambda self:Bridge())
    app=SimpleNamespace(platform=SimpleNamespace(_context=object()),paths=SimpleNamespace(data=tmp_path))
    old=DeviceSecrets(app);other=DeviceSecrets(app,credential_slot(config('chat')))
    old.put(FAKE_KEY);other.put('generic-key')
    assert old.get()==FAKE_KEY and other.get()=='generic-key'
    with pytest.raises(AIServiceError): other.prepare_import()
    other.remove()
    assert old.get()==FAKE_KEY and other.get()=='' and not list(tmp_path.iterdir())


@pytest.mark.parametrize('protocol,edit,code', [
    ('chat',lambda r:r['choices'][0].update(finish_reason='length'),'incomplete'),
    ('chat',lambda r:r['choices'][0]['message'].update(refusal='private'),'refusal'),
    ('chat',lambda r:r['choices'][0]['message'].update(tool_calls=[{}]),'incomplete'),
    ('anthropic',lambda r:r.update(stop_reason='max_tokens'),'incomplete'),
    ('anthropic',lambda r:r.update(stop_details={'type':'refusal'}),'refusal'),
    ('anthropic',lambda r:r.update(content=[{'type':'tool_use'}]),'response_format'),
    ('ollama',lambda r:r.update(done=False),'incomplete'),
    ('ollama',lambda r:r['message'].update(tool_calls=[{}]),'response_format'),
    ('ollama',lambda r:r['message'].update(content='   '),'response_format')])
def test_truncation_refusal_or_tool_request_cannot_become_teaching_content(protocol,edit,code):
    raw=response(protocol,{'questions':[]});edit(raw)
    with pytest.raises(AIServiceError) as exc:
        ConfiguredProvider(config(protocol),FAKE_KEY,transport=lambda *a,**k:raw).generate(request())
    assert exc.value.code==code


def test_discovery_http_uses_no_body_and_never_follows_redirect(monkeypatch):
    from kanamemory import ai_service
    import urllib.error
    captured=[]
    class Opener:
        def open(self,req,timeout):
            captured.append(req)
            raise urllib.error.HTTPError(req.full_url,302,'secret',{},None)
    monkeypatch.setattr(ai_service.urllib.request,'build_opener',lambda handler:Opener())
    with pytest.raises(AIServiceError) as exc: _get('https://fixture.invalid/models',token='fake')
    assert exc.value.code=='http_302'
    assert captured[0].method=='GET' and captured[0].data is None


@pytest.mark.parametrize('model,reasoning', [('gpt-4.1',False),('gpt-6.1-sol',True)])
def test_selected_openai_models_do_not_all_receive_reasoning_parameters(model,reasoning):
    calls=[]
    def transport(url,body,**kw): calls.append(body);return completed()
    ConfiguredProvider({**config('responses'),'model':model},FAKE_KEY,transport=transport).generate(request())
    assert ('reasoning' in calls[0]) is reasoning


@pytest.mark.parametrize('protocol',['responses','chat','anthropic','ollama'])
def test_suggestion_autocomplete_uses_selected_service_instead_of_openai(protocol):
    from kanamemory.tutor_suggestions import generate_suggestions
    calls=[]
    def transport(url,body,**kwargs):
        calls.append((url,body,kwargs))
        return response(protocol,{'ids':['hint']})
    c=config(protocol)
    result=generate_suggestions(ConfiguredProvider(c,FAKE_KEY,tutor=True,transport=transport))
    assert result==['给我一个不透露答案的小提示。']
    assert len(calls)==1 and calls[0][0].startswith(c['base_url'])
    assert calls[0][1]['model']=='tutor-small' and calls[0][2]['timeout']==12
